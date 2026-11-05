# PHASE 2A — RESEARCH INSTRUMENTATION FOR BTC TWAP STRATEGIES

You are working in the repository:

```
D:\TEST\twap-hunter
```

This phase follows the completed Phase 1 audit.

Before doing anything else, read completely:

```
PHASE_1_BTC_BOTS_STRATEGY_AUDIT.md
```

Treat that report as the authoritative starting point for this phase.

Do NOT repeat Phase 1 from scratch unless you find a contradiction while implementing Phase 2A.

---

# 1. PRIMARY OBJECTIVE

The existing historical trade data is NOT sufficient for true counterfactual replay.

It records actual trades, but it does not preserve enough information to answer questions such as:

```
What would have happened with:
entry_window = 45s
min_margin = 4bps
max_entry = 85¢
cut_margin = 1bps
cut_confirmation = 1s
```

because we do not know the complete historical state at every potential decision point.

The purpose of Phase 2A is therefore:

> Build a high-quality research/instrumentation dataset that records the market/oracle/strategy state needed for future replay and parameter optimization.

Phase 2A must NOT optimize the strategy.

Phase 2A must NOT change the trading decisions of existing bots.

---

# 2. TARGET BOTS

Primary research targets:

### Bot A

```
ID: b_0d55c464
Name: BTC Lock In Small Loss
Asset: BTC
Strategy: twap_lockin_cut
```

Current Phase 1 configuration:

```
entry_window_s = 90
min_margin_bps = 0.3
max_entry_cents = 95
cut_margin_bps = 0.5
cut_confirm_s = 2
order_usd = 100
```

### Bot B

```
ID: b_384a1650
Name: BTC TWAp LOCKIN
Asset: BTC
Strategy: twap_lockin
```

Current Phase 1 configuration:

```
entry_window_s = 60
min_margin_bps = 2.5
max_entry_cents = 90
order_usd = 100
```

Do NOT change these parameters.

Do NOT start/stop/reset these bots.

Do NOT create Bot C yet.

---

# 3. CRITICAL NON-REGRESSION REQUIREMENT

Phase 2A must be OBSERVATIONAL.

It must not change:

```
EntryIntent
entry eligibility
UP/DOWN decision
projected_final
projected_margin
PTB
TWAP
BUY behavior
SELL behavior
profit99
cut-loss behavior
settlement behavior
position sizing
fees
bot scheduling semantics
```

Existing Bot A and Bot B must make exactly the same trading decisions they would make without the research instrumentation.

Research recording must never be used as an entry gate.

If research persistence fails, trading logic must continue operating normally.

---

# 4. IMPORTANT FINDINGS FROM PHASE 1

Use the complete audit for details.

Key findings include:

### Bot A

```
479 completed trades
378 wins
101 losses
78.91% WR
Net P&L = -$764.63

Avg Win = +$11.95
Avg Loss = -$52.29
Break-even WR ≈ 81.40%
```

### Bot B

```
50 completed trades
37 wins
13 losses
74.00% WR
Net P&L = -$219.09

Avg Win = +$15.08
Avg Loss = -$59.78
Break-even WR ≈ 79.85%
```

Important Phase 1 observations:

- entry algorithm is shared;
- Bot A uses cut-loss;
- Bot B does not;
- both use hard-coded bid >= 99¢ profit exit;
- Bot A's cut is based on projected margin reversal, NOT token-price loss;
- historical trajectories of projected margin were not recorded;
- failed SELL attempts were not recorded;
- oracle/source freshness is insufficiently recorded;
- full replay is currently impossible.

Phase 2A exists specifically to fix the RESEARCH DATA limitation.

Do not fix trading behavior yet.

---

# 5. DESIGN BEFORE IMPLEMENTATION

Before editing code:

1. inspect the existing database/storage architecture;
2. inspect BotManager evaluation loop;
3. inspect Pipeline;
4. inspect current trades/rounds schema;
5. inspect current API/frontend patterns;
6. determine the safest low-overhead instrumentation architecture.

Then write a short implementation plan.

After that, implement it.

Do not ask for confirmation unless there is a genuinely destructive or ambiguous decision.

---

# 6. FUNDAMENTAL DESIGN PRINCIPLE

We need to distinguish between:

## MARKET STATE

State that is identical for all BTC bots at a particular moment:

```
BTC oracle
PTB
TWAP
projected final
projected margin
Polymarket UP/DOWN quotes
remaining time
market/window
```

and:

## BOT DECISION STATE

State specific to a particular bot:

```
bot_id
strategy
current settings
position
entry eligibility
selected side
entry rejection reason
cut timer
profit condition
actual action
```

Avoid unnecessarily duplicating high-frequency market data for every bot if a normalized architecture is cleaner.

However:

> correctness and future replay capability are more important than storage micro-optimization.

---

# 7. RESEARCH DATASET REQUIREMENTS

Design persistent research storage sufficient to reconstruct every potential decision.

A recommended architecture is:

```
research_market_snapshots
research_bot_decisions
research_execution_events
```

You may choose different names if existing project conventions strongly favor another design.

Document the final schema.

---

# 8. MARKET SNAPSHOTS

For every relevant BTC 5-minute round during the research collection period, persist enough state to replay entry/cut decisions.

At minimum consider:

```
timestamp_ms
monotonic/receive timestamp if useful

asset
window_id
market slug/id

window_start_ms
window_end_ms
remaining_ms

PTB
PTB source
PTB source timestamp
PTB age_ms

Chainlink last price
Chainlink source timestamp
Chainlink age_ms

TWAP30 if available
TWAP60 if available
TWAP60 source timestamp
TWAP60 age_ms

local rolling TWAP / relevant rolling state

projected_final
projected_margin_bps

UP bid
UP ask
UP bid_size
UP ask_size

DOWN bid
DOWN ask
DOWN bid_size
DOWN ask_size

quote timestamp / receive timestamp
quote age_ms

feed connectivity/staleness flags where available
```

Do not invent values that the runtime does not actually know.

If a source timestamp is unavailable, store NULL rather than a fabricated timestamp.

---

# 9. PROJECTED FINAL REPLAY PROBLEM

Phase 1 established that projected_final depends on a local RollingTwap of Chainlink observations.

Therefore merely recording:

```
projected_final
```

may not be sufficient if we later want to test different evaluation timestamps or lookback behavior.

Determine what is required to reproduce:

```
RollingTwap.value_at(window_end)
```

accurately.

Prefer preserving enough raw/normalized oracle observations to reconstruct the projection.

Possible solution:

```
research_oracle_samples
```

containing:

```
asset
source
source_timestamp_ms
receive_timestamp_ms
price
```

Do not blindly create this table if equivalent raw observations are already persisted.

First inspect existing storage.

The goal is:

> Later we must be able to reconstruct projected_final at arbitrary historical timestamps without relying on values that no longer exist in RAM.

---

# 10. SAMPLING FREQUENCY

The BotManager loop is approximately 0.25s.

Do NOT automatically write every 250ms state to SQLite if that creates unnecessary load.

Design an appropriate research sampling frequency.

Target:

```
~1 second snapshots
```

during the useful decision horizon is likely sufficient for initial research.

But cut confirmation is currently 2 seconds, so temporal resolution must be sufficient to reconstruct it.

Recommended:

```
1 Hz persistent market snapshots
```

while separately preserving actual decision/action events at their exact runtime timestamps.

If a more efficient event-driven scheme preserves equivalent information, explain and use it.

---

# 11. COLLECTION WINDOW

For BTC 5-minute markets, we care most about the final part of the round.

At minimum collect continuously from:

```
T-120s through T+settlement/reconciliation
```

This gives us enough data to test:

```
90s
75s
60s
50s
45s
40s
30s
20s
```

entry windows.

If storage impact is small, collecting the entire 5-minute round is acceptable.

Calculate expected storage/day before choosing.

Document the estimate.

---

# 12. BOT DECISION SNAPSHOTS

For Bot A and Bot B, record evaluations separately.

At minimum:

```
timestamp_ms

bot_id
strategy
asset
window_id

settings_version or settings snapshot reference

position_state
position_side
position_entry_price
position_shares

remaining_ms

projected_margin_bps

candidate_side

entry_window_pass
margin_pass
quote_pass
max_price_pass
spread_pass
balance_pass
one_entry_pass

entry_allowed

entry_rejection_reason
```

For Bot A additionally:

```
cut_margin_bps
cut_confirm_s

adverse
adverse_since_ms
adverse_duration_ms
cut_confirmed
```

For both:

```
profit99_condition
```

The exact schema may use compact enums/JSON if justified.

---

# 13. REJECTION REASONS

One of the biggest requirements is recording why NO TRADE happened.

Define stable machine-readable reasons such as:

```
OUTSIDE_ENTRY_WINDOW
NO_PTB
PTB_EXCHANGE_APPROX
NO_PROJECTED_FINAL
MARGIN_TOO_SMALL
NO_ASK
NO_BID
PRICE_ABOVE_CAP
SPREAD_TOO_WIDE
INSUFFICIENT_BALANCE
MIN_SHARES
ALREADY_ENTERED
POSITION_OPEN
STALE_ORACLE
STALE_QUOTE
```

IMPORTANT:

Do not add new trading gates such as:

```
STALE_ORACLE
STALE_QUOTE
```

if they are not currently used.

They may be recorded as diagnostic flags, but MUST NOT change entry behavior in Phase 2A.

Separate:

```
actual_gate_result
```

from:

```
research_warning
```

---

# 14. SETTINGS VERSIONING

Phase 1 established that historical setting changes are not persisted.

Fix this research limitation.

Whenever relevant bot settings change, preserve:

```
bot_id
strategy
timestamp
full parameter snapshot
```

or equivalent versioned representation.

Every research decision/snapshot must be attributable to the exact settings active at that moment.

Do not change how settings themselves are applied.

---

# 15. ACTUAL ACTION EVENTS

Record exact action attempts, not only successful trades.

Need events such as:

```
ENTRY_ATTEMPT
ENTRY_SUCCESS
ENTRY_REJECTED

PROFIT_SELL_ATTEMPT
PROFIT_SELL_SUCCESS
PROFIT_SELL_FAILED

CUT_TIMER_STARTED
CUT_TIMER_RESET
CUT_CONFIRMED

CUT_SELL_ATTEMPT
CUT_SELL_SUCCESS
CUT_SELL_FAILED

SETTLEMENT_STARTED
SETTLEMENT_LOCAL
SETTLEMENT_OFFICIAL
SETTLEMENT_ADJUST
SETTLEMENT_VOID
```

For sell attempt record where available:

```
bid
bid_size
shares_to_sell
result
failure_reason
```

This is especially important because Phase 1 found that a failed cut SELL currently leaves insufficient historical evidence.

---

# 16. DO NOT CHANGE PAPER EXECUTION YET

Phase 1 found that paper SELL:

- sells the whole position;
- ignores bid depth;
- does not model slippage;
- differs from realistic CLOB execution.

DO NOT fix this in Phase 2A.

Instead RECORD enough information to analyze it later:

```
requested_shares
available_bid_size
bid
spread
```

This will allow Phase 2B/2C to simulate more realistic fills.

---

# 17. ORACLE FRESHNESS

Phase 1 found no actual freshness gate.

Do NOT introduce one yet.

Instead calculate/store diagnostic values such as:

```
chainlink_age_ms
twap60_age_ms
ptb_age_ms
quote_age_ms
```

where legitimately derivable.

Also store:

```
ptb_source
```

Examples may include existing values such as:

```
official_twap60_carry
rolling
chainlink_last
backfill
first_reference
recovered
exchange_approx
```

Use the project's actual enum/string values.

Do not invent replacements.

---

# 18. RAW VS DERIVED DATA

Prefer storing enough RAW state that future derived calculations can be recomputed.

For example:

Better:

```
PTB
Chainlink samples
quotes
timestamps
```

plus derived:

```
projected_final
projected_margin
```

rather than storing only derived margin.

Future research may discover a bug in projected_final.

We need the ability to recompute it.

---

# 19. MARKET IDENTITY

Every snapshot/event must be unambiguously tied to the correct market.

Where available preserve:

```
asset
window_id
slug
condition_id
UP token_id
DOWN token_id
```

This is particularly important because Phase 1 found that current paper BUY does not independently verify quote/window identity.

Again:

DO NOT change BUY behavior yet.

Record identity so we can detect mismatches.

---

# 20. OUTCOME LABELS

After resolution, the research dataset must be joinable with:

```
outcome_local
outcome_official
final_twap
final_range_twap
PTB
```

Do NOT use outcome as a feature in live decision logic.

Outcome is strictly a POST-RESOLUTION LABEL.

No look-ahead leakage.

This is mandatory.

---

# 21. REPLAY READINESS

Design the data specifically so a later offline function can do:

```
replay(
    entry_window_s=60,
    min_margin_bps=3.0,
    max_entry_cents=85,
    cut_margin_bps=1.0,
    cut_confirm_s=2.0,
    profit_target_cents=99
)
```

and evaluate the hypothetical strategy against the same historical market stream.

DO NOT implement full optimization/grid search yet.

However, implement a basic DATA VALIDATION / REPLAY-READINESS checker if useful.

It should answer things such as:

```
Can this round be replayed?

YES
NO — missing oracle samples
NO — missing quote
NO — missing PTB
NO — snapshot gap
...
```

---

# 22. DATA QUALITY METRICS

Add a way to inspect research collection quality.

At minimum calculate/report:

```
number of BTC rounds observed
number of complete rounds
snapshot count
oracle sample count
decision count
execution event count

missing PTB %
missing projected_final %
missing quote %
stale oracle observations %
stale quote observations %

largest snapshot gap
median snapshot interval
p95 snapshot interval

rounds replayable %
```

This may be:

```
CLI script
API endpoint
research report
```

Choose the least invasive solution.

No large frontend dashboard is required in Phase 2A.

---

# 23. PERFORMANCE / SAFETY

Instrumentation must not materially interfere with trading evaluation.

Consider:

- SQLite write frequency;
- WAL behavior;
- batching;
- transaction duration;
- lock contention;
- unbounded RAM queues;
- database growth;
- failure handling.

Research persistence errors must:

```
be logged
NOT crash BotManager
NOT block trading indefinitely
NOT change strategy decisions
```

Do not silently swallow repeated failures forever.

Use rate-limited/error-aware logging if appropriate.

---

# 24. RETENTION

Estimate database growth for:

```
1 day
7 days
30 days
```

at the selected sampling rate.

Do not automatically delete research data in this phase unless the project already has a safe retention mechanism.

Document recommended retention.

---

# 25. INDEXES

Add indexes required for future queries such as:

```
asset + window_id + timestamp
bot_id + window_id + timestamp
event_type + timestamp
```

Avoid excessive indexing of high-frequency tables.

Document choices.

---

# 26. MIGRATION / DATABASE SAFETY

If schema changes are necessary:

- preserve all existing data;
- use project's existing schema/migration mechanism;
- make creation idempotent where appropriate;
- do not reset SQLite;
- do not delete old trades;
- do not alter existing bot balances.

Before migration, inspect how this project currently manages schema evolution.

Follow existing conventions.

---

# 27. TESTS

Add focused tests.

At minimum verify:

### Non-regression

For identical synthetic inputs:

```
before instrumentation decision
==
after instrumentation decision
```

for:

```
twap_lockin
twap_lockin_cut
```

### Research storage

Verify:

```
correct bot_id
correct window_id
correct timestamps
correct settings version
correct margin
correct quotes
correct adverse state
```

### Failure isolation

Simulate research DB write failure and verify:

```
strategy evaluation still proceeds
```

### No look-ahead

Verify outcome/final result cannot affect entry decision.

---

# 28. DO NOT FIX PHASE 1 HIGH ISSUES YET

Phase 1 identified HIGH risks including:

- oracle freshness;
- PTB timestamp/carry;
- stale market quote;
- unrealistic paper SELL depth;
- decisive-local settlement reconciliation;
- failed-cut timer reset;
- one-entry lock lost on restart;
- non-atomic trade/state persistence;
- resolution parser behavior.

DO NOT modify these behaviors in Phase 2A.

Instrumentation should expose them.

We need clean before/after comparisons later.

If implementation of instrumentation absolutely requires touching one of these areas, STOP and explain why before changing behavior.

---

# 29. DO NOT CHANGE STRATEGY PARAMETERS

Specifically do NOT change:

### Bot A

```
90s
0.3bps
95¢
0.5bps
2s
$100
```

### Bot B

```
60s
2.5bps
90¢
$100
```

Do not RESET either bot.

---

# 30. DO NOT CREATE EXPERIMENTAL BOT C YET

Later we may create a controlled pair such as:

```
same entry settings
same market stream
same side
same entry

Bot B:
no cut

Bot C:
cut
```

to isolate cut-loss effect.

Not in Phase 2A.

First make the research dataset trustworthy.

---

# 31. OPTIONAL API

If useful, add read-only endpoints such as:

```
/api/research/status
/api/research/rounds
```

but only if consistent with project architecture.

Do not build a complex frontend yet.

The important artifact is the dataset.

---

# 32. IMPLEMENTATION REPORT

After implementation create:

```
PHASE_2A_RESEARCH_INSTRUMENTATION.md
```

The report must contain:

## A. Objective

## B. Phase 1 Problem Being Solved

## C. Architecture

## D. Database Schema

Show every research table and field.

## E. Sampling Model

Frequency and collection window.

## F. Oracle Data Captured

## G. Market Data Captured

## H. Bot Decision Data Captured

## I. Execution Events Captured

## J. Settings Versioning

## K. Outcome Labeling

## L. Replay Capability

What can now be replayed.

## M. Remaining Replay Limitations

Be explicit.

## N. Performance Impact

## O. Storage Estimate

1d / 7d / 30d.

## P. Failure Isolation

What happens if research persistence fails.

## Q. Tests

Tests executed and results.

## R. Files Changed

Every modified/created file.

## S. Confirmation of Trading Non-Regression

Explicitly state whether:

```
entry logic unchanged
exit logic unchanged
cut logic unchanged
settlement logic unchanged
settings unchanged
bot balances/history unchanged
```

## T. How To Verify Collection

Exact commands/API/SQL needed to confirm that data is accumulating.

---

# 33. IMPORTANT VALIDATION AFTER IMPLEMENTATION

After implementing, do NOT simply say "done".

Inspect newly collected research rows if the application is already running and schema can safely become active without restart.

If restart is required:

DO NOT restart the running bots automatically.

Instead clearly state:

```
RESTART REQUIRED
```

and provide the exact safe restart procedure.

If restart is NOT required, demonstrate that new records are appearing.

---

# 34. FINAL RESPONSE

At completion reply with:

### Implementation

- files changed;
- tables created;
- sampling frequency;
- collection horizon;
- whether restart is required.

### Non-regression

Explicitly:

```
Bot A trading logic: UNCHANGED / CHANGED
Bot B trading logic: UNCHANGED / CHANGED
Bot A settings: UNCHANGED / CHANGED
Bot B settings: UNCHANGED / CHANGED
Existing trade history: UNCHANGED / CHANGED
Existing balances: UNCHANGED / CHANGED
```

Anything other than UNCHANGED must be explained.

### Research readiness

Report:

```
Can entry_window be replayed?
Can min_margin be replayed?
Can max_entry be replayed?
Can cut_margin be replayed?
Can cut_confirmation be replayed?
Can profit target be replayed?
Can realistic bid-depth execution be approximated?
```

Use:

```
YES
PARTIAL
NO
```

with explanation.

### Next step

State exactly how much data you recommend collecting before Phase 2B.

Do NOT start Phase 2B.

Do NOT optimize parameters.

Do NOT change bot settings.

STOP and wait for approval.