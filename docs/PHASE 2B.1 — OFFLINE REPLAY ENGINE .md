# PHASE 2B.1 — OFFLINE REPLAY ENGINE + PRODUCTION PARITY VALIDATION

Repository:

```
D:\TEST\twap-hunter
```

Read these reports first:

```
docs/PHASE_1_BTC_BOTS_STRATEGY_AUDIT.md
docs/PHASE_2A_RESEARCH_INSTRUMENTATION.md
docs/PHASE_2A_POST_RESTART_VALIDATION.md
docs/PHASE_2A_ACCUMULATED_DATA_REPORT.md
```

Database:

```
data\twaphunter.db
```

Primary bots:

```
Bot A
b_0d55c464
strategy = twap_lockin_cut

Bot B
b_384a1650
strategy = twap_lockin
```

---

# 0. OBJECTIVE

Build an OFFLINE deterministic replay engine for the BTC TWAP strategies using the Phase 2A research tape.

The immediate objective is NOT optimization.

The objective is:

> Given the exact historical research tape and the exact production settings, reproduce production decisions, entries, exits, and P&L closely enough that we can trust the engine for later counterfactual parameter research.

Phase 2B.1 must answer:

```
Can the offline replay reproduce Bot A?
Can the offline replay reproduce Bot B?

Where does it differ?

Why does it differ?

Which differences are caused by data resolution versus replay implementation?

Is the engine safe to use for Phase 2B.2 parameter search?
```

Do NOT run parameter optimization in this phase.

---

# 1. IMPORTANT SAFETY / SCOPE RULES

Phase 2B replay must be completely isolated from live trading.

DO NOT:

- modify Bot A;
- modify Bot B;
- modify bot settings;
- modify balances;
- modify existing trades;
- modify rounds;
- modify research rows;
- restart backend;
- stop the collector;
- change Phase 2A instrumentation;
- send orders;
- call live buy/sell execution;
- change production strategy behavior.

The live collector must continue running.

Replay must be:

```
READ research tape
READ historical production trades/events
COMPUTE offline
WRITE only dedicated replay output if needed
```

Prefer output files / reports over modifying the production database.

If persistent replay tables are truly useful, use a separate replay database/file or clearly isolated namespace.

Do NOT add replay rows to production `trades`.

---

# 2. CURRENT KNOWN DATA QUALITY

From the accumulated Phase 2A report:

```
fully captured completed windows = 198
original-rule replayable windows = 104

Bot A completed trades = 88
Bot B completed trades = 29

Bot A natural cut sells = 21
```

Known bad/stalled region:

```
5971701–5971731
```

There is a ~2.29 hour collector/oracle/outcome anomaly around this region.

Thirty completed windows there have no outcome, and projected-final reconstruction failed on sampled rows in the same region.

Do NOT use this region for production-parity scoring.

Also exclude:

- incomplete windows;
- windows without outcome where outcome is required;
- windows with insufficient oracle data for deterministic reconstruction;
- startup/resume partial windows when required for full replay.

Document every exclusion.

Do not silently discard rows.

---

# 3. DO NOT USE THE OLD 104-WINDOW RULE BLINDLY

The original replayability rule included:

```
UP ask missing >10%
```

and produced a strongly biased subset.

The accumulated report found:

```
fully captured labelled:
83 UP
86 DOWN

old REPLAYABLE subset:
19 UP
85 DOWN
```

This means the old UP-ask criterion is unsuitable as the primary Phase 2B universe selector.

For Phase 2B.1 define a CLEAN REPLAY UNIVERSE based on whether the historical state required for the strategy being replayed actually exists.

A window should not be excluded merely because an irrelevant side of the book has no ask.

For a candidate action, what matters is whether the required side's quote exists at that decision point.

Document the exact clean-universe rules before running replay.

Do not optimize those rules to improve parity.

---

# 4. CLEAN REPLAY UNIVERSE

Create deterministic inclusion/exclusion logic.

At minimum require:

```
fully captured window
valid settings version
usable PTB
sufficient oracle tape
research snapshots available
bot decisions available where applicable
outcome available for settlement
outside known corrupted/stall block
```

For entry evaluation, require the candidate side's necessary quote at the simulated decision point.

Do NOT require both sides to have full bid/ask coverage throughout the whole round.

Report:

```
total completed windows
fully captured windows
clean replay windows
excluded windows
```

Break exclusions down by reason.

Also report outcome balance in clean universe:

```
UP
DOWN
```

---

# 5. TIME MODEL — CRITICAL

Production does not operate purely on the ~1.15-second snapshot grid.

Known architecture:

- research snapshots / decisions are approximately 1 Hz;
- production evaluator/order path may run faster;
- actual production fill can occur between two decision snapshots.

Therefore DO NOT implement replay as:

> read one snapshot per second and pretend production only evaluated at those timestamps.

Instead inspect the production code and Phase 2A events and determine the highest-resolution historical sequence available.

Use, in order of preference:

```
research_execution_events
research_market_snapshots
research_oracle_samples
research_bot_decisions
```

for the role each actually records.

Document the effective replay resolution.

---

# 6. TWO REPLAY MODES

Implement two explicit modes if needed.

## Mode A — PRODUCTION-PARITY REPLAY

Purpose:

Reproduce what the existing production bot actually did.

This mode may use historical execution events as timing anchors where necessary.

It is for validation.

It must NOT use future outcome to decide entries/exits.

## Mode B — COUNTERFACTUAL REPLAY

Purpose:

Later evaluate hypothetical parameters using only information that would have existed at that time.

It must NOT depend on knowing that production entered.

Phase 2B.1 does not optimize with Mode B yet.

But architecture must clearly separate Mode A from Mode B so that Phase 2B.2 cannot accidentally use production fills as look-ahead.

---

# 7. RECONSTRUCT MARKET STATE

For every replay timestamp/window reconstruct or load:

```
window_id
window_start
window_end
remaining_ms

PTB
PTB source

Chainlink observations available by that time

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
oracle age
```

Do NOT use future Chainlink samples.

Oracle availability must be based on:

```
receive_ts_ms <= replay timestamp
```

not merely source timestamp.

---

# 8. PROJECTED FINAL

Use the exact current production algorithm.

Do NOT invent a mathematically improved TWAP.

Replay production behavior.

For clean windows:

```
reconstructed projected_final
```

must be checked against:

```
research_market_snapshots.projected_final
```

and margin against:

```
projected_margin_bps
```

Report differences.

The known corrupted block must remain excluded from parity scoring.

---

# 9. BOT A PRODUCTION SETTINGS

Replay Bot A with exactly:

```
strategy = twap_lockin_cut

entry_window_s = 90
min_margin_bps = 0.3
max_entry_cents = 95

cut_margin_bps = 0.5
cut_confirm_s = 2

order_usd = 100
profit target = 99 cents
```

Use the actual settings version stored in the research tape as source of truth.

Do NOT hardcode these values if the settings-version row already supplies them.

The values above are a validation expectation only.

---

# 10. BOT B PRODUCTION SETTINGS

Replay Bot B with exactly:

```
strategy = twap_lockin

entry_window_s = 60
min_margin_bps = 2.5
max_entry_cents = 90

order_usd = 100
profit target = 99 cents

NO CUT
```

Again, use research settings version as source of truth.

---

# 11. ENTRY LOGIC

Reproduce the production entry gates exactly.

At minimum inspect and reproduce:

```
bot running / replay active
correct asset/pipeline
no open position
one entry per window
remaining time gate
PTB validity
PTB source restrictions
projected_final availability
projected margin threshold
candidate side from margin sign
candidate ask exists
ask > 0
ask < 100
ask <= max_entry_cents
bid/spread conditions
minimum shares
balance/capital model
fees
```

Do NOT add new quality gates.

Do NOT add quote freshness gates unless production currently uses them.

Do NOT add oracle freshness gates unless production currently uses them.

Replay production, not the strategy we wish production had.

---

# 12. IMPORTANT — CAPITAL / BALANCE MODE

Production Bot A eventually reached approximately zero available balance, causing many:

```
INSUFFICIENT_BALANCE
```

rejections.

For parity validation, support:

```
capital_mode = production_wallet
```

This mode should reproduce actual balance constraints.

But architect replay so Phase 2B.2 can later use:

```
capital_mode = normalized
```

for example independent fixed starting capital or per-trade capital.

DO NOT use normalized capital for Phase 2B.1 parity scoring.

We need production-wallet parity first.

---

# 13. POSITION SIZING

Reproduce the current production/paper sizing formula exactly.

Inspect code rather than relying on documentation.

Include:

```
order_usd
ask
ask_size
shares
cost
entry fee
```

Use production fee formula.

Do not introduce slippage or deeper-book execution in parity mode if production did not use it.

---

# 14. BOT A CUT LOGIC

Reproduce the exact existing cut state machine.

Important semantics include:

```
adverse threshold direction by held side
strict threshold behavior
adverse_since
timer reset behavior
cut_confirm_s
behavior when projected margin becomes None
sell attempt behavior
timer behavior after failed sell
```

Use the actual current production code as authoritative.

Do not "fix" known oddities in replay.

Replay them.

---

# 15. PROFIT EXIT

Reproduce the hardcoded production rule:

```
held-side bid >= 99 cents
```

Sell at the production paper execution price/rules.

Do not substitute ask/mid/last.

---

# 16. SETTLEMENT

Reproduce production settlement as closely as the historical tape allows.

For parity mode:

- use outcome only once settlement is reached;
- outcome must not affect earlier decisions;
- reproduce settlement payout/fee/accounting;
- distinguish profit sell, cut sell, settlement win, settlement loss.

Document any settlement behavior that cannot be reconstructed exactly.

---

# 17. FEES AND P&L

Use production formulas.

For every replayed completed trade calculate:

```
entry cost
entry fee

exit proceeds
exit fee

realized P&L
```

Do not use simplified:

```
win = +X
loss = -Y
```

when actual price/size data exists.

---

# 18. PRODUCTION TRADE BASELINE

Build the baseline from actual post-instrumentation production trades.

Separate:

```
Bot A
Bot B
```

For every actual trade collect:

```
bot_id
window_id
side

production entry timestamp
production entry price
production shares

production exit timestamp
production exit reason
production exit price

production realized P&L
```

Only include trades/windows that satisfy the clean parity-universe criteria.

Report how many production trades are eligible for parity scoring.

---

# 19. TRADE-BY-TRADE PARITY TABLE

For every eligible production trade compare:

```
window_id

production entered? 
replay entered?

production side
replay side

production entry timestamp
replay entry timestamp
entry timing delta_ms

production entry price
replay entry price
entry price delta

production shares
replay shares

production exit reason
replay exit reason

production exit timestamp
replay exit timestamp
exit timing delta_ms

production exit price
replay exit price

production P&L
replay P&L
P&L difference

parity classification
mismatch reason
```

Write the full table to CSV if it is large.

Suggested:

```
research_output/phase2b1_trade_parity.csv
```

Create directory if needed.

Do not overwrite production data.

---

# 20. PARITY CLASSIFICATIONS

Classify each production trade:

```
EXACT
NEAR_MATCH
MISMATCH
UNREPLAYABLE
```

Define these before computing results.

Suggested concept:

## EXACT

Same:

```
window
entry yes/no
side
entry price
exit reason
exit price
```

and timing difference within expected tape/evaluator resolution.

## NEAR_MATCH

Same economic trade but timestamp/price differs slightly due to sampling resolution.

## MISMATCH

Replay behavior is materially different.

Examples:

```
no replay entry
extra replay entry
wrong side
different exit reason
materially different price
materially different P&L
```

## UNREPLAYABLE

Required historical state is absent/corrupt.

Do not classify missing-data cases as strategy mismatches.

---

# 21. FALSE POSITIVES — CRITICAL

Production-trade matching alone is not enough.

Replay may reproduce all production trades while generating many trades production never made.

Therefore also compare ALL CLEAN WINDOWS.

Report:

```
production entry + replay entry
production entry + no replay entry
no production entry + replay entry
no production entry + no replay entry
```

This is effectively an entry confusion matrix.

Do this separately for Bot A and Bot B.

The:

```
no production entry + replay entry
```

category is especially important.

List those windows.

---

# 22. ENTRY PARITY METRICS

For each bot calculate:

```
eligible clean windows

production entry windows
replay entry windows

true matched entries
missed production entries
extra replay entries
wrong-side entries

entry-window agreement %
side agreement %

median entry timing delta
p95 entry timing delta
max entry timing delta

median entry-price delta
max entry-price delta
```

---

# 23. EXIT PARITY METRICS

For matched entries calculate:

```
same exit reason %
same exit price %

profit-target parity
cut parity
settlement parity

median exit timing delta
p95 exit timing delta
max exit timing delta
```

For Bot A separately report:

```
production cut trades
replay cut trades
matched cuts
missed cuts
extra cuts
```

---

# 24. P&L PARITY

For eligible production trades compare aggregate production vs replay.

For each bot:

```
production trades
replay trades

production W/L
replay W/L

production net P&L
replay net P&L

absolute P&L difference
relative P&L difference

production gross profit
replay gross profit

production gross loss
replay gross loss

production profit factor
replay profit factor
```

Do not call replay validated merely because aggregate P&L happens to be similar.

Trade-level parity is primary.

---

# 25. CUT PARITY — BOT A

The dataset contains approximately:

```
21 natural completed cut sells
```

Use these as a dedicated validation set.

For each compare:

```
first adverse crossing
production adverse_since
replay adverse_since

production confirmation time
replay confirmation time

production sell attempt
replay sell attempt

production exit
replay exit
```

Report:

```
cut episodes eligible
exact/near matches
mismatches
unreplayable
```

This is a critical acceptance test.

---

# 26. BOT B LOSS CASES

Explicitly inspect the known Bot B settlement-loss windows from the accumulated report.

At minimum inspect:

```
5971564
5971597
```

Replay Bot B with NO CUT and confirm it holds according to production behavior.

Then, WITHOUT counting this as parameter optimization, perform a diagnostic only:

> Does the replay trajectory contain enough information that a hypothetical Bot-A-style 0.5 bps / 2 s cut could be evaluated later?

Answer YES/NO.

Do not include hypothetical cut P&L in the main parity result.

---

# 27. BOT A SETTLEMENT LOSS

Explicitly inspect Bot A settlement-loss window:

```
5971664
```

Verify that replay does NOT invent a cut if the production trajectory never satisfied the cut rule.

The accumulated report says the recorded trajectory did not cross the 0.5 bps adverse threshold.

Parity should reproduce that behavior.

---

# 28. KNOWN STALL BLOCK

Create explicit exclusion constant/config for Phase 2B.1:

```
5971701–5971731
```

Do not delete those rows.

Do not pretend they do not exist.

Mark:

```
EXCLUDED_DATA_QUALITY
```

with reasons:

```
missing outcomes
oracle/reconstruction inconsistency
pre-outage region
```

Architecture should allow additional bad ranges to be supplied later without changing replay logic.

---

# 29. DATA-QUALITY MASK

Implement a reusable replay data-quality mask.

Suggested output per window:

```
window_id
fully_captured
has_outcome
ptb_valid
oracle_sufficient
snapshot_sufficient
settings_available
known_bad_range
eligible_for_parity
exclusion_reasons[]
```

Save:

```
research_output/phase2b1_window_quality.csv
```

This will be reused in Phase 2B.2.

---

# 30. NO LOOK-AHEAD GUARANTEE

Counterfactual engine must enforce:

```
oracle receive_ts <= replay time
quote timestamp <= replay time
settings version valid at replay time
outcome unavailable until settlement
```

Add tests specifically proving future rows cannot affect an earlier replay state.

This is mandatory.

---

# 31. DETERMINISM

Replay must be deterministic.

Running:

```
same database snapshot
same universe
same bot settings
```

twice must produce identical results.

Add a determinism test.

If database continues growing while collector runs, define a replay cutoff timestamp at the start of each run.

For example:

```
replay_cutoff_ms = MAX(research_market_snapshots.t_ms)
```

captured once at replay start.

All replay queries must obey that cutoff.

This prevents a growing live database from changing the dataset halfway through one replay run.

---

# 32. PERFORMANCE

Measure replay runtime.

Report:

```
windows replayed
snapshots processed
oracle rows processed
elapsed time
windows/sec
```

Do not optimize prematurely.

Correctness is more important.

But design should eventually support thousands of parameter combinations without rereading SQLite inefficiently for every combination.

Prefer loading the clean historical tape into memory once per replay run.

---

# 33. ARCHITECTURE

Keep replay logic separate from live execution.

Suggested structure only if consistent with existing project:

```
backend/research/replay/
    models.py
    loader.py
    quality.py
    market_state.py
    strategy.py
    engine.py
    parity.py

scripts/
    phase2b1_replay.py
```

Do not force this exact structure if the repository has a better established pattern.

The important requirement is isolation.

---

# 34. CLI

Provide a read-only CLI.

Example target:

```
.\.venv\Scripts\python.exe scripts\phase2b1_replay.py
```

Useful options may include:

```
--bot A
--bot B
--bot all

--cutoff-ms

--output-dir research_output
```

Do NOT add parameter-grid options yet.

---

# 35. TESTS

Add tests covering at minimum:

```
projected_final reconstruction

margin sign

entry-window gate

margin gate

price-cap gate

one-entry-per-window

position gate

profit 99 exit

Bot A adverse direction for UP

Bot A adverse direction for DOWN

cut timer start

cut timer reset

cut confirmation

Bot B no-cut behavior

settlement win

settlement loss

fees

position sizing

missing ask

one-sided book

known-bad window exclusion

no look-ahead

determinism
```

Also add at least one fixture based on an actual Phase 2A captured trade if practical.

---

# 36. DO NOT COPY OUTCOMES INTO DECISION STATE

Be extremely careful here.

Outcome may be used for:

```
settlement
post-run scoring
```

Outcome must NOT be loaded into the strategy decision object before settlement.

A convenient DataFrame containing outcome alongside snapshots is dangerous.

Prefer structurally separating:

```
market tape
```

from:

```
labels/outcomes
```

until settlement/scoring.

---

# 37. PARITY ACCEPTANCE CRITERIA

Do not hide behind one aggregate score.

Report actual metrics first.

For Phase 2B.1 verdict, use:

## PASS

Only if:

- no look-ahead tests pass;
- deterministic replay passes;
- projected-final reconstruction passes on clean universe;
- production entry behavior is reproduced with high agreement;
- no large unexplained extra-entry population exists;
- Bot A cut behavior is reproduced with high agreement;
- material P&L differences are explainable by historical sampling resolution.

## PASS WITH LIMITATIONS

If the engine is logically correct but unavoidable ~1 Hz quote/snapshot resolution causes limited timing/price differences.

## FAIL

If there are unexplained:

- wrong sides;
- many missing production entries;
- many extra entries;
- cut-state mismatches;
- outcome leakage;
- material P&L divergence;
- non-determinism.

Do NOT lower acceptance criteria just to allow Phase 2B.2.

---

# 38. IMPORTANT MISMATCH ANALYSIS

For every mismatch classify root cause.

Use categories such as:

```
SNAPSHOT_RESOLUTION
QUOTE_RESOLUTION
ORACLE_RESOLUTION
EXECUTION_LOOP_TIMING
BALANCE_STATE
POSITION_STATE
ONE_ENTRY_STATE
CUT_TIMER_STATE
SETTLEMENT_STATE
MISSING_DATA
KNOWN_BAD_WINDOW
REPLAY_LOGIC_BUG
UNKNOWN
```

Report counts.

Any:

```
REPLAY_LOGIC_BUG
UNKNOWN
```

must be investigated before Phase 2B.2.

---

# 39. OUTPUT FILES

Create:

```
research_output/phase2b1_window_quality.csv
research_output/phase2b1_trade_parity.csv
research_output/phase2b1_entry_parity.csv
research_output/phase2b1_mismatches.csv
```

If useful also create:

```
research_output/phase2b1_replay_trades.csv
```

Do not include future labels in files intended to represent live decision state.

---

# 40. REPORT

Create:

```
docs/PHASE_2B1_OFFLINE_REPLAY_PARITY.md
```

Required sections:

## A. Executive Summary

## B. Replay Architecture

## C. Data Cutoff

## D. Clean Replay Universe

## E. Excluded Windows

## F. Data Quality Mask

## G. Time / Event Model

## H. No-Look-Ahead Design

## I. Production Strategy Reconstruction

## J. Bot A Replay Settings

## K. Bot B Replay Settings

## L. Projected-Final Parity

## M. Entry Parity — Bot A

## N. Entry Parity — Bot B

## O. Exit Parity — Bot A

## P. Exit Parity — Bot B

## Q. Bot A Cut Parity

## R. Settlement Parity

## S. P&L Parity

## T. False Positive / Extra Entry Analysis

## U. Mismatch Root Causes

## V. Determinism

## W. Replay Performance

## X. Tests

## Y. Known Limitations

## Z. Phase 2B.1 Verdict

---

# 41. FINAL SUMMARY TABLE

Include:


| Metric                    | Bot A | Bot B |
| ------------------------- | ----- | ----- |
| clean windows             |       |       |
| production entry windows  |       |       |
| replay entry windows      |       |       |
| matched entries           |       |       |
| missed production entries |       |       |
| extra replay entries      |       |       |
| wrong-side entries        |       |       |
| entry agreement           |       |       |
| median entry timing delta |       |       |
| same exit reason          |       |       |
| P&L production            |       |       |
| P&L replay                |       |       |
| P&L difference            |       |       |


Bot A additionally:

```
production cuts
replay cuts
matched cuts
missed cuts
extra cuts
```

---

# 42. FINAL VERDICT

Finish with exactly one:

```
PHASE 2B.1 PARITY: PASS
```

or:

```
PHASE 2B.1 PARITY: PASS WITH LIMITATIONS
```

or:

```
PHASE 2B.1 PARITY: FAIL
```

Then answer:

```
Replay engine deterministic: YES/NO
No-look-ahead verified: YES/NO
Projected-final parity on clean data: PASS/FAIL

Bot A entry parity acceptable: YES/NO
Bot B entry parity acceptable: YES/NO
Bot A cut parity acceptable: YES/NO

Unexplained replay logic mismatches: X
Unknown mismatches: X

Safe to start Phase 2B.2 parameter research: YES/NO

Live collector should remain running: YES
Live Bot A/B settings changed: NO
```

If Phase 2B.2 is NOT safe:

state exactly what must be fixed first.

Do NOT implement the fix automatically if it would alter production trading behavior.

---

# 43. WHAT TO RETURN TO ME

Return:

1. exact replay cutoff timestamp;
2. clean window count;
3. excluded window count + reasons;
4. UP/DOWN balance of clean universe;
5. eligible production trades A/B;
6. replay trades A/B;
7. matched/missed/extra/wrong-side entries;
8. entry timing deltas;
9. exit parity;
10. Bot A cut parity;
11. production vs replay P&L;
12. projected-final parity;
13. no-look-ahead result;
14. determinism result;
15. mismatch root-cause counts;
16. test results;
17. Phase 2B.1 verdict;
18. whether Phase 2B.2 is safe;
19. paths to:

```
docs/PHASE_2B1_OFFLINE_REPLAY_PARITY.md

research_output/phase2b1_window_quality.csv
research_output/phase2b1_trade_parity.csv
research_output/phase2b1_entry_parity.csv
research_output/phase2b1_mismatches.csv
```

STOP after Phase 2B.1.

DO NOT run any parameter grid.  
DO NOT recommend new Bot A/B settings.  
DO NOT start Phase 2B.2.