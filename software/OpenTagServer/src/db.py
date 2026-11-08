"""SQLite storage: one connection per thread, WAL mode, numbered schema migrations."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

_local = threading.local()
_db_path = None
_init_lock = threading.Lock()


MIGRATIONS = [
    # 1: initial schema
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
        created_at INTEGER NOT NULL
    );

    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at INTEGER NOT NULL
    );
    CREATE INDEX sessions_user ON sessions(user_id);

    CREATE TABLE devices (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        color TEXT NOT NULL,
        retention_days INTEGER,
        auto_created INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE INDEX devices_user ON devices(user_id);

    CREATE TABLE tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        stable_id TEXT NOT NULL,
        provider TEXT NOT NULL CHECK (provider IN ('apple', 'google')),
        name TEXT NOT NULL,
        device_id TEXT NOT NULL REFERENCES devices(id),
        created_at INTEGER NOT NULL,
        UNIQUE (user_id, stable_id)
    );
    CREATE INDEX tags_device ON tags(device_id);

    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        ts INTEGER NOT NULL,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        accuracy REAL,
        status TEXT,
        part TEXT,
        dedupe_key TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        UNIQUE (tag_id, dedupe_key)
    );
    CREATE INDEX events_user_ts ON events(user_id, ts);
    CREATE INDEX events_tag_ts ON events(tag_id, ts);

    CREATE TABLE alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider TEXT NOT NULL,
        type TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 1,
        first_at INTEGER NOT NULL,
        last_at INTEGER NOT NULL,
        dismissed_at INTEGER
    );
    CREATE INDEX alerts_user ON alerts(user_id, dismissed_at);

    CREATE TABLE status (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        payload TEXT NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, name)
    );

    -- Scheduler / worker state. user_id NULL = global.
    CREATE TABLE kv (
        key TEXT PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        value TEXT NOT NULL
    );
    """,
    # 2: device icons (Font Awesome icon name; NULL = default)
    """
    ALTER TABLE devices ADD COLUMN icon TEXT
    """,
    # 3: background jobs (manual + automatic fetches/syncs) with progress
    """
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider TEXT NOT NULL,
        kind TEXT NOT NULL,
        trigger TEXT NOT NULL,
        priority INTEGER NOT NULL,
        params TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL,
        stage TEXT NOT NULL DEFAULT '',
        progress INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL DEFAULT 0,
        result TEXT,
        error TEXT,
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER
    );
    CREATE INDEX jobs_queue ON jobs(state, provider, priority, id);
    CREATE INDEX jobs_user ON jobs(user_id, id)
    """,
]


def init(path):
    """Set the database path and apply pending migrations. Safe to call repeatedly."""
    global _db_path
    with _init_lock:
        new_path = str(Path(path))
        if _db_path != new_path:
            close()
        _db_path = new_path
        Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = connect()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for index in range(version, len(MIGRATIONS)):
            conn.executescript("BEGIN;" + MIGRATIONS[index] + f"; PRAGMA user_version = {index + 1}; COMMIT;")
        try:
            Path(_db_path).chmod(0o600)
        except OSError:
            pass


def connect():
    """Return this thread's connection (created on first use)."""
    if _db_path is None:
        raise RuntimeError("database not initialised")
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == _db_path:
        return conn
    conn = sqlite3.connect(_db_path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    _local.conn = conn
    _local.path = _db_path
    return conn


def close():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def transaction():
    """Write transaction (BEGIN IMMEDIATE so concurrent writers queue instead of failing)."""
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def query(sql, params=()):
    return connect().execute(sql, params).fetchall()


def query_one(sql, params=()):
    return connect().execute(sql, params).fetchone()


def execute(sql, params=()):
    with transaction() as conn:
        return conn.execute(sql, params)


# ── key/value state ──

def kv_get(key, default=None):
    row = query_one("SELECT value FROM kv WHERE key = ?", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except ValueError:
        return default


def kv_set(key, value, user_id=None):
    execute(
        "INSERT INTO kv(key, user_id, value) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, user_id, json.dumps(value)),
    )


def kv_delete(key):
    execute("DELETE FROM kv WHERE key = ?", (key,))


def user_key(user_id, name):
    return f"u:{int(user_id)}:{name}"
