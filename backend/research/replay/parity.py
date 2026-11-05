"""Production-parity scoring.

Classifications are fixed here, before the replay is scored.

EXACT
  same window, both entered, same side, same exit class,
  entry and exit prices within 0.11 cent,
  entry time within 2.5 s, exit time within 5 s.

NEAR_MATCH
  same side and exit class,
  prices within 2 cents,
  entry time within 15 s, exit time within 20 s.

MISMATCH
  a clean-universe trade whose economics differ, including a missed
  production entry or an extra replay entry.

UNREPLAYABLE
  required tape is outside the clean universe. Those windows are excluded
  from scoring rather than counted as strategy mismatches.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from backend.research.replay.loader import ProdTrade, Tape
from backend.research.replay.models import ClosedTrade
from backend.research.replay.quality import WindowQuality

ENTRY_EXACT_MS = 2500.0
EXIT_EXACT_MS = 5000.0
ENTRY_NEAR_MS = 15_000.0
EXIT_NEAR_MS = 20_000.0
PRICE_EXACT = 0.11
PRICE_NEAR = 2.0


def _class_row(prod: ProdTrade | None, rep: ClosedTrade | None) -> dict:
    if prod is None and rep is None:
        klass = "EXACT"
        reason = ""
    elif prod is not None and not prod.closed:
        klass = "UNREPLAYABLE"
        reason = "MISSING_DATA"
    elif prod is None and rep is not None:
        klass = "MISMATCH"
        reason = "EXECUTION_LOOP_TIMING"
    elif prod is not None and rep is None:
        klass = "MISMATCH"
        reason = "EXECUTION_LOOP_TIMING"
    else:
        assert prod is not None and rep is not None
        same_side = prod.side == rep.side
        same_exit = prod.exit_class == rep.exit_class
        ep = abs((prod.entry_price or 0) - rep.entry_price)
        xp = abs((prod.exit_price or 0) - rep.exit_price) if prod.exit_price is not None else None
        et = abs(prod.entry_t - rep.entry_t)
        xt = None if prod.exit_t is None else abs(prod.exit_t - rep.exit_t)
        if not same_side:
            klass, reason = "MISMATCH", "REPLAY_LOGIC_BUG"
        elif not same_exit:
            klass, reason = "MISMATCH", "CUT_TIMER_STATE" if "cut" in (prod.exit_class, rep.exit_class) else "SETTLEMENT_STATE"
        elif (
            ep <= PRICE_EXACT and (xp is None or xp <= PRICE_EXACT)
            and et <= ENTRY_EXACT_MS and (xt is None or xt <= EXIT_EXACT_MS)
        ):
            klass, reason = "EXACT", ""
        elif (
            ep <= PRICE_NEAR and (xp is None or xp <= PRICE_NEAR)
            and et <= ENTRY_NEAR_MS and (xt is None or xt <= EXIT_NEAR_MS)
        ):
            klass, reason = "NEAR_MATCH", "SNAPSHOT_RESOLUTION"
        else:
            klass, reason = "MISMATCH", "SNAPSHOT_RESOLUTION" if same_side and same_exit else "UNKNOWN"
        if klass == "MISMATCH" and same_side and same_exit and ep > PRICE_NEAR:
            reason = "QUOTE_RESOLUTION"
    wid = prod.window_id if prod else rep.window_id  # type: ignore[union-attr]
    return {
        "window_id": wid,
        "production_entered": int(prod is not None),
        "replay_entered": int(rep is not None),
        "production_side": None if prod is None else prod.side,
        "replay_side": None if rep is None else rep.side,
        "production_entry_t": None if prod is None else prod.entry_t,
        "replay_entry_t": None if rep is None else rep.entry_t,
        "entry_delta_ms": None if prod is None or rep is None else rep.entry_t - prod.entry_t,
        "production_entry_price": None if prod is None else prod.entry_price,
        "replay_entry_price": None if rep is None else rep.entry_price,
        "entry_price_delta": None if prod is None or rep is None else rep.entry_price - prod.entry_price,
        "production_shares": None if prod is None else prod.shares,
        "replay_shares": None if rep is None else rep.shares,
        "production_exit_class": None if prod is None else prod.exit_class,
        "replay_exit_class": None if rep is None else rep.exit_class,
        "production_exit_t": None if prod is None else prod.exit_t,
        "replay_exit_t": None if rep is None else rep.exit_t,
        "exit_delta_ms": None if prod is None or rep is None or prod.exit_t is None else rep.exit_t - prod.exit_t,
        "production_exit_price": None if prod is None else prod.exit_price,
        "replay_exit_price": None if rep is None else rep.exit_price,
        "production_pnl": None if prod is None else prod.pnl,
        "replay_pnl": None if rep is None else rep.pnl,
        "pnl_delta": None if prod is None or rep is None or prod.pnl is None else rep.pnl - prod.pnl,
        "parity": klass,
        "mismatch_reason": reason,
    }


def score_bot(tape: Tape, bot_key: str, replay: list[ClosedTrade]) -> dict:
    flag = "eligible_a" if bot_key == "A" else "eligible_b"
    clean = {wid for wid, row in tape.quality.items() if getattr(row, flag)}
    prod = {t.window_id: t for t in tape.prod[bot_key] if t.window_id in clean and t.closed}
    rep = {t.window_id: t for t in replay if t.window_id in clean}
    windows = sorted(clean)
    rows = []
    matrix = Counter()
    for wid in windows:
        p = prod.get(wid)
        r = rep.get(wid)
        if p and r:
            matrix["both"] += 1
        elif p and not r:
            matrix["missed"] += 1
        elif r and not p:
            matrix["extra"] += 1
        else:
            matrix["neither"] += 1
        if p or r:
            rows.append(_class_row(p, r))
    entry_deltas = [r["entry_delta_ms"] for r in rows if r["entry_delta_ms"] is not None and r["parity"] != "UNREPLAYABLE"]
    price_deltas = [abs(r["entry_price_delta"]) for r in rows if r["entry_price_delta"] is not None]
    matched = [r for r in rows if r["production_entered"] and r["replay_entered"] and r["production_side"] == r["replay_side"]]
    exit_same = [r for r in matched if r["production_exit_class"] == r["replay_exit_class"]]
    exit_px = [r for r in exit_same if r["production_exit_price"] is not None and abs((r["replay_exit_price"] or 0) - r["production_exit_price"]) <= PRICE_EXACT]
    exit_deltas = [r["exit_delta_ms"] for r in matched if r["exit_delta_ms"] is not None]

    def med(xs):
        if not xs:
            return None
        ys = sorted(xs)
        return ys[len(ys) // 2]

    def pct(xs, p):
        if not xs:
            return None
        ys = sorted(xs)
        return ys[min(len(ys) - 1, int(round((len(ys) - 1) * p)))]

    prod_pnls = [t.pnl for t in prod.values() if t.pnl is not None]
    rep_pnls = [t.pnl for t in rep.values()]
    def wl(pnls):
        return sum(p > 0 for p in pnls), sum(p < 0 for p in pnls)
    pw, pl = wl(prod_pnls)
    rw, rl = wl(rep_pnls)
    pgp = sum(p for p in prod_pnls if p > 0)
    pgl = sum(p for p in prod_pnls if p < 0)
    rgp = sum(p for p in rep_pnls if p > 0)
    rgl = sum(p for p in rep_pnls if p < 0)
    cuts_p = [t for t in prod.values() if t.exit_class == "cut"]
    cuts_r = [t for t in rep.values() if t.exit_class == "cut"]
    cut_ids_p = {t.window_id for t in cuts_p}
    cut_ids_r = {t.window_id for t in cuts_r}
    wrong = sum(1 for r in rows if r["production_entered"] and r["replay_entered"] and r["production_side"] != r["replay_side"])
    summary = {
        "clean_windows": len(clean),
        "production_entry_windows": len(prod),
        "replay_entry_windows": len(rep),
        "matched_entries": matrix["both"],
        "missed": matrix["missed"],
        "extra": matrix["extra"],
        "neither": matrix["neither"],
        "wrong_side": wrong,
        "entry_agreement": (matrix["both"] + matrix["neither"]) / len(clean) if clean else None,
        "side_agreement": (len(matched) / matrix["both"]) if matrix["both"] else None,
        "median_entry_dt": med(entry_deltas),
        "p95_entry_dt": pct([abs(x) for x in entry_deltas], 0.95),
        "max_entry_dt": max(entry_deltas) if entry_deltas else None,
        "median_entry_px": med(price_deltas),
        "max_entry_px": max(price_deltas) if price_deltas else None,
        "same_exit_reason": (len(exit_same) / len(matched)) if matched else None,
        "same_exit_price": (len(exit_px) / len(matched)) if matched else None,
        "median_exit_dt": med(exit_deltas),
        "p95_exit_dt": pct([abs(x) for x in exit_deltas], 0.95),
        "max_exit_dt": max(exit_deltas) if exit_deltas else None,
        "production_w": pw, "production_l": pl,
        "replay_w": rw, "replay_l": rl,
        "production_pnl": sum(prod_pnls),
        "replay_pnl": sum(rep_pnls),
        "pnl_delta": sum(rep_pnls) - sum(prod_pnls),
        "production_gp": pgp, "production_gl": pgl,
        "replay_gp": rgp, "replay_gl": rgl,
        "production_pf": (pgp / abs(pgl)) if pgl else None,
        "replay_pf": (rgp / abs(rgl)) if rgl else None,
        "production_cuts": len(cuts_p),
        "replay_cuts": len(cuts_r),
        "matched_cuts": len(cut_ids_p & cut_ids_r),
        "missed_cuts": len(cut_ids_p - cut_ids_r),
        "extra_cuts": len(cut_ids_r - cut_ids_p),
        "classes": dict(Counter(r["parity"] for r in rows)),
        "reasons": dict(Counter(r["mismatch_reason"] for r in rows if r["mismatch_reason"])),
    }
    return {"rows": rows, "summary": summary, "extra_windows": sorted(rep.keys() - prod.keys())}


def write_quality_csv(path: Path, quality: dict[int, WindowQuality]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "window_id", "fully_captured", "has_outcome", "ptb_valid", "oracle_sufficient",
        "snapshot_sufficient", "settings_available", "decisions_a", "decisions_b",
        "known_bad_range", "eligible_a", "eligible_b", "eligible_for_parity",
        "outcome", "snapshot_count", "max_gap_ms", "exclusion_reasons",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for wid in sorted(quality):
            row = quality[wid]
            w.writerow({
                "window_id": wid,
                "fully_captured": int(row.fully_captured),
                "has_outcome": int(row.has_outcome),
                "ptb_valid": int(row.ptb_valid),
                "oracle_sufficient": int(row.oracle_sufficient),
                "snapshot_sufficient": int(row.snapshot_sufficient),
                "settings_available": int(row.settings_available),
                "decisions_a": int(row.decisions_a),
                "decisions_b": int(row.decisions_b),
                "known_bad_range": int(row.known_bad_range),
                "eligible_a": int(row.eligible_a),
                "eligible_b": int(row.eligible_b),
                "eligible_for_parity": int(row.eligible_for_parity),
                "outcome": row.outcome or "",
                "snapshot_count": row.snapshot_count,
                "max_gap_ms": round(row.max_gap_ms, 1),
                "exclusion_reasons": "|".join(row.exclusion_reasons),
            })


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_replay_trades(path: Path, trades: list[ClosedTrade]) -> None:
    rows = [
        {
            "bot_id": t.bot_id, "window_id": t.window_id, "side": t.side,
            "entry_t": t.entry_t, "entry_price": t.entry_price, "shares": t.shares,
            "cost": t.cost, "entry_fee": t.entry_fee, "exit_t": t.exit_t,
            "exit_price": t.exit_price, "exit_fee": t.exit_fee, "exit_class": t.exit_class,
            "pnl": t.pnl, "margin_at_entry": t.margin_at_entry,
        }
        for t in trades
    ]
    write_rows(path, rows)
