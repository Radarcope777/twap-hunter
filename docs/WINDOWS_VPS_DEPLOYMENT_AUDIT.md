# Windows VPS Deployment Environment Audit

Audit date: 2026-10-12. Read-only. Nothing was installed, restarted, committed, or changed in settings.

The bot that is running on this machine is paper Bot B (`b_384a1650`, strategy `twap_lockin`). The process was not restarted for this audit.

---

## 1. Operating system

| Item | Current machine | Required on VPS |
| --- | --- | --- |
| Edition | Windows 10 Enterprise LTSC 2021 (`EditionID` EnterpriseS) | Windows 10 or 11 x64. Same build is not required. |
| Version | 22H2, build 19045, UBR 7291 | Any current 64-bit Windows that can run Python 3.14. |
| Architecture | AMD64 / 64-bit | x64. ARM is not what this project is running on. |
| PowerShell | 5.1.19041.7291 | Windows PowerShell 5.1 is enough. PowerShell 7 is not required. |
| Timezone | Russian Standard Time (UTC+3) | Any timezone. Trade stamps use the machine wall clock. Keep the VPS clock correct; the zone does not have to be UTC+3. |
| Time sync | Windows Time service is not running (`w32tm /query /status` → 0x80070426, service not started) | Enable Windows Time (or another NTP client) on the VPS. Wall-clock stamps are used for trades. Polymarket time inside the bot is wall clock minus the RTDS clock offset, so a wrong PC clock still shifts stored timestamps. |

Execution policy on this PC:

- Process: Bypass
- CurrentUser: RemoteSigned
- Machine / User policy: Undefined

REQUIRED: none, if startup calls `.venv\Scripts\python.exe` directly.

CURRENT MACHINE ONLY: CurrentUser RemoteSigned. That only matters if someone runs `Activate.ps1`. Direct `python.exe` does not need a policy change.

System prerequisites the project actually uses: outbound HTTPS and WSS. No Windows service, scheduled task, or inbound port is required for paper trading. An inbound TCP port is required only if the dashboard is opened from another machine. This PC binds `127.0.0.1:9000` because the default port 8010 sits inside a Windows excluded range on this PC (7912–8011). That exclusion is current-machine only. On a VPS, 8010 is fine if it is free.

---

## 2. Python environment

```
Python required/recommended version: 3.14.x, 64-bit (match 3.14.5)
Python currently running: 3.14.5 (tags/v3.14.5:5607950, May 10 2026, MSC v.1944 64 bit AMD64)
venv creation command: py -3.14 -m venv .venv
venv activation command: .\.venv\Scripts\Activate.ps1
dependency install command: .\.venv\Scripts\python.exe -m pip install -r requirements.txt
backend start command: .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 9000
```

- Executable used by the live backend: `D:\TEST\twap-hunter\.venv\Scripts\python.exe`
- Launcher: the venv `python.exe` via `python -m uvicorn`. Not `py`, and not `run.py`.
- venv type: stdlib `venv`
- venv location: `D:\TEST\twap-hunter\.venv`
- pip inside that venv: 26.2.1
- Working directory required at start: repository root (`D:\TEST\twap-hunter` here), because imports are `backend.*` and the default data directory is `<repo>\data`.

Code defaults, which this process does not use: `backend/__main__.py` and `run.py` listen on `0.0.0.0` and `PORT` or **8010**. The live command overrides that to `127.0.0.1:9000`.

Authoritative dependency file: `requirements.txt` at the repo root. `backend/requirements.txt` is the same list. There is no `pyproject.toml`, Poetry, Pipfile, or `setup.py`.

Declared packages:

```
fastapi>=0.115
uvicorn[standard]>=0.30
websockets>=13.0
httpx>=0.27
tzdata>=2024.1
eth-account>=0.13
cryptography>=43.0
py-clob-client>=0.34
py-clob-client-v2>=1.1.0
```

Do not copy the global `C:\Python314` install. Another app on this PC uses it. The VPS only needs a 3.14 venv plus `requirements.txt`.

Packages were not upgraded during this audit.

---

## 3. Node / frontend

The UI is static HTML/JS under `frontend/`. FastAPI mounts it with `StaticFiles` in `backend/main.py`. There is no `package.json`, lockfile, or frontend build.

```
Does the trading bot require Node/frontend to trade? NO

Can backend + bot operate without frontend? YES

Should Node.js be installed on VPS? NO
Reason: paper and live loops run inside the Python process. The dashboard is static files served by the same process. There is no Node build step.
```

---

## 4. Other software

```
Software: Git
Required: YES (to clone; not required after the app is running)
Current version: 2.54.0.windows.1
Recommended VPS version: current Git for Windows, 64-bit
Why it is required: clone https://github.com/Radaroscope777/twap-hunter.git and check out fix/ptb-twap
```

```
Software: Python 3.14 x64
Required: YES
Current version: 3.14.5 in the project venv
Recommended VPS version: 3.14.x 64-bit
Why it is required: the backend, paper bot, and SQLite access
```

```
Software: SQLite CLI
Required: NO
Current version: not required; Python sqlite3 is the stdlib
Recommended VPS version: none
Why it is required: schema is created by Storage() on startup
```

Not required just because a tool may exist on a PC: PostgreSQL, Redis, Docker, cloudflared, NSSM, Node.js, FFmpeg, a browser driver, Visual Studio Build Tools. `cryptography` and `eth-account` install from Windows wheels. OpenSSL is not a separate install. curl is optional for a health check; PowerShell `Invoke-WebRequest` is enough.

Proxifier on this PC is current-machine only. It is not in the repo. The VPS needs direct outbound access to the hosts in section 9. Do not copy this PC's proxy rules as a project requirement.

---

## 5. Database

- Engine: SQLite via Python `sqlite3`, WAL mode.
- Path: `D:\TEST\twap-hunter\data\twaphunter.db`
- Sidecars present: `twaphunter.db-wal` (about 24 MB, still being written), `twaphunter.db-shm`.
- This file holds bots, balances, trades, rounds, and the Phase 2A research tape.
- Bot B settings and balance live in the `bots` table (`data` JSON), not in git.
- Init: `Storage()` runs `CREATE TABLE IF NOT EXISTS` on startup. There is no separate migration tool.
- A fresh file can boot the app. It will not contain Bot B, the balance ~9882, the trade history, or the research tape.
- Copy the database only when preserving that state. Do not copy unrelated CSVs sitting in `data\`.

`data\.encryption_key` is not present on this machine. Paper Bot B does not need it. Real-account ciphertext, if any is added later, is unreadable without that Fernet key (`backend/accounts.py`, `data_dir() / ".encryption_key"`).

Do not file-copy the live DB while this process is writing the WAL. When a move is actually done, stop the source backend first or take a SQLite online backup. That step is not part of this audit.

---

## 6. Environment variables

`.env` exists. Names only (values were not printed):

- `SITE_PASSWORD` — required for dashboard login. Secret.

Also read by code, not set in this `.env`:

- `SESSION_SECRET` — optional; defaults to `SITE_PASSWORD`. Secret if set.
- `PORT` — optional; default 8010. This process does not use it because the port is on the uvicorn command line.
- `DATA_DIR` — optional; default `<repo>\data`. Health reports `data_dir_env: null`.
- `RAILWAY_ENVIRONMENT` — Railway only. Not used on a VPS.
- `FORCE_SECURE_COOKIES` — optional.

Polymarket private keys are not environment variables. They would be encrypted in SQLite. Binance market data is public; there is no Binance API key in env.

---

## 7. What is running now

Command (cwd `D:\TEST\twap-hunter`), started 2026-10-08 14:45:56:

```
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 9000
```

Health at audit time: `{"ok":true,"auth":true,"ready":true,"bots":5}`.

One process serves API, paper bots, and static UI. It is not a Windows service. After a reboot it does not start by itself. Bots whose saved status is `running` are loaded from SQLite when the backend starts, so Bot B comes back with the backend. No second terminal and no Node process.

---

## 8. Bot B at audit time

Read-only SQLite, 2026-10-12:

| Field | Value |
| --- | --- |
| id | `b_384a1650` |
| name | BTC TWAp LOCKIN |
| strategy | `twap_lockin` |
| status | `running` |
| mode | paper (this id is a paper bot; the live path is `real_bots.py`) |
| position | none |
| balance | 9882.06 |
| entry_window_s | 80 |
| min_margin_bps | 2.5 |
| max_entry_cents | 87 |
| order_usd | 100 |

These four parameters were changed on this machine on 2026-10-09 and stored in SQLite. They are not in the Git commit. A clone without the database will not have them.

---

## 9. External connections

No credentials in code. Outbound only:

- `https://gamma-api.polymarket.com`
- `https://clob.polymarket.com`
- `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- `wss://ws-live-data.polymarket.com` (Chainlink and official TWAP)
- `https://data-api.polymarket.com`
- `wss://stream.binance.com:9443` (spot trades)
- `https://api.binance.com/api/v3/klines`
- `https://fapi.binance.com` and `wss://fstream.binance.com` exist for futures assets, not the BTC spot path

Git remotes, for clone only:

- `origin` `https://github.com/Radaroscope777/twap-hunter.git` (fetch and push)
- `upstream` `https://github.com/funstersland/twap-hunter.git` (fetch and push)

Use `origin`.

---

## 10. Startup after a clean reboot

Not automatic. Manual, from the repo root:

```
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 9000
```

That starts the backend, the static UI, and every paper bot saved as `running`, including Bot B. Research recording starts only if the uncommitted research hooks are present. The GitHub commit does not include those hooks. Paper trading does not need them.

---

## 11. Git working tree

Branch `fix/ptb-twap`. Tracking line has no ahead/behind against `origin/fix/ptb-twap`. Nothing is staged.

Dirty tracked files:

- `backend/engine/bots.py` (+104 / −3 lines in the combined diff)
- `backend/engine/pipeline.py`
- `backend/storage.py`

Untracked:

- `backend/engine/research.py`
- `backend/research/` (offline replay; not imported by the live loop)
- `docs/` (including this audit)
- `research_output/`
- `scripts/phase2b1_replay.py`
- `scripts/research_quality.py`
- `tests/`

`.gitignore` excludes `.venv/`, `__pycache__/`, `*.pyc`, `data/`, `.env`, `.claude/launch.json`. `.env` and the database do not appear in `git status`. `docs/` and `research_output/` are not ignored; they are simply untracked. Do not commit them as part of deployment.

File write times vs process start (2026-10-08 14:45:56):

- `storage.py` 14:20:57
- `pipeline.py` 14:22:08
- `research.py` 14:24:44
- `bots.py` 14:24:48

The running process started after those writes, so it loaded this working tree, including the research hooks.

The uncommitted diff is research instrumentation (event hooks and research tables). It does not change Bot B's entry gates. Those gates in the committed file are the ones the paper loop uses, plus the extra research calls in the working tree.

---

## 12. GitHub and corrected TWAP

```
Corrected TWAP commit: f249b464e18ac3db21fc5231e5684b535e74f9b9
Branch: fix/ptb-twap
Present on GitHub: YES
Current HEAD contains corrected TWAP: YES
```

Commit message: `Use official RTDS 60s TWAP for live TWAP and next-window PTB.`
Date: 2026-09-11 13:03:13 +0300.

`git ls-remote origin refs/heads/fix/ptb-twap` returned the same SHA. Local `origin/fix/ptb-twap` is the same SHA. HEAD is that commit. `git log -S official_twap60_carry` points at this commit only.

```
CURRENT WORKING TREE  = HEAD + uncommitted research hooks in bots.py, pipeline.py, storage.py + untracked backend/engine/research.py
CURRENT HEAD          = f249b464e18ac3db21fc5231e5684b535e74f9b9
REMOTE BRANCH         = origin/fix/ptb-twap = same SHA
CORRECTED TWAP COMMIT = same SHA
```

```
If I clone the current GitHub repository/branch onto a clean VPS right now,
will I get the same source code that the currently running bot uses?

NOT FULLY

Missing from the clone, present in the running process:
- uncommitted edits in backend/engine/bots.py
- uncommitted edits in backend/engine/pipeline.py
- uncommitted edits in backend/storage.py
- untracked backend/engine/research.py
```

Those four are observational research. They are not required for paper trading. Replay, tests, docs, and `research_output/` are not part of the running trading loop.

Do not push them.

---

## 13. Runtime files git clone will not provide

```
.env
MUST COPY (or recreate with a new SITE_PASSWORD; do not paste the value into git or into this report)

data\twaphunter.db (+ wal/shm, or a SQLite backup taken when you actually migrate)
MUST COPY if Bot B, balance, trades, and research history must survive
MUST RECREATE (empty, via app startup) if a new paper bot is acceptable

data\.encryption_key
NOT PRESENT on this machine
MUST COPY later only if real accounts are encrypted with it

Bot B parameters 80 / 2.5 / 87 / 100
MUST COPY via the database, or MUST RECREATE in the UI after first launch
They are not in the Git commit.

research tape inside the same SQLite file
OPTIONAL/HISTORY ONLY for trading
MUST COPY if Phase 2A history must move with the bot

backend/engine/research.py and the three uncommitted diffs
OPTIONAL/HISTORY ONLY for paper trading
MUST COPY only if the VPS must keep recording the research tape the same way

backend/research/, tests, scripts, docs, research_output
OPTIONAL/HISTORY ONLY
not required to trade

logs, data\*.csv, data\_stats_*, data\_export_bot_csv.py
DO NOT COPY

.venv
DO NOT COPY
recreate with python -m venv and pip install -r requirements.txt
```

---

## 14. Live trading readiness (inspection only)

Live mode was not enabled. Bot B does not use this path.

```
Paper executor present: YES
Live executor present: YES

Real order submission implementation:
backend/engine/real_bots.py RealBotManager.buy → backend/engine/clob_executor.py place_market_order (FOK, chain 137, py-clob-client or v2 for sig type 3)

Wallet/signing implementation:
backend/accounts.py encrypt_key / decrypt_key
backend/engine/clob_executor.py client build and CLOB auth discovery

Order status/fill handling:
backend/engine/real_bots.py _clob_fill
0 filled shares → no position

Partial fill handling:
PARTIAL (FOK; position uses the share count the client reports)

Order rejection handling:
YES (exception → last_exec_error, no position)

Retry logic:
PARTIAL (feed reconnect backoff; flatten retry every 8s; no order-submit retry loop)

Cancel logic:
NO (no order-cancel call; only in-process task cancel)

Price re-check immediately before submission:
YES (ask is read immediately before place_market_order)

max_entry_cents re-check before real submission:
YES (ask > max_entry_cents rejects)

Protection against duplicate real orders:
PARTIAL (in-memory position and per-window entry counters; no exchange idempotency key)

Protection against trading same round twice:
YES for the paper twap_lockin loop and the real loop (entries reset on window change; a second entry in the same window returns; position gate)
```

---

## 15. Windows VPS software checklist

```
WINDOWS VPS REQUIRED SOFTWARE

1. Git for Windows
   Version: current 64-bit (this PC has 2.54.0)
   Required because: clone origin and check out fix/ptb-twap

2. Python
   Version: 3.14.x 64-bit (this process is 3.14.5)
   Required because: backend, paper bot, SQLite

3. Node.js
   Version: none
   Required: NO
   Reason: static UI served by FastAPI
```

Also enable Windows Time. No database server, Redis, Docker, or Node.

Proposed order (do not run it on this PC):

```
1. Install Git and Python 3.14 x64. Enable Windows Time.
2. Clone origin, checkout fix/ptb-twap at f249b464e18ac3db21fc5231e5684b535e74f9b9.
3. Create .venv and pip install -r requirements.txt.
4. Place .env with SITE_PASSWORD only.
5. Place a SQLite backup of data\twaphunter.db if Bot B state must be kept. Otherwise let startup create an empty data directory.
6. Start uvicorn on 127.0.0.1 and a free port, paper only.
7. Check /health, Bot B status, TWAP source, and that Polymarket/Binance feeds connect.
```

---

## 16. Clean VPS commands (do not execute here)

PowerShell. First launch stays paper. No secrets. No live-enable step.

```powershell
git --version
py -3.14 --version

git clone https://github.com/Radaroscope777/twap-hunter.git C:\twap-hunter
cd C:\twap-hunter
git checkout fix/ptb-twap
git rev-parse HEAD
# expect f249b464e18ac3db21fc5231e5684b535e74f9b9

py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Create .env yourself. One line, no quotes in the file:
# SITE_PASSWORD=<choose on the VPS>
# Do not commit .env.

New-Item -ItemType Directory -Force data | Out-Null
# To keep Bot B: copy a SQLite backup into data\twaphunter.db
# after the source backend has been stopped, or from an online .backup.
# Do not copy .venv. Do not copy logs or data\*.csv.

.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 9000
```

In another window:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:9000/health
```

Then log in with `POST /api/login` JSON `{"password":"<SITE_PASSWORD>"}` and read `GET /api/bots/b_384a1650`. Confirm `status` running, strategy `twap_lockin`, and params `entry_window_s=80`, `min_margin_bps=2.5`, `max_entry_cents=87`, `order_usd=100` if the database was copied. If the database was not copied, create the paper bot in the UI with those values. Do not switch it to live.

TWAP check: the commit above is the official RTDS `crypto_prices_twap_sixty` path (`official_twap60_carry`). Confirm the pipeline shows a TWAP value and a non-exchange PTB source after the first windows. Binance and Polymarket are connected when the pipeline leaves the startup state and `/health` stays `ready: true`. No frontend install and no frontend build.

---

## 17. Final report

```
WINDOWS VPS DEPLOYMENT AUDIT

Current project:
Repository: https://github.com/Radaroscope777/twap-hunter.git
Branch: fix/ptb-twap
Current commit: f249b464e18ac3db21fc5231e5684b535e74f9b9
Corrected TWAP commit: f249b464e18ac3db21fc5231e5684b535e74f9b9
Corrected TWAP present on GitHub: YES
Clone reproduces current running source: NOT FULLY

Required software:
- Git for Windows (clone only)
- Python 3.14.x 64-bit
- Windows Time (NTP)

Python: 3.14.5 in .venv; install from requirements.txt
Node: not required
Package manager: pip (requirements.txt). No Node package manager.
Database: SQLite file data/twaphunter.db (Python sqlite3, created on startup)

Files that must be transferred separately:
- .env (SITE_PASSWORD only; never commit it)
- data/twaphunter.db if Bot B, balance, and history must be kept
- optional: uncommitted research hooks (bots.py, pipeline.py, storage.py, backend/engine/research.py) only if research recording must match this PC

Bot B:
Mode: paper
Status: running
entry_window_s: 80
min_margin_bps: 2.5
max_entry_cents: 87
order_usd: 100

Production entrypoint: python -m uvicorn backend.main:app
Backend command: .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 9000
Frontend required for trading: NO

External connections:
- gamma-api.polymarket.com
- clob.polymarket.com
- wss://ws-subscriptions-clob.polymarket.com/ws/market
- wss://ws-live-data.polymarket.com
- data-api.polymarket.com
- stream.binance.com:9443
- api.binance.com

Live execution already implemented: YES
Main live-trading gaps:
- no order-cancel API
- duplicate protection is in-memory only (no exchange idempotency key)
- no submit-retry loop (FOK reject is recorded and dropped)
- partial fills follow the reported share count only
- data/.encryption_key is absent on this PC
- Bot B is paper and must not be switched to live in the first VPS launch

SAFE VPS DEPLOYMENT SOURCE:
Repository: https://github.com/Radaroscope777/twap-hunter.git
Branch: fix/ptb-twap
Commit: f249b464e18ac3db21fc5231e5684b535e74f9b9

CAN WE DEPLOY THIS VERSION TO WINDOWS VPS IN PAPER MODE NOW?
YES

NEXT STEP:
1. Install Git and Python 3.14 x64 on the VPS and enable Windows Time.
2. Clone origin, checkout fix/ptb-twap, confirm SHA f249b464e18ac3db21fc5231e5684b535e74f9b9.
3. Create .venv and install requirements.txt. Do not copy this PC's .venv.
4. Create .env with SITE_PASSWORD. Do not commit it.
5. If Bot B must keep 80 / 2.5 / 87 / 100 and its balance, copy a SQLite backup of data/twaphunter.db after the source process is stopped, or take an online backup. Do not copy that file as part of this audit.
6. Start uvicorn on 127.0.0.1 and a free port. Leave every bot in paper.
7. Check /health, Bot B, official TWAP, and Polymarket/Binance connectivity.
8. Do not enable live trading.
```
