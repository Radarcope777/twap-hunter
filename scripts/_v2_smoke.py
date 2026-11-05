"""Paper smoke: login, start Bot B, read WS snapshot. Never prints secrets."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import websockets

from backend.auth import _load_dotenv

_load_dotenv()
BASE = os.environ.get("TWAP_SMOKE_BASE", "http://127.0.0.1:9000")
WS = BASE.replace("http://", "ws://").replace("https://", "wss://") + "/ws?asset=BTC"
PASSWORD = os.environ.get("SITE_PASSWORD", "")
BOT_ID = "b_384a1650"


def _pick_snap(msg: dict) -> dict | None:
    if not isinstance(msg, dict):
        return None
    if msg.get("type") in ("init", "batch") and isinstance(msg.get("snapshot"), dict):
        return msg["snapshot"]
    return None


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=20.0, follow_redirects=True) as client:
        health = (await client.get("/health")).json()
        print("health", json.dumps(health, sort_keys=True))
        login = await client.post("/api/login", json={"password": PASSWORD})
        print("login_status", login.status_code)
        if login.status_code != 200:
            print("login_failed")
            return 1
        cookie = login.cookies.get("twap_session")
        reals = (await client.get("/api/real/bots")).json()
        real_bots = reals.get("bots") or []
        running_real = [b.get("id") for b in real_bots if b.get("status") == "running"]
        print("real_bots", len(real_bots), "running", running_real)
        if running_real:
            print("REFUSING to continue: live bots would trade")
            return 2
        started = await client.post(f"/api/bots/{BOT_ID}/start")
        print("start_status", started.status_code)
        bot = started.json()
        print(
            "paper",
            bot.get("id"),
            bot.get("strategy"),
            bot.get("status"),
            "live",
            bot.get("params") is not None,
        )

    headers = {"Cookie": f"twap_session={cookie}"} if cookie else {}
    last = None
    n_batch = 0
    async with websockets.connect(WS, additional_headers=headers, open_timeout=20) as ws:
        deadline = asyncio.get_event_loop().time() + 25
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            snap = _pick_snap(msg)
            if snap is None:
                continue
            last = snap
            n_batch += 1
            prices = snap.get("prices") or {}
            health_f = snap.get("health") or {}
            win = snap.get("window") or {}
            print(
                "snap",
                "twap", prices.get("twap"),
                "chainlink", prices.get("chainlink"),
                "rtds", health_f.get("rtds"),
                "binance", health_f.get("binance"),
                "clob", health_f.get("clob"),
                "ptb_source", win.get("ptb_source"),
                "ptb", win.get("ptb"),
            )
            if prices.get("twap") is not None and health_f.get("rtds") == "live":
                break

    print("batches", n_batch)
    if last is None:
        print("NO_SNAPSHOT")
        return 3
    prices = last.get("prices") or {}
    health_f = last.get("health") or {}
    ok = prices.get("twap") is not None and health_f.get("rtds") in ("live", "stale")
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
