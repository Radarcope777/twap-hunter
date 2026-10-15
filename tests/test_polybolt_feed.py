from backend.feeds.rtds import parse_frame, polybolt_symbol, subscribe_frame


def test_symbol():
    assert polybolt_symbol("btc/usd") == "btcusd"
    assert polybolt_symbol("XRP/USD") == "xrpusd"


def test_subscribe_uses_object_filter():
    frame = subscribe_frame("btc/usd")
    assert '"op": "subscribe"' in frame or '"op":"subscribe"' in frame.replace(" ", "")
    assert "price.crypto.twap" in frame
    assert "window_seconds" in frame
    assert "btc/usd" not in frame
    assert "btcusd" in frame


def test_spot_update_and_snapshot():
    update = (
        '{"v":1,"channel":"price.crypto","seq":1,"ts":1000,'
        '"payload":{"symbol":"btcusd","value":64120.7,'
        '"full_accuracy_value":"64120.70000000","timestamp":1000,"source":"chainlink"}}'
    )
    kind, symbol, points = parse_frame(update)
    assert kind == "chainlink"
    assert symbol == "btcusd"
    assert points == [(1000.0, 64120.7)]

    snap = (
        '{"v":1,"channel":"price.crypto","seq":2,"ts":2000,"snapshot":true,'
        '"payload":{"symbol":"btcusd","source":"chainlink","data":['
        '{"value":1,"full_accuracy_value":"1.5","timestamp":1500},'
        '{"value":2,"full_accuracy_value":"2.5","timestamp":1600}]}}'
    )
    kind, symbol, points = parse_frame(snap)
    assert kind == "backfill"
    assert points == [(1500.0, 1.5), (1600.0, 2.5)]


def test_twap_update_prefers_exact_decimal():
    raw = (
        '{"v":1,"channel":"price.crypto.twap","seq":3,"ts":3000,'
        '"payload":{"symbol":"btcusd","value":64120.7,'
        '"full_accuracy_value":"64120.70000001","timestamp":3000,'
        '"window_seconds":60,"source":"chainlink"}}'
    )
    kind, symbol, points = parse_frame(raw)
    assert kind == "twap60"
    assert symbol == "btcusd"
    assert points[0][0] == 3000.0
    assert abs(points[0][1] - 64120.70000001) < 1e-6


def test_control_frames_are_ignored():
    assert parse_frame('{"op":"authed"}') is None
    assert parse_frame('{"op":"error","code":"auth_required"}') is None
