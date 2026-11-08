"""Google work run as scheduler jobs (at most one per user at a time)."""
import logging
import time

import db
import history
import inventory
import keystore
import state
from fetch_google import fetch_google_locations, refresh_google_announcements, user_lock


logger = logging.getLogger(__name__)

LOCK_WAIT_SECONDS = 600
SETTLE_AFTER_SYNC_SECONDS = 15  # let Google process new announcements before fetching
FETCH_TIMEOUT_SECONDS = 45      # per tracker: wait for the FCM location answer


def _short(name):
    return name.replace("[OTB-C] ", "")


def _locked(username):
    lock = user_lock(username)
    if not lock.acquire(timeout=LOCK_WAIT_SECONDS):
        raise RuntimeError("another Google operation is still running for this account")
    return lock


def fetch_tags(user, stable_ids=None, manual=False, progress=None):
    """Fetch Google locations for some (or all) Google tags of a user and store them.

    Returns (state, result) with result = {"stored", "fetched": [...], "failed": [{"tag", "subtag", "error"}]}.
    A failing tag or subtag becomes an alert and does not stop the others.
    """
    progress = progress or (lambda **_: None)
    user_id, username = user["id"], user["username"]
    path = keystore.secrets_path(username)
    if not path:
        raise ValueError("upload secrets.json first")
    lock = _locked(username)
    try:
        inventory.sync_user(user_id, username)
        index = keystore.get_index(username)
        tags = [t for t in index["google"].values() if stable_ids is None or t["stable_id"] in stable_ids]
        if stable_ids is not None and len(tags) != len(set(stable_ids)):
            raise ValueError("unknown Google tag")
        kind = "fetch_location" if manual else "auto_fetch_location"
        alert_type = "manual_fetch_error" if manual else "auto_fetch_error"

        def steps(tag):
            return len(tag["subtags"]) if tag.get("compound_name") else len(tag["canonic_ids"])

        total = sum(max(1, steps(t)) for t in tags)
        done = 0
        progress(total=total, progress=0, stage=f"Fetching {len(tags)} Google tag(s)")
        result = {"stored": 0, "fetched": [], "failed": []}

        for tag in tags:
            tag_done_before = done
            seen = {"steps": 0}

            def on_progress(event, tag=tag):
                nonlocal done
                name = _short(event.get("name") or tag["name"])
                if event.get("event") == "start":
                    progress(stage=f"{tag['name']}: fetching {name}")
                elif event.get("event") in ("done", "failed"):
                    seen["steps"] += 1
                    done = tag_done_before + seen["steps"]
                    progress(progress=done)

            try:
                payloads = []
                if tag.get("compound_name"):
                    payloads.append(fetch_google_locations(
                        path, compound_name=tag["compound_name"], timeout=FETCH_TIMEOUT_SECONDS,
                        subtags=steps(tag), on_progress=on_progress))
                elif tag["canonic_ids"]:
                    for cid in tag["canonic_ids"]:
                        payloads.append(fetch_google_locations(
                            path, canonic_id=cid, timeout=FETCH_TIMEOUT_SECONDS, on_progress=on_progress))
                else:
                    raise RuntimeError(f"{tag['name']} has no known Google ids yet; sync with Google first")
                events = []
                for payload in payloads:
                    events.extend(history.google_events(payload, keystore.get_index(username)))
                    for failure in payload.get("failures") or []:
                        subtag = _short(failure.get("name") or failure.get("canonic_id") or "?")
                        error = state.clean_error_message(failure.get("error") or "failed")
                        result["failed"].append({"tag": tag["name"], "subtag": subtag, "error": error})
                        state.add_alert(user_id, "google", alert_type, error, target=f"{tag['name']} / {subtag}")
                result["stored"] += history.store_events(user_id, events)
                result["fetched"].append(tag["name"])
            except Exception as exc:
                error = state.clean_error_message(exc)
                logger.warning("Google fetch failed for %s / %s: %s", username, tag["name"], error)
                result["failed"].append({"tag": tag["name"], "subtag": None, "error": error})
                state.add_alert(user_id, "google", alert_type, error, target=tag["name"])
            done = tag_done_before + max(1, steps(tag))
            progress(progress=done)

        if result["failed"]:
            first = result["failed"][0]
            where = first["tag"] + (f" / {first['subtag']}" if first["subtag"] else "")
            state.set_status(user_id, "google", False, kind, error=f"{where}: {first['error']}", details=result)
        else:
            state.set_status(user_id, "google", True, kind, details=result)
        logger.info("Google %s for %s: %d tags, %d new events, %d failures",
                    kind, username, len(result["fetched"]), result["stored"], len(result["failed"]))
        if result["failed"] and not result["fetched"]:
            job_state = "failed"
        elif result["failed"]:
            job_state = "partial"
        else:
            job_state = "done"
        return job_state, result
    finally:
        lock.release()


def sync_with_google(user, manual=False, progress=None):
    """List devices on Google (updating the canonic-id cache) and announce upcoming keys.

    Manual syncs always upload and prune trackers Google no longer reports (their tags
    and history are deleted). Automatic syncs let the bridge skip recent uploads.
    """
    progress = progress or (lambda **_: None)
    user_id, username = user["id"], user["username"]
    path = keystore.secrets_path(username)
    if not path:
        raise ValueError("upload secrets.json first")
    lock = _locked(username)
    kind = "sync_keys" if manual else "auto_sync_keys"
    try:
        progress(stage="Listing trackers and announcing keys", total=1)
        before = keystore.capture_canonic_state(path) if manual else None
        payload = refresh_google_announcements(path, force_upload=manual)
        pruned = {"pruned_canonic_ids": 0, "pruned_compounds": 0}
        if manual and isinstance(payload, dict) and not payload.get("skipped"):
            pruned = keystore.prune_stale_google_entries(path, before)
        keystore.invalidate(username)
        sync = inventory.sync_user(user_id, username, prune=manual)
        details = dict(payload) if isinstance(payload, dict) else {}
        details.update(pruned)
        details["tags_added"] = len(sync["added"])
        details["tags_removed"] = len(sync["removed"])
        now = time.time()
        db.kv_set(db.user_key(user_id, "last_google_key_refresh"), now, user_id)
        db.kv_set(db.user_key(user_id, "google_fetch_not_before"), now + SETTLE_AFTER_SYNC_SECONDS, user_id)
        progress(progress=1)
        status = state.set_status(user_id, "google_keys", True, kind, details=details)
        return "done", status["details"]
    except Exception as exc:
        state.set_status(user_id, "google_keys", False, kind, error=exc)
        state.add_alert(user_id, "google", "key_sync_error", exc, target="announcements")
        db.kv_set(db.user_key(user_id, "google_refresh_retry_after"), time.time() + 300, user_id)
        raise
    finally:
        lock.release()


# ── automatic scheduling ──

def key_sync_due(runtime, user_id, now):
    hours = runtime.get("google_key_refresh_interval_hours", 24)
    if hours <= 0:
        return False
    last = float(db.kv_get(db.user_key(user_id, "last_google_key_refresh"), 0))
    retry_after = float(db.kv_get(db.user_key(user_id, "google_refresh_retry_after"), 0))
    return now - last > hours * 3600 and now >= retry_after


def fetch_due(runtime, user_id, now):
    interval = runtime.get("google_auto_query_interval_min", 60) * 60
    if interval <= 0 or not runtime.get("local_history", True):
        return False
    if now < float(db.kv_get(db.user_key(user_id, "google_fetch_not_before"), 0)):
        return False
    return now - float(db.kv_get(db.user_key(user_id, "last_google_fetch"), 0)) >= interval


def mark_fetch_queued(user_id):
    db.kv_set(db.user_key(user_id, "last_google_fetch"), time.time(), user_id)


def key_sync_status(runtime, user_id):
    hours = runtime.get("google_key_refresh_interval_hours", 24)
    last = float(db.kv_get(db.user_key(user_id, "last_google_key_refresh"), 0))
    return {
        "interval_hours": hours,
        "last_unix": int(last) if last else None,
        "next_due_unix": int(last + hours * 3600) if hours > 0 and last else None,
    }
