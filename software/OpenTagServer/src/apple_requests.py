"""Haystack request pacing shared by manual and background Apple fetches (in-process)."""
import threading
import time
from contextlib import contextmanager
from email.utils import parsedate_to_datetime

import requests


class AppleRequestDeferred(RuntimeError):
    """Background request yielded to manual work or an active rate cooldown."""


class AppleRequestLimiter:
    """Spaces Haystack requests at least `interval` seconds apart and applies a
    cooldown after errors. Manual fetches wait for their turn and take priority;
    background requests give way instead of waiting."""

    def __init__(self, cfg):
        self.interval = max(1, int(cfg.get("request_interval_seconds", 60)))
        self.backoff = max(self.interval, int(cfg.get("error_backoff_seconds", 300)))
        self._cond = threading.Condition()
        self._next = 0.0
        self._manual = 0
        self._busy = False

    @contextmanager
    def manual_fetch(self, timeout_seconds=180):
        deadline = time.monotonic() + timeout_seconds
        with self._cond:
            self._manual += 1
        try:
            def request(endpoint, **kwargs):
                return self.post(endpoint, _manual=True, _deadline=deadline, **kwargs)
            yield request
        finally:
            with self._cond:
                self._manual -= 1
                self._cond.notify_all()

    def _acquire(self, manual, deadline):
        with self._cond:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise (RuntimeError if manual else AppleRequestDeferred)(
                        "Apple fetch wait limit reached; please try again later")
                if not manual and self._manual > 0:
                    raise AppleRequestDeferred("Manual Apple fetch has priority")
                if self._busy:
                    self._cond.wait(min(remaining, 1))
                    continue
                delay = self._next - time.time()
                if delay <= 0:
                    self._busy = True
                    self._next = time.time() + self.interval
                    return
                if not manual and delay > remaining:
                    raise AppleRequestDeferred("Apple request rate cooldown active")
                self._cond.wait(min(delay, remaining, 1))

    def post(self, endpoint, _manual=False, _deadline=None, **kwargs):
        timeout = int(kwargs.get("timeout", 30))
        deadline = _deadline if _deadline is not None else time.monotonic() + self.interval + timeout + 5
        self._acquire(_manual, deadline)
        try:
            try:
                response = requests.post(endpoint, **kwargs)
                response.raise_for_status()
            except requests.RequestException as exc:
                delay = self.backoff
                response = getattr(exc, "response", None)
                if response is not None:
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = max(delay, float(retry_after))
                    except (TypeError, ValueError):
                        try:
                            delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
                        except (TypeError, ValueError, OverflowError):
                            pass
                with self._cond:
                    self._next = time.time() + delay
                # Do not propagate request exceptions containing endpoints or credentials.
                raise RuntimeError("Apple request failed; shared backoff applied") from None
            return response
        finally:
            with self._cond:
                self._busy = False
                self._cond.notify_all()
