"""Frequent bulk updates plus a resumable, low-rate singleton recovery sweep."""
import logging
import time
import uuid

import db
import history
import inventory
import keystore
import state
from apple_requests import AppleRequestDeferred
from fetch_apple import _build_key_map, fetch_apple_locations

logger = logging.getLogger(__name__)


def run_apple_user(runtime, user, request_limiter):
    """Perform due bulk work first, otherwise one singleton fetch."""
    if not runtime.get("local_history", True):
        return
    cfg = runtime.get("haystack", {})
    bulk_interval = runtime.get("apple_auto_query_interval_min", 600) * 60
    sweep_enabled = runtime.get("apple_singleton_sweep_enabled", True)
    if bulk_interval <= 0 and not sweep_enabled:
        return
    user_id, username = user["id"], user["username"]
    inventory.sync_user(user_id, username)
    rows_all = keystore.apple_rows(keystore.get_index(username))
    if not rows_all:
        return

    def key(name):
        return db.user_key(user_id, name)

    now = time.time()
    last_bulk = float(db.kv_get(key("last_apple_fetch"), 0))
    last_single = float(db.kv_get(key("last_apple_singleton"), 0))
    sweep_state = None
    sweep_hours = max(0, runtime.get("apple_singleton_sweep_interval_hours", 0))
    if bulk_interval > 0 and now - last_bulk >= bulk_interval:
        kind = "auto_fetch_location"
        # Record attempts too, so a failed bulk job cannot starve the sweep.
        db.kv_set(key("last_apple_fetch"), now, user_id)
        rows = rows_all
    elif sweep_enabled:
        interval = max(1, runtime.get("apple_singleton_interval_seconds", 60))
        if now - last_single < interval:
            return
        keys, sources = _build_key_map(rows_all)
        ids = list(keys)
        if not ids:
            return
        if sweep_hours > 0:
            sweep_state = db.kv_get(key("apple_singleton_sweep"), {}) or {}
            startup = runtime.get("apple_worker_start_id", "default")
            if sweep_state.get("startup") != startup:
                # Resume an interrupted pass; otherwise start a pass at startup.
                sweep_state["startup"] = startup
                sweep_state["next_due"] = 0
            if not sweep_state.get("pending"):
                if now < sweep_state.get("next_due", 0):
                    db.kv_set(key("apple_singleton_sweep"), sweep_state, user_id)
                    return
                sweep_state["pending"] = ids.copy()
            sweep_state["pending"] = [k for k in sweep_state["pending"] if k in keys]
            if not sweep_state["pending"]:
                sweep_state["next_due"] = now + sweep_hours * 3600
                db.kv_set(key("apple_singleton_sweep"), sweep_state, user_id)
                return
            db.kv_set(key("apple_singleton_sweep"), sweep_state, user_id)
            selected = sweep_state["pending"][0]
        else:
            previous = db.kv_get(key("apple_singleton_cursor"))
            index = (ids.index(previous) + 1) % len(ids) if previous in keys else 0
            selected = ids[index]
        source = sources[selected]
        rows = [{"privateKey": keys[selected], "name": source["tag"], "tag_id": source["tag_id"],
                 "source_file": source["file"]}]
        kind = "singleton_fetch_location"
        db.kv_set(key("last_apple_singleton"), now, user_id)
    else:
        return

    try:
        payload = fetch_apple_locations(
            cfg, rows, days=runtime.get("apple_query_days", 7), timeout=30,
            batch_size=runtime.get("apple_bulk_batch_size", 200),
            _request_callback=request_limiter.post)
        # Persist history before advancing the cursor. Empty results do not clear it.
        history.store_events(user_id, history.apple_events(payload))
        if kind == "singleton_fetch_location":
            db.kv_set(key("apple_singleton_cursor"), selected, user_id)
            if sweep_state is not None:
                sweep_state["pending"].pop(0)
                if not sweep_state["pending"]:
                    sweep_state["next_due"] = time.time() + sweep_hours * 3600
                db.kv_set(key("apple_singleton_sweep"), sweep_state, user_id)
        state.set_status(user_id, "apple_singleton" if kind == "singleton_fetch_location" else "apple",
                         True, kind, details=payload)
    except AppleRequestDeferred:
        # No HTTP attempt: leave work due rather than postponing bulk for hours.
        if kind == "auto_fetch_location":
            db.kv_set(key("last_apple_fetch"), last_bulk, user_id)
        else:
            db.kv_set(key("last_apple_singleton"), last_single, user_id)
    except Exception as exc:
        # Keep failed singleton cursor for a later, rate-limited retry.
        logger.warning("Apple %s failed for user %s (%s)", kind, username, type(exc).__name__)
        if kind == "auto_fetch_location":
            state.set_status(user_id, "apple", False, kind, error=exc)
        state.add_alert(user_id, "apple", "auto_fetch_error", "Apple fetch failed; will retry later", target=kind)


def apple_auto_fetch_loop(app, stop_event):
    # A new id per process start: the sweep starts a new pass (or resumes) after a restart.
    app.config["OPENTAG"]["apple_worker_start_id"] = uuid.uuid4().hex
    logger.info("Apple bulk and singleton worker started")
    while not stop_event.is_set():
        try:
            runtime = app.config["OPENTAG"]
            for user in db.query("SELECT id, username FROM users WHERE role = 'user'"):
                try:
                    run_apple_user(runtime, user, app.config["APPLE_REQUEST_LIMITER"])
                except Exception as exc:
                    logger.warning("Apple worker failed for user %s (%s: %s)", user["username"], type(exc).__name__, exc)
        except Exception as exc:
            logger.warning("Apple worker loop failed (%s)", type(exc).__name__)
        stop_event.wait(5)
