# 006 Operation flow

Start → load `.env` → FastAPI → Hub pipelines on demand → paper and real managers.

Every 250 ms: real bots reconcile `pending_op` if needed, settle if window rolled (no flatten POST), 99¢ lock sell, then strategy. FOK POST persisted before send. Fills via get_order + trades, not cash inference. No order retry.

Redeem ~20 s, batch 5, by `condition_id`. Feeds reconnect with backoff 0.5–5 s.

No Telegram. No STOP file. Stop = UI STOP or process exit.
