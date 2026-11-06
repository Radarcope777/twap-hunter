# 003 Installation Windows

PowerShell, project root. Port 8010 may be blocked by Windows excluded ranges; 9000 is used in this repo’s VPS audit.

```
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 9000
```

Do not use `&&`. Use the venv interpreter (`polymarket-client>=0.13`). Stop duplicate listeners (`WinError 10048`).

Optional watchdog: `scripts/keep_alive.py` (hardcoded 8010, `Scripts\python.exe`). No NSSM/WSL/Docker requirement.
