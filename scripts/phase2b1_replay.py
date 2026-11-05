"""Offline production-parity replay. Does not write the trading database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.data_dir import data_dir
from backend.research.replay.engine import projected_final_errors, replay_bot, replay_timed
from backend.research.replay.loader import connect, capture_cutoff, load_tape
from backend.research.replay.parity import score_bot, write_quality_csv, write_replay_trades, write_rows


def _sig(trades) -> list:
    return [
        (t.window_id, t.side, round(t.entry_price, 6), round(t.shares, 6),
         t.exit_class, round(t.exit_price, 6), round(t.pnl, 6))
        for t in trades
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2B.1 offline replay")
    parser.add_argument("--bot", choices=("A", "B", "all"), default="all")
    parser.add_argument("--cutoff-ms", type=float, default=None)
    parser.add_argument("--output-dir", default="research_output")
    args = parser.parse_args()
    db = data_dir() / "twaphunter.db"
    con = connect(db)
    cutoff = capture_cutoff(con)
    if args.cutoff_ms is not None:
        cutoff = min(cutoff, args.cutoff_ms)
    tape = load_tape(con, cutoff)
    con.close()

    keys = ("A", "B") if args.bot == "all" else (args.bot,)
    out = Path(args.output_dir)
    first, _ = replay_timed(tape, keys[0])
    second = replay_bot(tape, keys[0])
    deterministic = _sig(first) == _sig(second)

    results = {}
    all_rows = []
    mismatches = []
    replay_trades = []
    timings = {}
    finals = {}
    for key in keys:
        trades, timing = replay_timed(tape, key)
        if key == keys[0]:
            trades = first
        scored = score_bot(tape, key, trades)
        results[key] = scored["summary"]
        for row in scored["rows"]:
            row = {"bot": key, **row}
            all_rows.append(row)
            if row["parity"] in ("MISMATCH", "UNREPLAYABLE"):
                mismatches.append(row)
        replay_trades.extend(trades)
        timings[key] = timing
        finals[key] = projected_final_errors(tape, key)

    write_quality_csv(out / "phase2b1_window_quality.csv", tape.quality)
    write_rows(out / "phase2b1_trade_parity.csv", [r for r in all_rows if r["production_entered"]])
    write_rows(out / "phase2b1_entry_parity.csv", all_rows)
    write_rows(out / "phase2b1_mismatches.csv", mismatches)
    write_replay_trades(out / "phase2b1_replay_trades.csv", replay_trades)

    clean = [q for q in tape.quality.values() if q.eligible_for_parity]
    ups = sum(1 for q in clean if q.outcome == "UP")
    downs = sum(1 for q in clean if q.outcome == "DOWN")
    reasons: dict[str, int] = {}
    for q in tape.quality.values():
        if q.eligible_for_parity:
            continue
        for reason in q.exclusion_reasons:
            reasons[reason] = reasons.get(reason, 0) + 1
    summary = {
        "cutoff_ms": cutoff,
        "completed_windows": sum(1 for q in tape.quality.values() if "not_completed" not in q.exclusion_reasons),
        "fully_captured": sum(1 for q in tape.quality.values() if q.fully_captured),
        "clean_windows": len(clean),
        "clean_up": ups,
        "clean_down": downs,
        "exclusion_reason_counts": reasons,
        "deterministic": deterministic,
        "bots": results,
        "timing": timings,
        "projected_final": finals,
        "settings": {k: {"id": s.version_id, "strategy": s.strategy, "params": s.params} for k, s in tape.settings.items()},
    }
    (out / "phase2b1_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "cutoff_ms": cutoff,
        "clean_windows": len(clean),
        "clean_up": ups,
        "clean_down": downs,
        "deterministic": deterministic,
        "bots": results,
        "projected_final": finals,
        "timing": timings,
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
