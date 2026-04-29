import time
import logging

from threading import Thread

from flask import Blueprint, current_app, g, jsonify, request

from auth import login_required
from fetch_apple import fetch_apple_locations
from fetch_google import fetch_google_locations, list_google_targets, refresh_google_announcements
from storage import (
    delete_user_file,
    extract_google_compounds,
    append_history_events,
    clear_fetch_status,
    purge_history_events,
    get_fetch_status,
    get_history_events,
    list_user_files,
    merge_apple_keys,
    read_user_accessories,
    read_user_secrets,
    save_accessories_upload,
    save_secrets_upload,
    set_fetch_status,
    user_secrets_path,
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
    path = user_secrets_path(username)
    if not path:
        return jsonify({"error": "secrets.json is required"}), 400

    try:
        logger.info("Listing Google targets for user %s", username)
        payload = list_google_targets(path)
        return jsonify(payload)
    except Exception as exc:
        logger.error("Failed to list Google targets for user %s: %s", username, exc)
        return jsonify({"error": str(exc)}), 400


@api_bp.post("/google/refresh-keys")
@login_required
def google_refresh_keys():
    username = g.user["username"]
    path = user_secrets_path(username)
    if not path:
        return jsonify({"error": "secrets.json is required"}), 400

    body = request.get_json(silent=True) or {}
    force_upload = bool(body.get("force_upload", False))
    redis_client = current_app.config["REDIS"]

    try:
        logger.info("Refreshing Google keys for user %s (force=%s)", username, force_upload)
        payload = refresh_google_announcements(path, force_upload=force_upload)
        status_payload = {
            "provider": "google",
            "ok": True,
            "kind": "refresh_keys",
            "timestamp_unix": int(time.time()),
            "details": payload,
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

    try:
        logger.info("Fetching Apple locations for user %s, days=%d", username, days)
        payload = fetch_apple_locations(runtime.get("haystack", {}), accessories, days=days, timeout=timeout)
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
        logger.error("Apple location fetch failed for user %s: %s", username, exc)
        return jsonify(status_payload), 502


@api_bp.get("/history/combined")
@login_required
def history_combined():
    username = g.user["username"]
    redis_client = current_app.config["REDIS"]
    limit = int(request.args.get("limit", 500))
    events = get_history_events(redis_client, username, limit=limit)
    return jsonify({"events": events})
