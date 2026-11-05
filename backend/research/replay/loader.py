"""Read-only load of the research tape. Opens SQLite with mode=ro."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from backend.research.replay.models import OraclePoint, Settings, Snap
from backend.research.replay.quality import WindowQuality, assess_window

BOTS = {
    "A": "b_0d55c464",
    "B": "b_384a1650",
}


@dataclass
class ProdTrade:
    bot_id: str
    window_id: int
    side: str
    entry_t: float
    entry_price: float
    shares: float
    exit_t: float | None
    exit_price: float | None
    exit_class: str | None
    pnl: float | None
    closed: bool


@dataclass
class Tape:
    cutoff_ms: float
    snaps: dict[int, list[Snap]]
    oracle: list[OraclePoint]
    outcomes: dict[int, str]
    settings: dict[str, Settings]
    quality: dict[int, WindowQuality]
    prod: dict[str, list[ProdTrade]]
    balance_before: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    decision_windows: dict[str, set[int]] = field(default_factory=dict)


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def capture_cutoff(con: sqlite3.Connection) -> float:
    row = con.execute("SELECT MAX(t_ms) FROM research_market_snapshots").fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("research tape is empty")
    return float(row[0])


def _exit_class(action: str, reason: str, pnl: float | None) -> str:
    text = (reason or "").lower()
    if action == "SELL" and "cut" in text:
        return "cut"
    if action == "SELL" and ("profit" in text or "locked" in text):
        return "profit_target"
    if action == "SETTLE":
        return "settlement_win" if (pnl or 0) > 0 else "settlement_loss"
    return "other"


def load_tape(con: sqlite3.Connection, cutoff_ms: float) -> Tape:
    snap_rows = con.execute(
        """SELECT t_ms, window_id, window_end_ms, remaining_ms, lookback_s,
                  ptb, ptb_source, projected_final, projected_margin_bps,
                  up_bid, up_ask, up_bid_size, up_ask_size,
                  down_bid, down_ask, down_bid_size, down_ask_size
           FROM research_market_snapshots
           WHERE t_ms <= ?
           ORDER BY window_id, t_ms""",
        (cutoff_ms,),
    ).fetchall()
    by: dict[int, list[Snap]] = defaultdict(list)
    for r in snap_rows:
        by[int(r["window_id"])].append(Snap(
            t_ms=r["t_ms"], window_id=int(r["window_id"]),
            window_end_ms=r["window_end_ms"], remaining_ms=r["remaining_ms"],
            lookback_s=int(r["lookback_s"] or 60),
            ptb=r["ptb"], ptb_source=r["ptb_source"],
            projected_final=r["projected_final"],
            projected_margin_bps=r["projected_margin_bps"],
            up_bid=r["up_bid"], up_ask=r["up_ask"],
            up_bid_size=r["up_bid_size"], up_ask_size=r["up_ask_size"],
            down_bid=r["down_bid"], down_ask=r["down_ask"],
            down_bid_size=r["down_bid_size"], down_ask_size=r["down_ask_size"],
        ))
    newest = snap_rows[-1]
    newest_poly = newest["window_end_ms"] - newest["remaining_ms"]

    oracle = [
        OraclePoint(r["source_ts_ms"], r["receive_ts_ms"], r["price"])
        for r in con.execute(
            """SELECT source_ts_ms, receive_ts_ms, price
               FROM research_oracle_samples
               WHERE asset='BTC' AND source='chainlink' AND receive_ts_ms <= ?
               ORDER BY receive_ts_ms, source_ts_ms""",
            (cutoff_ms,),
        )
    ]
    outcomes: dict[int, str] = {}
    for r in con.execute(
        "SELECT window_id, outcome_official, outcome_local FROM rounds WHERE asset='BTC'"
    ):
        label = r["outcome_official"] or r["outcome_local"]
        if label:
            outcomes[int(r["window_id"])] = label

    settings: dict[str, Settings] = {}
    for key, bot_id in BOTS.items():
        row = con.execute(
            """SELECT id, strategy, params_json, created_ms
               FROM research_settings_versions
               WHERE bot_id=? AND created_ms <= ?
               ORDER BY id DESC LIMIT 1""",
            (bot_id, cutoff_ms),
        ).fetchone()
        if row is None:
            continue
        settings[key] = Settings(
            bot_id=bot_id, strategy=row["strategy"], version_id=int(row["id"]),
            created_ms=float(row["created_ms"]), params=json.loads(row["params_json"]),
        )

    dec_sets: dict[str, set[int]] = {k: set() for k in BOTS}
    for r in con.execute(
        """SELECT bot_id, window_id FROM research_bot_decisions
           WHERE t_ms <= ? AND settings_version_id IS NOT NULL
           GROUP BY bot_id, window_id""",
        (cutoff_ms,),
    ):
        for key, bot_id in BOTS.items():
            if r["bot_id"] == bot_id and r["window_id"] is not None:
                dec_sets[key].add(int(r["window_id"]))

    src_ts = [p.source_ts_ms for p in sorted(oracle, key=lambda p: p.source_ts_ms)]

    def cl_count(end: float) -> int:
        import bisect
        return bisect.bisect_right(src_ts, end) - bisect.bisect_left(src_ts, end - 300_000)

    quality: dict[int, WindowQuality] = {}
    for wid, snaps in by.items():
        end = snaps[-1].window_end_ms
        completed = end is not None and newest_poly is not None and end < newest_poly - 1000
        settings_ready = any(s.created_ms <= snaps[0].t_ms for s in settings.values())
        quality[wid] = assess_window(
            snaps,
            completed=completed,
            outcome=outcomes.get(wid),
            chainlink_in_window=cl_count(end) if end else 0,
            decisions_a=1 if wid in dec_sets["A"] else 0,
            decisions_b=1 if wid in dec_sets["B"] else 0,
            settings_ready=settings_ready,
        )

    prod: dict[str, list[ProdTrade]] = {k: [] for k in BOTS}
    balance_before: dict[str, list[tuple[float, float]]] = {k: [] for k in BOTS}
    for key, bot_id in BOTS.items():
        rows = con.execute(
            """SELECT t, window_id, action, side, price_cents, shares, pnl, reason, balance
               FROM trades WHERE bot_id=? AND t <= ? ORDER BY t""",
            (bot_id, cutoff_ms),
        ).fetchall()
        for r in rows:
            balance_before[key].append((float(r["t"]), float(r["balance"] or 0)))
        buys = [r for r in rows if r["action"] == "BUY" and r["window_id"] is not None]
        for b in buys:
            later = [
                r for r in rows
                if r["window_id"] == b["window_id"] and r["t"] >= b["t"] and r["action"] != "BUY"
            ]
            exits = [r for r in later if r["action"] in ("SELL", "SETTLE", "ADJUST", "VOID")]
            pnl = sum((r["pnl"] or 0) for r in exits) if exits else None
            sell = next((r for r in exits if r["action"] == "SELL"), None)
            settle = next((r for r in exits if r["action"] == "SETTLE"), None)
            chosen = sell or settle
            klass = None
            exit_t = None
            exit_px = None
            if chosen is not None:
                klass = _exit_class(chosen["action"], chosen["reason"] or "", chosen["pnl"])
                exit_t = float(chosen["t"])
                exit_px = chosen["price_cents"]
            prod[key].append(ProdTrade(
                bot_id=bot_id, window_id=int(b["window_id"]), side=b["side"],
                entry_t=float(b["t"]), entry_price=float(b["price_cents"] or 0),
                shares=float(b["shares"] or 0), exit_t=exit_t, exit_price=exit_px,
                exit_class=klass, pnl=pnl, closed=bool(exits),
            ))
    return Tape(
        cutoff_ms=cutoff_ms, snaps=dict(by), oracle=oracle, outcomes=outcomes,
        settings=settings, quality=quality, prod=prod, balance_before=balance_before,
        decision_windows=dec_sets,
    )


def balance_at(points: list[tuple[float, float]], t0: float, default: float = 1000.0) -> float:
    bal = default
    for t, b in points:
        if t >= t0:
            break
        bal = b
    return bal
