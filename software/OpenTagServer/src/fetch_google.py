import json
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_SCRIPT_PATH = Path(__file__).resolve().parent / "ext" / "portable_fetch_update.py"
SCRIPT_PATH = Path(os.environ.get("OPENTAG_GOOGLE_FETCH_SCRIPT", str(DEFAULT_SCRIPT_PATH)))
PYTHON_BIN = os.environ.get("OPENTAG_GOOGLE_FETCH_PYTHON", sys.executable)


def _run_google_script(args, timeout=120):
    cmd = [PYTHON_BIN, str(SCRIPT_PATH), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)

    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "google script failed")

    text = (proc.stdout or "").strip()
    if not text:
        return {"ok": True, "raw_output": ""}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": True, "raw_output": text}


def list_google_targets(auth_file):
    return _run_google_script(["--auth-file", str(auth_file), "--json"], timeout=120)


def refresh_google_announcements(auth_file, force_upload=False):
    args = ["--auth-file", str(auth_file), "--json", "--refresh-announcements"]
    if force_upload:
        args.append("--force-upload")
    return _run_google_script(args, timeout=300)


def fetch_google_locations(auth_file, canonic_id=None, compound_name=None, timeout=45):
    args = ["--auth-file", str(auth_file), "--json", "--timeout", str(timeout)]
    if canonic_id:
        args.extend(["--canonic-id", canonic_id])
    elif compound_name:
        args.extend(["--compound-name", compound_name])
    else:
        raise ValueError("canonic_id or compound_name is required")

    return _run_google_script(args, timeout=max(120, timeout + 60))
