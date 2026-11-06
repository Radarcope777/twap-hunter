# Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `BUY rejected: Trading restricted in your region` | Polymarket geoblock on this IP | Allowed region / IP. Restarting the Python process does not lift geoblock. |
| `FOK orders are fully filled or killed` | Not enough size at the **best ask** for the whole `order_usd` | Normal on thin 5m books with $1 lots. Raise liquidity, raise price cap, or accept skips. Do not expect a retry POST. |
| `lot $x = 0.78 sh @ 46¢ — CLOB min is 1 sh` | Visible ask size < 1 share | Skip; order was not sent. |
| `copy_trader not supported on real yet` | Live loop skips `asset==ALL` | Use paper Copy Trader, or a different live strategy. |
| `REDEEM withheld: Position ID must use a supported protocol v2 module` | Old queue used a CLOB token as V2 position id | Current code skips those rows (`REDEEM skipped: token is not a v2 position id`). Restart to load the fix. New settles must keep `condition_id`. |
| `REDEEM skipped: token is not a v2 position id` | Same, one-shot drop | Queue cleared; cannot redeem that leftover without a condition id. |
| `get_order failed: UnexpectedResponseError` / `sell withheld` / `still unproven` | SDK could not parse GET `/data/order/{id}` | Wait; reconcile retries. Often proves in seconds. Not caused by two bots sharing a market **for this error class**. |
| Two bots, same Open PnL | Data API wallet mark per asset | Compare `trades` PnL, not the card Open PnL. |
| `unauthorized` JSON | `SITE_PASSWORD` set, no cookie | POST `/api/login` `{"password":"..."}` or use `/login`. |
| Blank TWAP / PolyBolt auth missing | No private key in env and no stored account | Add account or `POLYMARKET_PRIVATE_KEY`. |
| `CLOB balance $0.00` after a read error | Older bug; current code uses `BalanceReadError` | If cash is unread, buys withhold. Check CLOB auth logs. |
| Port already in use / 10048 | Second uvicorn | Stop the process on that port. |
| `/health` `persistent: false` on Windows | Volume heuristic | Ignore locally if `data/twaphunter.db` is updating. |
| Paper bot created but no ticks | Pipeline not retained / network | Start the bot (retain), check outbound WSS. |
| Live bot RUNNING but no orders | Stopped evaluator (copy), geoblock, FOK, last 5 s, spread, pending_op | Read activity log levels wait/error/skip. |
| Positions hang at 0¢ after loss | Not redeemed yet | Running live bot redeem pass, or redeem in Polymarket UI. |
| VOID in log | No official/local outcome for 10 min | Position cleared; cash not refunded. |
| `UserInputError` redeem gasless key | Deposit wallet without builder key | Bot should create one; if create failed, check CLOB auth. Do not paste keys into tickets. |
| Tests wrote `r_test` into live SQLite | Running isolated case files in-process | Use wrappers `tests/test_pending_op.py` etc. with the **venv** Python. |
| System Python `polymarket-client` 0.6 | Wrong interpreter | `.venv/.../python.exe` with 0.13. |

## Activity log colors (UI)

- `error` — red (FOK reject, confirmed 0 shares sell, geoblock)
- `wait` — amber (unproven, sell withheld, redeem withheld/skipped)
- `order` — green (BUY/SELL/SETTLE/REDEEM success)

## Restart

Restart reloads code and SQLite. It does **not** clear geoblock or finish an unproven CLOB order; `pending_op` is persisted and reconciled after start.
