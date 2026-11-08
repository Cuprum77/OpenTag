"""Account management for the admin. The admin never sees keys, devices or locations."""
import logging

from flask import Blueprint, g, jsonify, request

import auth
import db
import keystore


logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def _user_row(user_id):
    row = db.query_one("SELECT id, username, role FROM users WHERE id = ?", (user_id,))
    if not row or row["role"] != "user":
        return None
    return row


@admin_bp.get("/users")
@auth.admin_required
def users_list():
    return jsonify({"users": [
        {"id": r["id"], "username": r["username"], "role": r["role"], "created_unix": r["created_at"]}
        for r in auth.list_users()
    ]})


@admin_bp.post("/users")
@auth.admin_required
def users_create():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    try:
        user_id = auth.create_user(username, body.get("password") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    logger.info("Admin %s created user %s", g.user["username"], username)
    return jsonify({"ok": True, "user": {"id": user_id, "username": username, "role": "user"}}), 201


@admin_bp.post("/users/<int:user_id>/password")
@auth.admin_required
def users_reset_password(user_id):
    row = _user_row(user_id)
    if not row:
        return jsonify({"error": "user not found"}), 404
    body = request.get_json(silent=True) or {}
    try:
        auth.set_password(user_id, body.get("password") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    logger.info("Admin %s reset the password of %s", g.user["username"], row["username"])
    return jsonify({"ok": True})


@admin_bp.delete("/users/<int:user_id>")
@auth.admin_required
def users_delete(user_id):
    row = _user_row(user_id)
    if not row:
        return jsonify({"error": "user not found"}), 404
    # Removes the account, its sessions, tags, devices, history, alerts and keyfiles.
    auth.delete_user_row(user_id)
    keystore.delete_user_files(row["username"])
    logger.info("Admin %s deleted user %s", g.user["username"], row["username"])
    return jsonify({"ok": True})
