import time
import logging
import uuid

from threading import Thread

from flask import Blueprint, current_app, g, jsonify, request, send_file

from auth import login_required
from fetch_apple import fetch_apple_locations
from fetch_google import fetch_google_locations, list_google_targets, refresh_google_announcements
from storage import (
    delete_user_file,
    extract_google_compounds,
    append_history_events,
    append_alert,
    clean_error_message,
    clear_fetch_status,
    purge_alerts,
    purge_history_events,
    purge_old_alerts,
    get_fetch_status,
    get_history_events,
    get_alerts,
    list_user_files,
    merge_apple_keys,
    prune_stale_google_compounds,
    _capture_canonic_state,
    read_user_accessories,
    read_user_secrets,
    save_accessories_upload,
    save_secrets_upload,
    set_fetch_status,
    user_dir,
    user_secrets_path,
    get_devices,
    add_device,
    update_device,
    delete_device,
)

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _run_apple_merge_status(redis_client, username):
    try:
        accessories = read_user_accessories(username)
        if not accessories:
            payload = {
                "provider": "apple_merge",
                "ok": True,
                "kind": "merge_keys",
                "timestamp_unix": int(time.time()),
                "details": {"unique_key_count": 0, "warnings": ["no accessories uploaded"]},
            }
            set_fetch_status(redis_client, username, "apple_merge", payload)
            return

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


def _spawn_merge_status_job(redis_client, username):
    Thread(target=_run_apple_merge_status, args=(redis_client, username), daemon=True).start()


def _extract_google_events(payload):
    events = []
    details = payload.get("details") if isinstance(payload, dict) else None
    if not isinstance(details, dict):
        return events
    targets = details.get("targets")
    if not isinstance(targets, list):
        return events

    for target in targets:
        if not isinstance(target, dict):
            continue
        label = target.get("label") or target.get("resolved_device_name") or "google-tag"
        result = target.get("result")
        if not isinstance(result, dict):
            result = {}

        locations = result.get("locations")
        if not isinstance(locations, list):
            locations = []

        for location in locations:
            if not isinstance(location, dict) or location.get("type") != "geo":
                continue
            events.append(
                {
                    "provider": "google",
                    "tag": label,
                    "latitude": location.get("latitude"),
                    "longitude": location.get("longitude"),
                    "timestamp_unix": int(location.get("time_unix", 0) or 0),
                    "status": location.get("status"),
                    "source": "google_compound",
                }
            )
    return events


def _extract_apple_events(payload):
    events = []
    details = payload.get("details") if isinstance(payload, dict) else None
    if not isinstance(details, dict):
        return events
    reports = details.get("reports")
    if not isinstance(reports, list):
        reports = []

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

        events.append(
            {
                "provider": "apple",
                "tag": (report.get("source") or {}).get("tag", "apple-tag"),
                "source_file": (report.get("source") or {}).get("file", ""),
                "latitude": report.get("latitude"),
                "longitude": report.get("longitude"),
                "timestamp_unix": ts_unix,
                "status": "APPLE",
                "source": "apple_haystack",
            }
        )
    return events


@api_bp.get("/keyfiles")
@login_required
def keyfiles_list():
    files = list_user_files(g.user["username"])
    logger.info("Listing %d keyfiles for user %s", len(files), g.user["username"])
    return jsonify({"files": files})


@api_bp.post("/upload/accessories")
@login_required
def upload_accessories():
    file_obj = request.files.get("file")
    if not file_obj:
        return jsonify({"error": "multipart field 'file' is required"}), 400

    try:
        result = save_accessories_upload(g.user["username"], file_obj)
    except Exception as exc:
        logger.error("Failed to upload accessories for user %s: %s", g.user["username"], exc)
        redis_client = current_app.config["REDIS"]
        append_alert(redis_client, g.user["username"], {
            "provider": "apple",
            "error": clean_error_message(str(exc)),
            "type": "file_upload",
            "target": "accessories",
        })
        return jsonify({"error": str(exc)}), 400

    logger.info("Uploaded accessories file %s for user %s (%d items)", result.get("filename", ""), g.user["username"], result.get("items", 0))
    _spawn_merge_status_job(current_app.config["REDIS"], g.user["username"])

    return jsonify({"ok": True, "saved": result})


@api_bp.delete("/keyfiles/<path:filename>")
@login_required
def keyfiles_delete(filename):
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    try:
        deleted = delete_user_file(username, filename)
    except FileNotFoundError:
        logger.warning("Delete failed: file %s not found for user %s", filename, username)
        return jsonify({"error": "file not found"}), 404
    except Exception as exc:
        logger.error("Failed to delete file %s for user %s: %s", filename, username, exc)
        append_alert(redis_client, username, {
            "provider": "system",
            "error": clean_error_message(str(exc)),
            "type": "file_delete",
            "target": filename,
        })
        return jsonify({"error": str(exc)}), 400

    removed_events = 0
    if deleted.get("category") == "accessories":
        removed_events = purge_history_events(redis_client, username, provider="apple", source_file=filename)
        clear_fetch_status(redis_client, username, "apple")
        _spawn_merge_status_job(redis_client, username)
        logger.info("Deleted accessories %s for user %s, purged %d apple history events", filename, username, removed_events)
    elif deleted.get("category") == "secrets":
        removed_events = purge_history_events(redis_client, username, provider="google")
        clear_fetch_status(redis_client, username, "google")
        logger.info("Deleted secrets %s for user %s, purged %d google history events", filename, username, removed_events)

    return jsonify({"ok": True, "deleted": deleted, "cache": {"removed_events": removed_events}})


@api_bp.post("/upload/secrets")
@login_required
def upload_secrets():
    file_obj = request.files.get("file")
    if not file_obj:
        return jsonify({"error": "multipart field 'file' is required"}), 400

    try:
        result = save_secrets_upload(g.user["username"], file_obj)
    except Exception as exc:
        logger.error("Failed to upload secrets for user %s: %s", g.user["username"], exc)
        redis_client = current_app.config["REDIS"]
        append_alert(redis_client, g.user["username"], {
            "provider": "google",
            "error": clean_error_message(str(exc)),
            "type": "file_upload",
            "target": "secrets",
        })
        return jsonify({"error": str(exc)}), 400

    logger.info("Uploaded secrets.json for user %s", g.user["username"])
    return jsonify({"ok": True, "saved": result})


@api_bp.get("/tags/raw")
@login_required
def tags_raw():
    username = g.user["username"]
    accessories = read_user_accessories(username)
    secrets_payload = read_user_secrets(username)
    compounds = extract_google_compounds(secrets_payload)

    apple_tags = []
    for row in accessories:
        additional = row.get("additionalKeys") or []
        apple_tags.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "source_file": row.get("source_file"),
                "key_count": 1 + (len(additional) if isinstance(additional, list) else 0),
            }
        )

    return jsonify(
        {
            "apple_tags": apple_tags,
            "google_compounds": compounds,
        }
    )


@api_bp.get("/tags/merged/apple")
@login_required
def tags_merged_apple():
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    status = get_fetch_status(redis_client, username, "apple_merge")
    if not status:
        _run_apple_merge_status(redis_client, username)
        status = get_fetch_status(redis_client, username, "apple_merge")
    return jsonify(status or {"provider": "apple_merge", "ok": True, "details": {"unique_key_count": 0}})


@api_bp.get("/status/fetch")
@login_required
def fetch_status():
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    return jsonify(
        {
            "google": get_fetch_status(redis_client, username, "google"),
            "apple": get_fetch_status(redis_client, username, "apple"),
            "apple_merge": get_fetch_status(redis_client, username, "apple_merge"),
        }
    )


@api_bp.get("/status/errors")
@login_required
def status_errors():
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    rows = []
    for provider in ("apple", "google", "apple_merge"):
        row = get_fetch_status(redis_client, username, provider)
        if isinstance(row, dict) and row.get("ok") is False:
            rows.append(row)
    rows.sort(key=lambda x: x.get("timestamp_unix", 0), reverse=True)
    return jsonify({"errors": rows})


@api_bp.get("/google/targets")
@login_required
def google_targets():
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    path = user_secrets_path(username)
    if not path:
        return jsonify({"error": "secrets.json is required"}), 400

    try:
        logger.info("Listing Google targets for user %s", username)
        payload = list_google_targets(path)
        return jsonify(payload)
    except Exception as exc:
        logger.error("Failed to list Google targets for user %s: %s", username, exc)
        append_alert(redis_client, username, {
            "provider": "google",
            "error": clean_error_message(str(exc)),
            "type": "list_targets",
            "target": "google_targets",
        })
        return jsonify({"error": str(exc)}), 400


@api_bp.post("/google/refresh-keys")
@login_required
def google_refresh_keys():
    username = g.user["username"]
    path = user_secrets_path(username)
    if not path:
        return jsonify({"error": "secrets.json is required"}), 400

    body = request.get_json(silent=True) or {}
    force_upload = True  # Always force on manual refresh; 24h TTL only applies to auto-refresh
    redis_client = current_app.config["REDIS"]

    try:
        logger.info("Refreshing Google keys for user %s (force=%s)", username, force_upload)

        # Capture canonic_ids state BEFORE refresh
        # This lets us detect which entries were NOT updated (i.e., stale)
        canonic_state_before = _capture_canonic_state(path)
        logger.info("Captured %d canonic_id entries before refresh for user %s", len(canonic_state_before or {}), username)

        payload = refresh_google_announcements(path, force_upload=force_upload)
        logger.info("Refresh payload for user %s: skipped=%s, keys=%s", username, payload.get("skipped", False) if isinstance(payload, dict) else "N/A", list(payload.keys()) if isinstance(payload, dict) else type(payload))

        # Prune stale compounds, targets, and canonic_ids no longer reported by Google
        # Only prune if the refresh actually happened (not skipped due to 24h TTL)
        prune_result = {"pruned_canonic_ids": 0, "pruned_compounds": 0, "pruned_targets": 0}
        if isinstance(payload, dict) and not payload.get("skipped", False):
            canonic_state_after = _capture_canonic_state(path)
            logger.info("Captured %d canonic_id entries after refresh for user %s", len(canonic_state_after or {}), username)
            # Log which entries changed
            if canonic_state_before and canonic_state_after:
                changed = {k: v for k, v in canonic_state_after.items() if canonic_state_before.get(k) != v}
                unchanged = {k for k in canonic_state_before if k in canonic_state_after and canonic_state_before[k] == canonic_state_after[k]}
                new_entries = set(canonic_state_after.keys()) - set(canonic_state_before.keys())
                logger.info("Refresh: %d changed, %d unchanged, %d new entries for user %s", len(changed), len(unchanged), len(new_entries), username)
            prune_result = prune_stale_google_compounds(path, payload, canonic_state_before=canonic_state_before)
            logger.info("Pruned %d canonic_ids, %d compounds, and %d targets for user %s", prune_result.get("pruned_canonic_ids", 0), prune_result.get("pruned_compounds", 0), prune_result.get("pruned_targets", 0), username)

        status_payload = {
            "provider": "google",
            "ok": True,
            "kind": "refresh_keys",
            "timestamp_unix": int(time.time()),
            "details": payload,
            "pruned": prune_result,
        }
        set_fetch_status(redis_client, username, "google", status_payload)
        logger.info("Google key refresh successful for user %s", username)
        return jsonify(status_payload)
    except Exception as exc:
        previous = get_fetch_status(redis_client, username, "google")
        status_payload = {
            "provider": "google",
            "ok": False,
            "kind": "refresh_keys",
            "timestamp_unix": int(time.time()),
            "error": str(exc),
            "last_data": previous.get("details") if isinstance(previous, dict) else None,
        }
        set_fetch_status(redis_client, username, "google", status_payload)
        append_alert(redis_client, username, {
            "provider": "google",
            "error": clean_error_message(str(exc)),
            "type": "manual_key_refresh",
            "target": "refresh_keys",
        })
        logger.error("Google key refresh failed for user %s: %s", username, exc)
        return jsonify(status_payload), 502


@api_bp.post("/google/fetch")
@login_required
def google_fetch():
    username = g.user["username"]
    path = user_secrets_path(username)
    if not path:
        return jsonify({"error": "secrets.json is required"}), 400

    body = request.get_json(silent=True) or {}
    canonic_id = body.get("canonic_id")
    compound_name = body.get("compound_name")
    timeout = int(body.get("timeout", 45))

    if bool(canonic_id) == bool(compound_name):
        return jsonify({"error": "provide either canonic_id or compound_name"}), 400

    redis_client = current_app.config["REDIS"]
    try:
        target = canonic_id or compound_name
        logger.info("Fetching Google location for user %s, target=%s", username, target)
        payload = fetch_google_locations(
            path,
            canonic_id=canonic_id,
            compound_name=compound_name,
            timeout=timeout,
        )
        status_payload = {
            "provider": "google",
            "ok": True,
            "kind": "fetch_location",
            "timestamp_unix": int(time.time()),
            "target": target,
            "details": payload,
        }
        events = _extract_google_events(status_payload)
        append_history_events(redis_client, username, events)
        logger.info("Google location fetch successful for user %s, target=%s, stored %d events", username, target, len(events))
        return jsonify(status_payload)
    except Exception as exc:
        previous = get_fetch_status(redis_client, username, "google")
        status_payload = {
            "provider": "google",
            "ok": False,
            "kind": "fetch_location",
            "timestamp_unix": int(time.time()),
            "target": canonic_id or compound_name,
            "error": str(exc),
            "last_data": previous.get("details") if isinstance(previous, dict) else None,
        }
        set_fetch_status(redis_client, username, "google", status_payload)
        append_alert(redis_client, username, {
            "provider": "google",
            "error": clean_error_message(str(exc)),
            "type": "manual_location_fetch",
            "target": canonic_id or compound_name,
        })
        logger.error("Google location fetch failed for user %s, target=%s: %s", username, canonic_id or compound_name, exc)
        return jsonify(status_payload), 502


@api_bp.post("/apple/fetch")
@login_required
def apple_fetch():
    username = g.user["username"]
    accessories = read_user_accessories(username)
    if not accessories:
        return jsonify({"error": "upload at least one accessories.json file first"}), 400

    body = request.get_json(silent=True) or {}
    days = int(body.get("days", 7))
    timeout = int(body.get("timeout", 30))

    runtime = current_app.config["OPENTAG"]
    redis_client = current_app.config["REDIS"]

    # Build alert callback for per-report decryption/orphaned failures
    def _apple_report_alert_callback(fail):
        reason = fail.get("reason", "unknown")
        is_orphaned = reason == "missing_private_key"
        alert_type = "orphaned_report" if is_orphaned else "decryption_failure"
        append_alert(redis_client, username, {
            "provider": "apple",
            "error": reason,
            "type": alert_type,
            "target": "apple_haystack_report",
        })

    try:
        logger.info("Fetching Apple locations for user %s, days=%d", username, days)
        payload = fetch_apple_locations(runtime.get("haystack", {}), accessories, days=days, timeout=timeout, _alert_callback=_apple_report_alert_callback)
        status_payload = {
            "provider": "apple",
            "ok": True,
            "kind": "fetch_location",
            "timestamp_unix": int(time.time()),
            "details": payload,
        }
        set_fetch_status(redis_client, username, "apple", status_payload)
        events = _extract_apple_events(status_payload)
        append_history_events(redis_client, username, events)
        logger.info("Apple location fetch successful for user %s, stored %d events", username, len(events))
        return jsonify(status_payload)
    except Exception as exc:
        previous = get_fetch_status(redis_client, username, "apple")
        status_payload = {
            "provider": "apple",
            "ok": False,
            "kind": "fetch_location",
            "timestamp_unix": int(time.time()),
            "error": str(exc),
            "last_data": previous.get("details") if isinstance(previous, dict) else None,
        }
        set_fetch_status(redis_client, username, "apple", status_payload)
        append_alert(redis_client, username, {
            "provider": "apple",
            "error": clean_error_message(str(exc)),
            "type": "manual_location_fetch",
            "target": "apple_locations",
        })
        logger.error("Apple location fetch failed for user %s: %s", username, exc)
        return jsonify(status_payload), 502


@api_bp.get("/history/combined")
@login_required
def history_combined():
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    days = float(request.args.get("days", 7))
    limit = int(request.args.get("limit", 500))
    cutoff = int(time.time()) - int(days * 86400)
    events = get_history_events(redis_client, username, limit=limit)
    # Filter by time window
    events = [e for e in events if e.get("timestamp_unix", 0) >= cutoff]
    return jsonify({"events": events})


@api_bp.get("/status/alerts")
@login_required
def status_alerts():
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    limit = int(request.args.get("limit", 100))
    alerts = get_alerts(redis_client, username, limit=limit)
    return jsonify({"alerts": alerts})


@api_bp.post("/status/alerts/clear")
@login_required
def clear_alerts():
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    removed = purge_alerts(redis_client, username)
    return jsonify({"ok": True, "removed": removed})


@api_bp.get("/keyfiles/<path:filename>/download")
@login_required
def keyfiles_download(filename):
    from pathlib import Path
    username = g.user["username"]
    # Prevent directory traversal
    if filename != Path(filename).name:
        return jsonify({"error": "invalid filename"}), 400
    # Restrict to known file patterns
    is_secrets = filename == "secrets.json"
    is_accessories = filename.startswith("accessories_") and filename.endswith(".json")
    if not (is_secrets or is_accessories):
        return jsonify({"error": "unsupported file type"}), 400
    path = user_dir(username) / filename
    if not path.exists():
        return jsonify({"error": "file not found"}), 404
    return send_file(str(path), as_attachment=True, download_name=filename)


# ── Devices ──

@api_bp.get("/devices")
@login_required
def devices_list():
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    devices = get_devices(redis_client, username)
    return jsonify({"devices": devices})


@api_bp.post("/devices")
@login_required
def devices_create():
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    color = body.get("color") or "#888888"
    tags = body.get("tags") or []

    if not name:
        return jsonify({"error": "device name is required"}), 400
    if not isinstance(tags, list) or len(tags) < 2:
        return jsonify({"error": "at least 2 tags must be selected"}), 400

    # Validate tag entries
    validated_tags = []
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        provider = tag.get("provider")
        tag_id = tag.get("tag_id")
        tag_name = tag.get("tag_name", "")
        if provider in ("apple", "google") and tag_id:
            validated_tags.append({
                "provider": provider,
                "tag_id": tag_id,
                "tag_name": tag_name,
            })

    if len(validated_tags) < 2:
        return jsonify({"error": "at least 2 valid tags must be selected"}), 400

    device = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "color": color,
        "tags": validated_tags,
        "created_unix": int(time.time()),
        "updated_unix": int(time.time()),
    }
    add_device(redis_client, username, device)
    logger.info("Created device '%s' (%d tags) for user %s", name, len(validated_tags), username)
    return jsonify({"ok": True, "device": device}), 201


@api_bp.put("/devices/<device_id>")
@login_required
def devices_update(device_id):
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    body = request.get_json(silent=True) or {}

    # Only allow updating name, color, or tags
    allowed = {}
    if "name" in body:
        allowed["name"] = body["name"].strip() if isinstance(body["name"], str) else body["name"]
    if "color" in body:
        allowed["color"] = body["color"]
    if "tags" in body:
        allowed["tags"] = body["tags"]

    if not allowed:
        return jsonify({"error": "no valid fields to update"}), 400

    update_device(redis_client, username, device_id, allowed)
    logger.info("Updated device '%s' for user %s", device_id, username)
    devices = get_devices(redis_client, username)
    device = next((d for d in devices if d.get("id") == device_id), None)
    if not device:
        return jsonify({"error": "device not found"}), 404
    return jsonify({"ok": True, "device": device})


@api_bp.delete("/devices/<device_id>")
@login_required
def devices_delete(device_id):
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    deleted = delete_device(redis_client, username, device_id)
    if not deleted:
        return jsonify({"error": "device not found"}), 404
    logger.info("Deleted device '%s' for user %s", device_id, username)
    return jsonify({"ok": True, "deleted": device_id})
