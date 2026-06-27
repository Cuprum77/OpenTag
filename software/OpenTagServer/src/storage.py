import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

import redis
from werkzeug.utils import secure_filename


USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def create_redis_client(runtime):
    redis_cfg = runtime["redis"]
    if redis_cfg.get("url"):
        return redis.Redis.from_url(redis_cfg["url"], decode_responses=True)
    return redis.Redis(
        host=redis_cfg["host"],
        port=redis_cfg["port"],
        db=redis_cfg["db"],
        decode_responses=True,
    )


def _validate_username(username):
    if not USERNAME_RE.match(username):
        raise ValueError("invalid username")


def _users_root():
    return Path(__file__).resolve().parents[1] / "data" / "secrets"


def _legacy_users_root():
    return Path(__file__).resolve().parents[1] / "data" / "users"


def _ensure_mode(path, mode):
    os.chmod(path, mode)


def user_dir(username):
    _validate_username(username)
    root = _users_root()
    root.mkdir(parents=True, exist_ok=True)
    _ensure_mode(root, 0o700)

    legacy_path = _legacy_users_root() / username
    udir = root / username

    # Move existing user data once from the previous path layout.
    if not udir.exists() and legacy_path.exists():
        udir.parent.mkdir(parents=True, exist_ok=True)
        try:
            legacy_path.rename(udir)
        except FileNotFoundError:
            # Another worker may have already migrated this user directory.
            pass

    udir.mkdir(parents=True, exist_ok=True)
    _ensure_mode(udir, 0o700)
    return udir


def _json_load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _json_dump(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    _ensure_mode(path, 0o600)


def _validate_accessories(payload):
    if not isinstance(payload, list):
        raise ValueError("accessories.json must be a JSON list")

    for idx, row in enumerate(payload):
        if not isinstance(row, dict):
            raise ValueError(f"accessory row {idx} is not an object")
        if "privateKey" not in row or not isinstance(row["privateKey"], str):
            raise ValueError(f"accessory row {idx} missing privateKey")
        additional = row.get("additionalKeys", [])
        if not isinstance(additional, list):
            raise ValueError(f"accessory row {idx} additionalKeys must be an array")


def _validate_secrets(payload):
    if not isinstance(payload, dict):
        raise ValueError("secrets.json must be a JSON object")
    if "fcm_credentials" not in payload:
        raise ValueError("secrets.json missing fcm_credentials")


def save_accessories_upload(username, file_storage):
    if not file_storage or not file_storage.filename:
        raise ValueError("missing file")

    safe_name = secure_filename(file_storage.filename)
    if not safe_name.lower().endswith(".json"):
        raise ValueError("file must be .json")

    raw = file_storage.read()
    payload = json.loads(raw)
    _validate_accessories(payload)

    stamp = int(time.time())
    suffix = secrets.token_hex(4)
    filename = f"accessories_{stamp}_{suffix}.json"

    path = user_dir(username) / filename
    _json_dump(path, payload)

    return {"filename": filename, "items": len(payload)}


def save_secrets_upload(username, file_storage):
    if not file_storage or not file_storage.filename:
        raise ValueError("missing file")

    safe_name = secure_filename(file_storage.filename)
    if not safe_name.lower().endswith(".json"):
        raise ValueError("file must be .json")

    raw = file_storage.read()
    payload = json.loads(raw)
    _validate_secrets(payload)

    path = user_dir(username) / "secrets.json"
    if path.exists():
        raise ValueError("secrets.json already exists; delete it first before uploading a new one")
    _json_dump(path, payload)

    return {"filename": "secrets.json", "updated": True}


def list_user_files(username):
    udir = user_dir(username)
    files = []
    for path in sorted(udir.glob("*.json")):
        st = path.stat()
        files.append(
            {
                "filename": path.name,
                "size": st.st_size,
                "updated_unix": int(st.st_mtime),
                "category": "secrets" if path.name == "secrets.json" else "accessories",
            }
        )
    return files


def delete_user_file(username, filename):
    if not isinstance(filename, str) or not filename:
        raise ValueError("filename is required")

    # Prevent traversal and restrict deletions to known auth file shapes.
    if filename != Path(filename).name:
        raise ValueError("invalid filename")

    is_secrets = filename == "secrets.json"
    is_accessories = filename.startswith("accessories_") and filename.endswith(".json")
    if not (is_secrets or is_accessories):
        raise ValueError("unsupported file type")

    path = user_dir(username) / filename
    if not path.exists():
        raise FileNotFoundError("file not found")

    path.unlink()
    return {
        "filename": filename,
        "category": "secrets" if is_secrets else "accessories",
    }


def read_user_accessories(username):
    udir = user_dir(username)
    rows = []
    for path in sorted(udir.glob("accessories_*.json")):
        payload = _json_load(path)
        if not isinstance(payload, list):
            continue
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            record = dict(entry)
            record["source_file"] = path.name
            rows.append(record)
    return rows


def read_user_secrets(username):
    path = user_dir(username) / "secrets.json"
    if not path.exists():
        return None
    payload = _json_load(path)
    if not isinstance(payload, dict):
        return None
    return payload


def user_secrets_path(username):
    path = user_dir(username) / "secrets.json"
    return path if path.exists() else None


def extract_google_compounds(secrets_payload):
    compounds_blob = (secrets_payload or {}).get("compound_trackers_v1")
    if not compounds_blob:
        return []

    compounds_data = compounds_blob
    if isinstance(compounds_blob, str):
        compounds_data = json.loads(compounds_blob)

    compounds = []
    for compound_id, info in (compounds_data.get("compounds") or {}).items():
        subtags = info.get("subtags") or []
        compounds.append(
            {
                "compound_id": compound_id,
                "base_name": info.get("base_name", compound_id),
                "requested_key_count": int(info.get("requested_key_count", 0)),
                "window_size": int(info.get("window_size", 0)),
                "subtags": [
                    {
                        "name": tag.get("name", ""),
                        "key_count": int(tag.get("key_count", 0)),
                    }
                    for tag in subtags
                    if isinstance(tag, dict)
                ],
            }
        )
    return compounds


def _get_cached_canonic_ids(secrets_payload):
    """Read the canonic_ids_v1 cache from secrets.json.

    Returns a dict mapping canonic_id (UUID) -> name, and a set of all names.
    """
    by_id = {}
    names = set()
    cache_blob = secrets_payload.get("canonic_ids_v1")
    if not cache_blob:
        return by_id, names

    cache_data = cache_blob
    if isinstance(cache_blob, str):
        try:
            cache_data = json.loads(cache_blob)
        except json.JSONDecodeError:
            return by_id, names

    if not isinstance(cache_data, dict):
        return by_id, names

    entries = cache_data.get("entries") or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        canonic = entry.get("canonic_id")
        name = entry.get("name")
        if canonic:
            by_id[canonic] = name or "Unknown"
        if name and name != "Unknown":
            names.add(name)

    return by_id, names


def _capture_canonic_state(secrets_path):
    """Capture the full state of canonic_ids_v1 cache before a refresh.

    Returns a dict mapping canonic_id -> {"name": ..., "last_seen": ...},
    or None if no cache exists.
    """
    if not secrets_path or not secrets_path.exists():
        return None
    try:
        payload = _json_load(secrets_path)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    cache_blob = payload.get("canonic_ids_v1")
    if not cache_blob:
        return None

    cache_data = cache_blob
    if isinstance(cache_blob, str):
        try:
            cache_data = json.loads(cache_blob)
        except json.JSONDecodeError:
            return None

    if not isinstance(cache_data, dict):
        return None

    state = {}
    entries = cache_data.get("entries") or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("canonic_id")
        if cid:
            state[cid] = {
                "name": entry.get("name", "Unknown"),
                "last_seen": entry.get("last_seen"),
            }
    return state


def prune_stale_google_compounds(secrets_path, refresh_payload, canonic_state_before=None):
    """Remove compounds and targets from secrets.json that no longer exist on Google's side.

    After a refresh, the external script updates canonic_ids_v1 with the current
    device list. Entries whose last_seen timestamp was updated are alive; entries
    whose last_seen stayed the same (or are missing) are stale.

    Args:
        secrets_path: Path to secrets.json
        refresh_payload: Response from refresh_google_announcements
        canonic_state_before: dict mapping canonic_id -> {"name": ..., "last_seen": ...}
                              captured BEFORE refresh. Entries not updated during refresh
                              will have unchanged last_seen.

    Returns:
        dict with counts: {pruned_canonic_ids: int, pruned_compounds: int, pruned_targets: int}
    """
    result = {"pruned_canonic_ids": 0, "pruned_compounds": 0, "pruned_targets": 0}

    if not secrets_path or not secrets_path.exists():
        return result

    try:
        payload = _json_load(secrets_path)
    except Exception:
        return result
    if not isinstance(payload, dict):
        return result

    # Determine which canonic IDs are still alive
    alive_canonic_ids = set()
    alive_names = set()

    if canonic_state_before:
        # Read current state after refresh
        by_id_after, names_after = _get_cached_canonic_ids(payload)

        # Build last_seen map from current cache
        last_seen_after = {}
        cache_blob = payload.get("canonic_ids_v1")
        if cache_blob:
            cache_data = cache_blob
            if isinstance(cache_blob, str):
                try:
                    cache_data = json.loads(cache_blob)
                except json.JSONDecodeError:
                    cache_data = {}
            if isinstance(cache_data, dict):
                for entry in cache_data.get("entries") or []:
                    if isinstance(entry, dict):
                        cid = entry.get("canonic_id")
                        ls = entry.get("last_seen")
                        if cid and ls is not None:
                            last_seen_after[cid] = ls

        # Entries whose last_seen changed (or are new) are alive
        for cid, name in by_id_after.items():
            before_info = canonic_state_before.get(cid)
            after_ls = last_seen_after.get(cid)
            before_ls = before_info["last_seen"] if before_info else None

            if before_ls is None:
                # New entry - alive
                alive_canonic_ids.add(cid)
            elif after_ls is not None and after_ls != before_ls:
                # last_seen updated - alive
                alive_canonic_ids.add(cid)
            # else: last_seen unchanged - stale

        # Also consider entries with updated names as alive (edge case)
        for cid in alive_canonic_ids:
            name = by_id_after.get(cid)
            if name and name != "Unknown":
                alive_names.add(name)
    else:
        # No before snapshot - use all cached entries as alive (conservative)
        by_id, names = _get_cached_canonic_ids(payload)
        alive_canonic_ids = set(by_id.keys())
        alive_names = names

    if not alive_canonic_ids and not alive_names:
        return result

    pruned_canonic_ids = 0
    pruned_compounds = 0
    pruned_targets = 0

    # Prune stale canonic_ids_v1 entries
    cache_blob = payload.get("canonic_ids_v1")
    if cache_blob and canonic_state_before:
        cache_data = cache_blob
        is_string_cache = isinstance(cache_blob, str)
        if is_string_cache:
            try:
                cache_data = json.loads(cache_blob)
            except json.JSONDecodeError:
                cache_data = {}

        if isinstance(cache_data, dict):
            entries = cache_data.get("entries") or []
            alive_entries = []
            for entry in entries:
                if not isinstance(entry, dict):
                    pruned_canonic_ids += 1
                    continue
                cid = entry.get("canonic_id")
                if cid and cid in alive_canonic_ids:
                    alive_entries.append(entry)
                else:
                    pruned_canonic_ids += 1

            cache_data["entries"] = alive_entries
            if is_string_cache:
                payload["canonic_ids_v1"] = json.dumps(cache_data)
            elif not alive_entries:
                payload.pop("canonic_ids_v1", None)

    # Prune stale compounds
    compounds_blob = payload.get("compound_trackers_v1")
    if compounds_blob:
        compounds_data = compounds_blob
        was_string = isinstance(compounds_blob, str)
        if was_string:
            try:
                compounds_data = json.loads(compounds_blob)
            except json.JSONDecodeError:
                compounds_data = {}

        if isinstance(compounds_data, dict) and "compounds" in compounds_data:
            compounds = compounds_data["compounds"]
            if isinstance(compounds, dict):
                stale_compound_ids = []
                for compound_id, info in compounds.items():
                    if not isinstance(info, dict):
                        stale_compound_ids.append(compound_id)
                        continue

                    subtags = info.get("subtags") or []

                    # Check if any subtag still references an alive canonic ID
                    # Match by canonic_id first (more reliable), then by name
                    is_stale = True
                    for subtag in subtags:
                        if isinstance(subtag, dict):
                            subtag_cid = subtag.get("canonic_id", "")
                            if subtag_cid and subtag_cid in alive_canonic_ids:
                                is_stale = False
                                break
                            subtag_name = subtag.get("name", "")
                            if subtag_name and subtag_name in alive_names:
                                is_stale = False
                                break

                    if is_stale:
                        stale_compound_ids.append(compound_id)

                for cid in stale_compound_ids:
                    del compounds[cid]
                    pruned_compounds += 1

                if was_string:
                    payload["compound_trackers_v1"] = json.dumps(compounds_data)
                elif not compounds:
                    payload.pop("compound_trackers_v1", None)

    # Prune stale non-compound targets
    targets_blob = payload.get("targets")
    if isinstance(targets_blob, list):
        stale_indices = []
        for idx, target in enumerate(targets_blob):
            if not isinstance(target, dict):
                stale_indices.append(idx)
                continue
            canonic = target.get("canonic_id") or target.get("canonicId") or ""
            label = target.get("label") or ""
            is_stale = True
            if canonic and canonic in alive_canonic_ids:
                is_stale = False
            elif label and label in alive_names:
                is_stale = False
            if is_stale:
                stale_indices.append(idx)

        for idx in reversed(stale_indices):
            del targets_blob[idx]
            pruned_targets += 1

    if pruned_canonic_ids > 0 or pruned_compounds > 0 or pruned_targets > 0:
        _json_dump(secrets_path, payload)

    result["pruned_canonic_ids"] = pruned_canonic_ids
    result["pruned_compounds"] = pruned_compounds
    result["pruned_targets"] = pruned_targets
    return result




def merge_apple_keys(accessories_rows):
    seen = {}
    for row in accessories_rows:
        row_name = row.get("name") or str(row.get("id") or "unknown")
        source_file = row.get("source_file", "")
        keys = []

        private_key = row.get("privateKey")
        if isinstance(private_key, str) and private_key:
            keys.append(private_key)

        additional = row.get("additionalKeys") or []
        if isinstance(additional, list):
            for k in additional:
                if isinstance(k, str) and k:
                    keys.append(k)

        for key_value in keys:
            digest = hashlib.sha256(key_value.encode("utf-8")).hexdigest()
            if digest not in seen:
                seen[digest] = {
                    "digest": digest,
                    "sources": [{"tag": row_name, "file": source_file}],
                }
            else:
                seen[digest]["sources"].append({"tag": row_name, "file": source_file})

    merged = list(seen.values())
    merged.sort(key=lambda x: x["digest"])
    return {
        "unique_key_count": len(merged),
        "keys": merged,
    }


def set_fetch_status(redis_client, username, provider, payload):
    key = f"user:{username}:status:{provider}"
    redis_client.set(key, json.dumps(payload))


def get_fetch_status(redis_client, username, provider):
    key = f"user:{username}:status:{provider}"
    raw = redis_client.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def append_history_events(redis_client, username, events, max_items=5000):
    if not events:
        return
    key = f"user:{username}:history:events"
    pipe = redis_client.pipeline()
    for event in events:
        pipe.lpush(key, json.dumps(event))
    pipe.ltrim(key, 0, max_items - 1)
    pipe.execute()


def get_history_events(redis_client, username, limit=500):
    key = f"user:{username}:history:events"
    rows = redis_client.lrange(key, 0, max(0, int(limit) - 1))
    parsed = []
    for row in rows:
        try:
            parsed.append(json.loads(row))
        except Exception:
            continue
    parsed.sort(key=lambda x: x.get("timestamp_unix", 0), reverse=True)
    return parsed


def clear_fetch_status(redis_client, username, provider):
    redis_client.delete(f"user:{username}:status:{provider}")


def purge_history_events(redis_client, username, provider=None, source_file=None):
    key = f"user:{username}:history:events"
    raw_rows = redis_client.lrange(key, 0, -1)
    if not raw_rows:
        return 0

    kept = []
    removed = 0
    for raw in raw_rows:
        try:
            row = json.loads(raw)
        except Exception:
            continue

        drop = False
        if provider and row.get("provider") == provider:
            if source_file is None:
                drop = True
            else:
                file_hint = row.get("source_file") or ""
                drop = file_hint == source_file or file_hint == ""

        if drop:
            removed += 1
        else:
            kept.append(raw)

    pipe = redis_client.pipeline()
    pipe.delete(key)
    if kept:
        pipe.rpush(key, *kept)
    pipe.execute()
    return removed


def purge_old_history_events(redis_client, username, max_age_seconds):
    """Remove history events older than max_age_seconds. Returns count of removed events."""
    if max_age_seconds <= 0:
        return 0

    key = f"user:{username}:history:events"
    raw_rows = redis_client.lrange(key, 0, -1)
    if not raw_rows:
        return 0

    cutoff = time.time() - max_age_seconds
    kept = []
    removed = 0

    for raw in raw_rows:
        try:
            row = json.loads(raw)
        except Exception:
            continue

        ts = row.get("timestamp_unix", 0)
        if ts and ts < cutoff:
            removed += 1
        else:
            kept.append(raw)

    if removed == 0:
        return 0

    pipe = redis_client.pipeline()
    pipe.delete(key)
    if kept:
        pipe.rpush(key, *kept)
    pipe.execute()
    return removed


import re

def clean_error_message(error_str):
    """Extract a clean, human-readable error message from a Python exception string.

    Strips traceback prefixes and extracts just the final exception type and message.
    Returns the cleaned message, or 'Unknown error' if nothing meaningful can be extracted.
    """
    if not error_str:
        return "Unknown error"

    # If the string contains "Traceback", extract only the last line (the actual exception)
    if "Traceback" in error_str:
        # Find the last line that contains the actual exception
        lines = error_str.strip().split("\n")
        for line in reversed(lines):
            line = line.strip()
            # Skip traceback header, "During handling", "The above exception", "While executing" lines
            if line.startswith("Traceback (most recent call last):"):
                continue
            if line.startswith("During handling of the above exception:"):
                continue
            if line.startswith("The above exception was the direct cause of the following exception:"):
                continue
            # Match patterns like "TypeError: something" or "httpcore.ReadTimeout: ..."
            if ":" in line and not line.startswith("  "):
                # Take the part after the last colon-space that looks like an exception
                parts = line.rsplit(":", 1)
                if len(parts) == 2:
                    return parts[1].strip() if parts[1].strip() else line
                return line
            # If it's just a message without exception type, use it
            if line and not line.startswith("  "):
                return line
        return "Unknown error"

    # No traceback — just clean up leading/trailing whitespace and return
    cleaned = error_str.strip()
    return cleaned if cleaned else "Unknown error"


def purge_alerts(redis_client, username):
    """Delete all alerts for a user. Returns the number of alerts removed."""
    key = f"user:{username}:alerts"
    count = redis_client.llen(key) if redis_client else 0
    if count > 0:
        redis_client.delete(key)
    return int(count)


def append_alert(redis_client, username, alert, max_items=500):
    """Append an alert event to the user's alert log, capped at max_items."""
    key = f"user:{username}:alerts"
    alert["created_unix"] = int(time.time())
    pipe = redis_client.pipeline()
    pipe.lpush(key, json.dumps(alert))
    pipe.ltrim(key, 0, max_items - 1)
    pipe.execute()


def get_alerts(redis_client, username, limit=100):
    """Get recent alerts sorted by creation time descending."""
    key = f"user:{username}:alerts"
    rows = redis_client.lrange(key, 0, max(0, limit - 1))
    parsed = []
    for row in rows:
        try:
            parsed.append(json.loads(row))
        except Exception:
            continue
    parsed.sort(key=lambda x: x.get("created_unix", 0), reverse=True)
    return parsed


def purge_old_alerts(redis_client, username, max_age_seconds):
    """Remove alerts older than max_age_seconds. Returns count of removed."""
    if max_age_seconds <= 0:
        return 0

    key = f"user:{username}:alerts"
    raw_rows = redis_client.lrange(key, 0, -1)
    if not raw_rows:
        return 0

    cutoff = time.time() - max_age_seconds
    kept = []
    removed = 0

    for raw in raw_rows:
        try:
            row = json.loads(raw)
        except Exception:
            continue

        ts = row.get("created_unix", 0)
        if ts and ts < cutoff:
            removed += 1
        else:
            kept.append(raw)

    if removed == 0:
        return 0

    pipe = redis_client.pipeline()
    pipe.delete(key)
    if kept:
        pipe.rpush(key, *kept)
    pipe.execute()
    return removed
