# 004 Configuration

App env: `SITE_PASSWORD`, `SESSION_SECRET` (default site password), `POLYMARKET_PRIVATE_KEY` (PolyBolt if no stored account), optional builder trio `POLYMARKET_BUILDER_API_KEY|SECRET|PASSPHRASE`, `DATA_DIR`, `PORT` (default 8010), `FORCE_SECURE_COOKIES`, `RAILWAY_ENVIRONMENT`.

`.env` loaded only for keys not already in the environment (`backend/auth.py`).

Not used by the server: `POLYMARKET_API_KEY` / `SECRET` / `PASSPHRASE` (preflight script only). L2 creds are derived from the private key.

Timing and endpoints are in `backend/config.py`, not `.env`. Strategy params are per-bot JSON, clamped by `sanitize_params`.

Restart after `.env` changes. Paper settings gear updates params live; real UI LOT updates `order_usd` only.
