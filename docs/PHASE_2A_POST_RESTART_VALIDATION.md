# Phase 2A — Post-Restart Validation

Read-only check of the research tape after the backend restart on port 9000. No code, settings, bots, or research rows were changed. Phase 2B was not started.

Collection window observed here is about 16.7 minutes (first research row through the T1 count). That is not enough history to tune a strategy.

## A. Runtime State

- Backend is running. Health: `ok=true`, `ready=true`, `bots=5`, storage `D:\TEST\twap-hunter\data`.
- Port 9000 is the process started at 2026-10-08 14:46:01 local (application startup complete, pipeline BTC started). This is the build that contains the Phase 2A hooks.
- `GET /api/bots` returns both primary bots as `running`.
- Neither bot was reset. Start balance is still 1000. Trade history starts well before this restart.

| bot | SQLite trades | earliest trade t_ms | latest trade t_ms | balance now | wins / losses / rounds |
| --- | --- | --- | --- | --- | --- |
| `b_0d55c464` | 1004 | 1791202000103 | 1791460466707 | 26.31 | 390 / 109 / 18 |
| `b_384a1650` | 112 | 1791201985247 | 1791459256498 | 779.10 | 39 / 14 / 17 |

API balances match the bots table (26.31 and 779.10). Stats match. API `params` match the JSON stored on each bot row.

## B. Bot A/B Current Configuration

| | Bot A `b_0d55c464` | Bot B `b_384a1650` |
| --- | --- | --- |
| strategy | `twap_lockin_cut` | `twap_lockin` |
| status | running | running |
| entry_window_s | 90 | 60 |
| min_margin_bps | 0.3 | 2.5 |
| max_entry_cents | 95 | 90 |
| cut_margin_bps | 0.5 | — |
| cut_confirm_s | 2 | — |
| order_usd | 100 | 100 |

These are the live values. They are not the older Phase 1 note of 55 / 2.5 / 88 / 100.

## C. Research Table Counts

`scripts/research_quality.py` was run once, before the 10-second accumulation wait. Complete output:

```
db D:\TEST\twap-hunter\data\twaphunter.db
rounds_with_snapshots 4
snapshots 842
oracle_samples 1998
decisions 1684
execution_events 1085
settings_versions 2
missing_ptb 0.1%
missing_projected_final 0.2%
missing_quote 3.7%
stale_oracle_gt_5s 0.7%
stale_quote_gt_5s 0.7%
snapshot_gap_ms largest 11229 median 1154 p95 1309
replayable_snapshots 73.6%
```

Independent read-only SQLite at T1 (about 10 seconds later):

| table | rows | earliest | latest | duration | distinct BTC windows |
| --- | --- | --- | --- | --- | --- |
| research_settings_versions | 2 | 1791459962474 (`created_ms`) | 1791459962511 | 38 ms | n/a (one row per bot) |
| research_market_snapshots | 849 | 1791459962473 (`t_ms`) | 1791460965793 | 1003 s | 4 |
| research_oracle_samples | 2011 | source 1791459895000; receive 1791459964183 | source 1791460955000; receive 1791460965596 | source span 1060 s; receive span 1001 s | not keyed by window |
| research_bot_decisions | 1698 | 1791459962473 | 1791460965793 | 1003 s | 4 |
| research_execution_events | 1085 | 1791460420244 | 1791460799821 | 380 s | 2 |

Important columns:

- settings: `id`, `bot_id`, `strategy`, `params_json`, `params_hash`, `created_ms`
- snapshots: `t_ms`, `window_id`, `remaining_ms`, `ptb`, `ptb_source`, `ptb_source_ts_ms`, `ptb_age_ms`, `chainlink`, `chainlink_age_ms`, `twap60`, `twap60_age_ms`, `projected_final`, `projected_margin_bps`, both UP and DOWN bid/ask/size, `quote_age_ms`
- oracle: `asset`, `source`, `source_ts_ms`, `receive_ts_ms`, `price` with unique `(asset, source, source_ts_ms)`
- decisions: bot, strategy, window, `settings_version_id`, position, remaining, margin, candidate, gate bits, cut fields, `profit99_condition`, `warnings_json`
- events: bot, strategy, window, `event_type`, side, bid/ask and sizes, shares, result, `failure_reason`, `details_json`

`ptb_source_ts_ms` and `ptb_age_ms` are present and null on the sampled rows. The latch still does not store a PTB source time.

## D. Live Accumulation Test

| table | count T0 | count T1 | delta |
| --- | --- | --- | --- |
| research_settings_versions | 2 | 2 | 0 |
| research_market_snapshots | 841 | 849 | +8 |
| research_oracle_samples | 1997 | 2011 | +14 |
| research_bot_decisions | 1682 | 1698 | +16 |
| research_execution_events | 1085 | 1085 | 0 |

T1 is 10 seconds after T0. Snapshots and both bots' decisions increased. Oracle samples increased. Execution events did not, because no new order attempt landed in that slice. Settings stayed at one version per bot.

## E. Sampling Quality

Target is about 1 Hz, not exactly 1000 ms. Observed median interval is about 1.15 s.

Active window `5971536` (still open at T1, last remaining ~144 s):

| snapshots | first t_ms | last t_ms | median | p95 | max | gaps >1.5s | >2s | >5s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 134 | 1791460810276 | 1791460965793 | 1154 ms | 1310 ms | 1904 ms | 2 | 0 | 0 |

Completed windows (3):

| window | snapshots | span | first remaining | last remaining | median | p95 | max | >1.5s | >2s | >5s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5971533 | 203 | 247 s | 238 s | 0.2 s | 1160 ms | 1264 ms | 11229 ms | 6 | 2 | 1 |
| 5971534 | 255 | 298 s | 299 s | 1.1 s | 1157 ms | 1317 ms | 2055 ms | 5 | 1 | 0 |
| 5971535 | 257 | 299 s | 300 s | 0.9 s | 1148 ms | 1302 ms | 2286 ms | 5 | 3 | 0 |

Across all completed-window intervals (712 gaps): median 1153 ms, p95 1302 ms, max 11229 ms, 16 gaps >1.5 s, 6 gaps >2 s, 1 gap >5 s.

The 11.2 s hole is in the restart window `5971533`, which was already in progress (first snapshot at T-238s, not T-300s). Later windows stay within about 2.3 s. The tape is slightly slower than 1.00 Hz and is continuous enough to see the round.

## F. Settings Version Validation

Two rows, both written at instrumentation boot. No later versions.

| id | bot | created_ms | params |
| --- | --- | --- | --- |
| 1 | `b_0d55c464` | 1791459962474 | 90 / 0.3 / 95 / cut 0.5 / 2 / order 100 |
| 2 | `b_384a1650` | 1791459962511 | 60 / 2.5 / 90 / order 100 |

| bot | research version vs bots table | vs GET /api/bots |
| --- | --- | --- |
| A | PASS | PASS |
| B | PASS | PASS |

Every decision row for both bots resolves to one of these ids (`missing_or_unresolved_version = 0`). Strategy on the decision matches the version (`twap_lockin_cut` for A, `twap_lockin` for B).

## G. Market Snapshot Example

Window `5971535` (full coverage). Ten consecutive snapshots starting at the row nearest T-60s. PTB source timestamp and PTB age are null. Quotes are one-sided here: UP is offered at 1¢ with no bid, DOWN is bid at 99¢ with no ask. That is a real locked book, and it is stored.

| t_ms | window | remaining_ms | ptb | ptb_source | chainlink | cl_age_ms | twap60 | twap60_age_ms | projected_final | margin_bps | up_bid | up_ask | up_bid_sz | up_ask_sz | dn_bid | dn_ask | dn_bid_sz | dn_ask_sz | quote_age_ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1791460750409 | 5971535 | 59592 | 82517.088 | official_twap60_carry | 82424.707 | 1408 | 82433.205 | 1408 | 82424.707 | -11.195 | null | 1 | null | 24320.43 | 99 | null | 24320.43 | null | 113 |
| 1791460751682 | 5971535 | 58319 | 82517.088 | official_twap60_carry | 82427.348 | 681 | 82432.797 | 681 | 82427.341 | -10.876 | null | 1 | null | 24605.48 | 99 | null | 24605.48 | null | 160 |
| 1791460752705 | 5971535 | 57296 | 82517.088 | official_twap60_carry | 82427.348 | 1704 | 82432.797 | 1704 | 82427.341 | -10.876 | null | 1 | null | 24609.51 | 99 | null | 24609.51 | null | 0 |
| 1791460753764 | 5971535 | 56237 | 82517.088 | official_twap60_carry | 82427.569 | 763 | 82432.458 | 763 | 82427.545 | -10.851 | null | 1 | null | 24629.51 | 99 | null | 24629.51 | null | 51 |
| 1791460754799 | 5971535 | 55202 | 82517.088 | official_twap60_carry | 82427.254 | 798 | 82432.314 | 798 | 82427.251 | -10.887 | null | 1 | null | 25659.51 | 99 | null | 25659.51 | null | 0 |
| 1791460755867 | 5971535 | 54135 | 82517.088 | official_twap60_carry | 82427.246 | 865 | 82432.023 | 865 | 82427.243 | -10.888 | null | 1 | null | 23659.51 | 99 | null | 23659.51 | null | 328 |
| 1791460756889 | 5971535 | 53112 | 82517.088 | official_twap60_carry | 82427.385 | 888 | 82432.023 | 888 | 82427.368 | -10.873 | null | 1 | null | 23659.51 | 99 | null | 23659.51 | null | 1351 |
| 1791460758154 | 5971535 | 51847 | 82517.088 | official_twap60_carry | 82427.291 | 1153 | 82431.879 | 1153 | 82427.285 | -10.883 | null | 1 | null | 23654.51 | 99 | null | 23654.51 | null | 75 |
| 1791460759470 | 5971535 | 50531 | 82517.088 | official_twap60_carry | 82427.078 | 469 | 82431.587 | 1469 | 82427.103 | -10.905 | null | 1 | null | 28525.51 | 99 | null | 28525.51 | null | 227 |
| 1791460760566 | 5971535 | 49435 | 82517.088 | official_twap60_carry | 82427.078 | 1565 | 82431.587 | 1565 | 82427.103 | -10.905 | null | 1 | null | 28525.51 | 99 | null | 28525.51 | null | 105 |

A snapshot of this shape is enough to replay remaining time, PTB, projected margin, and the visible top of book. It is not enough to recover the PTB source timestamp.

## H. Oracle Sample Validation

Window `5971535` (source time 1791460500000–1791460800000):

| | Chainlink | TWAP60 |
| --- | --- | --- |
| samples | 296 | 296 |
| source range | 1791460500000 – 1791460800000 | same |
| receive range | 1791460510777 – 1791460810873 | 1791460510777 – 1791460811433 |
| median source interval | 1000 ms | 1000 ms |
| p95 source interval | 1000 ms | 1000 ms |
| max source gap | 2000 ms | 2000 ms |

Source timestamps are strictly increasing. Receive timestamps do not step backwards inside this window. Prices are finite and in a tight BTC band (Chainlink 82423.35–82526.42, TWAP60 82428.79–82522.64). All 2011 oracle rows are `asset='BTC'`. Duplicate `(asset, source, source_ts_ms)` keys: 0.

Global Chainlink (1036 points): median 1000 ms, p95 1000 ms, max 8000 ms, one gap >5 s. Global TWAP60 (975 points): median 1000 ms, p95 1000 ms, max 8000 ms, one gap >5 s. Those 8 s holes line up with the restart / cold backfill, not with a broken live feed. Backfill source times begin about 67 s before the first receive time, which is expected.

## I. Projected Final Reconstruction

Eight snapshots near T-90, T-75, T-60, T-50, T-40, T-30, T-20, and T-10. `projected_final` was rebuilt with the production rolling TWAP: Chainlink samples whose receive time is at or before the snapshot, held to `window_end`, lookback from the snapshot.

| snapshot | window | remaining_ms | abs(final) | abs(margin bps) | ptb_source |
| --- | --- | --- | --- | --- | --- |
| 638 | 5971535 | 90028 | 0 | 0 | official_twap60_carry |
| 651 | 5971535 | 74982 | 0 | 0 | official_twap60_carry |
| 153 | 5971533 | 59907 | 0 | both null | exchange_approx |
| 417 | 5971534 | 49533 | 0 | 0 | official_twap60_carry |
| 681 | 5971535 | 40147 | 0 | 0 | official_twap60_carry |
| 690 | 5971535 | 30205 | 0 | 0 | official_twap60_carry |
| 698 | 5971535 | 20407 | 0 | 0 | official_twap60_carry |
| 195 | 5971533 | 10071 | 0 | both null | exchange_approx |

Stored `projected_final` matched the rebuild on all 8 rows (absolute difference 0). Stored margin matched wherever the live rule returns a number. On `exchange_approx` both stored and rebuilt margin are null, which is the live rule. Offline replay can reproduce the live signal from the oracle tape.

T-60 in this set fell in the restart window because that was the closest row to 60 s among early candidates; the other targets fell in later windows. The match still holds.

## J. Decision Consistency

Ten latest decision rows per bot were checked against the nearest snapshot (within 1.5 s) and the settings version.

Recomputed from the snapshot and the versioned params: entry window (10 s through `entry_window_s`), margin gate, candidate side from the sign of margin, quote present, ask versus `max_entry_cents`, spread ≤ 20¢, and `entry_allowed` given position / one-entry / balance bits.

| bot | checked | matching | mismatches |
| --- | --- | --- | --- |
| `b_0d55c464` | 10 | 10 | 0 |
| `b_384a1650` | 10 | 10 | 0 |

## K. Join Integrity

| check | count |
| --- | --- |
| decisions with no snapshot for that window | 0 |
| execution events with no snapshot for that window | 0 |
| execution events whose bot_id is not in `bots` | 0 |
| decisions whose bot_id is not in `bots` | 0 |
| decisions with a missing settings version | 0 |
| decisions with a missing strategy | 0 |
| unknown window ids on decisions | 0 |

Round labels join on `(BTC, window_id)`:

| window | official | local | in research |
| --- | --- | --- | --- |
| 5971533 | DOWN | DOWN | completed |
| 5971534 | UP | UP | completed |
| 5971535 | DOWN | DOWN | completed |
| 5971536 | none yet | none yet | still open |

Outcomes live only on `rounds`. They are not columns on the decision row.

## L. Execution Events

1085 events:

| event | count |
| --- | --- |
| ENTRY_ATTEMPT | 541 |
| ENTRY_REJECTED | 540 |
| ENTRY_SUCCESS | 1 |
| PROFIT_SELL_ATTEMPT | 1 |
| SELL_SUCCESS | 1 |
| PROFIT_SELL_SUCCESS | 1 |

Reject reasons: `NO_ASK` 460, `PRICE_ABOVE_CAP` 57, `INSUFFICIENT_BALANCE` 23. Bot B 287 rejects, Bot A 253. No cut events. No settlement events.

One complete post-restart trade, Bot A only. Bot B has attempts and rejects, and no fill.

Bot A, window `5971534`, settings version 1:

| t_ms | event | side | bid | ask | size | shares |
| --- | --- | --- | --- | --- | --- | --- |
| 1791460429339 | ENTRY_SUCCESS | UP | 92 | 93 | ask size 3.92 | 3.92 |
| 1791460466707 | PROFIT_SELL_ATTEMPT | UP | 99 | | bid size 4854.59 | 3.92 |
| 1791460466758 | SELL_SUCCESS | UP | 99 | | bid size 4854.59 | 3.92 |
| 1791460466758 | PROFIT_SELL_SUCCESS | UP | 99 | | | 3.92 |

Entry detail: `lock-in: projected final +5.1 bps vs PTB, 81s left`, cost 3.6456, fee 0.0192. The decision 1.3 s earlier shows remaining 81579 ms, margin +5.14 bps, candidate UP, position FLAT, version 1. The next decision, 0.35 s after the fill, is position OPEN. Exit reason is the 99¢ profit lock, not settlement. The round later resolved UP officially, after the sell.

The evaluator runs faster than the 1 Hz decision grid, so the fill sits between a decision that still says `entry_allowed=0` and the next decision that already shows the open position. The execution event is the fill record. The decision grid is the once-per-second gate picture.

## M. Bot A Cut Instrumentation

No adverse cut occurred after the restart. There is no `CUT_TIMER_STARTED`, `CUT_CONFIRMED`, or `CUT_SELL_*` row. Validation of that event path is pending a natural adverse episode. It was not forced.

## N. No-Look-Ahead Validation

PASS.

Decision columns do not include `outcome_official`, `final_twap`, or any other final outcome. Those values are only on `rounds`, joined after the fact by window id. Oracle rows used to rebuild `projected_final` were limited to samples received at or before the snapshot.

A naive comparison of wall-clock `t_ms` to poly-time `window_end_ms` flags 26 decisions as "after the window." Wall time on these snapshots sits about 10 s ahead of poly time (`t_ms` versus `window_end - remaining`). Those 26 rows are the last few seconds of a round under that offset, not future oracle or outcome fields copied into the decision.

## O. Replay Coverage

A completed window is one whose `window_end` is before the poly time of the newest snapshot. The open window `5971536` is excluded.

REPLAYABLE requires all of:

- PTB present and `ptb_source` not `exchange_approx`
- snapshot span at least 240 s and at least 200 snapshots
- no snapshot gap above 5 s
- UP ask missing on at most 10% of rows
- at least 100 Chainlink samples inside the window
- `projected_final` missing on at most 10% of rows
- at least one decision with a settings version
- an official or local outcome on `rounds`

PARTIAL means some of those hold. NOT REPLAYABLE means the window is missing PTB and oracle coverage together, or the snapshot span is under 60 s.

| window | class | why |
| --- | --- | --- |
| 5971533 | PARTIAL | restart mid-round; `exchange_approx` PTB (margin null); one 11.2 s gap; outcome DOWN is present |
| 5971534 | PARTIAL | quote holes above 10% (one-sided book); outcome UP is present |
| 5971535 | REPLAYABLE | full span, official TWAP60 carry, outcome DOWN |

```
total completed windows  3
replayable               1
partial                  2
not replayable           0
replayable %             33%
```

Reason counts: `missing_ptb` 1, `ptb_exchange_approx` 1, `snapshot_gap_gt_5s` 1, `quotes_missing` 1.

The quality script's 73.6% is a different metric: share of snapshot rows that have PTB, projected final, both asks, and a Chainlink timestamp. It is not the window-level replay rate above.

## P. Entry Counterfactual Readiness

At T-60, T-55, T-50, T-45, T-40, and T-30 the persisted fields can answer "what if `min_margin` were X and `max_entry` were Y?" on windows that have a numeric margin and a two-sided or at least an ask quote.

| parameter | class | limitation |
| --- | --- | --- |
| entry_window_s | YES | `remaining_ms` is on every snapshot and decision |
| min_margin_bps | YES | `projected_margin_bps` matches a rebuild from Chainlink; null when PTB is `exchange_approx` |
| max_entry_cents | PARTIAL | ask is stored when the book has one; a locked side often has null bid or null ask, so that side cannot be priced |

The 1 Hz decision bit can miss a fill that happens on a 250 ms tick. Counterfactuals should use the snapshot series plus execution events, not `entry_allowed` alone.

## Q. Cut Counterfactual Readiness

| parameter | class |
| --- | --- |
| cut_margin_bps | YES |
| cut_confirm_s | PARTIAL |

After Bot A's entry, snapshots continue through the hold (~37 s) with `projected_margin_bps`, remaining time, bid, and bid size. A different cut threshold can be walked on that trajectory. `cut_confirm_s` needs a sustained adverse interval. No such interval has been observed since restart, and the cut timer events have not fired, so the stored `adverse_since` path is not yet confirmed on a live episode.

## R. Profit Target Replay Limitations

Phase 2A called a different profit target PARTIAL because 99¢ is hardcoded in `lock_profit_if_bid`, not a bot parameter, and because a paper sell fills the whole position whenever the bid is positive.

With the new bid stream:

- Paper-model replay of 95¢, 96¢, 97¢, 98¢, or 99¢ is possible. Each snapshot has the held side's bid. The one observed exit fired at bid 99 with bid size 4854.59, larger than the 3.92 shares.
- Realistic executable replay stays PARTIAL. Recorded bid size can reject a target when size is smaller than the position, but the live paper fill does not walk the book or apply slippage. A threshold study on this tape is a paper-model study unless size is applied as an extra filter.

profit99 was not changed.

## S. Data Growth

| | bytes |
| --- | --- |
| `twaphunter.db` | 2,334,720 |
| `twaphunter.db-wal` | 4,202,432 |

Those files also hold the pre-existing bots, 1000+ trades, and rounds. They are not a pure research size.

Over ~1003 s of research time the tape added on the order of 850 snapshots, 1700 decisions, 2000 oracle rows, and 1085 events. A rough row-size extrapolation lands inside the earlier 90–120 MB/day band, but this window is only about 17 minutes and the event burst (about 500 rejects once the entry window opened) will not run all day. Treat 90–120 MB/day as still plausible, not as a measured daily total.

## T. Runtime/SQLite Health

Post-restart log (`279137`, from 14:46:01) has no `ERROR`, `WARNING`, `Traceback`, `database is locked`, or `research persistence` lines. Startup shows 5 bots loaded, BTC pipeline start, Binance klines 200, Gamma 200, and an RTDS backfill of 59 points.

| check | result |
| --- | --- |
| snapshot persistence latency | not separately stored; rows appear in the same second as `t_ms` |
| research queue / backlog | in-memory only; no "queue full" warning |
| SQLite errors | none in the log |
| database locked | none |
| dropped research records | none logged |
| BotManager loop anomalies | none logged; decision spacing tracks the snapshot spacing |

The main loop is still trading: Bot A bought and sold after the restart, and balances moved only with that trade.

## U. Problems Found

Documented only. Not fixed.

1. PTB source timestamp and age are always null.
2. Snapshot cadence is about 1.15 s, not 1.00 s. One 11.2 s gap sits in the restart window.
3. The first research window used `exchange_approx` PTB, so projected margin is null there by the live rule.
4. Locked books store a null bid or null ask. `max_entry` replay is blind on that side. This drove most `NO_ASK` rejects.
5. Decision rows are ~1 Hz while `buy` runs on the faster loop, so `entry_allowed` can be 0 on the sample just before a real fill.
6. Cut timer events have not occurred yet.
7. Event volume is high during the entry window (540 rejects). That is the recorder seeing real reject paths, including Bot A attempting a $100 order with about $26 left.

## V. Phase 2A Verdict

```
PHASE 2A VALIDATED WITH LIMITATIONS
```

The restarted process is writing a coherent tape: settings match runtime, snapshots and decisions are still growing, Chainlink rebuilds `projected_final` exactly, decision gates match the nearest snapshot, joins are intact, and one natural Bot A round-trip was captured from buy through the 99¢ sell. Limitations are the short history, the missing PTB source time, one-sided quotes, the 1.15 s grid, and no live cut episode. None of those corrupt the rows already stored.

```
Should we continue collecting data? YES

Is a code fix required before collecting more data? NO

Is restart required? NO

Is Phase 2B ready to start? NO
```

Three completed windows and one trade are not a sample for optimization. Keep collecting until many natural rounds, including cuts, are on the tape.
