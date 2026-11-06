# Live trading

Real bots: `/real`, `RealBotManager` (`backend/engine/real_bots.py`). Orders: `place_market_order` FOK via `polymarket-client` ≥ 0.13 `SecureClient`.

## Preconditions

1. Process running; dashboard login if `SITE_PASSWORD` is set.
2. Polymarket account with USDC/pUSD, trading allowed for the host IP (geoblock is enforced by Polymarket, not this app).
3. Add Account: label, address, **private key**. Key encrypted at rest. Proxy/funder may differ from the signer EOA (warning stored as `address_mismatch`).
4. Paper bot with the intended strategy/params (real card cannot edit entry_window / min_margin / max_entry).
5. Import: `POST /api/real/bots/import` `{paper_bot_id, account_id}`.
6. Set LOT. Press **Start** (explicit). Stopped real bots do not order and do not redeem.

Copy Trader: do not expect live copies. The loop sets `copy_trader not supported on real yet`.

## Order behavior

- BUY: FOK, `amount=order_usd`, `max_price=best ask`. Whole dollar must be available at that **single** price or the order is killed.
- SELL: FOK, `shares` ≈ 99.9% of local position, `min_price=bid`.
- No FAK setting. Raising FAK alone would still not walk 40¢ then 48¢ while `max_price` is the best ask.
- Persist-before-send: `pending_op` states intent → posted → uncertain/rejected.
- Fill proof: order id + trades. Cash movement is **not** used as fill proof.
- `get_order` parse failures (`UnexpectedResponseError`) withhold a second sell until a later read confirms.

## Extra live filters vs paper

- Last 5 s / first 2 s / 20¢ spread / min 1 share (same idea as paper).
- Unread share or cash balance: withhold, do not assume zero.
- Protocol version from the Gamma market (`v1`/`v2`); unknown version withholds.

## Settlement and redeem

After the 5-minute window rolls, live bots **do not** flatten-sell leftovers. They wait for official or local outcome, book SETTLE, then redeem.

Redeem:

- Needs `condition_id` (kept across reconcile after the condition-id fix).
- Deposit/proxy wallets need a Builder or Relayer API key; the bot can create and store one.
- EOA can broadcast without that key.
- Success log example: `REDEEM UP 1.4925 sh tx 0x…`
- `Position has no balance to redeem` is treated as already done.
- Invalid token-as-position-id is skipped, not retried every minute.

Cash appears after the next CLOB collateral sync, not inside the redeem return value.

## Open PnL on the card

Shared wallet mark for that asset from Data API. Not per-strategy PnL. Compare bots using each bot’s `trades[].pnl` / stats.

## Geoblock

CLOB may return: `Trading restricted in your region, please refer to available regions`. Restarting GOODFALLEN does not change this. This project does not implement proxies or region shopping.

## Health

`GET /health` — `{ok, auth, ready, bots, storage}`. On Windows, `storage.persistent: false` is the Railway volume heuristic, not “SQLite off”.
