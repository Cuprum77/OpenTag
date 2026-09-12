"""Frequent bulk updates plus a resumable, low-rate singleton recovery sweep."""
import logging
import json
from pathlib import Path
import time

from apple_requests import AppleRequestDeferred
from fetch_apple import _build_key_map, apple_history_events, fetch_apple_locations
from storage import append_alert, append_history_events, read_user_accessories, set_fetch_status

logger = logging.getLogger(__name__)


def container_start_id():
    """Same ID for all Gunicorn workers, different after container restart."""
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    stat = Path('/proc/1/stat').read_text()
    start_ticks = stat.rsplit(')', 1)[1].split()[19]
    return boot + ':' + start_ticks


def run_apple_user(redis_client, runtime, username, request_limiter):
    """Perform due bulk work first, otherwise one singleton. Safe across workers."""
    if not runtime.get('local_history', True):
        return
    cfg = runtime.get('haystack', {})
    bulk_interval = runtime.get('apple_auto_query_interval_min', 600) * 60
    sweep_enabled = runtime.get('apple_singleton_sweep_enabled', True)
    if bulk_interval <= 0 and not sweep_enabled:
        return
    # Avoid duplicate sweeps/bulk jobs when multiple web workers start threads.
    lock = redis_client.lock('user:' + username + ':apple:worker', timeout=3600)
    if not lock.acquire(blocking=False):
        return
    try:
        accessories = read_user_accessories(username)
        if not accessories:
            return
        prefix = 'user:' + username + ':meta:'
        now = time.time()
        last_bulk = float(redis_client.get(prefix + 'last_apple_fetch') or 0)
        if bulk_interval > 0 and now - last_bulk >= bulk_interval:
            kind = 'auto_fetch_location'
            # Record attempts too, so a failed bulk job cannot starve the sweep.
            redis_client.set(prefix + 'last_apple_fetch', str(now))
            rows = accessories
        elif sweep_enabled:
            interval = max(1, runtime.get('apple_singleton_interval_seconds', 60))
            last_single = float(redis_client.get(prefix + 'last_apple_singleton') or 0)
            if now - last_single < interval:
                return
            keys, sources = _build_key_map(accessories)
            ids = list(keys)
            if not ids:
                return
            sweep_hours = max(0, runtime.get('apple_singleton_sweep_interval_hours', 0))
            sweep_state = None
            if sweep_hours > 0:
                state_key = prefix + 'apple_singleton_sweep'
                raw_state = redis_client.get(state_key)
                sweep_state = json.loads(raw_state) if raw_state else {}
                startup = runtime.get('apple_worker_start_id', 'default')
                if sweep_state.get('startup') != startup:
                    # Resume an interrupted pass; otherwise start a pass at startup.
                    sweep_state['startup'] = startup
                    sweep_state['next_due'] = 0
                    redis_client.set(state_key, json.dumps(sweep_state))
                if not sweep_state.get('pending'):
                    if now < sweep_state.get('next_due', 0):
                        return
                    sweep_state['pending'] = ids.copy()
                    redis_client.set(state_key, json.dumps(sweep_state))
                sweep_state['pending'] = [key for key in sweep_state['pending'] if key in keys]
                if not sweep_state['pending']:
                    sweep_state['next_due'] = now + sweep_hours * 3600
                    redis_client.set(state_key, json.dumps(sweep_state))
                    return
                selected = sweep_state['pending'][0]
            else:
                previous = redis_client.get(prefix + 'apple_singleton_cursor')
                if isinstance(previous, bytes):
                    previous = previous.decode()
                index = (ids.index(previous) + 1) % len(ids) if previous in keys else 0
                selected = ids[index]
            source = sources[selected]
            rows = [{'privateKey': keys[selected], 'name': source['tag'],
                     'source_file': source['file']}]
            kind = 'singleton_fetch_location'
            redis_client.set(prefix + 'last_apple_singleton', str(now))
        else:
            return
        try:
            payload = fetch_apple_locations(
                cfg, rows, days=runtime.get('apple_query_days', 7), timeout=30,
                batch_size=runtime.get('apple_bulk_batch_size', 200),
                _request_callback=request_limiter.post)
            # Persist history before advancing the cursor. Empty results do not clear it.
            append_history_events(redis_client, username, apple_history_events(payload))
            if kind == 'singleton_fetch_location':
                redis_client.set(prefix + 'apple_singleton_cursor', selected)
                if sweep_state is not None:
                    sweep_state['pending'].pop(0)
                    if not sweep_state['pending']:
                        sweep_state['next_due'] = time.time() + sweep_hours * 3600
                    redis_client.set(state_key, json.dumps(sweep_state))
            provider_status = 'apple_singleton' if kind == 'singleton_fetch_location' else 'apple'
            set_fetch_status(redis_client, username, provider_status, {
                'provider': 'apple', 'ok': True, 'kind': kind,
                'timestamp_unix': int(time.time()), 'details': payload})
        except AppleRequestDeferred:
            # No HTTP attempt: leave work due rather than postponing bulk for hours.
            if kind == 'auto_fetch_location':
                redis_client.set(prefix + 'last_apple_fetch', str(last_bulk))
            else:
                redis_client.set(prefix + 'last_apple_singleton', str(last_single))
        except Exception as exc:
            # Keep failed singleton cursor for a later, rate-limited retry.
            logger.warning('Apple %s failed for user %s (%s)', kind, username, type(exc).__name__)
            append_alert(redis_client, username, {
                'provider': 'apple', 'error': 'Apple fetch failed; will retry later',
                'type': 'auto_fetch_error', 'target': kind,
                'timestamp_unix': int(time.time())})
    finally:
        lock.release()


def apple_auto_fetch_loop(app):
    app.config['OPENTAG']['apple_worker_start_id'] = container_start_id()
    logger.info('Apple bulk and singleton worker started')
    while True:
        try:
            with app.app_context():
                runtime = app.config['OPENTAG']
                for username in runtime.get('users', {}):
                    try:
                        run_apple_user(app.config['REDIS'], runtime, username,
                                       app.config['APPLE_REQUEST_LIMITER'])
                    except Exception as exc:
                        logger.warning('Apple worker failed for user %s (%s)', username, type(exc).__name__)
        except Exception as exc:
            logger.warning('Apple worker loop failed (%s)', type(exc).__name__)
        time.sleep(5)
