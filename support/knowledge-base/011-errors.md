# 011 Errors

`Trading restricted in your region` — Polymarket geoblock; restart does not fix.

`FOK orders are fully filled or killed` — whole lot not available at best ask; no retry POST.

`CLOB min is 1 sh` — skip, not sent.

`copy_trader not supported on real yet` — live copy disabled.

`REDEEM withheld: Position ID must use a supported protocol v2 module` — token used as V2 position id. Current code skips: `REDEEM skipped: token is not a v2 position id`.

`get_order` `UnexpectedResponseError` / sell withheld / still unproven — parse/unproven fill; wait for reconcile.

`unauthorized` — login cookie missing.

PolyBolt auth missing — no key.

Port 10048 — duplicate bind.

`/health` persistent false on Windows — Railway volume check, not SQLite off.
