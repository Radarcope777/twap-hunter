# Configuration

Sources: `.env` (loaded by `backend/auth.py` if the key is not already in the environment), process environment, and in-code `backend/config.py` / strategy params. There is **no** YAML/TOML trading config besides `railway.toml` / `mise.toml` (deploy/tooling only).

Restart the process after changing `.env`. Strategy params can be changed on a **paper** bot without restart (settings gear → Save). On a **real** card, only `order_usd` (LOT) is posted from the UI; other params stay as imported unless you call `POST /api/real/bots/{id}/update`.

## Environment variables

| Variable | Required | Default | Allowed values | Meaning | Example | Risk |
| --- | --- | --- | --- | --- | --- | --- |
| `SITE_PASSWORD` | No (auth off if empty) | empty | any string | Dashboard login. Compared with `hmac.compare_digest`. | (set a long password) | Empty = open UI. Weak password = stolen session. |
| `SESSION_SECRET` | No | `SITE_PASSWORD` | any string | HMAC secret for cookie `twap_session`. | random 32+ chars | If unset, changing the site password invalidates cookies. |
| `POLYMARKET_PRIVATE_KEY` | No | empty | 64 hex chars, optional `0x` | Derives CLOB L2 creds for **PolyBolt TWAP** when no stored account is used. | (never commit) | Full wallet control if leaked. |
| `POLYMARKET_BUILDER_API_KEY` | No | empty | SDK builder key | With secret + passphrase: gasless redeem without auto-creating a key. | (never commit) | Relayer credential. |
| `POLYMARKET_BUILDER_SECRET` | No | empty | string | Builder secret. All three builder vars must be set together (`_env_builder_present`). | | |
| `POLYMARKET_BUILDER_PASSPHRASE` | No | empty | string | Builder passphrase. | | |
| `DATA_DIR` | No | `<repo>/data` | writable path | SQLite, encryption key, bot JSON legacy. | `/data` | On Railway, unset or non-volume path loses data on redeploy. |
| `PORT` | No | `8010` | TCP port | Bind port for `python -m backend` and `run.py`. | `9000` | |
| `FORCE_SECURE_COOKIES` | No | unset | `1` | Sets Secure on the auth cookie. Also on if `RAILWAY_ENVIRONMENT` is set. | `1` | Breaks HTTP-only local login if forced on http://127.0.0.1. |
| `RAILWAY_ENVIRONMENT` | Set by Railway | unset | any | Enables Secure cookies and DATA_DIR persistence warnings. | | Do not set locally unless you want those behaviors. |
| `PYTHONPATH` | Docker only | `/app` in Dockerfile | path | Makes `import backend` work. | `/app` | |
| `TWAP_SMOKE_BASE` | Scripts only | `http://127.0.0.1:9000` | URL | `scripts/_v2_smoke.py` | | Not used by the app. |

**Not used by the running app** (only probed in `scripts/_v2_preflight.py`): `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE`. Live L2 creds are derived from the private key via `SecureClient.create`.

### Per variable (ops)

| Variable | Change while running | Restart needed | Paper-safe | Live-safe |
| --- | --- | --- | --- | --- |
| `SITE_PASSWORD` | No (read at import) | Yes | any | strong unique |
| `SESSION_SECRET` | No | Yes | optional | set separately |
| `POLYMARKET_PRIVATE_KEY` | No | Yes | omit if a stored account exists for TWAP | prefer stored account on `/real`; env key is extra surface |
| Builder trio | No | Yes (session cache drops if account later has a key) | omit | omit unless redeem needs a pre-made builder key; the bot can `create_builder_api_key()` for deposit wallets |
| `DATA_DIR` | No | Yes | `./data` | persistent volume in containers |
| `PORT` | No | Yes | 8010 or 9000 | same |
| `FORCE_SECURE_COOKIES` | No | Yes | unset | `1` only behind HTTPS |

## API credentials and wallet

- **Dashboard:** `SITE_PASSWORD` cookie `twap_session`, 30 days, httponly, samesite=lax.
- **Trading wallet:** pasted once on `/real` → stored as `encrypted_key` (Fernet). API never returns the key (`backend/accounts.py` `public_view`).
- **Encryption key file:** `data/.encryption_key`. Losing it makes stored keys unreadable.
- **CLOB L2 apiKey/secret/passphrase:** derived at session start; not stored in `.env` by the app.
- **Builder key:** created automatically for non-EOA wallets at first redeem, encrypted as `encrypted_builder` on the account, or taken from env.

## Market selection

Not an env var. Each bot has `asset` in `XRP|BTC|ETH|SOL|DOGE`. Copy Trader paper is forced to `ALL`. Pipelines start on demand (`Hub.retain`).

Hardcoded endpoints (`backend/config.py`): Binance WS, Gamma, CLOB market WS, PolyBolt, RTDS activity.

## Paper vs live

- Paper: `/bots`, `BotManager`. Simulated fills. Seed bot on empty DB.
- Live: `/real`, `RealBotManager`. No orders until **Start**. Shared evaluators from `backend/engine/bots.py`.

There is no `DRY_RUN` env flag.

## Size / budget

- `order_usd` (LOT), strategy schema typically min 1 max 500.
- Dip Harvester: `per_side_max_usd` per side per round.
- Paper `start_balance` on create: clamped 10–100_000, default 1000.
- Live cash is CLOB collateral (`sync_clob_balance`). Unread cash ≠ zero; buys are withheld.

## Exposure / daily loss / Telegram / rebates

**Not implemented.** No daily loss env, no STOP file, no Telegram vars, no rebate handling. Fee is taker-only `0.07 * shares * min(p, 1-p)` (`FEE_RATE` in `bots.py`).

## Logging

`logging.basicConfig` INFO to stderr/stdout. No log-level env var.

## Retry / reconnect

WebSocket feeds reconnect forever with backoff `(500, 1000, 2000, 5000)` ms (`CONFIG.reconnect_backoff_ms`). CLOB order POST is **not** retried (no idempotency). Unproven sends go to `pending_op` reconcile.

## Timing (code constants, not `.env`)

| Name | Value | Where |
| --- | --- | --- |
| `window_ms` | 300_000 | config |
| Bot loop | 0.25 s | BotManager / RealBotManager |
| UI WS batch | 50 ms | config |
| Discovery | 15 s | config |
| Resolution poll | 4 s, give up 300 s | config |
| Ping | 10 s | config |
| Paper/real buy: skip last 5 s and first 2 s of round | hardcoded in `buy()` | bots.py / real_bots.py |
| Spread cap | 20¢ | buy() |
| `LOCK_PROFIT_CENTS` | 99.0 | bots.py |
| `OFFICIAL_WAIT_MS` | 90_000 | bots.py |
| `LOCAL_SETTLE_MARGIN_BPS` | 5.0 | bots.py |
| `VOID_DEADLINE_MS` | 600_000 | bots.py |
| `RECONCILE_TTL_MS` | 900_000 | bots.py |
| `RECONCILE_GIVE_UP_MS` | 90_000 | real_bots.py |
| Redeem batch | 5, ~every 20 s (`balance_tick % 80 == 1`) | clob_executor / real_bots |
| Idle pipeline reap | 60 s | main.py `REAP_GRACE_S` |

## Strategy parameters

See [STRATEGIES.md](STRATEGIES.md). Clamped by `sanitize_params`.

## `.env.example`

Keep the repo example free of secrets. Builder vars are optional comments only.
