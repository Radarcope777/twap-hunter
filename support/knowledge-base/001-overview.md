# 001 Overview

Twap Hunter is a FastAPI app: research UI for Polymarket 5-minute crypto Up/Down (XRP, BTC, ETH, SOL, DOGE) plus paper bots (`/bots`) and real FOK bots (`/real`).

Paper fills are simulated against the live CLOB book. Real bots POST FOK market orders only after explicit Start. Copy Trader is paper-only on live (`copy_trader not supported on real yet`).

Not implemented: Telegram, daily loss limit, STOP file, FAK toggle, percent take-profit.

State: SQLite under `data/` (or `DATA_DIR`). Keys: Fernet in DB; file `data/.encryption_key`.

Trading can lose the deposited balance. No profit claim.
