@echo off
cd /d "%~dp0"
echo Starting GOODFALLEN keep-alive watchdog...
start "GOODFALLEN KeepAlive" /MIN ".venv\Scripts\python.exe" -u scripts\keep_alive.py
echo Watchdog launched. Server will restart itself if it dies.
echo Leave this PC awake (sleep is inhibited by the watchdog).
exit /b 0
