"""Key material lives in files under data/secrets/<user>/ (0700 dir, 0600 files).

Files are never copied into the database. They are parsed into an in-memory index at
startup and whenever a file changes (detected by name/mtime/size), which provides:

* stable tag ids derived from the key material itself, so re-uploading the same keys
  maps back onto the same tags (and their history);
* merged / de-duplicated Apple keys and per-file validation errors;
* the Google tag list (compound trackers and plain trackers) read from secrets.json.
"""
import binascii
import hashlib
import json
import logging
import os
import re
import secrets as pysecrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

from werkzeug.utils import secure_filename

from fetch_apple import _get_hashed_advertisement_key


logger = logging.getLogger(__name__)

SECRETS_FILENAME = "secrets.json"
ACCESSORIES_RE = re.compile(r"^accessories_[0-9]+_[0-9a-f]+\.json$")
COMPOUND_SUBTAG_RE = re.compile(r"^\[OTB-C\] (.+)_([0-9]+)$")

_root = None
_cache = {}
_cache_lock = threading.Lock()


def init(secrets_dir):
    global _root
    _root = Path(secrets_dir)
    _root.mkdir(parents=True, exist_ok=True)
    os.chmod(_root, 0o700)
    with _cache_lock:
        _cache.clear()


def _validate_username(username):
    if not isinstance(username, str) or not re.match(r"^[A-Za-z0-9_-]{1,64}$", username):
        raise ValueError("invalid username")


def user_dir(username, create=True):
    _validate_username(username)
    path = _root / username
    if create:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    return path


def _write_json_atomic(path, payload):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".upload-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _read_upload_json(file_storage):
    if not file_storage or not file_storage.filename:
        raise ValueError("missing file")
    if not secure_filename(file_storage.filename).lower().endswith(".json"):
        raise ValueError("file must be .json")
    try:
        return json.loads(file_storage.read())
    except ValueError as exc:
        raise ValueError(f"file is not valid JSON: {exc}") from None


# ── Apple accessories ──

def _accessory_keys(row):
    keys = []
    if isinstance(row.get("privateKey"), str) and row["privateKey"].strip():
        keys.append(row["privateKey"].strip())
    for key in row.get("additionalKeys") or []:
        if isinstance(key, str) and key.strip():
            keys.append(key.strip())
    return keys


def _validate_accessories(payload):
    if not isinstance(payload, list):
        raise ValueError("accessories file must be a JSON list")
    if not payload:
        raise ValueError("accessories file is empty")
    for idx, row in enumerate(payload):
        if not isinstance(row, dict):
            raise ValueError(f"accessory #{idx} is not an object")
        if not isinstance(row.get("privateKey"), str) or not row["privateKey"].strip():
            raise ValueError(f"accessory #{idx} is missing privateKey")
        if not isinstance(row.get("additionalKeys", []), list):
            raise ValueError(f"accessory #{idx}: additionalKeys must be a list")
        for key in _accessory_keys(row):
            try:
                _get_hashed_advertisement_key(key)
            except (binascii.Error, ValueError, TypeError) as exc:
                raise ValueError(f"accessory #{idx} ({row.get('name') or 'unnamed'}) has an invalid key: {exc}") from None


def _content_digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save_accessories_upload(username, file_storage):
    payload = _read_upload_json(file_storage)
    _validate_accessories(payload)
    udir = user_dir(username)
    digest = _content_digest(payload)
    for path in sorted(udir.glob("accessories_*.json")):
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _content_digest(existing) == digest:
            raise ValueError(f"this file is already uploaded as {path.name}")
    filename = f"accessories_{int(time.time())}_{pysecrets.token_hex(4)}.json"
    _write_json_atomic(udir / filename, payload)
    invalidate(username)
    return {"filename": filename, "items": len(payload)}


# ── Google secrets ──

def _validate_secrets(payload):
    if not isinstance(payload, dict):
        raise ValueError("secrets.json must be a JSON object")
    if "fcm_credentials" not in payload:
        raise ValueError("secrets.json is missing fcm_credentials")


def save_secrets_upload(username, file_storage):
    """Upload or replace secrets.json. Replacing keeps tags whose ids still match."""
    payload = _read_upload_json(file_storage)
    _validate_secrets(payload)
    path = user_dir(username) / SECRETS_FILENAME
    replaced = path.exists()
    _write_json_atomic(path, payload)
    invalidate(username)
    return {"filename": SECRETS_FILENAME, "replaced": replaced}


def secrets_path(username):
    path = user_dir(username) / SECRETS_FILENAME
    return path if path.exists() else None


def read_secrets(username):
    path = secrets_path(username)
    if not path:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _cached_json(payload, key, default):
    value = payload.get(key)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return default
    return value if value is not None else default


def _parse_google(payload):
    """Mirror OpenTagBridge's listing: compounds (with subtags) plus single trackers."""
    compounds = (_cached_json(payload, "compound_trackers_v1", {}) or {}).get("compounds") or {}
    entries = (_cached_json(payload, "canonic_ids_v1", {}) or {}).get("entries") or []
    rows = [
        {"canonic_id": e["canonic_id"], "name": str(e.get("name") or "Unknown")}
        for e in entries if isinstance(e, dict) and isinstance(e.get("canonic_id"), str) and e["canonic_id"]
    ]
    by_name = {}
    for row in rows:
        by_name.setdefault(row["name"], []).append(row["canonic_id"])

    tags = {}
    used = set()
    for compound_id, info in sorted(compounds.items()):
        if not isinstance(info, dict) or not isinstance(info.get("base_name"), str):
            continue
        subtags = []
        for sub in info.get("subtags") or []:
            if not isinstance(sub, dict) or not isinstance(sub.get("name"), str):
                continue
            ids = by_name.get(sub["name"], [])
            used.update(ids)
            subtags.append({"name": sub["name"], "key_count": int(sub.get("key_count") or 0), "canonic_ids": ids})
        stable_id = "google:compound:" + str(compound_id)
        tags[stable_id] = {
            "stable_id": stable_id, "provider": "google", "kind": "compound",
            "name": info["base_name"], "compound_name": info["base_name"],
            "key_count": int(info.get("requested_key_count") or sum(s["key_count"] for s in subtags)),
            "subtags": subtags, "canonic_ids": [cid for s in subtags for cid in s["canonic_ids"]],
        }

    # Compound pieces without compound metadata are still grouped by their base name.
    loose = {}
    for row in sorted(rows, key=lambda r: r["name"]):
        if row["canonic_id"] in used:
            continue
        match = COMPOUND_SUBTAG_RE.match(row["name"])
        if match:
            loose.setdefault(match.group(1), []).append(row)
            continue
        stable_id = "google:tracker:" + row["canonic_id"]
        tags[stable_id] = {
            "stable_id": stable_id, "provider": "google", "kind": "tracker", "name": row["name"],
            "compound_name": None, "key_count": None, "subtags": [], "canonic_ids": [row["canonic_id"]],
        }
    for base, pieces in loose.items():
        stable_id = "google:compound-name:" + base
        tags[stable_id] = {
            "stable_id": stable_id, "provider": "google", "kind": "compound", "name": base,
            "compound_name": None, "key_count": None,
            "subtags": [{"name": p["name"], "key_count": 0, "canonic_ids": [p["canonic_id"]]} for p in pieces],
            "canonic_ids": [p["canonic_id"] for p in pieces],
        }
    return tags


# ── files ──

def list_files(username):
    files = []
    for path in sorted(user_dir(username).glob("*.json")):
        if path.name != SECRETS_FILENAME and not ACCESSORIES_RE.match(path.name):
            continue
        st = path.stat()
        files.append({
            "filename": path.name, "size": st.st_size, "updated_unix": int(st.st_mtime),
            "category": "secrets" if path.name == SECRETS_FILENAME else "accessories",
        })
    return files


def file_path(username, filename):
    """Resolve a user keyfile name; only known file shapes are allowed."""
    if not isinstance(filename, str) or filename != Path(filename).name:
        raise ValueError("invalid filename")
    if filename != SECRETS_FILENAME and not ACCESSORIES_RE.match(filename):
        raise ValueError("unsupported file type")
    path = user_dir(username) / filename
    if not path.exists():
        raise FileNotFoundError("file not found")
    return path


def delete_file(username, filename):
    path = file_path(username, filename)
    path.unlink()
    invalidate(username)
    return {"filename": filename, "category": "secrets" if filename == SECRETS_FILENAME else "accessories"}


def delete_user_files(username):
    path = user_dir(username, create=False)
    if path.exists():
        shutil.rmtree(path)
    invalidate(username)


# ── index ──

def _signature(udir):
    sig = []
    for path in sorted(udir.glob("*.json")):
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        sig.append((path.name, st.st_mtime_ns, st.st_size))
    return tuple(sig)


def _build_index(username, udir):
    index = {
        "apple": {}, "google": {}, "canonic_owner": {},
        "errors": {"apple": [], "google": []}, "warnings": [],
        "apple_complete": True, "google_complete": True,
    }
    key_owner = {}
    for path in sorted(udir.glob("accessories_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            _validate_accessories(payload)
        except (OSError, ValueError) as exc:
            index["errors"]["apple"].append({"file": path.name, "error": str(exc)})
            index["apple_complete"] = False
            continue
        for row in payload:
            keys = _accessory_keys(row)
            stable_id = "apple:" + hashlib.sha256(keys[0].encode()).hexdigest()[:32]
            tag = index["apple"].get(stable_id)
            if tag is None:
                tag = index["apple"][stable_id] = {
                    "stable_id": stable_id, "provider": "apple", "kind": "accessory",
                    "name": row.get("name") or f"Accessory {row.get('id', '')}".strip(),
                    "files": [], "keys": [],
                }
            if path.name not in tag["files"]:
                tag["files"].append(path.name)
            for key in keys:
                owner = key_owner.get(key)
                if owner is None:
                    key_owner[key] = stable_id
                    tag["keys"].append(key)
                elif owner != stable_id:
                    index["warnings"].append(
                        f"{tag['name']}: a key is also used by {index['apple'][owner]['name']} and was ignored")
    for tag in index["apple"].values():
        tag["key_count"] = len(tag["keys"])

    path = udir / SECRETS_FILENAME
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            _validate_secrets(payload)
            index["google"] = _parse_google(payload)
        except (OSError, ValueError) as exc:
            # The bridge rewrites this file in place; a half-written file must not
            # look like "all Google tags were deleted".
            index["errors"]["google"].append({"file": SECRETS_FILENAME, "error": str(exc)})
            index["google_complete"] = False
    for tag in index["google"].values():
        for canonic_id in tag["canonic_ids"]:
            index["canonic_owner"][canonic_id] = tag["stable_id"]
    return index


def invalidate(username):
    with _cache_lock:
        _cache.pop(username, None)


def get_index(username):
    """Current index for a user; rebuilt when any keyfile changed on disk."""
    udir = user_dir(username)
    sig = _signature(udir)
    with _cache_lock:
        cached = _cache.get(username)
        if cached and cached["sig"] == sig:
            return cached["index"]
    index = _build_index(username, udir)
    if not index["google_complete"] and cached:
        # Keep the last good Google view while secrets.json is unreadable.
        index["google"] = cached["index"]["google"]
        index["canonic_owner"] = cached["index"]["canonic_owner"]
    with _cache_lock:
        _cache[username] = {"sig": sig, "index": index}
    return index


def all_tags(index):
    return list(index["apple"].values()) + list(index["google"].values())


def apple_rows(index, stable_ids=None):
    """Rows in the accessories format expected by fetch_apple, one per tag."""
    rows = []
    for tag in index["apple"].values():
        if stable_ids is not None and tag["stable_id"] not in stable_ids:
            continue
        if not tag["keys"]:
            continue
        rows.append({
            "privateKey": tag["keys"][0], "additionalKeys": tag["keys"][1:],
            "name": tag["name"], "tag_id": tag["stable_id"],
            "source_file": tag["files"][0] if tag["files"] else "",
        })
    return rows


# ── Google secrets maintenance (after a sync with Google) ──

def capture_canonic_state(path):
    """canonic_id -> {name, last_seen} before a refresh, used to detect stale entries."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = (_cached_json(payload, "canonic_ids_v1", {}) or {}).get("entries") or []
    return {
        e["canonic_id"]: {"name": e.get("name", "Unknown"), "last_seen": e.get("last_seen")}
        for e in entries if isinstance(e, dict) and e.get("canonic_id")
    } or None


def prune_stale_google_entries(path, state_before):
    """Drop canonic ids / compounds that Google no longer reported during a refresh.

    The bridge bumps `last_seen` for every device Google lists; entries whose
    last_seen did not change are gone on Google's side.
    """
    result = {"pruned_canonic_ids": 0, "pruned_compounds": 0}
    if not state_before:
        return result
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    cache = _cached_json(payload, "canonic_ids_v1", {}) or {}
    entries = [e for e in cache.get("entries") or [] if isinstance(e, dict)]
    alive = []
    for entry in entries:
        before = state_before.get(entry.get("canonic_id"))
        if before is None or entry.get("last_seen") != before.get("last_seen"):
            alive.append(entry)
    if not alive:
        # Google returned nothing at all; refuse to wipe everything on a bad response.
        return result
    result["pruned_canonic_ids"] = len(entries) - len(alive)
    alive_names = {e.get("name") for e in alive}

    compounds_blob = _cached_json(payload, "compound_trackers_v1", {}) or {}
    compounds = compounds_blob.get("compounds") or {}
    for compound_id in list(compounds):
        info = compounds[compound_id]
        names = {s.get("name") for s in (info.get("subtags") or []) if isinstance(s, dict)} if isinstance(info, dict) else set()
        if not names & alive_names:
            del compounds[compound_id]
            result["pruned_compounds"] += 1

    if result["pruned_canonic_ids"] or result["pruned_compounds"]:
        cache["entries"] = alive
        payload["canonic_ids_v1"] = json.dumps(cache)
        if "compound_trackers_v1" in payload:
            payload["compound_trackers_v1"] = json.dumps(compounds_blob)
        _write_json_atomic(path, payload)
    return result
