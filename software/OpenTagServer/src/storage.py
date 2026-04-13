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
