"""Clean replay universe.

A window is not dropped because one irrelevant side of the book has no ask.
Quote checks happen at the decision, on the candidate side only.

Rules are fixed before scoring. They are not tuned to raise parity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.research.replay.models import Snap, in_stall


@dataclass
class WindowQuality:
    window_id: int
    fully_captured: bool
    has_outcome: bool
    ptb_valid: bool
    oracle_sufficient: bool
    snapshot_sufficient: bool
    settings_available: bool
    decisions_a: bool
    decisions_b: bool
    known_bad_range: bool
    eligible_a: bool
    eligible_b: bool
    exclusion_reasons: list[str] = field(default_factory=list)
    outcome: str | None = None
    first_remaining_ms: float | None = None
    last_remaining_ms: float | None = None
    snapshot_count: int = 0
    max_gap_ms: float = 0.0

    @property
    def eligible_for_parity(self) -> bool:
        return self.eligible_a or self.eligible_b


def _gaps(ts: list[float]) -> list[float]:
    return [b - a for a, b in zip(ts, ts[1:])]


def assess_window(
    snaps: list[Snap],
    *,
    completed: bool,
    outcome: str | None,
    chainlink_in_window: int,
    decisions_a: int,
    decisions_b: int,
    settings_ready: bool,
) -> WindowQuality:
    wid = snaps[0].window_id if snaps else -1
    ts = [s.t_ms for s in snaps]
    gs = _gaps(ts)
    span = (ts[-1] - ts[0]) if len(ts) >= 2 else 0.0
    max_gap = max(gs) if gs else 0.0
    first_rem = snaps[0].remaining_ms if snaps else None
    last_rem = snaps[-1].remaining_ms if snaps else None
    fully = bool(
        completed
        and first_rem is not None
        and last_rem is not None
        and first_rem >= 290_000
        and last_rem <= 15_000
        and span >= 270_000
        and len(snaps) >= 200
    )
    n = len(snaps)
    valid_ptb = 0
    for s in snaps:
        if s.ptb is not None and s.ptb > 0 and s.ptb_source != "exchange_approx":
            valid_ptb += 1
    ptb_valid = n > 0 and valid_ptb / n >= 0.95
    oracle_ok = chainlink_in_window >= 100
    snap_ok = n >= 200 and span >= 240_000 and max_gap <= 10_000
    bad = in_stall(wid)
    reasons: list[str] = []
    if not completed:
        reasons.append("not_completed")
    if not fully:
        reasons.append("not_fully_captured")
    if bad:
        reasons.append("EXCLUDED_DATA_QUALITY")
        reasons.append("missing_outcomes_region")
        reasons.append("oracle_reconstruction_risk")
        reasons.append("pre_outage_region")
    if not outcome:
        reasons.append("missing_outcome")
    if not ptb_valid:
        reasons.append("ptb_invalid")
    if not oracle_ok:
        reasons.append("oracle_insufficient")
    if not snap_ok:
        reasons.append("snapshot_insufficient")
    if not settings_ready:
        reasons.append("settings_missing")
    shared_ok = not reasons
    reasons_a = list(reasons)
    reasons_b = list(reasons)
    if decisions_a <= 0:
        reasons_a.append("decisions_missing")
    if decisions_b <= 0:
        reasons_b.append("decisions_missing")
    return WindowQuality(
        window_id=wid,
        fully_captured=fully,
        has_outcome=bool(outcome),
        ptb_valid=ptb_valid,
        oracle_sufficient=oracle_ok,
        snapshot_sufficient=snap_ok,
        settings_available=settings_ready,
        decisions_a=decisions_a > 0,
        decisions_b=decisions_b > 0,
        known_bad_range=bad,
        eligible_a=shared_ok and decisions_a > 0,
        eligible_b=shared_ok and decisions_b > 0,
        exclusion_reasons=reasons_a if reasons_a == reasons_b else reasons,
        outcome=outcome,
        first_remaining_ms=first_rem,
        last_remaining_ms=last_rem,
        snapshot_count=n,
        max_gap_ms=max_gap,
    )
