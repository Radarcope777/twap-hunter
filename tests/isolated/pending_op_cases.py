"""Isolated pending-op / crash-safety tests. Does not open live SQLite or send orders."""
from __future__ import annotations

import json
import sys
import time
from types import ModuleType, SimpleNamespace

_orig_storage = sys.modules.get("backend.storage")
_orig_accounts = sys.modules.get("backend.accounts")

_STORE: dict = {}
_storage = ModuleType("backend.storage")


def _save_bot(b):
    _STORE[b["id"]] = json.loads(json.dumps(b))


_storage.storage = SimpleNamespace(
    save_account=lambda a: None,
    load_accounts=lambda: {},
    get_account=lambda i: {"id": i or "acc1", "encrypted_key": "stub"},
    save_real_bot=_save_bot,
    load_real_bots=lambda: {k: json.loads(json.dumps(v)) for k, v in _STORE.items()},
    load_bots=lambda: {},
)
sys.modules["backend.storage"] = _storage

_accounts = ModuleType("backend.accounts")
_accounts.decrypt_key = lambda x: "11" * 32
sys.modules["backend.accounts"] = _accounts

from backend.engine.real_bots import RealBotManager  # noqa: E402
from backend.engine import real_bots as _real_bots_mod  # noqa: E402
from backend.engine import clob_executor as ce  # noqa: E402

if hasattr(_real_bots_mod.storage, "_conn"):
    raise RuntimeError("isolation failed: live SQLite bound to real_bots")

if _orig_storage is None:
    sys.modules.pop("backend.storage", None)
else:
    sys.modules["backend.storage"] = _orig_storage
if _orig_accounts is None:
    sys.modules.pop("backend.accounts", None)
else:
    sys.modules["backend.accounts"] = _orig_accounts


def _pipe(version="v2", window_id=10, now=50_000):
    return SimpleNamespace(
        poly_now_ms=lambda: now,
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


def _bot(position=None, **extra):
    body = {
        "id": extra.pop("id", "r_test"),
        "account_id": "acc1",
        "position": position,
        "balance": 1000.0,
        "start_balance": 1000.0,
        "stats": {"rounds": 0, "wins": 0, "losses": 0, "fees": 0.0},
        "trades": [],
        "activity_log": [],
        "orders_placed": 0,
        "status": "running",
        "pending_op": None,
    }
    body.update(extra)
    return body


def _mgr(quote=(40.0, 100.0, 39.0)):
    bm = SimpleNamespace(_quote=lambda pipe, side: quote)
    hub = SimpleNamespace(pipelines={})
    return RealBotManager(hub, bm)


def _open_pos(**extra):
    pos = {
        "side": "UP", "shares": 3.0, "cost": 1.2, "fee_paid": 0,
        "window_id": 10, "token_id": "tid", "protocol_version": "v1",
        "order_id": "ord-keep",
    }
    pos.update(extra)
    return pos


def test_venv_polymarket_client_is_0_13_0():
    import importlib.metadata as m
    import sys
    exe = sys.executable.replace("\\", "/").lower()
    assert ".venv/" in exe or exe.endswith(".venv/scripts/python.exe")
    assert m.version("polymarket-client") == "0.13.0"


def test_local_error_does_not_call_sdk(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order") or {},
    )
    mgr = _mgr(quote=(None, None, None))
    bot = _bot()
    assert mgr.buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "x") is False
    assert orders == []
    assert bot.get("pending_op") is None


def test_intent_save_failure_does_not_call_sdk(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order") or {},
    )

    def boom(bot):
        raise RuntimeError("disk full")

    monkeypatch.setattr("backend.engine.real_bots.storage.save_real_bot", boom)
    mgr = _mgr()
    bot = _bot()
    assert mgr.buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "x") is False
    assert orders == []
    assert bot.get("pending_op") is None
    assert "not sent" in (bot.get("last_exec_error") or "")


def test_intent_persisted_before_send(monkeypatch):
    events = []

    def save(b):
        events.append(("save", (b.get("pending_op") or {}).get("state")))

    def place(*a, **k):
        events.append(("place",))
        raise TimeoutError("lost")

    monkeypatch.setattr("backend.engine.real_bots.storage.save_real_bot", save)
    monkeypatch.setattr("backend.engine.real_bots.place_market_order", place)
    mgr = _mgr()
    bot = _bot()
    assert mgr.buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "x") is False
    assert events[0][0] == "save"
    assert events[0][1] == "intent"
    assert events[1] == ("place",)
    assert bot["pending_op"]["state"] == "uncertain"
    assert bot["pending_op"]["needs_reconcile"] is True
    assert bot["position"] is None


def test_lost_buy_response_blocks_next_tick(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order") or (_ for _ in ()).throw(
            ConnectionError("reset")),
    )
    mgr = _mgr()
    bot = _bot()
    rt = {"entries": {}}
    pipe = _pipe()
    assert mgr.buy(bot, rt, pipe, "UP", 10.0, 90.0, "x") is False
    assert orders == ["order"]
    assert bot["position"] is None
    assert bot["pending_op"]["needs_reconcile"] is True
    assert mgr.buy(bot, rt, pipe, "UP", 10.0, 90.0, "again") is False
    assert orders == ["order"]


def test_post_then_fill_read_failure_keeps_ids(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: {
            "order_id": "ord-9", "orderID": "ord-9",
            "status": "matched", "ok": True,
            "trade_ids": ["tr-a"], "making_amount": "1.6", "taking_amount": "4",
            "transactions_hashes": ["0xabc"],
        },
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("clob down")),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 900.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.invalidate_snapshot", lambda account: None)
    orders = []
    orig_place = None

    def place(*a, **k):
        orders.append("order")
        return orig_place(*a, **k) if False else {
            "order_id": "ord-9", "status": "matched", "ok": True,
            "trade_ids": ["tr-a"], "making_amount": 0, "taking_amount": None,
            "transactions_hashes": ["0xabc"],
        }

    monkeypatch.setattr("backend.engine.real_bots.place_market_order", place)
    mgr = _mgr()
    bot = _bot()
    rt = {"entries": {}}
    assert mgr.buy(bot, rt, _pipe(), "UP", 10.0, 90.0, "x") is False
    op = bot["pending_op"]
    assert op["order_id"] == "ord-9"
    assert op["trade_ids"] == ["tr-a"]
    assert op["making_amount"] == 0
    assert op["taking_amount"] is None
    assert op["needs_reconcile"] is True
    assert bot["position"] is None
    assert mgr.buy(bot, rt, _pipe(), "UP", 10.0, 90.0, "again") is False
    assert orders == ["order"]


def test_sell_exception_after_send_keeps_position(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 3.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("after send")),
    )
    pos = _open_pos()
    bot = _bot(pos)
    stats = dict(bot["stats"])
    assert _mgr().sell(bot, _pipe("v1", window_id=10), "tp") is False
    assert bot["position"] is pos
    assert pos.get("needs_reconcile") is True
    assert bot["pending_op"]["needs_reconcile"] is True
    assert bot["stats"] == stats
    assert bot["trades"] == []


def test_flatten_no_book_or_cash_delta_is_not_settle(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 3.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order"),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 1090.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.fetch_official_outcome",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no gamma during wait")),
    )
    pos = _open_pos()
    bot = _bot(pos, asset="BTC")
    balance = bot["balance"]
    pipe = _pipe()
    pipe.round_results = {}
    _mgr()._close_stale(bot, {"id": "acc1"}, pos, pipe)
    assert orders == []
    assert bot["position"] is pos
    assert bot["balance"] == balance
    assert bot["stats"]["wins"] == 0 and bot["stats"]["losses"] == 0
    assert bot["trades"] == []
    assert bot.get("pending_op") is None
    acts = " ".join(e.get("msg") or "" for e in bot.get("activity_log") or [])
    assert "SETTLE" not in acts
    assert "waiting for resolution" in acts


def test_restored_unfinished_buy_without_position_blocks(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order"),
    )
    saved = json.loads(json.dumps(_bot(pending_op={
        "id": "op_restored", "bot_id": "r_test", "account_id": "acc1",
        "intent": "buy", "side": "BUY", "token_id": "up-v2",
        "protocol_version": "v2", "window_id": 10,
        "requested_amount": 10.0, "price_limit": 0.4,
        "state": "uncertain", "needs_reconcile": True,
        "order_id": None, "trade_ids": None,
        "making_amount": None, "taking_amount": None,
    })))
    mgr = _mgr()
    pipe = _pipe()
    assert mgr.buy(saved, {"entries": {}}, pipe, "UP", 10.0, 90.0, "x") is False
    mgr._close_stale(saved, {"id": "acc1"}, _open_pos())
    assert orders == []
    assert saved["position"] is None


def test_start_and_window_change_do_not_lift_lock(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order"),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 1000.0)
    bot = _bot(pending_op={
        "id": "op_lock", "bot_id": "r_test", "account_id": "acc1",
        "intent": "buy", "side": "BUY", "state": "uncertain",
        "needs_reconcile": True, "order_id": None,
    })
    mgr = _mgr()
    mgr.bots[bot["id"]] = bot
    mgr._sync_balance(bot, {"id": "acc1"})
    assert mgr.buy(bot, {"entries": {}}, _pipe(window_id=10), "UP", 10.0, 90.0, "x") is False
    assert mgr.buy(bot, {"entries": {}}, _pipe(window_id=99), "UP", 10.0, 90.0, "x") is False
    assert orders == []
    assert bot["pending_op"]["needs_reconcile"] is True


def _confirmed_fill(**extra):
    body = {
        "state": "confirmed_fill", "final": True, "filled": True,
        "amounts_ready": True, "shares": 2.5, "usd": 1.0, "price": 0.4,
        "price_cents": 40.0, "fee": 0, "fee_known": True,
        "order_id": "ord-ok", "trade_ids": ["tr-1"], "tx_hashes": ["0x1"],
        "error": None,
    }
    body.update(extra)
    return body


def _stub_fill_ok(monkeypatch, fill=None):
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: fill if fill is not None else _confirmed_fill())
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 3.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 900.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.invalidate_snapshot", lambda account: None)


def test_successful_buy_uses_fill_not_quote_and_clears_pending(monkeypatch):
    events = []

    def save(b):
        events.append(("save", (b.get("pending_op") or {}).get("state"),
                       (b.get("pending_op") or {}).get("order_id"),
                       (b.get("pending_op") or {}).get("trade_ids")))

    def place(*a, **k):
        events.append(("place",))
        return {
            "ok": True, "order_id": "ord-ok", "status": "matched",
            "trade_ids": ["tr-1"], "making_amount": "1.0", "taking_amount": "2.5",
            "transactions_hashes": ["0x1"],
        }

    monkeypatch.setattr("backend.engine.real_bots.storage.save_real_bot", save)
    monkeypatch.setattr("backend.engine.real_bots.place_market_order", place)
    _stub_fill_ok(monkeypatch)
    mgr = _mgr()
    bot = _bot()
    rt = {"entries": {}}
    assert mgr.buy(bot, rt, _pipe(), "UP", 10.0, 90.0, "x") is True
    assert events[0][0] == "save" and events[0][1] == "intent"
    assert ("place",) in events
    assert events.index(("place",)) > 0
    pos = bot["position"]
    assert pos is not None
    assert pos["order_id"] == "ord-ok"
    assert pos["shares"] == 2.5
    assert pos["cost"] == 1.0
    assert pos["entry_cents"] == 40.0
    assert pos["shares"] != 10.0 / 0.4
    assert pos["trade_ids"] == ["tr-1"]
    assert bot["pending_op"] is None
    assert rt["entries"][10] == 1


def test_trade_ids_absent_are_not_invented(monkeypatch):
    def place(*a, **k):
        return {"ok": True, "order_id": "ord-ok", "status": "matched"}

    monkeypatch.setattr("backend.engine.real_bots.place_market_order", place)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop after record")),
    )
    mgr = _mgr()
    bot = _bot()
    assert mgr.buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "x") is False
    op = bot["pending_op"]
    assert op["order_id"] == "ord-ok"
    assert op["trade_ids"] is None


def test_second_buy_same_window_not_sent_after_fill(monkeypatch):
    orders = []

    def place(*a, **k):
        orders.append("order")
        return {
            "ok": True, "order_id": "ord-ok", "status": "matched",
            "trade_ids": ["tr-1"], "making_amount": "1.0", "taking_amount": "2.5",
        }

    monkeypatch.setattr("backend.engine.real_bots.place_market_order", place)
    _stub_fill_ok(monkeypatch)
    mgr = _mgr()
    bot = _bot()
    rt = {"entries": {}}
    pipe = _pipe(window_id=10)
    assert mgr.buy(bot, rt, pipe, "UP", 10.0, 90.0, "x") is True
    assert mgr.buy(bot, rt, pipe, "UP", 10.0, 90.0, "again") is False
    assert orders == ["order"]


def test_uncertain_buy_blocks_sell_and_flatten(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order") or (_ for _ in ()).throw(
            TimeoutError("lost")),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 3.0)
    mgr = _mgr()
    bot = _bot()
    rt = {"entries": {}}
    assert mgr.buy(bot, rt, _pipe(), "UP", 10.0, 90.0, "x") is False
    bot["position"] = _open_pos()
    assert mgr.sell(bot, _pipe("v1", window_id=10), "tp") is False
    mgr._close_stale(bot, {"id": "acc1"}, bot["position"])
    assert orders == ["order"]


def test_successful_sell_closes_position(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: {
            "ok": True, "order_id": "ord-s", "status": "matched",
            "trade_ids": ["tr-s"], "making_amount": "3", "taking_amount": "1.2",
        },
    )
    _stub_fill_ok(monkeypatch, _confirmed_fill(
        shares=3.0, usd=1.2, order_id="ord-s", trade_ids=["tr-s"]))
    pos = _open_pos()
    bot = _bot(pos)
    assert _mgr().sell(bot, _pipe("v1", window_id=10), "tp") is True
    assert bot["position"] is None
    assert bot["pending_op"] is None


def test_rejected_sell_does_not_close_or_settle(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 3.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: {
            "ok": False, "rejected": True, "code": "fok_not_filled",
            "message": "FOK not filled", "order_id": None,
        },
    )
    pos = _open_pos()
    bot = _bot(pos)
    stats = dict(bot["stats"])
    mgr = _mgr()
    assert mgr.sell(bot, _pipe("v1", window_id=10), "tp") is False
    assert bot["position"] is pos
    assert pos.get("needs_reconcile") is not True
    assert bot["pending_op"] is None
    assert not mgr._op_blocks(bot)
    assert bot["stats"] == stats
    assert bot["trades"] == []
    acts = " ".join(e.get("msg") or "" for e in bot.get("activity_log") or [])
    assert "SETTLE" not in acts


def test_explicit_reject_is_not_a_fill_or_fake_trade(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: {
            "ok": False, "rejected": True, "code": "fok_not_filled",
            "message": "FOK not filled", "order_id": None,
            "making_amount": None, "taking_amount": None, "trade_ids": [],
        },
    )
    mgr = _mgr()
    bot = _bot()
    rt = {"entries": {}}
    assert mgr.buy(bot, rt, _pipe(), "UP", 10.0, 90.0, "x") is False
    assert bot["position"] is None
    assert bot["trades"] == []
    assert bot["stats"]["wins"] == 0
    assert "rejected" in (bot.get("last_exec_error") or "").lower()
    assert not mgr._op_blocks(bot)


def _posted_buy_op(**extra):
    op = {
        "id": "op_recon", "bot_id": "r_test", "account_id": "acc1",
        "intent": "buy", "side": "BUY", "market_side": "UP",
        "token_id": "up-v2", "protocol_version": "v2", "window_id": 10,
        "requested_amount": 5.0, "price_limit": 0.4,
        "state": "uncertain", "needs_reconcile": True,
        "order_id": "ord-ok", "trade_ids": ["tr-1"],
        "making_amount": "1.0", "taking_amount": "2.5",
        "transactions_hashes": ["0x1"],
    }
    op.update(extra)
    return op


def test_reconcile_matched_buy_after_restart_restores_position(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order") or (_ for _ in ()).throw(
            AssertionError("retry POST")),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: _confirmed_fill())
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 999.0)
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert orders == []
    pos = bot["position"]
    assert pos is not None
    assert pos["side"] == "UP"
    assert pos["shares"] == 2.5
    assert pos["cost"] == 1.0
    assert pos["order_id"] == "ord-ok"
    assert pos["trade_ids"] == ["tr-1"]
    assert pos["shares"] != 5.0
    assert bot["pending_op"] is None
    assert not mgr._op_blocks(bot)


def test_reconcile_rejected_order_clears_pending_without_position(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retry POST")),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {
            "state": "confirmed_empty", "final": True, "filled": False,
            "amounts_ready": True, "shares": 0, "usd": 0, "price": None,
            "price_cents": None, "fee": None, "fee_known": False,
            "order_id": "ord-ok", "trade_ids": [], "tx_hashes": [],
            "error": "order cancelled",
        },
    )
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["position"] is None
    assert bot["trades"] == []
    assert bot["pending_op"] is None
    assert not mgr._op_blocks(bot)


def test_reconcile_live_order_stays_blocked(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retry POST")),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {
            "state": "uncertain", "final": False, "filled": False,
            "amounts_ready": False, "shares": 0, "usd": None, "error":
            "order live with no matched size", "order_id": "ord-ok",
            "trade_ids": [], "tx_hashes": [],
        },
    )
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["position"] is None
    assert mgr._op_blocks(bot)
    assert bot["pending_op"]["needs_reconcile"] is True
    assert mgr.buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "x") is False


def test_reconcile_partial_fill_uses_filled_size_not_requested(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: _confirmed_fill(shares=1.25, usd=0.5, price=0.4,
                                        price_cents=40.0))
    bot = _bot(pending_op=_posted_buy_op(requested_amount=5.0, taking_amount="5.0"))
    _mgr()._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["position"]["shares"] == 1.25
    assert bot["position"]["shares"] != 5.0
    assert bot["position"]["cost"] == 0.5
    assert bot["pending_op"] is None


def test_reconcile_unknown_order_stays_needs_reconcile(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {
            "state": "uncertain", "final": False, "filled": False,
            "amounts_ready": False, "shares": None, "usd": None,
            "error": "get_order failed: TimeoutError", "order_id": "ord-ok",
            "trade_ids": ["tr-1"], "tx_hashes": [],
        },
    )
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["position"] is None
    assert mgr._op_blocks(bot)
    assert bot["pending_op"]["needs_reconcile"] is True


def test_reconcile_without_order_id_does_not_use_balance(monkeypatch):
    balances = []
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance",
        lambda account: balances.append("cash") or 50.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: balances.append("shares") or 9.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no id lookup")),
    )
    bot = _bot(pending_op=_posted_buy_op(order_id=None, trade_ids=None))
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert balances == []
    assert bot["position"] is None
    assert mgr._op_blocks(bot)
    assert "no order_id" in (bot["pending_op"].get("error") or "")


def test_reconcile_does_not_duplicate_buy(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order") or {
            "ok": True, "order_id": "ord-2", "status": "matched",
            "trade_ids": ["tr-2"], "making_amount": "1.0", "taking_amount": "2.5",
        },
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: _confirmed_fill())
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 3.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 900.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.invalidate_snapshot", lambda account: None)
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["pending_op"] is None
    assert mgr.buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "again") is False
    assert orders == []


def test_boot_reconciles_pending_before_trading(monkeypatch):
    import asyncio
    seen = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: seen.append("order") or (_ for _ in ()).throw(
            AssertionError("retry POST")),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: seen.append("resolve") or _confirmed_fill())
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 900.0)

    async def retain(asset, bot_id):
        seen.append("retain")
        return SimpleNamespace()

    bot = _bot(pending_op=_posted_buy_op(), status="running", asset="BTC")
    mgr = _mgr()
    mgr.bots[bot["id"]] = bot
    mgr._hub.retain = retain
    asyncio.run(mgr._boot_running())
    assert seen[0] == "resolve"
    assert "order" not in seen
    assert bot["position"] is not None
    assert bot["pending_op"] is None


def _buy_with_place(monkeypatch, place):
    monkeypatch.setattr("backend.engine.real_bots.place_market_order", place)
    mgr = _mgr()
    bot = _bot()
    ok = mgr.buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "x")
    return mgr, bot, ok


def test_classify_sdk_exception_types():
    from polymarket.errors import (
        InsufficientLiquidityError, RequestRejectedError, TransportError,
        TimeoutError as PolyTimeoutError, ConnectionLostError, UserInputError,
    )
    from polymarket import RejectedOrder

    raw = RejectedOrder.model_validate({
        "ok": False, "code": "fok_not_filled",
        "message": "order couldn't be fully filled. FOK orders are fully filled or killed.",
    })
    posted = ce.order_response_to_dict(raw, side="BUY", asset_id="tid")
    assert posted["outcome"] == ce.CONFIRMED_REJECT
    assert posted["rejected"] is True
    assert posted["unknown_send"] is False

    assert ce.classify_send_exception(InsufficientLiquidityError(
        "Insufficient liquidity for full fill.")) == ce.CONFIRMED_REJECT
    assert ce.classify_send_exception(UserInputError("bad")) == ce.CONFIRMED_REJECT
    assert ce.classify_send_exception(RequestRejectedError(
        "order couldn't be fully filled. FOK orders are fully filled or killed.",
        status=400, code="fok_not_filled",
    )) == ce.CONFIRMED_REJECT
    assert ce.classify_send_exception(Exception(
        "order couldn't be fully filled. FOK orders are fully filled or killed."
    )) == ce.CONFIRMED_REJECT
    assert ce.classify_send_exception(PolyTimeoutError("wait")) == ce.UNKNOWN_SEND_RESULT
    assert ce.classify_send_exception(TimeoutError("lost")) == ce.UNKNOWN_SEND_RESULT
    assert ce.classify_send_exception(TransportError("net")) == ce.UNKNOWN_SEND_RESULT
    assert ce.classify_send_exception(ConnectionResetError("reset")) == ce.UNKNOWN_SEND_RESULT
    assert ce.classify_send_exception(ConnectionLostError(
        "gone", code=1006, reason="")) == ce.UNKNOWN_SEND_RESULT
    assert ce.classify_send_exception(RequestRejectedError(
        "bad gateway", status=502)) == ce.UNKNOWN_SEND_RESULT
    assert ce.classify_send_exception(RuntimeError("boom")) == ce.UNKNOWN_SEND_RESULT


def test_place_market_order_classifies_without_raising(monkeypatch):
    from polymarket.errors import TimeoutError as PolyTimeoutError, InsufficientLiquidityError

    class Client:
        def place_market_order(self, **k):
            raise InsufficientLiquidityError("Insufficient liquidity for full fill.")

    monkeypatch.setattr(ce, "get_session", lambda acc: SimpleNamespace(client=Client()))
    out = ce.place_market_order({}, "tid", "BUY", 1.0, 0.82)
    assert out["outcome"] == ce.CONFIRMED_REJECT
    assert out["rejected"] is True
    assert out["unknown_send"] is False

    class Slow:
        def place_market_order(self, **k):
            raise PolyTimeoutError("timed out")

    monkeypatch.setattr(ce, "get_session", lambda acc: SimpleNamespace(client=Slow()))
    out = ce.place_market_order({}, "tid", "BUY", 1.0, 0.82)
    assert out["outcome"] == ce.UNKNOWN_SEND_RESULT
    assert out["unknown_send"] is True
    assert out["rejected"] is False


def test_explicit_rejected_order_clears_pending_no_position(monkeypatch):
    from polymarket import RejectedOrder
    raw = RejectedOrder.model_validate({
        "ok": False, "code": "fok_not_filled", "message": "FOK not filled",
    })
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: ce.order_response_to_dict(raw, side="BUY", asset_id="tid"),
    )
    mgr, bot, ok = _buy_with_place(monkeypatch, lambda *a, **k: ce.order_response_to_dict(
        raw, side="BUY", asset_id="tid"))
    assert ok is False
    assert bot["position"] is None
    assert bot["trades"] == []
    assert bot["pending_op"] is None
    assert not mgr._op_blocks(bot)
    assert "rejected" in (bot.get("last_exec_error") or "").lower()


def test_fok_kill_exception_is_confirmed_reject(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: ce._send_failure_dict(
            Exception("order couldn't be fully filled. FOK orders are fully filled or killed."),
            side="BUY", asset_id="tid"),
    )
    mgr, bot, ok = _buy_with_place(monkeypatch, lambda *a, **k: ce._send_failure_dict(
        Exception("order couldn't be fully filled. FOK orders are fully filled or killed."),
        side="BUY", asset_id="tid"))
    assert ok is False
    assert bot["position"] is None
    assert bot["trades"] == []
    assert bot["pending_op"] is None
    assert not mgr._op_blocks(bot)
    assert "send result unknown" not in (bot.get("last_exec_error") or "").lower()
    assert "rejected" in (bot.get("last_exec_error") or "").lower()


def test_fok_no_match_unmatched_is_confirmed_reject(monkeypatch):
    from polymarket import RejectedOrder
    raw = RejectedOrder.model_validate({
        "ok": False, "code": "unmatched", "message": "no match",
    })
    mgr, bot, ok = _buy_with_place(monkeypatch, lambda *a, **k: ce.order_response_to_dict(
        raw, side="BUY", asset_id="tid"))
    assert ok is False
    assert bot["position"] is None
    assert bot["pending_op"] is None
    assert not mgr._op_blocks(bot)


def test_timeout_is_unknown_send_needs_reconcile(monkeypatch):
    from polymarket.errors import TimeoutError as PolyTimeoutError
    mgr, bot, ok = _buy_with_place(monkeypatch, lambda *a, **k: ce._send_failure_dict(
        PolyTimeoutError("timed out"), side="BUY", asset_id="tid"))
    assert ok is False
    assert bot["position"] is None
    assert mgr._op_blocks(bot)
    assert bot["pending_op"]["needs_reconcile"] is True
    assert "send result unknown" in (bot.get("last_exec_error") or "").lower()


def test_connection_reset_after_send_is_unknown(monkeypatch):
    mgr, bot, ok = _buy_with_place(monkeypatch, lambda *a, **k: ce._send_failure_dict(
        ConnectionResetError("reset by peer"), side="BUY", asset_id="tid"))
    assert ok is False
    assert bot["position"] is None
    assert mgr._op_blocks(bot)
    assert bot["pending_op"]["needs_reconcile"] is True


def test_generic_transport_exception_is_unknown(monkeypatch):
    from polymarket.errors import TransportError
    mgr, bot, ok = _buy_with_place(monkeypatch, lambda *a, **k: ce._send_failure_dict(
        TransportError("proxy fail"), side="BUY", asset_id="tid"))
    assert ok is False
    assert bot["position"] is None
    assert mgr._op_blocks(bot)
    assert bot["pending_op"]["needs_reconcile"] is True


def test_no_exception_creates_false_fill(monkeypatch):
    from polymarket.errors import (
        InsufficientLiquidityError, TransportError, TimeoutError as PolyTimeoutError,
    )
    cases = [
        ce._send_failure_dict(InsufficientLiquidityError("no liq"), side="BUY", asset_id="t"),
        ce._send_failure_dict(Exception(
            "order couldn't be fully filled. FOK orders are fully filled or killed."),
            side="BUY", asset_id="t"),
        ce._send_failure_dict(PolyTimeoutError("t"), side="BUY", asset_id="t"),
        ce._send_failure_dict(TransportError("n"), side="BUY", asset_id="t"),
        ce._send_failure_dict(ConnectionResetError("r"), side="BUY", asset_id="t"),
        ce._send_failure_dict(RuntimeError("x"), side="BUY", asset_id="t"),
    ]
    for resp in cases:
        mgr, bot, ok = _buy_with_place(monkeypatch, lambda *a, _r=resp, **k: _r)
        assert ok is False
        assert bot["position"] is None
        assert bot["trades"] == []
        assert bot["stats"]["wins"] == 0


def _empty_fill(**extra):
    body = {
        "state": "confirmed_empty", "final": True, "filled": False,
        "amounts_ready": True, "shares": 0, "usd": 0, "price": None,
        "price_cents": None, "fee": None, "fee_known": False,
        "order_id": "ord-ok", "trade_ids": [], "tx_hashes": [],
        "error": "order cancelled",
    }
    body.update(extra)
    return body


def _no_retry(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retry POST")),
    )


def test_confirmed_fill_resumes_trading(monkeypatch):
    _no_retry(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill", lambda *a, **k: _confirmed_fill())
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["pending_op"] is None
    assert not mgr._op_blocks(bot)
    assert bot["position"] is not None
    assert bot.get("last_exec_error") is None
    assert bot.get("_resume_skip_eval") is True
    assert bot["position"].get("needs_reconcile") is False


def test_confirmed_empty_resumes_trading(monkeypatch):
    _no_retry(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill", lambda *a, **k: _empty_fill())
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["position"] is None
    assert bot["trades"] == []
    assert bot["pending_op"] is None
    assert not mgr._op_blocks(bot)
    assert bot.get("last_exec_error") is None
    assert bot.get("_resume_skip_eval") is True


def test_live_does_not_resume(monkeypatch):
    _no_retry(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {
            "state": "pending_settlement", "final": False, "filled": False,
            "amounts_ready": False, "error": "live", "order_id": "ord-ok",
            "trade_ids": ["tr-1"], "tx_hashes": [], "shares": None, "usd": None,
        },
    )
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert mgr._op_blocks(bot)
    assert bot["pending_op"]["needs_reconcile"] is True
    assert bot.get("_resume_skip_eval") is not True


def test_unknown_does_not_resume(monkeypatch):
    _no_retry(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {
            "state": "uncertain", "final": False, "filled": False,
            "amounts_ready": False, "error": "unknown", "order_id": "ord-ok",
            "trade_ids": [], "tx_hashes": [],
        },
    )
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert mgr._op_blocks(bot)


def test_api_error_does_not_resume(monkeypatch):
    _no_retry(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("get_order failed")),
    )
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert mgr._op_blocks(bot)
    assert "fill read failed" in (bot.get("last_exec_error") or "")


def test_restart_confirmed_fill_then_resume(monkeypatch):
    import asyncio
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retry POST")),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill", lambda *a, **k: _confirmed_fill())
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 900.0)
    bot = _bot(pending_op=_posted_buy_op(), status="running", asset="BTC")
    mgr = _mgr()
    mgr.bots[bot["id"]] = bot
    mgr._hub.retain = lambda *a, **k: SimpleNamespace()
    asyncio.run(mgr._boot_running())
    assert bot["pending_op"] is None
    assert bot["position"] is not None
    assert not mgr._op_blocks(bot)
    assert bot.get("_resume_skip_eval") is True
    assert mgr.buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "x") is False


def test_restart_confirmed_empty_then_resume(monkeypatch):
    import asyncio
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order") or {
            "ok": True, "order_id": "ord-new", "status": "matched",
            "trade_ids": ["tr-new"], "making_amount": "1.0", "taking_amount": "2.5",
        },
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: _empty_fill() if orders == [] else _confirmed_fill(
            order_id="ord-new", trade_ids=["tr-new"]))
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 3.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 900.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.invalidate_snapshot", lambda account: None)
    bot = _bot(pending_op=_posted_buy_op(), status="running", asset="BTC")
    mgr = _mgr()
    mgr.bots[bot["id"]] = bot
    mgr._hub.retain = lambda *a, **k: SimpleNamespace()
    asyncio.run(mgr._boot_running())
    assert bot["pending_op"] is None
    assert bot["position"] is None
    assert not mgr._op_blocks(bot)
    assert orders == []
    bot.pop("_resume_skip_eval", None)
    assert mgr.buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "later") is True
    assert orders == ["order"]


def test_no_duplicate_buy_immediately_after_reconciliation(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order") or {
            "ok": True, "order_id": "ord-2", "trade_ids": ["tr-2"],
            "making_amount": "1.0", "taking_amount": "2.5",
        },
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill", lambda *a, **k: _confirmed_fill())
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    skip = bot.pop("_resume_skip_eval", False)
    assert skip is True
    if not skip:
        mgr.buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "now")
    assert orders == []
    assert mgr.buy(bot, {"entries": {}}, _pipe(), "UP", 10.0, 90.0, "now") is False
    assert orders == []


def test_no_duplicate_position_or_trade_after_reconcile(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill", lambda *a, **k: _confirmed_fill())
    bot = _bot(pending_op=_posted_buy_op(), trades=[])
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    pos = bot["position"]
    n_trades = len(bot["trades"])
    n_activity_orders = sum(
        1 for e in bot["activity_log"] if e.get("level") == "order")
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["position"] is pos
    assert len(bot["trades"]) == n_trades
    assert sum(1 for e in bot["activity_log"] if e.get("level") == "order") == n_activity_orders


def _no_post(monkeypatch):
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retry POST")),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance",
        lambda account: (_ for _ in ()).throw(AssertionError("cash")),
    )


def test_confirmed_without_amounts_uses_making_taking(monkeypatch):
    _no_post(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: _confirmed_fill(
            amounts_ready=False, shares=None, usd=None, price=None, price_cents=None,
            error="confirmed_fill — still unproven",
        ),
    )
    bot = _bot(pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["pending_op"] is None
    assert bot["position"]["shares"] == 2.5
    assert bot["position"]["cost"] == 1.0
    assert bot["position"]["side"] == "UP"


def test_aged_unproven_zero_shares_clears_without_post(monkeypatch):
    seen = []
    _no_post(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {
            "state": "uncertain", "amounts_ready": False, "error": "get_order failed",
            "order_id": "ord-ok", "shares": None, "usd": None,
        },
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: seen.append("shares") or 0.0,
    )
    op = _posted_buy_op(created_ms=time.time() * 1000.0 - 120_000)
    bot = _bot(position=_open_pos(token_id="up-v2", order_id="ord-ok"), pending_op=op)
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert seen == ["shares"]
    assert bot["pending_op"] is None
    assert bot["position"] is None
    assert not mgr._op_blocks(bot)


def test_aged_unproven_positive_shares_keeps_position(monkeypatch):
    _no_post(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {
            "state": "uncertain", "amounts_ready": False, "error": "get_order failed",
        },
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 1.35)
    op = _posted_buy_op(created_ms=time.time() * 1000.0 - 120_000)
    bot = _bot(pending_op=op)
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["pending_op"] is None
    assert bot["position"]["shares"] == 1.35
    assert bot["position"]["side"] == "UP"
    assert bot["position"]["needs_reconcile"] is False


def test_unread_share_balance_keeps_block(monkeypatch):
    _no_post(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {"state": "uncertain", "amounts_ready": False, "error": "down"},
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance",
        lambda *a, **k: (_ for _ in ()).throw(ce.BalanceReadError("timeout")),
    )
    op = _posted_buy_op(created_ms=time.time() * 1000.0 - 120_000)
    bot = _bot(pending_op=op)
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert mgr._op_blocks(bot)
    assert "unread" in (bot["pending_op"].get("error") or "")


def test_stale_window_zero_shares_clears_immediately(monkeypatch):
    _no_post(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {"state": "pending_settlement", "amounts_ready": False, "error": None},
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 0.0)
    op = _posted_buy_op(created_ms=time.time() * 1000.0, window_id=10)
    bot = _bot(pending_op=op)
    mgr = _mgr()
    mgr._reconcile_pending(bot, {"id": "acc1"}, force=True, current_window_id=11)
    assert bot["pending_op"] is None
    assert bot["position"] is None


def test_flatten_withheld_logged_once():
    bot = _bot(
        position=_open_pos(),
        pending_op=_posted_buy_op(created_ms=time.time() * 1000.0),
    )
    mgr = _mgr()
    mgr._close_stale(bot, {"id": "acc1"}, bot["position"])
    mgr._close_stale(bot, {"id": "acc1"}, bot["position"])
    rows = [e for e in bot["activity_log"] if "flatten withheld" in e["msg"]]
    assert [e["msg"] for e in rows] == ["unfinished operation — flatten withheld"]
    assert rows[0]["level"] == "wait"
    assert mgr._op_blocks(bot)


def test_settlement_holds_log_as_wait_real_refusals_stay_error():
    from backend.engine.real_bots import activity_level

    assert activity_level("reconcile pending_settlement — still unproven") == "wait"
    assert activity_level("unfinished operation — sell withheld") == "wait"
    assert activity_level("SELL pending_settlement — held for reconcile") == "wait"
    assert activity_level("unfinished operation — flatten withheld") == "wait"
    assert activity_level("SELL withheld: confirmed 0 shares held (held=0)") == "error"
    assert activity_level("SELL rejected: order couldn't be fully filled") == "error"
    assert activity_level("reconcile fill read failed: timeout") == "error"
    assert activity_level("REDEEM withheld: gasless key required") == "wait"
    assert activity_level("REDEEM skipped: token is not a v2 position id — DOWN round") == "wait"

    bot = _bot(position=_open_pos(), pending_op=_posted_buy_op())
    mgr = _mgr()
    mgr._hold_for_reconcile(bot, bot["position"], "unfinished operation — sell withheld")
    assert bot["activity_log"][-1]["level"] == "wait"
    mgr._keep_needs_reconcile(bot, "reconcile pending_settlement — still unproven")
    assert bot["activity_log"][-1]["level"] == "wait"
    mgr._keep_needs_reconcile(bot, "reconcile fill read failed: boom")
    assert bot["activity_log"][-1]["level"] == "error"


def _ended_pipe(outcome=None, *, source=None, now=None, final=None, ptb=None):
    end_ms = 11 * 300_000
    pipe = _pipe(window_id=99, now=end_ms + 1_000 if now is None else now)
    pipe.window_id = 99
    result = {}
    if outcome is not None:
        result["outcome"] = outcome
    if source is not None:
        result["source"] = source
    if final is not None:
        result["final_twap"] = final
    if ptb is not None:
        result["ptb"] = ptb
    pipe.round_results = {10: result} if result else {}
    return pipe


def test_ended_round_official_win_settles_without_post_or_cash(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 3.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order"),
    )
    pos = _open_pos(asset="BTC")
    bot = _bot(pos, asset="BTC")
    balance = bot["balance"]
    _mgr()._close_stale(bot, {"id": "acc1"}, pos, _ended_pipe("UP", source="market"))
    assert orders == []
    assert bot["position"] is None
    assert bot["balance"] == balance
    assert bot["stats"]["wins"] == 1
    assert bot["stats"]["losses"] == 0
    assert bot["trades"][-1]["action"] == "SETTLE"
    assert bot["trades"][-1]["pnl"] == 3.0 - 1.2
    assert "resolved UP" in bot["activity_log"][-1]["msg"]
    assert bot["redeem_queue"][0]["position_id"] == "tid"
    assert bot["balance"] == balance


def test_ended_round_official_loss_settles_without_post(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 1.6393)
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order"),
    )
    pos = _open_pos(shares=1.6393, cost=1.0, fee_paid=0.0, asset="BTC")
    bot = _bot(pos, asset="BTC")
    _mgr()._close_stale(bot, {"id": "acc1"}, pos, _ended_pipe("DOWN", source="market"))
    assert orders == []
    assert bot["position"] is None
    assert bot["stats"]["losses"] == 1
    assert bot["trades"][-1]["pnl"] == -1.0
    assert bot["balance"] == 1000.0


def test_ended_round_waits_for_official_without_post(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 3.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order"),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.fetch_official_outcome",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("too early")),
    )
    pos = _open_pos(asset="BTC")
    bot = _bot(pos, asset="BTC")
    end_ms = 11 * 300_000
    pipe = _ended_pipe(now=end_ms + 10_000)
    _mgr()._close_stale(bot, {"id": "acc1"}, pos, pipe)
    _mgr()._close_stale(bot, {"id": "acc1"}, pos, pipe)
    assert orders == []
    assert bot["position"] is pos
    assert bot["trades"] == []
    waits = [e for e in bot["activity_log"] if "waiting for resolution" in e["msg"]]
    assert len(waits) == 1
    assert waits[0]["level"] == "wait"


def test_pending_op_blocks_resolution_settle(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 3.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order"),
    )
    pos = _open_pos(asset="BTC")
    bot = _bot(pos, asset="BTC", pending_op=_posted_buy_op())
    _mgr()._close_stale(bot, {"id": "acc1"}, pos, _ended_pipe("UP", source="market"))
    assert orders == []
    assert bot["position"] is pos
    assert bot["trades"] == []
    assert bot["stats"]["wins"] == 0


def test_dust_remainder_settles_instead_of_selling(monkeypatch):
    orders = []
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 0.0019)
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: orders.append("order"),
    )
    pos = _open_pos(shares=0.0019, cost=0.001, fee_paid=0.0, asset="BTC")
    bot = _bot(pos, asset="BTC")
    _mgr()._close_stale(bot, {"id": "acc1"}, pos, _ended_pipe("UP", source="market"))
    assert orders == []
    assert bot["position"] is None
    assert bot["trades"][-1]["shares"] == 0.0019
    assert bot["stats"]["wins"] == 1


def test_collect_redeem_targets_skips_open_token():
    rows = [
        {"redeemable": True, "size": 1.6, "asset_id": "open", "condition_id": "0xnow", "outcome": "UP"},
        {"redeemable": True, "size": 1.6, "asset_id": "old", "condition_id": "0xold", "outcome": "DOWN"},
        {"redeemable": False, "size": 2, "asset_id": "live", "condition_id": "0xlive", "outcome": "UP"},
    ]
    targets = ce.collect_redeem_targets(
        [{"condition_id": "0xold", "position_id": "old", "label": "queued"}],
        rows,
        ["open"],
        [],
    )
    assert [item["key"] for item in targets] == ["0xold"]
    assert targets[0]["condition_id"] == "0xold"
    assert targets[0]["position_id"] is None


def test_redeem_resolved_waits_and_does_not_report_cash(monkeypatch):
    seen = {}

    class Handle:
        def wait(self):
            return SimpleNamespace(transaction_hash="0xabc123")

    class Client:
        def redeem_positions(self, **kwargs):
            seen.update(kwargs)
            return Handle()

    monkeypatch.setattr(ce, "ensure_redeem_client", lambda account: Client())
    out = ce.redeem_resolved({"id": "acc"}, condition_id="0xcond", position_id="999")
    assert seen == {"condition_id": "0xcond"}
    assert out == {"ok": True, "already": False, "transaction_hash": "0xabc123"}
    assert "balance" not in out


def _v2_position_id() -> str:
    return str(int("01" + ("00" * 30) + "01", 16))


def test_reconcile_buy_keeps_condition_id(monkeypatch):
    _no_post(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: _confirmed_fill(),
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 999.0)
    bot = _bot(
        position=_open_pos(token_id="up-v2", order_id="ord-ok", condition_id="0xfrompos"),
        pending_op=_posted_buy_op(),
    )
    _mgr()._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["position"]["condition_id"] == "0xfrompos"

    restarted = _bot(pending_op=_posted_buy_op(condition_id="0xfromop"))
    _mgr()._reconcile_pending(restarted, {"id": "acc1"}, force=True)
    assert restarted["position"]["condition_id"] == "0xfromop"


def test_release_keeps_condition_id(monkeypatch):
    _no_post(monkeypatch)
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill",
        lambda *a, **k: {"state": "uncertain", "amounts_ready": False, "error": "get_order failed"},
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 1.35)
    op = _posted_buy_op(created_ms=time.time() * 1000.0 - 120_000, condition_id="0xrel")
    bot = _bot(
        position=_open_pos(token_id="up-v2", order_id="ord-ok", condition_id="0xold"),
        pending_op=op,
    )
    _mgr()._reconcile_pending(bot, {"id": "acc1"}, force=True)
    assert bot["position"]["condition_id"] == "0xrel"
    assert bot["position"]["shares"] == 1.35


def test_collect_redeem_targets_uses_row_condition_for_token():
    rows = [{
        "redeemable": True, "size": 1.2, "asset_id": "999",
        "condition_id": "0xabc", "outcome": "DOWN",
    }]
    targets = ce.collect_redeem_targets(
        [{"condition_id": "", "position_id": "999", "label": "DOWN round 9:10–9:15AM ET"}],
        rows, [], [],
    )
    assert len(targets) == 1
    assert targets[0]["key"] == "999"
    assert targets[0]["condition_id"] == "0xabc"
    assert targets[0]["position_id"] is None


def test_collect_redeem_targets_skips_plain_token_and_keeps_v2():
    v2 = _v2_position_id()
    assert ce.v2_redeem_position_id(v2) == v2
    assert ce.v2_redeem_position_id("999") is None
    targets = ce.collect_redeem_targets(
        [
            {"condition_id": "", "position_id": "999", "label": "plain"},
            {"condition_id": "", "position_id": v2, "label": "v2"},
        ],
        [], [], [],
    )
    assert [item["key"] for item in targets] == [v2]
    assert targets[0]["position_id"] == v2
    assert targets[0]["condition_id"] is None
    dropped = ce.unredeemable_queue_items(
        [
            {"condition_id": "", "position_id": "999", "label": "DOWN round 9:10–9:15AM ET"},
            {"condition_id": "", "position_id": v2, "label": "v2"},
            {"condition_id": "0xkeep", "position_id": "kept", "label": "has condition"},
        ],
        [], targets, [], [],
    )
    assert [item["key"] for item in dropped] == ["999"]


def test_redeem_account_drops_non_v2_token_without_calling(monkeypatch):
    monkeypatch.setattr(
        "backend.feeds.account_data.fetch_redeemable_positions", lambda account: [])
    monkeypatch.setattr(
        ce, "redeem_resolved",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("redeem called")),
    )
    out = ce.redeem_account_positions(
        {"id": "acc"},
        [{"condition_id": "", "position_id": "999", "label": "DOWN round 9:10–9:15AM ET"}],
        [], [],
    )
    assert out["done"] == ["999"]
    assert "not a v2 position id" in out["notes"][0]["msg"]
    assert out["notes"][0]["level"] == "wait"
    assert out["skip"] == {}


def test_redeem_resolved_zero_balance_is_already_done(monkeypatch):
    from polymarket.errors import UserInputError

    class Client:
        def redeem_positions(self, **kwargs):
            raise UserInputError("Position has no balance to redeem")

    monkeypatch.setattr(ce, "ensure_redeem_client", lambda account: Client())
    out = ce.redeem_resolved({"id": "acc"}, position_id="123")
    assert out["already"] is True
    assert out["transaction_hash"] is None
