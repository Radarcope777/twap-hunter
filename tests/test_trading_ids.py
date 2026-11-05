"""Isolated Protocol V2 trading-ID selection. Does not import bots or storage."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from backend.feeds.polymarket import (
    clob_market_subscribe_payload,
    parse_live_market,
    select_trading_ids,
    token_id_for_side,
)

NOW_MS = 1_700_000_000_000.0
WINDOW_MS = 300_000.0
LONG_UP = "9007199254740993"
LONG_DOWN = "90071992547409931337"
V1_UP = "111111111111111111"
V1_DOWN = "222222222222222222"
V2_UP = "333333333333333333"
V2_DOWN = "444444444444444444"


def _end_iso() -> str:
    end_ms = NOW_MS + 60_000.0
    return datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _events(market: dict) -> list:
    body = {
        "slug": "btc-updown-5m-test",
        "question": "Bitcoin Up or Down",
        "conditionId": "0xabc",
        "endDate": _end_iso(),
        **market,
    }
    return [{"markets": [body]}]


def _parse(market: dict):
    return parse_live_market(_events(market), NOW_MS, WINDOW_MS)


def test_v1_uses_clob_token_ids():
    parsed = _parse({
        "version": "v1",
        "outcomes": '["Up","Down"]',
        "clobTokenIds": json_ids(V1_UP, V1_DOWN),
    })
    assert parsed is not None
    assert parsed["version"] == "v1"
    assert parsed["up_token"] == V1_UP
    assert parsed["down_token"] == V1_DOWN


def test_v2_uses_position_ids_only():
    parsed = _parse({
        "version": "v2",
        "outcomes": ["Up", "Down"],
        "positionIds": [V2_UP, V2_DOWN],
    })
    assert parsed is not None
    assert parsed["version"] == "v2"
    assert parsed["up_token"] == V2_UP
    assert parsed["down_token"] == V2_DOWN


def test_v2_with_both_fields_selects_position_ids():
    parsed = _parse({
        "version": "v2",
        "outcomes": '["Up","Down"]',
        "clobTokenIds": json_ids(V1_UP, V1_DOWN),
        "positionIds": [V2_UP, V2_DOWN],
    })
    assert parsed["up_token"] == V2_UP
    assert parsed["down_token"] == V2_DOWN


def test_v1_with_both_fields_selects_clob_token_ids():
    parsed = _parse({
        "version": "v1",
        "outcomes": ["Up", "Down"],
        "clobTokenIds": json_ids(V1_UP, V1_DOWN),
        "positionIds": [V2_UP, V2_DOWN],
    })
    assert parsed["up_token"] == V1_UP
    assert parsed["down_token"] == V1_DOWN


def test_reversed_outcomes_down_then_up():
    parsed = _parse({
        "version": "v2",
        "outcomes": '["Down","Up"]',
        "positionIds": [LONG_DOWN, LONG_UP],
    })
    assert parsed["up_token"] == LONG_UP
    assert parsed["down_token"] == LONG_DOWN


def test_missing_and_unknown_version():
    ids, reason = select_trading_ids({
        "outcomes": ["Up", "Down"],
        "clobTokenIds": json_ids(V1_UP, V1_DOWN),
    })
    assert ids is None
    assert "version" in reason
    assert _parse({
        "outcomes": ["Up", "Down"],
        "clobTokenIds": json_ids(V1_UP, V1_DOWN),
    }) is None

    ids, reason = select_trading_ids({
        "version": "v3",
        "outcomes": ["Up", "Down"],
        "positionIds": [V2_UP, V2_DOWN],
    })
    assert ids is None
    assert "unsupported" in reason


def test_bad_outcomes_and_ids():
    assert select_trading_ids({
        "version": "v1",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": json_ids(V1_UP, V1_DOWN),
    })[0] is None
    assert select_trading_ids({
        "version": "v1",
        "outcomes": '["Up","Up"]',
        "clobTokenIds": json_ids(V1_UP, V1_DOWN),
    })[0] is None
    assert select_trading_ids({
        "version": "v2",
        "outcomes": ["Up"],
        "positionIds": [V2_UP, V2_DOWN],
    })[0] is None
    assert select_trading_ids({
        "version": "v2",
        "outcomes": ["Up", "Down"],
        "positionIds": [V2_UP, "not-a-number"],
    })[0] is None
    assert select_trading_ids({
        "version": "v2",
        "outcomes": ["Up", "Down"],
        "positionIds": [V2_UP, 12.5],
    })[0] is None
    assert select_trading_ids({
        "version": "v1",
        "outcomes": ["Up", "Down"],
    })[0] is None
    assert select_trading_ids({
        "version": "v2",
        "outcomes": ["Up", "Down"],
        "clobTokenIds": json_ids(V1_UP, V1_DOWN),
    })[0] is None


def test_long_ids_keep_decimal_precision():
    parsed = _parse({
        "version": "v1",
        "outcomes": '["Up","Down"]',
        "clobTokenIds": json_ids(LONG_UP, LONG_DOWN),
    })
    assert parsed["up_token"] == LONG_UP
    assert parsed["down_token"] == LONG_DOWN
    assert parsed["up_token"] != str(float(LONG_UP))


def test_subscribe_and_order_token_use_selected_ids():
    market = _parse({
        "version": "v2",
        "outcomes": '["Down","Up"]',
        "positionIds": [LONG_DOWN, LONG_UP],
        "clobTokenIds": json_ids(V1_UP, V1_DOWN),
    })
    payload = clob_market_subscribe_payload(market)
    assert payload["type"] == "market"
    assert payload["assets_ids"] == [LONG_UP, LONG_DOWN]

    pipe = SimpleNamespace(polymarket=SimpleNamespace(market=market))
    from backend.feeds.polymarket import token_id_for_side as order_token

    assert order_token(pipe.polymarket.market, "UP") == LONG_UP
    assert order_token(pipe.polymarket.market, "DOWN") == LONG_DOWN
    assert token_id_for_side(market, "up") == LONG_UP
    assert token_id_for_side({}, "UP") is None


def test_duplicate_trading_ids_rejected():
    same = "9007199254740993"
    ids, reason = select_trading_ids({
        "version": "v2",
        "outcomes": ["Up", "Down"],
        "positionIds": [same, same],
    })
    assert ids is None
    assert "same trading ID" in reason

    padded, reason = select_trading_ids({
        "version": "v1",
        "outcomes": '["Up","Down"]',
        "clobTokenIds": json_ids("0" + same, same),
    })
    assert padded is None
    assert "same trading ID" in reason


def test_duplicate_ids_never_enter_subscribe_or_token_side():
    parsed = _parse({
        "version": "v2",
        "outcomes": ["Down", "Up"],
        "positionIds": ["0123", "123"],
    })
    assert parsed is None
    feed = SimpleNamespace(market=None, _token_side={})
    if parsed is not None:
        feed.market = parsed
        feed._token_side = {parsed["up_token"]: "UP", parsed["down_token"]: "DOWN"}
        payload = clob_market_subscribe_payload(parsed)
    else:
        payload = None
    assert feed.market is None
    assert feed._token_side == {}
    assert payload is None


def test_token_id_for_side_rejects_unknown_direction():
    market = {
        "up_token": LONG_UP,
        "down_token": LONG_DOWN,
    }
    assert token_id_for_side(market, " UP ") == LONG_UP
    assert token_id_for_side(market, "down") == LONG_DOWN
    assert token_id_for_side(market, "") is None
    assert token_id_for_side(market, None) is None
    assert token_id_for_side(market, "YES") is None
    assert token_id_for_side(market, 1) is None
    assert token_id_for_side(None, "UP") is None


def _load_token_for_side():
    """Load clob_executor.token_for_side without importing storage or opening SQLite."""
    path = Path(__file__).resolve().parents[1] / "backend" / "engine" / "clob_executor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "token_for_side"
    )
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    ns: dict = {}
    exec(compile(module, str(path), "exec"), ns)
    return ns["token_for_side"]


def test_token_for_side_adapter_matches_subscribe_ids():
    token_for_side = _load_token_for_side()
    market = _parse({
        "version": "v2",
        "outcomes": '["Down","Up"]',
        "positionIds": [LONG_DOWN, LONG_UP],
    })
    assert market is not None
    subscribe_ids = clob_market_subscribe_payload(market)["assets_ids"]
    pipe = SimpleNamespace(polymarket=SimpleNamespace(market=market))
    up = token_for_side(pipe, " UP")
    down = token_for_side(pipe, "down ")
    assert up == LONG_UP
    assert down == LONG_DOWN
    assert set(subscribe_ids) == {up, down}
    assert token_for_side(pipe, "YES") is None
    assert token_for_side(pipe, "") is None
    assert token_for_side(pipe, None) is None
    empty = SimpleNamespace(polymarket=None)
    assert token_for_side(empty, "UP") is None
    assert token_for_side(SimpleNamespace(), "DOWN") is None


def json_ids(up: str, down: str) -> str:
    return f'["{up}","{down}"]'
