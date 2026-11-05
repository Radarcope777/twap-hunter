# Phase 2A — Research Instrumentation

## A. Objective

Record what the two running BTC TWAP paper bots saw and decided, so later research can replay `entry_window`, `min_margin`, `max_entry`, cut settings, and the 99¢ profit lock. Recording is observational. It does not choose entries, exits, cuts, or settlements.

## B. Phase 1 Problem Being Solved

Phase 1 could see completed trades, but not the quotes, oracle age, projected margin, or gate results on the seconds where the bot did not trade. Settings also changed over time (Bot B was observed earlier at 55 / 2.5 / 88 / 100 and is now 60 / 2.5 / 90 / 100). Without a settings version and a 1 Hz market tape, those trades cannot be replayed against other thresholds.

## C. Architecture

`ResearchRecorder` in `backend/engine/research.py` is the only writer. Strategies never read research tables.

- BTC Chainlink and official TWAP-60 points are copied in `Pipeline._run_rtds` after the existing sample is applied.
- Once per bot loop (~4 Hz) `safe_tick` enqueues a market snapshot and one decision row per running BTC `twap_lockin` / `twap_lockin_cut` bot, at most once per second, then flushes.
- `buy`, `sell`, `lock_profit_if_bid`, the cut evaluator, `_try_settle`, and `_reconcile` append execution events through `safe_event`.
- XRP and other strategies are ignored.
- Oracle age and quote age are warning labels on the decision row. They are not entry gates.

## D. Database Schema

Created with `CREATE TABLE IF NOT EXISTS` inside the existing SQLite file. No bot, trade, or round rows are rewritten.

### research_settings_versions

| field | meaning |
| --- | --- |
| id | version id |
| bot_id | bot |
| strategy | strategy name at insert |
| params_json | full params object |
| params_hash | sha256 of canonical JSON |
| created_ms | first time this hash was stored for the bot |

A new row is inserted only when the hash differs from that bot's latest row. History before instrumentation boot is not backfilled.

### research_market_snapshots

`id`, `t_ms`, `asset`, `window_id`, `slug`, `condition_id`, `up_token`, `down_token`, `window_start_ms`, `window_end_ms`, `remaining_ms`, `ptb`, `ptb_source`, `ptb_source_ts_ms`, `ptb_age_ms`, `chainlink`, `chainlink_source_ts_ms`, `chainlink_age_ms`, `twap30`, `twap60`, `twap60_source_ts_ms`, `twap60_age_ms`, `rolling_twap`, `lookback_s`, `projected_final`, `projected_margin_bps`, `up_bid`, `up_ask`, `up_bid_size`, `up_ask_size`, `down_bid`, `down_ask`, `down_bid_size`, `down_ask_size`, `quote_ts_ms`, `quote_age_ms`.

`ptb_source_ts_ms` and `ptb_age_ms` are stored as NULL. The runtime PTB latch does not keep the source timestamp. Index: `(asset, window_id, t_ms)`.

### research_oracle_samples

`id`, `asset`, `source` (`chainlink` or `twap60`), `source_ts_ms`, `receive_ts_ms`, `price`. Unique on `(asset, source, source_ts_ms)`. Inserts use `INSERT OR IGNORE`.

### research_bot_decisions

`id`, `t_ms`, `bot_id`, `strategy`, `asset`, `window_id`, `settings_version_id`, `position_state`, `position_side`, `position_entry_price`, `position_shares`, `remaining_ms`, `projected_margin_bps`, `candidate_side`, `entry_window_pass`, `margin_pass`, `quote_pass`, `max_price_pass`, `spread_pass`, `balance_pass`, `one_entry_pass`, `entry_allowed`, `entry_rejection_reason`, `cut_margin_bps`, `cut_confirm_s`, `adverse`, `adverse_since_ms`, `adverse_duration_ms`, `cut_confirmed`, `profit99_condition`, `warnings_json`.

Pass flags are 0/1 copies of the existing gates (entry window 10s through `entry_window_s`, one entry per round, margin, quote, max price, spread ≤ 20¢, min shares, balance). Cut columns are filled for `twap_lockin_cut` while a position is open. `warnings_json` may contain `PTB_EXCHANGE_APPROX`, `ORACLE_AGE_GT_5S`, `QUOTE_AGE_GT_5S`.

### research_execution_events

`id`, `t_ms`, `bot_id`, `strategy`, `asset`, `window_id`, `event_type`, `side`, `bid`, `bid_size`, `ask`, `ask_size`, `shares`, `result`, `failure_reason`, `details_json`.

## E. Sampling Model

- Market snapshots: 1 Hz for the whole 5-minute BTC round, while at least one research bot is running.
- Decisions: 1 Hz per running research bot, same horizon.
- Oracle samples: every BTC Chainlink update (live and backfill) and every official TWAP-60 update.
- Execution events: each attempt, not sampled.

Nothing is auto-deleted. Recommended retention is 30 days, cleaned by hand if the file grows past the estimate in section O.

## F. Oracle Data Captured

Raw Chainlink `(source_ts_ms, price)` plus receive time, enough to rebuild `RollingTwap.value_at`. Official TWAP-60 points are stored beside them. TWAP-30 is copied onto the snapshot from `pipe.twap30` and is not a separate oracle series.

## G. Market Data Captured

Each snapshot stores the live PTB and `ptb_source`, Chainlink, both displayed TWAPs, rolling TWAP, price-hold `projected_final`, projected margin in bps, and both sides of the CLOB top of book (price and size) with quote age.

## H. Bot Decision Data Captured

Each decision row names the bot, window, settings version, position, remaining time, projected margin, candidate side, every entry gate, the rejection reason, cut timer state, and whether the held bid is already ≥ 99¢. The official round outcome is not an input.

## I. Execution Events Captured

`ENTRY_ATTEMPT`, `ENTRY_REJECTED` (`POSITION_OPEN`, `OUTSIDE_ENTRY_WINDOW`, `NO_ASK`, `PRICE_ABOVE_CAP`, `NO_BID`, `SPREAD_TOO_WIDE`, `MIN_SHARES`, `INSUFFICIENT_BALANCE`), `ENTRY_SUCCESS`, `SELL_FAILED`, `SELL_SUCCESS`, `PROFIT_SELL_ATTEMPT`, `PROFIT_SELL_SUCCESS`, `PROFIT_SELL_FAILED`, `CUT_TIMER_RESET`, `CUT_TIMER_STARTED`, `CUT_CONFIRMED`, `CUT_SELL_ATTEMPT`, `CUT_SELL_SUCCESS`, `CUT_SELL_FAILED`, `SETTLEMENT_VOID`, `SETTLEMENT_LOCAL`, `SETTLEMENT_OFFICIAL`, `SETTLEMENT_ADJUST`.

Sell events record bid, bid size, and requested shares. Paper fills still ignore bid depth. A failed cut sell still clears `adverse_since`, which is the existing behavior.

## J. Settings Versioning

Versioning starts at the first decision after the instrumented process boots. The clarification's 55 / 88 figure for Bot B is stale. Live DB and the running API agree, and those values were not written back:

| bot | id | strategy | status | params |
| --- | --- | --- | --- | --- |
| Bot A | `b_0d55c464` | `twap_lockin_cut` | running | entry 90s, min margin 0.3 bps, max entry 95¢, cut margin 0.5 bps, cut confirm 2s, order $100 |
| Bot B | `b_384a1650` | `twap_lockin` | running | entry 60s, min margin 2.5 bps, max entry 90¢, order $100 |

## K. Outcome Labeling

Outcomes stay on the existing `rounds` table (`outcome_official` and local fields). Research rows join on `(asset, window_id)`. Settlement events record what the bot booked. They are not used to decide the next entry.

## L. Replay Capability

| question | answer |
| --- | --- |
| Can entry_window be replayed? | YES. `remaining_ms`, gate bits, and entry events cover the round at 1 Hz. |
| Can min_margin be replayed? | YES. `projected_margin_bps` is stored, and Chainlink samples can recompute `value_at`. |
| Can max_entry be replayed? | YES. Ask price and size are on the snapshot and on entry events. |
| Can cut_margin be replayed? | YES. Margin, adverse flag, and `cut_margin_bps` are on the decision row; timer events mark the transitions. |
| Can cut_confirmation be replayed? | YES. `adverse_since_ms`, duration, and `CUT_TIMER_*` / `CUT_CONFIRMED` events. |
| Can profit target be replayed? | PARTIAL. 99¢ is hardcoded, not a parameter. `profit99_condition` and `PROFIT_SELL_*` show when it fired. |
| Can realistic bid-depth execution be approximated? | PARTIAL. Bid size is recorded. The paper sell still fills the full position when any positive bid exists. |

## M. Remaining Replay Limitations

- PTB source timestamp is not stored. Replay must trust the latched `ptb` / `ptb_source` on the snapshot.
- Decision bits are a reconstructed copy of the gates. Execution events are the actual `buy` / `sell` path. If those two ever disagree, trust the events.
- Quote and oracle gaps wider than 1 second are visible as snapshot gaps, not filled in.
- One entry after a restart still depends on in-memory `rt["entries"]`, which is not reconstructed from research rows.
- No historical settings interval was invented for trades taken before this instrumentation.

## N. Performance Impact

The bot loop still decides first. Research work is a bounded in-memory queue (8,000 rows per kind) and one batched SQLite commit per loop when anything is queued. A full queue drops new research rows and logs a warning. Trading waits only for the existing `_save` path.

## O. Storage Estimate

About 90–120 MB per day at 1 Hz for one BTC pipeline, two decision streams, and Chainlink plus TWAP-60 samples. About 0.7 GB for 7 days and about 3 GB for 30 days. No automatic purge.

## P. Failure Isolation

`safe_tick`, `safe_event`, and `safe_oracle` swallow recorder errors. A failed flush leaves the batch in the queue for the next attempt. `force_fail` in tests raises inside the recorder and the evaluators still call `buy` / `sell` with the same result. Research failure does not change balance, position, or the cut-timer clear after a failed sell.

## Q. Tests

`tests/test_research_phase2a.py` (temporary `DATA_DIR`, not the live database):

```
4 passed
```

Covered: `twap_lockin` buy is identical with recorder failure forced; `twap_lockin_cut` keeps the position and clears `adverse_since` when the sell has no bid, with and without recorder failure; a decision row stores bot, window, settings version, and a positive-margin UP candidate even if `round_results` already says DOWN; XRP samples and events are dropped.

## R. Files Changed

- `backend/storage.py` — five research tables, indexes, `research_settings_version`, `research_insert_many`
- `backend/engine/research.py` — recorder
- `backend/engine/pipeline.py` — BTC oracle copies and `_twap60_src_ms`
- `backend/engine/bots.py` — observational events and `safe_tick`
- `tests/test_research_phase2a.py`
- `scripts/research_quality.py`
- `docs/PHASE_2A_RESEARCH_INSTRUMENTATION.md`

## S. Confirmation of Trading Non-Regression

```
entry logic unchanged
exit logic unchanged
cut logic unchanged
settlement logic unchanged
settings unchanged
bot balances/history unchanged
```

DB and `GET /api/bots` both show the parameters in section J. This process did not update `bots.params`, did not reset balances, and did not delete trades. The running process is still the previous code, so it is not writing research rows yet.

## T. How To Verify Collection

After the restart in the section below:

```
.\.venv\Scripts\python.exe scripts\research_quality.py
```

Or:

```sql
SELECT COUNT(*) FROM research_market_snapshots;
SELECT COUNT(*) FROM research_oracle_samples;
SELECT COUNT(*) FROM research_bot_decisions;
SELECT COUNT(*) FROM research_execution_events;
SELECT id, bot_id, params_json, created_ms
FROM research_settings_versions
ORDER BY id DESC;
```

Expect snapshot and decision counts to rise about once per second per running BTC research bot, and oracle rows on each Chainlink tick.

---

## RESTART REQUIRED

The process listening on port 9000 loaded the old modules. New hooks run only in a new process. Tables are already created (`CREATE TABLE IF NOT EXISTS` ran when a later local Python process opened the same database). That did not rewrite bots, trades, or rounds. The running server still does not record.

Safe procedure:

1. Leave Proxifier as it is (Binance Direct, Python via the USA proxy, localhost Direct).
2. Stop only the current listener on port 9000.
3. From `D:\TEST\twap-hunter` start:

```
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 9000
```

4. Do not delete `data\twaphunter.db`. Do not change bot params.
5. Run `scripts\research_quality.py` and confirm the counts increase.

Phase 2B is not started.
