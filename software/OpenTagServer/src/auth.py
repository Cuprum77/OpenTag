import functools
import hashlib
import logging
import re
import secrets
import time

from flask import Blueprint, current_app, g, jsonify, make_response, redirect, render_template, request, url_for
from passlib.hash import md5_crypt, sha256_crypt, sha512_crypt
from werkzeug.security import check_password_hash, generate_password_hash

import db


logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
SESSION_COOKIE = "opentag_session"
SESSION_TTL_SECONDS = 24 * 3600
_SESSION_REFRESH_SLACK = 300
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MIN_PASSWORD_LENGTH = 8

_CRYPT_SCHEMES = {"$6$": sha512_crypt, "$5$": sha256_crypt, "$1$": md5_crypt}


# ── passwords ──

def hash_password(password):
    return generate_password_hash(password)


def verify_password(password, password_hash):
    if not password_hash:
        return False
    try:
        for prefix, scheme in _CRYPT_SCHEMES.items():
            if password_hash.startswith(prefix):
                # `openssl passwd -6` style hashes (used for the config admin)
                return scheme.verify(password, password_hash)
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False


def validate_new_password(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def validate_username(username):
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        raise ValueError("username may only contain letters, digits, '_' and '-' (max 64)")


# ── users ──

def sync_config_admin(admin_cfg):
    """Make the database admin match the config file (the config is the source of truth)."""
    username = admin_cfg["username"]
    validate_username(username)
    with db.transaction() as conn:
        existing = conn.execute("SELECT id, role FROM users WHERE username = ?", (username,)).fetchone()
        if existing and existing["role"] != "admin":
            raise ValueError(f"config admin username '{username}' is already used by a regular user")
        # Only one admin exists; a renamed admin replaces the old account.
        conn.execute("DELETE FROM users WHERE role = 'admin' AND username != ?", (username,))
        if existing:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (admin_cfg["password_hash"], existing["id"]))
        else:
            conn.execute(
                "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
                (username, admin_cfg["password_hash"], int(time.time())),
            )


def get_user(username):
    return db.query_one("SELECT * FROM users WHERE username = ?", (username,))


def list_users():
    """Users with their number of signed-in sessions."""
    return db.query(
        "SELECT u.id, u.username, u.role, u.created_at, "
        "(SELECT COUNT(*) FROM sessions s WHERE s.user_id = u.id AND s.expires_at >= ?) AS sessions "
        "FROM users u ORDER BY u.role, u.username",
        (int(time.time()),),
    )


def create_user(username, password):
    validate_username(username)
    validate_new_password(password)
    with db.transaction() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ValueError("user already exists")
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, 'user', ?)",
            (username, hash_password(password), int(time.time())),
        )
        return cur.lastrowid


def set_password(user_id, password):
    validate_new_password(password)
    with db.transaction() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def delete_user_row(user_id):
    with db.transaction() as conn:
        conn.execute("DELETE FROM kv WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ? AND role = 'user'", (user_id,))


# ── sessions ──

def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (_token_hash(token), user_id, int(time.time()) + SESSION_TTL_SECONDS),
    )
    return token


def read_session(token):
    now = int(time.time())
    row = db.query_one(
        "SELECT s.token_hash, s.expires_at, u.id, u.username, u.role FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (_token_hash(token),),
    )
    if not row or row["expires_at"] < now:
        return None
    # Sliding expiry, written at most every few minutes.
    if row["expires_at"] - now < SESSION_TTL_SECONDS - _SESSION_REFRESH_SLACK:
        db.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (now + SESSION_TTL_SECONDS, row["token_hash"]))
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def clear_session(token):
    db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def count_sessions(user_id):
    row = db.query_one("SELECT COUNT(*) AS n FROM sessions WHERE user_id = ? AND expires_at >= ?", (user_id, int(time.time())))
    return row["n"]


def revoke_sessions(user_id, keep_token=None):
    """Sign a user out everywhere (except the session `keep_token`, if given). Returns how many ended."""
    with db.transaction() as conn:
        if keep_token:
            cur = conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, _token_hash(keep_token)))
        else:
            cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return cur.rowcount


def purge_expired_sessions():
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))


# ── request gates ──

def _expects_json_response():
    return request.path.startswith("/api") or request.is_json or "application/json" in (request.headers.get("Accept") or "")


def _deny(status, message):
    if _expects_json_response():
        return jsonify({"error": message}), status
    if status == 401:
        return redirect(url_for("auth.login"))
    return redirect(url_for("home"))


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return _deny(401, "authentication required")
        return view(*args, **kwargs)
    return wrapped


def user_required(view):
    """Regular users only: the admin manages accounts and never sees keys or locations."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return _deny(401, "authentication required")
        if g.user["role"] != "user":
            return _deny(403, "not available for the admin account")
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return _deny(401, "authentication required")
        if g.user["role"] != "admin":
            return _deny(403, "admin only")
        return view(*args, **kwargs)
    return wrapped


@auth_bp.before_app_request
def load_current_user():
    g.user = None
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return
    user = read_session(token)
    if user:
        g.user = dict(user, session_token=token)


# ── routes ──

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if g.user:
            return redirect(url_for("home"))
        return render_template("login.html")

    payload = request.get_json(silent=True) or (request.form.to_dict() if request.form else {})
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    expects_json = _expects_json_response()

    def fail(message, status):
        if expects_json:
            return jsonify({"error": message}), status
        return render_template("login.html", error=message, username=username), status

    if not username or not password:
        return fail("Username and password are required.", 400)

    user = get_user(username)
    if not user or not verify_password(password, user["password_hash"]):
        logger.info("Failed login for %r from %s", username, request.remote_addr)
        return fail("Invalid username or password.", 401)

    token = create_session(user["id"])
    session_payload = {"username": user["username"], "role": user["role"]}
    response = make_response(jsonify({"ok": True, "user": session_payload}) if expects_json else redirect(url_for("home")))
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, httponly=True,
        secure=current_app.config.get("SESSION_COOKIE_SECURE", False), samesite="Lax",
    )
    return response


@auth_bp.post("/logout")
def logout():
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        clear_session(token)
    response = make_response(jsonify({"ok": True}) if _expects_json_response() else redirect(url_for("auth.login")))
    response.delete_cookie(SESSION_COOKIE)
    return response


@auth_bp.get("/me")
@login_required
def me():
    return jsonify({"user": {"username": g.user["username"], "role": g.user["role"]}})


@auth_bp.post("/password")
@user_required
def change_password():
    """Users change their own password. The admin password lives in the config file."""
    body = request.get_json(silent=True) or {}
    user = get_user(g.user["username"])
    if not verify_password(body.get("current_password") or "", user["password_hash"]):
        return jsonify({"error": "current password is incorrect"}), 400
    try:
        set_password(user["id"], body.get("new_password") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    # set_password ends all sessions; start a fresh one for this browser.
    token = create_session(user["id"])
    response = make_response(jsonify({"ok": True}))
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, httponly=True,
        secure=current_app.config.get("SESSION_COOKIE_SECURE", False), samesite="Lax",
    )
    return response


@auth_bp.get("/sessions")
@login_required
def sessions_info():
    return jsonify({"sessions": count_sessions(g.user["id"])})


@auth_bp.post("/sessions/revoke-others")
@login_required
def sessions_revoke_others():
    """Sign out every other browser of the current user; this one stays signed in."""
    ended = revoke_sessions(g.user["id"], keep_token=g.user["session_token"])
    logger.info("%s signed out %d other session(s)", g.user["username"], ended)
    return jsonify({"ok": True, "ended": ended})
