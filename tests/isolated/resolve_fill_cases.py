"""Isolated CLOB fill-parse tests. Does not open live SQLite or send orders."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

_orig_storage = sys.modules.get("backend.storage")
_orig_accounts = sys.modules.get("backend.accounts")

_storage = ModuleType("backend.storage")
_storage.storage = SimpleNamespace(
    save_account=lambda a: None,
    load_accounts=lambda: {},
    get_account=lambda i: {"id": i or "acc1"},
    save_real_bot=lambda b: None,
    load_real_bots=lambda: {},
    load_bots=lambda: {},
)
sys.modules["backend.storage"] = _storage

_accounts = ModuleType("backend.accounts")
_accounts.decrypt_key = lambda x: "11" * 32
sys.modules["backend.accounts"] = _accounts

from backend.engine import clob_executor as ce  # noqa: E402

if _orig_storage is None:
    sys.modules.pop("backend.storage", None)
else:
    sys.modules["backend.storage"] = _orig_storage
if _orig_accounts is None:
    sys.modules.pop("backend.accounts", None)
else:
    sys.modules["backend.accounts"] = _orig_accounts


def _order(**kw):
    body = {
        "id": kw.get("id", "ord-1"),
        "status": kw.get("status", "matched"),
        "side": kw.get("side", "BUY"),
        "asset_id": kw.get("asset_id", "tid"),
        "price": kw.get("price", "0.4"),
        "original_size": kw.get("original_size", "100"),
        "size_matched": kw.get("size_matched", "0"),
        "associate_trades": kw.get("associate_trades", []),
        "fetch_error": None,
    }
    body.update(kw)
    return body


def _trade(**kw):
    body = {
        "id": kw.get("id", "tr-1"),
        "status": kw.get("status", "CONFIRMED"),
        "size": kw.get("size", "4"),
        "price": kw.get("price", "0.4"),
        "side": kw.get("side", "BUY"),
        "trader_side": kw.get("trader_side", "TAKER"),
        "taker_order_id": kw.get("taker_order_id", "ord-1"),
        "asset_id": kw.get("asset_id", "tid"),
        "transaction_hash": kw.get("transaction_hash"),
        "fee": kw.get("fee"),
        "fee_rate_bps": kw.get("fee_rate_bps"),
        "maker_order_ids": kw.get("maker_order_ids", []),
        "fetch_error": None,
    }
    body.update(kw)
    return body


def _stub(monkeypatch, order=None, trades=None, order_err=None, trade_err=None):
    def fo(account, oid):
        if order_err:
            return {"id": oid, "fetch_error": order_err}
        parsed = _order(id=oid)
        if order:
            parsed.update(order)
        return parsed

    def ft(account, trade_ids=None, asset_id=None):
        if trade_err:
            return {"trades": [], "fetch_error": trade_err}
        return {"trades": list(trades or []), "fetch_error": None}

    monkeypatch.setattr(ce, "fetch_clob_order", fo)
    monkeypatch.setattr(ce, "fetch_clob_trades", ft)


def test_delayed_zero_matched_does_not_use_original_size(monkeypatch):
    _stub(monkeypatch, order=_order(
        status="delayed", size_matched=0, original_size=100,
    ), trades=[])
    fill = ce.resolve_fill(
        {}, {"orderID": "ord-1", "status": "delayed"}, "BUY")
    assert fill["shares"] != 100
    assert fill["filled"] is False
    assert fill["state"] != ce.FILL_CONFIRMED
    assert fill["original_size"] == 100.0
    assert fill["size_matched"] == 0.0


def test_live_without_fill_is_not_a_filled_position(monkeypatch):
    _stub(monkeypatch, order=_order(
        status="live", size_matched=0, original_size=50,
    ), trades=[])
    fill = ce.resolve_fill({}, {"order_id": "ord-1", "status": "live"}, "BUY")
    assert fill["filled"] is False
    assert fill["state"] != ce.FILL_CONFIRMED
    assert fill["shares"] != 50


def test_cancelled_order_without_match_is_empty(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="cancelled", size_matched="0", original_size="5",
                     associate_trades=[]),
        trades=[],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "live",
        "trade_ids": [], "transactions_hashes": [],
    }, "BUY")
    assert fill["state"] == ce.FILL_EMPTY
    assert fill["filled"] is False
    assert fill["shares"] == 0


def test_fok_reject_is_confirmed_empty():
    from polymarket import RejectedOrder

    raw = RejectedOrder.model_validate({
        "ok": False, "code": "fok_not_filled", "message": "FOK not filled",
    })
    posted = ce.order_response_to_dict(raw, side="BUY", asset_id="tid")
    assert posted["rejected"] is True
    assert posted["success"] is False
    fill = ce.resolve_fill({}, posted, "BUY")
    assert fill["state"] == ce.FILL_EMPTY
    assert fill["final"] is True
    assert fill["filled"] is False
    assert fill["shares"] == 0


def test_matched_unsettled_is_pending(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4", original_size="4"),
        trades=[_trade(status="MATCHED", size="4", transaction_hash=None)],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1"], "transactions_hashes": [],
        "making_amount": "1.6", "taking_amount": "4",
    }, "BUY")
    assert fill["state"] == ce.FILL_PENDING
    assert fill["final"] is False
    assert fill["filled"] is False
    assert fill["shares"] == 4.0
    assert fill["tx_hashes"] == []


def test_confirmed_buy_and_sell_amounts_and_units(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4", original_size="4",
                     side="BUY", price="0.4"),
        trades=[_trade(status="CONFIRMED", size="4", price="0.4",
                       transaction_hash="0xabc", fee="0.02")],
    )
    buy = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1"], "transactions_hashes": ["0xabc"],
        "making_amount": "1.6", "taking_amount": "4",
        "side": "BUY",
    }, "BUY")
    assert buy["state"] == ce.FILL_CONFIRMED
    assert buy["shares"] == 4.0
    assert buy["usd"] == 1.6
    assert buy["shares"] != 4.0 / 1_000_000
    assert buy["usd"] != 1.6 / 1_000_000
    assert abs(buy["price"] - 0.4) < 1e-9
    assert buy["fee"] == 0.02
    assert buy["fee_known"] is True

    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4", original_size="4",
                     side="SELL", price="0.4"),
        trades=[_trade(status="CONFIRMED", size="4", price="0.4", side="SELL",
                       taker_order_id="ord-s", transaction_hash="0xdef",
                       fee="0.01")],
    )
    sell = ce.resolve_fill({}, {
        "order_id": "ord-s", "status": "matched",
        "trade_ids": ["tr-1"],
        "making_amount": "4", "taking_amount": "1.6",
        "side": "SELL",
    }, "SELL")
    assert sell["state"] == ce.FILL_CONFIRMED
    assert sell["shares"] == 4.0
    assert sell["usd"] == 1.6


def test_failed_trade_is_not_successful(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="FAILED", size="4", transaction_hash=None)],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
    }, "BUY")
    assert fill["filled"] is False
    assert fill["state"] != ce.FILL_CONFIRMED
    assert fill["state"] == ce.FILL_EMPTY


def test_query_error_is_uncertain_not_zero_fill(monkeypatch):
    _stub(monkeypatch, order_err="get_order failed: TimeoutError")
    fill = ce.resolve_fill({}, {"order_id": "ord-1", "status": "matched"}, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["filled"] is False
    assert fill["shares"] is None
    assert fill["final"] is False


def test_duplicate_trade_ids_are_not_double_counted(monkeypatch):
    trades = [
        _trade(id="tr-1", size="2", status="CONFIRMED"),
        _trade(id="tr-1", size="2", status="CONFIRMED"),
        _trade(id="tr-2", size="3", status="CONFIRMED", taker_order_id="ord-1"),
    ]
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="5", associate_trades=["tr-1", "tr-2"]),
        trades=trades,
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1", "tr-1", "tr-2"],
    }, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["shares"] == 5.0


def test_foreign_order_trades_are_ignored(monkeypatch):
    trades = [
        _trade(id="tr-ours", size="2", status="CONFIRMED", taker_order_id="ord-1"),
        _trade(id="tr-other", size="9", status="CONFIRMED",
               taker_order_id="ord-other", maker_order_ids=["ord-other"]),
    ]
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="2", associate_trades=["tr-ours"]),
        trades=trades,
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-ours"],
    }, "BUY")
    assert fill["shares"] == 2.0
    assert fill["shares"] != 11.0
    assert "tr-other" not in fill["trade_ids"]


def test_missing_fee_is_not_confirmed_zero(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4", fee=None)],
    )
    missing = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
    }, "BUY")
    assert missing["fee_known"] is False
    assert missing["fee"] is None

    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4", fee=0)],
    )
    zero = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
    }, "BUY")
    assert zero["fee_known"] is True
    assert zero["fee"] == 0.0


def test_empty_hashes_after_match_are_not_a_reject(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4", transaction_hash=None)],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1"], "transactions_hashes": [],
    }, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["tx_hashes"] == []
    assert fill["state"] != ce.FILL_EMPTY


def test_conflicting_sizes_are_uncertain(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="1"),
        trades=[_trade(status="CONFIRMED", size="9")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["filled"] is False
    assert fill["shares"] is None


def test_order_dict_does_not_alias_original_size_as_fill():
    raw = SimpleNamespace(
        id="o", status="delayed", side="BUY", asset_id="tid",
        price=0.5, original_size=100, size_matched=0,
        associate_trades=(), order_type="FOK", outcome="Up",
    )
    parsed = ce._order_dict(raw)
    assert parsed["original_size"] == 100
    assert parsed["size_matched"] == 0
    assert "size" not in parsed or parsed.get("size") != parsed["original_size"]


def test_failed_status_survives_trade_dict():
    raw = SimpleNamespace(
        id="tr-f", status="FAILED", size=3, price=0.4, side="BUY",
        trader_side="TAKER", taker_order_id="ord-1", asset_id="tid",
        transaction_hash=None, fee=None, fee_rate_bps=None, maker_orders=(),
    )
    parsed = ce._trade_dict(raw)
    assert parsed["status"] == "FAILED"


def test_fetch_trades_error_is_not_empty_success(monkeypatch):
    class Client:
        def list_account_trades(self, **kwargs):
            raise RuntimeError("timeout")

    monkeypatch.setattr(ce, "get_session", lambda acc: SimpleNamespace(client=Client()))
    result = ce.fetch_clob_trades({}, trade_ids=["tr-1"], asset_id="should-not-query")
    assert result["fetch_error"]
    assert result["trades"] == []


def test_fetch_trades_does_not_list_by_asset(monkeypatch):
    calls = []

    class Page:
        items = []

        def first_page(self):
            return self

    class Client:
        def list_account_trades(self, **kwargs):
            calls.append(kwargs)
            return Page()

    monkeypatch.setattr(ce, "get_session", lambda acc: SimpleNamespace(client=Client()))
    ce.fetch_clob_trades({}, trade_ids=["tr-1"], asset_id="other-asset")
    assert calls == [{"id": "tr-1"}]
    empty = ce.fetch_clob_trades({}, trade_ids=[], asset_id="other-asset")
    assert empty == {"trades": [], "fetch_error": None, "missing_ids": []}
    assert calls == [{"id": "tr-1"}]


def test_place_market_order_keeps_reject(monkeypatch):
    from polymarket import RejectedOrder

    rejected = RejectedOrder.model_validate({
        "ok": False, "code": "fok_not_filled", "message": "no match",
    })

    class Client:
        def place_market_order(self, **kwargs):
            return rejected

    monkeypatch.setattr(ce, "get_session", lambda acc: SimpleNamespace(client=Client()))
    posted = ce.place_market_order({}, "tid", "BUY", 10.0, 0.4)
    assert posted["rejected"] is True
    assert posted["ok"] is False
    assert posted["success"] is False
    assert posted["code"] == "fok_not_filled"


def test_incomplete_set_one_confirmed_of_two(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4",
                     associate_trades=["tr-1", "tr-2"]),
        trades=[_trade(id="tr-1", status="CONFIRMED", size="2")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1", "tr-2"],
    }, "BUY")
    assert fill["state"] != ce.FILL_CONFIRMED
    assert fill["final"] is False
    assert fill["filled"] is False


def test_incomplete_set_one_failed_of_two(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", associate_trades=["tr-1", "tr-2"]),
        trades=[_trade(id="tr-1", status="FAILED", size="2")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1", "tr-2"],
    }, "BUY")
    assert fill["state"] != ce.FILL_EMPTY
    assert fill["state"] != ce.FILL_CONFIRMED
    assert fill["final"] is False


def test_confirmed_plus_missing_status(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", associate_trades=["tr-1", "tr-2"]),
        trades=[
            _trade(id="tr-1", status="CONFIRMED", size="2"),
            _trade(id="tr-2", status="", size="2"),
        ],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1", "tr-2"],
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["filled"] is False


def test_confirmed_plus_mined_or_retrying(monkeypatch):
    for other in ("MINED", "RETRYING"):
        _stub(
            monkeypatch,
            order=_order(status="matched", size_matched="4",
                         associate_trades=["tr-1", "tr-2"]),
            trades=[
                _trade(id="tr-1", status="CONFIRMED", size="2"),
                _trade(id="tr-2", status=other, size="2"),
            ],
        )
        fill = ce.resolve_fill({}, {
            "order_id": "ord-1", "status": "matched",
            "trade_ids": ["tr-1", "tr-2"],
        }, "BUY")
        assert fill["state"] == ce.FILL_PENDING, other
        assert fill["final"] is False
        assert fill["filled"] is False


def test_order_id_and_asset_id_mismatch(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(id="ord-other", status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4")],
    )
    monkeypatch.setattr(
        ce, "fetch_clob_order",
        lambda account, oid: {
            **_order(id="ord-other", status="matched", size_matched="4"),
            "id_mismatch": True,
        },
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "asset_id": "tid",
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN

    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4", asset_id="tid"),
        trades=[_trade(status="CONFIRMED", size="4", asset_id="other-asset")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "asset_id": "tid",
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["filled"] is False


def test_maker_fill_does_not_use_full_taker_size(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="2"),
        trades=[_trade(
            status="CONFIRMED", size="10", price="0.4",
            trader_side="MAKER", taker_order_id="ord-taker",
            maker_orders=[{
                "order_id": "ord-1", "matched_amount": "2", "price": "0.4",
                "side": "BUY", "asset_id": "tid",
            }],
            maker_order_ids=["ord-1"],
        )],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "asset_id": "tid",
    }, "BUY")
    assert fill["shares"] != 10
    assert fill["shares"] == 2
    assert fill["usd"] is None
    assert fill["usd_known"] is False
    assert fill["amounts_ready"] is False
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["usd_estimate"] == 0.8
    assert "exact" not in (fill.get("usd_estimate_source") or "")
    assert "unsigned" in (fill.get("usd_estimate_source") or "")


def test_negative_size_and_notional_rejected(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="-4", price="0.4")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["filled"] is False

    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4", price="0.4")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "-1.6", "taking_amount": "4",
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN


def test_missing_fill_price_is_not_order_price(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4", price="0.9"),
        trades=[_trade(status="CONFIRMED", size="4", price=None)],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
    }, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["price"] is None
    assert fill["usd"] is None
    assert fill["usd_known"] is False
    assert fill["filled"] is True
    assert fill["amounts_ready"] is False
    assert fill["final"] is False


def test_conflicting_duplicate_trade_id(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", associate_trades=["tr-1"]),
        trades=[
            _trade(id="tr-1", status="CONFIRMED", size="2"),
            _trade(id="tr-1", status="FAILED", size="2"),
        ],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["filled"] is False


def test_complete_confirmed_set_still_succeeds(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="5",
                     associate_trades=["tr-1", "tr-2"]),
        trades=[
            _trade(id="tr-1", status="CONFIRMED", size="2", price="0.4", fee="0"),
            _trade(id="tr-2", status="CONFIRMED", size="3", price="0.4", fee="0"),
        ],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1", "tr-2"],
        "making_amount": "2.0", "taking_amount": "5",
    }, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["filled"] is True
    assert fill["final"] is True
    assert fill["amounts_ready"] is True
    assert fill["shares"] == 5.0
    assert fill["usd"] == 2.0


def test_consumer_does_not_close_on_incomplete_fill(monkeypatch):
    from backend.engine.real_bots import RealBotManager

    fill = {
        "state": ce.FILL_UNCERTAIN, "final": False, "filled": False,
        "amounts_ready": False, "shares": 2, "usd": 0.8, "price_cents": 40,
        "error": "incomplete trade set", "order_id": "ord-1",
        "tx_hashes": [], "trade_ids": ["tr-1", "tr-2"],
    }
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 3.0)
    posted = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: posted.append("order") or {
            "ok": True, "order_id": "ord-1", "status": "matched",
            "trade_ids": ["tr-1", "tr-2"],
        },
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill", lambda *a, **k: fill)
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 1000.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.invalidate_snapshot", lambda account: None)

    pos = {
        "side": "UP", "shares": 3.0, "cost": 1.2, "fee_paid": 0,
        "window_id": 10, "token_id": "tid", "protocol_version": "v1",
    }
    bot = {
        "id": "r_test", "account_id": "acc1", "position": pos,
        "balance": 1000.0, "start_balance": 1000.0,
        "stats": {"rounds": 0, "wins": 0, "losses": 0, "fees": 0.0},
        "trades": [], "activity_log": [], "orders_placed": 0, "status": "running",
    }
    pipe = SimpleNamespace(
        poly_now_ms=lambda: 50_000,
        window_start_ms=0.0, window_end_ms=300_000.0, window_id=10,
        asset=SimpleNamespace(key="BTC"),
        polymarket=SimpleNamespace(market={
            "version": "v1", "up_token": "tid", "down_token": "down",
        }),
    )
    mgr = RealBotManager(SimpleNamespace(pipelines={}), SimpleNamespace(
        _quote=lambda p, s: (40.0, 100.0, 39.0)))
    assert mgr.sell(bot, pipe, "tp") is False
    sent = len(posted)
    mgr._close_stale(bot, {"id": "acc1"}, pos)
    assert posted[sent:] == []
    assert bot["position"] is pos
    if posted:
        assert pos.get("needs_reconcile") is True
    assert bot["stats"]["wins"] == 0 and bot["stats"]["losses"] == 0
    assert bot["trades"] == []


def test_post_conflicts_with_same_set_buy(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4", price="0.4")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "99", "taking_amount": "200",
        "side": "BUY",
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["final"] is False
    assert fill["amounts_ready"] is False
    assert fill["filled"] is False


def test_post_conflicts_with_same_set_sell(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4", side="SELL"),
        trades=[_trade(status="CONFIRMED", size="4", price="0.4", side="SELL",
                       taker_order_id="ord-1")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "200", "taking_amount": "99",
        "side": "SELL",
    }, "SELL")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["final"] is False
    assert fill["amounts_ready"] is False


def test_post_agrees_with_same_set(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4", price="0.4", fee="0")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "1.6", "taking_amount": "4",
    }, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["shares"] == 4.0
    assert fill["usd"] == 1.6
    assert fill["amounts_ready"] is True
    assert fill["shares_known"] is True
    assert fill["usd_known"] is True
    assert fill["shares_source"] == "trade_sizes"
    assert fill["usd_source"] == "post_placement"
    assert fill["usd_estimate"] == 1.6
    assert "exact" not in (fill.get("usd_estimate_source") or "")


def _maker_fill(order_id, matched_amount, price, side="SELL"):
    return {
        "order_id": order_id,
        "matched_amount": matched_amount,
        "price": price,
        "side": side,
        "asset_id": "tid",
    }


def test_one_maker_fill_rounding_matches_floored_post(monkeypatch):
    price = "0.333333"
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="1"),
        trades=[_trade(
            status="CONFIRMED", size="1", price=price, fee="0",
            maker_orders=[_maker_fill("mk-1", "1", price)],
        )],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "0.333333", "taking_amount": "1",
    }, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["shares"] == 1.0
    assert fill["usd"] == 0.333333
    assert fill["amounts_ready"] is True


def test_multiple_trades_accumulate_maker_floors(monkeypatch):
    price = "0.333333"
    trades = [
        _trade(
            id=f"tr-{i}", status="CONFIRMED", size="1", price=price, fee="0",
            maker_orders=[_maker_fill(f"mk-{i}", "1", price)],
        )
        for i in (1, 2, 3)
    ]
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="3",
                     associate_trades=["tr-1", "tr-2", "tr-3"]),
        trades=trades,
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1", "tr-2", "tr-3"],
        "making_amount": "0.999999", "taking_amount": "3",
    }, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["shares"] == 3.0
    assert fill["usd"] == 0.999999
    assert fill["amounts_ready"] is True


def test_multiple_maker_fills_in_one_trade(monkeypatch):
    price = "0.333333"
    makers = [
        _maker_fill("mk-1", "1", price),
        _maker_fill("mk-2", "1", price),
        _maker_fill("mk-3", "1", price),
    ]
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="3"),
        trades=[_trade(
            status="CONFIRMED", size="3", price=price, fee="0",
            maker_orders=makers,
        )],
    )
    post = {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "0.999999", "taking_amount": "3",
    }
    fill = ce.resolve_fill({}, post, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["usd"] == 0.999999
    assert fill["usd_source"] == "post_placement"
    assert fill["usd_known"] is True
    assert "exact" not in (fill.get("usd_estimate_source") or "")

    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="3"),
        trades=[_trade(
            status="CONFIRMED", size="3", price=price, fee="0",
            maker_orders=makers,
        )],
    )
    unfloored = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "1", "taking_amount": "3",
    }, "BUY")
    assert unfloored["state"] == ce.FILL_CONFIRMED
    assert unfloored["usd"] == 1.0
    assert unfloored["usd_source"] == "post_placement"
    assert unfloored["amounts_ready"] is True
    assert "exact" not in (unfloored.get("usd_estimate_source") or "")


def test_buy_and_sell_agree_with_reconstructed_fills(monkeypatch):
    price = "0.333333"
    makers = [
        _maker_fill("mk-1", "1", price),
        _maker_fill("mk-2", "1", price),
    ]
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="2"),
        trades=[_trade(
            status="CONFIRMED", size="2", price=price, fee="0",
            maker_orders=makers,
        )],
    )
    buy = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "0.666666", "taking_amount": "2",
    }, "BUY")
    assert buy["state"] == ce.FILL_CONFIRMED
    assert buy["shares"] == 2.0
    assert buy["usd"] == 0.666666

    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="2", side="SELL"),
        trades=[_trade(
            status="CONFIRMED", size="2", price=price, fee="0", side="SELL",
            maker_orders=[
                _maker_fill("mk-1", "1", price, side="BUY"),
                _maker_fill("mk-2", "1", price, side="BUY"),
            ],
        )],
    )
    sell = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "2", "taking_amount": "0.666666",
        "side": "SELL",
    }, "SELL")
    assert sell["state"] == ce.FILL_CONFIRMED
    assert sell["shares"] == 2.0
    assert sell["usd"] == 0.666666


def test_share_qty_one_base_unit_off_is_uncertain(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4.000001", price="0.4", fee="0")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "1.6", "taking_amount": "4",
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["filled"] is False
    assert fill["amounts_ready"] is False


def test_small_estimate_post_diff_is_not_a_rounding_conflict(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4", price="0.4", fee="0")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "1.600001", "taking_amount": "4",
    }, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["usd"] == 1.600001
    assert fill["usd_source"] == "post_placement"
    assert fill["usd_known"] is True
    assert fill["amounts_ready"] is True


def test_trade_id_count_is_not_a_rounding_budget(monkeypatch):
    price = "0.333333"
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="3",
                     associate_trades=["tr-1", "tr-2", "tr-3"]),
        trades=[
            _trade(id=f"tr-{i}", status="CONFIRMED", size="1", price=price, fee="0")
            for i in (1, 2, 3)
        ],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1", "tr-2", "tr-3"],
        "making_amount": "1", "taking_amount": "3",
    }, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["usd"] == 1.0
    assert fill["usd_source"] == "post_placement"
    assert "exact" not in (fill.get("usd_estimate_source") or "")


def test_later_fills_are_not_compared_to_placement_amounts(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="6",
                     associate_trades=["tr-1", "tr-2"]),
        trades=[
            _trade(id="tr-1", status="CONFIRMED", size="4", price="0.4", fee="0"),
            _trade(id="tr-2", status="CONFIRMED", size="2", price="0.4", fee="0"),
        ],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1"],
        "making_amount": "1.6", "taking_amount": "4",
    }, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["shares"] == 6.0
    assert fill["shares_source"] == "trade_sizes"
    assert fill["usd"] is None
    assert fill["usd_known"] is False
    assert fill["usd_source"] is None
    assert fill["amounts_ready"] is False
    assert fill["final"] is False
    assert fill["usd_estimate"] == 2.4


def test_price_above_one_is_uncertain_including_maker_leg(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4", price="1.20")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["filled"] is False
    assert "1.20" in (fill.get("error") or "") or "1.2" in (fill.get("error") or "")

    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="2"),
        trades=[_trade(
            status="CONFIRMED", size="10", price="0.4",
            trader_side="MAKER", taker_order_id="ord-taker",
            maker_orders=[{
                "order_id": "ord-1", "matched_amount": "2", "price": "1.20",
                "side": "BUY", "asset_id": "tid",
            }],
        )],
    )
    maker = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "asset_id": "tid",
    }, "BUY")
    assert maker["state"] == ce.FILL_UNCERTAIN
    assert maker["filled"] is False


def test_implied_price_outside_range_is_uncertain(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4", price=None)],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "6", "taking_amount": "4",
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["filled"] is False
    assert fill["amounts_ready"] is False


def test_unknown_status_only_is_uncertain(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="OPEN", size="4", price="0.4")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["final"] is False
    assert fill["state"] != ce.FILL_PENDING


def test_confirmed_plus_unknown_status_is_uncertain(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4",
                     associate_trades=["tr-1", "tr-2"]),
        trades=[
            _trade(id="tr-1", status="CONFIRMED", size="2", price="0.4"),
            _trade(id="tr-2", status="WEIRD", size="2", price="0.4"),
        ],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched",
        "trade_ids": ["tr-1", "tr-2"],
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["final"] is False
    assert fill["filled"] is False


def test_known_intermediate_statuses_stay_pending(monkeypatch):
    for status in ("MATCHED", "MINED", "RETRYING", "MATCHED_NOT_BROADCASTED"):
        _stub(
            monkeypatch,
            order=_order(status="matched", size_matched="4"),
            trades=[_trade(status=status, size="4", price="0.4")],
        )
        fill = ce.resolve_fill({}, {
            "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
            "making_amount": "1.6", "taking_amount": "4",
        }, "BUY")
        assert fill["state"] == ce.FILL_PENDING, status
        assert fill["final"] is False
        assert fill["filled"] is False
        assert fill["amounts_ready"] is False
        assert fill["usd_known"] is False


def test_maker_buy_and_sell_reconstruction_is_not_exact(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="2"),
        trades=[_trade(
            status="CONFIRMED", size="10", price="0.4",
            trader_side="MAKER", taker_order_id="ord-taker",
            maker_orders=[_maker_fill("ord-1", "2", "0.4", side="BUY")],
        )],
    )
    buy = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "asset_id": "tid",
    }, "BUY")
    assert buy["state"] == ce.FILL_CONFIRMED
    assert buy["shares"] == 2
    assert buy["usd"] is None
    assert buy["usd_known"] is False
    assert buy["amounts_ready"] is False
    assert buy["usd_estimate"] == 0.8
    assert "exact" not in (buy.get("usd_estimate_source") or "")
    assert "BUY" in (buy.get("usd_estimate_source") or "")
    assert "unsigned" in (buy.get("usd_estimate_source") or "")

    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="2", side="SELL"),
        trades=[_trade(
            status="CONFIRMED", size="10", price="0.4", side="SELL",
            trader_side="MAKER", taker_order_id="ord-taker",
            maker_orders=[_maker_fill("ord-1", "2", "0.4", side="SELL")],
        )],
    )
    sell = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "asset_id": "tid", "side": "SELL",
    }, "SELL")
    assert sell["state"] == ce.FILL_CONFIRMED
    assert sell["shares"] == 2
    assert sell["usd"] is None
    assert sell["usd_known"] is False
    assert sell["amounts_ready"] is False
    assert "exact" not in (sell.get("usd_estimate_source") or "")
    assert "SELL" in (sell.get("usd_estimate_source") or "")


def test_many_maker_rows_do_not_make_estimate_exact(monkeypatch):
    price = "0.4"
    makers = [_maker_fill(f"mk-{i}", "1", price) for i in (1, 2, 3)]
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="3"),
        trades=[_trade(
            status="CONFIRMED", size="3", price=price, fee="0",
            maker_orders=makers,
        )],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
    }, "BUY")
    assert fill["state"] == ce.FILL_CONFIRMED
    assert fill["usd"] is None
    assert fill["usd_known"] is False
    assert fill["amounts_ready"] is False
    assert fill["usd_estimate"] == 1.2
    assert fill["usd_estimate_source"]
    assert "exact" not in fill["usd_estimate_source"]


def test_identity_conflicts_still_block(monkeypatch):
    _stub(
        monkeypatch,
        order=_order(status="matched", size_matched="4"),
        trades=[_trade(status="CONFIRMED", size="4", price="0.4",
                       taker_order_id="other-order")],
    )
    fill = ce.resolve_fill({}, {
        "order_id": "ord-1", "status": "matched", "trade_ids": ["tr-1"],
        "making_amount": "1.6", "taking_amount": "4",
    }, "BUY")
    assert fill["state"] == ce.FILL_UNCERTAIN
    assert fill["filled"] is False
    assert fill["final"] is False
    assert fill["amounts_ready"] is False


def test_consumer_holds_when_usd_not_documented(monkeypatch):
    from backend.engine.real_bots import RealBotManager

    fill = {
        "state": ce.FILL_CONFIRMED, "final": False, "filled": True,
        "amounts_ready": False, "usd_known": False, "shares_known": True,
        "shares": 2, "usd": None, "usd_estimate": 0.8, "price_cents": None,
        "error": None, "order_id": "ord-1",
        "tx_hashes": [], "trade_ids": ["tr-1"], "fee_known": False, "fee": None,
    }
    monkeypatch.setattr(
        "backend.engine.real_bots.token_share_balance", lambda *a, **k: 2.0)
    posted = []
    monkeypatch.setattr(
        "backend.engine.real_bots.place_market_order",
        lambda *a, **k: posted.append("order") or {
            "ok": True, "order_id": "ord-1", "status": "matched",
            "trade_ids": ["tr-1"],
        },
    )
    monkeypatch.setattr(
        "backend.engine.real_bots.resolve_fill", lambda *a, **k: fill)
    monkeypatch.setattr(
        "backend.engine.real_bots.sync_clob_balance", lambda account: 1000.0)
    monkeypatch.setattr(
        "backend.engine.real_bots.invalidate_snapshot", lambda account: None)

    pos = {
        "side": "UP", "shares": 2.0, "cost": 0.8, "fee_paid": 0,
        "window_id": 10, "token_id": "tid", "protocol_version": "v1",
    }
    bot = {
        "id": "r_test", "account_id": "acc1", "position": pos,
        "balance": 1000.0, "start_balance": 1000.0,
        "stats": {"rounds": 0, "wins": 0, "losses": 0, "fees": 0.0},
        "trades": [], "activity_log": [], "orders_placed": 0, "status": "running",
    }
    pipe = SimpleNamespace(
        poly_now_ms=lambda: 50_000,
        window_start_ms=0.0, window_end_ms=300_000.0, window_id=10,
        asset=SimpleNamespace(key="BTC"),
        polymarket=SimpleNamespace(market={
            "version": "v1", "up_token": "tid", "down_token": "down",
        }),
    )
    mgr = RealBotManager(SimpleNamespace(pipelines={}), SimpleNamespace(
        _quote=lambda p, s: (40.0, 100.0, 39.0)))
    assert mgr.sell(bot, pipe, "tp") is False
    sent = len(posted)
    mgr._close_stale(bot, {"id": "acc1"}, pos)
    assert posted[sent:] == []
    assert bot["position"] is pos
    if posted:
        assert pos.get("needs_reconcile") is True
    assert bot["stats"]["wins"] == 0 and bot["stats"]["losses"] == 0
    assert bot["trades"] == []
