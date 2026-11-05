"""Production paper rules for twap_lockin and twap_lockin_cut.

This is a copy of the live gates for offline use. It does not import the
live bot manager, so a replay run cannot open the trading database for write
or call buy/sell on the running process.

Oddities are kept on purpose: a failed cut sell still clears the timer, and
a missing margin does not reset that timer.
"""

from __future__ import annotations

from backend.research.replay.models import (
    LOCK_PROFIT_CENTS,
    MIN_ORDER_SHARES,
    SPREAD_MAX_CENTS,
    BotRuntime,
    ClosedTrade,
    MarketState,
    Position,
    Settings,
    taker_fee,
)


def _try_buy(rt: BotRuntime, st: MarketState, settings: Settings) -> str | None:
    p = settings.params
    if rt.position is not None:
        return None
    if rt.entries.get(st.window_id, 0) >= 1:
        return None
    if not (10_000 <= st.remaining_ms <= float(p["entry_window_s"]) * 1000.0):
        return None
    if st.window_end_ms - st.poly_t < 5000 or st.poly_t - st.window_start_ms < 2000:
        return None
    margin = st.projected_margin_bps
    if margin is None or abs(margin) < float(p["min_margin_bps"]):
        return None
    side = "UP" if margin > 0 else "DOWN"
    ask, ask_size, bid, _bid_size = st.side_quote(side)
    max_entry = float(p["max_entry_cents"])
    if ask is None or ask > max_entry or ask <= 0 or ask >= 100:
        return None
    if bid is None or bid <= 0 or (ask - bid) > SPREAD_MAX_CENTS:
        return None
    price = ask / 100.0
    order_usd = float(p["order_usd"])
    shares = order_usd / price
    if ask_size is not None:
        shares = min(shares, ask_size)
    if shares < MIN_ORDER_SHARES:
        return None
    cost = shares * price
    fee = taker_fee(shares, price)
    if cost + fee > rt.balance:
        return None
    rt.balance -= cost + fee
    rt.position = Position(
        side=side, shares=shares, cost=cost, fee_paid=fee,
        entry_cents=ask, entry_t=st.wall_t, window_id=st.window_id,
        margin_at_entry=margin,
    )
    rt.entries[st.window_id] = rt.entries.get(st.window_id, 0) + 1
    return "entry"


def _try_sell(rt: BotRuntime, st: MarketState, exit_class: str, bot_id: str) -> ClosedTrade | None:
    pos = rt.position
    if pos is None or pos.window_id != st.window_id:
        return None
    _ask, _asz, bid, _bsz = st.side_quote(pos.side)
    if bid is None or bid <= 0:
        return None
    price = bid / 100.0
    proceeds = pos.shares * price
    fee = taker_fee(pos.shares, price)
    rt.balance += proceeds - fee
    pnl = (proceeds - fee) - (pos.cost + pos.fee_paid)
    trade = ClosedTrade(
        bot_id=bot_id, window_id=pos.window_id, side=pos.side,
        entry_t=pos.entry_t, entry_price=pos.entry_cents, shares=pos.shares,
        cost=pos.cost, entry_fee=pos.fee_paid, exit_t=st.wall_t, exit_price=bid,
        exit_fee=fee, exit_class=exit_class, pnl=pnl,
        margin_at_entry=pos.margin_at_entry, adverse_since=rt.adverse_since,
        cut_confirmed_t=rt.cut_confirmed_t,
    )
    rt.position = None
    return trade


def _profit(rt: BotRuntime, st: MarketState, bot_id: str) -> ClosedTrade | None:
    pos = rt.position
    if pos is None or pos.window_id != st.window_id:
        return None
    _ask, _asz, bid, _bsz = st.side_quote(pos.side)
    if bid is None or bid < LOCK_PROFIT_CENTS:
        return None
    return _try_sell(rt, st, "profit_target", bot_id)


def _adverse(margin: float, side: str, cut_margin: float) -> bool:
    if side == "UP":
        return margin < -cut_margin
    return margin > cut_margin


def _cut(rt: BotRuntime, st: MarketState, settings: Settings) -> ClosedTrade | None:
    pos = rt.position
    if pos is None or pos.window_id != st.window_id:
        return None
    margin = st.projected_margin_bps
    if margin is None:
        return None
    cut_margin = float(settings.params["cut_margin_bps"])
    if not _adverse(margin, pos.side, cut_margin):
        rt.adverse_since = None
        rt.cut_confirmed_t = None
        return None
    if rt.adverse_since is None:
        rt.adverse_since = st.poly_t
        return None
    confirm_ms = float(settings.params["cut_confirm_s"]) * 1000.0
    if st.poly_t - rt.adverse_since < confirm_ms:
        return None
    rt.cut_confirmed_t = st.poly_t
    trade = _try_sell(rt, st, "cut", settings.bot_id)
    rt.adverse_since = None
    return trade


def settle(rt: BotRuntime, settings: Settings, outcome: str, settle_t: float) -> ClosedTrade | None:
    """Outcome is read only here, after the window can no longer be traded."""
    pos = rt.position
    if pos is None:
        return None
    won = outcome == pos.side
    proceeds = pos.shares * (1.0 if won else 0.0)
    rt.balance += proceeds
    pnl = proceeds - (pos.cost + pos.fee_paid)
    trade = ClosedTrade(
        bot_id=settings.bot_id, window_id=pos.window_id, side=pos.side,
        entry_t=pos.entry_t, entry_price=pos.entry_cents, shares=pos.shares,
        cost=pos.cost, entry_fee=pos.fee_paid, exit_t=settle_t,
        exit_price=100.0 if won else 0.0, exit_fee=0.0,
        exit_class="settlement_win" if pnl > 0 else "settlement_loss",
        pnl=pnl, margin_at_entry=pos.margin_at_entry,
        adverse_since=rt.adverse_since, cut_confirmed_t=rt.cut_confirmed_t,
    )
    rt.position = None
    return trade


def on_new_round(rt: BotRuntime, window_id: int) -> None:
    if rt.round_id == window_id:
        return
    rt.round_id = window_id
    rt.entries = {}
    rt.adverse_since = None
    rt.cut_confirmed_t = None


def step(rt: BotRuntime, st: MarketState, settings: Settings) -> ClosedTrade | None:
    """One production evaluation. Returns a closed trade when an exit fills."""
    on_new_round(rt, st.window_id)
    if rt.position is not None and rt.position.window_id != st.window_id:
        return None
    sold = _profit(rt, st, settings.bot_id)
    if sold is not None:
        return sold
    if settings.strategy == "twap_lockin_cut" and rt.position is not None:
        return _cut(rt, st, settings)
    if rt.position is not None:
        return None
    rt.adverse_since = None
    _try_buy(rt, st, settings)
    return None
