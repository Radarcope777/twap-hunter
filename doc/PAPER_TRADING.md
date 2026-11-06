# Paper trading

Paper bots live on `/bots`. Implementation: `backend/engine/bots.py` `BotManager`. Fills are simulated against the **live** CLOB top of book. Comment in code: no real order leaves this process.

## Enable

Always on. Create a bot: strategy + asset + optional params + `start_balance` (default $1000). Start it. Running paper bots `retain` their market pipeline so they trade without a browser.

Fresh database: seeds **BTC Lock In Small Loss** (`twap_lockin_cut`, BTC, `order_usd=1`, running).

## What is simulated

- BUY at ask, size min(lot shares, ask size), fee `taker_fee`.
- SELL at bid.
- Settlement credits **paper** `balance` ($1/share win, $0 loss).
- Copy Trader `gbuy`/`gsell` for non-crypto leader trades (paper portfolio).
- STOP on a paper bot tries `sell(..., "flatten on stop")` if a position is open.

## What is still real data

Binance ticks, PolyBolt TWAP60, Gamma rounds, CLOB quotes. Wrong TWAP auth means empty TWAP and strategies that need PTB/TWAP will not enter.

PolyBolt needs CLOB creds: a stored account private key **or** `POLYMARKET_PRIVATE_KEY` in `.env`. Paper bots do not themselves hold a key.

## UI

- Settings gear: full strategy params (`frontend/js/bots.js`).
- LOT control: updates `order_usd`.
- Reset: fresh balance, clears trade history; strategy stays.

## Persistence

SQLite `bots` + `trades` + `rounds`. In-memory activity trimmed (`MAX_TRADES_KEPT = 300`); full trades via `GET /api/bots/{id}/trades`.

## Tests

`tests/test_research_phase2a.py` uses a temporary `DATA_DIR`. Isolated live-safety tests stub storage so they do not write the live DB. Run with the project venv:

```
.venv/bin/python -m pytest tests/test_pending_op.py tests/test_position_balance.py tests/test_resolve_fill.py
```

(Windows: `.\.venv\Scripts\python.exe -m pytest ...`)
