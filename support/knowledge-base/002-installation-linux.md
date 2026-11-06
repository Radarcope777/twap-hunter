# 002 Installation Linux

Python 3.12. From project root:

```
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# set SITE_PASSWORD
.venv/bin/python -m backend
```

Default bind `0.0.0.0:8010` (`PORT` overrides). Auth off if `SITE_PASSWORD` empty.

No shipped systemd unit; wrap `python -m backend` if needed. `scripts/keep_alive.py` is Windows-only.

Outbound WSS/HTTPS to Binance and Polymarket required. NTP recommended.

Docker optional: `Dockerfile` `CMD python -u -m backend`, `PYTHONPATH=/app`, expose 8080.
