# PHASE 2A — POST-RESTART VALIDATION

Phase 2A instrumentation has now been implemented and the backend has been restarted.

DO NOT modify code.

DO NOT modify bot settings.

DO NOT restart/reset bots.

DO NOT delete or alter research data.

DO NOT start Phase 2B.

This task is READ-ONLY validation of the research data now being collected.

Primary objective:

> Prove that Phase 2A is actually collecting a coherent dataset that will later support counterfactual replay.

Primary bots:

```
Bot A
b_0d55c464
BTC
twap_lockin_cut

Bot B
b_384a1650
BTC
twap_lockin
```

---

# 1. VERIFY CURRENT RUNTIME

First confirm:

- backend process is running;
- port 9000 is served by the restarted build;
- GET /api/bots works;
- Bot A exists and is running;
- Bot B exists and is running;
- actual current parameters of both bots;
- neither bot was reset;
- balances/history survived restart.

Report the actual values.

Do NOT change anything.

---

# 2. RUN RESEARCH QUALITY CHECK

Run:

```
.\.venv\Scripts\python.exe scripts\research_quality.py
```

Include the COMPLETE output in the report.

Then independently verify the important numbers directly from SQLite rather than relying only on the script.

Database:

```
data\twaphunter.db
```

Use read-only queries where practical.

---

# 3. VERIFY ALL RESEARCH TABLES

Inspect:

```
research_settings_versions
research_market_snapshots
research_oracle_samples
research_bot_decisions
research_execution_events
```

For each table report:

```
row count
earliest timestamp
latest timestamp
covered duration
distinct BTC window count
```

where applicable.

Also show the schema/important columns.

---

# 4. PROVE DATA IS STILL ACCUMULATING

We need evidence that instrumentation is live.

Take counts at T0.

Wait approximately 10 seconds.

Take counts again at T1.

Report:

```
table
count T0
count T1
delta
```

Expected:

- market snapshots should increase;
- oracle samples should increase when source updates arrive;
- Bot A/B decisions should increase while evaluation applies;
- execution events may remain unchanged if no trading event occurs.

Do not fabricate expected counts.

Report actual observations.

---

# 5. SAMPLING QUALITY

Analyze `research_market_snapshots`.

For the currently active/recent BTC window calculate:

```
number of snapshots
first timestamp
last timestamp
median interval
p95 interval
maximum interval
number of gaps >1.5s
number of gaps >2s
number of gaps >5s
```

Also calculate these metrics across all completed research windows currently available.

We are targeting approximately 1 Hz.

Do not require exactly 1000ms.

We need to identify meaningful holes.

---

# 6. BOT DECISION SAMPLING

Analyze `research_bot_decisions` separately for:

```
b_0d55c464
b_384a1650
```

For each:

```
row count
distinct windows
first timestamp
last timestamp
median interval
p95 interval
max gap
```

Verify that decision rows belong to the correct bot ID and strategy.

Verify that settings/settings_version association resolves correctly.

---

# 7. SETTINGS VERSION VALIDATION

Inspect `research_settings_versions`.

Show the active recorded settings snapshot for A and B.

Verify it agrees with the actual current runtime configuration.

Important:

Do NOT assume settings from old Phase 1 documentation.

Compare:

```
research settings version
vs
bots table
vs
GET /api/bots
```

Report PASS/FAIL for each bot.

If multiple settings versions exist, explain why and show timestamps.

Do not modify them.

---

# 8. MARKET SNAPSHOT EXAMPLE

Choose one recent BTC market/window with good coverage.

Show approximately 10 consecutive snapshot rows around a meaningful point, preferably around:

```
T-60s
```

or an actual BUY if one has occurred since restart.

Include at minimum:

```
timestamp
window_id
remaining_ms

PTB
PTB source
PTB source timestamp/age if available

Chainlink price
Chainlink age

TWAP60
TWAP60 age

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

quote age
```

Use the actual column names if they differ.

The purpose is to visually verify that a snapshot contains enough state for replay.

---

# 9. ORACLE SAMPLE VALIDATION

Inspect `research_oracle_samples`.

For a recent BTC window report:

```
number of Chainlink samples
number of TWAP60 samples

source timestamp range
receive timestamp range

median Chainlink interval
p95 Chainlink interval
max Chainlink gap
```

Check:

- timestamps are monotonic where expected;
- prices are plausible numeric values;
- duplicate behavior;
- samples are associated with BTC correctly.

Do NOT change deduplication behavior.

Report anomalies.

---

# 10. PROJECTED FINAL CONSISTENCY CHECK

This is important.

Select at least 10 research market snapshots from different remaining times such as approximately:

```
T-90
T-75
T-60
T-50
T-40
T-30
T-20
T-10
```

where data exists.

Using persisted oracle samples, independently reconstruct:

```
RollingTwap.value_at(window_end)
```

according to the CURRENT production algorithm.

Compare:

```
stored projected_final
vs
reconstructed projected_final
```

and:

```
stored projected_margin_bps
vs
reconstructed projected_margin_bps
```

Report absolute differences.

Do NOT change production code if there is a mismatch.

Just report it.

This test tells us whether future offline replay can reproduce the live signal.

---

# 11. DECISION CONSISTENCY CHECK

For at least 10 decision rows for Bot A and 10 for Bot B, independently verify the recorded decision against the corresponding market snapshot/settings.

For example verify:

```
remaining inside/outside entry window

abs(projected_margin_bps) >= min_margin_bps

candidate side from margin sign

ask <= max_entry_cents

spread condition

already entered condition

position state

entry_allowed
```

Report:

```
checked rows
matching rows
mismatches
```

If mismatches exist, show them individually.

Do NOT fix them yet.

---

# 12. JOIN CONSISTENCY

Verify that we can reliably join:

```
market snapshot
        ↓
window_id + timestamp

bot decision
        ↓
bot_id + window_id + timestamp

execution event
        ↓
trade / position

round
        ↓
final outcome
```

Check for:

```
orphan decisions
orphan execution events
unknown window IDs
missing bot IDs
missing strategy/settings references
```

Report counts.

The goal is to ensure a future replay can reconstruct the complete lifecycle.

---

# 13. EXECUTION EVENTS

Inspect `research_execution_events`.

If trading events have occurred since restart, show the complete lifecycle for at least one trade from BUY through exit/settlement.

Prefer one from each bot if available.

We want events such as:

```
ENTRY_ATTEMPT
ENTRY_SUCCESS

PROFIT_SELL_ATTEMPT
PROFIT_SELL_SUCCESS

CUT_TIMER_STARTED
CUT_CONFIRMED
CUT_SELL_ATTEMPT
CUT_SELL_SUCCESS/FAILED

SETTLEMENT...
```

depending on what actually happened.

For each actual trade verify:

```
bot_id
window_id
side
entry price
shares
bid/ask
available size
projected margin
settings version
timestamps
exit reason
```

If no trade has happened since restart:

DO NOT manufacture or trigger one.

Simply state:

```
No complete post-restart trade lifecycle available yet.
```

---

# 14. BOT A CUT INSTRUMENTATION

If Bot A has experienced an adverse condition since restart, verify that the research data captures:

```
margin before adverse
adverse threshold crossing
adverse_since
duration
cut confirmation
SELL attempt
SELL result
bid
bid_size
shares
```

If none occurred, state that validation is pending natural occurrence.

Do NOT intentionally create one.

---

# 15. NO LOOK-AHEAD CHECK

Verify from persisted rows that entry/decision records do NOT contain future information usable by the live strategy.

Specifically inspect whether fields such as:

```
outcome_official
final_twap
final outcome
future oracle observations
```

are absent from the live decision input or only joined later as post-resolution labels.

Report:

```
PASS
```

or explain the problem.

---

# 16. REPLAY COVERAGE

For every completed BTC research window currently available, classify:

```
REPLAYABLE
PARTIAL
NOT REPLAYABLE
```

Define criteria explicitly.

At minimum consider:

```
PTB available
oracle coverage
market snapshots
quotes
settings version
outcome label
acceptable snapshot gaps
```

Report:

```
total completed windows
replayable
partial
not replayable
replayable %
```

Also list the reason counts for incomplete windows.

Do not lower quality criteria just to obtain a high percentage.

---

# 17. IMPORTANT — ENTRY COUNTERFACTUAL

Verify specifically whether the collected data is sufficient to ask:

```
At T-60, T-55, T-50, T-45, T-40, T-30:

What would the strategy have done with:

min_margin = X
max_entry = Y
?
```

Answer based on the actual persisted fields.

For each parameter:

```
entry_window_s
min_margin_bps
max_entry_cents
```

classify:

```
YES
PARTIAL
NO
```

and explain any limitation.

---

# 18. IMPORTANT — CUT COUNTERFACTUAL

Verify whether the collected data is sufficient to replay:

```
cut_margin_bps
cut_confirm_s
```

on an identical entry.

We need the time trajectory of:

```
projected_margin
token bid
bid_size
remaining time
```

after entry.

Classify:

```
YES
PARTIAL
NO
```

and explain.

---

# 19. PROFIT TARGET COUNTERFACTUAL

Phase 2A previously classified changing profit99 as PARTIAL.

Explain precisely why.

Determine what can and cannot be simulated for hypothetical targets such as:

```
95¢
96¢
97¢
98¢
99¢
```

using the newly collected bid/ask stream.

Separate:

```
paper-model replay
```

from:

```
realistic executable replay
```

because bid depth/slippage may differ.

Do NOT modify profit99.

---

# 20. DATA SIZE

Report current:

```
twaphunter.db size
research table row counts
```

If practical, estimate actual bytes/row or actual observed growth.

Compare with the earlier estimate:

```
90–120 MB/day
```

Do not extrapolate confidently from only a few seconds of data.

Label estimates appropriately.

---

# 21. PERFORMANCE IMPACT

Inspect whether instrumentation appears to affect the main loop.

Use available timestamps/logs/metrics.

Report:

```
snapshot persistence latency if measurable
research queue/backlog if applicable
SQLite errors
database locked errors
dropped research records
BotManager loop anomalies
```

Do NOT run destructive stress tests against the live bot.

---

# 22. CHECK APPLICATION LOGS

Inspect post-restart logs for:

```
research
SQLite
database locked
exception
traceback
BotManager
Pipeline
oracle
CLOB
```

Report relevant warnings/errors.

Do not dump thousands of irrelevant normal log lines.

---

# 23. DO NOT CHANGE ANYTHING

This validation phase is strictly read-only.

Do NOT:

```
edit code
edit DB
change bot params
reset bots
restart backend
create test trades
start Phase 2B
run optimization
```

If you find a defect:

document it.

Do not repair it yet.

---

# 24. CREATE VALIDATION REPORT

Create:

```
docs/PHASE_2A_POST_RESTART_VALIDATION.md
```

The report must contain:

## A. Runtime State

## B. Bot A/B Current Configuration

## C. Research Table Counts

## D. Live Accumulation Test

## E. Sampling Quality

## F. Settings Version Validation

## G. Market Snapshot Example

## H. Oracle Sample Validation

## I. Projected Final Reconstruction

## J. Decision Consistency

## K. Join Integrity

## L. Execution Events

## M. Bot A Cut Instrumentation

## N. No-Look-Ahead Validation

## O. Replay Coverage

## P. Entry Counterfactual Readiness

## Q. Cut Counterfactual Readiness

## R. Profit Target Replay Limitations

## S. Data Growth

## T. Runtime/SQLite Health

## U. Problems Found

## V. Phase 2A Verdict

---

# 25. FINAL VERDICT

Finish with exactly one of:

```
PHASE 2A VALIDATED
```

or:

```
PHASE 2A VALIDATED WITH LIMITATIONS
```

or:

```
PHASE 2A NOT VALIDATED
```

Explain why.

Then answer:

```
Should we continue collecting data? YES/NO

Is a code fix required before collecting more data? YES/NO

Is restart required? YES/NO

Is Phase 2B ready to start? YES/NO
```

For Phase 2B, answer YES only if enough NATURAL historical data has actually accumulated for meaningful analysis.

Even if the architecture is correct, a few minutes of data is not enough to optimize the strategy.

---

# 26. WHAT TO RETURN TO ME

In your final response provide:

1. the complete output of `scripts/research_quality.py`;
2. current Bot A/B settings;
3. research table row counts;
4. number of completed research windows;
5. replayable percentage;
6. snapshot median/p95/max interval;
7. oracle median/p95/max interval;
8. projected_final reconstruction result;
9. decision consistency result;
10. orphan/missing-reference counts;
11. number of execution events;
12. whether a complete trade lifecycle has been captured;
13. errors/warnings found;
14. Phase 2A verdict;
15. exact path:

```
docs/PHASE_2A_POST_RESTART_VALIDATION.md
```

Do NOT make further changes.

STOP after reporting.