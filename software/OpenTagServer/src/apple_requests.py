"""Redis-coordinated Apple HTTP requests shared by manual and background fetches."""
import hashlib
import uuid
from contextlib import contextmanager
import time
from email.utils import parsedate_to_datetime

import requests


class AppleRequestDeferred(RuntimeError):
    """Background request yielded to manual work or an active rate cooldown."""


class AppleRequestLimiter:
    def __init__(self, redis_client, cfg):
        self.redis = redis_client
        self.interval = max(1, int(cfg.get('request_interval_seconds', 60)))
        self.backoff = max(self.interval, int(cfg.get('error_backoff_seconds', 300)))
        endpoint_hash = hashlib.sha256(cfg.get('endpoint', '').encode()).hexdigest()
        self.prefix = 'apple:requests:' + endpoint_hash

    @contextmanager
    def manual_fetch(self, timeout_seconds=180):
        token = uuid.uuid4().hex
        deadline = time.monotonic() + timeout_seconds
        priority_key = self.prefix + ':manual'
        self.redis.zadd(priority_key, {token: time.time() + timeout_seconds})
        try:
            def request(endpoint, **kwargs):
                return self.post(endpoint, _manual=True, _deadline=deadline, **kwargs)
            yield request
        finally:
            self.redis.zrem(priority_key, token)

    def post(self, endpoint, _manual=False, _deadline=None, **kwargs):
        # Wait outside the lock. Manual work has priority; background work yields.
        timeout = int(kwargs.get('timeout', 30))
        deadline = _deadline if _deadline is not None else time.monotonic() + self.interval + timeout + 5
        lock = self.redis.lock(self.prefix + ':lock', timeout=max(300, timeout * 4))
        while True:
            if time.monotonic() >= deadline:
                error = RuntimeError if _manual else AppleRequestDeferred
                raise error('Apple fetch wait limit reached; please try again later')
            if lock.acquire(blocking=False):
                try:
                    now = time.time()
                    self.redis.zremrangebyscore(self.prefix + ':manual', '-inf', now)
                    priority = self.redis.zcard(self.prefix + ':manual') > 0
                    delay = float(self.redis.get(self.prefix + ':next') or 0) - now
                    if not _manual and priority:
                        raise AppleRequestDeferred('Manual Apple fetch has priority')
                    if delay <= 0:
                        break
                    if not _manual and delay > deadline - time.monotonic():
                        raise AppleRequestDeferred('Apple request rate cooldown active')
                except Exception:
                    lock.release()
                    raise
                lock.release()
                time.sleep(min(max(delay, .05), 1, max(.001, deadline - time.monotonic())))
            else:
                time.sleep(min(.1, max(.001, deadline - time.monotonic())))
        try:
            self.redis.set(self.prefix + ':next', str(time.time() + self.interval))
            try:
                response = requests.post(endpoint, **kwargs)
                response.raise_for_status()
            except requests.RequestException as exc:
                delay = self.backoff
                response = getattr(exc, 'response', None)
                if response is not None:
                    retry_after = response.headers.get('Retry-After', '')
                    try:
                        delay = max(delay, float(retry_after))
                    except (TypeError, ValueError):
                        try:
                            delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
                        except (TypeError, ValueError, OverflowError):
                            pass
                self.redis.set(self.prefix + ':next', str(time.time() + delay))
                # Do not propagate request exceptions containing endpoints or credentials.
                raise RuntimeError('Apple request failed; shared backoff applied') from None
            return response
        finally:
            lock.release()
