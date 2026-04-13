#!/usr/bin/env python3
import logging
import threading
import time

from flask import Flask, g, jsonify, redirect, render_template, url_for

from api import api_bp
from auth import auth_bp, login_required
from config import load_runtime_config
from storage import create_redis_client, merge_apple_keys, read_user_accessories, set_fetch_status


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


def create_app() -> Flask:
    app = Flask(__name__)

    runtime = load_runtime_config()
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

    worker = threading.Thread(target=_apple_merge_background_loop, args=(app,), daemon=True)
    worker.start()

    return app


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app.run(host="0.0.0.0", port=8080, debug=False)
