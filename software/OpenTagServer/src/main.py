#!/usr/bin/env python3
import logging
import threading
import time

from flask import Flask, g, jsonify, redirect, render_template, url_for

from api import api_bp
from auth import auth_bp, login_required
from config import load_runtime_config
from fetch_google import fetch_google_locations, refresh_google_announcements
from storage import (
    create_redis_client,
    merge_apple_keys,
    purge_old_history_events,
    purge_old_alerts,
    read_user_accessories,
    read_user_secrets,
    set_fetch_status,
    append_alert,
    user_secrets_path,
)

logger = logging.getLogger(__name__)


def _configure_logging(runtime):
    log_level = str(runtime.get("log_level", "INFO")).upper()
    numeric_level = getattr(logging, log_level, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger().setLevel(numeric_level)
    logger.setLevel(numeric_level)
    logger.info("Configured logging at %s", log_level)


def _apple_merge_background_loop(app: Flask):
    while True:
        try:
            with app.app_context():
                runtime = app.config["OPENTAG"]
                redis_client = app.config["REDIS"]
                users = runtime.get("users", {})
                for username in users.keys():
                    try:
                        accessories = read_user_accessories(username)
                        merged = merge_apple_keys(accessories)
                        payload = {
                            "provider": "apple_merge",
                            "ok": True,
                            "kind": "merge_keys",
                            "timestamp_unix": int(time.time()),
                            "details": {"unique_key_count": merged.get("unique_key_count", 0)},
                        }
                        set_fetch_status(redis_client, username, "apple_merge", payload)
                    except Exception as exc:
                        payload = {
                            "provider": "apple_merge",
                            "ok": False,
                            "kind": "merge_keys",
                            "timestamp_unix": int(time.time()),
                            "error": str(exc),
                        }
                        set_fetch_status(redis_client, username, "apple_merge", payload)
        except Exception:
            pass

        time.sleep(300)


def _google_auto_refresh_loop(app: Flask):
    logger.info("Google auto-refresh worker started")
    while True:
        query_interval_min = 60
        try:
            with app.app_context():
                runtime = app.config["OPENTAG"]
                redis_client = app.config["REDIS"]
                users = runtime.get("users", {})
                local_history = runtime.get("local_history", True)
                query_interval_min = runtime.get("google_auto_query_interval_min", 60)
                key_refresh_hours = runtime.get("google_key_refresh_interval_hours", 24)

                if not local_history or query_interval_min <= 0:
                    logger.debug("Google auto-refresh disabled (local_history=%s, interval=%d)", local_history, query_interval_min)

                for username in users.keys():
                    try:
                        secrets_path = user_secrets_path(username)
                        if not secrets_path:
                            continue

                        # Key refresh (every key_refresh_hours)
                        last_refresh_key = "user:" + username + ":meta:last_google_key_refresh"
                        last_refresh = redis_client.get(last_refresh_key)
                        last_refresh_ts = float(last_refresh) if last_refresh else 0
                        now = time.time()
                        if (now - last_refresh_ts) > (key_refresh_hours * 3600):
                            logger.info("Auto-refreshing Google keys for user %s (last refresh %.0f hours ago)", username, (now - last_refresh_ts) / 3600)
                            try:
                                payload = refresh_google_announcements(secrets_path, force_upload=False)
                                status_payload = {
                                    "provider": "google",
                                    "ok": True,
                                    "kind": "auto_refresh_keys",
                                    "timestamp_unix": int(now),
                                    "details": payload,
                                }
                                set_fetch_status(redis_client, username, "google", status_payload)
                                redis_client.set(last_refresh_key, str(now))
                                logger.info("Google key refresh successful for user %s", username)
                            except Exception as exc:
                                logger.error("Google key refresh failed for user %s: %s", username, exc)
                                append_alert(redis_client, username, {
                                    "provider": "google",
                                    "error": str(exc),
                                    "type": "auto_key_refresh",
                                    "target": "keys",
                                    "timestamp_unix": int(now),
                                })

                        # Location fetch (every query_interval_min)
                        if local_history and query_interval_min > 0:
                            last_fetch_key = "user:" + username + ":meta:last_google_fetch"
                            last_fetch = redis_client.get(last_fetch_key)
                            last_fetch_ts = float(last_fetch) if last_fetch else 0
                            if (now - last_fetch_ts) > (query_interval_min * 60):
                                logger.info("Auto-fetching Google locations for user %s", username)
                                try:
                                    secrets_payload = read_user_secrets(username)
                                    compounds = []
                                    if secrets_payload:
                                        from storage import extract_google_compounds
                                        compounds = extract_google_compounds(secrets_payload)

                                    all_events = []
                                    for compound in compounds:
                                        try:
                                            payload = fetch_google_locations(secrets_path, compound_name=compound.get("base_name"), timeout=45)
                                            status_payload = {
                                                "provider": "google",
                                                "ok": True,
                                                "kind": "auto_fetch_location",
                                                "timestamp_unix": int(now),
                                                "target": compound.get("base_name"),
                                                "details": payload,
                                            }
                                            set_fetch_status(redis_client, username, "google", status_payload)
                                            details = payload.get("details") if isinstance(payload, dict) else None
                                            if isinstance(details, dict):
                                                targets = details.get("targets", [])
                                                for target in targets:
                                                    if not isinstance(target, dict):
                                                        continue
                                                    label = target.get("label") or target.get("resolved_device_name") or "google-tag"
                                                    result = target.get("result", {})
                                                    locations = result.get("locations", [])
                                                    for loc in locations:
                                                        if isinstance(loc, dict) and loc.get("type") == "geo":
                                                            all_events.append({
                                                                "provider": "google",
                                                                "tag": label,
                                                                "latitude": loc.get("latitude"),
                                                                "longitude": loc.get("longitude"),
                                                                "timestamp_unix": int(loc.get("time_unix", 0) or 0),
                                                                "status": loc.get("status"),
                                                                "source": "google_compound",
                                                            })
                                        except Exception as exc:
                                            logger.warning("Google auto-fetch failed for compound %s, user %s: %s", compound.get("base_name"), username, exc)
                                            append_alert(redis_client, username, {
                                                "provider": "google",
                                                "error": str(exc),
                                                "type": "auto_fetch_error",
                                                "target": compound.get("base_name"),
                                                "timestamp_unix": int(now),
                                            })

                                    if all_events:
                                        from storage import append_history_events
                                        append_history_events(redis_client, username, all_events)
                                        logger.info("Stored %d Google location events for user %s", len(all_events), username)

                                    redis_client.set(last_fetch_key, str(now))
                                except Exception as exc:
                                    logger.error("Google auto-fetch failed for user %s: %s", username, exc)
                    except Exception as exc:
                        logger.error("Google auto-refresh error for user %s: %s", username, exc)
        except Exception as exc:
            logger.error("Google auto-refresh loop error: %s", exc)

        time.sleep(max(60, query_interval_min * 60 // 4))


def _apple_auto_fetch_loop(app: Flask):
    logger.info("Apple auto-fetch worker started")
    while True:
        query_interval_min = 600
        try:
            with app.app_context():
                runtime = app.config["OPENTAG"]
                redis_client = app.config["REDIS"]
                users = runtime.get("users", {})
                local_history = runtime.get("local_history", True)
                query_interval_min = runtime.get("apple_auto_query_interval_min", 600)

                if not local_history or query_interval_min <= 0:
                    logger.debug("Apple auto-fetch disabled (local_history=%s, interval=%d)", local_history, query_interval_min)

                for username in users.keys():
                    try:
                        accessories = read_user_accessories(username)
                        if not accessories:
                            continue

                        last_fetch_key = "user:" + username + ":meta:last_apple_fetch"
                        last_fetch = redis_client.get(last_fetch_key)
                        last_fetch_ts = float(last_fetch) if last_fetch else 0
                        now = time.time()
                        if (now - last_fetch_ts) > (query_interval_min * 60):
                            logger.info("Auto-fetching Apple locations for user %s", username)
                            try:
                                from fetch_apple import fetch_apple_locations
                                from storage import append_history_events
                                payload = fetch_apple_locations(runtime.get("haystack", {}), accessories, days=7, timeout=30)
                                status_payload = {
                                    "provider": "apple",
                                    "ok": True,
                                    "kind": "auto_fetch_location",
                                    "timestamp_unix": int(now),
                                    "details": payload,
                                }
                                set_fetch_status(redis_client, username, "apple", status_payload)

                                details = payload.get("details") if isinstance(payload, dict) else None
                                events = []
                                if isinstance(details, dict):
                                    reports = details.get("reports", [])
                                    for report in reports:
                                        if not isinstance(report, dict):
                                            continue
                                        ts_iso = report.get("timestamp", "")
                                        ts_unix = 0
                                        if isinstance(ts_iso, str) and ts_iso:
                                            try:
                                                ts_unix = int(time.mktime(time.strptime(ts_iso[:19], "%Y-%m-%dT%H:%M:%S")))
                                            except Exception:
                                                ts_unix = 0
                                        events.append({
                                            "provider": "apple",
                                            "tag": (report.get("source") or {}).get("tag", "apple-tag"),
                                            "source_file": (report.get("source") or {}).get("file", ""),
                                            "latitude": report.get("latitude"),
                                            "longitude": report.get("longitude"),
                                            "timestamp_unix": ts_unix,
                                            "status": "APPLE",
                                            "source": "apple_haystack",
                                        })

                                if events:
                                    append_history_events(redis_client, username, events)
                                    logger.info("Stored %d Apple location events for user %s", len(events), username)

                                redis_client.set(last_fetch_key, str(now))
                            except Exception as exc:
                                logger.error("Apple auto-fetch failed for user %s: %s", username, exc)
                                append_alert(redis_client, username, {
                                    "provider": "apple",
                                    "error": str(exc),
                                    "type": "auto_fetch_error",
                                    "target": "apple_locations",
                                    "timestamp_unix": int(now),
                                })
                    except Exception as exc:
                        logger.error("Apple auto-fetch error for user %s: %s", username, exc)
        except Exception as exc:
            logger.error("Apple auto-fetch loop error: %s", exc)

        time.sleep(max(60, query_interval_min * 60 // 4))


def _history_cleanup_loop(app: Flask):
    logger.info("History cleanup worker started")
    while True:
        try:
            with app.app_context():
                runtime = app.config["OPENTAG"]
                redis_client = app.config["REDIS"]
                users = runtime.get("users", {})
                retention_days = runtime.get("history_retention_days", 30)

                if retention_days <= 0:
                    logger.debug("History cleanup disabled (retention_days=%d)", retention_days)
                    time.sleep(3600)
                    continue

                max_age_seconds = retention_days * 86400
                for username in users.keys():
                    try:
                        removed = purge_old_history_events(redis_client, username, max_age_seconds)
                        if removed > 0:
                            logger.info("Purged %d old history events for user %s (retention=%d days)", removed, username, retention_days)
                    except Exception as exc:
                        logger.error("History cleanup error for user %s: %s", username, exc)
        except Exception as exc:
            logger.error("History cleanup loop error: %s", exc)

        time.sleep(3600)


def _alerts_cleanup_loop(app: Flask):
    logger.info("Alerts cleanup worker started")
    while True:
        try:
            with app.app_context():
                runtime = app.config["OPENTAG"]
                redis_client = app.config["REDIS"]
                users = runtime.get("users", {})
                retention_days = runtime.get("history_retention_days", 30)

                if retention_days <= 0:
                    logger.debug("Alerts cleanup disabled (retention_days=%d)", retention_days)
                    time.sleep(3600)
                    continue

                max_age_seconds = retention_days * 86400
                for username in users.keys():
                    try:
                        removed = purge_old_alerts(redis_client, username, max_age_seconds)
                        if removed > 0:
                            logger.info("Purged %d old alerts for user %s (retention=%d days)", removed, username, retention_days)
                    except Exception as exc:
                        logger.error("Alerts cleanup error for user %s: %s", username, exc)
        except Exception as exc:
            logger.error("Alerts cleanup loop error: %s", exc)

        time.sleep(3600)


def create_app() -> Flask:
    app = Flask(__name__)

    runtime = load_runtime_config()
    _configure_logging(runtime)
    app.config["OPENTAG"] = runtime
    app.config["SECRET_KEY"] = runtime["secret_key"]
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False

    app.config["REDIS"] = create_redis_client(runtime)

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)

    @app.get("/")
    @login_required
    def dashboard():
        return render_template("dashboard.html", username=g.user["username"])

    @app.get("/healthz")
    def healthz():
        redis_client = app.config["REDIS"]
        redis_ok = False
        try:
            redis_ok = bool(redis_client.ping())
        except Exception:
            redis_ok = False

        return jsonify({"ok": redis_ok})

    @app.get("/login")
    def login_page():
        return redirect(url_for("auth.login"))

    # Start background workers
    worker = threading.Thread(target=_apple_merge_background_loop, args=(app,), daemon=True)
    worker.start()

    google_worker = threading.Thread(target=_google_auto_refresh_loop, args=(app,), daemon=True)
    google_worker.start()

    apple_worker = threading.Thread(target=_apple_auto_fetch_loop, args=(app,), daemon=True)
    apple_worker.start()

    cleanup_worker = threading.Thread(target=_history_cleanup_loop, args=(app,), daemon=True)
    cleanup_worker.start()

    alerts_worker = threading.Thread(target=_alerts_cleanup_loop, args=(app,), daemon=True)
    alerts_worker.start()

    return app


app = create_app()


if __name__ == "__main__":
    logger.info("Starting OpenTagServer")
    app.run(host="0.0.0.0", port=8080, debug=False)
