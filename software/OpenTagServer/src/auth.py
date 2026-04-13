import crypt
import functools
import json
import secrets
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, make_response, redirect, render_template, request, url_for


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
SESSION_COOKIE = "opentag_session"
SESSION_TTL_SECONDS = int(timedelta(hours=24).total_seconds())


def _expects_json_response():
    return request.path.startswith("/api") or request.is_json or "application/json" in (request.headers.get("Accept") or "")


def _get_users():
    runtime = current_app.config["OPENTAG"]
    return runtime.get("users", {})


def _verify_password(password, password_hash):
    return crypt.crypt(password, password_hash) == password_hash


def _session_key(token):
    return f"session:{token}"


def _set_session(token, payload):
    redis_client = current_app.config["REDIS"]
    redis_client.setex(_session_key(token), SESSION_TTL_SECONDS, json.dumps(payload))


def _read_session(token):
    redis_client = current_app.config["REDIS"]
    raw = redis_client.get(_session_key(token))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    redis_client.expire(_session_key(token), SESSION_TTL_SECONDS)
    return payload


def _clear_session(token):
    redis_client = current_app.config["REDIS"]
    redis_client.delete(_session_key(token))


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            wants_json = _expects_json_response()
            if wants_json:
                return jsonify({"error": "authentication required"}), 401
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


@auth_bp.before_app_request
def load_current_user():
    g.user = None
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return

    payload = _read_session(token)
    if not payload:
        return

    username = payload.get("username")
    role = payload.get("role")
    if not username:
        return

    g.user = {"username": username, "role": role or "user", "session_token": token}


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if g.user:
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    payload = request.get_json(silent=True) or {}
    if not payload:
        payload = request.form.to_dict() if request.form else {}

    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    expects_json = _expects_json_response()

    if not username or not password:
        if expects_json:
            return jsonify({"error": "username and password required"}), 400
        return render_template("login.html", error="Username and password are required.", username=username), 400

    user = _get_users().get(username)
    if not user:
        if expects_json:
            return jsonify({"error": "invalid credentials"}), 401
        return render_template("login.html", error="Invalid username or password.", username=username), 401

    password_hash = user.get("password_hash") or ""
    if not password_hash or not _verify_password(password, password_hash):
        if expects_json:
            return jsonify({"error": "invalid credentials"}), 401
        return render_template("login.html", error="Invalid username or password.", username=username), 401

    token = secrets.token_urlsafe(32)
    session_payload = {"username": username, "role": user.get("role", "user")}
    _set_session(token, session_payload)

    if expects_json:
        response = make_response(jsonify({"ok": True, "user": session_payload}))
    else:
        response = make_response(redirect(url_for("dashboard")))

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=False,
        samesite="Lax",
    )
    return response


@auth_bp.post("/logout")
def logout():
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _clear_session(token)

    expects_json = _expects_json_response()
    if expects_json:
        response = make_response(jsonify({"ok": True}))
    else:
        response = make_response(redirect(url_for("auth.login")))

    response.delete_cookie(SESSION_COOKIE)
    return response


@auth_bp.get("/me")
@login_required
def me():
    return jsonify({"user": g.user})
