"""Tags and devices.

A *tag* is one provider identity (an Apple accessory, a Google compound tracker or a
plain Google tracker). Tag rows only hold metadata; key material stays in the
keyfiles (see keystore). A *device* is one physical thing and owns one or more
tags. Every tag belongs to exactly one device: new tags get an auto-created
single-tag device, grouping merges tags into one device, ungrouping splits them
back into single-tag devices.
"""
import logging
import re
import time
import uuid

import db
import keystore


logger = logging.getLogger(__name__)

DEVICE_COLORS = ["#ff961f", "#18a96b", "#1f78ff", "#e53935", "#9c27b0", "#00bcd4", "#795548", "#607d8b"]
MAX_RETENTION_DAYS = 36500
ICON_RE = re.compile(r"^[a-z0-9-]{1,40}$")  # Font Awesome icon name (without "fa-")


class InventoryError(ValueError):
    pass


def _new_device(conn, user_id, name, color=None, auto_created=True, icon=None):
    if color is None:
        count = conn.execute("SELECT COUNT(*) FROM devices WHERE user_id = ?", (user_id,)).fetchone()[0]
        color = DEVICE_COLORS[count % len(DEVICE_COLORS)]
    device_id = uuid.uuid4().hex[:12]
    now = int(time.time())
    conn.execute(
        "INSERT INTO devices(id, user_id, name, color, icon, retention_days, auto_created, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
        (device_id, user_id, name, color, icon, 1 if auto_created else 0, now, now),
    )
    return device_id


def _drop_empty_devices(conn, user_id):
    conn.execute(
        "DELETE FROM devices WHERE user_id = ? AND id NOT IN (SELECT device_id FROM tags WHERE user_id = ?)",
        (user_id, user_id),
    )


def sync_user(user_id, username, prune=False):
    """Bring tag rows in line with the keyfiles.

    New tags are always added. Tags whose key material disappeared are deleted
    (with their history) only when `prune` is set, i.e. after an explicit user
    action or at startup, and never for a provider whose files failed to parse.
    """
    index = keystore.get_index(username)
    wanted = {tag["stable_id"]: tag for tag in keystore.all_tags(index)}
    added, removed = [], []
    with db.transaction() as conn:
        existing = {
            row["stable_id"]: row
            for row in conn.execute("SELECT id, stable_id, provider, name, device_id FROM tags WHERE user_id = ?", (user_id,))
        }
        now = int(time.time())
        for stable_id, tag in wanted.items():
            row = existing.get(stable_id)
            if row is None:
                device_id = _new_device(conn, user_id, tag["name"])
                conn.execute(
                    "INSERT INTO tags(user_id, stable_id, provider, name, device_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, stable_id, tag["provider"], tag["name"], device_id, now),
                )
                added.append(stable_id)
            elif row["name"] != tag["name"]:
                conn.execute("UPDATE tags SET name = ? WHERE id = ?", (tag["name"], row["id"]))
                # Keep untouched auto-created single-tag devices named after their tag.
                conn.execute(
                    "UPDATE devices SET name = ?, updated_at = ? WHERE id = ? AND auto_created = 1 AND name = ? "
                    "AND (SELECT COUNT(*) FROM tags WHERE device_id = devices.id) = 1",
                    (tag["name"], now, row["device_id"], row["name"]),
                )
        if prune:
            complete = {"apple": index["apple_complete"], "google": index["google_complete"]}
            for stable_id, row in existing.items():
                if stable_id not in wanted and complete[row["provider"]]:
                    conn.execute("DELETE FROM tags WHERE id = ?", (row["id"],))
                    removed.append(stable_id)
            _drop_empty_devices(conn, user_id)
    if added or removed:
        logger.info("Inventory sync for %s: %d tags added, %d removed", username, len(added), len(removed))
    return {"added": added, "removed": removed}


def effective_retention(device_days, global_days):
    """Device retention capped by the config maximum (0 = unlimited)."""
    if global_days and global_days > 0:
        return min(device_days, global_days) if device_days else global_days
    return device_days or None


def get_inventory(user_id, username, global_retention_days):
    index = keystore.get_index(username)
    meta = {tag["stable_id"]: tag for tag in keystore.all_tags(index)}
    last_seen = {
        row["tag_id"]: row["ts"]
        for row in db.query("SELECT tag_id, MAX(ts) AS ts FROM events WHERE user_id = ? GROUP BY tag_id", (user_id,))
    }
    tags_by_device = {}
    for row in db.query("SELECT * FROM tags WHERE user_id = ? ORDER BY provider, name", (user_id,)):
        info = meta.get(row["stable_id"])
        tag = {
            "id": row["stable_id"], "provider": row["provider"], "name": row["name"],
            "device_id": row["device_id"], "missing": info is None,
            "kind": info["kind"] if info else None,
            "key_count": info.get("key_count") if info else None,
            "last_seen_unix": last_seen.get(row["id"]),
        }
        if info and row["provider"] == "apple":
            tag["files"] = list(info["files"])
        if info and row["provider"] == "google":
            tag["subtags"] = [{"name": s["name"], "key_count": s["key_count"]} for s in info["subtags"]]
            tag["canonic_ids"] = list(info["canonic_ids"])
        tags_by_device.setdefault(row["device_id"], []).append(tag)

    devices = []
    for row in db.query("SELECT * FROM devices WHERE user_id = ? ORDER BY name COLLATE NOCASE", (user_id,)):
        devices.append({
            "id": row["id"], "name": row["name"], "color": row["color"], "icon": row["icon"],
            "retention_days": row["retention_days"],
            "effective_retention_days": effective_retention(row["retention_days"], global_retention_days),
            "auto_created": bool(row["auto_created"]),
            "tags": tags_by_device.get(row["id"], []),
        })
    return {
        "devices": devices,
        "max_retention_days": global_retention_days or None,
        "errors": index["errors"]["apple"] + index["errors"]["google"],
        "warnings": index["warnings"],
    }


# ── device operations ──

def _tag_rows(conn, user_id, stable_ids):
    stable_ids = list(dict.fromkeys(stable_ids))
    if not stable_ids:
        return []
    marks = ",".join("?" * len(stable_ids))
    rows = conn.execute(
        f"SELECT id, stable_id, name, device_id FROM tags WHERE user_id = ? AND stable_id IN ({marks})",
        (user_id, *stable_ids),
    ).fetchall()
    if len(rows) != len(stable_ids):
        raise InventoryError("unknown tag id")
    return rows


def _validate_device_fields(name=None, color=None, retention_days=None, icon=None):
    if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 100):
        raise InventoryError("device name must be 1-100 characters")
    if color is not None and (not isinstance(color, str) or len(color) != 7 or not color.startswith("#")):
        raise InventoryError("color must look like #rrggbb")
    if icon is not None and (not isinstance(icon, str) or not ICON_RE.match(icon)):
        raise InventoryError("unknown icon")
    if retention_days is not None and (
            not isinstance(retention_days, int) or isinstance(retention_days, bool)
            or not 1 <= retention_days <= MAX_RETENTION_DAYS):
        raise InventoryError("retention_days must be a whole number of days, or empty for the maximum")


def _get_device(conn, user_id, device_id):
    row = conn.execute("SELECT * FROM devices WHERE id = ? AND user_id = ?", (device_id, user_id)).fetchone()
    if not row:
        raise LookupError("device not found")
    return row


def group_tags(user_id, name, color, tag_ids, icon=None):
    """Create a device from the given tags (moving them out of their current devices)."""
    _validate_device_fields(name=name, color=color, icon=icon)
    with db.transaction() as conn:
        rows = _tag_rows(conn, user_id, tag_ids)
        if not rows:
            raise InventoryError("select at least one tag")
        device_id = _new_device(conn, user_id, name.strip(), color, auto_created=False, icon=icon)
        conn.execute(
            f"UPDATE tags SET device_id = ? WHERE id IN ({','.join('?' * len(rows))})",
            (device_id, *[r["id"] for r in rows]),
        )
        _drop_empty_devices(conn, user_id)
    return device_id


def update_device(user_id, device_id, name=None, color=None, retention_days=..., tag_ids=None, icon=None):
    """Rename / recolor / set retention, and optionally set the exact tag membership.

    Tags removed from the device each get their own new single-tag device.
    `retention_days=None` means "use the maximum"; leave it as `...` to keep it.
    """
    _validate_device_fields(name=name, color=color, icon=icon,
                            retention_days=None if retention_days is ... else retention_days)
    with db.transaction() as conn:
        device = _get_device(conn, user_id, device_id)
        now = int(time.time())
        fields, params = ["updated_at = ?", "auto_created = 0"], [now]
        if name is not None:
            fields.append("name = ?")
            params.append(name.strip())
        if color is not None:
            fields.append("color = ?")
            params.append(color)
        if icon is not None:
            fields.append("icon = ?")
            params.append(icon)
        if retention_days is not ...:
            fields.append("retention_days = ?")
            params.append(retention_days)
        conn.execute(f"UPDATE devices SET {', '.join(fields)} WHERE id = ?", (*params, device["id"]))

        if tag_ids is not None:
            rows = _tag_rows(conn, user_id, tag_ids)
            if not rows:
                raise InventoryError("a device needs at least one tag; delete its key files to remove it")
            keep = {r["id"] for r in rows}
            for row in conn.execute("SELECT id, name FROM tags WHERE device_id = ?", (device["id"],)).fetchall():
                if row["id"] not in keep:
                    conn.execute("UPDATE tags SET device_id = ? WHERE id = ?", (_new_device(conn, user_id, row["name"]), row["id"]))
            conn.execute(
                f"UPDATE tags SET device_id = ? WHERE id IN ({','.join('?' * len(rows))})",
                (device["id"], *keep),
            )
            _drop_empty_devices(conn, user_id)


def ungroup_device(user_id, device_id):
    """Dissolve a custom device: each of its tags goes back to an auto-created single-tag device."""
    with db.transaction() as conn:
        device = _get_device(conn, user_id, device_id)
        if device["auto_created"]:
            raise InventoryError("this tag is not part of a custom device")
        rows = conn.execute("SELECT id, name FROM tags WHERE device_id = ?", (device["id"],)).fetchall()
        for row in rows:
            conn.execute("UPDATE tags SET device_id = ? WHERE id = ?", (_new_device(conn, user_id, row["name"]), row["id"]))
        conn.execute("DELETE FROM devices WHERE id = ?", (device["id"],))


def delete_preview(user_id, username, filename):
    """What deleting a keyfile would remove, so the UI can ask for confirmation.

    Mirrors sync_user(prune=True) after the file is gone: tags without key material
    in any remaining file are deleted together with their history.
    """
    index = keystore.get_index(username)
    is_secrets = filename == keystore.SECRETS_FILENAME
    remaining = set()
    for tag in index["apple"].values():
        if [f for f in tag["files"] if f != filename]:
            remaining.add(tag["stable_id"])
    if not is_secrets:
        remaining.update(index["google"])
    complete = {
        "apple": all(e["file"] == filename for e in index["errors"]["apple"]),
        "google": is_secrets or index["google_complete"],
    }
    counts = {
        row["tag_id"]: row["n"]
        for row in db.query("SELECT tag_id, COUNT(*) AS n FROM events WHERE user_id = ? GROUP BY tag_id", (user_id,))
    }
    rows = db.query(
        "SELECT t.id, t.stable_id, t.provider, t.name AS tag_name, t.device_id, d.name AS device_name, "
        "d.auto_created, d.icon, d.color, (SELECT COUNT(*) FROM tags x WHERE x.device_id = t.device_id) AS device_size "
        "FROM tags t JOIN devices d ON d.id = t.device_id WHERE t.user_id = ? ORDER BY d.name, t.name",
        (user_id,),
    )
    devices = {}
    for row in rows:
        if row["stable_id"] in remaining or not complete[row["provider"]]:
            continue
        device = devices.setdefault(row["device_id"], {
            "name": row["device_name"], "custom": not row["auto_created"],
            "icon": row["icon"], "color": row["color"],
            "tag_count": row["device_size"], "tags": [], "events": 0,
        })
        device["tags"].append({"name": row["tag_name"], "provider": row["provider"]})
        device["events"] += counts.get(row["id"], 0)
    result = []
    for device in devices.values():
        device["deleted"] = len(device["tags"]) >= device["tag_count"]
        result.append(device)
    return {
        "filename": filename,
        "devices": result,
        "tags_removed": sum(len(d["tags"]) for d in result),
        "events_removed": sum(d["events"] for d in result),
    }
