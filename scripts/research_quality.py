"""Read-only Phase 2A collection quality check. Does not write."""

from __future__ import annotations

import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.storage import DB_PATH  # noqa: E402


def _pct(n: int, d: int) -> str:
    if d <= 0:
        return "n/a"
    return f"{100.0 * n / d:.1f}%"


def main() -> None:
    path = DB_PATH
    print(f"db {path}")
    if not path.exists():
        print("database missing")
        return
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    tables = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    needed = [
        "research_market_snapshots",
        "research_oracle_samples",
        "research_bot_decisions",
        "research_execution_events",
        "research_settings_versions",
    ]
    missing = [t for t in needed if t not in tables]
    if missing:
        print("research tables not created yet:", ", ".join(missing))
        print("RESTART REQUIRED before collection can start")
        return

    def count(table: str) -> int:
        return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    snaps = count("research_market_snapshots")
    oracles = count("research_oracle_samples")
    decisions = count("research_bot_decisions")
    events = count("research_execution_events")
    versions = count("research_settings_versions")
    rounds = con.execute(
        "SELECT COUNT(DISTINCT window_id) FROM research_market_snapshots"
    ).fetchone()[0]

    def missing_pct(column: str) -> str:
        if snaps == 0:
            return "n/a"
        n = con.execute(
            f"SELECT COUNT(*) FROM research_market_snapshots WHERE {column} IS NULL"
        ).fetchone()[0]
        return _pct(n, snaps)

    stale_oracle = "n/a"
    stale_quote = "n/a"
    if snaps:
        stale_oracle = _pct(
            con.execute(
                "SELECT COUNT(*) FROM research_market_snapshots "
                "WHERE chainlink_age_ms IS NULL OR chainlink_age_ms > 5000"
            ).fetchone()[0],
            snaps,
        )
        stale_quote = _pct(
            con.execute(
                "SELECT COUNT(*) FROM research_market_snapshots "
                "WHERE quote_age_ms IS NULL OR quote_age_ms > 5000"
            ).fetchone()[0],
            snaps,
        )

    gaps = []
    rows = con.execute(
        "SELECT window_id, t_ms FROM research_market_snapshots "
        "ORDER BY window_id, t_ms"
    ).fetchall()
    prev = None
    for wid, t_ms in rows:
        if prev is not None and prev[0] == wid:
            gaps.append(t_ms - prev[1])
        prev = (wid, t_ms)

    def qtile(xs: list[float], p: float) -> str:
        if not xs:
            return "n/a"
        xs = sorted(xs)
        i = min(len(xs) - 1, max(0, int(round((len(xs) - 1) * p))))
        return f"{xs[i]:.0f}"

    replayable = "n/a"
    if snaps:
        good = con.execute(
            "SELECT COUNT(*) FROM research_market_snapshots "
            "WHERE ptb IS NOT NULL AND projected_final IS NOT NULL "
            "AND up_ask IS NOT NULL AND down_ask IS NOT NULL "
            "AND chainlink_source_ts_ms IS NOT NULL"
        ).fetchone()[0]
        replayable = _pct(good, snaps)

    print(f"rounds_with_snapshots {rounds}")
    print(f"snapshots {snaps}")
    print(f"oracle_samples {oracles}")
    print(f"decisions {decisions}")
    print(f"execution_events {events}")
    print(f"settings_versions {versions}")
    print(f"missing_ptb {missing_pct('ptb')}")
    print(f"missing_projected_final {missing_pct('projected_final')}")
    print(f"missing_quote {missing_pct('up_ask')}")
    print(f"stale_oracle_gt_5s {stale_oracle}")
    print(f"stale_quote_gt_5s {stale_quote}")
    if gaps:
        print(f"snapshot_gap_ms largest {max(gaps):.0f} "
              f"median {statistics.median(gaps):.0f} p95 {qtile(gaps, 0.95)}")
    else:
        print("snapshot_gap_ms n/a")
    print(f"replayable_snapshots {replayable}")


if __name__ == "__main__":
    main()
