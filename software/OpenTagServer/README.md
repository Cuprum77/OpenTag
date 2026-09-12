# OpenTagServer
This is a web server that combines Apple and Google tag management, fetching, advertising, history, and more. It is intended to be used as the primary day-to-day frontend for anyone using the polyglot tag hardware/firmware presented in this repo.

# Installation
TODO

# Usage
TODO

## Limitations
- Vibe based security. In general this is intended for deployments behind a VPN tunnel or on a local network only. Thus, while basic security measures are in place, this is **NEVER INTENDED FOR PUBLIC INTERNET DEPLOYMENTS!**.
- Config based user management. Users are currently only managed using the `config.toml` file. This is intended for simple few user deployments where the server administrator is intended to manage the users explicitly.
- Server side secret storage. The key material to decrypt/announce and fetch tag locations is stored entirely on the server. Meaning it is up to the server administrator to prevent unauthorized access to the servers `secrets` directory containing the key material. 
## Apple bulk polling and gap recovery

Automatic Apple fetching now combines bulk updates with a slow singleton sweep.
The sweep queries every uploaded Apple key in turn, saving each user's position
in Redis so it resumes after server restarts. Existing observations are retained;
repeat observations are deduplicated by canonical key and encrypted payload.
Singleton status is available as `apple_singleton` from the fetch-status API and
does not replace the most recent bulk status. Apple timestamps are stored as UTC.

In `[config]` in `data/config.toml` (also documented in the example):

- `apple_auto_query_interval_min = 600`: bulk polling interval; zero disables bulk.
  The previous misspelling `appple_auto_query_interval_min` remains supported.
- `apple_singleton_sweep_enabled = true`: enables background gap recovery.
- `apple_singleton_interval_seconds = 60`: minimum singleton interval per user.
- `apple_request_interval_seconds = 60`: shared minimum spacing between HTTP
  request starts, including manual fetches, every bulk batch, and singletons.
  Requests to the same endpoint are serialized through Redis across users and
  Gunicorn workers. Manual requests can therefore take longer while waiting.
- `apple_error_backoff_seconds = 300`: shared HTTP/network-error cooldown.
  A longer `Retry-After` is honored; no immediate automatic HTTP retries occur.
- `apple_bulk_batch_size = 200`: keys in each bulk/manual HTTP request.
- `apple_query_days = 7`: lookback for automatic bulk and singleton retrieval.

`local_history = false` disables both automatic Apple modes. Disabling bulk
polling leaves the singleton sweep enabled unless explicitly disabled. Failed
singleton fetches keep their cursor for a later retry; empty successful responses
advance the sweep without clearing stored history. Due bulk work takes precedence
on each user's worker tick. At the defaults a 255-key sweep takes approximately
4¼ hours plus bulk activity, other users, and any error backoff.

This recovers omissions demonstrated in multi-key requests. It does not guarantee
complete history when the upstream also limits a single key's response. History
continues to use the existing 5,000-event buffer and configured age retention.
Redis persistence is required to retain sweep positions across Redis restarts.
Apply configuration changes by restarting the server. Regression test instructions
are in `tests/README.md`; tests use an isolated database and mocked HTTP.

### Manual fetch waiting and startup recovery

“Fetch All Selected” now submits the selected Apple entries (including source-file
identity), gives manual Apple requests priority over background work, and displays
which provider it is fetching. Google selections still run sequentially after Apple.
The rate limiter releases its lock while waiting for the next permitted request.
Background Apple jobs yield while manual work is pending or cooling down, leaving
those jobs due for a later tick. Shared rate limits and error backoff remain active.

`apple_manual_wait_timeout_seconds = 180` bounds the whole manual Apple operation's
wait for request slots (maximum 180 seconds). An in-flight HTTP request can add its
HTTP timeout. The dashboard times out Apple POSTs after four minutes, other JSON
POSTs after seven minutes, and status/history GETs after 20 seconds; timeout errors
restore the button. A browser timeout does not cancel already-running server work.

Google automatic work uses a per-user Redis lock shared across Gunicorn workers.
`google_refresh_settle_seconds = 15` defers automatic location fetching after a
successful announcement refresh. The scheduler checks every 15 seconds. Failed key
refreshes wait five minutes before another attempt and skip location fetching until
the refresh succeeds. Setting key refresh interval to zero now disables uploads.
Read-side Google Nova 500/502/503/504 errors receive at most two retries, after five
and fifteen seconds. Authentication errors and key uploads are not automatically
retried. Persistent upstream failures still surface as errors.

Compose waits for a successful Valkey health check. Expiry cleanup is atomic, so
simultaneous startup cleanup and location fetches cannot overwrite one another's
history or alerts. These fixes do not alter host `vm.overcommit_memory`; that Valkey
warning is separate from the Google HTTP 500 error.

Recreate the Compose services to apply the health check and reload the server;
reload the dashboard to load the updated JavaScript. Python tests cover request
priority, bounded lock waits, selected accessories, transient Google retries,
startup coordination/settling, and atomic cleanup. Dashboard tests cover network
timeouts and spinner restoration.

### Scheduled singleton passes

`apple_singleton_sweep_interval_hours = 24` pauses singleton scanning for N hours
after completing a full pass over the uploaded keys. Each pass keeps the existing
`apple_singleton_interval_seconds` per-key pacing and shared request rate limiter.
Container startup starts a new pass, or resumes an interrupted pass. All Gunicorn
workers share the same container-start identity and Redis schedule; worker respawns
do not restart the pass. Bulk polling continues during the singleton sleep.
Set the interval to `0` for continuous passes, or disable the sweep with
`apple_singleton_sweep_enabled = false`. Failed/deferred requests do not consume
the pending key. Keys added during a pass are included in the next pass.
