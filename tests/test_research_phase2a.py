"""Phase 2A: research recording must not change TWAP paper decisions."""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="twap-research-test-")
os.environ["DATA_DIR"] = _TMP

from backend.engine.bots import (  # noqa: E402
    BotManager,
    _eval_twap_lockin,
    _eval_twap_lockin_cut,
    _fresh_rt,
)
from backend.engine.metrics import RollingTwap  # noqa: E402
from backend.engine.research import recorder  # noqa: E402
from backend.storage import storage  # noqa: E402


class _Asset:
    key = "BTC"


class _Poly:
    market = {
        "slug": "btc-updown-5m-100",
        "condition_id": "c1",
        "up_token": "u",
        "down_token": "d",
    }


class Pipe:
    def __init__(self) -> None:
        self.window_id = 100
        self.window_start_ms = 0.0
        self.window_end_ms = 300_000.0
        self.ptb = 100_000.0
        self.ptb_source = "official_twap60_carry"
        self.chainlink = 100_050.0
        self.twap30 = None
        self.twap60 = 100_010.0
        self._twap60_src_ms = 200_000.0
        self.rolling = RollingTwap(60)
        for i in range(70):
            self.rolling.add(170_000.0 + i * 1000.0, 100_050.0)
        self.asset = _Asset()
        self.polymarket = _Poly()
        self.last_quote = {
            "t": 240_000.0,
            "up_bid": 60.0, "up_ask": 62.0,
            "up_bid_size": 500.0, "up_ask_size": 500.0,
            "down_bid": 38.0, "down_ask": 40.0,
            "down_bid_size": 500.0, "down_ask_size": 500.0,
        }
        self.round_results = {}
        self._now = 240_000.0

    def poly_now_ms(self) -> float:
        return self._now


class Mgr:
    def __init__(self) -> None:
        self._dirty: set[str] = set()
        self.bots: dict[str, dict] = {}
        self._rt: dict[str, dict] = {}
        self._hub = type("H", (), {"pipelines": {}})()

    _quote = BotManager._quote
    _log = BotManager._log
    _bump_winloss = BotManager._bump_winloss
    buy = BotManager.buy
    sell = BotManager.sell


def _bot(strategy: str, **params) -> dict:
    base = {
        "entry_window_s": 90,
        "min_margin_bps": 0.3,
        "max_entry_cents": 95,
        "order_usd": 100,
    }
    base.update(params)
    return {
        "id": "b_test_" + strategy,
        "name": "t",
        "asset": "BTC",
        "strategy": strategy,
        "status": "running",
        "params": base,
        "balance": 1000.0,
        "position": None,
        "stats": {"fees": 0.0, "wins": 0, "losses": 0, "rounds": 0},
        "trades": [],
    }


def _fingerprint(bot: dict) -> tuple:
    pos = bot["position"]
    pos_key = None if pos is None else (
        pos["side"], round(pos["shares"], 6), round(pos["cost"], 6), pos["entry_cents"],
    )
    actions = tuple(t["action"] for t in bot["trades"])
    return (round(bot["balance"], 6), pos_key, actions, bot["stats"]["wins"], bot["stats"]["losses"])


def test_lockin_identical_when_research_fails():
    pipe = Pipe()
    mgr = Mgr()
    recorder.force_fail = True
    bot_fail = _bot("twap_lockin", min_margin_bps=2.5, max_entry_cents=90, entry_window_s=60)
    rt_fail = _fresh_rt()
    _eval_twap_lockin(mgr, bot_fail, rt_fail, pipe, pipe.poly_now_ms())

    recorder.force_fail = False
    recorder._queues["event"].clear()
    bot_ok = _bot("twap_lockin", min_margin_bps=2.5, max_entry_cents=90, entry_window_s=60)
    rt_ok = _fresh_rt()
    _eval_twap_lockin(mgr, bot_ok, rt_ok, pipe, pipe.poly_now_ms())
    assert _fingerprint(bot_fail) == _fingerprint(bot_ok)
    assert bot_ok["position"]["side"] == "UP"
    assert bot_ok["trades"][0]["action"] == "BUY"


def test_cut_identical_and_timer_clears_after_failed_sell():
    pipe = Pipe()
    pipe.last_quote["up_bid"] = 0
    mgr = Mgr()
    kwargs = dict(
        min_margin_bps=0.3, max_entry_cents=95,
        cut_margin_bps=0.5, cut_confirm_s=2, entry_window_s=90,
    )

    def run(force: bool) -> tuple:
        recorder.force_fail = force
        bot = _bot("twap_lockin_cut", **kwargs)
        bot["position"] = {
            "side": "UP", "shares": 10.0, "cost": 6.0, "entry_cents": 60.0,
            "fee_paid": 0.1, "window_id": pipe.window_id, "entry_t": 1.0, "asset": "BTC",
        }
        rt = _fresh_rt()
        now = pipe.poly_now_ms()
        _eval_twap_lockin_cut(mgr, bot, rt, pipe, now)
        started = rt["adverse_since"]
        _eval_twap_lockin_cut(mgr, bot, rt, pipe, now + 2500)
        return _fingerprint(bot), started, rt["adverse_since"]

    failed, started_f, cleared_f = run(True)
    ok, started_o, cleared_o = run(False)
    assert failed == ok
    assert started_f == started_o
    assert cleared_f is None and cleared_o is None
    assert ok[1] is not None  # position still open: sell had no bid


def test_research_rows_and_no_lookahead():
    recorder.force_fail = False
    for q in recorder._queues.values():
        q.clear()
    pipe = Pipe()
    pipe.round_results[pipe.window_id] = {"outcome": "DOWN", "source": "market"}
    bot = _bot("twap_lockin", min_margin_bps=2.5, max_entry_cents=90, entry_window_s=90)
    mgr = Mgr()
    mgr.bots[bot["id"]] = bot
    mgr._rt[bot["id"]] = _fresh_rt()
    mgr._hub.pipelines["BTC"] = pipe
    recorder._last_snap_ms = 0
    recorder._last_decision_ms.clear()
    recorder.tick(mgr)
    decisions = storage._conn.execute(
        "SELECT bot_id, window_id, settings_version_id, projected_margin_bps, "
        "candidate_side, entry_allowed FROM research_bot_decisions"
    ).fetchall()
    snaps = storage._conn.execute(
        "SELECT asset, window_id, ptb, up_ask FROM research_market_snapshots"
    ).fetchall()
    versions = storage._conn.execute(
        "SELECT bot_id, params_hash FROM research_settings_versions"
    ).fetchall()
    assert decisions and decisions[0][0] == bot["id"]
    assert decisions[0][1] == pipe.window_id
    assert decisions[0][2] is not None
    assert decisions[0][3] is not None and decisions[0][3] > 0
    assert decisions[0][4] == "UP"  # future official DOWN must not flip the candidate
    assert snaps and snaps[0][0] == "BTC" and snaps[0][2] == pipe.ptb
    assert versions and versions[0][0] == bot["id"]

    recorder.oracle("BTC", "chainlink", 240_000.0, 240_100.0, 100_050.0)
    recorder.oracle("XRP", "chainlink", 240_000.0, 240_100.0, 1.0)
    recorder.flush()
    n = storage._conn.execute(
        "SELECT COUNT(*) FROM research_oracle_samples"
    ).fetchone()[0]
    xrp = storage._conn.execute(
        "SELECT COUNT(*) FROM research_oracle_samples WHERE asset='XRP'"
    ).fetchone()[0]
    assert n == 1 and xrp == 0


def test_xrp_events_are_not_stored():
    recorder.force_fail = False
    recorder._queues["event"].clear()
    bot = _bot("twap_lockin")
    bot["asset"] = "XRP"
    recorder.event(bot, "ENTRY_ATTEMPT", window_id=1)
    assert len(recorder._queues["event"]) == 0
