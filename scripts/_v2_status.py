"""Status: accounts, real bots, BTC TWAP. No secrets, does not start live."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import websockets

from backend.auth import _load_dotenv

_load_dotenv()
BASE = "http://127.0.0.1:9000"
PASSWORD = os.environ.get("SITE_PASSWORD", "")


def _mask_addr(addr: str) -> str:
    a = (addr or "").strip()
    if len(a) < 12:
        return "(none)"
    return f"{a[:6]}…{a[-4:]}"


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
        login = await client.post("/api/login", json={"password": PASSWORD})
        print("login", login.status_code)
        cookie = login.cookies.get("twap_session")
        accs = (await client.get("/api/accounts")).json().get("accounts") or []
        print("accounts", len(accs))
        for a in accs:
            print(
                "account",
                a.get("id"),
                a.get("label"),
                "addr", _mask_addr(a.get("address") or ""),
                "cash", a.get("cash_balance"),
                "clob_err", bool(a.get("clob_error") or a.get("fetch_error")),
            )
        reals = (await client.get("/api/real/bots")).json().get("bots") or []
        print("real_bots", len(reals))
        running = []
        for b in reals:
            st = b.get("status")
            print(
                "real",
                b.get("id"),
                b.get("name"),
                b.get("strategy"),
                b.get("asset"),
                st,
                "account", b.get("account_id"),
            )
            if st == "running":
                running.append(b.get("id"))
        print("live_running", running)

    headers = {"Cookie": f"twap_session={cookie}"} if cookie else {}
    last = None
    async with websockets.connect(
        BASE.replace("http://", "ws://") + "/ws?asset=BTC",
        additional_headers=headers,
        open_timeout=20,
        max_size=16_000_000,
    ) as ws:
        deadline = asyncio.get_event_loop().time() + 20
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            snap = msg.get("snapshot") if isinstance(msg, dict) else None
            if not isinstance(snap, dict):
                continue
            last = snap
            prices = snap.get("prices") or {}
            health = snap.get("health") or {}
            win = snap.get("window") or {}
            print(
                "snap twap", prices.get("twap"),
                "chainlink", prices.get("chainlink"),
                "rtds", health.get("rtds"),
                "binance", health.get("binance"),
                "clob", health.get("clob"),
                "ptb_source", win.get("ptb_source"),
            )
            if prices.get("twap") is not None and health.get("rtds") == "live":
                break
    if last is None:
        print("RESULT FAIL no snapshot")
        return 1
    prices = last.get("prices") or {}
    ok = prices.get("twap") is not None
    print("RESULT", "TWAP_OK" if ok else "TWAP_FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
