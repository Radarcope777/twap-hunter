"""15-minute PTB latch built from the existing official 60s TWAP feed."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ptb15mState:
    """Current fixed PTB for one 15-minute market block."""

    value: float | None = None
    source: str | None = None
    block_id: int | None = None


class Ptb15mConnector:
    """Convert 5m PTB carry events into a stable 15m PTB reference.

    This class intentionally does not open a second websocket. The existing
    RTDS/PolyBolt connector already delivers the official 60s TWAP; this
    adapter only latches it at a 15m boundary and ignores the next two 5m
    updates.
    """

    def __init__(self, round_ms: int = 15 * 60_000) -> None:
        if round_ms <= 0:
            raise ValueError("round_ms must be positive")
        self.round_ms = int(round_ms)
        self.state = Ptb15mState()

    def accept_5m_carry(
        self,
        window_start_ms: float,
        value: float | None,
        source: str | None,
    ) -> bool:
        """Latch a carry only when the 15m market block starts."""
        if value is None or value <= 0:
            return False
        start_ms = int(window_start_ms)
        if start_ms % self.round_ms != 0:
            return False
        block_id = start_ms // self.round_ms
        if self.state.block_id == block_id:
            return False
        self.state = Ptb15mState(
            value=float(value),
            source=f"{source}_15m" if source else "ptb_15m",
            block_id=block_id,
        )
        return True

    def snapshot(self) -> dict:
        """Return a JSON-safe diagnostic snapshot."""
        return {
            "value": self.state.value,
            "source": self.state.source,
            "block_id": self.state.block_id,
            "round_ms": self.round_ms,
        }
