# Paper bots — strategy catalog (agent brief)

This file is a **plain-text dump** of the Cursor canvas
`paper-strategies-catalog.canvas.tsx` so Codex / other agents can read it
without the canvas SDK.

- **Source of truth:** `backend/engine/bots.py` (`STRATEGIES` + `_EVALUATORS`).
  If this note disagrees with the code, trust the code.
- **UI:** `/bots` → `+ ADD BOT`. Same evaluators are imported by live
  `RealBotManager`, except Copy Trader (live loop refuses it).
- **Paper fill:** live CLOB top-of-book; taker fee `7% × shares × min(p, 1−p)`;
  settlement by official (or provisional local) outcome. No CLOB POST.
- **Markets:** XRP, BTC, ETH, SOL, DOGE (Copy Trader also copies non-crypto
  into a paper portfolio).
- Longer per-strategy writeup: [STRATEGIES.md](STRATEGIES.md).

## Shared rules

- Almost every strategy holds **one position per market**. Exceptions:
  `dip_harvester` (UP+DOWN book) and Copy Trader non-crypto portfolio.
- Many strategies sell when bid ≥ 99¢ (`lock_profit_if_bid`).
- Unreliable PTB (`ptb_source == exchange_approx`) blocks entry for TWAP-based
  strategies (`twap_lockin`, `twap_lockin_cut`, `cross_line`, `cross_hunter`,
  Winning Hold projection).
- Paper bots do **not** need a wallet to simulate fills, but official TWAP /
  Chainlink still need PolyBolt auth (stored `/real` account **or**
  `POLYMARKET_PRIVATE_KEY`). Without it the TWAP chart is empty and TWAP
  strategies will not enter.

## Signal / entry / exit

| Strategy | Code id | Signal | Entry | Exit | Note |
| --- | --- | --- | --- | --- | --- |
| TWAP Lock-In | `twap_lockin` | Projected final TWAP vs PTB (bps) | Last N s, ask ≤ max ¢, 1×/round | Hold → settlement; sell @ ≥99¢ | Main math edge |
| TWAP Lock-In + Cut | `twap_lockin_cut` | Same + adverse projection | Like Lock-In (default window 90s) | Cut loss after confirm; else settle | Seed bot on empty DB |
| Winning Hold | `winning_hold` | Projection favor near 50¢ | Early entry, ≥60s left | Switch side / settle | Up to `max_switches` |
| Cross Line | `cross_line` | Sign flip TWAP↔PTB (> `min_cross_bps`) | Immediately on cross side | TP / SL / optional mom-rev / settle | Up to `max_entries` |
| Next Round Scalp | `next_round_scalp` | Momentum at end of previous round | First N s of new round ~50¢ | TP / SL / `max_hold`; optional continuous | Carry `wid-1` → `wid` |
| Copy Trader | `copy_trader` | Leader wallet trades (activity feed) | Mirror BUY/SELL, `asset=ALL` | Copy sells / redeem portfolio | Crypto + other markets; **live blocked** |
| Dip Harvester | `dip_harvester` | Ask << recent high (both sides) | Buy dips UP and DOWN | Hold to settlement (book) | Pairs <100¢ lock profit |
| Pressure Scalp | `pressure_scalp` | \|pressure score\| ≥ threshold | Pressure side, ¢ band | TP / SL / settle | Code note: live 0W/11L |
| Momentum Rider | `momentum_rider` | Spot momentum bps / window | Move side, ¢ band | TP / SL / settle | Quote lag vs spot |
| Cross Hunter | `cross_hunter` | Sign flip TWAP↔PTB (>0.5 bps fixed) | Cross side, ask ≤ max | TP / SL / settle | Simplified Cross Line |

## Defaults (from `STRATEGIES` params)

| ID | Param | Default | Range |
| --- | --- | --- | --- |
| twap_lockin | entry_window_s | 55 | 15–240 |
| twap_lockin | min_margin_bps | 2.5 | 0.1–50 |
| twap_lockin | max_entry_cents | 88 | 50–95 |
| twap_lockin | order_usd | 1 | 1–500 |
| twap_lockin_cut | entry_window_s | 90 | 15–240 |
| twap_lockin_cut | min_margin_bps | 1.5 | 0.1–50 |
| twap_lockin_cut | max_entry_cents | 88 | 50–95 |
| twap_lockin_cut | cut_margin_bps | 0.5 | 0–10 |
| twap_lockin_cut | cut_confirm_s | 2 | 0–15 |
| twap_lockin_cut | order_usd | 1 | 1–500 |
| winning_hold | entry_min/max_cents | 40 / 60 | 20–60 / 40–80 |
| winning_hold | conf_bps | 0.5 | 0–10 |
| winning_hold | switch_margin_bps | 1.2 | 0.2–20 |
| winning_hold | switch_confirm_s | 4 | 1–30 |
| winning_hold | switch_max_cents | 78 | 50–95 |
| winning_hold | stop_switch_s | 20 | 5–120 |
| winning_hold | max_switches | 3 | 0–8 |
| winning_hold | order_usd | 1 | 1–500 |
| cross_line | min_cross_bps | 0.5 | 0.1–10 |
| cross_line | max_entry_cents | 78 | 5–99 |
| cross_line | tp_cents | 5 (0=off) | 0–50 |
| cross_line | use_momrev | 0 | 0–1 |
| cross_line | rev_bps / rev_confirm_s | 2.0 / 2 | 0.3–20 / 0–15 |
| cross_line | sl_cents | 12 | 2–50 |
| cross_line | order_usd / max_entries | 1 / 2 | 1–500 / 1–10 |
| next_round_scalp | signal_bps_min | 2.0 | 0.3–20 |
| next_round_scalp | capture_last_s | 8 | 3–30 |
| next_round_scalp | entry_window_s | 20 | 5–60 |
| next_round_scalp | entry_min/max_cents | 49 / 51 | 30–55 / 45–70 |
| next_round_scalp | tp / sl / max_hold_s | 3 / 4 / 45 | ¢ / ¢ / 10–240s |
| next_round_scalp | continuous / max_entries | 0 / 4 | 0–1 / 1–12 |
| next_round_scalp | re_entry_min/max_cents | 40 / 60 | 10–60 / 40–90 |
| next_round_scalp | order_usd | 1 | 1–500 |
| copy_trader | wallet | (text 0x…) | required |
| copy_trader | copy_sells | 1 | 0–1 |
| copy_trader | min_leader_size | 20 sh | 0–10000 |
| copy_trader | max_copy_delay_s | 4 | 1–30 |
| copy_trader | min_copy_gap_s | 3 | 0–60 |
| copy_trader | max_entry_cents | 95 | 5–99 |
| copy_trader | order_usd | 1 | 1–500 |
| copy_trader | slippage_cents | 1 | 0–10 |
| copy_trader | max_open | 10 | 1–50 |
| dip_harvester | dip_cents | 12 | 3–40 |
| dip_harvester | lookback_s | 60 | 10–240 |
| dip_harvester | max_price_cents | 80 | 10–95 |
| dip_harvester | order_usd / per_side_max_usd | 1 / 3 | 1–100 / 1–1000 |
| dip_harvester | min_gap_s / no_buy_last_s | 4 / 15 | 0–60 / 5–60 |
| pressure_scalp | pressure_min | 65 | 30–95 |
| pressure_scalp | entry_min/max_cents | 35 / 68 | 1–95 / 5–99 |
| pressure_scalp | tp / sl | 10 / 14 | 1–50 |
| pressure_scalp | order_usd / max_entries | 1 / 3 | 1–500 / 1–10 |
| momentum_rider | mom_bps_min / mom_window_s | 2.5 / 1 | 0.5–50 / 1–10 |
| momentum_rider | entry_min/max_cents | 30 / 75 | 1–95 / 5–99 |
| momentum_rider | tp / sl | 8 / 8 | 1–50 |
| momentum_rider | order_usd / max_entries | 1 / 3 | 1–500 / 1–10 |
| cross_hunter | max_entry_cents | 78 | 5–99 |
| cross_hunter | tp / sl | 10 / 10 | 1–50 |
| cross_hunter | order_usd / max_entries | 1 / 2 | 1–500 / 1–10 |

Paper `/bots` settings gear can edit these. Real `/real` card edits **LOT (`order_usd`) only**.

## Evaluator mechanics (short)

| Code id | Mechanics |
| --- | --- |
| twap_lockin | remaining ∈ [10s, entry_window]; \|projected−PTB\| ≥ min_margin; BUY UP/DOWN; hold |
| twap_lockin_cut | Entry like lockin; if projection against ≥ cut_margin for cut_confirm_s → market sell |
| winning_hold | Mid-round ask ∈ [min,max]; adverse → sell+buy other (switch / stop_switch_s caps) |
| cross_line | Track sign(TWAP−PTB); on sign change through threshold → BUY; exits TP/SL/momrev |
| next_round_scalp | End of round writes end_signal; start of next → BUY; continuous: re-scalp 1s mom |
| copy_trader | Activity feed by wallet; crypto → live book; else gbuy/gsell + portfolio redeem. Live: `copy_trader not supported on real yet` |
| dip_harvester | For UP and DOWN: if ask ≤ high−dip_cents → buy_book; cap per_side; no buys last N s |
| pressure_scalp | score = composite pressure; \|score\|≥min → BUY; cooldown 5s; TP/SL |
| momentum_rider | momentum_bps(window) ≥ trigger → BUY; cooldown 5s; TP/SL |
| cross_hunter | Like cross_line, threshold hardcoded 0.5 bps; TP/SL only, no mom-rev |

Fresh empty DB seeds one paper bot: **BTC Lock In Small Loss** (`twap_lockin_cut`, BTC, `order_usd=1`).
