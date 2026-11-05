"""Read-only preflight: bot statuses, creds presence, disk. No secrets printed."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from backend.storage import storage
from backend.auth import _load_dotenv

root = Path(__file__).resolve().parent.parent
_load_dotenv()

print("disk_free_mb", round(shutil.disk_usage(str(root)).free / 1e6, 1))
print("site_password_set", bool(os.environ.get("SITE_PASSWORD", "").strip()))
print("poly_api_key_set", bool(os.environ.get("POLYMARKET_API_KEY", "").strip()))
print("poly_api_secret_set", bool(os.environ.get("POLYMARKET_API_SECRET", "").strip()))
print("poly_passphrase_set", bool(os.environ.get("POLYMARKET_PASSPHRASE", "").strip()))
enc = root / "data" / ".encryption_key"
print("encryption_key_present", enc.is_file())

print("--- paper ---")
for b in storage.load_bots().values():
    print(
        b.get("id"),
        b.get("name"),
        b.get("asset"),
        b.get("strategy"),
        b.get("status"),
        "pos", bool(b.get("position")),
    )

print("--- real ---")
reals = storage.load_real_bots()
if not reals:
    print("(none)")
for b in reals.values():
    print(
        b.get("id"),
        b.get("name"),
        b.get("asset"),
        b.get("strategy"),
        b.get("status"),
        "account", bool(b.get("account_id")),
        "pos", bool(b.get("position")),
    )

print("--- accounts ---")
n = 0
n_key = 0
for a in storage.load_accounts().values():
    n += 1
    if a.get("encrypted_key"):
        n_key += 1
print("accounts", n, "with_encrypted_key", n_key)
