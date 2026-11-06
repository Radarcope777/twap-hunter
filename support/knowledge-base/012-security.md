# 012 Security

Never collect private keys in support tickets. Keys live encrypted in SQLite; Fernet file `data/.encryption_key` is required to decrypt.

Empty `SITE_PASSWORD` disables auth. Cookie httponly, 30 days; Secure on Railway or `FORCE_SECURE_COOKIES=1`.

Do not ship `.env` or `data/` to customers. API `public_view` omits keys.

No CLOB POST retry. Redeem logs strip secret-like exception text.

This app is not a geoblock bypass.
