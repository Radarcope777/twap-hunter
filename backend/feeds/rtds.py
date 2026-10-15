"""PolyBolt reference prices: Chainlink spot + official 60s TWAP.

Replaces the public RTDS price topics. The activity feed stays on the old RTDS URL.

Wire (Live Data Channel):
- wss://ws-live-v2.polymarket.com/ws
- {"op":"auth","auth":{"apiKey","secret","passphrase"}} before price channels
- price.crypto and price.crypto.twap with window_seconds 60
- symbol is btcusd, not btc/usd
- the server sends WebSocket pings; there is no text PING
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import AsyncIterator

import websockets

from backend.config import CONFIG
from backend.feeds.binance import backoff_delay_ms
from backend.feeds.clock import OffsetTracker

logger = logging.getLogger(__name__)

STALL_TIMEOUT_S = 30.0
CHANNEL_SPOT = "price.crypto"
CHANNEL_TWAP = "price.crypto.twap"


def polybolt_symbol(rtds_symbol: str) -> str:
    """btc/usd -> btcusd."""
    return rtds_symbol.replace("/", "").strip().lower()


def subscribe_frame(symbol: str) -> str:
    sym = polybolt_symbol(symbol)
    return json.dumps({
        "op": "subscribe",
        "subscriptions": [
            {"channel": CHANNEL_SPOT, "filter": {"symbol": sym}},
            {
                "channel": CHANNEL_TWAP,
                "filter": {"symbol": sym, "window_seconds": 60},
            },
        ],
    })


def _point(entry: dict) -> tuple[float, float] | None:
    try:
        ts_ms = float(entry["timestamp"])
        raw = entry.get("full_accuracy_value")
        if raw is None or raw == "":
            raw = entry.get("value")
        value = float(raw)
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return ts_ms, value


def parse_frame(raw) -> tuple[str, str, list[tuple[float, float]]] | None:
    """-> ("chainlink"|"twap60"|"backfill", symbol, [(ts_ms, value), ...]).

    Spot snapshots are backfill so the pipeline can seed the rolling series.
    TWAP snapshots stay twap60 and are not treated as Chainlink samples.
    """
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        if not text:
            return None
        try:
            msg = json.loads(text)
        except ValueError:
            return None
        if not isinstance(msg, dict) or msg.get("op"):
            return None
        channel = msg.get("channel")
        payload = msg.get("payload")
        if not isinstance(payload, dict):
            return None
        symbol = payload.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            return None
        data = payload.get("data")
        if channel == CHANNEL_SPOT:
            if isinstance(data, list):
                points = [p for p in (_point(e) for e in data if isinstance(e, dict)) if p]
                return ("backfill", symbol, points) if points else None
            point = _point(payload)
            return ("chainlink", symbol, [point]) if point else None
        if channel == CHANNEL_TWAP:
            if isinstance(data, list):
                points = [p for p in (_point(e) for e in data if isinstance(e, dict)) if p]
                return ("twap60", symbol, points) if points else None
            point = _point(payload)
            return ("twap60", symbol, [point]) if point else None
        return None
    except Exception:
        return None


def _control_error(raw) -> str | None:
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        msg = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    if not isinstance(msg, dict) or msg.get("op") != "error":
        return None
    return str(msg.get("code") or "error")


class RTDSFeed:
    """Yield ("chainlink"|"twap60"|"backfill", receive_ms, points) forever."""

    def __init__(self, symbol: str) -> None:
        self._symbol = polybolt_symbol(symbol)
        self.connected = False
        self._offset = OffsetTracker()

    @property
    def clock_offset_ms(self) -> float | None:
        return self._offset.value

    async def _authenticate(self, ws) -> None:
        from backend.engine.clob_executor import polybolt_auth_payload, polybolt_auth_reset

        auth = polybolt_auth_payload()
        if auth is None:
            raise ConnectionError(
                "polybolt auth missing: set POLYMARKET_PRIVATE_KEY "
                "or save a Polymarket account (SDK derives CLOB L2 creds)"
            )
        await ws.send(json.dumps({"op": "auth", "auth": auth}))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            if not isinstance(raw, str) or not raw.strip():
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            op = msg.get("op")
            if op == "authed":
                return
            if op == "error":
                polybolt_auth_reset()
                raise ConnectionError(f"polybolt auth rejected: {msg.get('code') or 'error'}")
        raise ConnectionError("polybolt auth timed out")

    async def stream(self) -> AsyncIterator[tuple[str, float, list[tuple[float, float]]]]:
        attempt = 0
        while True:
            try:
                async with websockets.connect(
                    CONFIG.polybolt_ws_url,
                    ping_interval=None,
                    open_timeout=15,
                    close_timeout=5,
                ) as ws:
                    await self._authenticate(ws)
                    await ws.send(subscribe_frame(self._symbol))
                    parsed_any = False
                    last_data = time.monotonic()
                    while True:
                        if time.monotonic() - last_data > STALL_TIMEOUT_S:
                            raise ConnectionError("polybolt silent stall (no updates)")
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        err = _control_error(raw)
                        if err:
                            raise ConnectionError(f"polybolt {err}")
                        receive_ms = time.time() * 1000.0
                        parsed = parse_frame(raw)
                        if parsed is None:
                            continue
                        last_data = time.monotonic()
                        if not parsed_any:
                            parsed_any = True
                            self.connected = True
                            if attempt:
                                logger.info("polybolt %s reconnected", self._symbol)
                            attempt = 0
                        kind, symbol, points = parsed
                        if symbol.strip().lower() != self._symbol:
                            continue
                        if points:
                            self._offset.add(receive_ms, points[-1][0])
                        if kind == "twap60" and len(points) > 1:
                            for point in points:
                                yield kind, receive_ms, [point]
                            continue
                        yield kind, receive_ms, points
                raise ConnectionError("server closed the connection")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                attempt += 1
                delay = backoff_delay_ms(attempt)
                if attempt == 1 or attempt % 5 == 0:
                    logger.warning(
                        "polybolt %s feed lost (%s: %s); retry in %d ms",
                        self._symbol, type(exc).__name__, exc, delay,
                    )
                await asyncio.sleep(delay / 1000.0)
