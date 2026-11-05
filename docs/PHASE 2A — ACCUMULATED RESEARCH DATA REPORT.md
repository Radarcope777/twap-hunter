# PHASE 2A — ACCUMULATED RESEARCH DATA REPORT

We have been collecting Phase 2A BTC TWAP research data since the instrumentation restart.

Repository:

```
D:\TEST\twap-hunter
```

Existing reference documents:

```
docs/PHASE_2A_RESEARCH_INSTRUMENTATION.md
docs/PHASE_2A_POST_RESTART_VALIDATION.md
```

Read both documents before analysis.

This task is strictly READ-ONLY.

DO NOT:

- modify code;
- modify database rows;
- modify bot settings;
- reset bots;
- restart backend;
- create migrations;
- create synthetic trades;
- change research instrumentation;
- start Phase 2B;
- run parameter optimization;
- run a strategy grid search.

The purpose is:

> Determine how much usable Phase 2A data has accumulated and whether the dataset is mature enough to proceed to Phase 2B replay research.

Primary database:

```
data\twaphunter.db
```

Primary bots:

```
Bot A
b_0d55c464
twap_lockin_cut

Bot B
b_384a1650
twap_lockin
```

---

# 1. CURRENT RUNTIME STATE

Confirm current runtime state without changing anything.

Report:

```
backend status
backend uptime / start time if available

Bot A status
Bot B status

Bot A current parameters
Bot B current parameters

Bot A current balance
Bot B current balance

Bot A total trades
Bot B total trades
```

Also compare current parameters with the settings versions stored in:

```
research_settings_versions
```

If parameters changed during collection, identify every settings version and its approximate validity interval.

Do NOT treat a parameter change as an error.

We need to know exactly which research periods correspond to which settings.

---

# 2. RUN CURRENT QUALITY SCRIPT

Run:

```
.\.venv\Scripts\python.exe scripts\research_quality.py
```

Include the COMPLETE output in the report.

Then independently verify important statistics directly from SQLite.

Do not rely solely on the script.

---

# 3. COLLECTION PERIOD

Determine:

```
first research timestamp
latest research timestamp

elapsed wall-clock collection period

number of BTC windows touched
number of completed BTC windows
number of currently open/incomplete windows
```

Convert collection duration into:

```
hours
days
```

where useful.

Also detect any major periods when instrumentation was not collecting.

---

# 4. RESEARCH TABLE COUNTS

Report current row counts for:

```
research_settings_versions
research_market_snapshots
research_oracle_samples
research_bot_decisions
research_execution_events
```

For each applicable table report:

```
earliest timestamp
latest timestamp
covered duration
distinct window count
```

---

# 5. DATABASE SIZE

Report:

```
twaphunter.db size
twaphunter.db-wal size if present
twaphunter.db-shm size if present
```

Estimate actual research growth rate if enough elapsed time exists.

Report approximately:

```
MB/hour
MB/day
estimated 7-day size
estimated 30-day size
```

Clearly distinguish measured values from extrapolated estimates.

---

# 6. SNAPSHOT QUALITY

Across COMPLETED BTC research windows calculate:

```
snapshot count
median snapshot interval
p95 interval
p99 interval
maximum interval

gaps >1.5s
gaps >2s
gaps >5s
gaps >10s
```

Also calculate distribution of snapshot coverage per completed window:

```
median snapshots/window
p10
p25
p75
p90
minimum
maximum
```

Identify windows with unusually poor coverage.

---

# 7. ORACLE QUALITY

For:

```
Chainlink
TWAP60
```

report:

```
sample count
median interval
p95 interval
p99 interval
max gap

gaps >2s
gaps >5s
gaps >10s

stale >5s observations
```

Also report:

```
missing PTB %
exchange_approx PTB %
missing projected_final %
```

If possible, calculate these at both:

```
snapshot level
window level
```

---

# 8. QUOTE QUALITY

Analyze the Polymarket quote tape.

Report:

```
missing quote %
stale quote >5s %

UP ask missing %
UP bid missing %

DOWN ask missing %
DOWN bid missing %
```

Also identify how often the book is effectively one-sided/locked.

For entry replay specifically calculate:

> Of snapshots during T-90 through T-10, what percentage have a usable ask for at least one candidate side?

Also calculate separately:

```
T-90 to T-60
T-60 to T-45
T-45 to T-30
T-30 to T-10
```

This matters for future entry-window optimization.

---

# 9. WINDOW-LEVEL REPLAYABILITY

Use the same criteria as:

```
docs/PHASE_2A_POST_RESTART_VALIDATION.md
```

unless the criteria are clearly defective.

Do not silently change them.

If you believe a criterion should change, show BOTH:

```
original classification
proposed classification
```

For every completed BTC research window classify:

```
REPLAYABLE
PARTIAL
NOT REPLAYABLE
```

Report totals:

```
completed windows
replayable
partial
not replayable

replayable %
partial %
not replayable %
```

Also report reason counts:

```
missing PTB
exchange_approx PTB
insufficient oracle coverage
snapshot gaps
quote holes
missing settings version
missing outcome
other
```

---

# 10. IMPORTANT — FULLY CAPTURED WINDOWS

Distinguish:

```
windows started before instrumentation/restart
```

from:

```
windows captured naturally from approximately T-300 through resolution
```

Report the number of FULLY CAPTURED completed windows.

This number is more important than total touched windows.

---

# 11. OUTCOME LABELS

For completed research windows report:

```
official outcome available
local outcome only
no outcome
official/local conflicts
```

Then show:

```
UP outcomes
DOWN outcomes
```

among fully captured/replayable windows.

We need to make sure the sample is not accidentally one-sided.

---

# 12. SETTINGS VERSION COVERAGE

For every settings version report:

```
bot_id
strategy
created time
params

number of decisions
number of windows
number of successful entries
number of completed trades
```

If Bot A/B settings never changed, state that clearly.

If they did change, do NOT merge their performance blindly.

---

# 13. EXECUTION EVENT SUMMARY

Summarize:

```
ENTRY_ATTEMPT
ENTRY_REJECTED
ENTRY_SUCCESS

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

Use the actual event names implemented in the project.

For each event type report count.

For entry rejections report counts by:

```
failure_reason
```

---

# 14. NATURAL TRADES SINCE INSTRUMENTATION

This section is important.

Count only trades occurring AFTER Phase 2A instrumentation became active.

For each bot report:

```
entries
completed trades
open positions

wins
losses
win rate

net realized P&L
gross profit
gross loss

average win
average loss

profit factor
```

Separate exits into:

```
profit target
cut
settlement win
settlement loss
other
```

Do NOT mix pre-Phase-2A trades into these statistics.

---

# 15. BOT A CUT OBSERVATIONS

For Bot A specifically report:

```
CUT_TIMER_STARTED count
CUT_TIMER_RESET count
CUT_CONFIRMED count
CUT_SELL_ATTEMPT count
CUT_SELL_SUCCESS count
CUT_SELL_FAILED count
```

Then identify the number of DISTINCT TRADES that experienced an adverse/cut episode.

For each completed cut trade report:

```
window_id
side
entry time
entry price
entry projected margin

first adverse timestamp
margin at first adverse

confirmed timestamp
confirmation duration

sell timestamp
sell bid
bid size
shares

realized P&L
final market outcome
```

If there are still no natural cut episodes, state that explicitly.

---

# 16. SETTLEMENT LOSSES

For post-instrumentation trades that reached settlement and lost, report each one.

Include:

```
bot_id
window_id
side

entry time
entry price
remaining time at entry

entry projected margin

maximum favorable projected margin after entry
maximum adverse projected margin after entry

whether Bot A cut threshold was crossed
how long it stayed crossed

whether a cut attempt occurred

final outcome
realized loss
```

This is one of the main reasons we built Phase 2A.

Do NOT infer unavailable values.

Use the recorded trajectory.

---

# 17. ENTRY PRICE DISTRIBUTION

For successful post-instrumentation entries, bucket by entry price:

```
<50¢
50–59¢
60–69¢
70–74¢
75–79¢
80–84¢
85–87¢
88–89¢
90–92¢
93–95¢
```

For each bucket report:

```
entries
wins
losses
WR
net P&L
avg P&L
```

Keep Bot A and Bot B separate.

If sample sizes are tiny, clearly say so.

Do NOT optimize based on small buckets yet.

---

# 18. ENTRY MARGIN DISTRIBUTION

Bucket successful entries by absolute projected margin:

```
0.0–0.49 bps
0.5–0.99
1.0–1.99
2.0–2.99
3.0–3.99
4.0–4.99
5.0–7.49
7.5–9.99
10+
```

For each bot/bucket report:

```
N
W
L
WR
net P&L
avg P&L
```

Again: descriptive analysis only.

No parameter recommendation yet.

---

# 19. ENTRY TIME DISTRIBUTION

Bucket by remaining time at actual entry:

```
75–90s
60–74s
50–59s
40–49s
30–39s
20–29s
10–19s
```

For each bot report:

```
N
W
L
WR
net P&L
avg P&L
```

Do not silently discard entries outside these bins; report them separately.

---

# 20. SIDE DISTRIBUTION

For post-instrumentation trades separately for each bot:

```
UP:
N / W / L / WR / P&L

DOWN:
N / W / L / WR / P&L
```

Also report outcome distribution of the underlying research windows.

---

# 21. REPLAY SIGNAL RECONSTRUCTION

Repeat the earlier reconstruction test on a much larger sample.

Randomly/deterministically sample at least:

```
100 market snapshots
```

across different completed windows and remaining times.

Reconstruct from persisted oracle samples:

```
projected_final
projected_margin_bps
```

Compare to stored values.

Report:

```
rows tested
exact matches
mismatches

max projected_final error
mean projected_final error

max margin error
mean margin error
```

If fewer than 100 eligible rows exist, test all eligible rows.

Do NOT modify anything if mismatches occur.

Show examples.

---

# 22. DECISION RECONSTRUCTION

Sample at least:

```
100 Bot A decisions
100 Bot B decisions
```

across multiple windows.

Reconstruct:

```
candidate side
entry window gate
margin gate
quote availability
price cap
spread gate
position gate
one-entry gate
entry_allowed
```

Report separately:

```
Bot A checked / matched / mismatched
Bot B checked / matched / mismatched
```

Show every mismatch category.

---

# 23. JOIN INTEGRITY

Recheck:

```
orphan decisions
orphan execution events
missing settings versions
unknown window IDs
missing bot IDs
missing strategies
research windows with no rounds row
completed research windows with no outcome
```

All counts must be reported.

---

# 24. DATA CONTINUITY

Identify outages.

Find periods after instrumentation start with:

```
no market snapshots for >10 seconds
```

Report:

```
start
end
duration
affected window
```

Separate:

```
known restart/startup gaps
```

from unexplained gaps.

Also report the longest unexplained gap.

---

# 25. NO-LOOK-AHEAD RECHECK

Verify that the architecture still preserves:

```
decision time data
```

separately from:

```
post-resolution labels
```

Confirm no final outcome/final TWAP is being used by live decision logic.

Report PASS/FAIL.

---

# 26. COUNTERFACTUAL READINESS

Based on the accumulated tape, classify:


| Parameter                       | YES / PARTIAL / NO |
| ------------------------------- | ------------------ |
| entry_window_s                  |                    |
| min_margin_bps                  |                    |
| max_entry_cents                 |                    |
| cut_margin_bps                  |                    |
| cut_confirm_s                   |                    |
| profit_target_cents             |                    |
| bid-depth-aware paper execution |                    |


Explain each limitation.

This should reflect the CURRENT accumulated dataset, not the initial 17-minute validation.

---

# 27. SAMPLE SUFFICIENCY

This is NOT optimization.

Assess whether we have enough data to BEGIN Phase 2B.

Use objective counts.

Report:

```
fully captured completed windows
replayable windows

Bot A natural entries
Bot B natural entries

Bot A completed trades
Bot B completed trades

Bot A cut episodes
Bot A settlement losses
Bot B settlement losses
```

Then classify dataset maturity:

```
TOO EARLY

EARLY / EXPLORATORY ONLY

SUFFICIENT FOR INITIAL PHASE 2B REPLAY

SUFFICIENT FOR BROADER PARAMETER RESEARCH
```

Explain why.

Do NOT choose parameters.

Do NOT recommend a winning configuration.

---

# 28. SUGGESTED DATA THRESHOLDS

Compare actual data to these working checkpoints:

### Initial Phase 2B checkpoint

Prefer approximately:

```
100–150 fully captured BTC 5m windows
```

with multiple natural entries and preferably multiple cut/adverse episodes.

### Stronger research checkpoint

Prefer approximately:

```
300–500 fully captured windows
```

before drawing stronger conclusions about parameter performance.

These are working research thresholds, not statistical guarantees.

Report actual progress as:

```
X / 100
X / 150
X / 300
X / 500
```

for fully captured windows.

---

# 29. IMPORTANT: DO NOT OPTIMIZE YET

Even if enough data exists:

DO NOT run:

```
entry_window grid
margin grid
price-cap grid
cut grid
profit-target grid
```

in this task.

First validate the accumulated dataset.

Phase 2B will be authorized separately.

---

# 30. CREATE REPORT

Create:

```
docs/PHASE_2A_ACCUMULATED_DATA_REPORT.md
```

Required sections:

## A. Executive Summary

## B. Runtime / Current Bot State

## C. Collection Period

## D. Research Table Counts

## E. Database Growth

## F. Snapshot Quality

## G. Oracle Quality

## H. Quote Quality

## I. Window Replayability

## J. Outcome Coverage

## K. Settings Versions

## L. Execution Events

## M. Post-Instrumentation Bot A Performance

## N. Post-Instrumentation Bot B Performance

## O. Bot A Cut Episodes

## P. Settlement Loss Analysis

## Q. Entry Price Distribution

## R. Entry Margin Distribution

## S. Entry Time Distribution

## T. UP vs DOWN

## U. Projected Final Reconstruction

## V. Decision Reconstruction

## W. Join Integrity

## X. Data Continuity

## Y. No-Look-Ahead Check

## Z. Counterfactual Readiness

## AA. Sample Sufficiency

## AB. Problems / Limitations

## AC. Verdict

---

# 31. FINAL VERDICT

Finish with:

```
PHASE 2A DATASET STATUS:
<TOO EARLY / EARLY-EXPLORATORY / READY FOR INITIAL PHASE 2B / READY FOR BROADER RESEARCH>
```

Then:

```
Fully captured completed windows: X
Replayable windows: X

Bot A post-instrumentation completed trades: X
Bot B post-instrumentation completed trades: X

Bot A natural cut episodes: X

Bot A settlement losses: X
Bot B settlement losses: X

Projected-final reconstruction: PASS/FAIL
Decision reconstruction: PASS/FAIL
Join integrity: PASS/FAIL
No-look-ahead: PASS/FAIL

Continue collecting before Phase 2B: YES/NO
Code fix required before continuing: YES/NO
Restart required: YES/NO
```

If continuing collection is recommended, estimate approximately how many MORE fully captured windows are needed to reach:

```
100
150
300
500
```

Do not estimate elapsed time unless useful.

---

# 32. RETURN TO ME

In your final response provide:

1. complete `research_quality.py` output;
2. collection start/end and duration;
3. completed windows;
4. fully captured windows;
5. replayable windows;
6. table row counts;
7. snapshot median/p95/max;
8. oracle median/p95/max;
9. quote missing/stale rates;
10. Bot A post-instrumentation performance;
11. Bot B post-instrumentation performance;
12. cut event counts;
13. settlement losses;
14. projected-final reconstruction result;
15. decision reconstruction result;
16. join-integrity result;
17. major data gaps/outages;
18. counterfactual-readiness table;
19. sample-sufficiency classification;
20. exact report path:

```
docs/PHASE_2A_ACCUMULATED_DATA_REPORT.md
```

DO NOT make any changes after producing the report.

STOP.