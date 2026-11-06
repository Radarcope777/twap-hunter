# Security

## Secrets this app stores

| Item | Location | Exposed to UI/API? |
| --- | --- | --- |
| Site password | `.env` `SITE_PASSWORD` | No (login only) |
| Session HMAC | `SESSION_SECRET` or site password | Cookie is a digest, httponly |
| Wallet private key | Fernet `encrypted_key` in SQLite | Never in `public_view` |
| Fernet key | `data/.encryption_key` | No |
| Builder key/secret/passphrase | `encrypted_builder` or env | Not in public_view; logs strip secret-like exception text in `_safe_redeem_reason` |
| CLOB L2 apiKey | Derived in memory | Not written to `.env` by the app |

`.gitignore` includes `.venv/`, `data/`, `.env`. `.dockerignore` excludes `.env` and `data/`.

## Delivery / sale

Before sending a copy to a buyer: delete `.env`, the entire `data/` directory (DB + wal + encryption key), and do not zip `.venv`. A leftover `.encryption_key` plus DB is a full wallet compromise.

## Auth

- If `SITE_PASSWORD` is empty, **all routes are open** (local-dev convenience).
- Public without cookie: `/login`, `/api/login`, `/health`, `/style.css`.
- `/api/*` and `/ws` return 401 without a valid cookie when auth is on.
- Cookie `Secure` on Railway or `FORCE_SECURE_COOKIES=1`.

## Trading safety

- No CLOB POST retry (orders are not idempotent).
- Unread balance is not treated as zero.
- Redeem does not invent fills from cash.
- Do not log private keys. `_safe_redeem_reason` returns only the exception type if the message looks like it contains secrets.

## Network

Binding `0.0.0.0` exposes the dashboard on all interfaces. Use `127.0.0.1` plus a tunnel if the host is not trusted.

## Geoblock and proxies

The application has no proxy configuration. Do not treat this repo as a geoblock bypass.

## Updates

Do not commit buyer secrets. Rotate Polymarket keys if a database was copied accidentally.
