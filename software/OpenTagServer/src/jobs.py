"""Background jobs: every fetch / sync (manual or automatic) is a row with live progress.

States: queued -> running -> done | partial | failed. The scheduler picks queued jobs
(manual before automatic) and runs them; the UI polls them by id. Rows are kept for a
week for the record.
"""
import json
import time

import db


PROVIDERS = {"apple_fetch": "apple", "google_fetch": "google", "google_sync": "google"}
LABELS = {"apple_fetch": "Apple fetch", "google_fetch": "Google fetch", "google_sync": "Google key sync"}
TRIGGERS = {"manual": 0, "upload": 0, "auto": 1}  # trigger -> priority (lower runs first)
ACTIVE = ("queued", "running")
KEEP_SECONDS = 7 * 86400


class Progress:
    """Handed to job runners: report what is happening as it happens."""

    def __init__(self, job_id):
        self.job_id = job_id

    def __call__(self, stage=None, progress=None, total=None):
        fields, params = [], []
        for column, value in (("stage", stage), ("progress", progress), ("total", total)):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value)
        if fields:
            db.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", (*params, self.job_id))


def _row(row):
    if row is None:
        return None
    job = {key: row[key] for key in row.keys() if key not in ("params", "result")}
    job["params"] = json.loads(row["params"] or "{}")
    job["result"] = json.loads(row["result"]) if row["result"] else None
    job["label"] = LABELS.get(row["kind"], row["kind"])
    job["active"] = row["state"] in ACTIVE
    return job


def create(user_id, kind, trigger="manual", params=None):
    """Queue a job, or return the identical one that is already queued/running."""
    if kind not in PROVIDERS or trigger not in TRIGGERS:
        raise ValueError("unknown job")
    params_json = json.dumps(params or {}, sort_keys=True)
    with db.transaction() as conn:
        existing = conn.execute(
            "SELECT * FROM jobs WHERE user_id = ? AND kind = ? AND params = ? AND state IN ('queued', 'running') "
            "ORDER BY id LIMIT 1",
            (user_id, kind, params_json),
        ).fetchone()
        if existing is not None:
            if TRIGGERS[trigger] < existing["priority"] and existing["state"] == "queued":
                # A user asked for what was only scheduled: let it jump the queue.
                conn.execute("UPDATE jobs SET priority = ?, trigger = ? WHERE id = ?",
                             (TRIGGERS[trigger], trigger, existing["id"]))
                existing = conn.execute("SELECT * FROM jobs WHERE id = ?", (existing["id"],)).fetchone()
            return _row(existing)
        cursor = conn.execute(
            "INSERT INTO jobs(user_id, provider, kind, trigger, priority, params, state, stage, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'queued', 'Queued', ?)",
            (user_id, PROVIDERS[kind], kind, trigger, TRIGGERS[trigger], params_json, int(time.time())),
        )
        return _row(conn.execute("SELECT * FROM jobs WHERE id = ?", (cursor.lastrowid,)).fetchone())


def get(user_id, job_id):
    return _row(db.query_one("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)))


def list_recent(user_id, finished_within=120):
    """Active jobs plus the ones that finished in the last couple of minutes."""
    since = int(time.time()) - finished_within
    rows = db.query(
        "SELECT * FROM jobs WHERE user_id = ? AND (state IN ('queued', 'running') OR finished_at >= ?) "
        "ORDER BY id DESC LIMIT 50",
        (user_id, since),
    )
    return [_row(r) for r in rows]


def has_active(user_id, kind):
    return db.query_one(
        "SELECT 1 FROM jobs WHERE user_id = ? AND kind = ? AND state IN ('queued', 'running') LIMIT 1",
        (user_id, kind),
    ) is not None


def next_queued(provider, user_id=None):
    """Highest-priority queued job of a provider (optionally for one user)."""
    sql = "SELECT * FROM jobs WHERE state = 'queued' AND provider = ?"
    params = [provider]
    if user_id is not None:
        sql += " AND user_id = ?"
        params.append(user_id)
    return _row(db.query_one(sql + " ORDER BY priority, id LIMIT 1", params))


def queued_users(provider):
    return [r["user_id"] for r in db.query(
        "SELECT DISTINCT user_id FROM jobs WHERE state = 'queued' AND provider = ?", (provider,))]


def claim(job_id):
    """queued -> running; False if someone else got it (or it is gone)."""
    with db.transaction() as conn:
        cursor = conn.execute(
            "UPDATE jobs SET state = 'running', stage = 'Starting', started_at = ? WHERE id = ? AND state = 'queued'",
            (int(time.time()), job_id),
        )
        return cursor.rowcount == 1


def finish(job_id, state, result=None, error=None, stage=None):
    db.execute(
        "UPDATE jobs SET state = ?, result = ?, error = ?, stage = COALESCE(?, stage), finished_at = ? WHERE id = ?",
        (state, json.dumps(result) if result is not None else None, error, stage, int(time.time()), job_id),
    )


def recover_after_restart():
    """Jobs that were running when the process stopped cannot be resumed."""
    db.execute(
        "UPDATE jobs SET state = 'failed', error = 'Interrupted by a server restart', finished_at = ? "
        "WHERE state = 'running'",
        (int(time.time()),),
    )


def purge_old():
    db.execute("DELETE FROM jobs WHERE state NOT IN ('queued', 'running') AND finished_at < ?",
               (int(time.time()) - KEEP_SECONDS,))
