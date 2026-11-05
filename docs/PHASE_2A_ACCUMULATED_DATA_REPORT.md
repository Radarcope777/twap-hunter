# Phase 2A — Accumulated Data Report

Read-only review of the BTC TWAP research tape. No code, database rows, bot settings, or processes were changed. No parameter search was run.

Counts below come from a read-only SQLite pass a few minutes after `scripts/research_quality.py`. The script output is copied in full and is slightly earlier.

## A. Executive Summary

The collector has been up since the 8 Oct 2026 14:45 restart. It has about 18.9 hours of wall clock, 201 completed BTC windows, and 198 windows captured from about T-300 through the end of the round.

That meets the working checkpoint for an initial Phase 2B replay (100–150 fully captured windows), with 88 Bot A trades and 29 Bot B trades after instrumentation, including 21 completed Bot A cuts. It does not meet the 300–500 window checkpoint for broader conclusions.

Two defects bound how the tape should be used. Snapshots stopped for 2.29 hours while the same backend process stayed alive, and the 30 completed windows just before that hole have no outcome row. Rebuilding `projected_final` from stored Chainlink samples failed on 8 of 100 sampled snapshots, all inside that pre-gap stretch. Decision gates on 100 rows per bot matched the snapshots exactly.

## B. Runtime / Current Bot State

Backend: `GET /health` returned `ok=true`, `ready=true`, `bots=5`. The uvicorn process on port 9000 was created 8 Oct 2026 14:45:56 and was still that process during this check. Uptime is about 18.9 hours. It was not restarted for this report.

| | Bot A `b_0d55c464` | Bot B `b_384a1650` |
| --- | --- | --- |
| status | running | running |
| strategy | `twap_lockin_cut` | `twap_lockin` |
| entry_window_s | 90 | 60 |
| min_margin_bps | 0.3 | 2.5 |
| max_entry_cents | 95 | 90 |
| cut | 0.5 bps / 2 s | — |
| order_usd | 100 | 100 |
| runtime balance | 0.66 | 1246.07 |
| start balance | 1000 | 1000 |
| persisted trades | 1178 | 170 |

Runtime parameters match `research_settings_versions` id 1 and id 2, and they match the JSON in the `bots` table. Settings did not change during collection. There is one version per bot, both written at instrumentation boot (`created_ms` 1791459962474 and 1791459962511).

The persisted `bots` row is behind the live process. Bot A `updated_ms` is 1791504257974 and Bot B is 1791507887967, about 6.6 h and 5.6 h before the latest research snapshot. Live balances above are from `GET /api/bots`. The SQLite balances at the same moment were 0.99 and 1074.83. Research decisions and events continue through the latest snapshot. The trade-performance section uses the `trades` table only.

## C. Collection Period

| | |
| --- | --- |
| first research snapshot | 1791459962473 (instrumentation boot, 8 Oct 2026 14:46 local) |
| latest research snapshot | 1791528022032 |
| elapsed | 18.91 hours (0.79 days) |
| BTC windows touched | 202 |
| completed | 201 |
| open | 1 (`5971760`) |

Fully captured completed windows: **198**. Definition: completed, first snapshot remaining at least 290 s, last remaining at most 15 s, span at least 270 s.

Not fully captured:

| window | why |
| --- | --- |
| 5971533 | instrumentation started mid-round, first remaining 238 s, one 11.2 s gap |
| 5971731 | only 10 snapshots, then the long outage |
| 5971758 | collection resumed mid-round, first remaining 153 s |

## D. Research Table Counts

`scripts/research_quality.py` complete output:

```
db D:\TEST\twap-hunter\data\twaphunter.db
rounds_with_snapshots 201
snapshots 51584
oracle_samples 108009
decisions 95168
execution_events 80835
settings_versions 2
missing_ptb 0.0%
missing_projected_final 0.0%
missing_quote 10.1%
stale_oracle_gt_5s 0.6%
stale_quote_gt_5s 0.3%
snapshot_gap_ms largest 11229 median 1144 p95 1291
replayable_snapshots 80.1%
```

Independent SQLite a few minutes later:

| table | rows | earliest | latest | duration | distinct windows |
| --- | --- | --- | --- | --- | --- |
| research_settings_versions | 2 | 1791459962474 | 1791459962511 | 38 ms | n/a |
| research_market_snapshots | 51617 | 1791459962473 | 1791528025696 | 18.91 h | 202 |
| research_oracle_samples | 108083 | source 1791459895000 | source 1791528013000 | 18.92 h | not keyed |
| research_bot_decisions | 95234 | 1791459962473 | 1791528025696 | 18.91 h | 187 |
| research_execution_events | 81027 | 1791460420244 | 1791528002476 | 18.77 h | 179 |

The script's `replayable_snapshots` 80.1% is the share of snapshot rows with PTB, projected final, both asks, and a Chainlink timestamp. It is not the window-level replay rate in section I.

## E. Database Growth

| file | bytes |
| --- | --- |
| twaphunter.db | 73,216,000 |
| twaphunter.db-wal | 8,627,312 |
| twaphunter.db-shm | 32,768 |

At the post-restart validation (~17 minutes into collection) the database plus WAL was 6.54 MB. It is now 78.1 MB combined. Measured growth of the whole file, which also holds the older trade history, is about 71.8 MB over the remaining ~18.6 hours.

| | measured extrapolation |
| --- | --- |
| MB/hour | 3.9 |
| MB/day | 93 |
| 7-day size | ~0.65 GB |
| 30-day size | ~2.8 GB |

This sits in the earlier 90–120 MB/day band. It is a whole-database rate, not a research-only page count.

## F. Snapshot Quality

Completed windows only (51,408 intervals):

| median | p95 | p99 | max | >1.5 s | >2 s | >5 s | >10 s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1144 ms | 1291 ms | 1663 ms | 11229 ms | 766 | 159 | 2 | 1 |

Snapshots per completed window:

| median | p10 | p25 | p75 | p90 | min | max |
| --- | --- | --- | --- | --- | --- | --- |
| 257 | 254 | 255 | 258 | 270 | 10 | 291 |

A healthy 5-minute window has about 250–270 snapshots, a little slower than 1.00 Hz. Poor coverage is three windows: `5971731` (10 snapshots), `5971758` (123, resume), `5971533` (203, boot). The only gap above 10 s inside a window is the 11.2 s hole at boot.

## G. Oracle Quality

| | Chainlink | TWAP60 |
| --- | --- | --- |
| samples | 54135 | 53949 |
| median interval | 1000 ms | 1000 ms |
| p95 | 1000 ms | 1000 ms |
| p99 | 2000 ms | 2000 ms |
| max gap | 12896000 ms (3.58 h) | 12896000 ms (3.58 h) |
| gaps >2 s | 68 | 80 |
| gaps >5 s | 37 | 39 |
| gaps >10 s | 4 | 6 |

Source timestamps are monotonic. Prices are finite BTC values (Chainlink 80329–82714). Every oracle row is `asset=BTC`. The multi-hour max gap is the same outage as the snapshot hole, measured on source time.

Snapshot-level:

| | |
| --- | --- |
| missing PTB | 0.0019% (1 row) |
| exchange_approx PTB | 0.39% |
| missing projected_final | 0.0039% |
| Chainlink age >5 s or null | 0.60% |
| TWAP60 age >5 s or null | 0.65% |

Window-level, among 201 completed windows: 1 window has any null PTB, 1 has any `exchange_approx`, 1 has any null projected final, 40 have at least one Chainlink age above 5 s.

## H. Quote Quality

| | |
| --- | --- |
| stale quote >5 s or null age | 0.35% |
| UP ask missing | 10.2% |
| UP bid missing | 9.7% |
| DOWN ask missing | 9.7% |
| DOWN bid missing | 10.2% |
| both asks missing | 0.006% |
| one-sided book (bid present xor ask present, either side) | 19.9% |

Usable ask (price strictly between 0 and 100) for at least one side, completed windows:

| band | snapshots | either side | UP ask | DOWN ask |
| --- | --- | --- | --- | --- |
| T-90 to T-10 | 13756 | 100% | 67.7% | 69.5% |
| T-90 to T-60 | 5153 | 100% | 82.2% | 79.8% |
| T-60 to T-45 | 2586 | 100% | 67.7% | 69.8% |
| T-45 to T-30 | 2590 | 100% | 58.9% | 64.4% |
| T-30 to T-10 | 3427 | 100% | 52.4% | 57.6% |

The book is often locked on one side, and that gets worse inside the last half-minute. An ask for some side is still present on every sampled row in these bands.

## I. Window Replayability

Original rules from `docs/PHASE_2A_POST_RESTART_VALIDATION.md`, unchanged:

REPLAYABLE requires PTB present and not `exchange_approx`, span at least 240 s and at least 200 snapshots, no snapshot gap above 5 s, UP ask missing on at most 10% of rows, at least 100 Chainlink samples, projected final missing on at most 10%, a settings version on at least one decision, and an official or local outcome. PARTIAL means some of those hold. NOT REPLAYABLE means missing PTB and thin oracle together, or span under 60 s.

| | n | % of 201 |
| --- | --- | --- |
| completed | 201 | 100% |
| REPLAYABLE | 104 | 51.7% |
| PARTIAL | 96 | 47.8% |
| NOT REPLAYABLE | 1 | 0.5% |

Reason counts (a window can have several):

| reason | windows |
| --- | --- |
| quote holes (UP ask null > 10%) | 82 |
| missing outcome | 30 |
| insufficient oracle | 16 |
| missing settings version | 15 |
| snapshot gaps | 2 |
| short snapshot coverage | 2 |
| missing PTB | 1 |
| exchange_approx PTB | 1 |

The UP-ask rule is defective for this tape. Fully captured windows are balanced (83 UP / 86 DOWN), but original REPLAYABLE windows are 19 UP / 85 DOWN, because a locked UP book drops the UP ask. A recount a few minutes later, same rules, was 104 REPLAYABLE / 97 PARTIAL / 1 NOT REPLAYABLE on 202 completed windows. Of those, 65 failed the original rules only because of the UP-ask test.

Proposed rule, shown beside the original and not used as the headline rate: call a quote hole only when both asks are missing on more than 10% of rows. That recount classified 169 REPLAYABLE, 32 PARTIAL, 1 NOT REPLAYABLE.

## J. Outcome Coverage

Completed research windows:

| | n |
| --- | --- |
| official outcome | 168 |
| local only | 3 |
| no rounds row | 30 |
| official/local conflict | 0 |

Fully captured windows that have a label: 83 UP, 86 DOWN. The underlying sample is not one-sided. The original replayable subset is one-sided because of the UP-ask rule, not because the market was.

The 30 missing outcomes are windows `5971701`–`5971731` except `5971706` (no snapshots). They sit immediately before the long outage.

## K. Settings Versions

Settings never changed. Do not split performance by version.

| id | bot | decisions | windows | ENTRY_SUCCESS since version |
| --- | --- | --- | --- | --- |
| 1 | A `twap_lockin_cut` | 47620 | 187 | 88 |
| 2 | B `twap_lockin` | 47620 | 187 | 30 |

Fifteen snapshot windows have no decision row (`missing_settings` in the replay reasons). They are part of the stall, not a second parameter set.

## L. Execution Events

| event | count |
| --- | --- |
| ENTRY_ATTEMPT | 40318 |
| ENTRY_REJECTED | 40199 |
| ENTRY_SUCCESS | 118 |
| PROFIT_SELL_ATTEMPT | 86 |
| PROFIT_SELL_SUCCESS | 86 |
| PROFIT_SELL_FAILED | 0 |
| SELL_SUCCESS | 107 |
| CUT_TIMER_STARTED | 26 |
| CUT_TIMER_RESET | 5 |
| CUT_CONFIRMED | 21 |
| CUT_SELL_ATTEMPT | 21 |
| CUT_SELL_SUCCESS | 21 |
| CUT_SELL_FAILED | 0 |
| SETTLEMENT_LOCAL | 8 |
| SETTLEMENT_OFFICIAL | 11 |
| SETTLEMENT_ADJUST | 0 |
| SETTLEMENT_VOID | 0 |

There is no `SETTLEMENT_STARTED` event in this build.

Reject reasons: `NO_ASK` 35054, `PRICE_ABOVE_CAP` 2942, `INSUFFICIENT_BALANCE` 2200, `MIN_SHARES` 2, `SPREAD_TOO_WIDE` 1. Bot A’s live balance is under $1 against a $100 order, so balance rejects are expected and were not created for this study.

## M. Post-Instrumentation Bot A Performance

Persisted trades with `t` at or after the first research snapshot. Pre-Phase-2A trades are excluded. Closed PnL is the sum of SELL, SETTLE, and ADJUST on that entry. 88 entries, all closed.

| | |
| --- | --- |
| entries / completed / open | 88 / 88 / 0 |
| W / L | 67 / 21 |
| win rate | 76.1% |
| net realized PnL | -25.07 |
| gross profit / gross loss | 348.38 / -373.45 |
| average win / average loss | 5.20 / -17.78 |
| profit factor | 0.93 |

Exits: profit target 62, cut 21, settlement win 4, settlement loss 1.

These are descriptive. They are not a parameter result.

## N. Post-Instrumentation Bot B Performance

| | |
| --- | --- |
| entries / completed / open | 29 / 29 / 0 |
| W / L | 27 / 2 |
| win rate | 93.1% |
| net realized PnL | 295.70 |
| gross profit / gross loss | 375.58 / -79.88 |
| average win / average loss | 13.91 / -39.94 |
| profit factor | 4.70 |

Exits: profit target 23, settlement win 4, settlement loss 2. No cut exits. Bot B has no cut logic.

`ENTRY_SUCCESS` events are 30 for Bot B versus 29 persisted BUY rows. One success is not in the trade table used here.

## O. Bot A Cut Episodes

| event | count |
| --- | --- |
| CUT_TIMER_STARTED | 26 |
| CUT_TIMER_RESET | 5 |
| CUT_CONFIRMED | 21 |
| CUT_SELL_ATTEMPT | 21 |
| CUT_SELL_SUCCESS | 21 |
| CUT_SELL_FAILED | 0 |

Distinct windows with any cut event: 23. Completed cut sells: 21. Two windows started a timer and then reset it; those exits were a profit-target sell (window `5971585`, pnl 1.81) and a settlement win (`5971610`, pnl 3.07).

`CUT_SELL_SUCCESS` does not store bid or bid size. Those fields are null on every cut-sell row. Shares and PnL below are from the trade row. Confirmation is usually about 2.0–2.5 s, matching `cut_confirm_s` of 2. Three confirms were longer (7.3 s, 5.4 s, 3.9 s) after a reset/restart of the timer.

| window | side | entry price | entry margin bps | adverse margin | confirm s | shares | pnl | official |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5971538 | UP | 58 | 2.19 | -1.91 | 2.10 | 3.84 | -1.45 | DOWN |
| 5971543 | UP | 64 | 0.75 | -0.67 | 2.17 | 30.0 | -14.93 | DOWN |
| 5971547 | UP | 55 | 1.27 | -1.35 | 2.08 | 3.18 | -0.10 | DOWN |
| 5971559 | DOWN | 72 | -2.66 | 0.56 | 2.20 | 15.0 | -9.56 | UP |
| 5971563 | DOWN | 60 | -0.92 | 1.20 | 2.12 | 15.0 | -2.72 | UP |
| 5971564 | UP | 89 | 6.29 | -0.78 | 2.04 | 6.97 | -1.30 | DOWN |
| 5971566 | DOWN | 81 | -5.13 | 0.58 | 2.11 | 12.26 | -7.24 | UP |
| 5971578 | UP | 85 | 9.48 | -0.61 | 2.08 | 6.66 | 0.15 | DOWN |
| 5971580 | DOWN | 53 | -2.48 | 0.58 | 2.27 | 5.51 | -0.69 | DOWN |
| 5971584 | UP | 88 | 9.25 | -1.24 | 2.14 | 10.0 | -3.70 | UP |
| 5971586 | UP | 49 | 0.38 | -0.52 | 2.04 | 14.42 | -2.73 | DOWN |
| 5971597 | DOWN | 48 | -0.23 | 0.73 | 2.45 | 5.0 | -1.27 | UP |
| 5971606 | DOWN | 87 | -8.30 | 0.52 | 7.28 | 5.0 | -3.28 | UP |
| 5971618 | DOWN | 65 | -2.48 | 0.95 | 2.09 | 16.0 | -4.69 | UP |
| 5971619 | UP | 64 | 1.04 | -0.97 | 2.10 | 55.0 | -23.80 | DOWN |
| 5971631 | UP | 55 | 1.22 | -0.73 | 2.22 | 50.0 | -2.61 | DOWN |
| 5971659 | UP | 81 | 0.46 | -0.64 | 2.23 | 88.59 | -53.16 | DOWN |
| 5971667 | DOWN | 83 | -3.30 | 0.50 | 5.40 | 120.48 | -99.19 | UP |
| 5971671 | UP | 53 | 0.92 | -0.57 | 3.86 | 60.0 | -31.54 | DOWN |
| 5971678 | UP | 86 | 2.69 | -2.09 | 2.07 | 5.65 | -1.50 | DOWN |
| 5971680 | DOWN | 70 | -1.36 | 0.94 | 2.17 | 10.0 | -6.56 | UP |

Sum of these 21 cut PnLs: -271.87. One cut (`5971578`) is a small positive. No cut sell failed.

## P. Settlement Loss Analysis

Three persisted post-instrumentation trades settled as losses. Trajectory is snapshot `projected_margin_bps` after the entry. For an UP position, adverse means the margin went below -0.5 bps. For DOWN, adverse means it went above +0.5 bps. That is Bot A’s threshold, applied here as a measurement also on Bot B, which does not cut.

| | Bot A `5971664` | Bot B `5971564` | Bot B `5971597` |
| --- | --- | --- | --- |
| side | DOWN | UP | DOWN |
| entry price | 83¢ | 85¢ | 90¢ |
| remaining at entry | 90.6 s | 60.4 s | 60.2 s |
| entry margin | -0.64 bps | +4.88 bps | -6.43 bps |
| max favorable margin | -1.30 | +5.62 | -6.39 |
| max adverse margin | +0.18 | -2.73 | +2.10 |
| Bot A threshold crossed | no | yes, 27 snapshots, 30.6 s | yes, 22 snapshots, 29.3 s |
| cut attempt | no | no (Bot B has no cut) | no |
| official outcome | UP | DOWN | UP |
| realized loss | -101.43 | -4.67 | -75.21 |

Bot A’s -101.43 loss never crossed the 0.5 bps cut line on the recorded trajectory (worst margin +0.18). Both Bot B losses did cross that line for about half a minute and were held to settlement. No values were filled in where the tape was empty.

## Q. Entry Price Distribution

Small buckets. Not a basis for a price cap. Bot B has no rows in 50–59, 70–74, or 93–95.

Bot A:

| bucket | N | W | L | WR | net | avg |
| --- | --- | --- | --- | --- | --- | --- |
| <50¢ | 4 | 2 | 2 | 50% | 92.63 | 23.16 |
| 50–59¢ | 6 | 1 | 5 | 17% | -9.15 | -1.53 |
| 60–69¢ | 11 | 7 | 4 | 64% | -12.02 | -1.09 |
| 70–74¢ | 10 | 8 | 2 | 80% | 49.59 | 4.96 |
| 75–79¢ | 8 | 8 | 0 | 100% | 41.06 | 5.13 |
| 80–84¢ | 10 | 6 | 4 | 60% | -239.60 | -23.96 |
| 85–87¢ | 12 | 10 | 2 | 83% | 34.48 | 2.87 |
| 88–89¢ | 8 | 6 | 2 | 75% | 2.22 | 0.28 |
| 90–92¢ | 9 | 9 | 0 | 100% | 8.59 | 0.95 |
| 93–95¢ | 10 | 10 | 0 | 100% | 7.13 | 0.71 |

Bot B:

| bucket | N | W | L | WR | net | avg |
| --- | --- | --- | --- | --- | --- | --- |
| <50¢ | 2 | 2 | 0 | 100% | 37.04 | 18.52 |
| 60–69¢ | 4 | 4 | 0 | 100% | 136.82 | 34.21 |
| 75–79¢ | 3 | 3 | 0 | 100% | 38.14 | 12.71 |
| 80–84¢ | 7 | 7 | 0 | 100% | 80.30 | 11.47 |
| 85–87¢ | 2 | 1 | 1 | 50% | 10.48 | 5.24 |
| 88–89¢ | 6 | 6 | 0 | 100% | 43.58 | 7.26 |
| 90–92¢ | 5 | 4 | 1 | 80% | -50.66 | -10.13 |

## R. Entry Margin Distribution

Absolute projected margin at the snapshot nearest the fill. Bot A has no missing bucket. Bot B has no 0.0–0.49 bucket. Several Bot B buckets have N of 1 or 2.

Bot A:

| bps | N | W | L | WR | net | avg |
| --- | --- | --- | --- | --- | --- | --- |
| 0.0–0.49 | 3 | 0 | 3 | 0% | -57.16 | -19.05 |
| 0.5–0.99 | 11 | 7 | 4 | 64% | 8.44 | 0.77 |
| 1.0–1.99 | 14 | 10 | 4 | 71% | 51.46 | 3.68 |
| 2.0–2.99 | 18 | 13 | 5 | 72% | 37.52 | 2.08 |
| 3.0–3.99 | 6 | 5 | 1 | 83% | -82.98 | -13.83 |
| 4.0–4.99 | 5 | 5 | 0 | 100% | 5.66 | 1.13 |
| 5.0–7.49 | 15 | 13 | 2 | 87% | 11.19 | 0.75 |
| 7.5–9.99 | 9 | 7 | 2 | 78% | -3.81 | -0.42 |
| 10+ | 7 | 7 | 0 | 100% | 4.61 | 0.66 |

Bot B:

| bps | N | W | L | WR | net | avg |
| --- | --- | --- | --- | --- | --- | --- |
| 1.0–1.99 | 1 | 1 | 0 | 100% | 48.43 | 48.43 |
| 2.0–2.99 | 9 | 9 | 0 | 100% | 116.99 | 13.00 |
| 3.0–3.99 | 6 | 6 | 0 | 100% | 96.29 | 16.05 |
| 4.0–4.99 | 2 | 1 | 1 | 50% | -1.05 | -0.53 |
| 5.0–7.49 | 9 | 8 | 1 | 89% | 13.28 | 1.48 |
| 7.5–9.99 | 1 | 1 | 0 | 100% | 11.47 | 11.47 |
| 10+ | 1 | 1 | 0 | 100% | 10.29 | 10.29 |

## S. Entry Time Distribution

Remaining time is the nearest snapshot, so a few Bot A fills sit just outside the 75–90 s bin.

Bot A: 75–90 s is 70 trades, 51 W / 19 L, net 78.39. 60–74 s is 10 trades, net -45.66. 50–59 s is 1 win, net 0.26. Outside the bins: 90.2 s (1, +0.21), 90.3 s (3, +34.64), 90.5 s (2, +8.52), 90.6 s (1, -101.43). No entries in 10–49 s.

Bot B: 50–59 s is 17 trades, 17 W, net 241.76. 40–49 s is 5, all wins, net 81.11. 30–39 s is 2 wins, net 15.65. 60–74 s is 5 trades, 3 W / 2 L, net -42.82. No entries below 30 s or above 74 s.

## T. UP vs DOWN

| bot | side | N | W | L | WR | net |
| --- | --- | --- | --- | --- | --- | --- |
| A | UP | 46 | 35 | 11 | 76.1% | 39.28 |
| A | DOWN | 42 | 32 | 10 | 76.2% | -64.35 |
| B | UP | 11 | 10 | 1 | 90.9% | 161.28 |
| B | DOWN | 18 | 17 | 1 | 94.4% | 134.42 |

Window labels on fully captured rounds: 83 UP, 86 DOWN.

## U. Projected Final Reconstruction

100 snapshots, spread across completed windows and remaining times. Chainlink samples with receive time at or before the snapshot were fed through the production rolling TWAP to `window_end`.

| | |
| --- | --- |
| rows tested | 100 |
| exact matches | 92 |
| mismatches | 8 |
| max projected_final error | 324.23 |
| mean projected_final error | 12.53 |
| max margin error | 39.33 bps |
| mean margin error | 1.54 bps |

Result: **FAIL** as an exact-match test of the whole sample.

All 8 mismatches are windows `5971717`, `5971719`, `5971721`, `5971723`, `5971725`, `5971727`, `5971729`, and `5971731`. Every rebuild returned the same stale final, 82118.69, while the stored finals ran from 82023.66 to 82442.92. These windows are inside the pre-outage stretch that also has no outcome row. Example: snapshot 51220, window `5971731`, remaining 298 s, stored final 82442.92, stored margin +0.53 bps, rebuilt final 82118.69, rebuilt margin -38.80 bps.

The other 92 rows matched exactly, including margin. The failure is local to the stall, not a general break of the oracle tape.

## V. Decision Reconstruction

100 decisions per bot, strided across the decision history, checked against the nearest snapshot within 2 s and the settings version.

| | checked | matched | mismatched |
| --- | --- | --- | --- |
| Bot A | 100 | 100 | 0 |
| Bot B | 100 | 100 | 0 |

Gates checked: candidate side, entry window, margin, quote, price cap, spread, and `entry_allowed` given position, one-entry, and balance bits. No mismatch categories.

Result: **PASS**.

## W. Join Integrity

| check | count |
| --- | --- |
| orphan decisions | 0 |
| orphan execution events | 0 |
| decisions missing a settings version | 0 |
| unknown window ids | 0 |
| decisions with an unknown bot | 0 |
| events with an unknown bot | 0 |
| decisions missing strategy | 0 |
| research windows with no rounds row | 31 |
| completed research windows with no outcome | 30 |

Result: **FAIL**. Identity joins are clean. Outcome linkage is not: 30 completed windows have no official and no local outcome, in one block before the outage. The 31st window without a rounds row is the open window.

## X. Data Continuity

Snapshot gaps longer than 10 s after instrumentation start:

| start t_ms | end t_ms | duration | windows | class |
| --- | --- | --- | --- | --- |
| 1791459964782 | 1791459976011 | 11.2 s | 5971533 | known startup |
| 1791519322826 | 1791527559716 | 2.288 h | 5971731 → 5971758 | unexplained |

The long hole is not a process restart. The same Python process from 14:45 on 8 Oct was still the listener. Snapshots, and the rounds labels just before them, stopped. Collection resumed inside window `5971758`. Longest unexplained gap: **2.29 hours**.

## Y. No-Look-Ahead Check

**PASS.**

`research_bot_decisions` has no outcome column and no final-TWAP column. Official and local outcomes stay on `rounds` and are joined by window id after the fact. The reconstruction used only Chainlink samples received by the snapshot time. The eight rebuild errors are missing or late samples in the stall, not future labels copied into the decision.

## Z. Counterfactual Readiness

| parameter | class | why |
| --- | --- | --- |
| entry_window_s | YES | `remaining_ms` is on the snapshot grid for 198 full windows. Entry-time buckets are dominated by 75–90 s (A) and 50–59 s (B), so other windows are thinly traded but the clock is there. |
| min_margin_bps | YES | Margin is stored and matched a Chainlink rebuild on 92/100 rows. Exclude the pre-outage block where the rebuild failed. |
| max_entry_cents | PARTIAL | Some side has a usable ask on 100% of T-90..T-10 rows. Each side is missing an ask on 30–48% of the late window because the book locks. |
| cut_margin_bps | YES | 21 natural confirms record the margin at the threshold cross, and the post-entry snapshot path is stored. |
| cut_confirm_s | YES | Confirm delays are stored (mostly 2.0–2.5 s, three longer). Five timers reset before confirm. |
| profit_target_cents | PARTIAL | 86 profit sells show the live 99¢ path. Other targets can be walked on the bid series as a paper model. 99¢ is still not a parameter. |
| bid-depth-aware paper execution | PARTIAL | Sizes are on snapshots. Cut-sell events themselves omit bid and bid size. Live paper fills still do not walk the book. |

## AA. Sample Sufficiency

| | n |
| --- | --- |
| fully captured completed windows | 198 |
| replayable windows (original rule) | 104 |
| Bot A natural entries | 88 |
| Bot B natural entries | 29 persisted, 30 ENTRY_SUCCESS |
| Bot A completed trades | 88 |
| Bot B completed trades | 29 |
| Bot A cut episodes (completed sells) | 21 |
| Bot A settlement losses | 1 |
| Bot B settlement losses | 2 |

Progress against the working checkpoints, using fully captured windows:

| checkpoint | progress | still short |
| --- | --- | --- |
| 100 | 198 / 100 | 0 |
| 150 | 198 / 150 | 0 |
| 300 | 198 / 300 | 102 |
| 500 | 198 / 500 | 302 |

Classification: **SUFFICIENT FOR INITIAL PHASE 2B REPLAY**.

The initial checkpoint was about 100–150 fully captured windows plus several natural entries and cut episodes. This tape has 198 full windows, both bots have closed trades, and Bot A has 21 cut sells and one settlement loss. Bot B has two settlement losses where the recorded margin crossed Bot A’s cut line. That is enough to start a replay study.

It is not enough for broader parameter claims. Bucket counts are often under 15, Bot B has only two losses, and about 30 windows plus a 2.3 hour hole should be dropped from any replay set. No configuration is recommended here.

## AB. Problems / Limitations

1. Unexplained 2.29 hour snapshot gap (`5971731` to `5971758`) without a process restart.
2. Thirty completed windows immediately before that gap have no outcome.
3. `projected_final` rebuild failed on that same stretch (8/100 samples, one stale price).
4. Original replay rule based on UP ask missing marks a balanced tape as DOWN-heavy. A both-asks rule raises replayable windows from 104 to about 169.
5. Persisted `bots` / `trades` rows lag the live process by several hours. One Bot B entry is in research events and not in the trade table used for PnL.
6. Bot A balance is about $0.66 against `order_usd` 100, so later entry attempts die on balance. Settings were not changed.
7. Cut-sell events do not store bid or bid size.
8. PTB source timestamp is still null.
9. Snapshot cadence is about 1.15 s.

## AC. Verdict

```
PHASE 2A DATASET STATUS:
READY FOR INITIAL PHASE 2B
```

```
Fully captured completed windows: 198
Replayable windows: 104

Bot A post-instrumentation completed trades: 88
Bot B post-instrumentation completed trades: 29

Bot A natural cut episodes: 21

Bot A settlement losses: 1
Bot B settlement losses: 2

Projected-final reconstruction: FAIL
Decision reconstruction: PASS
Join integrity: FAIL
No-look-ahead: PASS

Continue collecting before Phase 2B: NO
Code fix required before continuing: NO
Restart required: NO
```

Initial replay can start on the fully captured windows that have an outcome and are outside `5971701`–`5971731`. Broader research still wants about 102 more fully captured windows to reach 300, and 302 more to reach 500. Leave the collector running. Do not restart it for this report, and do not change bot settings.
