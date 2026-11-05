"""Build strategy-visible state from the tape. Future receives are invisible."""

from __future__ import annotations

import bisect

from backend.engine.metrics import rolling_twap
from backend.research.replay.models import MarketState, OraclePoint, Snap


def projected_margin(ptb: float | None, ptb_source: str | None, projected: float | None) -> float | None:
    if ptb is None or ptb <= 0 or ptb_source == "exchange_approx" or projected is None:
        return None
    return (projected - ptb) / ptb * 10_000.0


def _project(samples: list[tuple[float, float]], window_end: float, lookback_s: int) -> float | None:
    if not samples:
        return None
    return rolling_twap(samples, window_end, lookback_s)


class TapeCursor:
    """Monotonic clock. Oracle rows are admitted only when receive_ts <= wall time."""

    def __init__(self, snaps: list[Snap], oracle: list[OraclePoint]) -> None:
        self.snaps = snaps
        self.oracle = oracle
        end = snaps[0].window_end_ms
        self.window_end = end
        self.window_start = end - 300_000.0
        self.window_id = snaps[0].window_id

    def states(self) -> list[MarketState]:
        first = self.snaps[0].t_ms
        last = self.snaps[-1].t_ms
        lookback = self.snaps[0].lookback_s or 60
        # value_at(window_end) holds the last price across [end-lookback, end].
        # The sample that straddles that interval is often 60s+ before the
        # entry (Bot A enters with ~90s left). Keep the prior lookback.
        floor = self.window_start - (lookback + 30) * 1000.0
        samples: list[tuple[float, float]] = []
        seen: set[float] = set()
        for pt in self.oracle:
            if pt.receive_ts_ms > first:
                break
            if pt.source_ts_ms < floor or pt.source_ts_ms in seen:
                continue
            seen.add(pt.source_ts_ms)
            samples.append((pt.source_ts_ms, pt.price))
        samples.sort()

        events: list[tuple[float, int, str, object]] = []
        for s in self.snaps:
            events.append((s.t_ms, 0, "snap", s))
        for pt in self.oracle:
            if first < pt.receive_ts_ms <= last:
                events.append((pt.receive_ts_ms, 1, "oracle", pt))
        events.sort(key=lambda e: (e[0], e[1]))

        quote: Snap | None = None
        offset = self.snaps[0].t_ms - (self.window_end - self.snaps[0].remaining_ms)
        last_oracle_t: float | None = None
        out: list[MarketState] = []
        oi = 0
        while oi < len(self.oracle) and self.oracle[oi].receive_ts_ms <= first:
            oi += 1
        for wall, _ord, kind, payload in events:
            if kind == "oracle":
                pt = payload
                assert isinstance(pt, OraclePoint)
                last_oracle_t = pt.receive_ts_ms
                if pt.source_ts_ms >= floor and pt.source_ts_ms not in seen:
                    seen.add(pt.source_ts_ms)
                    bisect.insort(samples, (pt.source_ts_ms, pt.price))
            else:
                quote = payload  # type: ignore[assignment]
                assert isinstance(quote, Snap)
                offset = quote.t_ms - (self.window_end - quote.remaining_ms)
                lookback = quote.lookback_s or lookback
            if quote is None:
                continue
            poly = wall - offset
            remaining = self.window_end - poly
            projected = _project(samples, self.window_end, lookback)
            margin = projected_margin(quote.ptb, quote.ptb_source, projected)
            out.append(MarketState(
                wall_t=wall,
                poly_t=poly,
                window_id=self.window_id,
                window_start_ms=self.window_start,
                window_end_ms=self.window_end,
                remaining_ms=remaining,
                ptb=quote.ptb,
                ptb_source=quote.ptb_source,
                projected_final=projected,
                projected_margin_bps=margin,
                up_bid=quote.up_bid,
                up_ask=quote.up_ask,
                up_bid_size=quote.up_bid_size,
                up_ask_size=quote.up_ask_size,
                down_bid=quote.down_bid,
                down_ask=quote.down_ask,
                down_bid_size=quote.down_bid_size,
                down_ask_size=quote.down_ask_size,
                quote_t=quote.t_ms,
                oracle_receive_t=last_oracle_t,
            ))
        return out
