import os
import secrets
import tomllib
from pathlib import Path


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "config.toml"


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


def load_runtime_config():
    config_path = Path(os.environ.get("OPENTAG_CONFIG", str(DEFAULT_CONFIG_PATH)))
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "rb") as f:
        doc = tomllib.load(f)

    cfg = doc.get("config", {})
    users = doc.get("users", {})

    runtime = {
        "config_path": str(config_path),
        "data_dir": str(Path(__file__).resolve().parents[1] / "data"),
        "users": users,
        "haystack": {
            "endpoint": cfg.get("haystack_endpoint", ""),
            "login": cfg.get("haystack_login", ""),
            "password": cfg.get("haystack_password", ""),
            "request_interval_seconds": max(1, _to_int(cfg.get("apple_request_interval_seconds", 60), 60)),
            "error_backoff_seconds": max(1, _to_int(cfg.get("apple_error_backoff_seconds", 300), 300)),
        },
        "local_history": _to_bool(cfg.get("local_history", True), default=True),
        "google_auto_query_interval_min": _to_int(cfg.get("google_auto_query_interval_min", 60), default=60),
        "apple_auto_query_interval_min": _to_int(cfg.get("apple_auto_query_interval_min", cfg.get("appple_auto_query_interval_min", 600)), default=600),
        "apple_manual_wait_timeout_seconds": max(1, min(180, _to_int(cfg.get("apple_manual_wait_timeout_seconds", 180), 180))),
        "google_refresh_settle_seconds": max(0, _to_int(cfg.get("google_refresh_settle_seconds", 15), 15)),
        "apple_singleton_sweep_enabled": _to_bool(cfg.get("apple_singleton_sweep_enabled", True), True),
        "apple_singleton_sweep_interval_hours": max(0, _to_int(cfg.get("apple_singleton_sweep_interval_hours", 24), 24)),
        "apple_singleton_interval_seconds": max(1, _to_int(cfg.get("apple_singleton_interval_seconds", 60), 60)),
        "apple_bulk_batch_size": max(1, _to_int(cfg.get("apple_bulk_batch_size", 200), 200)),
        "apple_query_days": max(1, _to_int(cfg.get("apple_query_days", 7), 7)),
        "google_key_refresh_interval_hours": _to_int(cfg.get("google_key_refresh_interval_hours", 24), default=24),
        "history_retention_days": _to_int(cfg.get("history_retention_days", 30), default=30),
        "log_level": os.environ.get("OPENTAG_LOG_LEVEL", cfg.get("log_level", "INFO")).upper(),
        "secret_key": os.environ.get("OPENTAG_SECRET_KEY", secrets.token_urlsafe(48)),
    }

    redis_url = os.environ.get("REDIS_URL")
    runtime["redis"] = {
        "url": redis_url,
        "host": os.environ.get("REDIS_HOST", "db"),
        "port": _to_int(os.environ.get("REDIS_PORT", "6379"), default=6379),
        "db": _to_int(os.environ.get("REDIS_DB", "0"), default=0),
    }

    return runtime
