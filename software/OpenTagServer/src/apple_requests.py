"""Haystack request pacing.

All Apple work runs in the scheduler's single Apple lane, so requests never overlap;
this only decides *when* the next request may go out:

* a minimum gap since the previous request (5 s for manual/upload fetches, the
  configured `apple_request_interval_seconds` for automatic work), and
* an error backoff after a failed request, applied to everything (honours Retry-After).
"""
import threading
import time
from email.utils import parsedate_to_datetime

import requests


MANUAL_GAP_SECONDS = 5
ERROR_BACKOFF_SECONDS = 300


class AppleRequestLimiter:
    def __init__(self, cfg):
        self.interval = max(1, int(cfg.get("request_interval_seconds", 60)))
        self.backoff = max(MANUAL_GAP_SECONDS, int(cfg.get("error_backoff_seconds", ERROR_BACKOFF_SECONDS)))
        self._lock = threading.Lock()
        self._last = 0.0
        self._backoff_until = 0.0

    def spacing(self, manual):
        return MANUAL_GAP_SECONDS if manual else self.interval

    def ready_in(self, manual):
        """Seconds until a request of this kind may be sent (0 = now)."""
        with self._lock:
            due = max(self._last + self.spacing(manual), self._backoff_until)
        return max(0.0, due - time.time())

    def backoff_remaining(self):
        with self._lock:
            return max(0.0, self._backoff_until - time.time())

    def wait(self, manual, on_wait=None, stop_event=None):
        """Block until a request may be sent; `on_wait(seconds, reason)` is called while waiting."""
        while True:
            delay = self.ready_in(manual)
            if delay <= 0:
                return
            if on_wait:
                on_wait(delay, "backoff" if self.backoff_remaining() > 0 else "spacing")
            step = min(delay, 5)
            if stop_event is not None:
                if stop_event.wait(step):
                    raise RuntimeError("server is shutting down")
            else:
                time.sleep(step)

    def post(self, endpoint, **kwargs):
        with self._lock:
            self._last = time.time()
        try:
            response = requests.post(endpoint, **kwargs)
            response.raise_for_status()
            return response
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
            with self._lock:
                self._backoff_until = time.time() + delay
            # Do not propagate request exceptions: they can contain endpoints or credentials.
            status = f" (HTTP {response.status_code})" if response is not None else ""
            raise RuntimeError(f"Haystack request failed{status}; pausing Apple requests for {int(delay)} s") from None
