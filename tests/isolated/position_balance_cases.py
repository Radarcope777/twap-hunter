"""Isolated V1/V2 share-balance tests. Stubs storage so live SQLite is never opened."""
from __future__ import annotations

import json
import math
import sys
from types import ModuleType, SimpleNamespace

_orig_storage = sys.modules.get("backend.storage")
_orig_accounts = sys.modules.get("backend.accounts")

_storage = ModuleType("backend.storage")
_saved_bots: list = []
_storage.storage = SimpleNamespace(
    save_account=lambda a: None,
    load_accounts=lambda: {},
    get_account=lambda i: {"id": i or "acc1", "encrypted_key": "stub"},
    save_real_bot=lambda b: _saved_bots.append(json.loads(json.dumps(b))),
    load_real_bots=lambda: {},
    load_bots=lambda: {},
)
sys.modules["backend.storage"] = _storage

_accounts = ModuleType("backend.accounts")
_accounts.decrypt_key = lambda x: "11" * 32
sys.modules["backend.accounts"] = _accounts

from backend.engine import clob_executor as ce  # noqa: E402
from backend.engine.real_bots import RealBotManager  # noqa: E402
from backend.engine import real_bots as _real_bots_mod  # noqa: E402

if hasattr(_real_bots_mod.storage, "_conn"):
    raise RuntimeError("isolation failed: live SQLite bound to real_bots")

# Keep the stub bound inside real_bots/clob_executor, but restore sys.modules
# so later test modules import the real storage/accounts packages.
if _orig_storage is None:
    sys.modules.pop("backend.storage", None)
else:
    sys.modules["backend.storage"] = _orig_storage
if _orig_accounts is None:
    sys.modules.pop("backend.accounts", None)
else:
    sys.modules["backend.accounts"] = _orig_accounts


class _Bal:
    def __init__(self, balance):
        self.balance = balance


_UNSET = object()


def _session(calls, balance=1_000_000, error=None, raw=_UNSET):
    class Client:
        def get_balance_allowance(self, **kwargs):
            calls.append(kwargs)
            if error is not None:
                raise error
            if raw is not _UNSET:
                return SimpleNamespace(balance=raw)
            return _Bal(balance)

    return SimpleNamespace(client=Client())


def test_v1_sends_conditional_and_id(monkeypatch):
    calls = []
    monkeypatch.setattr(ce, "get_session", lambda acc: _session(calls, 2_000_000))
    shares = ce.token_share_balance({}, "111", version="v1")
    assert shares == 2.0
    assert calls == [{"asset_type": "CONDITIONAL", "token_id": "111"}]


def test_v2_sends_conditional_v2_and_id(monkeypatch):
    calls = []
    monkeypatch.setattr(ce, "get_session", lambda acc: _session(calls, 1_000_000))
    shares = ce.token_share_balance({}, "9997199254740993", version="v2")
    assert shares == 1.0
    assert calls == [{"asset_type": "CONDITIONAL-V2", "token_id": "9997199254740993"}]


def test_collateral_cash_balance_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(ce, "get_session", lambda acc: _session(calls, 5_000_000))
    cash = ce.sync_clob_balance({})
    assert cash == 5.0
    assert calls[0]["asset_type"] == "COLLATERAL"
    assert "token_id" not in calls[0]


def test_cash_timeout_is_unread_not_zero(monkeypatch):
    monkeypatch.setattr(
        ce, "get_session",
        lambda acc: _session([], error=RuntimeError("timeout")),
    )
    try:
        ce.sync_clob_balance({})
        assert False, "expected BalanceReadError"
    except ce.BalanceReadError as exc:
        assert "failed" in str(exc).lower()


def test_cash_malformed_is_unread(monkeypatch):
    monkeypatch.setattr(
        ce, "get_session", lambda acc: _session([], raw="not-int"))
    try:
        ce.sync_clob_balance({})
        assert False, "expected BalanceReadError"
    except ce.BalanceReadError:
        pass


def test_cash_missing_field_is_unread(monkeypatch):
    monkeypatch.setattr(
        ce, "get_session",
        lambda acc: SimpleNamespace(client=SimpleNamespace(
            get_balance_allowance=lambda **k: object(),
        )),
    )
    try:
        ce.sync_clob_balance({})
        assert False, "expected BalanceReadError"
    except ce.BalanceReadError:
        pass


def test_cash_proven_zero(monkeypatch):
    monkeypatch.setattr(ce, "get_session", lambda acc: _session([], 0))
    assert ce.sync_clob_balance({}) == 0.0


def test_cash_dict_response(monkeypatch):
    class Client:
        def get_balance_allowance(self, **kwargs):
            return {"balance": "2500000"}

    monkeypatch.setattr(
        ce, "get_session",
        lambda acc: SimpleNamespace(client=Client(), balance=0),
    )
    assert ce.sync_clob_balance({}) == 2.5


def test_sync_keeps_last_cash_on_unread(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance",
        lambda account: (_ for _ in ()).throw(ce.BalanceReadError("timeout")),
    )
    bot = _bot()
    bot["balance"] = 20.45
    bot["last_exec_error"] = None
    assert _mgr()._sync_balance(bot, {"id": "acc1"}) is None
    assert bot["balance"] == 20.45
    assert bot["balance_unread"] is True
    assert "unread" in (bot["last_exec_error"] or "")


def test_sync_clears_deposit_banner(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 20.45)
    bot = _bot()
    bot["balance"] = 0.0
    bot["last_exec_error"] = (
        "CLOB balance $0.00 — deposit USDC at polymarket.com to place trades")
    assert _mgr()._sync_balance(bot, {"id": "acc1"}) == 20.45
    assert bot["balance"] == 20.45
    assert bot["balance_unread"] is False
    assert bot["last_exec_error"] is None


def test_sync_proven_zero_sets_deposit_banner(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 0.0)
    bot = _bot()
    bot["balance"] = 20.45
    bot["last_exec_error"] = None
    assert _mgr()._sync_balance(bot, {"id": "acc1"}) == 0.0
    assert bot["balance"] == 0.0
    assert "deposit USDC" in (bot["last_exec_error"] or "")


def test_buy_withheld_when_cash_unread(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("must not send")),
    )
    bot = _bot()
    bot["balance"] = 20.45
    bot["balance_unread"] = True
    bot["last_exec_error"] = "CLOB cash unread: timeout"
    assert _mgr().buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "test") is False
    assert bot["orders_placed"] == 0


def test_base_units_one_share(monkeypatch):
    monkeypatch.setattr(ce, "get_session", lambda acc: _session([], 1_000_000))
    assert ce.token_share_balance({}, "1", version="v1") == 1.0


def test_zero_distinct_from_errors(monkeypatch):
    monkeypatch.setattr(ce, "get_session", lambda acc: _session([], 0))
    assert ce.token_share_balance({}, "1", version="v1") == 0.0

    monkeypatch.setattr(
        ce, "get_session",
        lambda acc: _session([], error=RuntimeError("timeout")),
    )
    try:
        ce.token_share_balance({}, "1", version="v2")
        assert False, "expected BalanceReadError"
    except ce.BalanceReadError:
        pass

    monkeypatch.setattr(
        ce, "get_session", lambda acc: _session([], raw="not-int"))
    try:
        ce.token_share_balance({}, "1", version="v1")
        assert False, "expected BalanceReadError"
    except ce.BalanceReadError:
        pass


def test_unknown_version_does_not_query(monkeypatch):
    calls = []
    monkeypatch.setattr(ce, "get_session", lambda acc: _session(calls, 1))
    try:
        ce.token_share_balance({}, "1", version=None)
        assert False
    except ce.BalanceReadError as exc:
        assert "unknown" in str(exc)
    try:
        ce.token_share_balance({}, "1", version="v3")
        assert False
    except ce.BalanceReadError:
        pass
    assert calls == []


def _pipe(version="v2", window_id=10):
    return SimpleNamespace(
        poly_now_ms=lambda: 50_000,
        window_start_ms=0.0,
        window_end_ms=300_000.0,
        window_id=window_id,
        asset=SimpleNamespace(key="BTC"),
        polymarket=SimpleNamespace(market={
            "version": version,
            "up_token": "up-v2" if version == "v2" else "up-v1",
            "down_token": "down-v2" if version == "v2" else "down-v1",
        }),
    )


def _bot(position=None):
    return {
        "id": "r_test",
        "account_id": "acc1",
        "position": position,
        "balance": 1000.0,
        "start_balance": 1000.0,
        "stats": {"rounds": 0, "wins": 0, "losses": 0, "fees": 0.0},
        "trades": [],
        "activity_log": [],
        "orders_placed": 0,
        "status": "running",
    }


def _mgr():
    bm = SimpleNamespace(_quote=lambda pipe, side: (40.0, 100.0, 39.0))
    hub = SimpleNamespace(pipelines={})
    return RealBotManager(hub, bm)


def test_stale_v1_position_on_v2_market_reads_v1(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda account, token_id, *, version: (
            seen.append({"token_id": token_id, "version": version}) or 1.0
        ),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no sell in this test")),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 1000.0)
    pos = {
        "side": "UP", "shares": 1.0, "cost": 0.4, "fee_paid": 0,
        "window_id": 1, "token_id": "old-v1-id", "protocol_version": "v1",
    }
    bot = _bot(pos)
    _mgr()._close_stale(bot, {"id": "acc1"}, pos)
    assert seen == [{"token_id": "old-v1-id", "version": "v1"}]
    assert bot["position"] is pos


def test_stale_v2_position_on_v1_market_reads_v2(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda account, token_id, *, version: seen.append(version) or 0.0,
    )
    pos = {
        "side": "DOWN", "shares": 2.0, "cost": 1.0, "fee_paid": 0,
        "window_id": 1, "token_id": "v2-id", "protocol_version": "v2",
    }
    bot = _bot(pos)
    _mgr()._close_stale(bot, {"id": "acc1"}, pos)
    assert seen == ["v2"]
    assert bot["position"] is pos
    assert bot["stats"]["losses"] == 0
    assert bot["trades"] == []


def test_position_version_survives_json_roundtrip():
    pos = {
        "side": "UP", "shares": 1.5, "token_id": "abc",
        "protocol_version": "v2", "window_id": 9,
    }
    restored = json.loads(json.dumps({"position": pos}))["position"]
    assert restored["protocol_version"] == "v2"
    assert restored["token_id"] == "abc"


def test_balance_error_does_not_close_or_record_trade(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: (_ for _ in ()).throw(ce.BalanceReadError("timeout")),
    )
    pos = {
        "side": "UP", "shares": 3.0, "cost": 1.2, "fee_paid": 0,
        "window_id": 1, "token_id": "tid", "protocol_version": "v1",
    }
    bot = _bot(pos)
    _mgr()._close_stale(bot, {"id": "acc1"}, pos)
    assert bot["position"] is pos
    assert bot["stats"]["wins"] == 0 and bot["stats"]["losses"] == 0
    assert bot["trades"] == []
    assert "unread" in (bot.get("last_exec_error") or "")


def test_legacy_position_without_version_held_for_reconcile(monkeypatch):
    called = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: called.append(1) or 1.0,
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: called.append("order"),
    )
    pos = {
        "side": "UP", "shares": 1.0, "cost": 0.4, "fee_paid": 0,
        "window_id": 1, "token_id": "tid",
    }
    bot = _bot(pos)
    mgr = _mgr()
    mgr._close_stale(bot, {"id": "acc1"}, pos)
    pipe = _pipe("v2", window_id=1)
    assert mgr.sell(bot, pipe, "tp") is False
    assert bot["position"] is pos
    assert pos.get("needs_reconcile") is True
    assert called == []
    assert bot["stats"]["losses"] == 0


def test_buy_balance_error_after_post_blocks_reentry(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: {"orderID": "ord-1", "status": "MATCHED"},
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {"shares": 0, "usd": 0, "order_id": "ord-1", "tx_hashes": []},
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: (_ for _ in ()).throw(ce.BalanceReadError("net")),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 900.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.invalidate_snapshot", lambda account: None)

    mgr = _mgr()
    bot = _bot()
    rt = {"entries": {}}
    pipe = _pipe("v2", window_id=42)
    assert mgr.buy(bot, rt, pipe, "UP", 10.0, 90.0, "test") is True
    assert bot["position"] is not None
    assert bot["position"]["protocol_version"] == "v2"
    assert bot["position"]["token_id"] == "up-v2"
    assert bot["position"]["fill_uncertain"] is True
    assert rt["entries"][42] == 1
    assert mgr.buy(bot, rt, pipe, "UP", 10.0, 90.0, "test") is False
    assert bot["orders_placed"] == 1
    assert bot["position"]["needs_reconcile"] is True
    assert bot["position"]["order_id"] == "ord-1"


def _open_pos(**extra):
    pos = {
        "side": "UP", "shares": 3.0, "cost": 1.2, "fee_paid": 0,
        "window_id": 10, "token_id": "tid", "protocol_version": "v1",
        "order_id": "ord-keep",
    }
    pos.update(extra)
    return pos


def test_sell_confirmed_zero_does_not_place_order(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: 0.0,
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append((a, k)) or {"orderID": "x"},
    )
    pos = _open_pos()
    bot = _bot(pos)
    stats = dict(bot["stats"])
    assert _mgr().sell(bot, _pipe("v1", window_id=10), "tp") is False
    assert orders == []
    assert bot["position"] is pos
    assert pos.get("needs_reconcile") is True
    assert bot["stats"] == stats
    assert bot["trades"] == []
    assert bot["orders_placed"] == 0


def test_uncertain_zero_local_does_not_sell_min_lot(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: 2.0,
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append(k.get("amount") or (a[3] if len(a) > 3 else None)),
    )
    pos = _open_pos(shares=0.0, fill_uncertain=True)
    bot = _bot(pos)
    mgr = _mgr()
    pipe = _pipe("v1", window_id=10)
    assert mgr.sell(bot, pipe, "tp") is False
    mgr._close_stale(bot, {"id": "acc1"}, pos)
    assert orders == []
    assert bot["position"] is pos
    assert bot["trades"] == []
    assert bot["stats"]["wins"] == 0 and bot["stats"]["losses"] == 0


def test_reconcile_flags_block_sell_flatten_and_rebuy(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: 2.0,
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order"),
    )
    mgr = _mgr()
    pipe = _pipe("v2", window_id=10)
    for flag in ("fill_uncertain", "needs_reconcile"):
        pos = _open_pos(**{flag: True})
        bot = _bot(pos)
        rt = {"entries": {}}
        assert mgr.sell(bot, pipe, "tp") is False
        mgr._close_stale(bot, {"id": "acc1"}, pos)
        assert mgr.buy(bot, rt, pipe, "UP", 10.0, 90.0, "test") is False
        assert bot["position"] is pos
        assert pos.get(flag) is True
        assert pos.get("needs_reconcile") is True
        assert bot["stats"]["wins"] == 0 and bot["stats"]["losses"] == 0
        assert bot["trades"] == []
    assert orders == []


def test_reconcile_flags_survive_saved_state_restore(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: 5.0,
    )
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order"),
    )
    saved = json.loads(json.dumps(_bot(_open_pos(
        fill_uncertain=True, needs_reconcile=True, protocol_version="v2",
    ))))
    bot = saved
    pos = bot["position"]
    mgr = _mgr()
    pipe = _pipe("v2", window_id=10)
    assert pos["fill_uncertain"] is True
    assert pos["needs_reconcile"] is True
    assert pos["order_id"] == "ord-keep"
    assert mgr.sell(bot, pipe, "tp") is False
    mgr._close_stale(bot, {"id": "acc1"}, pos)
    assert mgr.buy(bot, {"entries": {}}, pipe, "UP", 10.0, 90.0, "x") is False
    assert bot["position"] is pos
    assert pos["fill_uncertain"] is True
    assert pos["needs_reconcile"] is True
    assert orders == []


def test_buy_share_read_error_kept_when_resolve_fill_positive(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: {"orderID": "ord-2", "status": "MATCHED"},
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {
            "shares": 4.0, "usd": 1.6, "order_id": "ord-2",
            "price": 0.4, "price_cents": 40.0, "fee": 0, "tx_hashes": ["0xabc"],
        },
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: (_ for _ in ()).throw(ce.BalanceReadError("net")),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 900.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.invalidate_snapshot", lambda account: None)

    mgr = _mgr()
    bot = _bot()
    rt = {"entries": {}}
    pipe = _pipe("v1", window_id=7)
    assert mgr.buy(bot, rt, pipe, "DOWN", 10.0, 90.0, "test") is True
    pos = bot["position"]
    assert pos["order_id"] == "ord-2"
    assert pos["tx_hashes"] == ["0xabc"]
    assert pos["shares"] == 4.0
    assert pos["fill_uncertain"] is True
    assert pos["needs_reconcile"] is True
    assert pos["protocol_version"] == "v1"
    assert pos["token_id"] == "down-v1"
    assert "unread" in (bot.get("last_exec_error") or "")
    assert mgr.buy(bot, rt, pipe, "DOWN", 10.0, 90.0, "again") is False
    assert bot["orders_placed"] == 1
    assert bot["trades"] == []


def test_invalid_balance_values_rejected(monkeypatch):
    def _run(raw):
        monkeypatch.setattr(ce, "get_session", lambda acc: _session([], raw=raw))
        return ce.token_share_balance({}, "1", version="v1")

    for raw in (True, False, 1.5, 1.0, -1, None, math.nan, math.inf,
                "1.0", "abc", "1e6", "-1", "", " 1.5 "):
        try:
            _run(raw)
            assert False, f"expected BalanceReadError for {raw!r}"
        except ce.BalanceReadError:
            pass

    missing = SimpleNamespace()
    monkeypatch.setattr(
        ce, "get_session",
        lambda acc: SimpleNamespace(
            client=SimpleNamespace(
                get_balance_allowance=lambda **k: missing,
            ),
        ),
    )
    try:
        ce.token_share_balance({}, "1", version="v2")
        assert False
    except ce.BalanceReadError:
        pass

    monkeypatch.setattr(ce, "get_session", lambda acc: _session([], 0))
    assert ce.token_share_balance({}, "1", version="v1") == 0.0
    monkeypatch.setattr(ce, "get_session", lambda acc: _session([], raw="1000000"))
    assert ce.token_share_balance({}, "1", version="v2") == 1.0
    monkeypatch.setattr(ce, "get_session", lambda acc: _session([], 2_000_000))
    assert ce.token_share_balance({}, "1", version="v1") == 2.0


def test_refusals_do_not_change_trade_counters(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: (_ for _ in ()).throw(ce.BalanceReadError("down")),
    )
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order"),
    )
    pos = _open_pos()
    bot = _bot(pos)
    mgr = _mgr()
    pipe = _pipe("v1", window_id=10)
    assert mgr.sell(bot, pipe, "tp") is False
    mgr._close_stale(bot, {"id": "acc1"}, pos)
    assert bot["position"] is pos
    assert pos.get("needs_reconcile") is True
    assert bot["stats"] == {"rounds": 0, "wins": 0, "losses": 0, "fees": 0.0}
    assert bot["trades"] == []
    assert orders == []
