"""Google operations shared by the API and the background worker."""
import logging
import time

import db
import history
import inventory
import keystore
import state
from fetch_google import fetch_google_locations, refresh_google_announcements, user_lock


logger = logging.getLogger(__name__)

MANUAL_LOCK_WAIT_SECONDS = 600


class GoogleBusy(RuntimeError):
    pass


def _fetch_tag_payloads(secrets_path, tag, timeout):
    """Compound trackers with bridge metadata are fetched in one call; anything else
    (plain trackers, compound pieces without metadata) per canonic id."""
    if tag.get("compound_name"):
        return [fetch_google_locations(secrets_path, compound_name=tag["compound_name"], timeout=timeout)]
    if not tag["canonic_ids"]:
        raise RuntimeError(f"{tag['name']} has no known Google ids yet; sync with Google first")
    return [fetch_google_locations(secrets_path, canonic_id=cid, timeout=timeout) for cid in tag["canonic_ids"]]


def fetch_tags(user, stable_ids=None, manual=False, timeout=45):
    """Fetch Google locations for some (or all) Google tags of a user and store them.

    Returns {"stored": n, "fetched": [...], "failed": [{"tag", "error"}]}.
    Failures are recorded as alerts; a failing tag does not stop the others.
    """
    user_id, username = user["id"], user["username"]
    path = keystore.secrets_path(username)
    if not path:
        raise ValueError("upload secrets.json first")
    lock = user_lock(username)
    if not lock.acquire(timeout=MANUAL_LOCK_WAIT_SECONDS if manual else 0):
        raise GoogleBusy("another Google operation is still running for this account")
    try:
        inventory.sync_user(user_id, username)
        index = keystore.get_index(username)
        tags = [t for t in index["google"].values() if stable_ids is None or t["stable_id"] in stable_ids]
        if stable_ids is not None and len(tags) != len(set(stable_ids)):
            raise ValueError("unknown Google tag")
        kind = "fetch_location" if manual else "auto_fetch_location"
        result = {"stored": 0, "fetched": [], "failed": []}
        for tag in tags:
            try:
                events = []
                for payload in _fetch_tag_payloads(path, tag, timeout):
                    events.extend(history.google_events(payload, keystore.get_index(username)))
                result["stored"] += history.store_events(user_id, events)
                result["fetched"].append(tag["name"])
            except Exception as exc:
                logger.warning("Google fetch failed for %s / %s: %s", username, tag["name"], state.clean_error_message(exc))
                result["failed"].append({"tag": tag["name"], "error": state.clean_error_message(exc)})
                state.add_alert(user_id, "google", "manual_fetch_error" if manual else "auto_fetch_error", exc, target=tag["name"])
        if result["failed"]:
            first = result["failed"][0]
            state.set_status(user_id, "google", False, kind, error=f"{first['tag']}: {first['error']}", details=result)
        else:
            state.set_status(user_id, "google", True, kind, details=result)
        logger.info("Google %s for %s: %d tags, %d new events, %d failed",
                    kind, username, len(result["fetched"]), result["stored"], len(result["failed"]))
        return result
    finally:
        lock.release()


def sync_with_google(user, manual=False):
    """List devices on Google (updating the canonic-id cache) and announce upcoming keys.

    Manual syncs always upload and prune trackers Google no longer reports (their tags
    and history are deleted). Automatic syncs let the bridge skip recent uploads.
    """
    user_id, username = user["id"], user["username"]
    path = keystore.secrets_path(username)
    if not path:
        raise ValueError("upload secrets.json first")
    lock = user_lock(username)
    if not lock.acquire(timeout=MANUAL_LOCK_WAIT_SECONDS if manual else 0):
        raise GoogleBusy("another Google operation is still running for this account")
    kind = "sync_keys" if manual else "auto_sync_keys"
    try:
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
        db.kv_set(db.user_key(user_id, "last_google_key_refresh"), time.time(), user_id)
        return state.set_status(user_id, "google_keys", True, kind, details=details)
    except Exception as exc:
        state.set_status(user_id, "google_keys", False, kind, error=exc)
        state.add_alert(user_id, "google", "key_sync_error", exc, target="announcements")
        raise
    finally:
        lock.release()


def run_google_user(runtime, user):
    """One scheduler tick for a user: key announcement first, then location fetch."""
    user_id, username = user["id"], user["username"]
    if not keystore.secrets_path(username):
        return
    now = time.time()

    def key(name):
        return db.user_key(user_id, name)

    refresh_hours = runtime.get("google_key_refresh_interval_hours", 24)
    if refresh_hours > 0:
        last_refresh = float(db.kv_get(key("last_google_key_refresh"), 0))
        retry_after = float(db.kv_get(key("google_refresh_retry_after"), 0))
        if now - last_refresh > refresh_hours * 3600:
            if now < retry_after:
                return
            logger.info("Auto-syncing Google keys for %s (last %.0f h ago)", username, (now - last_refresh) / 3600)
            try:
                sync_with_google(user, manual=False)
                db.kv_set(key("google_fetch_not_before"), time.time() + runtime.get("google_refresh_settle_seconds", 15), user_id)
            except GoogleBusy:
                return
            except Exception as exc:
                logger.error("Google key sync failed for %s: %s", username, state.clean_error_message(exc))
                db.kv_set(key("google_refresh_retry_after"), time.time() + 300, user_id)
                return

    if time.time() < float(db.kv_get(key("google_fetch_not_before"), 0)):
        return
    interval_min = runtime.get("google_auto_query_interval_min", 60)
    if not runtime.get("local_history", True) or interval_min <= 0:
        return
    if now - float(db.kv_get(key("last_google_fetch"), 0)) <= interval_min * 60:
        return
    if not keystore.get_index(username)["google"]:
        return
    logger.info("Auto-fetching Google locations for %s", username)
    try:
        fetch_tags(user, manual=False)
    except GoogleBusy:
        return
    except Exception as exc:
        state.add_alert(user_id, "google", "worker_error", exc, target="google_auto_fetch")
    db.kv_set(key("last_google_fetch"), now, user_id)


def google_auto_loop(app, stop_event):
    logger.info("Google worker started")
    while not stop_event.is_set():
        try:
            runtime = app.config["OPENTAG"]
            for user in db.query("SELECT id, username FROM users WHERE role = 'user'"):
                try:
                    run_google_user(runtime, user)
                except Exception as exc:
                    logger.error("Google worker error for %s: %s", user["username"], exc)
        except Exception as exc:
            logger.error("Google worker loop error: %s", exc)
        stop_event.wait(15)
