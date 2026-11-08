"""Data API for regular users (the admin account has no access to any of this)."""
import logging
import time

from flask import Blueprint, current_app, g, jsonify, request, send_file

import apple_service
import google_service
import history
import inventory
import jobs
import keystore
import state
from auth import user_required


logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _uid():
    return g.user["id"]


def _runtime():
    return current_app.config["OPENTAG"]


def _scheduler():
    return current_app.config["SCHEDULER"]


def _error(message, status=400):
    return jsonify({"error": message}), status


def _body():
    return request.get_json(silent=True) or {}


def _string_list(value, field):
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{field} must be a list of strings")
    return value


# ── keyfiles ──

@api_bp.get("/keyfiles")
@user_required
def keyfiles_list():
    return jsonify({"files": keystore.list_files(g.user["username"])})


@api_bp.get("/keyfiles/<path:filename>/download")
@user_required
def keyfiles_download(filename):
    try:
        path = keystore.file_path(g.user["username"], filename)
    except FileNotFoundError:
        return _error("file not found", 404)
    except ValueError as exc:
        return _error(str(exc))
    return send_file(str(path), as_attachment=True, download_name=filename)


@api_bp.get("/keyfiles/<path:filename>/delete-preview")
@user_required
def keyfiles_delete_preview(filename):
    try:
        keystore.file_path(g.user["username"], filename)
    except FileNotFoundError:
        return _error("file not found", 404)
    except ValueError as exc:
        return _error(str(exc))
    inventory.sync_user(_uid(), g.user["username"])
    return jsonify(inventory.delete_preview(_uid(), g.user["username"], filename))


@api_bp.delete("/keyfiles/<path:filename>")
@user_required
def keyfiles_delete(filename):
    username = g.user["username"]
    try:
        deleted = keystore.delete_file(username, filename)
    except FileNotFoundError:
        return _error("file not found", 404)
    except ValueError as exc:
        return _error(str(exc))
    # Tags that no longer have key material anywhere are deleted with their history.
    sync = inventory.sync_user(_uid(), username, prune=True)
    if deleted["category"] == "secrets":
        state.clear_status(_uid(), "google")
        state.clear_status(_uid(), "google_keys")
    logger.info("User %s deleted %s (%d tags removed)", username, filename, len(sync["removed"]))
    return jsonify({"ok": True, "deleted": deleted, "tags_removed": len(sync["removed"])})


@api_bp.post("/upload/accessories")
@user_required
def upload_accessories():
    username = g.user["username"]
    try:
        saved = keystore.save_accessories_upload(username, request.files.get("file"))
    except ValueError as exc:
        return _error(str(exc))
    sync = inventory.sync_user(_uid(), username)
    warnings = keystore.get_index(username)["warnings"]
    logger.info("User %s uploaded %s (%d accessories, %d new tags)", username, saved["filename"], saved["items"], len(sync["added"]))
    job = None
    if sync["added"] and _runtime()["haystack"].get("endpoint"):
        # First fetch for new tags right away, without the automatic request spacing.
        job = _scheduler().submit(_uid(), "apple_fetch", "upload", {"tag_ids": sorted(sync["added"])})
    return jsonify({"ok": True, "saved": saved, "tags_added": len(sync["added"]), "warnings": warnings, "job": job})


@api_bp.post("/upload/secrets")
@user_required
def upload_secrets():
    username = g.user["username"]
    try:
        saved = keystore.save_secrets_upload(username, request.files.get("file"))
    except ValueError as exc:
        return _error(str(exc))
    # On replacement, Google tags whose ids still match keep their history.
    sync = inventory.sync_user(_uid(), username, prune=True)
    logger.info("User %s uploaded secrets.json (replaced=%s, +%d/-%d tags)",
                username, saved["replaced"], len(sync["added"]), len(sync["removed"]))
    return jsonify({"ok": True, "saved": saved, "tags_added": len(sync["added"]), "tags_removed": len(sync["removed"])})


# ── inventory / devices ──

@api_bp.get("/inventory")
@user_required
def inventory_get():
    inventory.sync_user(_uid(), g.user["username"])
    return jsonify(inventory.get_inventory(_uid(), g.user["username"], _runtime()["history_retention_days"]))


@api_bp.post("/devices")
@user_required
def devices_group():
    body = _body()
    try:
        device_id = inventory.group_tags(_uid(), body.get("name"), body.get("color") or "#ff961f",
                                         _string_list(body.get("tag_ids"), "tag_ids") or [],
                                         icon=body.get("icon"))
    except ValueError as exc:
        return _error(str(exc))
    return jsonify({"ok": True, "device_id": device_id}), 201


@api_bp.put("/devices/<device_id>")
@user_required
def devices_update(device_id):
    body = _body()
    try:
        inventory.update_device(
            _uid(), device_id,
            name=body.get("name"), color=body.get("color"), icon=body.get("icon"),
            retention_days=body["retention_days"] if "retention_days" in body else ...,
            tag_ids=_string_list(body.get("tag_ids"), "tag_ids"),
        )
    except LookupError as exc:
        return _error(str(exc), 404)
    except ValueError as exc:
        return _error(str(exc))
    return jsonify({"ok": True})


@api_bp.delete("/devices/<device_id>")
@user_required
def devices_ungroup(device_id):
    try:
        inventory.ungroup_device(_uid(), device_id)
    except LookupError as exc:
        return _error(str(exc), 404)
    except ValueError as exc:
        return _error(str(exc))
    return jsonify({"ok": True})


# ── fetching ──

def _tag_ids_param(provider):
    """Validated tag_ids from the request body (None = all tags of the provider)."""
    tag_ids = _string_list(_body().get("tag_ids"), "tag_ids")
    if tag_ids is None:
        return None
    if not tag_ids:
        raise ValueError(f"no {provider.capitalize()} tags selected")
    inventory.sync_user(_uid(), g.user["username"])
    known = {t["stable_id"] for t in keystore.all_tags(keystore.get_index(g.user["username"])) if t["provider"] == provider}
    if not set(tag_ids) <= known:
        raise ValueError(f"unknown {provider.capitalize()} tag")
    return sorted(set(tag_ids))


@api_bp.post("/apple/fetch")
@user_required
def apple_fetch():
    try:
        tag_ids = _tag_ids_param("apple")
    except ValueError as exc:
        return _error(str(exc))
    if not keystore.get_index(g.user["username"])["apple"]:
        return _error("upload an Apple accessories file first")
    if not _runtime()["haystack"].get("endpoint"):
        return _error("haystack_endpoint is not set in config.toml")
    params = {} if tag_ids is None else {"tag_ids": tag_ids}
    return jsonify({"job": _scheduler().submit(_uid(), "apple_fetch", "manual", params)}), 202


@api_bp.post("/google/fetch")
@user_required
def google_fetch():
    try:
        tag_ids = _tag_ids_param("google")
    except ValueError as exc:
        return _error(str(exc))
    if not keystore.secrets_path(g.user["username"]):
        return _error("upload secrets.json first")
    params = {} if tag_ids is None else {"tag_ids": tag_ids}
    return jsonify({"job": _scheduler().submit(_uid(), "google_fetch", "manual", params)}), 202


@api_bp.post("/google/sync")
@user_required
def google_sync():
    if not keystore.secrets_path(g.user["username"]):
        return _error("upload secrets.json first")
    return jsonify({"job": _scheduler().submit(_uid(), "google_sync", "manual")}), 202


# ── jobs ──

@api_bp.get("/jobs")
@user_required
def jobs_list():
    return jsonify({"jobs": jobs.list_recent(_uid())})


@api_bp.get("/jobs/<int:job_id>")
@user_required
def jobs_get(job_id):
    job = jobs.get(_uid(), job_id)
    if job is None:
        return _error("job not found", 404)
    return jsonify({"job": job})


# ── history / status / alerts ──

@api_bp.get("/history")
@user_required
def history_get():
    """?from=<unix>&to=<unix> (to omitted = now), or the older ?days=N."""
    try:
        limit = max(1, min(100000, int(request.args.get("limit", 20000))))
        if request.args.get("from"):
            since = int(float(request.args["from"]))
            until = int(float(request.args["to"])) if request.args.get("to") else None
        else:
            days = max(0.01, min(36500.0, float(request.args.get("days", 7))))
            since, until = int(time.time() - days * 86400), None
    except ValueError:
        return _error("from, to, days and limit must be numbers")
    if until is not None and until < since:
        return _error("the end of the range is before its start")
    events = history.get_events(_uid(), since, until, limit)
    return jsonify({"events": events, "truncated": len(events) >= limit})


@api_bp.get("/status")
@user_required
def status_get():
    status = {name: state.get_status(_uid(), name) for name in ("apple", "apple_singleton", "google", "google_keys")}
    status["gap_recovery"] = apple_service.sweep_status(_runtime(), _uid())
    status["google_keys_schedule"] = google_service.key_sync_status(_runtime(), _uid())
    status["apple_schedule"] = apple_service.fetch_schedule(_runtime(), _uid())
    status["google_schedule"] = google_service.fetch_schedule(_runtime(), _uid())
    return jsonify(status)


@api_bp.get("/alerts")
@user_required
def alerts_list():
    return jsonify({"alerts": state.list_alerts(_uid())})


@api_bp.post("/alerts/<int:alert_id>/dismiss")
@user_required
def alerts_dismiss(alert_id):
    if not state.dismiss_alert(_uid(), alert_id):
        return _error("alert not found", 404)
    return jsonify({"ok": True})


@api_bp.post("/alerts/dismiss-all")
@user_required
def alerts_dismiss_all():
    return jsonify({"ok": True, "dismissed": state.dismiss_all_alerts(_uid())})
