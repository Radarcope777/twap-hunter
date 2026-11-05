"""Probe public Data API v2 envelope. No keys."""
import json
import httpx

url = "https://data-api.polymarket.com/v2/value"
params = {"user": "0x0000000000000000000000000000000000000001"}
r = httpx.get(url, params=params, timeout=20.0)
print("status", r.status_code)
text = r.text[:400]
print("body_prefix", text.replace("\n", " "))
try:
    body = r.json()
except Exception:
    body = None
if isinstance(body, dict):
    print("keys", sorted(body.keys()))
    data = body.get("data")
    print("data_type", type(data).__name__)
