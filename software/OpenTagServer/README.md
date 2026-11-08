# OpenTagServer
A self-hosted web app that tracks OpenTag tags through both Apple's and Google's networks. It is meant to be the day-to-day frontend for the tag hardware and firmware in this repo.

## Features
- **Overview:** a map of your devices with time ranges, trails, a per-report table.
- **Keys:** upload Apple `accessories.json` files (from [Macless Haystack](https://github.com/dchristl/macless-haystack)) and a Google `secrets.json` (from [OpenTagBridge](https://github.com/diminDDL/OpenTagBridge)).
- **Devices:** group tags (for example the Apple and Google halves of one tag) into a single device with a name, color, icon, and history retention.
- **Background fetching:** locations are fetched on a schedule and stored in a local SQLite history. Google announcement keys are refreshed automatically.
- **Multiple users:** each user only sees their own keys and devices. An admin account is used for management.
- Light and dark themes, and a mobile layout.

## Setup
1. Copy `data/config.toml.example` to `data/config.toml` and fill it in.
   - Point `haystack_endpoint` at your Macless Haystack server.
   - Set the admin password hash, generated with `openssl passwd -6`.
2. Start the server with `docker compose up -d --build`. It listens on port `8888` by default.
3. Sign in as the admin and create user accounts. Users then upload their key files on the **Keys** tab and group tags on the **Devices** tab.

Everything is stored in `data/`: the config, `opentag.db`, and the uploaded key material in `secrets/`.

Changes to `config.toml` take effect after a restart. Every option is explained in the example file.

### Environment variables
| Variable | Purpose |
|---|---|
| `OPENTAG_DATA_DIR` | Data directory (default `data/`) |
| `OPENTAG_CONFIG` / `OPENTAG_DB` | Override the config or database path |
| `OPENTAG_SECURE_COOKIES=1` | Mark session cookies as Secure (set this when served over HTTPS) |
| `OPENTAG_LOG_LEVEL` | Override `log_level` |
| `OPENTAG_DISABLE_WORKERS=1` | Run without background fetching |

## Development
```sh
pip install -r src/requirements.txt
cd tests && python -m unittest
```
The tests use a temporary database and mocked HTTP.

## Limitations
- **Not built for the public internet.** Basic protections are in place (hashed passwords, session cookies), but the server is meant to run on a local network or behind a VPN.
- **Key material lives on the server.** Anyone with access to `data/secrets/` can locate the tags, so protect that directory.
