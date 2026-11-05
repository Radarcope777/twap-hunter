# PHASE 2B.1 — OFFLINE REPLAY PARITY

Read-only replay of the Phase 2A tape. Production bots, settings, balances, trades, and research rows were not modified. The live collector was not restarted.

## A. Executive Summary

The offline engine reproduces production **decisions** on the clean universe: same side, same exit class, and entry times within about a second on the median. It does not reproduce **dollar P&L**. Snapshot `ask_size` at ~1 Hz is not the size the 250 ms loop filled, so replay share counts (and therefore P&L) diverge by hundreds of dollars on a handful of windows.

Projected-final reconstruction on clean windows matches stored snapshots (median error 0, max 0.11). Replay is deterministic and does not read the outcome until settlement. Bot A cuts match on 20 of 21 production cut windows. Two Bot A rows end on the opposite side; at the production entry timestamp the reconstructed margin has the production sign, and the later flip is a balance rejection against an oversized snapshot size.

PHASE 2B.1 PARITY: FAIL

Phase 2B.2 parameter search on P&L is not safe until sizing is solved. No production change was made.

## B. Replay Architecture

Package `backend/research/replay/` (`models`, `loader`, `quality`, `market_state`, `strategy`, `engine`, `parity`). CLI `scripts/phase2b1_replay.py`.

The loader opens SQLite with `mode=ro` and does not import `backend.engine.bots` or `backend.storage`. Strategy gates are a copy of production `twap_lockin` / `twap_lockin_cut`, including the oddities: a failed cut sell still clears `adverse_since`, and `margin is None` while in a position does not clear the timer.

Mode A and Mode B share `replay_bot`. That function never reads production fills. Production trades are compared only in `parity.py` after the run. `capital_mode=normalized` exists and was not used for scoring.

One replay bug was fixed before the scored run: oracle samples were kept only if `source_ts` was within 75 s of `window_end`. `value_at(window_end)` needs the last price from before that interval so it can be held forward. Bot A enters with ~90 s left, so its margin was `None` until too late. The floor is now `window_start - (lookback + 30 s)`. This change is inside the replay package only.

## C. Data Cutoff

Captured once at process start as `MAX(research_market_snapshots.t_ms)`.

- `cutoff_ms`: **1791542085154.99**
- UTC: **2026-10-09 10:34:45.155**
- Local (UTC+3): **2026-10-09 13:34:45**

Every snapshot and oracle read uses `t_ms` / `receive_ts_ms <= cutoff`.

## D. Clean Replay Universe

A window is eligible only when all of the following hold:

- completed (`window_end` before the newest poly time on the tape)
- fully captured: first remaining ≥ 290 s, last remaining ≤ 15 s, span ≥ 270 s, ≥ 200 snapshots
- PTB present and not `exchange_approx` on ≥ 95% of snapshots
- ≥ 100 Chainlink source timestamps inside `[window_end - 300 s, window_end]`
- snapshot span ≥ 240 s and max gap ≤ 10 s
- a settings version created before the window
- at least one decision row for that bot
- not inside the stall range 5971701–5971731
- an official or local outcome exists

Quote presence is not a window filter. The candidate side is checked at the decision.

| | |
| --- | --- |
| snapshot windows | 248 |
| clean windows | **184** |
| clean UP | **91** |
| clean DOWN | **93** |
| eligible for A only or B only | 0 |

## E. Excluded Windows

**64** windows are not in the clean universe. Rows were not deleted. Reason tags overlap.

| Reason | Windows |
| --- | ---: |
| missing_outcome | 59 |
| EXCLUDED_DATA_QUALITY | 31 |
| missing_outcomes_region | 31 |
| oracle_reconstruction_risk | 31 |
| pre_outage_region | 31 |
| oracle_insufficient | 30 |
| decisions_missing | 28 |
| not_fully_captured | 4 |
| snapshot_insufficient | 4 |
| ptb_invalid | 1 |
| settings_missing | 1 |
| not_completed | 1 |

The stall block 5971701–5971731 is marked `EXCLUDED_DATA_QUALITY` plus the three region tags. It is not scored.

## F. Data Quality Mask

`research_output/phase2b1_window_quality.csv` has one row per snapshot window: capture flags, outcome, snapshot count, max gap, per-bot eligibility, and exclusion reasons.

## G. Time / Event Model

For each window the cursor merges snapshots and Chainlink points with `first_snap_t < receive_ts <= last_snap_t`. At the same timestamp the snapshot is applied before the oracle point. Quotes are carried forward. `poly_now = wall - offset`, with `offset` taken from the latest snapshot (`t_ms - (window_end - remaining_ms)`). The strategy steps on every event after the first quote. Settlement, if a position is still open and an outcome exists, is applied at the last snapshot time. Outcome is not an input to `step`.

## H. No-Look-Ahead Design

- Oracle points with `receive_ts_ms` above the replay wall time are not in the TWAP sample set.
- `MarketState` has no outcome field.
- `settle()` is the only reader of the round outcome, and it runs after the window's states are consumed.
- Unit test `test_no_lookahead_oracle_receive_time`: a future receive at 950000 does not change state at 900000, and does change state after a snapshot at 960000.

No-look-ahead verified: **YES**

## I. Production Strategy Reconstruction

Entry window `10_000 <= remaining_ms <= entry_window_s * 1000`, plus the buy-path rejects for remaining < 5 s or age < 2 s. Side is UP when projected margin > 0, else DOWN. Margin below `min_margin_bps` does not enter. Ask must be in `(0, max_entry_cents]`, bid present, spread ≤ 20. Shares = `order_usd / ask`, capped by `ask_size` when the snapshot has a size, minimum 1 share. Fee `0.07 * shares * min(p, 1-p)`. Reject when `cost + fee` exceeds the seeded wallet. One entry per window. Profit lock when the held bid ≥ 99, before the cut check. Cut: adverse for `cut_confirm_s`, then sell, then clear the timer even if the sell fails. Settlement pays $1 or $0 per share with no exit fee.

Lock-profit at 99¢ is the production constant, not a settings field.

## J. Bot A Replay Settings

From `research_settings_versions` id 1, bot `b_0d55c464`, strategy `twap_lockin_cut`.

| param | value |
| --- | --- |
| entry_window_s | 90 |
| min_margin_bps | 0.3 |
| max_entry_cents | 95 |
| cut_margin_bps | 0.5 |
| cut_confirm_s | 2 |
| order_usd | 100 |
| profit lock | 99¢ (code constant) |

## K. Bot B Replay Settings

From `research_settings_versions` id 2, bot `b_384a1650`, strategy `twap_lockin`. No cut parameters.

| param | value |
| --- | --- |
| entry_window_s | 60 |
| min_margin_bps | 2.5 |
| max_entry_cents | 90 |
| order_usd | 100 |
| profit lock | 99¢ (code constant) |

## L. Projected-Final Parity

Clean-universe snapshots where both stored and reconstructed finals exist:

| | Bot A | Bot B |
| --- | ---: | ---: |
| compared rows | 47050 | 47050 |
| exact | 46984 | 46984 |
| stored final with no reconstruction | 159 | 159 |
| median final error | 0 | 0 |
| max final error | 0.110 | 0.110 |
| max margin error (bps) | 0.0134 | 0.0134 |

Projected-final parity on clean data: **PASS**

The 159 skipped rows are snapshots that still had no usable sample. They are 0.3% of clean snapshots and are not the stall block.

## M. Entry Parity — Bot A

Confusion matrix on all 184 clean windows:

| both | missed | extra | neither |
| ---: | ---: | ---: | ---: |
| 80 | 8 | 1 | 95 |

Wrong side among the 80 paired entries: **2**. Side agreement 78/80 = **97.5%**. Entry agreement `(both + neither) / clean` = **95.1%**.

Median signed entry time delta (replay − production): **+399 ms**. Median absolute entry price delta: **1¢**. 27 paired entries differ by more than 2¢. p95 absolute entry delta **17.2 s** is the late tail (the two opposite-side rows and a few size-blocked delays), not the median.

Missed production entries (all profit-target wins, small P&L): 5971537, 5971546, 5971548, 5971593, 5971595, 5971596, 5971626, 5971633. Extra replay entry: 5971774 DOWN, cut, +0.34.

## N. Entry Parity — Bot B

| both | missed | extra | neither |
| ---: | ---: | ---: | ---: |
| 30 | 3 | 1 | 150 |

Wrong side: **0**. Side agreement **100%**. Entry agreement **97.8%**.

Median entry time delta **+37 ms**. p95 absolute **796 ms**. Median absolute entry price delta **1¢**, max **7¢**.

Missed: 5971552, 5971676, 5971768 (settlement win +89.35, 196 shares). Extra: 5971559 UP profit-target +9.14.

## O. Exit Parity — Bot A

Among the 78 same-side paired entries, exit class matches **100%**. Exit price within 0.11¢ on **85.9%** of those pairs. Median exit time delta **+361 ms**. Five exits differ by more than 20 s.

## P. Exit Parity — Bot B

Among 30 same-side pairs, exit class matches **100%** and exit price matches within 0.11¢ on **100%**. Median exit time delta **+375 ms**. Six exits differ by more than 20 s. Those are settlement timestamps: replay settles at the last snapshot, production settles when the official result arrives. Prices on those rows are 100 or 0 on both sides.

## Q. Bot A Cut Parity

| production cuts | replay cuts | matched windows | missed | extra |
| ---: | ---: | ---: | ---: | ---: |
| 21 | 22 | 20 | 1 | 2 |

Missed cut 5971586 and extra cut 5971645 are the two opposite-side rows below. Extra cut 5971774 is the extra entry. Same-side pairs do not disagree on cut versus profit versus settlement.

## R. Settlement Parity

Same-side settlement rows keep the production win/loss class.

- Bot A 5971664 DOWN, official loss. Production −101.43 on 120.48 shares. Replay −26.94 on 32 shares (snapshot ask size 32 at 83¢). Replay did **not** open a cut. Reconstructed margin at the entry snapshot is −0.65 bps, which does not cross the 0.5 bps adverse threshold.
- Bot B 5971564 UP held to `settlement_loss` (−4.67 production, −4.54 replay). No cut.
- Bot B 5971597 DOWN held to `settlement_loss` (−75.21 on 82.92 shares production, −13.61 on 15 shares replay). No cut.

Hypothetical Bot-A-style cut (0.5 bps adverse for 2 s) on the Bot B trajectories, not included in parity P&L:

| window | crossed 0.5 bps for 2 s | bid present | extreme margin |
| --- | --- | --- | --- |
| 5971564 UP | YES | YES | −0.78 bps |
| 5971597 DOWN | YES | YES | +2.04 bps |

A later study can evaluate that cut from this tape. This run did not.

## S. P&L Parity

| | Bot A | Bot B |
| --- | ---: | ---: |
| production P&L | −25.07 | 511.43 |
| replay P&L | 58.17 | 1240.23 |
| difference (replay − production) | +83.24 | +728.80 |
| production W/L | 67 / 21 | 31 / 2 |
| replay W/L | 59 / 22 | 29 / 2 |

The Bot B gap is one window. 5971578 DOWN settled a win: production filled **20** shares at 13¢ (+17.22). The snapshot next to that fill shows ask 12¢ with size **866**, so replay buys the full $100, **833** shares, +726.33. Seeded balance was $877, so this is not a wallet bug. The 1 Hz size is not the size production traded.

Other large share-ratio rows (replay / production), same pattern: Bot B 5971585 (2.69 → 126.6 shares), 5971610 (33.5 → 163.9), 5971765 (6.6 → 116.3). Bot A examples: 5971664 (120.5 → 32), 5971655 (138.9 → 10), 5971658 (58 → 156).

## T. False Positive / Extra Entry Analysis

One extra entry per bot.

- Bot A 5971774 DOWN, cut, +0.34. Production did not buy this window.
- Bot B 5971559 UP, profit target, +9.14. Production did not buy this window.

These are single windows, not a population. They are still real extras and are scored as `EXECUTION_LOOP_TIMING` mismatches.

The two Bot A opposite sides are not extra entries. Production and replay both entered. At the production timestamp:

| window | production | margin at that snapshot | why the $100 order was rejected | later replay fill |
| --- | --- | --- | --- | --- |
| 5971586 | UP 49¢, 14.42 sh | +0.38 bps UP | seed balance $9.17, snapshot size 738 wants $100 | 17 s later DOWN 91¢, 2.41 sh |
| 5971645 | DOWN 33¢, 132.55 sh | −0.96 bps DOWN | seed balance $74.31, snapshot size 835 wants $100 | 15 s later UP 80¢, 8.47 sh |

Production's smaller fill would have fit the wallet. The snapshot size did not cap the order, the balance check failed, and the next affordable quote had the other sign. Classifier tag `REPLAY_LOGIC_BUG` is what the pre-registered rule emits for any side mismatch. Investigation: margin sign at the production entry matches. Cause is `BALANCE_STATE` together with quote-size resolution.

## U. Mismatch Root Causes

Pre-registered classifier counts on scored rows (paired entries, misses, extras):

| reason | Bot A | Bot B |
| --- | ---: | ---: |
| QUOTE_RESOLUTION | 27 | 8 |
| SNAPSHOT_RESOLUTION | 26 | 12 |
| EXECUTION_LOOP_TIMING | 9 | 4 |
| REPLAY_LOGIC_BUG (raw tag) | 2 | 0 |
| UNKNOWN | 0 | 0 |

After investigation the two `REPLAY_LOGIC_BUG` rows are `BALANCE_STATE` (section T). Unexplained logic mismatches: **0**. Unknown mismatches: **0**.

`QUOTE_RESOLUTION` here means same side and exit class with entry price more than 2¢ off. `SNAPSHOT_RESOLUTION` is the same economics class outside the near-match time/price band, including settlement clocks and size-driven P&L with a matching exit class.

## V. Determinism

At the fixed cutoff, Bot A `replay_bot` was run twice. Window, side, entry price, shares, exit class, exit price, and P&L matched. Unit test `test_determinism_on_synthetic_tape` also passed.

Replay engine deterministic: **YES**

## W. Replay Performance

| | windows | snapshots | oracle rows | seconds | windows/s |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bot A | 184 | 47209 | 63476 | 8.79 | 20.9 |
| Bot B | 184 | 47209 | 63476 | 8.01 | 23.0 |

Tape load is extra and is not inside those two timers. No parameter grid was run.

## X. Tests

`tests/test_replay_phase2b1.py`: **11 passed** in 0.08 s.

Covered: margin sign, entry window, margin threshold, price cap, DOWN entry, one-entry and position gate, profit at 99 not 98, missing ask, one-sided book, cut start/reset/confirm for UP, cut timer for DOWN, Bot B does not cut, settlement win and loss, fee and sizing, failed cut clears the timer, stall window excluded, no look-ahead, determinism, `exchange_approx` blocks margin.

## Y. Known Limitations

- Quotes and sizes are snapshot-resolution. Fills between snapshots are invisible. That is the source of the P&L gap.
- Each window's wallet is the balance on the last persisted trade before the window's first snapshot, not a single running replay wallet. A miss does not change later windows' cash. It also does not recover an in-memory balance that was never written. Bot A is often near zero, so a $100 order at a large snapshot size is rejected even when production filled a smaller book.
- Settlement time is the last snapshot, not the official publish time. Exit class and price still match; the clock can be tens of seconds off.
- 159 clean snapshots have a stored projected final and no reconstruction.
- Mode B is implemented as the same stepper without production fills. It was not used to search parameters.

## Z. Phase 2B.1 Verdict

Decision parity (side, exit class, cut, projected final, determinism, no look-ahead) is strong. Dollar parity is not. One Bot B window moves P&L by +$709 because snapshot size was 866 shares and production filled 20. That is large enough to fail the bar. The criteria were not relaxed.

PHASE 2B.1 PARITY: FAIL

What must be fixed before Phase 2B.2, without changing live trading:

1. Position size. Counterfactual P&L cannot treat ~1 Hz `ask_size` as the fill size. Either record a finer book-size tape for research, or define an explicit size convention for 2B.2 (and exclude or separately report windows where the snapshot size and the production fill size disagree). Do not change Bot A/B order logic to do this.
2. Re-score the two opposite-side Bot A windows after that sizing fix. They are balance rejections against the oversized snapshot, not a margin-sign bug.

---

# 41. Final summary table

| Metric | Bot A | Bot B |
| --- | ---: | ---: |
| clean windows | 184 | 184 |
| production entry windows | 88 | 33 |
| replay entry windows | 81 | 31 |
| matched entries | 80 | 30 |
| missed production entries | 8 | 3 |
| extra replay entries | 1 | 1 |
| wrong-side entries | 2 | 0 |
| entry agreement | 95.1% | 97.8% |
| median entry timing delta | +399 ms | +37 ms |
| same exit reason | 100% | 100% |
| P&L production | −25.07 | 511.43 |
| P&L replay | 58.17 | 1240.23 |
| P&L difference | +83.24 | +728.80 |

Bot A cuts:

```
production cuts  21
replay cuts      22
matched cuts     20
missed cuts       1
extra cuts        2
```

# 42. Final verdict

```
PHASE 2B.1 PARITY: FAIL
```

```
Replay engine deterministic: YES
No-look-ahead verified: YES
Projected-final parity on clean data: PASS

Bot A entry parity acceptable: YES
Bot B entry parity acceptable: YES
Bot A cut parity acceptable: YES

Unexplained replay logic mismatches: 0
Unknown mismatches: 0

Safe to start Phase 2B.2 parameter research: NO

Live collector should remain running: YES
Live Bot A/B settings changed: NO
```

Phase 2B.2 is not safe because replay dollar P&L is not production dollar P&L. Fix position-size fidelity first (section Z). Do not change production trading behavior to do it.
