# Install on Linux (Ubuntu / Debian)

Commands match this repository: `python -m backend`, `requirements.txt`, `.env.example`. Do not invent extra system packages. The Docker image installs wheels only (`pip install --only-binary=:all: -r requirements.txt`); a normal venv can use the same requirements file with ordinary pip.

## Recommended OS and Python

- Ubuntu 22.04 or 24.04, or another Debian-like amd64 host.
- Python **3.12** (`runtime.txt` = `python-3.12`). The Dockerfile is `python:3.12-slim-bookworm`.

## System packages

On Ubuntu/Debian, Python 3.12 plus venv/pip is enough for the declared dependencies (FastAPI, uvicorn, httpx, websockets, cryptography, eth-account, polymarket-client). If `python3.12` is missing:

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
```

`python3.12-dev` is only needed if a wheel is missing and pip has to compile. Prefer wheels.

## Unpack or clone

```bash
cd /opt
# unpack the delivery zip, or:
# git clone <your-repo-url> twap-hunter
cd twap-hunter
```

Work from the **project root** (the folder that contains `backend/` and `frontend/`). Imports are `backend.*`. Default data dir is `<repo>/data`.

## Virtual environment and dependencies

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Authoritative list: `requirements.txt` at the repo root (`backend/requirements.txt` is the same list).

```
fastapi>=0.115
uvicorn[standard]>=0.30
websockets>=13.0
httpx>=0.27
tzdata>=2024.1
eth-account>=0.13
cryptography>=43.0
polymarket-client>=0.13
```

## `.env`

```bash
cp .env.example .env
```

Edit `.env`. Minimum for a password-protected dashboard:

```
SITE_PASSWORD=choose-a-long-password
```

Optional: `SESSION_SECRET` (defaults to `SITE_PASSWORD`). Optional: `POLYMARKET_PRIVATE_KEY` so PolyBolt 60s TWAP can authenticate if no stored account exists yet. Never commit `.env`.

If `SITE_PASSWORD` is empty, dashboard auth is **disabled** (`backend/auth.py`). For a VPS that is reachable from the internet, set a password.

## Time and timezone

Trade stamps use the machine wall clock. Polymarket time inside the bot is wall clock minus the feed clock offset. Enable NTP (systemd-timesyncd or chrony). The timezone does not have to be UTC; round labels use ET in the UI.

```bash
timedatectl status
sudo timedatectl set-ntp true
```

## Network

Outbound HTTPS/WSS required:

- `stream.binance.com:9443` (spot trades)
- `gamma-api.polymarket.com`
- `clob.polymarket.com` and `wss://ws-subscriptions-clob.polymarket.com`
- `wss://ws-live-v2.polymarket.com` (PolyBolt)
- `wss://ws-live-data.polymarket.com` (Copy Trader paper activity feed only)
- `data-api.polymarket.com`
- `relayer-v2.polymarket.com` (gasless redeem / deposit wallet)

Inbound: TCP **8010** (or `PORT`) only if you open the UI from another host. `python -m backend` binds **`0.0.0.0`**. For local-only access, use uvicorn `--host 127.0.0.1`.

Firewall example (optional, if the UI must be remote):

```bash
sudo ufw allow 8010/tcp
```

## Start

```bash
cd /opt/twap-hunter
.venv/bin/python -m backend
```

Default URL: `http://127.0.0.1:8010`. Override port:

```bash
PORT=9000 .venv/bin/python -m backend
```

Equivalent uvicorn (Windows-style host from `backend/main.py` comments; Linux production entry is `0.0.0.0` via `python -m backend`):

```bash
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010
```

## Paper check

1. Open `/`. Confirm an asset tab loads (XRP by default).
2. Open `/bots`. A seed paper bot **BTC Lock In Small Loss** may already be running on a fresh database.
3. Create or Start a paper bot. Activity log should show skips/signals; no CLOB POST.
4. `GET /health` should return `"ok": true`. `"storage.persistent"` is a Railway-volume check; on a normal Linux disk it is often `false` even though `./data` is writable.

## Logs

The process logs to stdout:

```
%(asctime)s %(levelname)-7s %(name)s: %(message)s
```

There is no separate log file in the app. Redirect if you want a file:

```bash
.venv/bin/python -m backend >> /var/log/twaphunter.log 2>&1
```

`scripts/keep_alive.py` writes `data/watchdog.log` but is a **Windows** helper (uses `Scripts\python.exe` and `ctypes.windll`). Do not use it on Linux.

## Stop

Foreground: Ctrl+C. The FastAPI lifespan stops real bots, paper bots, and pipelines.

## systemd (optional)

The repo does **not** ship a unit file. The confirmed entrypoint is `python -m backend` from the project root. An operator-supplied unit can wrap that. Example (create only if you need autostart; adjust user and paths):

```ini
[Unit]
Description=Twap Hunter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=twap
WorkingDirectory=/opt/twap-hunter
Environment=PORT=8010
EnvironmentFile=/opt/twap-hunter/.env
ExecStart=/opt/twap-hunter/.venv/bin/python -m backend
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`railway.toml` uses `restartPolicyType = "ON_FAILURE"` with 10 retries for container deploys; that is not systemd.

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now twaphunter
sudo journalctl -u twaphunter -f
```

## Update

1. Stop the service.
2. Replace code (git pull or unpack). Keep `.env` and `data/`.
3. `.venv/bin/python -m pip install -r requirements.txt`
4. Start again.

## Backup

Copy at least:

- `.env`
- `data/twaphunter.db` (and `-wal` / `-shm` if the process is stopped cleanly, copy those too)
- `data/.encryption_key` — without it, stored private keys cannot be decrypted.

Do not back up `.venv`.

## Typical errors

| Symptom | Cause |
| --- | --- |
| `No module named backend` | Not started from project root, or `PYTHONPATH` missing (Docker sets `PYTHONPATH=/app`). |
| `unauthorized` on `/api` | `SITE_PASSWORD` set; log in at `/login`. |
| Dashboard with no login | `SITE_PASSWORD` empty; auth disabled. |
| PolyBolt missing | No stored account and no `POLYMARKET_PRIVATE_KEY`. |
| Port in use | Change `PORT` or stop the other process. |
| `DATA_DIR ... ephemeral` on Railway | Mount a volume and set `DATA_DIR`. Local Linux `./data` is fine. |

Docker (optional, not required): `Dockerfile` exposes **8080** and runs `python -u -m backend`. Set `PORT` to match the platform (Railway injects `PORT`). Do not copy `.env` or `data/` into the image (see `.dockerignore`).
