"""Replay one bot over a loaded tape.

Mode A (production parity) and mode B (counterfactual) share this stepper.
Mode B must not be given production fills. This function never reads them.
"""

from __future__ import annotations

import time

from backend.research.replay.loader import Tape, balance_at
from backend.research.replay.market_state import TapeCursor, projected_margin
from backend.research.replay.models import BotRuntime, ClosedTrade, Settings
from backend.research.replay.strategy import settle, step


def replay_bot(
    tape: Tape,
    bot_key: str,
    *,
    capital_mode: str = "production_wallet",
    normalized_balance: float = 1000.0,
    window_ids: list[int] | None = None,
) -> list[ClosedTrade]:
    settings = tape.settings[bot_key]
    q = tape.quality
    ids = window_ids
    if ids is None:
        flag = "eligible_a" if bot_key == "A" else "eligible_b"
        ids = sorted(wid for wid, row in q.items() if getattr(row, flag))
    trades: list[ClosedTrade] = []
    for wid in ids:
        snaps = tape.snaps[wid]
        if capital_mode == "normalized":
            bal = normalized_balance
        else:
            bal = balance_at(tape.balance_before[bot_key], snaps[0].t_ms)
        rt = BotRuntime(balance=bal)
        cursor = TapeCursor(snaps, tape.oracle)
        for state in cursor.states():
            closed = step(rt, state, settings)
            if closed is not None:
                trades.append(closed)
        if rt.position is not None:
            outcome = tape.outcomes.get(wid)
            if outcome:
                closed = settle(rt, settings, outcome, snaps[-1].t_ms)
                if closed is not None:
                    trades.append(closed)
    return trades


def projected_final_errors(tape: Tape, bot_key: str = "A") -> dict:
    """Compare reconstructed finals to stored snapshot finals on the clean universe."""
    flag = "eligible_a" if bot_key == "A" else "eligible_b"
    errs_f: list[float] = []
    errs_m: list[float] = []
    n = 0
    exact = 0
    skipped = 0
    for wid, row in tape.quality.items():
        if not getattr(row, flag):
            continue
        snaps = tape.snaps[wid]
        cursor = TapeCursor(snaps, tape.oracle)
        at_snap = {s.wall_t: s for s in cursor.states() if s.wall_t == s.quote_t}
        for snap in snaps:
            st = at_snap.get(snap.t_ms)
            if st is None or snap.projected_final is None or st.projected_final is None:
                if snap.projected_final is not None and (st is None or st.projected_final is None):
                    skipped += 1
                continue
            n += 1
            df = abs(st.projected_final - snap.projected_final)
            stored_m = snap.projected_margin_bps
            recon_m = projected_margin(snap.ptb, snap.ptb_source, st.projected_final)
            dm = None if stored_m is None or recon_m is None else abs(stored_m - recon_m)
            errs_f.append(df)
            if dm is not None:
                errs_m.append(dm)
            if df == 0 and (dm == 0 or (stored_m is None and recon_m is None)):
                exact += 1
    errs_f.sort()
    def pct(xs, p):
        if not xs:
            return None
        i = min(len(xs) - 1, int(round((len(xs) - 1) * p)))
        return xs[i]
    return {
        "rows": n,
        "exact": exact,
        "skipped_reconstruction": skipped,
        "max_final": errs_f[-1] if errs_f else None,
        "median_final": pct(errs_f, 0.5),
        "p95_final": pct(errs_f, 0.95),
        "mean_final": (sum(errs_f) / len(errs_f)) if errs_f else None,
        "max_margin": max(errs_m) if errs_m else None,
        "median_margin": pct(sorted(errs_m), 0.5),
    }


def replay_timed(tape: Tape, bot_key: str) -> tuple[list[ClosedTrade], dict]:
    t0 = time.perf_counter()
    trades = replay_bot(tape, bot_key)
    elapsed = time.perf_counter() - t0
    flag = "eligible_a" if bot_key == "A" else "eligible_b"
    windows = [wid for wid, row in tape.quality.items() if getattr(row, flag)]
    snaps = sum(len(tape.snaps[w]) for w in windows)
    return trades, {
        "windows": len(windows),
        "snapshots": snaps,
        "oracle_rows": len(tape.oracle),
        "elapsed_s": elapsed,
        "windows_per_s": (len(windows) / elapsed) if elapsed else None,
    }
