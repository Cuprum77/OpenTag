"""Data API for regular users (the admin account has no access to any of this)."""
import logging

from flask import Blueprint, current_app, g, jsonify, request, send_file

import history
import inventory
import keystore
import state
from auth import user_required
from fetch_apple import fetch_apple_locations
from google_service import GoogleBusy, fetch_tags, sync_with_google


logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _uid():
    return g.user["id"]


def _runtime():
    return current_app.config["OPENTAG"]


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
    return jsonify({"ok": True, "saved": saved, "tags_added": len(sync["added"]), "warnings": warnings})


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

@api_bp.post("/apple/fetch")
@user_required
def apple_fetch():
    username = g.user["username"]
    body = _body()
    try:
        tag_ids = _string_list(body.get("tag_ids"), "tag_ids")
        days = max(1, min(7, int(body.get("days", 7))))
    except (TypeError, ValueError) as exc:
        return _error(str(exc))
    inventory.sync_user(_uid(), username)
    rows = keystore.apple_rows(keystore.get_index(username), set(tag_ids) if tag_ids is not None else None)
    if not rows:
        return _error("no Apple tags selected" if tag_ids is not None else "upload an Apple accessories file first")

    runtime = _runtime()

    def report_failure(fail):
        reason = fail.get("reason", "unknown")
        alert_type = "orphaned_report" if reason == "missing_private_key" else "decryption_failure"
        state.add_alert(_uid(), "apple", alert_type, reason, target="haystack_report")

    try:
        with current_app.config["APPLE_REQUEST_LIMITER"].manual_fetch(
                runtime["apple_manual_wait_timeout_seconds"]) as request_callback:
            payload = fetch_apple_locations(
                runtime["haystack"], rows, days=days, timeout=30,
                batch_size=runtime["apple_bulk_batch_size"],
                _alert_callback=report_failure, _request_callback=request_callback,
            )
    except Exception as exc:
        status = state.set_status(_uid(), "apple", False, "fetch_location", error=exc)
        state.add_alert(_uid(), "apple", "manual_fetch_error", exc, target="apple_locations")
        logger.error("Apple fetch failed for %s: %s", username, exc)
        return jsonify(status), 502

    stored = history.store_events(_uid(), history.apple_events(payload))
    status = state.set_status(_uid(), "apple", True, "fetch_location", details=payload)
    logger.info("Apple fetch for %s: %d reports, %d new events", username, payload["counts"]["decoded"], stored)
    return jsonify(dict(status, stored=stored))


@api_bp.post("/google/fetch")
@user_required
def google_fetch():
    try:
        tag_ids = _string_list(_body().get("tag_ids"), "tag_ids")
        result = fetch_tags(g.user, set(tag_ids) if tag_ids is not None else None, manual=True)
    except GoogleBusy as exc:
        return _error(str(exc), 409)
    except ValueError as exc:
        return _error(str(exc))
    if result["failed"] and not result["fetched"]:
        return jsonify(dict(result, error=result["failed"][0]["error"])), 502
    return jsonify(dict(result, ok=not result["failed"]))


@api_bp.post("/google/sync")
@user_required
def google_sync():
    try:
        return jsonify(sync_with_google(g.user, manual=True))
    except GoogleBusy as exc:
        return _error(str(exc), 409)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(state.clean_error_message(exc), 502)


# ── history / status / alerts ──

@api_bp.get("/history")
@user_required
def history_get():
    try:
        days = max(0.01, min(36500.0, float(request.args.get("days", 7))))
        limit = max(1, min(100000, int(request.args.get("limit", 20000))))
    except ValueError:
        return _error("days and limit must be numbers")
    return jsonify({"events": history.get_events(_uid(), days, limit)})


@api_bp.get("/status")
@user_required
def status_get():
    return jsonify({name: state.get_status(_uid(), name) for name in ("apple", "apple_singleton", "google", "google_keys")})


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
