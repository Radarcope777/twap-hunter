"""Replay value types. Market tape and outcome labels are separate."""

from __future__ import annotations

from dataclasses import dataclass, field


FEE_RATE = 0.07
MIN_ORDER_SHARES = 1.0
LOCK_PROFIT_CENTS = 99.0
SPREAD_MAX_CENTS = 20.0

# Additional ranges can be appended without changing replay logic.
STALL_RANGES: tuple[tuple[int, int], ...] = ((5971701, 5971731),)


def in_stall(window_id: int) -> bool:
    return any(lo <= window_id <= hi for lo, hi in STALL_RANGES)


def taker_fee(shares: float, price_usd: float) -> float:
    return FEE_RATE * shares * min(price_usd, 1.0 - price_usd)


@dataclass(frozen=True)
class Snap:
    t_ms: float
    window_id: int
    window_end_ms: float
    remaining_ms: float
    lookback_s: int
    ptb: float | None
    ptb_source: str | None
    projected_final: float | None
    projected_margin_bps: float | None
    up_bid: float | None
    up_ask: float | None
    up_bid_size: float | None
    up_ask_size: float | None
    down_bid: float | None
    down_ask: float | None
    down_bid_size: float | None
    down_ask_size: float | None


@dataclass(frozen=True)
class OraclePoint:
    source_ts_ms: float
    receive_ts_ms: float
    price: float


@dataclass
class MarketState:
    """State the strategy is allowed to see. No outcome field exists here."""

    wall_t: float
    poly_t: float
    window_id: int
    window_start_ms: float
    window_end_ms: float
    remaining_ms: float
    ptb: float | None
    ptb_source: str | None
    projected_final: float | None
    projected_margin_bps: float | None
    up_bid: float | None
    up_ask: float | None
    up_bid_size: float | None
    up_ask_size: float | None
    down_bid: float | None
    down_ask: float | None
    down_bid_size: float | None
    down_ask_size: float | None
    quote_t: float | None
    oracle_receive_t: float | None

    def quote_age_ms(self) -> float | None:
        if self.quote_t is None:
            return None
        return self.wall_t - self.quote_t

    def side_quote(self, side: str) -> tuple[float | None, float | None, float | None, float | None]:
        if side == "UP":
            return self.up_ask, self.up_ask_size, self.up_bid, self.up_bid_size
        return self.down_ask, self.down_ask_size, self.down_bid, self.down_bid_size


@dataclass
class Position:
    side: str
    shares: float
    cost: float
    fee_paid: float
    entry_cents: float
    entry_t: float
    window_id: int
    margin_at_entry: float | None


@dataclass
class BotRuntime:
    balance: float
    position: Position | None = None
    entries: dict[int, int] = field(default_factory=dict)
    adverse_since: float | None = None
    cut_confirmed_t: float | None = None
    round_id: int | None = None


@dataclass
class ClosedTrade:
    bot_id: str
    window_id: int
    side: str
    entry_t: float
    entry_price: float
    shares: float
    cost: float
    entry_fee: float
    exit_t: float
    exit_price: float
    exit_fee: float
    exit_class: str
    pnl: float
    margin_at_entry: float | None
    adverse_since: float | None
    cut_confirmed_t: float | None


@dataclass(frozen=True)
class Settings:
    bot_id: str
    strategy: str
    version_id: int
    created_ms: float
    params: dict
