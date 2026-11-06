# Strategies and trading logic

Catalog: `STRATEGIES` and `_EVALUATORS` in `backend/engine/bots.py`. Real bots import the same evaluators (`from backend.engine.bots import ... _EVALUATORS`). Names below are the **code ids**, not marketing labels.

Shared execution (paper `BotManager.buy` / real `RealBotManager.buy` unless noted):

- One open `position` for most strategies (Dip Harvester uses `book` UP+DOWN piles).
- Size: `shares = order_usd / (ask/100)`, capped by visible ask size.
- Min size: 1 share (`MIN_ORDER_SHARES` / `REAL_MIN_ORDER_SHARES`).
- Fee modeled: `0.07 * shares * min(p, 1-p)` (taker only). Real CLOB also charges; the bot does not implement maker rebates.
- Skip if last 5 s of round or first 2 s; if spread (ask−bid) > 20¢; if ask outside the strategy cap.
- Real extra: unread CLOB cash withholds buy; FOK POST of full `order_usd` at **best ask only** (does not walk the book).
- Mid-round lock: if bid ≥ 99¢, `lock_profit_if_bid` sells (real loop runs this **before** the evaluator).
- Round end: no flatten POST on live. Settlement books $1/share win or $0 loss. Paper credits paper balance; live does **not** credit cash until redeem + next `sync_clob_balance`.
- Shared `_exits`: sell if bid − entry ≥ `tp_cents`, or move ≤ −`sl_cents`, or bid ≥ 99¢.

Real vs paper: Copy Trader live is blocked. Real card cannot edit entry_window / min_margin / max_entry — only LOT. Two live bots on one wallet/asset can sell each other’s shares.

## A. Implemented and selectable in the UI

All of these appear in `/api/strategies` and can be created as paper bots. Live import works for all except Copy Trader (import may succeed; the live loop will not trade).

### 1. `twap_lockin` — TWAP Lock-In

- **File:** `_eval_twap_lockin`
- **Markets:** one of XRP/BTC/ETH/SOL/DOGE
- **Data:** projected final TWAP vs PTB (`_projected_margin_bps`), remaining time, ask
- **Entry:** last `entry_window_s` seconds, but remaining must be ≥ 10 s; `|margin| ≥ min_margin_bps`; one entry per round; buy projected side
- **Orders:** one BUY; hold; optional 99¢ sell
- **Side:** one side
- **Done:** settlement or 99¢ sell
- **Params:** `entry_window_s` (def 55, 15–240), `min_margin_bps` (2.5), `max_entry_cents` (88, 50–95), `order_usd` (1)

### 2. `twap_lockin_cut` — TWAP Lock-In + Cut Loss

- Same entry as lock-in (defaults: window 90 s, margin 1.5 bps).
- If projected TWAP flips against the position by `cut_margin_bps` for `cut_confirm_s`, **sell** (small loss vs full $0 settle).
- Seed paper bot on empty DB: name “BTC Lock In Small Loss”, BTC, these params including `order_usd=1`.
- Extra params: `cut_margin_bps` (0.5), `cut_confirm_s` (2)

### 3. `winning_hold` — Winning Hold

- **Entry:** remaining > 60 s; `|margin| ≥ conf_bps`; ask in `[entry_min_cents, entry_max_cents]`; one initial entry per round
- **Hold** to settlement unless projection stays against the side by `switch_margin_bps` for `switch_confirm_s`, then sell and buy the other side at `switch_max_cents`, up to `max_switches`. No switches in last `stop_switch_s`.
- Live also sells at 99¢ via shared lock-profit.
- Params: `entry_min_cents` 40, `entry_max_cents` 60, `conf_bps` 0.5, `switch_margin_bps` 1.2, `switch_confirm_s` 4, `switch_max_cents` 78, `stop_switch_s` 20, `max_switches` 3, `order_usd` 1

### 4. `cross_line` — Cross Line

- Fires when rolling TWAP **crosses** PTB by `min_cross_bps` (sign change). Ignores PTB if `ptb_source == "exchange_approx"`.
- Exits: optional `tp_cents` (0 = none), always `sl_cents`, optional momentum reverse (`use_momrev`, `rev_bps`, `rev_confirm_s`). Else settlement.
- Params include `max_entries` (2).

### 5. `next_round_scalp` — Next Round Scalping

- Last `capture_last_s` of round: store momentum bps.
- Next round first `entry_window_s`: if `|bps| ≥ signal_bps_min` and ask in [entry_min, entry_max], buy that side.
- Exits: `_exits` tp/sl, then `max_hold_s`.
- `continuous=1`: extra mid-round scalps on 1s momentum, ask in re-entry band, cooldown 8 s, `max_entries`.

### 6. `copy_trader` — Copy Trader

- **Paper only for live orders.** Asset forced to `ALL`. Activity feed `wss://ws-live-data.polymarket.com`.
- Copies leader wallet from `params.wallet`. Skips history on first tick. Filters `min_leader_size`, `max_copy_delay_s`, `min_copy_gap_s`.
- Crypto 5m slug match → paper `buy`/`sell` on that pipeline.
- Other markets → paper `gbuy`/`gsell` at leader price ± `slippage_cents`, cap `max_open`.
- **Live:** `copy_trader not supported on real yet` every loop tick. No CLOB POST.

### 7. `dip_harvester` — Dip Harvester

- Buys **both** UP and DOWN when that side’s ask dips `dip_cents` below its rolling high over `lookback_s`.
- Caps `per_side_max_usd`, `max_price_cents`, `min_gap_s`, no buys in last `no_buy_last_s`.
- Uses `buy_book` (not single `position`). Holds to settlement. Paired UP+DOWN under 100¢ combined is locked at settle regardless of outcome; unpaired excess rides the leftover side. This is **not** advertised as arbitrage in the evaluator — it is dip accumulation of both outcomes.

### 8. `pressure_scalp` — Pressure Scalp

- Entry when `|pressure score| ≥ pressure_min` and ask in band. Cooldown 5 s. `max_entries`.
- Exits: `_exits` tp/sl.
- Strategy text in code: live testing went **0W/11L**. Still selectable. Treat as experimental **behavior**, not a disabled flag.

### 9. `momentum_rider` — Momentum Rider

- Spot momentum `mom_bps_min` over `mom_window_s`. Buy move side if ask in band. Cooldown 5 s. tp/sl `_exits`.

### 10. `cross_hunter` — Cross Hunter

- Similar to Cross Line but cross threshold is hardcoded **0.5 bps** in the evaluator (not a param). tp/sl `_exits`. `max_entry_cents`, `max_entries`.

## B. Implemented but disabled on live

| Id | Paper | Live |
| --- | --- | --- |
| `copy_trader` | yes | loop continue; error string |

No other strategy is gated off in `_EVALUATORS`.

## C. Experimental / research-only

- `backend/engine/research.py` — observational events for paper (ENTRY_ATTEMPT, CUT_*, etc.). Not consulted for live entry/exit.
- `backend/research/replay/` — offline replay; must not import `backend.engine.bots` execution in production path (replay is a separate package).
- Pressure Scalp: kept despite documented 0W/11L note in `STRATEGIES` description.

## D. Configuration-only modes

- LOT `$` vs `%` in the UI (`frontend/js/lot.js`) still saves **`order_usd`** (percent is converted using displayed capital before POST).
- Paper vs real is a **separate bot record**, not a strategy param.

## E. Not in the code

- Arbitrage engine, market making, grid, hedging module (Dip Harvester buying both sides is the closest; it is dip buying, not a hedge module).
- Percent take-profit, FAK, iceberg, GTC limit resting bids.
- Telegram, daily loss kill-switch, STOP file.
- Auto-retry of FOK POST.

## Settlement (all hold-to-round strategies)

1. Prefer official Gamma outcome (`source == "market"` or DB `outcome_official`).
2. Else local TWAP vs PTB if margin ≥ 5 bps.
3. Else wait 90 s, then provisional local math (correctable without cash on live).
4. Else poll Gamma slug; after 10 min **VOID**: clear position, pnl 0, **cash not refunded**.
5. Live official (non-provisional) SETTLE enqueues redeem by `condition_id` or token id.

## Related files

| Path | Role |
| --- | --- |
| `backend/engine/bots.py` | Catalog, paper fills, evaluators, fees, settlement constants |
| `backend/engine/real_bots.py` | Live loop, pending_op, redeem queue, FOK buy/sell |
| `backend/engine/clob_executor.py` | SecureClient, FOK, balances, redeem |
| `backend/feeds/polymarket.py` | Market discovery, v1 clobTokenIds / v2 positionIds |
| `backend/feeds/rtds.py` | PolyBolt TWAP60 |
| `backend/feeds/activity.py` | Copy Trader paper feed |
