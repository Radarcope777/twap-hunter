import { bus, connect } from "./bus.js";
import { fmtCountdown, fmtPrice } from "./format.js";

const state = {
  candles: [], barMs: 15000, roundMs: 900000,
  reference: localStorage.getItem("raven-tv-reference") || "",
  panelMode: localStorage.getItem("raven-panel-mode") || "Standard",
  signals: [], lastLockKey: "",
};

const $ = (id) => document.getElementById(id);
const directionText = (d) => d === 1 ? "UP" : d === -1 ? "DOWN" : "—";
const directionClass = (d) => d === 1 ? "up" : d === -1 ? "down" : "flat";
const num = (v, digits = 2) => v == null || !isFinite(v) ? "—" : Number(v).toFixed(digits);
const regimeText = (value) => value === 1 ? "STRONG TREND" : value === -1 ? "SHOCK" : "WEAK TREND";

function mergeTick(row) {
  if (!row || row.length < 3) return;
  const [rawTs, price, qty] = row;
  const ts = Math.floor(rawTs / state.barMs) * state.barMs;
  let candle = state.candles[state.candles.length - 1];
  if (!candle || candle[0] !== ts) {
    candle = [ts, price, price, price, price, qty || 0];
    state.candles.push(candle);
  } else {
    candle[2] = Math.max(candle[2], price); candle[3] = Math.min(candle[3], price);
    candle[4] = price; candle[5] += qty || 0;
  }
  if (state.candles.length > 900) state.candles.splice(0, state.candles.length - 900);
}

function setText(id, value, cls = "") {
  const node = $(id); if (!node) return;
  node.textContent = value; if (cls) node.className = cls;
}

function renderTabs() {
  const host = $("raven-assets"); if (!host || !bus.assets.length) return;
  host.innerHTML = bus.assets.map((asset) => `<a class="raven-tab${asset === bus.asset ? " active" : ""}" href="/raven?asset=${encodeURIComponent(asset)}">${asset}</a>`).join("");
}

function renderPanelMode() {
  const standard = $("raven-standard-panels"); const complete = $("raven-complete-panels");
  if (standard) standard.hidden = state.panelMode === "Streamline";
  if (complete) complete.hidden = state.panelMode !== "complete";
  const selector = $("raven-panel-mode"); if (selector) selector.value = state.panelMode;
}

function renderSnapshot(snap) {
  const r = snap?.raven; if (!r) return;
  const signal = r.signal || null; const lockedDirection = r.locked_direction || signal || 0;
  const stateLabel = r.status || "WARMING";
  setText("raven-signal", signal ? directionText(signal) : stateLabel, signal ? `raven-signal ${directionClass(signal)}` : "raven-signal");
  setText("raven-signal-state", stateLabel.replaceAll("_", " "), r.locked ? "raven-state locked" : r.diamond_preview ? "raven-state preview" : "raven-state");
  setText("raven-bias", directionText(r.bias), directionClass(r.bias));
  setText("raven-quality", r.quality == null ? "—" : `${num(r.quality, 1)} / ${num(r.threshold, 0)}`);
  setText("raven-lock", r.locked ? directionText(lockedDirection) : "NOT LOCKED", r.locked ? directionClass(lockedDirection) : "muted");
  setText("raven-locked-quality", r.locked ? `${num(r.locked_score, 1)} at ${r.locked_at_ms ? new Date(r.locked_at_ms).toLocaleTimeString() : "—"}` : "—");
  setText("raven-confirm", r.ready ? `${r.confirm_count || 0}/${r.confirm_bars || 0} (${r.confirm_window || 0})` : "—");
  setText("raven-trend", r.ready ? `${r.trend_agree}/${r.trend_total}` : "—");
  setText("raven-momentum", r.ready ? `${r.momentum_agree}/${r.momentum_total}` : "—");
  setText("raven-mtf", r.ready ? `${r.mtf_agree}/${r.mtf_total}` : "—");
  setText("raven-pm", r.pm_agree == null ? "UNAVAILABLE" : r.pm_agree ? "AGREE" : "CONFLICT", r.pm_agree == null ? "muted" : r.pm_agree ? "up" : "down");
  setText("raven-regime", regimeText(r.market_regime)); setText("raven-risk", r.black_swan_prob == null ? "—" : `${num(r.black_swan_prob, 1)}%`);
  setText("raven-adx", num(r.adx)); setText("raven-rsi", num(r.rsi)); setText("raven-distance", num(r.distance_atr, 3)); setText("raven-volatility", num(r.volatility_ratio));
  setText("raven-pm-cents", r.pm_up_cents == null ? "—" : `${r.pm_up_cents}¢ / ${r.pm_down_cents}¢`);
  setText("raven-pm-confidence", r.pm_confidence == null ? "—" : `${num(r.pm_confidence, 1)}%`);
  setText("raven-pm-up", r.pm_up_cents == null ? "UP —¢" : `UP ${r.pm_up_cents}¢`); setText("raven-pm-down", r.pm_down_cents == null ? "DOWN —¢" : `DOWN ${r.pm_down_cents}¢`);
  setText("raven-pm-direction", directionText(r.pm_direction), directionClass(r.pm_direction)); setText("raven-pm-quote", directionText(r.pm_quote_direction), directionClass(r.pm_quote_direction));
  setText("raven-ptb", r.ptb == null ? "—" : `${num(r.ptb, 2)} (${r.reference_mode || "—"})`);
  setText("raven-ptb-source", r.ptb_source || "UNAVAILABLE", r.ptb_official ? "up" : "warn");
  setText("raven-time-remaining", r.time_remaining_s == null ? "—" : fmtCountdown(r.time_remaining_s * 1000));
  setText("raven-threshold", `${num(r.base_threshold, 0)} → ${num(r.threshold, 0)}`); setText("raven-flex", r.flex_active ? "ACTIVE" : "STANDBY", r.flex_active ? "warn" : "muted");
  setText("raven-stability", `${r.trend_stable ? "✓Stable" : "✗Unstable"} / ${r.dist_growing ? "✓Growth" : "✗Retract"}`);
  const blockers = $("raven-blockers");
  if (blockers) blockers.textContent = r.ready ? (r.blockers?.length ? `Blocked by: ${r.blockers.join(", ")}` : "All core conditions are satisfied") : `Warming: ${r.bars || 0}/${r.required_bars || 60} candles`;
  renderComponents(r.components || {}); renderFilters(r.filters || {}); renderDetails(r); renderBacktest(r.backtest || {}); renderSettings(r.settings || {}); renderReference(r); renderPanelMode();
  if (r.round_end_ms) { $("raven-countdown").textContent = fmtCountdown(Math.max(0, r.round_end_ms - bus.nowServerMs())); $("raven-round-label").textContent = `${bus.asset || "BTC"} · ${Math.round((r.bar_ms || 15000) / 1000)}s candles / ${Math.round((r.round_ms || 900000) / 60000)}m round`; }
  if (r.locked && r.locked_at_ms) {
    const lockKey = `${r.round_id}:${r.locked_at_ms}:${r.locked_direction}`;
    if (state.lastLockKey !== lockKey) { state.signals.push({ ts: r.locked_at_ms, direction: r.locked_direction, round_id: r.round_id }); state.lastLockKey = lockKey; }
  }
}

function renderComponents(components) {
  const host = $("raven-components"); if (!host) return;
  const labels = { ema: "EMA", macd: "MACD", vwap: "VWAP", adx: "ADX", supertrend: "SuperTrend", rsi: "RSI", momentum: "Momentum", stoch: "Stoch", williams: "Williams", volume_delta: "Vol delta", mfi: "MFI", cmf: "CMF", obv: "OBV", vwap_slope: "VWAP slope", ha: "HA", ichimoku: "Ichi", linreg: "LinReg", psar: "PSAR" };
  host.innerHTML = Object.entries(labels).map(([key, label]) => `<span class="raven-chip ${directionClass(components[key])}"><i></i>${label} ${directionText(components[key])}</span>`).join("");
}

function renderFilters(filters) {
  const host = $("raven-filters"); if (!host) return;
  host.innerHTML = Object.entries(filters).map(([name, pass]) => `<span class="raven-filter ${pass ? "pass" : "fail"}">${pass ? "✓" : "✗"} ${name.replaceAll("_", " ")}</span>`).join("");
}

function renderDetails(r) {
  const host = $("raven-detail-table"); if (!host) return;
  const c = r.components || {}; const v = r.indicator_values || {};
  const rows = [["SuperTrend", directionText(c.supertrend)], ["EMA 5/34", directionText(c.ema)], ["MACD", `${directionText(c.macd)} ${num(v.macd, 2)}`], ["VWAP", directionText(c.vwap)], ["ADX / DI", `${num(r.adx, 1)} ${directionText(c.adx)}`], ["VWAP slope", directionText(c.vwap_slope)], ["Keltner", "calculated"], ["MTF", `${r.mtf_agree || 0}/${r.mtf_total || 0}`], ["Heikin Ashi", directionText(c.ha)], ["Ichimoku", directionText(c.ichimoku)], ["LinReg / R²", `${directionText(c.linreg)} / ${num(v.linreg_r2, 2)}`], ["PSAR", directionText(c.psar)]];
  host.innerHTML = rows.map(([name, value]) => `<div class="raven-detail-row"><span>${name}</span><b>${value}</b></div>`).join("");
}

function renderBacktest(bt) {
  setText("raven-bt-rate", bt.win_rate == null ? "—" : `${num(bt.win_rate, 1)}%`); setText("raven-bt-wl", bt.signals == null ? "—" : `${bt.wins || 0}W / ${bt.losses || 0}L`);
  setText("raven-bt-coverage", `${bt.rounds || 0} / ${bt.signals || 0}`); setText("raven-bt-up", `${bt.up_wins || 0}W / ${Math.max(0, (bt.up_signals || 0) - (bt.up_wins || 0))}L`); setText("raven-bt-down", `${bt.down_wins || 0}W / ${Math.max(0, (bt.down_signals || 0) - (bt.down_wins || 0))}L`);
  setText("raven-bt-streak", bt.win_streak ? `🔥 ${bt.win_streak}W` : bt.loss_streak ? `❄ ${bt.loss_streak}L` : "—");
  const host = $("raven-bt-history"); if (host) host.innerHTML = (bt.history_10 || []).map((item) => `<span class="bt-dot ${item.signal ? (item.win ? "win" : "loss") : "empty"}">${item.signal ? (item.win ? "W" : "L") : "·"}</span>`).join("");
}

function renderSettings(settings) {
  const host = $("raven-settings"); if (!host) return;
  const selected = ["preset_mode", "round_mode", "custom_duration_minutes", "time_window_mode", "time_preset", "custom_window_start_min", "custom_window_end_min", "quality_threshold", "adx_min", "trend_agree_min", "momentum_agree_min", "black_swan_max", "distance_atr_min", "velocity_max", "volume_ratio_min", "volatility_ratio_max", "rsi_min", "rsi_max", "signal_confirm_bars", "signal_confirm_window", "use_flex_mode", "use_smart_cooldown", "use_mtf", "use_obv", "use_bb_state", "use_vwap_slope", "use_keltner", "use_volume_delta", "use_mfi", "use_ha_confirm", "use_ichimoku", "use_linreg", "use_psar", "use_cci", "use_cmf"];
  host.innerHTML = selected.filter((key) => key in settings).map((key) => `<div class="raven-detail-row"><span>${key.replaceAll("_", " ")}</span><b>${typeof settings[key] === "boolean" ? (settings[key] ? "ON" : "OFF") : settings[key]}</b></div>`).join("");
}

function renderReference(r) {
  const host = $("raven-match"); if (!host) return;
  if (!state.reference) { host.textContent = "REFERENCE NOT SET"; host.className = "muted"; return; }
  if (!r.locked_direction) { host.textContent = `TV ${directionText(Number(state.reference))} · WAITING LOCK`; host.className = "muted"; return; }
  const same = Number(state.reference) === r.locked_direction; host.textContent = same ? "MATCH" : "MISMATCH"; host.className = same ? "up" : "down";
}

function drawChart() {
  const canvas = $("raven-chart"); if (!canvas || !state.candles.length) return;
  const rect = canvas.getBoundingClientRect(); const dpr = window.devicePixelRatio || 1; const width = Math.max(1, Math.floor(rect.width)); const height = Math.max(1, Math.floor(rect.height));
  if (canvas.width !== width * dpr || canvas.height !== height * dpr) { canvas.width = width * dpr; canvas.height = height * dpr; }
  const ctx = canvas.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);
  const candles = state.candles.slice(-180); const prices = candles.flatMap((c) => [c[2], c[3]]).filter(Number.isFinite); if (!prices.length) return;
  const min = Math.min(...prices), max = Math.max(...prices), pad = Math.max((max - min) * 0.12, max * 0.00015); const lo = min - pad, hi = max + pad; const left = 8, right = width - 8, top = 12, bottom = height - 22;
  const x = (i) => left + (i / Math.max(1, candles.length - 1)) * (right - left); const y = (p) => bottom - ((p - lo) / (hi - lo)) * (bottom - top);
  ctx.strokeStyle = "#20283a"; ctx.lineWidth = 1; for (let i = 1; i < 5; i++) { const gy = top + (i / 5) * (bottom - top); ctx.beginPath(); ctx.moveTo(left, gy); ctx.lineTo(right, gy); ctx.stroke(); }
  const widthBar = Math.max(2, Math.min(11, (right - left) / candles.length * 0.72));
  for (let i = 0; i < candles.length; i++) { const c = candles[i], up = c[4] >= c[1], cx = x(i); ctx.strokeStyle = up ? "#22c77a" : "#f0475c"; ctx.fillStyle = ctx.strokeStyle; ctx.beginPath(); ctx.moveTo(cx, y(c[2])); ctx.lineTo(cx, y(c[3])); ctx.stroke(); const bodyTop = y(Math.max(c[1], c[4])), bodyBottom = y(Math.min(c[1], c[4])); ctx.fillRect(cx - widthBar / 2, bodyTop, widthBar, Math.max(1, bodyBottom - bodyTop)); }
  const snap = bus.snapshot?.raven;
  const referencePrice = snap?.reference_price ?? snap?.opening_price;
  if (referencePrice != null && snap.round_start_ms != null) { const openIndex = candles.findIndex((c) => c[0] >= snap.round_start_ms); if (openIndex >= 0) { ctx.setLineDash([5, 4]); ctx.strokeStyle = "#f5b942"; ctx.beginPath(); ctx.moveTo(x(openIndex), y(referencePrice)); ctx.lineTo(right, y(referencePrice)); ctx.stroke(); ctx.setLineDash([]); } }
  for (const marker of state.signals) { let index = candles.findIndex((c) => c[0] >= marker.ts); if (index < 0) continue; const c = candles[index], cy = y(marker.direction === 1 ? c[3] : c[2]); ctx.fillStyle = marker.direction === 1 ? "#35d0d0" : "#b07cff"; ctx.beginPath(); if (marker.direction === 1) { ctx.moveTo(x(index), cy - 8); ctx.lineTo(x(index) - 6, cy + 4); ctx.lineTo(x(index) + 6, cy + 4); } else { ctx.moveTo(x(index), cy + 8); ctx.lineTo(x(index) - 6, cy - 4); ctx.lineTo(x(index) + 6, cy - 4); } ctx.closePath(); ctx.fill(); }
  if (snap?.diamond_preview) { const index = candles.length - 1, c = candles[index], cy = y(snap.bias === 1 ? c[3] : c[2]); ctx.strokeStyle = "#f5d76e"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.arc(x(index), cy, 7, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]); }
  ctx.fillStyle = "#6b7488"; ctx.font = "10px Cascadia Mono, monospace"; ctx.fillText(fmtPrice(hi, 2), left, 10); ctx.fillText(fmtPrice(lo, 2), left, height - 5);
}

function frame() { renderSnapshot(bus.snapshot); drawChart(); requestAnimationFrame(frame); }
bus.on("init", (msg) => { state.barMs = msg.raven?.bar_ms || 15000; state.roundMs = msg.raven?.round_ms || 900000; state.candles = (msg.raven?.candles || []).slice(); renderTabs(); renderSnapshot(msg.snapshot); });
bus.on("batch", (msg) => { for (const tick of msg.ticks || []) mergeTick(tick); renderSnapshot(msg.snapshot); });

const ref = $("tv-reference");
if (ref) { ref.value = state.reference; ref.addEventListener("change", () => { state.reference = ref.value; localStorage.setItem("raven-tv-reference", state.reference); renderReference(bus.snapshot?.raven || {}); }); }
const mode = $("raven-panel-mode");
if (mode) { mode.value = state.panelMode; mode.addEventListener("change", () => { state.panelMode = mode.value; localStorage.setItem("raven-panel-mode", state.panelMode); renderPanelMode(); }); }
connect(); requestAnimationFrame(frame);
