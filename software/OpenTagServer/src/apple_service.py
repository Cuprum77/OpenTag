"""Apple (Haystack) work run by the scheduler's Apple lane.

* Fetch jobs (manual, after upload, or the periodic automatic 7-day fetch): all keys of
  the selected tags in batches of 250. Manual/upload jobs only keep a 5 s gap between
  requests; automatic ones use the configured spacing.
* Gap recovery ("singleton sweep"): multi-key requests have been seen to miss reports,
  so when the lane is idle it walks through every key one request at a time, paced like
  automatic work. A pass resumes where it stopped (also across restarts, and a restart
  starts a new pass once the current one is done); after a full pass it waits 24 h.
"""
import logging
import time

import db
import history
import inventory
import keystore
import state
from fetch_apple import _build_key_map, fetch_apple_locations


logger = logging.getLogger(__name__)

QUERY_DAYS = 7
SWEEP_PASS_PAUSE_SECONDS = 24 * 3600


def _alert_report_failure(user_id):
    def report(fail):
        reason = fail.get("reason", "unknown")
        alert_type = "orphaned_report" if reason == "missing_private_key" else "decryption_failure"
        state.add_alert(user_id, "apple", alert_type, reason, target="haystack_report")
    return report


def run_fetch_job(runtime, limiter, user, params, manual, progress, stop_event=None):
    """Returns (state, result). Raises on failure (the job is then marked failed)."""
    user_id, username = user["id"], user["username"]
    kind = "fetch_location" if manual else "auto_fetch_location"
    tag_ids = params.get("tag_ids")
    inventory.sync_user(user_id, username)
    rows = keystore.apple_rows(keystore.get_index(username), set(tag_ids) if tag_ids is not None else None)
    if not rows:
        raise ValueError("no Apple tags selected" if tag_ids is not None else "no Apple key files uploaded")

    batch = {"n": 0, "total": 1}

    def on_batch(index, total, size):
        batch.update(n=index, total=total)
        progress(stage=f"Requesting batch {index}/{total} ({size} keys)", progress=index - 1, total=total)

    def on_wait(seconds, reason):
        what = "Haystack asked us to slow down" if reason == "backoff" else "Pacing requests"
        progress(stage=f"{what}: next batch in {int(seconds) + 1} s")

    def request(endpoint, **kwargs):
        limiter.wait(manual, on_wait=on_wait, stop_event=stop_event)
        progress(stage=f"Requesting batch {batch['n']}/{batch['total']}")
        return limiter.post(endpoint, **kwargs)

    try:
        payload = fetch_apple_locations(
            runtime["haystack"], rows, days=int(params.get("days") or QUERY_DAYS), timeout=30,
            _alert_callback=_alert_report_failure(user_id), _request_callback=request, _progress_callback=on_batch,
        )
    except Exception as exc:
        state.set_status(user_id, "apple", False, kind, error=exc)
        state.add_alert(user_id, "apple", "manual_fetch_error" if manual else "auto_fetch_error", exc, target="apple_locations")
        raise
    stored = history.store_events(user_id, history.apple_events(payload))
    state.set_status(user_id, "apple", True, kind, details=payload)
    progress(progress=batch["total"], total=batch["total"])
    logger.info("Apple %s for %s: %d reports, %d new events", kind, username, payload["counts"]["decoded"], stored)
    return "done", {"stored": stored, "reports": payload["counts"]["decoded"], "keys": payload["query"]["ids"],
                    "batches": payload["query"]["batches"]}


def auto_fetch_due(runtime, user_id, now=None):
    interval = runtime.get("apple_auto_query_interval_min", 0) * 60
    if interval <= 0:
        return False
    return (now or time.time()) - float(db.kv_get(db.user_key(user_id, "last_apple_fetch"), 0)) >= interval


def fetch_schedule(runtime, user_id):
    minutes = runtime.get("apple_auto_query_interval_min", 0)
    last = float(db.kv_get(db.user_key(user_id, "last_apple_fetch"), 0))
    return {
        "interval_min": minutes,
        "last_unix": int(last) if last else None,
        "next_due_unix": int(last + minutes * 60) if minutes > 0 and last else None,
    }


def mark_auto_fetch_queued(user_id):
    db.kv_set(db.user_key(user_id, "last_apple_fetch"), time.time(), user_id)


# ── gap recovery (singleton sweep) ──

def _sweep_key(user_id):
    return db.user_key(user_id, "apple_singleton_sweep")


def _sweep_target(user, startup_id, now):
    """The next key to query for this user, or None if nothing is due.

    Returns (hashed_id, row, sweep_state)."""
    user_id, username = user["id"], user["username"]
    rows_all = keystore.apple_rows(keystore.get_index(username))
    if not rows_all:
        return None
    keys, sources = _build_key_map(rows_all)
    if not keys:
        return None
    sweep = db.kv_get(_sweep_key(user_id), {}) or {}
    if sweep.get("startup") != startup_id:
        # New process: finish an interrupted pass, otherwise start a new one now.
        sweep["startup"] = startup_id
        sweep["next_due"] = 0
    if not sweep.get("pending"):
        if now < sweep.get("next_due", 0):
            db.kv_set(_sweep_key(user_id), sweep, user_id)
            return None
        sweep["pending"] = list(keys)
        sweep["total"] = len(keys)
        sweep["pass_started"] = now
    sweep["pending"] = [k for k in sweep["pending"] if k in keys]
    if not sweep["pending"]:
        sweep["next_due"] = now + SWEEP_PASS_PAUSE_SECONDS
        db.kv_set(_sweep_key(user_id), sweep, user_id)
        return None
    db.kv_set(_sweep_key(user_id), sweep, user_id)
    selected = sweep["pending"][0]
    source = sources[selected]
    row = {"privateKey": keys[selected], "name": source["tag"], "tag_id": source["tag_id"], "source_file": source["file"]}
    return selected, row, sweep


def sweep_step(runtime, limiter, user, startup_id):
    """Query one key for this user if a sweep step is due. Returns True if a request was made."""
    user_id = user["id"]
    now = time.time()
    target = _sweep_target(user, startup_id, now)
    if target is None:
        return False
    selected, row, sweep = target
    try:
        payload = fetch_apple_locations(runtime["haystack"], [row], days=QUERY_DAYS, timeout=30,
                                        _request_callback=limiter.post)
        history.store_events(user_id, history.apple_events(payload))
    except Exception as exc:
        # Keep the key pending; it is retried after the limiter's backoff.
        logger.warning("Apple gap recovery request failed for %s: %s", user["username"], exc)
        state.set_status(user_id, "apple_singleton", False, "singleton_fetch_location", error=exc)
        state.add_alert(user_id, "apple", "auto_fetch_error", "Apple gap recovery request failed; will retry later",
                        target="gap_recovery")
        return True
    sweep["pending"] = [k for k in sweep["pending"] if k != selected]
    if not sweep["pending"]:
        sweep["next_due"] = time.time() + SWEEP_PASS_PAUSE_SECONDS
        logger.info("Apple gap recovery pass complete for %s (%d keys)", user["username"], sweep.get("total", 0))
    db.kv_set(_sweep_key(user_id), sweep, user_id)
    state.set_status(user_id, "apple_singleton", True, "singleton_fetch_location", details=payload)
    return True


def sweep_status(runtime, user_id):
    if not runtime.get("apple_gap_recovery", True) or not runtime.get("local_history", True):
        return {"enabled": False}
    sweep = db.kv_get(_sweep_key(user_id), {}) or {}
    pending = len(sweep.get("pending") or [])
    total = max(sweep.get("total") or 0, pending)  # older saved state had no total
    return {
        "enabled": True,
        "running": pending > 0,
        "done": max(0, total - pending) if pending else total,
        "total": total,
        "next_pass_unix": int(sweep["next_due"]) if not pending and sweep.get("next_due") else None,
        "request_interval_seconds": runtime["haystack"]["request_interval_seconds"],
    }
