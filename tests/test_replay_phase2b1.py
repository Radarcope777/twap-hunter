"""Offline replay gates. These tests do not open the trading database."""

from __future__ import annotations

from backend.research.replay.engine import replay_bot
from backend.research.replay.loader import Tape
from backend.research.replay.market_state import TapeCursor, projected_margin
from backend.research.replay.models import (
    BotRuntime,
    MarketState,
    OraclePoint,
    Settings,
    Snap,
    taker_fee,
)
from backend.research.replay.quality import assess_window
from backend.research.replay.strategy import settle, step


def _state(**kw) -> MarketState:
    base = dict(
        wall_t=1_000_000.0, poly_t=990_000.0, window_id=10,
        window_start_ms=700_000.0, window_end_ms=1_000_000.0, remaining_ms=60_000.0,
        ptb=100_000.0, ptb_source="official_twap60_carry",
        projected_final=100_050.0, projected_margin_bps=5.0,
        up_bid=60.0, up_ask=62.0, up_bid_size=100.0, up_ask_size=100.0,
        down_bid=38.0, down_ask=40.0, down_bid_size=100.0, down_ask_size=100.0,
        quote_t=1_000_000.0, oracle_receive_t=999_000.0,
    )
    base.update(kw)
    return MarketState(**base)


def _settings(strategy="twap_lockin", **params) -> Settings:
    p = {"entry_window_s": 90, "min_margin_bps": 0.3, "max_entry_cents": 95, "order_usd": 100}
    if strategy == "twap_lockin_cut":
        p["cut_margin_bps"] = 0.5
        p["cut_confirm_s"] = 2
    p.update(params)
    bot = "b_0d55c464" if strategy == "twap_lockin_cut" else "b_384a1650"
    return Settings(bot, strategy, 1, 0.0, p)


def test_margin_sign_and_entry_window_and_price_cap():
    rt = BotRuntime(balance=1000)
    step(rt, _state(remaining_ms=120_000, projected_margin_bps=5), _settings())
    assert rt.position is None
    step(rt, _state(projected_margin_bps=0.1), _settings())
    assert rt.position is None
    step(rt, _state(up_ask=96), _settings(max_entry_cents=95))
    assert rt.position is None
    step(rt, _state(projected_margin_bps=-4), _settings())
    assert rt.position is not None and rt.position.side == "DOWN"


def test_one_entry_and_position_gate():
    s = _settings()
    rt = BotRuntime(balance=1000)
    step(rt, _state(), s)
    assert rt.position is not None
    held = rt.position
    step(rt, _state(wall_t=1_001_000, projected_margin_bps=-8), s)
    assert rt.position is held
    assert rt.entries[10] == 1


def test_profit_99_and_missing_ask_and_one_sided():
    s = _settings()
    rt = BotRuntime(balance=1000)
    step(rt, _state(), s)
    step(rt, _state(up_bid=98, up_ask=99), s)
    assert rt.position is not None
    closed = step(rt, _state(up_bid=99, up_ask=None, up_ask_size=None), s)
    assert closed is not None and closed.exit_class == "profit_target"
    assert closed.exit_price == 99
    rt2 = BotRuntime(balance=1000)
    step(rt2, _state(up_ask=None, up_bid=60), s)
    assert rt2.position is None
    step(rt2, _state(up_bid=None, up_ask=62), s)
    assert rt2.position is None


def test_cut_directions_timer_reset_and_confirm():
    s = _settings("twap_lockin_cut")
    rt = BotRuntime(balance=1000)
    step(rt, _state(projected_margin_bps=3), s)
    assert rt.position.side == "UP"
    step(rt, _state(wall_t=1_010_000, poly_t=1_000_000, projected_margin_bps=-0.6, up_bid=40), s)
    assert rt.adverse_since == 1_000_000
    step(rt, _state(wall_t=1_011_000, poly_t=1_001_000, projected_margin_bps=0.1, up_bid=40), s)
    assert rt.adverse_since is None and rt.position is not None
    step(rt, _state(wall_t=1_012_000, poly_t=1_002_000, projected_margin_bps=-0.7, up_bid=40), s)
    assert rt.adverse_since == 1_002_000
    closed = step(rt, _state(wall_t=1_014_100, poly_t=1_004_100, projected_margin_bps=-0.8, up_bid=40), s)
    assert closed is not None and closed.exit_class == "cut"
    rt = BotRuntime(balance=1000)
    step(rt, _state(projected_margin_bps=-3), s)
    assert rt.position.side == "DOWN"
    step(rt, _state(wall_t=1_020_000, poly_t=1_010_000, projected_margin_bps=0.6, down_bid=30), s)
    assert rt.adverse_since == 1_010_000


def test_bot_b_does_not_cut():
    s = _settings("twap_lockin")
    rt = BotRuntime(balance=1000)
    step(rt, _state(projected_margin_bps=4), s)
    step(rt, _state(wall_t=1_030_000, poly_t=1_020_000, projected_margin_bps=-5, up_bid=20), s)
    assert rt.position is not None and rt.adverse_since is None


def test_settlement_fees_and_sizing():
    s = _settings(order_usd=100)
    rt = BotRuntime(balance=1000)
    st = _state(up_ask=50, up_ask_size=1000, up_bid=48)
    step(rt, st, s)
    pos = rt.position
    assert pos is not None
    price = 0.50
    shares = 100 / price
    fee = taker_fee(shares, price)
    assert abs(pos.shares - shares) < 1e-9
    assert abs(pos.fee_paid - fee) < 1e-9
    win = settle(rt, s, "UP", 2_000_000)
    assert win.exit_class == "settlement_win"
    assert abs(win.pnl - (shares - shares * price - fee)) < 1e-6
    rt = BotRuntime(balance=1000)
    step(rt, st, s)
    loss = settle(rt, s, "DOWN", 2_000_000)
    assert loss.exit_class == "settlement_loss"
    assert loss.pnl < 0


def test_failed_cut_sell_clears_timer():
    s = _settings("twap_lockin_cut")
    rt = BotRuntime(balance=1000)
    step(rt, _state(projected_margin_bps=3), s)
    step(rt, _state(wall_t=1_040_000, poly_t=1_030_000, projected_margin_bps=-1, up_bid=None), s)
    assert rt.adverse_since == 1_030_000
    closed = step(rt, _state(wall_t=1_042_100, poly_t=1_032_100, projected_margin_bps=-1, up_bid=None), s)
    assert closed is None
    assert rt.adverse_since is None
    assert rt.position is not None


def test_known_bad_window_excluded():
    snaps = []
    end = 300_000.0
    for i in range(220):
        t = 1_000_000 + i * 1100
        snaps.append(Snap(
            t, 5971705, end, end - (t - 10_000), 60, 100.0, "official_twap60_carry",
            100.0, 1.0, 40, 42, 10, 10, 58, 60, 10, 10,
        ))
    # force fully-captured shape
    snaps[0] = Snap(snaps[0].t_ms, 5971705, 1_300_000, 295_000, 60, 100.0, "official_twap60_carry",
                    100.0, 1.0, 40, 42, 10, 10, 58, 60, 10, 10)
    last_t = snaps[0].t_ms + 280_000
    snaps[-1] = Snap(last_t, 5971705, 1_300_000, 5_000, 60, 100.0, "official_twap60_carry",
                     100.0, 1.0, 40, 42, 10, 10, 58, 60, 10, 10)
    q = assess_window(snaps, completed=True, outcome="UP", chainlink_in_window=200,
                      decisions_a=10, decisions_b=10, settings_ready=True)
    assert q.known_bad_range
    assert not q.eligible_a
    assert "EXCLUDED_DATA_QUALITY" in q.exclusion_reasons


def test_no_lookahead_oracle_receive_time():
    end = 1_000_000.0
    snap = Snap(900_000, 1, end, 100_000, 60, 100_000, "official_twap60_carry",
                None, None, 50, 52, 10, 10, 48, 50, 10, 10)
    early = [
        OraclePoint(800_000 + i * 1000, 800_000 + i * 1000, 100_000)
        for i in range(100)
    ]
    future = early + [OraclePoint(950_000, 950_000, 200_000)]
    a = TapeCursor([snap], early).states()[0]
    b = TapeCursor([snap], future).states()[0]
    assert a.projected_final == b.projected_final
    assert a.projected_margin_bps == b.projected_margin_bps
    later = Snap(960_000, 1, end, 40_000, 60, 100_000, "official_twap60_carry",
                 None, None, 50, 52, 10, 10, 48, 50, 10, 10)
    after = TapeCursor([snap, later], future).states()
    assert after[-1].projected_final != a.projected_final


def test_determinism_on_synthetic_tape():
    end = 1_000_000.0
    snaps = []
    t0 = 700_000.0
    for i in range(250):
        wall = t0 + i * 1200
        rem = end - (wall - 10_000)
        snaps.append(Snap(
            wall, 42, end, rem, 60, 100_000, "official_twap60_carry",
            100_050, 5, 60, 62, 100, 100, 38, 40, 100, 100,
        ))
    oracle = [OraclePoint(end - 80_000 + i * 1000, end - 80_000 + i * 1000, 100_050) for i in range(80)]
    from backend.research.replay.quality import WindowQuality
    q = WindowQuality(
        42, True, True, True, True, True, True, True, True, False, True, True, [], "UP",
        290_000, 5_000, 250, 1200,
    )
    tape = Tape(
        cutoff_ms=snaps[-1].t_ms, snaps={42: snaps}, oracle=oracle, outcomes={42: "UP"},
        settings={"B": _settings()}, quality={42: q}, prod={"B": []},
        balance_before={"B": []}, decision_windows={"B": {42}},
    )
    a = replay_bot(tape, "B")
    b = replay_bot(tape, "B")
    assert [(t.side, t.entry_price, round(t.pnl, 6), t.exit_class) for t in a] == [
        (t.side, t.entry_price, round(t.pnl, 6), t.exit_class) for t in b
    ]
    assert a and a[0].side == "UP"


def test_exchange_approx_blocks_margin():
    assert projected_margin(100.0, "exchange_approx", 101.0) is None
    assert projected_margin(100.0, "official_twap60_carry", 101.0) == 100.0
