"""The one place that decides what runs when.

A single dispatcher thread
* plans automatic work (Google key sync + location fetch, Apple 7-day fetch) by
  queueing jobs when they are due,
* starts queued jobs: one Apple job at a time for everyone (they share the Haystack
  server and its pacing), and one Google job at a time per user (the bridge rewrites
  that user's secrets.json); manual/upload jobs run before automatic ones,
* lets Apple gap recovery take one step whenever the Apple lane is idle and the
  request pacing allows it,
* runs hourly maintenance (retention, sessions, old alerts and jobs).
"""
import logging
import threading
import time
import uuid

import apple_service
import auth
import db
import google_service
import history
import jobs
import keystore
import state


logger = logging.getLogger(__name__)

TICK_SECONDS = 2
PLAN_EVERY_SECONDS = 15
MAINTENANCE_EVERY_SECONDS = 3600


def _users():
    return [dict(r) for r in db.query("SELECT id, username FROM users WHERE role = 'user' ORDER BY id")]


class Scheduler:
    def __init__(self, app, limiter):
        self.app = app
        self.limiter = limiter
        self.stop_event = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._apple_busy = False
        self._google_busy = set()      # user ids with a running Google job
        self._last_plan = 0.0
        self._last_maintenance = 0.0
        self._sweep_cursor = 0
        self.startup_id = uuid.uuid4().hex
        self._thread = None

    @property
    def runtime(self):
        return self.app.config["OPENTAG"]

    # ── public ──

    def start(self):
        jobs.recover_after_restart()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self):
        self.stop_event.set()
        self._wake.set()

    def notify(self):
        """Something was queued: look at the queue now instead of at the next tick."""
        self._wake.set()

    def submit(self, user_id, kind, trigger="manual", params=None):
        job = jobs.create(user_id, kind, trigger, params)
        self.notify()
        return job

    # ── loop ──

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                now = time.time()
                if now - self._last_plan >= PLAN_EVERY_SECONDS:
                    self._last_plan = now
                    self.plan(now)
                if now - self._last_maintenance >= MAINTENANCE_EVERY_SECONDS:
                    self._last_maintenance = now
                    self.maintenance()
                self.dispatch()
            except Exception as exc:
                logger.error("Scheduler error: %s", exc)
            self._wake.wait(TICK_SECONDS)
            self._wake.clear()

    def plan(self, now=None):
        """Queue automatic jobs that are due."""
        now = now or time.time()
        runtime = self.runtime
        apple_auto = runtime.get("local_history", True) and bool(runtime["haystack"].get("endpoint"))
        for user in _users():
            uid = user["id"]
            try:
                if keystore.secrets_path(user["username"]):
                    if google_service.key_sync_due(runtime, uid, now):
                        if not jobs.has_active(uid, "google_sync"):
                            jobs.create(uid, "google_sync", "auto")
                    elif (google_service.fetch_due(runtime, uid, now) and not jobs.has_active(uid, "google_sync")
                          and not jobs.has_active(uid, "google_fetch")
                          and keystore.get_index(user["username"])["google"]):
                        google_service.mark_fetch_queued(uid)
                        jobs.create(uid, "google_fetch", "auto")
                if apple_auto and apple_service.auto_fetch_due(runtime, uid, now) and not jobs.has_active(uid, "apple_fetch"):
                    if keystore.get_index(user["username"])["apple"]:
                        apple_service.mark_auto_fetch_queued(uid)
                        jobs.create(uid, "apple_fetch", "auto")
            except Exception as exc:
                logger.error("Planning failed for %s: %s", user["username"], exc)

    def dispatch(self):
        with self._lock:
            if not self._apple_busy:
                job = jobs.next_queued("apple")
                if job and jobs.claim(job["id"]):
                    self._apple_busy = True
                    self._spawn(self._run_apple_job, job)
                elif job is None and self._sweep_allowed():
                    self._apple_busy = True
                    self._spawn(self._run_sweep_step)
            for user_id in jobs.queued_users("google"):
                if user_id in self._google_busy:
                    continue
                job = jobs.next_queued("google", user_id=user_id)
                if job and jobs.claim(job["id"]):
                    self._google_busy.add(user_id)
                    self._spawn(self._run_google_job, job)

    def maintenance(self):
        try:
            removed = history.purge_expired(self.runtime["history_retention_days"])
            if removed:
                logger.info("Retention removed %d history events", removed)
            auth.purge_expired_sessions()
            state.purge_dismissed_alerts()
            jobs.purge_old()
        except Exception as exc:
            logger.error("Maintenance error: %s", exc)

    # ── runners ──

    def _spawn(self, target, *args):
        threading.Thread(target=target, args=args, daemon=True, name=target.__name__).start()

    def _user(self, user_id):
        row = db.query_one("SELECT id, username FROM users WHERE id = ?", (user_id,))
        return dict(row) if row else None

    def run_job(self, job):
        """Run one claimed job to completion (also used directly by tests)."""
        progress = jobs.Progress(job["id"])
        user = self._user(job["user_id"])
        manual = job["trigger"] != "auto"
        try:
            if user is None:
                raise RuntimeError("user no longer exists")
            if job["kind"] == "apple_fetch":
                final, result = apple_service.run_fetch_job(
                    self.runtime, self.limiter, user, job["params"], manual, progress, self.stop_event)
            elif job["kind"] == "google_fetch":
                tag_ids = job["params"].get("tag_ids")
                final, result = google_service.fetch_tags(
                    user, set(tag_ids) if tag_ids is not None else None, manual=manual, progress=progress)
            elif job["kind"] == "google_sync":
                final, result = google_service.sync_with_google(user, manual=manual, progress=progress)
            else:
                raise RuntimeError(f"unknown job kind {job['kind']}")
            stage = {"done": "Finished", "partial": "Finished with errors", "failed": "Failed"}[final]
            error = None
            if final != "done" and isinstance(result, dict) and result.get("failed"):
                first = result["failed"][0]
                error = first["tag"] + (f" / {first['subtag']}" if first.get("subtag") else "") + ": " + first["error"]
            jobs.finish(job["id"], final, result=result, error=error, stage=stage)
        except Exception as exc:
            logger.warning("Job %s (%s) failed: %s", job["id"], job["kind"], state.clean_error_message(exc))
            jobs.finish(job["id"], "failed", error=state.clean_error_message(exc), stage="Failed")

    def _run_apple_job(self, job):
        try:
            self.run_job(job)
        finally:
            with self._lock:
                self._apple_busy = False
            self.notify()

    def _run_google_job(self, job):
        try:
            self.run_job(job)
        finally:
            with self._lock:
                self._google_busy.discard(job["user_id"])
            self.notify()

    # ── gap recovery ──

    def _sweep_allowed(self):
        runtime = self.runtime
        return (runtime.get("apple_gap_recovery", True) and runtime.get("local_history", True)
                and bool(runtime["haystack"].get("endpoint")) and self.limiter.ready_in(manual=False) <= 0)

    def run_sweep_step(self):
        """One gap-recovery request for the next user that has one due (round robin)."""
        users = _users()
        for offset in range(len(users)):
            user = users[(self._sweep_cursor + offset) % len(users)]
            try:
                if apple_service.sweep_step(self.runtime, self.limiter, user, self.startup_id):
                    self._sweep_cursor = (self._sweep_cursor + offset + 1) % len(users)
                    return True
            except Exception as exc:
                logger.warning("Gap recovery failed for %s: %s", user["username"], exc)
        return False

    def _run_sweep_step(self):
        try:
            self.run_sweep_step()
        finally:
            with self._lock:
                self._apple_busy = False

    # ── tests / tooling ──

    def run_queued_now(self):
        """Run every queued job synchronously in this thread (tests; workers disabled)."""
        ran = 0
        while True:
            job = jobs.next_queued("apple") or jobs.next_queued("google")
            if job is None or not jobs.claim(job["id"]):
                return ran
            self.run_job(job)
            ran += 1
