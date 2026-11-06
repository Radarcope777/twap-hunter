# Twap Hunter — operator README

Twap Hunter is a FastAPI research terminal and trading bot for Polymarket **5-minute crypto Up or Down** markets. It shows live TWAP, Chainlink, Binance ticks, and CLOB quotes, and can run **paper** bots (simulated fills) and **real** bots (FOK market orders on the CLOB).

This document is for buyers and operators. It is written from the source in this repository. Features that are not in the code are marked **not implemented**.

Trading can lose the entire deposited balance. Nothing here is a profit guarantee.

## What it does

- Watches Polymarket 5-minute Up/Down rounds for **XRP, BTC, ETH, SOL, DOGE**.
- Projects the official 60-second TWAP versus Price To Beat (PTB) and evaluates strategy rules every ~250 ms.
- Paper bots fill against the live displayed CLOB top of book and model crypto taker fees. No CLOB POST is sent.
- Real bots, after an explicit Start on `/real`, send **FOK** market orders through `polymarket-client`.
- After a round ends, a leftover position is **settled** from the official (or provisional local) outcome. Real bots then **redeem** resolved tokens on-chain when a `condition_id` is known.

## Markets

| UI key | Binance symbol | Gamma series slug |
| --- | --- | --- |
| XRP | xrpusdt | xrp-up-or-down-5m |
| BTC | btcusdt | btc-up-or-down-5m |
| ETH | ethusdt | eth-up-or-down-5m |
| SOL | solusdt | sol-up-or-down-5m |
| DOGE | dogeusdt | doge-up-or-down-5m |

Round length is 300_000 ms (5 minutes). Default dashboard asset is XRP. A running bot keeps its asset pipeline alive without a browser tab.

Copy Trader on paper can also record non-crypto leader trades into a paper portfolio. That path is **paper only**.

## Modes

| Mode | URL | Orders |
| --- | --- | --- |
| Research terminal | `/` | none |
| Paper bots | `/bots` | simulated against the live book |
| Real accounts + bots | `/real` | live FOK CLOB orders after Start |

Paper and real params are independent copies. Importing a paper bot onto `/real` copies name, asset, strategy, and params onto a stored account.

## What is in the delivery

- Python backend (`backend/`), static frontend (`frontend/`), tests (`tests/`).
- This `doc/` operator pack and `support/knowledge-base/` for AI support.
- `.env.example` (no secrets).
- Optional Docker image (`Dockerfile`, `railway.toml`) for container deploys.

## What it does not do

- **Telegram notifications** — not implemented. There is no Telegram token, bot, or report sender in the code.
- **Daily loss limit / exposure cap / STOP file** — not implemented. Stop a bot with the UI Stop button or by stopping the process.
- **Copy Trader on `/real`** — Start is allowed; the live loop sets `copy_trader not supported on real yet` and sends no orders.
- **FOK/FAK switch** — live market orders always use `order_type="FOK"`.
- **Percent take-profit** — exits are in cents, or hold to settlement, or sell at 99¢ bid.
- **Guaranteed fills or profit** — a FOK order is fully filled or killed. Geoblock, thin books, and fees can produce losses.

## System requirements

- Python **3.12** (`runtime.txt` is `python-3.12`; Docker uses `python:3.12-slim-bookworm`). The development VPS audit used 3.14 in a venv; 3.12 is the declared runtime.
- 64-bit Windows 10/11 or a Debian/Ubuntu-like Linux.
- Outbound HTTPS and WSS to Binance, Polymarket Gamma, CLOB, PolyBolt, Data API, and (for Copy Trader paper) the activity RTDS host.
- Inbound TCP only if the dashboard is opened from another machine. Default bind for `python -m backend` is `0.0.0.0` and port `PORT` or **8010**.
- Disk for SQLite under `data/` (or `DATA_DIR`).
- A Polymarket account and USDC/pUSD for live trading. The operator IP must be allowed by Polymarket (see geoblock).

## Architecture (short)

```
Binance trade WS          PolyBolt WS (Chainlink + TWAP60)       Gamma REST + CLOB market WS
        │                              │                                    │
        └──────────────► pipeline.py (one pipeline per asset) ◄─────────────┘
                                    │
                    paper BotManager (~250 ms)     real RealBotManager (~250 ms)
                                    │
                         FastAPI + static UI + /ws
```

State: SQLite in `data/twaphunter.db` (bots, real bots, accounts, trades, rounds). Private keys are Fernet-encrypted; the Fernet key is `data/.encryption_key`.

## Quick start

1. Install Python 3.12 and create a venv in the project root.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env`. Set `SITE_PASSWORD`.
4. From the project root:

```bash
python -m backend
```

5. Open `http://127.0.0.1:8010` (or the `PORT` you set). Log in if `SITE_PASSWORD` is set.
6. Create a paper bot on `/bots`, Start it, watch the activity log.

Windows PowerShell uses `.venv\Scripts\python.exe`. Linux uses `.venv/bin/python`. If port 8010 is blocked on Windows, bind `--port 9000` with uvicorn as in [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md).

## Paper mode

See [PAPER_TRADING.md](PAPER_TRADING.md). Paper bots never POST to the CLOB. Fresh installs seed one paper bot named **BTC Lock In Small Loss** (`twap_lockin_cut`) if that name/asset/strategy is not already present.

## Live mode

See [LIVE_TRADING.md](LIVE_TRADING.md).

1. Add an account on `/real` (label, address, private key). The key is encrypted at rest and never returned by the API.
2. Import a paper bot onto that account.
3. Set LOT (`order_usd`) on the real card. Other strategy params stay as imported; the real card has no settings gear.
4. Press **Start**. Confirm cash on the CLOB before expecting fills.

## Telegram

Not implemented. [TELEGRAM.md](TELEGRAM.md) records that fact for support.

## Risk limits

There is no daily-loss or max-exposure env var. Practical limits in code:

- `order_usd` (LOT), clamped per strategy (typically $1–$500).
- One open single-position lot for most strategies (Dip Harvester uses a two-side book).
- Real buy refuses last 5 s of the round, first 2 s, spread > 20¢, ask outside `max_entry_cents`, unread cash, size under 1 share.
- FOK: the whole dollar (buy) or share size (sell) must fill at the capped price or the order is killed.

See [RISK_MANAGEMENT.md](RISK_MANAGEMENT.md).

## Stop the bot

- Paper/real bot: **STOP** on the card (paper STOP also tries a paper flatten sell if a position is open).
- Process: Ctrl+C in the terminal, or stop the systemd/Task Scheduler job.
- There is **no** STOP-file watcher.

## Updates

See [UPDATES.md](UPDATES.md). Replace code, keep `.env` and `data/`, reinstall requirements if they changed, restart the process.

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Common live rejects: geoblock, FOK not filled, CLOB min 1 share, unfinished `pending_op`.

## Security

See [SECURITY.md](SECURITY.md). Never commit `.env`, `data/`, or `.encryption_key`. Do not zip the whole folder for a buyer if those files exist.

## Legal / risk

This software places real orders when a real bot is Started. You can lose money from fees, FOK kills, settlement at $0/share, VOID (position cleared, cash not refunded), geoblock, and shared-wallet conflicts if two real bots trade the same asset on one account. Use only where Polymarket trading is lawful for you.

## Doc index

| File | Topic |
| --- | --- |
| [INSTALL_LINUX.md](INSTALL_LINUX.md) | Ubuntu/Debian install |
| [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) | Windows 10/11 install |
| [CONFIGURATION.md](CONFIGURATION.md) | Env vars and tunables |
| [STRATEGIES.md](STRATEGIES.md) | Strategy catalog from code |
| [OPERATION_FLOW.md](OPERATION_FLOW.md) | Process lifecycle |
| [RISK_MANAGEMENT.md](RISK_MANAGEMENT.md) | Limits that exist (and do not) |
| [PAPER_TRADING.md](PAPER_TRADING.md) | Paper bots |
| [LIVE_TRADING.md](LIVE_TRADING.md) | Real bots, FOK, redeem |
| [TELEGRAM.md](TELEGRAM.md) | Not implemented |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Errors |
| [SECURITY.md](SECURITY.md) | Keys, cookies, geoblock |
| [UPDATES.md](UPDATES.md) | How to update |
| [FAQ.md](FAQ.md) | Short answers |
| [SUPPORT_RAG.md](SUPPORT_RAG.md) | How to use the knowledge base |
