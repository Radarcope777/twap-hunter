# Support / RAG

Use this pack as the only product truth for customer support.

## Sources (priority)

1. `support/knowledge-base/*.md` — short articles, one topic each  
2. `support/knowledge-base.jsonl` — same facts as retrieval chunks  
3. `support/knowledge-base-index.md` — id → file map  
4. `doc/*.md` — long-form operator manuals  
5. Repository source if docs and code disagree **code wins**

Do not use chat history, marketing copy, or guessed Telegram/risk-limit features.

## Grounding rules for the support model

- If a feature is not in the knowledge base, say **not implemented** or **not verified**.
- Never invent env vars, strategy names, or commands.
- Never request or echo private keys, builder secrets, or `SITE_PASSWORD`.
- Never promise returns.
- Geoblock: explain it is Polymarket’s region check. Do not instruct users to shop proxy countries.
- Copy Trader live: quote `copy_trader not supported on real yet`.
- FOK: whole lot at one price or kill; no retry POST.
- Open PnL on real cards: shared wallet, not per-bot.

## Suggested retrieval queries

See headings in `support/knowledge-base-index.md`. Match error strings exactly when possible (FOK message, geoblock URL, redeem module error).
