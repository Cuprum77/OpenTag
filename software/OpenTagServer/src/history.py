"""Location history: one row per report, de-duplicated per tag."""
import hashlib
import logging
import time

import db
from inventory import effective_retention


logger = logging.getLogger(__name__)


def store_events(user_id, events):
    """Insert events ({tag_id: stable id, ts, latitude, longitude, dedupe_key, ...}).

    Duplicates (same tag + dedupe key) and events for unknown tags are ignored.
    Returns the number of new rows.
    """
    if not events:
        return 0
    tag_ids = {row["stable_id"]: row["id"] for row in db.query("SELECT id, stable_id FROM tags WHERE user_id = ?", (user_id,))}
    now = int(time.time())
    added = 0
    with db.transaction() as conn:
        for event in events:
            tag_id = tag_ids.get(event.get("tag_id"))
            lat, lon = event.get("latitude"), event.get("longitude")
            if tag_id is None or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO events(user_id, tag_id, ts, latitude, longitude, accuracy, status, part, dedupe_key, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, tag_id, int(event.get("ts") or 0), float(lat), float(lon), event.get("accuracy"),
                 event.get("status"), event.get("part"), event["dedupe_key"], now),
            )
            added += cur.rowcount
    return added


def get_events(user_id, days, limit=20000):
    cutoff = int(time.time() - float(days) * 86400)
    rows = db.query(
        "SELECT e.ts, e.latitude, e.longitude, e.accuracy, e.status, e.part, "
        "t.stable_id, t.provider, t.name AS tag_name, t.device_id "
        "FROM events e JOIN tags t ON t.id = e.tag_id "
        "WHERE e.user_id = ? AND e.ts >= ? ORDER BY e.ts DESC LIMIT ?",
        (user_id, cutoff, int(limit)),
    )
    return [{
        "provider": r["provider"], "tag": r["tag_name"], "tag_id": r["stable_id"], "device_id": r["device_id"],
        "latitude": r["latitude"], "longitude": r["longitude"], "accuracy": r["accuracy"],
        "timestamp_unix": r["ts"], "status": r["status"], "part": r["part"],
    } for r in rows]


def purge_expired(global_retention_days):
    """Apply per-device retention (capped by the config maximum). Returns rows removed."""
    now = time.time()
    removed = 0
    for device in db.query("SELECT id, retention_days FROM devices"):
        days = effective_retention(device["retention_days"], global_retention_days)
        if not days:
            continue
        cur = db.execute(
            "DELETE FROM events WHERE ts < ? AND tag_id IN (SELECT id FROM tags WHERE device_id = ?)",
            (int(now - days * 86400), device["id"]),
        )
        removed += cur.rowcount
    return removed


# ── provider payload → events ──

def apple_events(payload):
    """Decoded Haystack reports (see fetch_apple) → events."""
    from fetch_apple import apple_history_events
    events = []
    for row in apple_history_events(payload or {}):
        if not row.get("tag_id") or not row.get("report_id"):
            continue
        events.append({
            "tag_id": row["tag_id"], "ts": row["timestamp_unix"],
            "latitude": row["latitude"], "longitude": row["longitude"],
            "accuracy": row.get("accuracy"), "status": "APPLE", "dedupe_key": row["report_id"],
        })
    return events


def google_events(payload, index):
    """Bridge script output (single or compound fetch) → events.

    The bridge prints `targets` at the top level; older call sites wrapped the
    output in `details`, so both shapes are accepted.
    """
    if not isinstance(payload, dict):
        return []
    targets = payload.get("targets")
    if targets is None and isinstance(payload.get("details"), dict):
        targets = payload["details"].get("targets")
    owner = index["canonic_owner"]
    by_subtag_name = {
        sub["name"]: tag["stable_id"]
        for tag in index["google"].values() for sub in tag["subtags"]
    }
    events = []
    for target in targets or []:
        if not isinstance(target, dict):
            continue
        ids = list(target.get("resolved_canonic_ids") or []) + [target.get("requested_canonic_id")]
        stable_id = next((owner[i] for i in ids if i in owner), None)
        part = target.get("resolved_device_name") or target.get("label")
        if stable_id is None and part:
            stable_id = by_subtag_name.get(part)
        if stable_id is None:
            continue
        for loc in (target.get("result") or {}).get("locations") or []:
            if not isinstance(loc, dict) or loc.get("type") != "geo":
                continue
            ts = int(loc.get("time_unix") or 0)
            lat, lon = loc.get("latitude"), loc.get("longitude")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            key = hashlib.sha256(f"{ts}:{lat:.7f}:{lon:.7f}".encode()).hexdigest()
            events.append({
                "tag_id": stable_id, "ts": ts, "latitude": lat, "longitude": lon,
                "accuracy": loc.get("accuracy"), "status": loc.get("status"), "part": part, "dedupe_key": key,
            })
    return events
