# PHASE 2A — CLARIFICATION / AUTHORIZATION

Proceed with Phase 2A.

Your interpretation of the conflict is correct. Apply the following clarifications.

## 1. CODE CHANGES ARE AUTHORIZED

You ARE authorized to modify the project in order to implement Phase 2A research instrumentation.

You may:

- create new research tables;
- add schema initialization/migrations following existing project conventions;
- create research/instrumentation modules;
- modify `BotManager`, `Pipeline`, storage, or related code where necessary to OBSERVE and persist existing runtime state;
- add read-only research API endpoints if useful;
- add tests;
- create `PHASE_2A_RESEARCH_INSTRUMENTATION.md`.

The prohibition is specifically against changing TRADING BEHAVIOR.

You must NOT change:

- BUY eligibility logic;
- side selection;
- projected_final calculation;
- projected_margin calculation;
- PTB calculation/selection;
- SELL behavior;
- profit99 behavior;
- cut logic;
- cut timing;
- settlement behavior;
- position sizing;
- fee calculations;
- existing bot parameters.

Instrumentation must observe the existing decision path, not alter it.

The intended invariant is:

```
same runtime inputs
+
instrumentation disabled

must produce the same trading decisions as

same runtime inputs
+
instrumentation enabled
```

Research persistence failure must not alter a trading decision.

---

## 2. LIVE DATABASE/RUNTIME IS THE SOURCE OF TRUTH FOR CURRENT SETTINGS

The Phase 1 report and original Phase 2A prompt contain settings captured at earlier points in time.

They are historical observations, NOT configuration that should be restored.

For current instrumentation, use the bot's ACTUAL runtime/persisted settings.

You have now observed:

### Bot A — `b_0d55c464`

Current settings still match the previous audit.

Do not change them.

### Bot B — `b_384a1650`

The previous Phase 1 snapshot showed:

```
entry_window_s = 60
min_margin_bps = 2.5
max_entry_cents = 90
order_usd = 100
```

The CURRENT database now shows:

```
entry_window_s = 55
min_margin_bps = 2.5
max_entry_cents = 88
order_usd = 100
```

Treat:

```
55 / 2.5 / 88 / 100
```

as the current configuration IF the running API/runtime agrees with the database.

Do NOT change Bot B back to 60 / 90.

Before implementation, verify DB vs running runtime/API.

If they agree, continue.

If DB and runtime disagree, STOP and report the discrepancy before modifying anything.

---

## 3. THIS CONFIGURATION CHANGE IS ITSELF IMPORTANT RESEARCH EVIDENCE

Phase 1 already established that historical parameter changes were not versioned.

The change from:

```
60s / 2.5bps / 90¢
```

to:

```
55s / 2.5bps / 88¢
```

demonstrates exactly why Phase 2A needs settings versioning.

From Phase 2A onward, every research decision must be attributable to the exact settings active at that moment.

Do NOT assume settings remain constant throughout a research period.

Persist either:

```
settings_version_id
```

with immutable version records,

or another architecture that guarantees the exact parameter snapshot can be reconstructed for every decision.

---

## 4. DO NOT USE THE PHASE 2A PROMPT VALUES AS CONFIGURATION

Any parameter values written in the original Phase 2A specification are CONTEXT ONLY.

They must never cause code to overwrite current bot settings.

The instrumentation must observe:

```
bot.params
```

or the equivalent actual runtime configuration.

This applies to:

```
entry_window_s
min_margin_bps
max_entry_cents
cut_margin_bps
cut_confirm_s
order_usd
```

and any other strategy parameter.

---

## 5. IMPORTANT SETTINGS-VERSION BOOTSTRAP

When instrumentation starts, create/record an initial settings snapshot for each target bot based on its ACTUAL current runtime configuration.

For example:

```
timestamp
bot_id
strategy
full_params_json
```

Then record a new immutable settings version whenever relevant parameters change.

Do not infer historical settings versions before instrumentation existed.

The Phase 1 audit can document old known snapshots, but those must NOT be inserted into the research database as if their exact validity intervals were known.

---

## 6. TARGET BOTS REMAIN

Primary research targets remain:

```
b_0d55c464
b_384a1650
```

However, design the instrumentation generically enough that research collection is not unnecessarily hard-coded to these literal IDs if the existing architecture supports strategy/asset-based collection cleanly.

Do NOT accidentally collect XRP or unrelated strategies unless explicitly intended and documented.

BTC TWAP research is the current scope.

---

## 7. NON-REGRESSION IS THE MOST IMPORTANT ACCEPTANCE TEST

Before and after instrumentation, verify that the relevant strategy functions produce identical decisions for identical synthetic state.

Specifically:

```
twap_lockin
twap_lockin_cut
```

Instrumentation may observe:

```
ENTRY WOULD PASS
ENTRY WOULD FAIL
CUT TIMER STARTED
CUT CONDITION TRUE
SELL ATTEMPTED
```

but it must not create an independent second implementation of trading rules that can influence the real strategy.

Where possible, instrument the existing decision path rather than rewriting the strategy into a new research evaluator.

---

## 8. DO NOT "FIX" OBSERVED HIGH-RISK BEHAVIOR

Still do NOT fix:

- stale oracle acceptance;
- stale quote acceptance;
- PTB carry behavior;
- quote/market identity behavior;
- paper bid-depth behavior;
- cut retry behavior;
- settlement reconciliation behavior;
- one-entry restart behavior;
- atomic persistence;
- resolution parser behavior.

Record enough information to study them.

For example:

```
oracle_age_ms = 8200
```

may later be classified as stale by research analysis.

But Phase 2A must NOT suddenly make:

```
oracle_age_ms > X
```

an entry rejection rule.

---

## 9. PROCEED

You no longer need to stop because Phase 2A requires source-code changes.

Proceed with:

1. verify current DB/runtime Bot A and Bot B settings;
2. inspect architecture;
3. write implementation plan;
4. implement instrumentation;
5. create research schema;
6. add settings versioning;
7. add market/oracle snapshots;
8. add bot decision observations;
9. add execution/cut/settlement events;
10. add failure isolation;
11. add tests;
12. verify non-regression;
13. create `PHASE_2A_RESEARCH_INSTRUMENTATION.md`.

Do NOT change bot parameters.

Do NOT reset trade history.

Do NOT reset balances.

Do NOT restart running bots automatically.

If activation requires restart, finish implementation and explicitly report:

```
RESTART REQUIRED
```

with the reason and safe procedure.

At completion, report the ACTUAL settings observed at implementation time, not the stale values from the original Phase 2A prompt.

Then STOP before Phase 2B.