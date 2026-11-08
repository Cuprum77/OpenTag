#!/usr/bin/env python3
import logging
import os
import threading

from flask import Flask, g, jsonify, redirect, render_template, url_for

import auth
import db
import history
import inventory
import keystore
import state
from admin import admin_bp
from api import api_bp
from apple_requests import AppleRequestLimiter
from apple_worker import apple_auto_fetch_loop
from config import load_runtime_config
from google_service import google_auto_loop

logger = logging.getLogger(__name__)


def _configure_logging(runtime):
    level = getattr(logging, str(runtime.get("log_level", "INFO")).upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)


def _maintenance_loop(app, stop_event):
    """Hourly: per-device history retention, expired sessions, old dismissed alerts."""
    logger.info("Maintenance worker started")
    while not stop_event.is_set():
        try:
            removed = history.purge_expired(app.config["OPENTAG"]["history_retention_days"])
            if removed:
                logger.info("Retention removed %d history events", removed)
            auth.purge_expired_sessions()
            state.purge_dismissed_alerts()
        except Exception as exc:
            logger.error("Maintenance error: %s", exc)
        stop_event.wait(3600)


def _startup_sync():
    """Load every user's keyfiles and reconcile tags (files may have changed while stopped)."""
    for user in db.query("SELECT id, username FROM users WHERE role = 'user'"):
        try:
            inventory.sync_user(user["id"], user["username"], prune=True)
            index = keystore.get_index(user["username"])
            for err in index["errors"]["apple"] + index["errors"]["google"]:
                state.add_alert(user["id"], "system", "keyfile_error", f"{err['file']}: {err['error']}", target=err["file"])
        except Exception as exc:
            logger.error("Startup sync failed for %s: %s", user["username"], exc)


def start_workers(app):
    stop_event = threading.Event()
    for target in (google_auto_loop, apple_auto_fetch_loop, _maintenance_loop):
        threading.Thread(target=target, args=(app, stop_event), daemon=True, name=target.__name__).start()
    app.config["WORKERS_STOP"] = stop_event
    return stop_event


def create_app(start_background=None) -> Flask:
    app = Flask(__name__)

    runtime = load_runtime_config()
    _configure_logging(runtime)
    app.config["OPENTAG"] = runtime
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("OPENTAG_SECURE_COOKIES", "").lower() in {"1", "true", "yes"}
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    db.init(runtime["database_path"])
    keystore.init(runtime["secrets_dir"])
    auth.sync_config_admin(runtime["admin"])
    app.config["APPLE_REQUEST_LIMITER"] = AppleRequestLimiter(runtime["haystack"])

    app.register_blueprint(auth.auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)

    @app.get("/")
    @auth.login_required
    def home():
        if g.user["role"] == "admin":
            return redirect(url_for("admin_page"))
        return render_template("dashboard.html", username=g.user["username"])

    @app.get("/admin")
    @auth.admin_required
    def admin_page():
        return render_template("admin.html", username=g.user["username"])

    @app.get("/account")
    @auth.user_required
    def account_page():
        return render_template("account.html", username=g.user["username"])

    @app.get("/healthz")
    def healthz():
        try:
            db.query_one("SELECT 1")
            return jsonify({"ok": True})
        except Exception:
            return jsonify({"ok": False}), 503

    @app.get("/login")
    def login_page():
        return redirect(url_for("auth.login"))

    _startup_sync()
    if start_background is None:
        start_background = os.environ.get("OPENTAG_DISABLE_WORKERS", "").lower() not in {"1", "true", "yes"}
    if start_background:
        start_workers(app)
    return app


if os.environ.get("OPENTAG_NO_AUTO_APP", "").lower() not in {"1", "true", "yes"}:
    app = create_app()


if __name__ == "__main__":
    logger.info("Starting OpenTagServer")
    app.run(host="0.0.0.0", port=8080, debug=False)
