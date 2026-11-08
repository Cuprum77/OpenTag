"""Alerts (sticky, grouped) and the latest fetch status per provider."""
import json
import re
import time

import db


DISMISSED_ALERT_RETENTION_SECONDS = 30 * 86400


def clean_error_message(error):
    """Reduce an exception / script stderr to its last meaningful line."""
    text = str(error or "").strip()
    if not text:
        return "Unknown error"
    if "Traceback" in text:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line or line.startswith(("Traceback", "File ", "During handling", "The above exception", "^")):
                continue
            match = re.match(r"^[\w.]+(?:Error|Exception|Timeout\w*)\s*:\s*(.+)$", line)
            return match.group(1).strip() if match else line
        return "Unknown error"
    return text[:1000]


# ── alerts ──

def add_alert(user_id, provider, type_, message, target=""):
    """Record a problem. Repeats of an active alert bump its counter instead of piling up."""
    message = clean_error_message(message)
    target = str(target or "")
    now = int(time.time())
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM alerts WHERE user_id = ? AND dismissed_at IS NULL AND provider = ? AND type = ? "
            "AND target = ? AND message = ?",
            (user_id, provider, type_, target, message),
        ).fetchone()
        if row:
            conn.execute("UPDATE alerts SET count = count + 1, last_at = ? WHERE id = ?", (now, row["id"]))
        else:
            conn.execute(
                "INSERT INTO alerts(user_id, provider, type, target, message, count, first_at, last_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (user_id, provider, type_, target, message, now, now),
            )


def list_alerts(user_id):
    rows = db.query(
        "SELECT * FROM alerts WHERE user_id = ? AND dismissed_at IS NULL ORDER BY last_at DESC", (user_id,))
    return [{
        "id": r["id"], "provider": r["provider"], "type": r["type"], "target": r["target"],
        "message": r["message"], "count": r["count"], "first_unix": r["first_at"], "last_unix": r["last_at"],
    } for r in rows]


def dismiss_alert(user_id, alert_id):
    cur = db.execute(
        "UPDATE alerts SET dismissed_at = ? WHERE id = ? AND user_id = ? AND dismissed_at IS NULL",
        (int(time.time()), alert_id, user_id),
    )
    return cur.rowcount > 0


def dismiss_all_alerts(user_id):
    cur = db.execute(
        "UPDATE alerts SET dismissed_at = ? WHERE user_id = ? AND dismissed_at IS NULL", (int(time.time()), user_id))
    return cur.rowcount


def purge_dismissed_alerts():
    db.execute("DELETE FROM alerts WHERE dismissed_at IS NOT NULL AND dismissed_at < ?",
               (int(time.time()) - DISMISSED_ALERT_RETENTION_SECONDS,))


# ── fetch status ──

_HEAVY_KEYS = ("reports", "failures", "targets", "raw_output")


def _summarise(details):
    if not isinstance(details, dict):
        return details
    return {k: v for k, v in details.items() if k not in _HEAVY_KEYS}


def set_status(user_id, name, ok, kind, error=None, details=None, target=None):
    payload = {
        "ok": bool(ok), "kind": kind, "timestamp_unix": int(time.time()),
        "error": clean_error_message(error) if error else None,
        "target": target, "details": _summarise(details),
    }
    db.execute(
        "INSERT INTO status(user_id, name, payload, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, name) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
        (user_id, name, json.dumps(payload), payload["timestamp_unix"]),
    )
    return payload


def get_status(user_id, name):
    row = db.query_one("SELECT payload FROM status WHERE user_id = ? AND name = ?", (user_id, name))
    return json.loads(row["payload"]) if row else None


def clear_status(user_id, name):
    db.execute("DELETE FROM status WHERE user_id = ? AND name = ?", (user_id, name))
