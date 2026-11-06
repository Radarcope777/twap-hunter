# FAQ

**What markets does it trade?**  
Polymarket 5-minute crypto Up/Down: XRP, BTC, ETH, SOL, DOGE. Copy Trader paper can also book other Polymarket markets in a paper portfolio.

**Does paper send real orders?**  
No.

**Does Start on `/real` send real orders?**  
Yes, FOK market orders, except Copy Trader which is blocked on live.

**Why did FOK fail when the book looked like it had size?**  
FOK needs the **whole** `order_usd` at the **best ask** only. Size at worse prices is ignored.

**Can I switch FOK to FAK?**  
Not in the UI or params. Code hardcodes FOK.

**Why do two live bots show the same Open PnL?**  
That field is the shared wallet’s Data API mark for the asset, not each bot’s strategy PnL.

**Is there Telegram?**  
No.

**Is there a daily loss limit?**  
No.

**Python version?**  
Declared 3.12 (`runtime.txt`, Dockerfile). Use the project venv.

**Default port?**  
8010 (`PORT`). On some Windows hosts use 9000.

**Where is state stored?**  
`data/goodfallen.db` and `data/.encryption_key` (or `DATA_DIR`).

**Copy Trader on live?**  
Not supported. Paper only.

**Redeem vs settlement?**  
Settlement is the bot’s PnL bookkeeping. Redeem is the on-chain burn that pays pUSD for winning tokens (0 for losers).

**Geoblock after restart?**  
Unchanged. The CLOB still sees the same IP.

**Can I run Docker?**  
Yes, optional. `Dockerfile` CMD `python -u -m backend`, expose 8080, set `PORT`. Not required for Windows/Linux venv installs.
