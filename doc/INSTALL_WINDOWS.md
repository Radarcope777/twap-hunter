# Install on Windows 10 / 11 (PowerShell)

All commands are PowerShell. Do not chain with `&&`. The live process on the development machine used port **9000** because Windows had an excluded range covering 8010 (`docs/WINDOWS_VPS_DEPLOYMENT_AUDIT.md`). If 8010 binds, use it; otherwise use 9000.

This project does **not** require Docker, WSL, or NSSM. `scripts/keep_alive.py` is a Windows watchdog that starts `.venv\Scripts\python.exe -m uvicorn ... --port 8010` and inhibits sleep. Use it only if you want that helper; it is not the primary entrypoint.

## Python

1. Install 64-bit Python 3.12 from python.org (or 3.14 if that is what you already run in a dedicated venv). Check “Add python.exe to PATH”.
2. In a **new** PowerShell:

```powershell
python --version
where.exe python
```

Use the project venv after it exists; do not mix with another global install.

## Unpack

Unzip to a folder without spaces if possible, e.g. `D:\twap-hunter`. Open PowerShell there:

```powershell
Set-Location D:\twap-hunter
```

## venv and dependencies

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If `py -3.12` is missing, `python -m venv .venv` is enough when `python --version` is 3.12+.

## `.env`

```powershell
Copy-Item .env.example .env
```

Edit `.env` in a text editor. Set `SITE_PASSWORD`. Optional `SESSION_SECRET`, optional `POLYMARKET_PRIVATE_KEY`. Never commit `.env`.

PowerShell encoding: save `.env` as UTF-8. If the terminal shows mojibake, run:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

## Time

Enable Windows Time (NTP). Stamps use the wall clock.

```powershell
Get-Service w32time
# if stopped:
Start-Service w32time
w32tm /resync
```

## Firewall

Outbound HTTPS/WSS must be allowed (Binance, Polymarket). Inbound TCP is only needed if another PC opens the UI. Binding `127.0.0.1` needs no inbound firewall rule.

If you bind `0.0.0.0` (`python -m backend`), Windows may prompt for a private-network allow on first listen.

## Start (paper)

From the project root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 9000
```

Or the production entry (listens on `0.0.0.0`, port `PORT` or 8010):

```powershell
$env:PORT = "9000"
.\.venv\Scripts\python.exe -m backend
```

Open `http://127.0.0.1:9000`. Log in if `SITE_PASSWORD` is set. Open `/bots`, Start a paper bot.

## Live

Same process. In the UI: `/real` → Add Account (private key) → Import paper bot → set LOT → Start.

Live orders require a Polymarket-allowed IP. A geoblock reject is from the CLOB, not from a hung process. Restarting the app does not change your region.

## Logs

Stdout in the PowerShell window. There is no app log file. `scripts/keep_alive.py` appends `data\watchdog.log` if you use that helper.

## Stop

In the uvicorn window: Ctrl+C. If it does not exit, stop the Python process that owns the port:

```powershell
Get-NetTCPConnection -LocalPort 9000 -State Listen | Select-Object OwningProcess
Stop-Process -Id <pid> -Force
```

Do not leave two uvicorn processes on the same port (`WinError 10048`).

## Autostart

No Task Scheduler XML is shipped. Compatible options:

1. **Task Scheduler** — Action: start program  
   Program: `D:\twap-hunter\.venv\Scripts\python.exe`  
   Arguments: `-m uvicorn backend.main:app --host 127.0.0.1 --port 9000`  
   Start in: `D:\twap-hunter`  
   Trigger: At log on. Run whether user is logged on or not only if the password is stored in the task.
2. **`scripts/keep_alive.py`** — Windows-only loop: restarts uvicorn on crash and if `GET http://127.0.0.1:8010/api/bots` fails 3 times. Hardcoded port **8010** and `Scripts\python.exe`. Change the script if you use 9000.

NSSM is not referenced in the repo.

## Permissions

- Write access to `data\` (SQLite + `.encryption_key`).
- Execution policy: calling `.\.venv\Scripts\python.exe` does not need `Activate.ps1`. If you activate the venv, CurrentUser RemoteSigned is enough.

## Typical errors

| Symptom | Cause |
| --- | --- |
| `WinError 10048` address already in use | Another uvicorn on that port. |
| Port 8010 bind fail | Windows excluded port range; use 9000. |
| `Terminal unavailable` / sandbox | Operator issue in Cursor; run uvicorn in a normal PowerShell. |
| Geoblock on BUY | Polymarket rejected the IP. Not fixed by restart. |
| Mixing `C:\Python314` and `.venv` | Live code needs `polymarket-client>=0.13` from the venv. |

## Backup

Copy `.env`, `data\goodfallen.db` (+ wal/shm), `data\.encryption_key`. Exclude `.venv`.
