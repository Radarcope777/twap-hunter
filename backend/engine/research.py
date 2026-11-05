"""Observational research recorder for BTC TWAP paper bots.

Nothing in this module is consulted by entry, exit, cut, or settlement.
Persistence failures are logged and swallowed. Queues are bounded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)

RESEARCH_STRATEGIES = frozenset({"twap_lockin", "twap_lockin_cut"})
RESEARCH_ASSET = "BTC"
SNAPSHOT_INTERVAL_MS = 1000.0
DECISION_INTERVAL_MS = 1000.0
QUEUE_MAX = 8_000
FAIL_LOG_INTERVAL_S = 30.0

_TABLES = {
    "snap": "research_market_snapshots",
    "oracle": "research_oracle_samples",
    "decision": "research_bot_decisions",
    "event": "research_execution_events",
}

_COLUMNS = {
    "snap": [
        "t_ms", "asset", "window_id", "slug", "condition_id", "up_token", "down_token",
        "window_start_ms", "window_end_ms", "remaining_ms",
        "ptb", "ptb_source", "ptb_source_ts_ms", "ptb_age_ms",
        "chainlink", "chainlink_source_ts_ms", "chainlink_age_ms",
        "twap30", "twap60", "twap60_source_ts_ms", "twap60_age_ms",
        "rolling_twap", "lookback_s", "projected_final", "projected_margin_bps",
        "up_bid", "up_ask", "up_bid_size", "up_ask_size",
        "down_bid", "down_ask", "down_bid_size", "down_ask_size",
        "quote_ts_ms", "quote_age_ms",
    ],
    "oracle": ["asset", "source", "source_ts_ms", "receive_ts_ms", "price"],
    "decision": [
        "t_ms", "bot_id", "strategy", "asset", "window_id", "settings_version_id",
        "position_state", "position_side", "position_entry_price", "position_shares",
        "remaining_ms", "projected_margin_bps", "candidate_side",
        "entry_window_pass", "margin_pass", "quote_pass", "max_price_pass",
        "spread_pass", "balance_pass", "one_entry_pass",
        "entry_allowed", "entry_rejection_reason",
        "cut_margin_bps", "cut_confirm_s", "adverse", "adverse_since_ms",
        "adverse_duration_ms", "cut_confirmed", "profit99_condition", "warnings_json",
    ],
    "event": [
        "t_ms", "bot_id", "strategy", "asset", "window_id", "event_type",
        "side", "bid", "bid_size", "ask", "ask_size", "shares",
        "result", "failure_reason", "details_json",
    ],
}


def is_research_bot(bot: dict | None) -> bool:
    if not bot:
        return False
    return (
        bot.get("asset") == RESEARCH_ASSET
        and bot.get("strategy") in RESEARCH_STRATEGIES
    )


def projected_margin_bps(pipe) -> float | None:
    """Same predicate as bots._projected_margin_bps. Read-only copy for logs."""
    ptb = pipe.ptb
    if ptb is None or ptb <= 0 or pipe.ptb_source == "exchange_approx":
        return None
    projected = pipe.rolling.value_at(pipe.window_end_ms)
    if projected is None:
        return None
    return (projected - ptb) / ptb * 10_000.0


def _params_hash(params: dict) -> str:
    blob = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _bit(ok: bool) -> int:
    return 1 if ok else 0


class ResearchRecorder:
    def __init__(self) -> None:
        self._queues: dict[str, deque] = {k: deque(maxlen=QUEUE_MAX) for k in _COLUMNS}
        self._oracle_seen: set[tuple] = set()
        self._last_snap_ms = 0.0
        self._last_decision_ms: dict[str, float] = {}
        self._settings_cache: dict[str, tuple[str, int]] = {}
        self._dropped = 0
        self._fail_count = 0
        self._last_fail_log = 0.0
        self.force_fail = False  # tests only

    def note_failure(self) -> None:
        self._fail_count += 1
        now = time.monotonic()
        if now - self._last_fail_log >= FAIL_LOG_INTERVAL_S:
            self._last_fail_log = now
            logger.warning(
                "research persistence errors: %d (trading continues)",
                self._fail_count,
            )

    def _enqueue(self, kind: str, row: tuple) -> None:
        if self.force_fail:
            raise RuntimeError("research forced failure")
        q = self._queues[kind]
        if len(q) >= QUEUE_MAX:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 1000 == 0:
                logger.warning("research queue full (%s), dropped=%d", kind, self._dropped)
            return
        q.append(row)

    def oracle(self, asset: str, source: str, source_ts_ms: float,
               receive_ts_ms: float | None, price: float) -> None:
        if asset != RESEARCH_ASSET:
            return
        key = (asset, source, round(float(source_ts_ms), 3))
        if key in self._oracle_seen:
            return
        if len(self._oracle_seen) > 20_000:
            self._oracle_seen.clear()
        self._oracle_seen.add(key)
        self._enqueue("oracle", (asset, source, float(source_ts_ms), receive_ts_ms, float(price)))

    def event(self, bot: dict, event_type: str, *, side=None, bid=None, bid_size=None,
              ask=None, ask_size=None, shares=None, result=None, failure_reason=None,
              details: dict | None = None, window_id=None) -> None:
        if not is_research_bot(bot):
            return
        self._enqueue("event", (
            time.time() * 1000.0,
            bot.get("id"),
            bot.get("strategy"),
            bot.get("asset"),
            window_id,
            event_type,
            side,
            bid,
            bid_size,
            ask,
            ask_size,
            shares,
            result,
            failure_reason,
            json.dumps(details, separators=(",", ":")) if details else None,
        ))

    def tick(self, mgr) -> None:
        """1 Hz snapshots + decisions, then a batched flush. Never raises."""
        try:
            bots = [
                b for b in mgr.bots.values()
                if is_research_bot(b) and b.get("status") == "running"
            ]
            pipe = mgr._hub.pipelines.get(RESEARCH_ASSET) if bots else None
            now = pipe.poly_now_ms() if pipe is not None else 0.0
            if pipe is not None and now - self._last_snap_ms >= SNAPSHOT_INTERVAL_MS:
                self._last_snap_ms = now
                self._enqueue("snap", _snapshot_row(pipe, now))
            if pipe is not None:
                for bot in bots:
                    last = self._last_decision_ms.get(bot["id"], 0.0)
                    if now - last < DECISION_INTERVAL_MS:
                        continue
                    self._last_decision_ms[bot["id"]] = now
                    rt = mgr._rt.get(bot["id"]) or {}
                    self._enqueue("decision", self._decision_row(bot, rt, pipe, now))
        except Exception:
            self.note_failure()
        self.flush()

    def flush(self) -> None:
        try:
            if self.force_fail:
                raise RuntimeError("research forced failure")
            from backend.storage import storage
            for kind, cols in _COLUMNS.items():
                q = self._queues[kind]
                if not q:
                    continue
                batch = list(q)
                storage.research_insert_many(_TABLES[kind], cols, batch)
                for _ in range(len(batch)):
                    if q:
                        q.popleft()
        except Exception:
            self.note_failure()

    def settings_version_id(self, bot: dict) -> int | None:
        try:
            if self.force_fail:
                raise RuntimeError("research forced failure")
            from backend.storage import storage
            params = bot.get("params") or {}
            digest = _params_hash(params)
            cached = self._settings_cache.get(bot["id"])
            if cached is not None and cached[0] == digest:
                return cached[1]
            vid = storage.research_settings_version(
                bot["id"], bot.get("strategy") or "",
                json.dumps(params, sort_keys=True, default=str),
                digest, time.time() * 1000.0,
            )
            self._settings_cache[bot["id"]] = (digest, vid)
            return vid
        except Exception:
            self.note_failure()
            return None

    def _decision_row(self, bot: dict, rt: dict, pipe, now: float) -> tuple:
        p = bot.get("params") or {}
        pos = bot.get("position")
        remaining = pipe.window_end_ms - now
        margin = projected_margin_bps(pipe)
        warnings = []
        if pipe.ptb_source == "exchange_approx":
            warnings.append("PTB_EXCHANGE_APPROX")
        cl_ts = pipe.rolling.last_sample_ms
        if cl_ts is not None and now - cl_ts > 5000:
            warnings.append("ORACLE_AGE_GT_5S")
        q = pipe.last_quote or {}
        if q.get("t") is not None and now - q["t"] > 5000:
            warnings.append("QUOTE_AGE_GT_5S")

        entry_window_pass = 10_000 <= remaining <= float(p.get("entry_window_s", 0)) * 1000.0
        one_entry_pass = rt.get("entries", {}).get(pipe.window_id, 0) < 1
        if margin is None:
            margin_pass = False
            candidate = None
            if pipe.ptb is None:
                reject = "NO_PTB"
            elif pipe.ptb_source == "exchange_approx":
                reject = "PTB_EXCHANGE_APPROX"
            else:
                reject = "NO_PROJECTED_FINAL"
        else:
            margin_pass = abs(margin) >= float(p.get("min_margin_bps", 0))
            candidate = "UP" if margin > 0 else "DOWN"
            reject = None if margin_pass else "MARGIN_TOO_SMALL"

        ask = bid = ask_sz = bid_sz = None
        if candidate:
            ask = q.get(f"{candidate.lower()}_ask")
            bid = q.get(f"{candidate.lower()}_bid")
            ask_sz = q.get(f"{candidate.lower()}_ask_size")
            bid_sz = q.get(f"{candidate.lower()}_bid_size")
        quote_pass = ask is not None and bid is not None and ask > 0 and bid > 0
        max_price_pass = ask is not None and ask <= float(p.get("max_entry_cents", 0)) and ask < 100
        spread_pass = quote_pass and (ask - bid) <= 20
        order_usd = float(p.get("order_usd", 0))
        balance_pass = True
        if ask is not None and ask > 0:
            price = ask / 100.0
            shares = order_usd / price
            if ask_sz is not None:
                shares = min(shares, ask_sz)
            from backend.engine.bots import taker_fee, MIN_ORDER_SHARES
            cost = shares * price
            fee = taker_fee(shares, price)
            balance_pass = shares >= MIN_ORDER_SHARES and (cost + fee) <= float(bot.get("balance") or 0)
            if quote_pass and max_price_pass and spread_pass and not balance_pass:
                if shares < MIN_ORDER_SHARES:
                    reject = reject or "MIN_SHARES"
                else:
                    reject = reject or "INSUFFICIENT_BALANCE"
        if candidate and ask is None:
            reject = reject or "NO_ASK"
        elif candidate and (bid is None or bid <= 0):
            reject = reject or "NO_BID"
        elif candidate and quote_pass and not spread_pass:
            reject = reject or "SPREAD_TOO_WIDE"
        elif candidate and not max_price_pass and ask is not None:
            reject = reject or "PRICE_ABOVE_CAP"

        flat = pos is None
        if not flat:
            reject = "POSITION_OPEN"
        elif not one_entry_pass:
            reject = "ALREADY_ENTERED"
        elif not entry_window_pass:
            reject = "OUTSIDE_ENTRY_WINDOW"

        entry_allowed = bool(
            flat and one_entry_pass and entry_window_pass and margin_pass
            and quote_pass and max_price_pass and spread_pass and balance_pass
        )
        if entry_allowed:
            reject = None

        adverse = None
        adverse_since = rt.get("adverse_since")
        adverse_duration = None
        cut_confirmed = None
        cut_margin = p.get("cut_margin_bps")
        cut_confirm = p.get("cut_confirm_s")
        if bot.get("strategy") == "twap_lockin_cut" and pos is not None and margin is not None:
            side = pos.get("side")
            thr = float(cut_margin or 0)
            adverse = (margin < -thr) if side == "UP" else (margin > thr)
            if adverse and adverse_since is not None:
                adverse_duration = now - adverse_since
                cut_confirmed = adverse_duration >= float(cut_confirm or 0) * 1000.0
            else:
                cut_confirmed = False
        profit99 = 0
        if pos is not None and pos.get("window_id") == pipe.window_id:
            held_bid = q.get(f"{str(pos.get('side','')).lower()}_bid")
            profit99 = 1 if held_bid is not None and held_bid >= 99 else 0

        return (
            time.time() * 1000.0,
            bot.get("id"),
            bot.get("strategy"),
            bot.get("asset"),
            pipe.window_id,
            self.settings_version_id(bot),
            "OPEN" if pos else "FLAT",
            pos.get("side") if pos else None,
            pos.get("entry_cents") if pos else None,
            pos.get("shares") if pos else None,
            remaining,
            margin,
            candidate,
            _bit(entry_window_pass),
            _bit(margin_pass),
            _bit(quote_pass),
            _bit(max_price_pass),
            _bit(spread_pass),
            _bit(balance_pass),
            _bit(one_entry_pass),
            _bit(entry_allowed),
            reject,
            cut_margin,
            cut_confirm,
            None if adverse is None else _bit(adverse),
            adverse_since,
            adverse_duration,
            None if cut_confirmed is None else _bit(bool(cut_confirmed)),
            profit99,
            json.dumps(warnings) if warnings else None,
        )


def _snapshot_row(pipe, now: float) -> tuple:
    market = pipe.polymarket.market or {}
    q = pipe.last_quote or {}
    cl_ts = pipe.rolling.last_sample_ms
    twap60_ts = getattr(pipe, "_twap60_src_ms", None)
    projected = None
    try:
        projected = pipe.rolling.value_at(pipe.window_end_ms)
    except Exception:
        projected = None
    margin = projected_margin_bps(pipe)
    quote_t = q.get("t")

    def age(ts):
        if ts is None:
            return None
        return now - ts

    return (
        time.time() * 1000.0,
        pipe.asset.key,
        pipe.window_id,
        market.get("slug"),
        market.get("condition_id"),
        market.get("up_token"),
        market.get("down_token"),
        pipe.window_start_ms,
        pipe.window_end_ms,
        pipe.window_end_ms - now,
        pipe.ptb,
        pipe.ptb_source,
        None,  # PTB latch has no stored source timestamp
        None,
        pipe.chainlink,
        cl_ts,
        age(cl_ts),
        pipe.twap30,
        pipe.twap60,
        twap60_ts,
        age(twap60_ts) if twap60_ts is not None else None,
        pipe.rolling.value,
        pipe.rolling.lookback_s,
        projected,
        margin,
        q.get("up_bid"), q.get("up_ask"), q.get("up_bid_size"), q.get("up_ask_size"),
        q.get("down_bid"), q.get("down_ask"), q.get("down_bid_size"), q.get("down_ask_size"),
        quote_t,
        age(quote_t),
    )


recorder = ResearchRecorder()


def safe_event(bot, event_type: str, **kwargs) -> None:
    try:
        recorder.event(bot, event_type, **kwargs)
    except Exception:
        recorder.note_failure()


def safe_oracle(asset: str, source: str, source_ts_ms: float,
                receive_ts_ms: float | None, price: float) -> None:
    try:
        recorder.oracle(asset, source, source_ts_ms, receive_ts_ms, price)
    except Exception:
        recorder.note_failure()


def safe_tick(mgr) -> None:
    try:
        recorder.tick(mgr)
    except Exception:
        recorder.note_failure()
