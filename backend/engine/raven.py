"""Streaming Python port of the RaVen indicator's diagnostic core.

The TradingView source is kept in ``RaVen_V2.3.17_EN.pine`` as the visual
reference.  This module deliberately owns only calculations and state; it
does not place orders and it does not make any assumptions about a strategy.

RaVen V2.3.17 evaluates 15-second candles.  The engine therefore aggregates
the existing Binance ticks into 15-second candles and exposes a compact
snapshot for the Raven Lab page.  The calculations are dependency-free so the
live terminal does not need pandas/numpy just to render the diagnostic view.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from typing import Iterable


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _last(values: Iterable[float | None]) -> float | None:
    for value in reversed(list(values)):
        if value is not None:
            return value
    return None


def _round(value: float | None, digits: int = 6) -> float | None:
    value = _finite(value)
    return None if value is None else round(value, digits)


def _sma(values: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        if all(v is not None for v in window):
            out[i] = sum(float(v) for v in window) / period
    return out


def _ema(values: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    alpha = 2.0 / (period + 1.0)
    previous: float | None = None
    for i, value in enumerate(values):
        if value is None:
            continue
        if previous is None:
            previous = float(value)
        else:
            previous = alpha * float(value) + (1.0 - alpha) * previous
        out[i] = previous
    return out


def _rma(values: list[float | None], period: int) -> list[float | None]:
    """Wilder's moving average, used by Pine's RSI/ATR/ADX."""
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        return out
    seed: list[float] = []
    previous: float | None = None
    for i, value in enumerate(values):
        if value is None:
            seed.clear()
            previous = None
            continue
        number = float(value)
        if previous is None:
            seed.append(number)
            if len(seed) < period:
                continue
            previous = sum(seed[-period:]) / period
        else:
            previous = (previous * (period - 1) + number) / period
        out[i] = previous
    return out


def _rsi(closes: list[float], period: int) -> list[float | None]:
    gains: list[float | None] = [None]
    losses: list[float | None] = [None]
    for before, after in zip(closes, closes[1:]):
        change = after - before
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = _rma(gains, period)
    avg_loss = _rma(losses, period)
    out: list[float | None] = [None] * len(closes)
    for i, (gain, loss) in enumerate(zip(avg_gain, avg_loss)):
        if gain is None or loss is None:
            continue
        if loss == 0:
            out[i] = 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + gain / loss)
    return out


def _true_range(candles: list["Candle"]) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    for i, candle in enumerate(candles):
        if i == 0:
            out[i] = candle.high - candle.low
        else:
            previous = candles[i - 1].close
            out[i] = max(candle.high - candle.low, abs(candle.high - previous), abs(candle.low - previous))
    return out


def _atr(candles: list["Candle"], period: int) -> list[float | None]:
    return _rma(_true_range(candles), period)


def _adx(candles: list["Candle"], period: int = 7) -> tuple[list[float | None], list[float | None], list[float | None]]:
    tr = _true_range(candles)
    plus_dm: list[float | None] = [None]
    minus_dm: list[float | None] = [None]
    for previous, candle in zip(candles, candles[1:]):
        up = candle.high - previous.high
        down = previous.low - candle.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    tr_rma = _rma(tr, period)
    plus_rma = _rma(plus_dm, period)
    minus_rma = _rma(minus_dm, period)
    di_plus: list[float | None] = [None] * len(candles)
    di_minus: list[float | None] = [None] * len(candles)
    dx: list[float | None] = [None] * len(candles)
    for i, (trv, pv, mv) in enumerate(zip(tr_rma, plus_rma, minus_rma)):
        if trv is None or trv == 0 or pv is None or mv is None:
            continue
        di_plus[i] = 100.0 * pv / trv
        di_minus[i] = 100.0 * mv / trv
        total = di_plus[i] + di_minus[i]
        dx[i] = 100.0 * abs(di_plus[i] - di_minus[i]) / total if total else 0.0
    return di_plus, di_minus, _rma(dx, period)


def _direction(value: float | None, threshold: float = 0.0) -> int:
    if value is None:
        return 0
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def _aggregate(candles: list["Candle"], timeframe_ms: int) -> list["Candle"]:
    result: list[Candle] = []
    for candle in candles:
        bucket = (candle.ts // timeframe_ms) * timeframe_ms
        if not result or result[-1].ts != bucket:
            result.append(Candle(bucket, candle.open, candle.high, candle.low, candle.close, candle.volume, candle.buy_volume, candle.sell_volume))
        else:
            current = result[-1]
            current.high = max(current.high, candle.high)
            current.low = min(current.low, candle.low)
            current.close = candle.close
            current.volume += candle.volume
            current.buy_volume += candle.buy_volume
            current.sell_volume += candle.sell_volume
    return result


def _linreg(values: list[float], period: int) -> tuple[float | None, float | None, float | None]:
    if len(values) < period:
        return None, None, None
    ys = values[-period:]
    xs = list(range(period))
    x_mean = (period - 1) / 2.0
    y_mean = sum(ys) / period
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return None, None, None
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / den
    current = y_mean + slope * (period - 1)
    projected = y_mean + slope * (period - 2)
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (y_mean + slope * (x - x_mean))) ** 2 for x, y in zip(xs, ys))
    r2 = max(0.0, min(1.0, 1.0 - ss_res / ss_tot)) if ss_tot else 0.0
    return current, projected, r2


def _stddev(values: list[float], period: int) -> float | None:
    """Return the population standard deviation used by Bollinger-style checks."""
    if len(values) < period or period <= 0:
        return None
    window = values[-period:]
    mean = sum(window) / period
    return math.sqrt(sum((value - mean) ** 2 for value in window) / period)


def _supertrend(candles: list["Candle"], period: int = 7, factor: float = 2.0) -> tuple[int, float | None]:
    """Calculate a compact Pine-compatible SuperTrend direction and line."""
    if len(candles) < period + 2:
        return 0, None
    atr_values = _atr(candles, period)
    upper: list[float | None] = [None] * len(candles)
    lower: list[float | None] = [None] * len(candles)
    direction = 1
    line: float | None = None
    for index, candle in enumerate(candles):
        atr_value = atr_values[index]
        if atr_value is None:
            continue
        midpoint = (candle.high + candle.low) / 2.0
        basic_upper = midpoint + factor * atr_value
        basic_lower = midpoint - factor * atr_value
        if index == 0 or upper[index - 1] is None or lower[index - 1] is None:
            upper[index] = basic_upper
            lower[index] = basic_lower
        else:
            previous_close = candles[index - 1].close
            upper[index] = basic_upper if basic_upper < upper[index - 1] or previous_close > upper[index - 1] else upper[index - 1]
            lower[index] = basic_lower if basic_lower > lower[index - 1] or previous_close < lower[index - 1] else lower[index - 1]
        if index > 0 and upper[index - 1] is not None and lower[index - 1] is not None:
            if direction == -1 and candle.close > upper[index]:
                direction = 1
            elif direction == 1 and candle.close < lower[index]:
                direction = -1
        line = lower[index] if direction == 1 else upper[index]
    return direction, line


def _psar(candles: list["Candle"], step: float = 0.02, maximum: float = 0.2) -> tuple[int, float | None]:
    """Calculate a deterministic Parabolic SAR direction without third-party dependencies."""
    if len(candles) < 3:
        return 0, None
    rising = True
    sar = candles[0].low
    extreme = candles[0].high
    acceleration = step
    for index in range(1, len(candles)):
        candle = candles[index]
        previous = candles[index - 1]
        sar = sar + acceleration * (extreme - sar)
        if rising:
            sar = min(sar, previous.low)
            if index > 1:
                sar = min(sar, candles[index - 2].low)
            if candle.low < sar:
                rising = False
                sar = extreme
                extreme = candle.low
                acceleration = step
            elif candle.high > extreme:
                extreme = candle.high
                acceleration = min(maximum, acceleration + step)
        else:
            sar = max(sar, previous.high)
            if index > 1:
                sar = max(sar, candles[index - 2].high)
            if candle.high > sar:
                rising = True
                sar = extreme
                extreme = candle.high
                acceleration = step
            elif candle.low < extreme:
                extreme = candle.low
                acceleration = min(maximum, acceleration + step)
    return (1 if rising else -1), sar


def _normal_cdf(value: float) -> float:
    """Standard normal CDF used by the Pine Black-Scholes approximation."""
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _safe_ratio(numerator: float, denominator: float, fallback: float = 0.0) -> float:
    return numerator / denominator if abs(denominator) > 1e-12 else fallback


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0

    def as_row(self) -> list[float | int]:
        return [self.ts, self.open, self.high, self.low, self.close, self.volume]


@dataclass
class RavenSettings:
    """Editable settings mirrored from the Pine input groups."""

    preset_mode: str = "Polymarket"
    round_mode: str = "15 minutes round"
    custom_duration_minutes: int = 15
    custom_open_price: float = 0.0
    custom_open_expire_min: float = 2.0
    use_official_ptb: bool = True
    require_official_ptb: bool = True
    main_panel_pos: str = "lower left"
    detail_panel_pos: str = "lower right"
    pm_panel_pos: str = "middle to lower"
    pm_bt_panel_pos: str = "middle left"
    panel_mode: str = "Standard"
    show_main_panel: bool = True
    show_detail_panel: bool = False
    detail_section: str = "All"
    show_open_line: bool = True
    show_pm_panel: bool = True
    show_pm_bt_panel: bool = False
    use_custom_duration: bool = False
    time_window_mode: str = "Customize"
    time_preset: str = "1 second-10 minutes"
    custom_window_start_min: float = 0.0
    custom_window_end_min: float = 10.0
    quality_threshold: float = 78.0
    adx_min: float = 12.0
    trend_agree_min: int = 3
    momentum_agree_min: int = 3
    black_swan_max: float = 42.0
    distance_atr_min: float = 0.08
    velocity_max: float = 18.0
    volume_ratio_min: float = 0.1
    volatility_ratio_max: float = 5.0
    rsi_min: float = 10.0
    rsi_max: float = 90.0
    trend_bars_min: int = 2
    use_htf_alignment: bool = True
    use_stability_checks: bool = True
    use_distance_stability: bool = True
    use_enhanced_indicators: bool = True
    signal_confirm_bars: int = 2
    signal_confirm_window: int = 3
    use_signal_confirm: bool = True
    use_flex_mode: bool = True
    flex_quality_bonus: float = 5.0
    use_smart_cooldown: bool = True
    cooldown_bars: int = 12
    use_vol_adapt: bool = True
    use_late_penalty: bool = True
    use_mtf: bool = True
    use_obv: bool = True
    use_bb_state: bool = True
    use_vwap_slope: bool = True
    use_keltner: bool = True
    use_volume_delta: bool = True
    use_mfi: bool = True
    use_ha_confirm: bool = True
    use_ichimoku: bool = True
    use_linreg: bool = True
    use_psar: bool = True
    use_cci: bool = True
    use_cmf: bool = True


class _LegacyRavenEnginePrototype:
    """Legacy prototype retained as a reference while V2 owns the import path."""

    def __init__(self, *, bar_ms: int = 15_000, round_ms: int = 900_000, max_bars: int = 3_000) -> None:
        self.bar_ms = bar_ms
        self.round_ms = round_ms
        self.candles: deque[Candle] = deque(maxlen=max_bars)
        self.quote: dict | None = None
        self.ptb: float | None = None
        self.ptb_source: str | None = None
        self._dirty = True
        self._cached: dict = {"ready": False, "status": "WARMING"}
        self.locked_round: int | None = None
        self.locked_direction = 0
        self._last_condition_round: int | None = None

    def on_tick(self, ts_ms: float, price: float, qty: float, side: str | int) -> None:
        ts = int(ts_ms // self.bar_ms * self.bar_ms)
        price = float(price)
        qty = max(0.0, float(qty))
        buy = side in (1, "buy", "BUY", True)
        if not self.candles or self.candles[-1].ts != ts:
            self.candles.append(Candle(ts, price, price, price, price, qty, qty if buy else 0.0, 0.0 if buy else qty))
        else:
            candle = self.candles[-1]
            candle.high = max(candle.high, price)
            candle.low = min(candle.low, price)
            candle.close = price
            candle.volume += qty
            if buy:
                candle.buy_volume += qty
            else:
                candle.sell_volume += qty
        self._dirty = True

    def on_quote(self, quote: dict | None) -> None:
        self.quote = dict(quote) if quote else None
        self._dirty = True

    def _pm_direction(self) -> int | None:
        if not self.quote:
            return None
        up_bid = self.quote.get("up_bid")
        up_ask = self.quote.get("up_ask")
        down_bid = self.quote.get("down_bid")
        down_ask = self.quote.get("down_ask")
        if up_bid is None or up_ask is None:
            return None
        up = (float(up_bid) + float(up_ask)) / 2.0
        down = None
        if down_bid is not None and down_ask is not None:
            down = (float(down_bid) + float(down_ask)) / 2.0
        if down is None:
            down = 100.0 - up
        return 1 if up >= down else -1

    def _calculate(self, now_ms: int | None = None) -> dict:
        candles = list(self.candles)
        if len(candles) < 40:
            return {
                "ready": False,
                "status": "WARMING",
                "bars": len(candles),
                "required_bars": 40,
                "bias": None,
                "signal": None,
            }

        now = int(now_ms or candles[-1].ts + self.bar_ms)
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]
        round_id = candles[-1].ts // self.round_ms
        round_start = round_id * self.round_ms
        current_round = [c for c in candles if c.ts >= round_start]
        opening = current_round[0].open if current_round else candles[-1].open
        close = closes[-1]
        price_diff = close - opening
        current_direction = 1 if price_diff > 0 else -1

        atr = _atr(candles, 14)
        atr_value = _last(atr)
        atr_sma = _last(_sma(atr, 50))
        atr_safe = atr_value or _last(_sma([abs(closes[i] - closes[i - 1]) if i else None for i in range(len(closes))], 14)) or 0.0
        volatility_ratio = atr_value / atr_sma if atr_value and atr_sma else 1.0
        price_diff_atr = abs(price_diff) / atr_safe if atr_safe else 0.0

        ema_fast = _last(_ema(closes, 5))
        ema_slow = _last(_ema(closes, 34))
        rsi = _last(_rsi(closes, 7))
        rsi_slow = _last(_rsi(closes, 14))
        momentum_short = closes[-1] - closes[-4]
        momentum_mid = closes[-1] - closes[-9]
        abs_change = [None] + [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
        price_velocity = _last(_sma(abs_change, 3))
        velocity_ratio = price_velocity / atr_safe * 10.0 if price_velocity is not None and atr_safe else 0.0

        di_plus, di_minus, adx = _adx(candles, 7)
        di_p = _last(di_plus)
        di_m = _last(di_minus)
        adx_value = _last(adx) or 0.0
        adx_direction = 1 if di_p is not None and di_m is not None and di_p > di_m else -1
        macd_line = _ema(closes, 12)
        macd_slow = _ema(closes, 26)
        macd_hist = [a - b if a is not None and b is not None else None for a, b in zip(macd_line, macd_slow)]
        macd_signal = _ema(macd_hist, 9)
        macd_value = _last([a - b if a is not None and b is not None else None for a, b in zip(macd_hist, macd_signal)]) or 0.0

        typical = [(c.high + c.low + c.close) / 3.0 for c in candles]
        vwap = sum(p * v for p, v in zip(typical, volumes)) / sum(volumes) if sum(volumes) else close
        vwap_prev = sum(typical[-4:-1][i] * volumes[-4:-1][i] for i in range(len(typical[-4:-1]))) / sum(volumes[-4:-1]) if sum(volumes[-4:-1]) else vwap
        vwap_slope = vwap - vwap_prev
        stoch_low = min(lows[-7:])
        stoch_high = max(highs[-7:])
        stoch = (close - stoch_low) / (stoch_high - stoch_low) * 100.0 if stoch_high != stoch_low else 50.0
        williams = (stoch - 100.0)

        vol_sma = _last(_sma(volumes, 20)) or 0.0
        vol_ratio = volumes[-1] / vol_sma if vol_sma else 1.0
        delta_raw = [c.buy_volume - c.sell_volume for c in candles]
        delta = _last(_sma(delta_raw, 5)) or 0.0
        delta_direction = _direction(delta)

        # MFI and CMF use the same 14/20 periods as the Pine source.
        money_flow = [p * c.volume for p, c in zip(typical, candles)]
        pos_flow = [money_flow[i] if i == 0 or typical[i] >= typical[i - 1] else 0.0 for i in range(len(candles))]
        neg_flow = [money_flow[i] if i == 0 or typical[i] < typical[i - 1] else 0.0 for i in range(len(candles))]
        pos = sum(pos_flow[-14:])
        neg = sum(neg_flow[-14:])
        mfi = 100.0 if neg == 0 else 100.0 - 100.0 / (1.0 + pos / neg)
        cmf_num = sum((((c.close - c.low) - (c.high - c.close)) / max(c.high - c.low, 1e-12)) * c.volume for c in candles[-20:])
        cmf_den = sum(c.volume for c in candles[-20:])
        cmf = cmf_num / cmf_den if cmf_den else 0.0

        # Lightweight equivalents for Pine's additional trend dimensions.
        supertrend_direction = 1 if close >= (_last(_ema(closes, 10)) or close) else -1
        ha_close = (candles[-1].open + candles[-1].high + candles[-1].low + candles[-1].close) / 4.0
        ha_open = (candles[-1].open + candles[-1].close) / 2.0
        ha_direction = 1 if ha_close > ha_open else -1
        ichi_top = max((max(highs[-9:]) + min(lows[-9:])) / 2.0, (max(highs[-26:]) + min(lows[-26:])) / 2.0)
        ichi_bottom = min((max(highs[-9:]) + min(lows[-9:])) / 2.0, (max(highs[-26:]) + min(lows[-26:])) / 2.0)
        ichi_direction = 1 if close > ichi_top else -1 if close < ichi_bottom else 0
        linreg_current, linreg_projected, linreg_r2 = _linreg(closes, 20)
        linreg_direction = _direction((linreg_projected or 0.0) - (linreg_current or 0.0))
        ema_direction = 1 if ema_fast is not None and ema_slow is not None and ema_fast > ema_slow else -1
        macd_direction = _direction(macd_value)
        vwap_direction = 1 if close > vwap else -1
        vwap_slope_direction = _direction(vwap_slope)
        rsi_direction = 1 if rsi is not None and rsi > 52 else -1 if rsi is not None and rsi < 48 else 0
        momentum_direction = 1 if momentum_short > 0 and momentum_mid > 0 else -1 if momentum_short < 0 and momentum_mid < 0 else 0
        stoch_direction = 1 if stoch > 50 else -1
        williams_direction = 1 if williams > -50 else -1
        mfi_direction = 1 if mfi > 55 else -1 if mfi < 45 else 0
        cmf_direction = 1 if cmf > 0.05 else -1 if cmf < -0.05 else 0

        trend_dimensions = [supertrend_direction, ema_direction, macd_direction, vwap_direction, adx_direction, vwap_slope_direction, ha_direction, ichi_direction, linreg_direction]
        trend_agree = sum(direction == current_direction for direction in trend_dimensions)
        momentum_dimensions = [rsi_direction, momentum_direction, stoch_direction, williams_direction, delta_direction, mfi_direction, cmf_direction]
        momentum_agree = sum(direction == current_direction for direction in momentum_dimensions)

        mtf_dirs: dict[str, int] = {}
        for name, timeframe in (("1m", 60_000), ("3m", 180_000), ("5m", 300_000)):
            higher = _aggregate(candles, timeframe)
            higher_closes = [c.close for c in higher]
            if len(higher_closes) >= 21:
                fast = _last(_ema(higher_closes, 8))
                slow = _last(_ema(higher_closes, 21))
                higher_rsi = _last(_rsi(higher_closes, 14))
                mtf_dirs[name] = 1 if fast is not None and slow is not None and fast > slow else -1
                if higher_rsi is not None and 45 <= higher_rsi <= 55:
                    mtf_dirs[name] = mtf_dirs[name]
            else:
                mtf_dirs[name] = 0
        mtf_agree = sum(value == current_direction for value in mtf_dirs.values())

        pm_direction = self._pm_direction()
        pm_agree = None if pm_direction is None else pm_direction == current_direction
        time_remaining = max(0.0, round_start + self.round_ms - now) / 1000.0
        in_time_window = 1.0 <= time_remaining <= 600.0
        blockers: list[str] = []
        if not in_time_window:
            blockers.append("time window")
        if adx_value < 12 or adx_direction != current_direction:
            blockers.append("ADX")
        if trend_agree < 3:
            blockers.append("trend consensus")
        if momentum_agree < 2:
            blockers.append("momentum consensus")
        if price_diff_atr < 0.10:
            blockers.append("distance")
        if pm_agree is False:
            blockers.append("PM direction")
        if pm_agree is None:
            blockers.append("PM quote unavailable")

        adx_score = max(0.0, min(20.0, (adx_value - 8.0) / 22.0 * 20.0)) if adx_direction == current_direction else 0.0
        dist_score = max(0.0, min(10.0, price_diff_atr / 0.3 * 10.0))
        trend_score = 25.0 if trend_agree >= 9 else 22.0 if trend_agree >= 8 else 18.0 if trend_agree >= 7 else 13.0 if trend_agree >= 5 else 8.0 if trend_agree >= 2 else 3.0
        momentum_score = 20.0 if momentum_agree >= 7 else 17.0 if momentum_agree >= 6 else 13.0 if momentum_agree >= 5 else 8.0 if momentum_agree >= 2 else 4.0 if momentum_agree >= 1 else 0.0
        risk_score = 7.5  # neutral until the Pine black-swan model is ported
        quality = adx_score + dist_score + trend_score + momentum_score + risk_score
        quality += 5.0 if supertrend_direction == current_direction else 0.0
        quality += 5.0 if vwap_direction == current_direction else 0.0
        quality += 5.0 if volatility_ratio < 1.2 else 0.0
        quality += 5.0 if trend_agree >= 8 else 0.0
        quality += 20.0 if mtf_agree == 3 else 12.0 if mtf_agree == 2 else 5.0 if mtf_agree == 1 else -5.0
        quality += 3.0 if delta_direction == current_direction else 0.0
        quality += 3.0 if vwap_slope_direction == current_direction else 0.0
        quality = max(0.0, min(100.0, quality))

        core_ready = in_time_window and adx_value >= 12 and adx_direction == current_direction and trend_agree >= 3 and momentum_agree >= 2 and price_diff_atr >= 0.10 and 10 <= (rsi or 50) <= 90 and volatility_ratio <= 6.0 and velocity_ratio <= 20.0
        conditions_met = core_ready and quality >= 78.0 and (pm_agree is not False)
        final_signal = current_direction if conditions_met and pm_agree is True else None
        if final_signal is not None and self.locked_round != round_id:
            self.locked_round = round_id
            self.locked_direction = final_signal
        locked = self.locked_round == round_id and self.locked_direction != 0

        return {
            "ready": True,
            "status": "LOCKED" if locked else "SIGNAL" if final_signal else "BIAS",
            "bars": len(candles),
            "bar_ms": self.bar_ms,
            "round_ms": self.round_ms,
            "round_id": round_id,
            "round_start_ms": round_start,
            "round_end_ms": round_start + self.round_ms,
            "time_remaining_s": round(time_remaining, 1),
            "opening_price": _round(opening, 8),
            "close": _round(close, 8),
            "bias": current_direction,
            "signal": self.locked_direction if locked else final_signal,
            "pm_direction": pm_direction,
            "pm_agree": pm_agree,
            "quality": round(quality, 1),
            "threshold": 78.0,
            "trend_agree": trend_agree,
            "trend_total": len(trend_dimensions),
            "momentum_agree": momentum_agree,
            "momentum_total": len(momentum_dimensions),
            "mtf_agree": mtf_agree,
            "mtf_total": len(mtf_dirs),
            "adx": _round(adx_value, 2),
            "rsi": _round(rsi, 2),
            "atr": _round(atr_safe, 8),
            "volatility_ratio": _round(volatility_ratio, 2),
            "distance_atr": _round(price_diff_atr, 3),
            "velocity_ratio": _round(velocity_ratio, 2),
            "blockers": blockers,
            "components": {
                "ema": ema_direction,
                "macd": macd_direction,
                "vwap": vwap_direction,
                "adx": adx_direction,
                "supertrend": supertrend_direction,
                "rsi": rsi_direction,
                "momentum": momentum_direction,
                "stoch": stoch_direction,
                "williams": williams_direction,
                "volume_delta": delta_direction,
                "mfi": mfi_direction,
                "cmf": cmf_direction,
                "mtf": mtf_dirs,
            },
        }

    def snapshot(self, now_ms: int | None = None) -> dict:
        if self._dirty:
            self._cached = self._calculate(now_ms)
            self._dirty = False
        return self._cached

    def history_payload(self) -> dict:
        return {
            "bar_ms": self.bar_ms,
            "round_ms": self.round_ms,
            "candles": [c.as_row() for c in list(self.candles)[-900:]],
            "snapshot": self.snapshot(),
        }


class RavenEngineV2:
    """RaVen calculation/state engine with Pine-style preview, confirmation and lock states."""

    _WINDOW_PRESETS = {
        "1 second-5 minutes": (1.0, 5.0),
        "1 second-8 minutes": (1.0, 8.0),
        "1 second-10 minutes": (1.0, 10.0),
        "2 minutes-10 minutes": (2.0, 10.0),
        "2 minutes-12 minutes": (2.0, 12.0),
        "1 second-13 minutes": (1.0, 13.0),
    }

    def __init__(
        self,
        *,
        bar_ms: int = 15_000,
        round_ms: int = 900_000,
        max_bars: int = 3_000,
        settings: RavenSettings | None = None,
    ) -> None:
        self.bar_ms = int(bar_ms)
        self.round_ms = int(round_ms)
        self.max_bars = max_bars
        self.settings = settings or RavenSettings()
        self.candles: deque[Candle] = deque(maxlen=max_bars)
        self.quote: dict | None = None
        self.ptb: float | None = None
        self.ptb_source: str | None = None
        self._dirty = True
        self._cached: dict = {"ready": False, "status": "WARMING"}

        # These fields mirror Pine's var state and are reset only at a new round.
        self._active_round_id: int | None = None
        self._confirm_direction = 0
        self._confirm_history: deque[bool] = deque(maxlen=7)
        self.locked_round: int | None = None
        self.locked_direction = 0
        self.locked_score = 0.0
        self.locked_at_ms: int | None = None
        self._last_locked_bar_ts: int | None = None
        self.round_had_signal = False
        self.round_signal_direction = 0
        self.round_signal_score = 0.0
        self.round_signal_time_min: float | None = None

        # Backtest counters are intentionally signal-only, matching Pine's W/L panel.
        self.total_rounds = 0
        self.total_signals = 0
        self.total_wins = 0
        self.up_signals = 0
        self.up_wins = 0
        self.down_signals = 0
        self.down_wins = 0
        self.high_score_signals = 0
        self.high_score_wins = 0
        self.low_score_signals = 0
        self.low_score_wins = 0
        self.current_win_streak = 0
        self.current_loss_streak = 0
        self.max_win_streak = 0
        self.max_loss_streak = 0
        self.history_10: deque[dict] = deque(maxlen=10)
        self.history_100: deque[dict] = deque(maxlen=100)

    def _round_duration_ms(self) -> int:
        """Resolve the Pine round selector into milliseconds."""
        if self.settings.round_mode == "5 minutes round":
            return 5 * 60_000
        if self.settings.round_mode == "1 hour game":
            return 60 * 60_000
        if self.settings.round_mode == "Customize":
            return max(1, self.settings.custom_duration_minutes) * 60_000
        return 15 * 60_000

    def _window_seconds(self) -> tuple[float, float]:
        """Resolve the Pine default/custom signal window."""
        if self.settings.time_window_mode == "Default gear":
            start, end = self._WINDOW_PRESETS.get(self.settings.time_preset, (1.0, 10.0))
        else:
            start, end = self.settings.custom_window_start_min, self.settings.custom_window_end_min
        return max(0.0, start * 60.0), max(max(0.0, start * 60.0), end * 60.0)

    def _reset_round(self, round_id: int) -> None:
        """Reset all per-round lock and confirmation variables."""
        self._active_round_id = round_id
        self._confirm_direction = 0
        self._confirm_history.clear()
        self.locked_round = None
        self.locked_direction = 0
        self.locked_score = 0.0
        self.locked_at_ms = None
        self._last_locked_bar_ts = None
        self.round_had_signal = False
        self.round_signal_direction = 0
        self.round_signal_score = 0.0
        self.round_signal_time_min = None

    def on_tick(self, ts_ms: float, price: float, qty: float, side: str | int) -> None:
        """Aggregate a Binance trade and finalize the preceding 15-second candle."""
        ts = int(ts_ms // self.bar_ms * self.bar_ms)
        price = float(price)
        qty = max(0.0, float(qty))
        buy = side in (1, "buy", "BUY", True)
        if self.candles and self.candles[-1].ts != ts:
            previous = list(self.candles)
            closed = self._calculate(now_ms=previous[-1].ts + self.bar_ms, candles=previous)
            self._accept_closed_bar(closed)
            previous_round = previous[-1].ts // self.round_ms
            new_round = ts // self.round_ms
            if new_round != previous_round:
                self._settle_round(previous_round, closed)
                self._reset_round(new_round)
        if not self.candles or self.candles[-1].ts != ts:
            self.candles.append(Candle(ts, price, price, price, price, qty, qty if buy else 0.0, 0.0 if buy else qty))
        else:
            candle = self.candles[-1]
            candle.high = max(candle.high, price)
            candle.low = min(candle.low, price)
            candle.close = price
            candle.volume += qty
            if buy:
                candle.buy_volume += qty
            else:
                candle.sell_volume += qty
        self._dirty = True

    def on_quote(self, quote: dict | None) -> None:
        """Store the live PM quote for comparison; it never changes the Pine model direction."""
        self.quote = dict(quote) if quote else None
        self._dirty = True

    def on_ptb(self, value: float | None, source: str | None) -> None:
        """Receive the pipeline Price To Beat and its provenance for Polymarket mode."""
        self.ptb = _finite(value)
        self.ptb_source = str(source) if source else None
        self._dirty = True

    def _quote_direction(self) -> int | None:
        """Return the direction implied by live PM quotes when both sides exist."""
        if not self.quote:
            return None
        up_bid = _finite(self.quote.get("up_bid"))
        up_ask = _finite(self.quote.get("up_ask"))
        down_bid = _finite(self.quote.get("down_bid"))
        down_ask = _finite(self.quote.get("down_ask"))
        if up_bid is None or up_ask is None:
            return None
        up = (up_bid + up_ask) / 2.0
        down = 100.0 - up if down_bid is None or down_ask is None else (down_bid + down_ask) / 2.0
        return 1 if up >= down else -1

    def _pm_model(self, close: float, opening: float, atr: float, time_remaining: float) -> dict:
        """Port Pine's Black-Scholes binary UP/DOWN forecast and confidence score."""
        seconds_per_year = 365.25 * 24.0 * 60.0 * 60.0
        sigma = _safe_ratio(atr, close, 0.0) * math.sqrt(seconds_per_year / max(self.bar_ms / 1000.0, 1.0))
        time_years = max(time_remaining, 1.0) / seconds_per_year
        if close <= 0 or opening <= 0:
            up_probability = 0.5
        elif sigma <= 1e-12 or time_years <= 0:
            up_probability = 1.0 if close >= opening else 0.0
        else:
            d2 = (math.log(close / opening) - 0.5 * sigma * sigma * time_years) / (sigma * math.sqrt(time_years))
            up_probability = _normal_cdf(d2)
        up_cents = max(1, min(99, int(round(up_probability * 100.0))))
        down_cents = 100 - up_cents
        direction = 1 if up_cents >= down_cents else -1
        drift_pct = abs(close - opening) / opening * 100.0 if opening else 0.0
        drift_score = 25.0 if drift_pct >= 0.15 else 20.0 if drift_pct >= 0.10 else 15.0 if drift_pct >= 0.06 else 10.0 if drift_pct >= 0.03 else 5.0 if drift_pct >= 0.01 else 0.0
        time_score = 25.0 if time_remaining <= 30 else 22.0 if time_remaining <= 60 else 18.0 if time_remaining <= 120 else 12.0 if time_remaining <= 300 else 6.0 if time_remaining <= 600 else 2.0
        confidence = max(0.0, min(100.0, drift_score + time_score + (20.0 if abs(up_cents - down_cents) >= 60 else 12.0) + (15.0 if sigma < 2.0 else 8.0) + (15.0 if close >= opening else 0.0)))
        return {
            "up_cents": up_cents,
            "down_cents": down_cents,
            "direction": direction,
            "confidence": confidence,
            "sigma": sigma,
        }

    def _settle_round(self, round_id: int, last_result: dict) -> None:
        """Settle one completed round using system open/close, exactly like Pine backtest state."""
        if self._active_round_id != round_id:
            return
        round_candles = [c for c in self.candles if c.ts // self.round_ms == round_id]
        if not round_candles:
            return
        signal_direction = self.round_signal_direction if self.round_had_signal else 0
        settle_direction = 1 if round_candles[-1].close > round_candles[0].open else -1
        is_signal = bool(signal_direction)
        is_win = bool(is_signal and signal_direction == settle_direction)
        item = {
            "round_id": round_id,
            "direction": signal_direction,
            "score": self.round_signal_score if is_signal else 0.0,
            "win": is_win,
            "signal": is_signal,
        }
        self.total_rounds += 1
        self.history_10.appendleft(item)
        self.history_100.appendleft(item)
        if not is_signal:
            return
        self.total_signals += 1
        self.total_wins += int(is_win)
        if signal_direction == 1:
            self.up_signals += 1
            self.up_wins += int(is_win)
        else:
            self.down_signals += 1
            self.down_wins += int(is_win)
        if item["score"] >= 90:
            self.high_score_signals += 1
            self.high_score_wins += int(is_win)
        else:
            self.low_score_signals += 1
            self.low_score_wins += int(is_win)
        if is_win:
            self.current_win_streak += 1
            self.current_loss_streak = 0
            self.max_win_streak = max(self.max_win_streak, self.current_win_streak)
        else:
            self.current_loss_streak += 1
            self.current_win_streak = 0
            self.max_loss_streak = max(self.max_loss_streak, self.current_loss_streak)

    def _accept_closed_bar(self, result: dict) -> None:
        """Update Pine's confirmation buffer and lock only on a closed bar."""
        if not result.get("ready"):
            return
        round_id = result["round_id"]
        if self._active_round_id is None:
            self._active_round_id = round_id
        if self._active_round_id != round_id:
            return
        direction = result.get("bias") or 0
        if direction != self._confirm_direction:
            self._confirm_direction = direction
            self._confirm_history.clear()
        met = bool(result.get("conditions_met") and result.get("pm_agree") is True)
        self._confirm_history.append(met)
        confirm_count = sum(1 for value in list(self._confirm_history)[-self.settings.signal_confirm_window:] if value)
        confirmed = not self.settings.use_signal_confirm or confirm_count >= self.settings.signal_confirm_bars
        if met and confirmed and self.locked_round != round_id:
            self.locked_round = round_id
            self.locked_direction = direction
            self.locked_score = float(result.get("quality") or 0.0)
            self.locked_at_ms = int(result.get("bar_close_ms") or 0)
            self._last_locked_bar_ts = int(result.get("bar_ts") or 0)
            self.round_had_signal = True
            self.round_signal_direction = direction
            self.round_signal_score = self.locked_score
            self.round_signal_time_min = float(result.get("time_remaining_s") or 0.0) / 60.0

    def _backtest_summary(self) -> dict:
        """Build the W/L and rolling-history data consumed by the Python Pine-like panels."""
        win_rate = self.total_wins * 100.0 / self.total_signals if self.total_signals else None
        recent_signals = [x for x in self.history_100 if x.get("signal")]
        recent_wins = sum(1 for x in recent_signals[:100] if x.get("win"))
        return {
            "rounds": self.total_rounds,
            "signals": self.total_signals,
            "wins": self.total_wins,
            "losses": self.total_signals - self.total_wins,
            "win_rate": _round(win_rate, 1),
            "up_signals": self.up_signals,
            "up_wins": self.up_wins,
            "down_signals": self.down_signals,
            "down_wins": self.down_wins,
            "high_score_signals": self.high_score_signals,
            "high_score_wins": self.high_score_wins,
            "low_score_signals": self.low_score_signals,
            "low_score_wins": self.low_score_wins,
            "rolling_100_signals": len(recent_signals[:100]),
            "rolling_100_wins": recent_wins,
            "win_streak": self.current_win_streak,
            "loss_streak": self.current_loss_streak,
            "max_win_streak": self.max_win_streak,
            "max_loss_streak": self.max_loss_streak,
            "history_10": list(self.history_10),
            "history_100": list(self.history_100),
        }

    def _calculate(self, now_ms: int | None = None, candles: list[Candle] | None = None) -> dict:
        """Calculate current RaVen values without mutating lock/backtest state."""
        candles = list(candles or self.candles)
        required_bars = 60
        if len(candles) < required_bars:
            return {"ready": False, "status": "WARMING", "bars": len(candles), "required_bars": required_bars, "bias": None, "signal": None}
        self.round_ms = self._round_duration_ms()
        now = int(now_ms or candles[-1].ts + self.bar_ms)
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]
        # Round ownership follows the candle timestamp; using the close time would
        # incorrectly move the last closed candle into the next round.
        round_id = candles[-1].ts // self.round_ms
        round_start = round_id * self.round_ms
        round_candles = [c for c in candles if c.ts >= round_start and c.ts < round_start + self.round_ms]
        system_open = round_candles[0].open if round_candles else candles[-1].open
        elapsed = max(0.0, (now - round_start) / 1000.0)
        custom_active = self.settings.custom_open_price > 0 and elapsed <= self.settings.custom_open_expire_min * 60.0
        ptb_available = self.ptb is not None and self.ptb > 0
        ptb_official = bool(self.ptb_source and self.ptb_source.startswith("official_twap60_carry"))
        use_ptb_reference = self.settings.use_official_ptb and self.settings.preset_mode == "Polymarket" and ptb_available
        reference_open = self.ptb if use_ptb_reference else system_open
        display_open = self.settings.custom_open_price if custom_active else reference_open
        close = closes[-1]
        price_diff = close - display_open
        current_direction = 1 if price_diff >= 0 else -1
        atr_values = _atr(candles, 14)
        atr = _last(atr_values) or _last(_sma([abs(closes[i] - closes[i - 1]) if i else None for i in range(len(closes))], 14)) or 0.0
        atr_sma = _last(_sma(atr_values, 50)) or atr or 1.0
        volatility_ratio = _safe_ratio(atr, atr_sma, 1.0)
        distance_atr = _safe_ratio(abs(price_diff), atr, 0.0)
        price_diff_pct = _safe_ratio(price_diff, display_open, 0.0) * 100.0

        ema5 = _last(_ema(closes, 5))
        ema13 = _last(_ema(closes, 13))
        ema34 = _last(_ema(closes, 34))
        rsi7 = _last(_rsi(closes, 7)) or 50.0
        rsi14 = _last(_rsi(closes, 14)) or 50.0
        momentum_short = closes[-1] - closes[-4]
        momentum_mid = closes[-1] - closes[-9]
        changes = [None] + [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        abs_change = [None if value is None else abs(value) for value in changes]
        velocity = _last(_sma(abs_change, 3)) or 0.0
        velocity_ratio = _safe_ratio(velocity, atr, 0.0)
        di_plus, di_minus, adx_values = _adx(candles, 7)
        di_p = _last(di_plus) or 0.0
        di_m = _last(di_minus) or 0.0
        adx = _last(adx_values) or 0.0
        adx_direction = 1 if di_p >= di_m else -1
        macd_line = _ema(closes, 12)
        macd_base = _ema(closes, 26)
        macd_hist = [a - b if a is not None and b is not None else None for a, b in zip(macd_line, macd_base)]
        macd_signal = _ema(macd_hist, 9)
        macd_value = _last([a - b if a is not None and b is not None else None for a, b in zip(macd_hist, macd_signal)]) or 0.0
        macd_previous = next((value for value in reversed(macd_hist[:-1]) if value is not None), macd_value)
        macd_decaying = abs(macd_value) < abs(macd_previous)

        typical = [(c.high + c.low + c.close) / 3.0 for c in candles]
        volume_sum = sum(volumes)
        vwap = sum(price * volume for price, volume in zip(typical, volumes)) / volume_sum if volume_sum else close
        previous_volume = sum(volumes[-4:-1])
        vwap_previous = sum(typical[i] * volumes[i] for i in range(max(0, len(candles) - 4), len(candles) - 1)) / previous_volume if previous_volume else vwap
        vwap_slope = vwap - vwap_previous
        stoch_low = min(lows[-7:])
        stoch_high = max(highs[-7:])
        stoch = _safe_ratio(close - stoch_low, stoch_high - stoch_low, 0.5) * 100.0
        williams = stoch - 100.0
        volume_average = _last(_sma(volumes, 20)) or 0.0
        volume_ratio = _safe_ratio(volumes[-1], volume_average, 1.0)
        delta_values = [c.buy_volume - c.sell_volume for c in candles]
        volume_delta = _last(_sma(delta_values, 5)) or 0.0

        # Volume-weighted indicators use the same periods as the Pine source.
        money_flow = [price * volume for price, volume in zip(typical, volumes)]
        positive_flow = sum(money_flow[i] for i in range(max(0, len(candles) - 14), len(candles)) if i == 0 or typical[i] >= typical[i - 1])
        negative_flow = sum(money_flow[i] for i in range(max(0, len(candles) - 14), len(candles)) if i > 0 and typical[i] < typical[i - 1])
        mfi = 100.0 if negative_flow == 0 else 100.0 - 100.0 / (1.0 + positive_flow / negative_flow)
        cmf_denominator = sum(volumes[-20:])
        cmf_numerator = sum((((c.close - c.low) - (c.high - c.close)) / max(c.high - c.low, 1e-12)) * c.volume for c in candles[-20:])
        cmf = _safe_ratio(cmf_numerator, cmf_denominator, 0.0)
        obv_values = [0.0]
        for index in range(1, len(candles)):
            obv_values.append(obv_values[-1] + (volumes[index] if closes[index] > closes[index - 1] else -volumes[index] if closes[index] < closes[index - 1] else 0.0))
        obv_direction = _direction(obv_values[-1] - obv_values[-4])
        bb_mean = _last(_sma(closes, 20)) or close
        bb_std = _stddev(closes, 20) or 0.0
        bb_width = _safe_ratio(2.0 * bb_std, bb_mean, 0.0)
        bb_compressed = bb_width < 0.004
        kc_middle = _last(_ema(closes, 20)) or close
        kc_atr = _last(_atr(candles, 10)) or atr
        keltner_aligned = close > kc_middle + 1.5 * kc_atr if current_direction == 1 else close < kc_middle - 1.5 * kc_atr

        supertrend_direction, _ = _supertrend(candles, 7, 2.0)
        ha_close_values = [(c.open + c.high + c.low + c.close) / 4.0 for c in candles]
        ha_open_values = [(c.open + c.close) / 2.0 for c in candles]
        ha_direction = 1 if ha_close_values[-1] >= ha_open_values[-1] else -1
        ha_continuity = sum(1 for a, b in zip(ha_close_values[-3:], ha_open_values[-3:]) if (1 if a >= b else -1) == ha_direction)
        conversion = (max(highs[-9:]) + min(lows[-9:])) / 2.0
        base = (max(highs[-26:]) + min(lows[-26:])) / 2.0
        ichi_span_a = (conversion + base) / 2.0
        ichi_span_b = (max(highs[-52:]) + min(lows[-52:])) / 2.0
        ichi_direction = 1 if close > max(ichi_span_a, ichi_span_b) else -1 if close < min(ichi_span_a, ichi_span_b) else 0
        linreg_current, linreg_previous, linreg_r2 = _linreg(closes, 20)
        linreg_direction = 1 if linreg_current is not None and linreg_previous is not None and linreg_current >= linreg_previous else -1
        psar_direction, _ = _psar(candles)
        cci_mean = _last(_sma(typical, 20)) or typical[-1]
        cci_deviation = sum(abs(value - cci_mean) for value in typical[-20:]) / min(20, len(typical))
        cci = _safe_ratio(typical[-1] - cci_mean, 0.015 * cci_deviation, 0.0)

        ema_direction = 1 if ema5 is not None and ema34 is not None and ema5 >= ema34 else -1
        macd_direction = _direction(macd_value)
        vwap_direction = 1 if close >= vwap else -1
        vwap_slope_direction = _direction(vwap_slope)
        rsi_direction = 1 if rsi7 > 52 else -1 if rsi7 < 48 else 0
        momentum_direction = 1 if momentum_short > 0 and momentum_mid > 0 else -1 if momentum_short < 0 and momentum_mid < 0 else 0
        stoch_direction = 1 if stoch > 50 else -1
        williams_direction = 1 if williams > -50 else -1
        delta_direction = _direction(volume_delta)
        mfi_direction = 1 if mfi > 55 else -1 if mfi < 45 else 0
        cmf_direction = 1 if cmf > 0.05 else -1 if cmf < -0.05 else 0

        trend_dimensions = [supertrend_direction, ema_direction, macd_direction, vwap_direction, adx_direction]
        if self.settings.use_enhanced_indicators and self.settings.use_vwap_slope:
            trend_dimensions.append(vwap_slope_direction)
        if self.settings.use_enhanced_indicators and self.settings.use_ha_confirm:
            trend_dimensions.append(ha_direction)
        if self.settings.use_enhanced_indicators and self.settings.use_ichimoku:
            trend_dimensions.append(ichi_direction)
        if self.settings.use_enhanced_indicators and self.settings.use_linreg:
            trend_dimensions.append(linreg_direction)
        trend_agree = sum(value == current_direction for value in trend_dimensions)
        momentum_dimensions = [rsi_direction, momentum_direction, stoch_direction, williams_direction]
        if self.settings.use_enhanced_indicators and self.settings.use_volume_delta:
            momentum_dimensions.append(delta_direction)
        if self.settings.use_enhanced_indicators and self.settings.use_mfi:
            momentum_dimensions.append(mfi_direction)
        if self.settings.use_enhanced_indicators and self.settings.use_cmf:
            momentum_dimensions.append(cmf_direction)
        momentum_agree = sum(value == current_direction for value in momentum_dimensions)
        mtf_dirs: dict[str, int] = {}
        for name, timeframe in (("1m", 60_000), ("3m", 180_000), ("5m", 300_000)) if self.settings.use_enhanced_indicators and self.settings.use_mtf else ():
            higher = _aggregate(candles, timeframe)
            higher_closes = [c.close for c in higher]
            if len(higher_closes) >= 21:
                fast = _last(_ema(higher_closes, 8)) or higher_closes[-1]
                slow = _last(_ema(higher_closes, 21)) or higher_closes[-1]
                mtf_dirs[name] = 1 if fast >= slow else -1
            else:
                mtf_dirs[name] = 0
        mtf_agree = sum(value == current_direction for value in mtf_dirs.values())
        htf_aligned = not self.settings.use_htf_alignment or not mtf_dirs or mtf_agree >= 2

        round_end = round_start + self.round_ms
        time_remaining = max(0.0, (round_end - now) / 1000.0)
        window_start, window_end = self._window_seconds()
        in_time_window = window_start <= time_remaining <= window_end
        pm_model = self._pm_model(close, display_open, atr, time_remaining)
        pm_direction = pm_model["direction"]
        pm_agree = pm_direction == current_direction

        # Reversal flags feed both the black-swan risk and the quality penalty.
        previous_rsi = _last(_rsi(closes[:-1], 7)) if len(closes) > 15 else rsi7
        ema_previous = _last(_ema(closes[:-1], 5)) if len(closes) > 10 else ema5
        reversal_flags = {
            "rsi_turn": (rsi7 < previous_rsi and rsi7 > 70) or (rsi7 > previous_rsi and rsi7 < 30),
            "macd_decay": macd_decaying,
            "ema_reversal": ema_previous is not None and ema5 is not None and (ema5 - ema_previous) * current_direction < 0,
            "volume_reversal": delta_direction == -current_direction,
            "supertrend_flip": supertrend_direction != current_direction,
            "volume_climax": volume_ratio > 2.0,
            "psar_flip": psar_direction != current_direction,
        }
        reversal_count = sum(1 for value in reversal_flags.values() if value)
        has_reversal_warning = reversal_count >= 2
        previous_round_candles = [c for c in candles if c.ts >= round_start - self.round_ms and c.ts < round_start]
        previous_close = previous_round_candles[-1].close if previous_round_candles else close
        previous_distance = _safe_ratio(abs(previous_close - display_open), atr, 0.0)
        dist_growing = not self.settings.use_distance_stability or distance_atr >= previous_distance * 0.75
        trend_stable = not self.settings.use_stability_checks or (trend_agree >= self.settings.trend_agree_min and (current_direction == (1 if previous_close >= display_open else -1) or distance_atr >= previous_distance))
        trend_maturity = 2 if trend_stable else 0
        market_regime = 1 if adx >= 25 and trend_agree >= round(0.78 * len(trend_dimensions)) and not bb_compressed else -1 if volatility_ratio > 2.5 or has_reversal_warning else 0

        # This is the Pine black-swan decomposition, kept explicit for panel debugging.
        time_risk = max(0.0, min(25.0, (1.0 - time_remaining / max(self.round_ms / 1000.0, 1.0)) * 25.0))
        volatility_risk = max(0.0, min(20.0, max(0.0, volatility_ratio - 1.0) * 12.0))
        distance_risk = max(0.0, min(15.0, max(0.0, distance_atr - 0.8) * 8.0))
        rsi_risk = 10.0 if rsi7 > 75 or rsi7 < 25 else 0.0
        disagreement_risk = max(0.0, min(15.0, (len(trend_dimensions) - trend_agree) * 1.5 + (len(momentum_dimensions) - momentum_agree)))
        velocity_risk = max(0.0, min(10.0, max(0.0, velocity_ratio - 1.0) * 3.0))
        reversal_risk = min(20.0, reversal_count * 4.0)
        black_swan_prob = max(0.0, min(95.0, time_risk + volatility_risk + distance_risk + rsi_risk + disagreement_risk + velocity_risk + reversal_risk))

        adx_score = max(0.0, min(20.0, (adx - 8.0) / 22.0 * 20.0)) if adx_direction == current_direction else 0.0
        distance_score = max(0.0, min(10.0, distance_atr / 0.3 * 10.0))
        trend_score = 25.0 if trend_agree >= 9 else 22.0 if trend_agree >= 8 else 18.0 if trend_agree >= 7 else 13.0 if trend_agree >= 5 else 8.0 if trend_agree >= 2 else 3.0
        momentum_score = 20.0 if momentum_agree >= 7 else 17.0 if momentum_agree >= 6 else 13.0 if momentum_agree >= 5 else 8.0 if momentum_agree >= 2 else 4.0 if momentum_agree >= 1 else 0.0
        risk_score = max(0.0, 15.0 * (1.0 - black_swan_prob / 100.0))
        bonuses = {
            "supertrend": 5.0 if supertrend_direction == current_direction else 0.0,
            "vwap": 5.0 if vwap_direction == current_direction else 0.0,
            "low_volatility": 5.0 if volatility_ratio < 1.2 else 0.0,
            "strong_trend": 5.0 if trend_agree >= 8 else 0.0,
            "mtf": 20.0 if mtf_agree == 3 else 12.0 if mtf_agree == 2 else 5.0 if mtf_agree == 1 else -5.0,
            "obv": 7.0 if obv_direction == current_direction else 0.0,
            "bb": 4.0 if not bb_compressed else -3.0,
            "keltner": 4.0 if keltner_aligned else 0.0,
            "vwap_slope": 3.0 if vwap_slope_direction == current_direction else 0.0,
            "volume_delta": 3.0 if delta_direction == current_direction else 0.0,
            "stability": 3.0 if trend_stable and dist_growing else 0.0,
            "maturity": float(trend_maturity),
            "ha": min(5.0, float(ha_continuity)),
            "ichimoku": 4.0 if ichi_direction == current_direction else 0.0,
            "linreg": 4.0 * (linreg_r2 or 0.0) if linreg_direction == current_direction else 0.0,
            "cci": -4.0 if (cci > 150 and current_direction == 1) or (cci < -150 and current_direction == -1) else 0.0,
            "reversal": -min(25.0, reversal_count * 4.0),
        }
        quality = max(0.0, min(100.0, adx_score + distance_score + trend_score + momentum_score + risk_score + sum(bonuses.values())))

        filter_flags = {
            "time_window": in_time_window,
            "adx": adx >= self.settings.adx_min and adx_direction == current_direction,
            "distance": distance_atr >= self.settings.distance_atr_min,
            "trend": trend_agree >= self.settings.trend_agree_min,
            "momentum": momentum_agree >= self.settings.momentum_agree_min,
            "risk": black_swan_prob <= self.settings.black_swan_max,
            "volume": volume_ratio >= self.settings.volume_ratio_min,
            "velocity": velocity_ratio <= self.settings.velocity_max,
            "rsi": self.settings.rsi_min <= rsi7 <= self.settings.rsi_max,
            "volatility": volatility_ratio <= self.settings.volatility_ratio_max,
            "trend_stability": trend_stable,
            "distance_growth": dist_growing,
            "pm_agreement": pm_agree,
            "official_ptb": (not self.settings.require_official_ptb) or (not self.settings.use_official_ptb) or ptb_official,
            "htf": htf_aligned if self.settings.use_htf_alignment else True,
        }
        strict_core = all(filter_flags[name] for name in ("time_window", "adx", "distance", "trend", "momentum", "risk", "pm_agreement", "official_ptb", "htf"))
        essential_core = all(filter_flags[name] for name in ("time_window", "adx", "trend", "pm_agreement", "official_ptb"))
        core_missing = sum(not filter_flags[name] for name in ("distance", "momentum", "risk", "official_ptb", "htf"))
        filter_missing = sum(not filter_flags[name] for name in ("volume", "velocity", "rsi", "volatility", "trend_stability", "distance_growth"))
        flex_active = self.settings.use_flex_mode and essential_core and core_missing <= 1 and filter_missing <= 1 and not strict_core
        effective_threshold = self.settings.quality_threshold + (self.settings.flex_quality_bonus if flex_active else 0.0)
        if self.settings.use_vol_adapt:
            effective_threshold += 3.0 if volatility_ratio > 1.5 else -2.0 if volatility_ratio < 0.8 else 0.0
        effective_threshold += 5.0 if market_regime == -1 else -3.0 if market_regime == 1 else 0.0
        effective_threshold = max(0.0, min(100.0, effective_threshold))
        cooldown_ok = self._last_locked_bar_ts is None or (candles[-1].ts - self._last_locked_bar_ts) >= self.settings.cooldown_bars * self.bar_ms
        conditions_met = (strict_core or flex_active) and cooldown_ok and quality >= effective_threshold
        conditions_total = len(filter_flags) + 2
        conditions_passed = sum(1 for value in filter_flags.values() if value) + int(cooldown_ok) + int(quality >= effective_threshold)
        confirm_count = sum(1 for value in list(self._confirm_history)[-self.settings.signal_confirm_window:] if value) if self._confirm_direction == current_direction else 0
        locked = self.locked_round == round_id and self.locked_direction != 0
        raw = conditions_met and pm_agree
        preview = raw and not locked
        signal = self.locked_direction if locked else None
        if not locked and raw and (not self.settings.use_signal_confirm or confirm_count >= self.settings.signal_confirm_bars):
            signal = current_direction
        blockers = [name for name, value in filter_flags.items() if not value]
        status = "LOCKED" if locked else "DIAMOND_PREVIEW" if preview else "DIAMOND_CANDIDATE" if conditions_met else "BIAS"
        return {
            "ready": True,
            "status": status,
            "bars": len(candles),
            "bar_ms": self.bar_ms,
            "round_ms": self.round_ms,
            "round_id": round_id,
            "round_start_ms": round_start,
            "round_end_ms": round_end,
            "bar_ts": candles[-1].ts,
            "bar_close_ms": candles[-1].ts + self.bar_ms,
            "time_remaining_s": round(time_remaining, 1),
            "opening_price": _round(system_open, 8),
            "display_open_price": _round(display_open, 8),
            "reference_price": _round(display_open, 8),
            "reference_mode": "CUSTOM_OPEN" if custom_active else "PTB" if use_ptb_reference else "CANDLE_OPEN",
            "ptb": _round(self.ptb, 8),
            "ptb_source": self.ptb_source,
            "ptb_official": ptb_official,
            "custom_open_active": custom_active,
            "close": _round(close, 8),
            "price_diff": _round(price_diff, 8),
            "price_diff_pct": _round(price_diff_pct, 4),
            "bias": current_direction,
            "signal": signal,
            "diamond_candidate": conditions_met,
            "diamond_raw": raw,
            "diamond_preview": preview,
            "diamond_confirmed": locked or bool(signal),
            "locked": locked,
            "locked_direction": self.locked_direction if locked else 0,
            "locked_score": _round(self.locked_score, 1),
            "locked_at_ms": self.locked_at_ms,
            "pm_direction": pm_direction,
            "pm_quote_direction": self._quote_direction(),
            "pm_agree": pm_agree,
            "pm_up_cents": pm_model["up_cents"],
            "pm_down_cents": pm_model["down_cents"],
            "pm_confidence": _round(pm_model["confidence"], 1),
            "pm_sigma": _round(pm_model["sigma"], 5),
            "quality": round(quality, 1),
            "threshold": round(effective_threshold, 1),
            "base_threshold": self.settings.quality_threshold,
            "confirm_count": confirm_count,
            "confirm_bars": self.settings.signal_confirm_bars,
            "confirm_window": self.settings.signal_confirm_window,
            "conditions_met": conditions_met,
            "conditions_passed": conditions_passed,
            "conditions_total": conditions_total,
            "trend_agree": trend_agree,
            "trend_total": len(trend_dimensions),
            "momentum_agree": momentum_agree,
            "momentum_total": len(momentum_dimensions),
            "mtf_agree": mtf_agree,
            "mtf_total": len(mtf_dirs),
            "adx": _round(adx, 2),
            "rsi": _round(rsi7, 2),
            "atr": _round(atr, 8),
            "volatility_ratio": _round(volatility_ratio, 2),
            "distance_atr": _round(distance_atr, 3),
            "velocity_ratio": _round(velocity_ratio, 2),
            "black_swan_prob": _round(black_swan_prob, 1),
            "reversal_count": reversal_count,
            "has_reversal_warning": has_reversal_warning,
            "market_regime": market_regime,
            "trend_stable": trend_stable,
            "dist_growing": dist_growing,
            "flex_active": flex_active,
            "cooldown_ok": cooldown_ok,
            "blockers": blockers,
            "filters": filter_flags,
            "score_components": {"adx": round(adx_score, 1), "distance": round(distance_score, 1), "trend": trend_score, "momentum": momentum_score, "risk": round(risk_score, 1), "bonuses": {key: round(value, 1) for key, value in bonuses.items()}},
            "components": {
                "ema": ema_direction, "macd": macd_direction, "vwap": vwap_direction, "adx": adx_direction, "supertrend": supertrend_direction,
                "rsi": rsi_direction, "momentum": momentum_direction, "stoch": stoch_direction, "williams": williams_direction,
                "volume_delta": delta_direction, "mfi": mfi_direction, "cmf": cmf_direction, "obv": obv_direction,
                "vwap_slope": vwap_slope_direction, "ha": ha_direction, "ichimoku": ichi_direction, "linreg": linreg_direction, "psar": psar_direction,
                "mtf": mtf_dirs,
            },
            "indicator_values": {
                "ema5": _round(ema5, 8), "ema13": _round(ema13, 8), "ema34": _round(ema34, 8), "macd": _round(macd_value, 8),
                "vwap": _round(vwap, 8), "vwap_slope": _round(vwap_slope, 8), "stoch": _round(stoch, 2), "williams": _round(williams, 2),
                "mfi": _round(mfi, 2), "cmf": _round(cmf, 4), "cci": _round(cci, 2), "bb_width": _round(bb_width, 5),
                "volume_ratio": _round(volume_ratio, 2), "volume_delta": _round(volume_delta, 8), "linreg_r2": _round(linreg_r2, 4),
            },
            "backtest": self._backtest_summary(),
            "settings": asdict(self.settings),
        }

    def snapshot(self, now_ms: int | None = None) -> dict:
        """Return a cached live snapshot while preserving closed-bar state semantics."""
        if self._dirty:
            if self.candles and self._active_round_id is None:
                self._reset_round(self.candles[-1].ts // self.round_ms)
            self._cached = self._calculate(now_ms)
            self._dirty = False
        return self._cached

    def history_payload(self) -> dict:
        """Return chart history and Pine-like diagnostics for the Raven Lab page."""
        return {
            "bar_ms": self.bar_ms,
            "round_ms": self.round_ms,
            "candles": [c.as_row() for c in list(self.candles)[-900:]],
            "snapshot": self.snapshot(),
        }


# Keep the existing import path stable while the V2 implementation replaces the prototype.
RavenEngine = RavenEngineV2
