"""Stop paper Bot B after failed TWAP smoke. No secrets printed."""
import os
import httpx
from backend.auth import _load_dotenv

_load_dotenv()
base = "http://127.0.0.1:9000"
with httpx.Client(base_url=base, timeout=15.0) as c:
    r = c.post("/api/login", json={"password": os.environ.get("SITE_PASSWORD", "")})
    print("login", r.status_code)
    s = c.post("/api/bots/b_384a1650/stop")
    print("stop", s.status_code, s.json().get("status"), s.json().get("id"))
