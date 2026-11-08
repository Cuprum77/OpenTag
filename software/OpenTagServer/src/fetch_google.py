"""Runs the pinned OpenTagBridge script (portable_fetch_update.py) as a subprocess.

stdout carries the final --json result; with --progress the script also writes
`OTB-PROGRESS {json}` lines to stderr while it works, which are passed to a callback.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path


DEFAULT_SCRIPT_PATH = Path(__file__).resolve().parent / "ext" / "portable_fetch_update.py"
SCRIPT_PATH = Path(os.environ.get("OPENTAG_GOOGLE_FETCH_SCRIPT", str(DEFAULT_SCRIPT_PATH)))
PYTHON_BIN = os.environ.get("OPENTAG_GOOGLE_FETCH_PYTHON", sys.executable)
PROGRESS_PREFIX = "OTB-PROGRESS "

# The bridge script rewrites secrets.json; never run two invocations for one user at once.
_user_locks = defaultdict(threading.Lock)
_user_locks_guard = threading.Lock()


def user_lock(username):
    with _user_locks_guard:
        return _user_locks[username]


def _run_google_script(args, timeout=120, on_progress=None):
    cmd = [PYTHON_BIN, str(SCRIPT_PATH), *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    out_chunks, err_lines = [], []

    def read_stdout():
        out_chunks.append(proc.stdout.read())

    def read_stderr():
        for line in proc.stderr:
            if line.startswith(PROGRESS_PREFIX):
                if on_progress:
                    try:
                        on_progress(json.loads(line[len(PROGRESS_PREFIX):]))
                    except Exception:
                        pass  # progress is best effort
                continue
            err_lines.append(line)

    readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
    for reader in readers:
        reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError(f"Google script timed out after {timeout} s")
    finally:
        for reader in readers:
            reader.join(timeout=5)

    stdout = "".join(out_chunks).strip()
    stderr = "".join(err_lines).strip()
    if proc.returncode != 0:
        raise RuntimeError(stderr or stdout or "google script failed")
    if not stdout:
        return {"ok": True, "raw_output": ""}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": True, "raw_output": stdout}


def list_google_targets(auth_file):
    return _run_google_script(["--auth-file", str(auth_file), "--json"], timeout=120)


def refresh_google_announcements(auth_file, force_upload=False):
    args = ["--auth-file", str(auth_file), "--json", "--refresh-announcements"]
    if force_upload:
        args.append("--force-upload")
    return _run_google_script(args, timeout=300)


def fetch_google_locations(auth_file, canonic_id=None, compound_name=None, timeout=45, subtags=1, on_progress=None):
    """Fetch one tracker (canonic id) or all subtags of a compound.

    Compound fetches keep going when a subtag fails; failed subtags are listed under
    "failures" in the result. Only if all fail does this raise.
    """
    args = ["--auth-file", str(auth_file), "--json", "--progress", "--timeout", str(timeout)]
    if canonic_id:
        args.extend(["--canonic-id", canonic_id])
    elif compound_name:
        args.extend(["--compound-name", compound_name, "--continue-on-error"])
    else:
        raise ValueError("canonic_id or compound_name is required")

    # Each subtag waits up to `timeout` for its FCM answer, plus request overhead.
    script_timeout = max(120, (timeout + 30) * max(1, subtags) + 60)
    for attempt in range(3):
        try:
            return _run_google_script(args, timeout=script_timeout, on_progress=on_progress)
        except RuntimeError as exc:
            # Retry only read-side Nova transient server errors; key uploads are not retried.
            if attempt == 2 or not re.search(r"Nova request failed \(50[0234]\)", str(exc)):
                raise
            time.sleep((5, 15)[attempt])
