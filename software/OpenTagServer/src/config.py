import logging
import os
import tomllib
from pathlib import Path


logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _to_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _admin_from_doc(doc):
    """The single admin account. `[admin]` is the current format; a lone
    `[users.<name>]` entry with role = "admin" is accepted for older configs."""
    admin = doc.get("admin")
    if isinstance(admin, dict) and admin.get("username"):
        return {"username": str(admin["username"]), "password_hash": str(admin.get("password_hash") or "")}

    legacy = [
        (name, entry) for name, entry in (doc.get("users") or {}).items()
        if isinstance(entry, dict) and entry.get("role") == "admin"
    ]
    if len(legacy) == 1:
        name, entry = legacy[0]
        logger.warning("config: [users.%s] is deprecated, move the admin account to an [admin] section", name)
        return {"username": name, "password_hash": str(entry.get("password_hash") or "")}
    return None


# Settings that became fixed defaults (see apple_service / apple_requests / google_service).
RETIRED_KEYS = (
    "apple_error_backoff_seconds", "apple_manual_wait_timeout_seconds", "google_refresh_settle_seconds",
    "apple_singleton_sweep_interval_hours", "apple_singleton_interval_seconds", "apple_bulk_batch_size",
    "apple_query_days",
)


def load_runtime_config():
    data_dir = Path(os.environ.get("OPENTAG_DATA_DIR", str(DEFAULT_DATA_DIR)))
    config_path = Path(os.environ.get("OPENTAG_CONFIG", str(data_dir / "config.toml")))
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "rb") as f:
        doc = tomllib.load(f)

    cfg = doc.get("config", {})
    admin = _admin_from_doc(doc)
    if not admin or not admin["password_hash"]:
        raise ValueError("config: an [admin] section with username and password_hash is required")

    runtime = {
        "config_path": str(config_path),
        "data_dir": str(data_dir),
        "secrets_dir": str(data_dir / "secrets"),
        "database_path": os.environ.get("OPENTAG_DB", str(data_dir / "opentag.db")),
        "admin": admin,
        "haystack": {
            "endpoint": cfg.get("haystack_endpoint", ""),
            "login": cfg.get("haystack_login", ""),
            "password": cfg.get("haystack_password", ""),
            # Spacing between automatic Haystack requests (bulk batches and gap recovery).
            "request_interval_seconds": max(1, _to_int(cfg.get("apple_request_interval_seconds", 60), 60)),
        },
        "local_history": _to_bool(cfg.get("local_history", True), default=True),
        "google_auto_query_interval_min": _to_int(cfg.get("google_auto_query_interval_min", 60), default=60),
        "apple_auto_query_interval_min": _to_int(cfg.get("apple_auto_query_interval_min", 600), default=600),
        # Gap recovery: query every Apple key on its own, one request at a time (old name accepted).
        "apple_gap_recovery": _to_bool(cfg.get("apple_gap_recovery", cfg.get("apple_singleton_sweep_enabled", True)), True),
        "google_key_refresh_interval_hours": _to_int(cfg.get("google_key_refresh_interval_hours", 24), default=24),
        # Global maximum; devices may choose a shorter retention in the UI. 0 = unlimited.
        "history_retention_days": max(0, _to_int(cfg.get("history_retention_days", 30), default=30)),
        "log_level": os.environ.get("OPENTAG_LOG_LEVEL", cfg.get("log_level", "INFO")).upper(),
    }
    retired = [key for key in RETIRED_KEYS if key in cfg]
    if retired:
        # Logged before logging is configured, hence warning (shown by Python's fallback handler).
        logger.warning("config: these settings are no longer used and can be removed: %s", ", ".join(retired))
    return runtime
