from backend.engine.raven import RavenEngine, RavenSettings
from backend.config import ASSETS
from backend.engine.pipeline import Pipeline
from backend.engine.ptb15m import Ptb15mConnector


def _engine_with_ticks(count: int = 900) -> RavenEngine:
    engine = RavenEngine()
    for index in range(count):
        # A deterministic rising stream gives the indicators enough history
        # without opening a network feed or touching the trading pipeline.
        engine.on_tick(index * 1_000, 100_000.0 + index * 2.0, 1.0, "buy")
    return engine


def test_raven_warms_then_exposes_a_snapshot():
    engine = _engine_with_ticks()
    snapshot = engine.snapshot(900_000)

    assert snapshot["ready"] is True
    assert snapshot["bars"] == 60  # 15-second candles from 15 minutes of ticks
    assert snapshot["bias"] == 1
    assert snapshot["trend_total"] == 9
    assert 0 <= snapshot["quality"] <= 100


def test_raven_quote_agreement_is_visible_but_does_not_place_orders():
    engine = _engine_with_ticks()
    engine.on_quote({"up_bid": 60.0, "up_ask": 62.0, "down_bid": 38.0, "down_ask": 40.0})
    snapshot = engine.snapshot(900_000)

    assert snapshot["pm_direction"] == 1
    assert snapshot["pm_agree"] is True
    assert snapshot["signal"] in (None, 1, -1)


def test_raven_uses_official_pipeline_ptb_as_polymarket_reference():
    engine = _engine_with_ticks()
    engine.on_ptb(100_100.0, "official_twap60_carry")
    snapshot = engine.snapshot(900_000)

    assert snapshot["ptb"] == 100_100.0
    assert snapshot["ptb_source"] == "official_twap60_carry"
    assert snapshot["ptb_official"] is True
    assert snapshot["reference_mode"] == "PTB"
    assert snapshot["reference_price"] == 100_100.0


def test_raven_exposes_pine_style_state_without_treating_bias_as_diamond():
    engine = _engine_with_ticks()
    snapshot = engine.snapshot(900_000)

    assert snapshot["status"] in {"BIAS", "DIAMOND_CANDIDATE", "DIAMOND_PREVIEW", "LOCKED"}
    assert "diamond_preview" in snapshot
    assert "diamond_raw" in snapshot
    assert "locked_score" in snapshot
    assert "black_swan_prob" in snapshot
    assert "backtest" in snapshot
    if not snapshot["locked"]:
        assert snapshot["signal"] is None


def test_raven_locks_only_after_closed_bar_confirmation():
    # Relaxed settings make the deterministic stream suitable for a state-machine test.
    settings = RavenSettings(
        custom_window_end_min=15.0,
        quality_threshold=0.0,
        black_swan_max=95.0,
        use_htf_alignment=False,
        require_official_ptb=False,
        trend_agree_min=0,
        momentum_agree_min=0,
    )
    engine = RavenEngine(settings=settings)
    for index in range(1_800):
        engine.on_tick(index * 1_000, 100_000.0 + index * 2.0, 1.0, "buy")

    snapshot = engine.snapshot(1_800_000)

    assert snapshot["locked"] is True
    assert snapshot["status"] == "LOCKED"
    assert snapshot["locked_direction"] == 1
    assert snapshot["locked_at_ms"] is not None


def test_pipeline_holds_raven_ptb_for_three_five_minute_cycles():
    pipeline = Pipeline(ASSETS["BTC"])

    # First 15m block: the boundary PTB is accepted by Raven.
    pipeline.window_start_ms = 0
    pipeline._set_ptb(100_000.0, "official_twap60_carry")
    assert pipeline.raven.ptb == 100_000.0

    # The next two 5m PTBs belong to the same 15m market and must not replace it.
    for start, value in ((300_000, 100_100.0), (600_000, 100_200.0)):
        pipeline.ptb = None
        pipeline.ptb_source = None
        pipeline.window_start_ms = start
        pipeline._set_ptb(value, "official_twap60_carry")
    assert pipeline.raven.ptb == 100_000.0

    # At the next 15m boundary, Raven accepts the new PTB.
    pipeline.ptb = None
    pipeline.ptb_source = None
    pipeline.window_start_ms = 900_000
    pipeline._set_ptb(100_300.0, "official_twap60_carry")
    assert pipeline.raven.ptb == 100_300.0


def test_ptb15m_connector_latches_only_at_fifteen_minute_boundary():
    connector = Ptb15mConnector()

    assert connector.accept_5m_carry(0, 100.0, "official_twap60_carry") is True
    assert connector.state.source == "official_twap60_carry_15m"
    assert connector.accept_5m_carry(300_000, 101.0, "official_twap60_carry") is False
    assert connector.accept_5m_carry(600_000, 102.0, "official_twap60_carry") is False
    assert connector.state.value == 100.0
    assert connector.accept_5m_carry(900_000, 103.0, "official_twap60_carry") is True
    assert connector.state.value == 103.0
