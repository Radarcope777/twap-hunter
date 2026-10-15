# Twap Hunter

Real-time tick-level research terminal for scalping Polymarket's 5-minute
crypto **Up or Down** markets (XRP, BTC, ETH, SOL, DOGE) using the TWAP lag
edge: the real market moves first, the Polymarket TWAP chart follows.

## What it shows (all live, tick level)

| Panel | Data | Source |
|---|---|---|
| Oracle / TWAP chart | Chainlink reference price, official 60s TWAP (the quantity the round resolves on), **Price To Beat** line, round boundaries | Polymarket PolyBolt (`wss://ws-live-v2.polymarket.com/ws`) |
| Exchange ticks chart | Every trade with aggressor side, dot size ∝ quantity, 500ms buy/sell volume bars | Binance `{sym}@trade` WebSocket |
| UP quote chart | UP bid/ask/last over the whole round (0–100¢) | Polymarket CLOB market WebSocket |
| Prices | Chainlink, Binance last, official TWAP 60s, Price To Beat + source | |
| Deltas | **TWAP Δ vs PTB** (resolves the round) and oracle price Δ, in $ and bps | |
| Session volume | Buy / Sell / Net volume ($ and units), trade count, session imbalance + rolling 1s/5s/30s imbalance | reset every round |
| Volume pressure | −100..+100 weighted score (imbalance 40%, tick direction 25%, momentum 25%, tick velocity 10%) with component breakdown | |
| Momentum | Price change over 250ms/500ms/1s/2s/5s in $ and bps, tick velocity, up-tick ratio | |
| Polymarket | Round question, UP/DOWN bid/ask + sizes, last trade, spread | |
| TWAP prediction | Every 5s the rolling TWAP is projected forward (+30s dot and a round-end diamond) by holding the current Chainlink price — the rolling integral makes the near future largely deterministic. Dots land in the future space right of the now-line and self-score when time reaches them (green = actual TWAP within 1 bp, red = miss). The panel tracks +30s accuracy and round-end direction hit rate for the model vs the market's own implied prediction (UP mid > 50¢). | |
| Event timeline | Round rolls, PTB latches, TWAP target crosses, momentum spikes, volume spikes, strong pressure, quote swings, market discovery, resolutions | notification box, newest first |

**Price To Beat**: the official 60s TWAP at the close of the previous
round (`official_twap60_carry`). That is what the new Polymarket 5-minute
Up/Down markets resolve against. There is no 30s TWAP on PolyBolt.

PolyBolt price channels require CLOB credentials. The bot derives them via
the unified SDK from a stored account private key (Real → Add Account) or
from `POLYMARKET_PRIVATE_KEY` in `.env`. Never commit the key. The old
public RTDS host is used only for the copy-trading `activity` topic.

## Architecture

```
Binance trade WS      Polymarket PolyBolt WS     Polymarket Gamma REST + CLOB WS
 (ticks, aggressor)    (Chainlink + TWAP60)       (market discovery, books, resolution)
        │                     │                            │
        ▼                     ▼                            ▼
              backend/engine/pipeline.py  (asyncio, one pipeline per asset)
     ring buffers · volume/momentum/pressure engines · 5m window + PTB · events
                          │
                          ▼  batched every 50 ms (no per-tick JSON)
              FastAPI WebSocket  /ws   ─►  frontend (vanilla JS + canvas)
                                            rAF-rendered charts, zero frameworks
```

- **Hot path** per tick: buffer append + O(small-window) metric updates.
  Pressure/velocity math is throttled to 10 Hz.
- The UI receives batches every 50 ms; charts render on their own
  `requestAnimationFrame` loop from ring buffers — a burst of hundreds of
  ticks/second never causes DOM churn.
- The countdown is computed from the server round boundary plus a measured
  clock offset — never a blindly decremented timer.
- All feeds reconnect forever with backoff. Paper bots simulate fills
  against the live CLOB book. Real bots (explicit Start on `/real`) place
  FOK market orders through `polymarket-client`. Account UI reads
  Data API v2 (`/v2/value`, `/v2/positions`, `/v2/trades`).

## Run

Requirements: Python 3.12+ (no Node needed).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt
.venv/Scripts/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010
```

Open http://127.0.0.1:8010 — the XRP terminal loads immediately. Switch
assets with the tabs in the top bar (the backend swaps its feeds to the
selected series; all connected browsers follow).

## Layout

- `backend/config.py` — asset registry (Binance symbol / RTDS symbol /
  Gamma series slug per asset) and all tunables.
- `backend/feeds/` — `binance.py`, `rtds.py` (PolyBolt Chainlink + 60s TWAP),
  `polymarket.py` (discovery, books, resolution), `activity.py` (legacy RTDS
  activity topic), `account_data.py` (Data API v2).
- `backend/engine/clob_executor.py` — unified SDK: derive L2 creds, balances,
  live FOK orders.
- `backend/engine/metrics.py` — pure metric engines (rolling TWAP, volume,
  momentum, pressure).
- `backend/engine/pipeline.py` — wiring, 5m window, PTB, event detection,
  wire payloads.
- `backend/main.py` — FastAPI app + WebSocket hub + asset switching.
- `frontend/` — static, no build step: `js/bus.js` (WS + ring buffers),
  `js/chart.js` (canvas engine), `js/panels.js` (DOM panels + timeline).

## Paper-trading bots (/bots)

The **/bots** page manages automated paper-trading bots per market —
"+ ADD BOT" picks a strategy, market, and parameters. Everything is
simulation: fills execute against the REAL displayed CLOB top-of-book
(price and size), Polymarket's crypto taker fee is modeled
(`7% × shares × min(p, 1−p)`, taker only — it is brutal near 50¢ and this
matters for strategy design), and rounds settle by the real rule
(final TWAP ≥ price-to-beat → UP). No real orders exist anywhere.

Strategies:
- **TWAP Lock-In** — enter in the last N seconds when the projected final
  TWAP clears the PTB by a margin but the side still sells below a max
  price. The verified statistical edge.
- **Pressure Scalp** — enter on strong order-flow pressure, cents-based TP/SL.
- **Momentum Rider** — enter on sharp 1s spot moves before quotes reprice.
- **Cross Hunter** — buy the side the TWAP just crossed to.

Running bots keep their market's data pipeline alive without any browser
tab, persist across restarts (SQLite in `data/`), and log every action
with its reason. Live execution is a separate `/real` layer: import a
paper config onto a stored account and Start it explicitly.
