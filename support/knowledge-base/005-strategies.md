# 005 Strategies

Code ids in `backend/engine/bots.py` `_EVALUATORS`: `twap_lockin`, `twap_lockin_cut`, `winning_hold`, `cross_line`, `next_round_scalp`, `copy_trader`, `dip_harvester`, `pressure_scalp`, `momentum_rider`, `cross_hunter`.

Shared: ~250 ms loop, taker fee 7% × shares × min(p,1-p), skip first 2s/last 5s, spread>20¢, min 1 share, sell at 99¢ bid (`LOCK_PROFIT_CENTS`). Live uses the same evaluators plus FOK and settlement/redeem.

`copy_trader` live is disabled. `pressure_scalp` description in code notes 0W/11L live testing; still selectable.

Dip Harvester buys both UP and DOWN on dips; it is not a separate hedge/arbitrage module.

Do not rename these as market making or guaranteed lock-in beyond what the evaluator does.
