# Risk management

What the code actually limits. There is **no** daily loss cap, **no** max notional exposure env, **no** STOP file, **no** Telegram alert.

## Limits that exist

| Control | Where | Effect |
| --- | --- | --- |
| `order_usd` | strategy param / LOT UI | Dollar size of each BUY (real FOK spends this whole amount at one price) |
| Strategy min/max | `sanitize_params` | Clamps numeric params to schema |
| One position | most evaluators | Skip BUY if `position` is open |
| `max_entries` / `max_switches` | some strategies | Per-round caps |
| Dip `per_side_max_usd` | dip_harvester | Cap spent per side per round |
| First 2 s / last 5 s | `buy()` paper and real | No new buys |
| Spread > 20¢ | `buy()` | Skip |
| Ask vs `max_entry_cents` | `buy()` / evaluator | Skip |
| Min 1 share | paper and real | Skip; live may log “CLOB min is 1 sh” |
| Paper `start_balance` | create bot | 10–100_000 |
| Live cash | `sync_clob_balance` | BUY skipped if cost+fee > last read cash; unread cash withholds (not treated as $0) |
| FOK | `place_market_order` | Unfilled remainder kills the **entire** order |
| `pending_op` | real | Blocks further BUY/SELL/settle until proven or released |
| Lock profit 99¢ | shared | Sell instead of waiting for $1 settle |
| Cut / switch | lock-in-cut, winning_hold | Exit or flip when TWAP projection turns |
| TP/SL cents | scalp-style strategies | Mid-round FOK sell (paper: simulated) |
| Redeem skip held token | `collect_redeem_targets` | Will not redeem the token a running bot still holds |
| Account delete | API | 409 if real bots still linked |

## Limits that do not exist

- Daily / weekly loss halt
- Max open bots or max total USD
- Per-account kill switch besides UI Stop
- FAK / partial fills
- Cancel of resting orders (the bot does not rest GTC bids)
- Rebate-aware sizing
- Isolated cash per real bot (all real bots on one account share CLOB cash and token balances)

## Shared wallet

Two running live bots on the same Polymarket account and the same asset share token balances. One bot’s FOK sell can consume shares the other still records as `position`. That shows up as FOK kill or “confirmed 0 shares”, not as a parse error on `get_order`.

UI **Open PnL** on a real card is Data API `cash_pnl` filtered to the bot’s asset for the **whole wallet**, not that strategy’s booked trades.

## Settlement cash risk

- VOID after 10 minutes with no official or local outcome: position cleared, **cash not refunded**.
- Provisional settle can later be corrected in the trade log without moving CLOB cash.
- Losing tokens stay on the wallet until redeem; redeem of a loser pays 0 pUSD and removes the row.

## Fees

Taker fee formula used in sizing and paper PnL: `7% × shares × min(price, 1−price)`. Near 50¢ this is expensive. Maker rebates are not modeled.

## Operator checklist (not automated)

- Start with paper.
- Live LOT $1 until geoblock and FOK behavior are understood.
- One live bot per asset per account.
- Allowed jurisdiction / IP.
- Backup `.encryption_key` with the database.
