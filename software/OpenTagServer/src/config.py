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
        },
        "local_history": _to_bool(cfg.get("local_history", True), default=True),
        "google_auto_query_interval_min": _to_int(cfg.get("google_auto_query_interval_min", 60), default=60),
        "apple_auto_query_interval_min": _to_int(cfg.get("appple_auto_query_interval_min", 600), default=600),
        "history_retention_days": _to_int(cfg.get("history_retention_days", 30), default=30),
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
