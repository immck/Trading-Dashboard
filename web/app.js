/* 多周期交易看板 */
"use strict";

const $ = id => document.getElementById(id);
const C = { up: "#00b956", down: "#ff4976", accent: "#4da6ff", dim: "#8b949e",
            purple: "#c480ff", orange: "#ff9500" };
const TF_ORDER = ["15m", "30m", "1h", "4h", "1d", "1w"];

const state = {
  symbol: "BTC", tf: "1h", sub: "macd", wallTier: "near",
  overlays: {
    ma: true, ema: false, boll: false, st: false, vwap: false, markers: true,
    ichimoku: false, psar: false, keltner: false, donchian: false,
    hma: false, vwma: false, chandelier: false, pivot: false,
  },
  snap: null, mtf: null, chartData: null, liq: null, priceLines: [],
  mainChart: null, subChart: null, candleSeries: null, volSeries: null,
  overlaySeries: [], subSeries: [], syncing: false, loading: 0,
  nextFunding: 0, timers: [],
};

/* ---------------------------------------------------------------- 工具 */
const num = v => (v === null || v === undefined || Number.isNaN(Number(v))) ? null : Number(v);
function fmt(v, d) {
  const n = num(v);
  if (n === null) return "--";
  if (d !== undefined) return n.toFixed(d);
  const a = Math.abs(n);
  if (a >= 1000) return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (a >= 100) return n.toFixed(2);
  if (a >= 1) return n.toFixed(3);
  if (a >= 0.01) return n.toFixed(4);
  if (a >= 0.0001) return n.toFixed(6);
  return n.toFixed(8);
}
function usd(v) {
  const n = num(v);
  if (n === null) return "--";
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return n.toFixed(0);
}
const pct = v => { const n = num(v); return n === null ? "--" : (n > 0 ? "+" : "") + n.toFixed(2) + "%"; };
/* 安全定点：undefined / null / NaN 一律 --，避免 undefined.toFixed 崩掉 */
const fx = (v, d) => { const n = num(v); return n === null ? "--" : n.toFixed(d); };
const spct = (v, d) => {
  const n = num(v);
  return n === null ? "--" : (n > 0 ? "+" : "") + n.toFixed(d) + "%";
};
const cls = v => v === null ? "" : v > 0 ? "up" : v < 0 ? "down" : "";
const glyph = d => d > 0 ? "▲" : d < 0 ? "▼" : "·";
const dirColor = d => d > 0 ? C.up : d < 0 ? C.down : C.dim;

async function api(path) {
  const r = await fetch(path, { cache: "no-store" });
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}

/* ---------------------------------------------------------------- 图表 */
function themeOpts() {
  return {
    layout: { background: { type: "solid", color: "#0d1117" }, textColor: "#8b949e", fontSize: 11 },
    grid: { vertLines: { color: "#161b22" }, horzLines: { color: "#161b22" } },
    rightPriceScale: { borderColor: "#2b2f3a", scaleMargins: { top: 0.06, bottom: 0.24 } },
    timeScale: { borderColor: "#2b2f3a", timeVisible: true, secondsVisible: false, rightOffset: 5 },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "#4da6ff55", style: 2, labelBackgroundColor: "#1f6feb" },
      horzLine: { color: "#4da6ff55", style: 2, labelBackgroundColor: "#1f6feb" } },
    localization: { priceFormatter: p => fmt(p) },
  };
}

function initCharts() {
  const m = $("mainChart"), s = $("subChart");
  state.mainChart = LightweightCharts.createChart(m, Object.assign(themeOpts(),
    { width: m.clientWidth, height: m.clientHeight }));
  state.candleSeries = state.mainChart.addCandlestickSeries({
    upColor: C.up, downColor: C.down, borderVisible: false,
    wickUpColor: C.up, wickDownColor: C.down, priceLineColor: "#4da6ff88",
  });
  state.volSeries = state.mainChart.addHistogramSeries({
    priceScaleId: "vol", priceFormat: { type: "volume" },
    lastValueVisible: false, priceLineVisible: false,
  });
  state.mainChart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 }, borderVisible: false });

  state.subChart = LightweightCharts.createChart(s, Object.assign(themeOpts(), {
    width: s.clientWidth, height: s.clientHeight,
    rightPriceScale: { borderColor: "#2b2f3a", scaleMargins: { top: 0.12, bottom: 0.12 } },
  }));

  const link = (from, to) => r => {
    if (state.syncing || !r) return;
    state.syncing = true;
    try { to.timeScale().setVisibleLogicalRange(r); } catch (e) { /* ignore */ }
    state.syncing = false;
  };
  state.mainChart.timeScale().subscribeVisibleLogicalRangeChange(link(state.mainChart, state.subChart));
  state.subChart.timeScale().subscribeVisibleLogicalRangeChange(link(state.subChart, state.mainChart));

  const ro = new ResizeObserver(() => {
    if (state.mainChart) state.mainChart.applyOptions({ width: m.clientWidth, height: m.clientHeight });
    if (state.subChart) state.subChart.applyOptions({ width: s.clientWidth, height: s.clientHeight });
    if (state.snap) drawGauges();
  });
  ro.observe(m); ro.observe(s);
}

function lineData(times, vals) {
  const out = [];
  for (let i = 0; i < times.length; i++) {
    const v = num(vals[i]);
    if (v !== null) out.push({ time: times[i], value: v });
  }
  return out;
}

function clearOverlays() {
  for (const s of state.overlaySeries) { try { state.mainChart.removeSeries(s); } catch (e) { /* ignore */ } }
  state.overlaySeries = [];
}

function addOverlay(color, vals, opts) {
  const s = state.mainChart.addLineSeries(Object.assign({
    color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    crosshairMarkerVisible: false,
  }, opts || {}));
  s.setData(lineData(state.chartData.bars.time, vals));
  state.overlaySeries.push(s);
  return s;
}

const MA_COLORS = ["#f0b90b", "#4da6ff", "#c480ff", "#ff9500", "#79c0ff", "#d2a8ff"];
const EMA_COLORS = ["#39d353", "#2ea043", "#238636", "#196c2e"];

function renderChart() {
  const d = state.chartData;
  if (!d) return;
  const t = d.bars.time;
  state.candleSeries.setData(t.map((time, i) => ({
    time, open: d.bars.open[i], high: d.bars.high[i], low: d.bars.low[i], close: d.bars.close[i],
  })));
  state.volSeries.setData(t.map((time, i) => ({
    time, value: d.bars.volume[i],
    color: d.bars.close[i] >= d.bars.open[i] ? "#00b95630" : "#ff497630",
  })));

  clearOverlays();
  const ov = state.overlays;
  const legend = [];

  if (ov.ma) {
    Object.keys(d.ma).forEach((p, i) => {
      addOverlay(MA_COLORS[i % MA_COLORS.length], d.ma[p], { lineWidth: (p === "20" || p === "60") ? 1.5 : 1 });
    });
  }
  if (ov.ema) Object.keys(d.ema).forEach((p, i) => addOverlay(EMA_COLORS[i % EMA_COLORS.length], d.ema[p]));
  if (ov.boll) {
    addOverlay("#4da6ff", d.boll.mid, { lineStyle: 2 });
    addOverlay("#4da6ff66", d.boll.upper);
    addOverlay("#4da6ff66", d.boll.lower);
  }
  if (ov.st) {
    const up = [], dn = [];
    for (let i = 0; i < t.length; i++) {
      const v = num(d.supertrend.value[i]), dir = d.supertrend.direction[i];
      up.push(v === null ? null : (dir > 0 ? v : null));
      dn.push(v === null ? null : (dir < 0 ? v : null));
    }
    addOverlay(C.up, up, { lineWidth: 2 });
    addOverlay(C.down, dn, { lineWidth: 2 });
  }
  if (ov.vwap) addOverlay(C.purple, d.vwap, { lineWidth: 1.5 });

  // ---- TradingView 叠加 ----
  if (ov.ichimoku && d.ichimoku) {
    addOverlay("#f0b90b", d.ichimoku.tenkan, { lineWidth: 1.2 });
    addOverlay("#4da6ff", d.ichimoku.kijun, { lineWidth: 1.2 });
    addOverlay("#00b95688", d.ichimoku.senkou_a, { lineWidth: 1 });
    addOverlay("#ff497688", d.ichimoku.senkou_b, { lineWidth: 1 });
  }
  if (ov.psar && d.psar) addOverlay("#e6edf3", d.psar, { lineWidth: 1 });
  if (ov.keltner && d.keltner) {
    addOverlay(C.purple, d.keltner.mid, { lineStyle: 2 });
    addOverlay(C.purple + "77", d.keltner.upper);
    addOverlay(C.purple + "77", d.keltner.lower);
  }
  if (ov.donchian && d.donchian) {
    addOverlay(C.orange, d.donchian.upper);
    addOverlay(C.orange, d.donchian.lower);
  }
  if (ov.hma && d.hma) addOverlay("#39d353", d.hma, { lineWidth: 1.8 });
  if (ov.vwma && d.vwma) addOverlay("#79c0ff", d.vwma, { lineWidth: 1.4 });
  if (ov.chandelier && d.chandelier) {
    const up = [], dn = [];
    for (let i = 0; i < t.length; i++) {
      const v = num(d.chandelier.value[i]), dir = d.chandelier.direction[i];
      up.push(v === null ? null : (dir > 0 ? v : null));
      dn.push(v === null ? null : (dir < 0 ? v : null));
    }
    addOverlay(C.up, up, { lineWidth: 1.6, lineStyle: 2 });
    addOverlay(C.down, dn, { lineWidth: 1.6, lineStyle: 2 });
  }

  // 形态标记
  const markers = [];
  if (ov.markers) {
    for (const m of (d.markers || [])) {
      if (m.i < 0 || m.i >= t.length) continue;
      markers.push({
        time: t[m.i],
        position: m.dir > 0 ? "belowBar" : "aboveBar",
        color: dirColor(m.dir),
        shape: m.dir > 0 ? "arrowUp" : m.dir < 0 ? "arrowDown" : "circle",
        text: m.label,
      });
    }
  }
  markers.sort((a, b) => a.time - b.time);
  state.candleSeries.setMarkers(markers);

  // 图例
  if (ov.ma) {
    Object.keys(d.ma).forEach((p, i) => {
      const v = d.ma[p][d.ma[p].length - 1];
      if (v !== null) legend.push(`<span style="color:${MA_COLORS[i % MA_COLORS.length]}">MA${p} ${fmt(v)}</span>`);
    });
  }
  if (ov.boll) legend.push(`<span style="color:${C.accent}">BOLL ${fmt(last(d.boll.upper))} / ${fmt(last(d.boll.mid))} / ${fmt(last(d.boll.lower))}</span>`);
  if (ov.st) {
    const dir = d.supertrend.direction[t.length - 1];
    legend.push(`<span style="color:${dir > 0 ? C.up : C.down}">ST ${dir > 0 ? "多" : "空"} ${fmt(last(d.supertrend.value))}</span>`);
  }
  if (ov.vwap) legend.push(`<span style="color:${C.purple}">VWAP ${fmt(last(d.vwap))}</span>`);
  $("mainLegend").innerHTML = legend.join("");

  renderSub();
  state.mainChart.timeScale().fitContent();
  renderSR();
  setPriceLines(state.liq);
  applyPivots();
}

function applyPivots() {
  if (!state.candleSeries) return;
  for (const pl of (state.pivotLines || [])) {
    try { state.candleSeries.removePriceLine(pl); } catch (e) { /* ignore */ }
  }
  state.pivotLines = [];
  if (!state.overlays.pivot) return;
  const p = state.chartData && state.chartData.pivot;
  if (!p) return;
  const levels = [["P", C.accent], ["R1", C.down], ["R2", C.down], ["R3", C.down],
                  ["S1", C.up], ["S2", C.up], ["S3", C.up]];
  for (const [k, color] of levels) {
    if (!p[k]) continue;
    state.pivotLines.push(state.candleSeries.createPriceLine({
      price: p[k], color, lineWidth: 1, lineStyle: 3,
      axisLabelVisible: true, title: k,
    }));
  }
}

const last = a => a && a.length ? a[a.length - 1] : null;

function renderSub() {
  const d = state.chartData;
  if (!d) return;
  for (const s of state.subSeries) { try { state.subChart.removeSeries(s); } catch (e) { /* ignore */ } }
  state.subSeries = [];
  const t = d.bars.time;
  const add = (color, vals, opts) => {
    const s = state.subChart.addLineSeries(Object.assign({
      color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    }, opts || {}));
    s.setData(lineData(t, vals));
    state.subSeries.push(s);
    return s;
  };
  const hist = (vals, posColor) => {
    const s = state.subChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
    const rows = [];
    for (let i = 0; i < t.length; i++) {
      const v = num(vals[i]);
      if (v !== null) rows.push({ time: t[i], value: v, color: v >= 0 ? posColor : "#ff497688" });
    }
    s.setData(rows);
    state.subSeries.push(s);
  };
  const parts = [];
  const k = state.sub;

  if (k === "macd") {
    hist(d.macd.hist, "#00b95688");
    add("#4da6ff", d.macd.dif, { lineWidth: 1.4 });
    add("#f0b90b", d.macd.dea, { lineWidth: 1.4 });
    parts.push(`DIF ${fmt(last(d.macd.dif), 1)}`, `DEA ${fmt(last(d.macd.dea), 1)}`, `柱 ${fmt(last(d.macd.hist), 1)}`);
  } else if (k === "rsi") {
    add("#c480ff", d.rsi, { lineWidth: 1.6 });
    add("#ff497655", t.map(() => 70), { lineStyle: 2 });
    add("#00b95655", t.map(() => 30), { lineStyle: 2 });
    parts.push(`RSI ${fmt(last(d.rsi), 1)}`);
  } else if (k === "kdj") {
    add("#f0b90b", d.kdj.k); add("#4da6ff", d.kdj.d); add("#c480ff", d.kdj.j);
    parts.push(`K ${fmt(last(d.kdj.k), 1)}`, `D ${fmt(last(d.kdj.d), 1)}`, `J ${fmt(last(d.kdj.j), 1)}`);
  } else if (k === "adx") {
    add("#f0b90b", d.adx.adx, { lineWidth: 1.6 });
    add(C.up, d.adx.plus_di); add(C.down, d.adx.minus_di);
    add("#8b949e55", t.map(() => 25), { lineStyle: 2 });
    parts.push(`ADX ${fmt(last(d.adx.adx), 1)}`, `+DI ${fmt(last(d.adx.plus_di), 1)}`, `-DI ${fmt(last(d.adx.minus_di), 1)}`);
  } else if (k === "obv") {
    add(C.accent, d.obv, { lineWidth: 1.6 });
    parts.push(`OBV ${usd(last(d.obv))}`);
  } else if (k === "cci") {
    add("#c480ff", d.cci, { lineWidth: 1.6 });
    add("#ff497655", t.map(() => 100), { lineStyle: 2 });
    add("#00b95655", t.map(() => -100), { lineStyle: 2 });
    parts.push(`CCI ${fmt(last(d.cci), 1)}`);
  } else if (k === "mfi") {
    add("#f0b90b", d.mfi, { lineWidth: 1.6 });
    add("#ff497655", t.map(() => 80), { lineStyle: 2 });
    add("#00b95655", t.map(() => 20), { lineStyle: 2 });
    parts.push(`MFI ${fmt(last(d.mfi), 1)}`);
  } else if (k === "cmf") {
    hist(d.cmf, "#00b95688");
    parts.push(`CMF ${fmt(last(d.cmf), 3)}`);
  } else if (k === "wr") {
    add("#f0b90b", d.wr, { lineWidth: 1.6 });
    add("#ff497655", t.map(() => -20), { lineStyle: 2 });
    add("#00b95655", t.map(() => -80), { lineStyle: 2 });
    parts.push(`WR ${fmt(last(d.wr), 1)}`);
  } else if (k === "stochrsi") {
    add("#f0b90b", d.stochrsi.k); add("#4da6ff", d.stochrsi.d);
    parts.push(`K ${fmt(last(d.stochrsi.k), 1)}`, `D ${fmt(last(d.stochrsi.d), 1)}`);
  } else if (k === "stoch") {
    add("#f0b90b", d.stoch.k, { lineWidth: 1.6 });
    add("#4da6ff", d.stoch.d);
    add("#ff497655", t.map(() => 80), { lineStyle: 2 });
    add("#00b95655", t.map(() => 20), { lineStyle: 2 });
    parts.push(`%K ${fmt(last(d.stoch.k), 1)}`, `%D ${fmt(last(d.stoch.d), 1)}`);
  } else if (k === "ao") {
    hist(d.ao, "#00b95688");
    parts.push(`AO ${fmt(last(d.ao), 3)}`);
  } else if (k === "trix") {
    add(C.purple, d.trix.trix, { lineWidth: 1.6 });
    add("#f0b90b", d.trix.signal);
    parts.push(`TRIX ${fmt(last(d.trix.trix), 3)}`, `信号 ${fmt(last(d.trix.signal), 3)}`);
  } else if (k === "uo") {
    add("#e3b341", d.uo, { lineWidth: 1.6 });
    add("#ff497655", t.map(() => 70), { lineStyle: 2 });
    add("#00b95655", t.map(() => 30), { lineStyle: 2 });
    parts.push(`UO ${fmt(last(d.uo), 1)}`);
  } else if (k === "vortex") {
    add(C.up, d.vortex.vi_plus, { lineWidth: 1.6 });
    add(C.down, d.vortex.vi_minus, { lineWidth: 1.6 });
    parts.push(`VI+ ${fmt(last(d.vortex.vi_plus), 2)}`, `VI- ${fmt(last(d.vortex.vi_minus), 2)}`);
  } else if (k === "chaikin") {
    hist(d.chaikin.osc, "#00b95688");
    parts.push(`蔡金 ${fmt(last(d.chaikin.osc), 1)}`);
  } else if (k === "squeeze") {
    const s2 = state.subChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
    const rows = [];
    for (let i = 0; i < t.length; i++) {
      const v = num(d.squeeze.momentum[i]);
      if (v === null) continue;
      const on = d.squeeze.on[i];
      rows.push({ time: t[i], value: v, color: on ? "#ff9500aa" : (v >= 0 ? "#00b95688" : "#ff497688") });
    }
    s2.setData(rows);
    state.subSeries.push(s2);
    parts.push(`动能 ${fmt(last(d.squeeze.momentum), 3)}`,
      last(d.squeeze.on) ? "挤压中" : "已释放");
  } else if (k === "dpo") {
    hist(d.dpo, "#00b95688");
    parts.push(`DPO ${fmt(last(d.dpo), 3)}`);
  }
  $("subLegend").textContent = parts.join("   ");
}

/* ---------------------------------------------------------------- 仪表盘 */
function gaugeSpecs() {
  const s = state.snap || {};
  const p = s.positioning || {};
  return [
    { label: "恐贪指数", value: s.fear_greed && s.fear_greed.value, min: 0, max: 100, mid: 50, fmt: v => v.toFixed(0) },
    { label: "大户持仓比", value: p.top_position_ratio, min: 0, max: 4, mid: 1, fmt: v => v.toFixed(2) },
    { label: "大户账户比", value: p.top_account_ratio, min: 0, max: 4, mid: 1, fmt: v => v.toFixed(2) },
    { label: "全网多空比", value: p.global_ratio, min: 0, max: 4, mid: 1, fmt: v => v.toFixed(2) },
    { label: "主动买卖比", value: p.taker_ratio, min: 0, max: 2, mid: 1, fmt: v => v.toFixed(2) },
    { label: "资金费率", value: s.funding === null || s.funding === undefined ? null : s.funding * 100,
      min: -0.05, max: 0.05, mid: 0, fmt: v => v.toFixed(4) + "%" },
    { label: "OI 24h", value: s.oi_change_pct, min: -10, max: 10, mid: 0, fmt: v => v.toFixed(1) + "%" },
    { label: "量比", value: s.vol_ratio, min: 0, max: 3, mid: 1, fmt: v => v.toFixed(2) },
  ];
}

function drawGauges() {
  const row = $("gaugeRow");
  const specs = gaugeSpecs();
  if (row.childElementCount !== specs.length) {
    row.innerHTML = specs.map(sp =>
      `<div class="gauge-box"><canvas></canvas><div class="gauge-label">${sp.label}</div></div>`).join("");
  }
  row.querySelectorAll(".gauge-box").forEach((box, i) => {
    const cv = box.querySelector("canvas");
    const sp = specs[i];
    const v = num(sp.value);
    const label = box.querySelector(".gauge-label");
    label.textContent = sp.label + (v === null ? "" : " " + sp.fmt(v));
    const ratio = v === null ? 0 : Math.max(0, Math.min(1, (v - sp.min) / (sp.max - sp.min)));
    const color = v === null ? "#3a4152" : (v >= sp.mid ? C.up : C.down);
    drawArc(cv, ratio, color);
  });
}

function drawArc(canvas, ratio, color) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 90, h = canvas.clientHeight || 46;
  if (!w || !h) return;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const cx = w / 2, cy = h - 4;
  const r = Math.min(w / 2 - 5, h - 10);
  const a0 = Math.PI * 1.15, a1 = Math.PI * 1.85;     // 顶部扇形
  const span = a1 - a0;

  ctx.lineWidth = 5;
  ctx.lineCap = "round";
  ctx.strokeStyle = "#2b2f3a";
  ctx.beginPath();
  ctx.arc(cx, cy, r, a0, a1);
  ctx.stroke();

  if (ratio > 0.001) {
    ctx.strokeStyle = color;
    ctx.beginPath();
    ctx.arc(cx, cy, r, a0, a0 + span * ratio);
    ctx.stroke();
  }
  // 指针
  const ang = a0 + span * ratio;
  ctx.strokeStyle = "#e6edf3";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + Math.cos(ang) * (r - 7), cy + Math.sin(ang) * (r - 7));
  ctx.stroke();
}

/* ---------------------------------------------------------------- 快照渲染 */
function renderSnapshot(s) {
  state.snap = s;
  state.nextFunding = s.next_funding || 0;
  renderQuote(s);
  drawGauges();
  renderVenues(s);
  renderWalls(s);
  renderFlow(s);
  renderPositions(s);
}

function renderQuote(s) {
  const chg = num(s.change_pct);
  const imb = s.imbalance || {};
  const items = [];
  items.push(`<span class="q-price ${cls(chg)}" id="qPrice">${fmt(s.price)}</span>`);
  items.push(`<span class="q-change ${cls(chg)}">${pct(chg)}</span>`);
  items.push(`<span class="q-sep"></span>`);
  items.push(`<span class="q-item">高 <b>${fmt(s.high)}</b></span>`);
  items.push(`<span class="q-item">低 <b>${fmt(s.low)}</b></span>`);
  items.push(`<span class="q-item">OI <b>${usd(s.oi_usd)}</b></span>`);
  items.push(`<span class="q-item">费率 <b class="${s.funding > 0 ? "up" : s.funding < 0 ? "down" : ""}">${s.funding === null || s.funding === undefined ? "--" : (s.funding * 100).toFixed(4) + "%"}</b></span>`);
  items.push(`<span class="q-item">结算 <b id="qCountdown">--</b></span>`);
  items.push(`<span class="q-item">24h量 <b>${usd(s.vol_usd)}</b></span>`);
  items.push(`<span class="q-item">量比 <b>${fx(s.vol_ratio, 2)}</b></span>`);
  items.push(`<span class="q-item" title="±0.5% 内买/卖挂单比">盘口 <b class="${imb.ratio > 0.5 ? "up" : "down"}">${fx(imb.ratio * 100, 0)}${imb.ratio === null || imb.ratio === undefined ? "" : "%"}</b></span>`);
  if (s.xex) items.push(`<span class="q-item" title="${s.xex.count} 所">价差 <b>${fx(s.xex.pct, 3)}${s.xex.pct === null || s.xex.pct === undefined ? "" : "%"}</b></span>`);
  items.push(`<span class="q-item">CVD <b class="${cls(s.orderflow && s.orderflow.cvd)}">${usd(s.orderflow && s.orderflow.cvd)}</b></span>`);
  $("quoteBar").innerHTML = items.join("");
  tickCountdown();
}

function tickCountdown() {
  const el = $("qCountdown");
  if (!el) return;
  if (!state.nextFunding) { el.textContent = "--"; return; }
  const left = Math.max(0, state.nextFunding - Date.now()) / 1000;
  const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60);
  el.textContent = `${h}h${String(m).padStart(2, "0")}m`;
}

function renderVenues(s) {
  const vs = s.venues || [];
  const totalVol = vs.reduce((a, v) => a + (v.vol_usd || 0), 0);
  $("volLegend").textContent = `${vs.length} 所 · 合计 ${usd(totalVol)}`;

  const tbody = $("venueTable").querySelector("tbody");
  tbody.innerHTML = vs.map(v => `<tr>
    <td>${v.name}</td>
    <td>${fmt(v.last)}</td>
    <td class="${cls(v.change)}">${pct(v.change)}</td>
    <td>${usd(v.vol_usd)}</td>
    <td>${v.oi_usd ? usd(v.oi_usd) : "--"}</td>
    <td class="${v.funding > 0 ? "up" : v.funding < 0 ? "down" : ""}">${v.funding ? (v.funding * 100).toFixed(4) + "%" : "--"}</td>
  </tr>`).join("") || '<tr><td colspan="6" class="muted">无数据</td></tr>';

  // 量能条：按交易所成交量占比
  const top = vs.slice(0, 4);
  $("volSummary").innerHTML = top.map(v => {
    const share = totalVol ? (v.vol_usd || 0) / totalVol : 0;
    return `<div class="vol-row"><label>${v.name.slice(0, 6)}</label>
      <span class="vol-bar"><i class="vb-bid" style="width:${(share * 100).toFixed(1)}%"></i></span>
      <span class="vol-num">${usd(v.vol_usd)}</span></div>`;
  }).join("") + `<div class="vol-row"><label>盘口</label>
      <span class="vol-bar"><i class="vb-bid" style="width:${s.walls && s.walls.bid_total && s.walls.ask_total ? (s.walls.bid_total / (s.walls.bid_total + s.walls.ask_total) * 100).toFixed(1) : 50}%"></i><i class="vb-ask" style="flex:1"></i></span>
      <span class="vol-num">${usd(s.walls && (s.walls.bid_total + s.walls.ask_total))}</span></div>`;
}

function renderWalls(s) {
  const tier = state.wallTier;
  const w = s.walls || {};
  const rows = w[tier] || [];
  const labels = { near: "近", mid: "中", far: "远" };
  const stepPct = (w.tiers && w.tiers[tier]) || null;
  $("tierInfo").textContent =
    `${labels[tier]}档 步长 ${stepPct === null ? "--" : stepPct + "%"}` +
    `　深度 ${w.span_pct === null || w.span_pct === undefined ? "--" : "±" + (w.span_pct / 2).toFixed(2) + "%"}` +
    `　买 ${usd(w.bid_total)} / 卖 ${usd(w.ask_total)}`;
  const tbody = $("wallTable").querySelector("tbody");
  tbody.innerHTML = rows.map(w => `<tr>
    <td>${fmt(w.price)}</td>
    <td>${usd(w.total)}</td>
    <td class="${cls(w.dist_pct)}">${spct(w.dist_pct, 3)}</td>
    <td class="${w.side === "bid" ? "up" : "down"}">${w.side === "bid" ? "买墙" : "卖墙"}</td>
  </tr>`).join("") || '<tr><td colspan="4" class="muted">无数据</td></tr>';
}

function renderFlow(s) {
  const f = s.orderflow || {};
  $("flowStats").innerHTML = `
    <div class="liq-stat"><label>CVD 累计</label><b class="${cls(f.cvd)}">${usd(f.cvd)}</b></div>
    <div class="liq-stat"><label>1h 主动买</label><b class="up">${usd(f.buy_1h)}</b></div>
    <div class="liq-stat"><label>1h 主动卖</label><b class="down">${usd(f.sell_1h)}</b></div>`;

  const cv = $("cvdChart");
  const pts = (f.series || []).map(p => p[1]);
  if (!pts.length) return;
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 320, h = 64;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const min = Math.min(...pts), max = Math.max(...pts);
  const span = max - min || 1;
  const x = i => (i / (pts.length - 1)) * (w - 2) + 1;
  const y = v => h - 3 - ((v - min) / span) * (h - 8);
  const color = pts[pts.length - 1] >= pts[0] ? C.up : C.down;

  // 零轴
  if (min < 0 && max > 0) {
    ctx.strokeStyle = "#2b2f3a";
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(0, y(0)); ctx.lineTo(w, y(0)); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  pts.forEach((v, i) => i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)));
  ctx.stroke();
  ctx.lineTo(x(pts.length - 1), h); ctx.lineTo(x(0), h); ctx.closePath();
  ctx.fillStyle = color + "22";
  ctx.fill();
}

function renderPositions(s) {
  const p = s.positioning || {};
  const fg = s.fear_greed || {};
  const items = [
    ["大户持仓多空比", p.top_position_ratio === undefined ? "--" : p.top_position_ratio, 2],
    ["大户账户多空比", p.top_account_ratio === undefined ? "--" : p.top_account_ratio, 2],
    ["全网多空比", p.global_ratio === undefined ? "--" : p.global_ratio, 2],
    ["主动买卖比", p.taker_ratio === undefined ? "--" : p.taker_ratio, 2],
    ["恐贪指数", fg.value === undefined ? "--" : `${fg.value} ${fg.label || ""}`, null],
    ["OI 24h 变化", s.oi_change_pct === undefined ? "--" : pct(s.oi_change_pct), null],
  ];
  $("posGrid").innerHTML = items.map(([label, v, d]) => {
    const isNum = typeof v === "number";
    const color = !isNum ? "" : (label.indexOf("比") >= 0 ? (v > 1 ? "up" : "down") : cls(v));
    const txt = isNum ? (d === 2 ? v.toFixed(2) : fmt(v)) : v;
    return `<div class="beh-item"><label>${label}</label><b class="${color}">${txt}</b></div>`;
  }).join("");
}

function renderSR() {
  const d = state.chartData;
  if (!d) return;
  const price = d.snapshot.close;
  const rows = [];
  for (const r of (d.sr.resistance || []).slice(0, 3)) {
    rows.push({ type: "阻力", price: r.price, touch: r.touches, dist: (r.price / price - 1) * 100 });
  }
  for (const r of (d.sr.support || []).slice(0, 3)) {
    rows.push({ type: "支撑", price: r.price, touch: r.touches, dist: (r.price / price - 1) * 100 });
  }
  $("srLegend").textContent = d.volprofile && d.volprofile.poc ? `POC ${fmt(d.volprofile.poc)}` : "";
  $("srTable").querySelector("tbody").innerHTML = rows.map(r => `<tr>
    <td class="${r.type === "阻力" ? "down" : "up"}">${r.type}</td>
    <td>${fmt(r.price)}</td>
    <td>${r.touch}</td>
    <td class="${cls(r.dist)}">${r.dist > 0 ? "+" : ""}${r.dist.toFixed(2)}%</td>
  </tr>`).join("") || '<tr><td colspan="4" class="muted">无数据</td></tr>';
}

/* ---------------------------------------------------------------- 多周期 */
function renderMTF(m) {
  state.mtf = m;
  const rows = [...m.rows].sort((a, b) => TF_ORDER.indexOf(a.tf) - TF_ORDER.indexOf(b.tf));

  $("leftMeta").textContent = `${m.verdict_label} · ${m.consensus}`;
  $("leftMeta").style.color = m.direction > 0 ? C.up : m.direction < 0 ? C.down : C.dim;

  // 共振条
  $("cfBars").innerHTML = rows.map(r => {
    if (r.error) return `<div class="cf-row"><span class="tf" data-tf="${r.tf}">${r.tf}</span><span class="bar"></span><span class="v muted">--</span><span class="vd"></span></div>`;
    const v = r.score, w = Math.min(50, Math.abs(v) / 2);
    const style = v >= 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`;
    return `<div class="cf-row">
      <span class="tf" data-tf="${r.tf}">${r.tf}</span>
      <span class="bar"><i class="${v >= 0 ? "pos" : "neg"}" style="${style}"></i></span>
      <span class="v" style="color:${v > 0 ? C.up : v < 0 ? C.down : C.dim}">${v > 0 ? "+" : ""}${v.toFixed(0)}</span>
      <span class="vd">${r.verdict_label}</span>
    </div>`;
  }).join("");
  $("cfBars").querySelectorAll(".tf").forEach(el => { el.onclick = () => switchTF(el.dataset.tf); });

  // 多周期卡片
  $("trendStrip").innerHTML = rows.map(r => {
    if (r.error) return `<div class="trend-card"><div class="tc-head"><span class="tc-tf">${r.tf}</span></div><div class="tc-score muted">无数据</div></div>`;
    const pat = r.patterns && r.patterns.latest ? r.patterns.latest.label : "";
    const bias = r.patterns && r.patterns.bias;
    return `<div class="trend-card ${r.tf === state.tf ? "selected" : ""}" data-tf="${r.tf}">
      <div class="tc-head">
        <span class="tc-tf">${r.tf}</span>
        <span class="tc-trend ${r.verdict}">${r.verdict_label}</span>
      </div>
      <div class="tc-score">
        <span>分 ${r.score > 0 ? "+" : ""}${r.score.toFixed(0)}</span>
        <span style="color:${bias > 0 ? C.up : bias < 0 ? C.down : C.dim}">${pat || "—"}</span>
      </div>
    </div>`;
  }).join("");
  $("trendStrip").querySelectorAll(".trend-card[data-tf]").forEach(el => {
    el.onclick = () => switchTF(el.dataset.tf);
  });

  renderSignals();
  renderPatterns();
}

function currentRow() {
  const m = state.mtf;
  if (!m) return null;
  return m.rows.find(r => r.tf === state.tf && !r.error) || m.rows.find(r => !r.error);
}

function renderSignals() {
  const row = currentRow();
  if (!row) { $("sigList").innerHTML = '<div class="empty">无数据</div>'; $("sigTf").textContent = ""; return; }
  $("sigTf").textContent = `· ${row.tf} ${row.verdict_label} ${row.score > 0 ? "+" : ""}${row.score.toFixed(0)}`;
  $("sigList").innerHTML = row.signals.map(s => `<div class="sig-row ${s.dir === 0 ? "dim" : ""}"
      title="${s.label}｜权重 ${s.w}｜${s.note || ""}">
      <span class="dot" style="background:${dirColor(s.dir)}"></span>
      <span class="lab">${s.label}</span>
      <span class="val">${s.value === null || s.value === undefined ? (s.note || "") : fmt(s.value)}</span>
    </div>`).join("");
}

function renderPatterns() {
  const row = currentRow();
  if (!row) { $("patList").innerHTML = '<div class="empty">无数据</div>'; return; }
  const pats = [];
  for (const p of (row.patterns.chart || [])) pats.push(p);
  for (const p of (row.patterns.candles || [])) pats.push(p);
  $("patList").innerHTML = pats.slice(0, 6).map(p => `<div class="pat-row">
      <span class="nm" style="color:${dirColor(p.dir)}">${p.label}</span>
      <span>${p.note || ""}</span>
    </div>`).join("") || '<div class="empty">未识别到显著形态</div>';
}

/* ---------------------------------------------------------------- 数据加载 */
async function loadSnapshot() {
  try {
    const s = await api(`/api/snapshot?symbol=${state.symbol}`);
    renderSnapshot(s);
  } catch (e) {
    console.error(e);
    $("flowStats").innerHTML = `<div class="empty">快照失败: ${e.message}</div>`;
  }
}

async function loadMTF() {
  try {
    const m = await api(`/api/mtf?symbol=${state.symbol}`);
    renderMTF(m);
  } catch (e) {
    console.error(e);
    $("leftMeta").textContent = "研判失败";
  }
}

async function loadChart(keepView) {
  state.loading++;
  $("chartLoading").hidden = false;
  try {
    const range = keepView && state.mainChart ? state.mainChart.timeScale().getVisibleLogicalRange() : null;
    const d = await api(`/api/chart?symbol=${state.symbol}&tf=${state.tf}&limit=320`);
    state.chartData = d;
    renderChart();
    if (range) { try { state.mainChart.timeScale().setVisibleLogicalRange(range); } catch (e) { /* ignore */ } }
    $("hdrStale").hidden = !d.stale;
  } catch (e) {
    console.error(e);
    $("subLegend").textContent = "加载失败: " + e.message;
  } finally {
    state.loading--;
    if (state.loading <= 0) $("chartLoading").hidden = true;
  }
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    $("hdrTransport").textContent = "通道 " + (h.transport === "direct" ? "直连" : "代理");
    $("hdrTransport").title = `缓存 ${h.cache} 项 · 请求 ${h.requests} 次`;
  } catch (e) { /* ignore */ }
}

/* ---------------------------------------------------------------- 多空损 */
async function loadLiqmap() {
  try {
    const d = await api(`/api/liqmap?symbol=${state.symbol}&range=12`);
    state.liq = d;
    renderLiqmap(d);
    setPriceLines(d);
  } catch (e) {
    $("liqLegend").textContent = "估算失败";
    $("liqStats").innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
  }
}

function renderLiqmap(d) {
  const tb = $("liqTable").querySelector("tbody");
  if (!d || !d.ok) {
    $("liqLegend").textContent = "无数据";
    $("liqStats").innerHTML = "";
    tb.innerHTML = '<tr><td colspan="4" class="muted">该标的持仓量不足，无法估算</td></tr>';
    return;
  }
  $("liqLegend").textContent = `${d.model} · ±${d.range_pct}%`;
  $("liqStats").innerHTML = `
    <div class="liq-stat"><label>多头爆仓 3%内</label><b class="up">${usd(d.near3_long_usd)}</b></div>
    <div class="liq-stat"><label>空头爆仓 3%内</label><b class="down">${usd(d.near3_short_usd)}</b></div>
    <div class="liq-stat"><label>多头占比</label><b>${d.long_share === null ? "--" : d.long_share + "%"}</b></div>`;

  // 空头在上、多头在下，按金额排序各取 4
  const rows = [];
  for (const z of (d.top_short || []).slice(0, 4)) {
    rows.push({ ...z, kind: "空损", v: z.short_usd, cls: "down" });
  }
  for (const z of (d.top_long || []).slice(0, 4)) {
    rows.push({ ...z, kind: "多损", v: z.long_usd, cls: "up" });
  }
  tb.innerHTML = rows.map(z => `<tr>
      <td class="${z.cls}">${fmt(z.price)}</td>
      <td>${z.long_usd ? usd(z.long_usd) : "--"}</td>
      <td>${z.short_usd ? usd(z.short_usd) : "--"}</td>
      <td class="${cls(z.dist_pct)}">${spct(z.dist_pct, 2)}</td>
    </tr>`).join("") || '<tr><td colspan="4" class="muted">无数据</td></tr>';
}

function setPriceLines(d) {
  if (!state.candleSeries) return;
  for (const pl of (state.priceLines || [])) {
    try { state.candleSeries.removePriceLine(pl); } catch (e) { /* ignore */ }
  }
  state.priceLines = [];
  if (!d || !d.ok) return;
  const add = (z, color, title) => {
    if (!z) return;
    state.priceLines.push(state.candleSeries.createPriceLine({
      price: z.price, color, lineWidth: 1, lineStyle: 2,
      axisLabelVisible: true, title,
    }));
  };
  add(d.nearest_short, C.down, `空损 ${usd(d.nearest_short && d.nearest_short.short_usd)}`);
  add(d.nearest_long, C.up, `多损 ${usd(d.nearest_long && d.nearest_long.long_usd)}`);
}

/* ---------------------------------------------------------------- 标的搜索 */
let allSymbols = [];

async function loadAllSymbols() {
  try {
    const d = await api("/api/symbols/all");
    allSymbols = d.symbols || [];
  } catch (e) { allSymbols = []; }
}

function filterSymbols(q) {
  const s = (q || "").trim().toUpperCase();
  if (!s) return allSymbols.slice(0, 40);
  const exact = [], starts = [], contains = [];
  for (const it of allSymbols) {
    const sym = it.symbol.toUpperCase();
    if (sym === s) exact.push(it);
    else if (sym.startsWith(s)) starts.push(it);
    else if (sym.indexOf(s) >= 0) contains.push(it);
  }
  // allSymbols 已按成交额排序，三类内部自然保持「越活跃越靠前」
  return exact.concat(starts, contains).slice(0, 40);
}

function showDrop(list, q) {
  const drop = $("symDrop");
  if (!list.length) {
    drop.innerHTML = '<div class="sym-empty">无匹配交易对</div>';
    drop.hidden = false;
    return;
  }
  drop.innerHTML = list.map((it, i) =>
    `<div class="sym-item" data-sym="${it.symbol}" data-i="${i}">
      <span>${it.symbol}</span><span class="p">${it.pair}</span></div>`).join("");
  drop.hidden = false;
  drop.querySelectorAll(".sym-item").forEach(el => {
    el.onmousedown = e => {            // 用 mousedown 抢在 blur 之前
      e.preventDefault();
      pickSymbol(el.dataset.sym);
    };
  });
}

function pickSymbol(sym) {
  $("symDrop").hidden = true;
  $("symInput").value = sym;
  $("symInput").blur();
  switchSymbol(sym);
}

function bindSearch() {
  const inp = $("symInput");
  inp.addEventListener("input", () => showDrop(filterSymbols(inp.value), inp.value));
  inp.addEventListener("focus", () => showDrop(filterSymbols(inp.value), inp.value));
  inp.addEventListener("keydown", e => {
    const items = $("symDrop").querySelectorAll(".sym-item");
    if (e.key === "Escape") { $("symDrop").hidden = true; inp.blur(); return; }
    if (e.key === "Enter") {
      if (items.length) pickSymbol(items[0].dataset.sym);
      return;
    }
    if (e.key === "ArrowDown" && items.length) { e.preventDefault(); items[0].focus?.(); }
  });
  inp.addEventListener("blur", () => { setTimeout(() => { $("symDrop").hidden = true; }, 150); });
}

/* ---------------------------------------------------------------- 交互 */
function switchTF(tf) {
  state.tf = tf;
  document.querySelectorAll("#tfGroup .tf-btn").forEach(b => b.classList.toggle("active", b.dataset.tf === tf));
  if (state.mtf) {
    $("trendStrip").querySelectorAll(".trend-card").forEach(el =>
      el.classList.toggle("selected", el.dataset.tf === tf));
  }
  renderSignals();
  renderPatterns();
  loadChart(false);
}

function switchSymbol(sym) {
  state.symbol = sym;
  $("symInput").value = sym;
  document.querySelectorAll("#symbolGroup .tb-btn").forEach(el => el.classList.toggle("active", el.dataset.sym === sym));
  loadSnapshot();
  loadChart(false);
  loadMTF();
  loadLiqmap();
}

async function loadSymbols() {
  try {
    const d = await api("/api/symbols");
    $("symbolGroup").innerHTML = (d.symbols || []).map(s =>
      `<button class="tb-btn ${s.symbol === state.symbol ? "active" : ""}" data-sym="${s.symbol}"
        title="${s.last ? fmt(s.last) + "  " + pct(s.change_pct) : ""}">${s.symbol}</button>`).join("");
    $("symbolGroup").querySelectorAll(".tb-btn").forEach(el => { el.onclick = () => switchSymbol(el.dataset.sym); });

    $("tfGroup").innerHTML = (d.timeframes || []).map(tf =>
      `<button class="tf-btn ${tf === state.tf ? "active" : ""}" data-tf="${tf}">${tf}</button>`).join("");
    $("tfGroup").querySelectorAll(".tf-btn").forEach(el => { el.onclick = () => switchTF(el.dataset.tf); });
  } catch (e) {
    $("symbolGroup").innerHTML = `<span class="pill">加载失败</span>`;
  }
}

const OV_TOGGLES = [
  ["ma", "MA"], ["ema", "EMA"], ["boll", "BOLL"], ["st", "超级趋势"], ["vwap", "VWAP"],
  ["ichimoku", "一目"], ["psar", "SAR"], ["keltner", "肯特纳"], ["donchian", "唐奇安"],
  ["hma", "HMA"], ["vwma", "VWMA"], ["chandelier", "吊灯"], ["pivot", "枢轴"], ["markers", "形态"],
];
const SUB_TOGGLES = [
  ["macd", "MACD"], ["rsi", "RSI"], ["kdj", "KDJ"], ["adx", "ADX"], ["obv", "OBV"],
  ["cci", "CCI"], ["mfi", "MFI"], ["cmf", "CMF"], ["wr", "WR"], ["stochrsi", "StochRSI"],
  ["stoch", "随机"], ["ao", "AO"], ["trix", "TRIX"], ["uo", "UO"], ["vortex", "涡旋"],
  ["chaikin", "蔡金"], ["squeeze", "挤压"], ["dpo", "DPO"],
];

function renderToolbar() {
  const ov = OV_TOGGLES.map(([k, label]) =>
    `<label class="ind-toggle ${state.overlays[k] ? "on" : ""}" data-ov="${k}">
      <input type="checkbox" ${state.overlays[k] ? "checked" : ""}>${label}</label>`).join("");
  const sub = SUB_TOGGLES.map(([k, label]) =>
    `<label class="ind-toggle ${state.sub === k ? "on" : ""}" data-sub="${k}">
      <input type="checkbox" ${state.sub === k ? "checked" : ""}>${label}</label>`).join("");
  $("indToolbar").innerHTML = ov + sub;

  $("indToolbar").querySelectorAll("[data-ov]").forEach(el => {
    el.onclick = e => {
      e.preventDefault();
      const k = el.dataset.ov;
      state.overlays[k] = !state.overlays[k];
      renderToolbar();
      if (state.chartData) renderChart();
    };
  });
  $("indToolbar").querySelectorAll("[data-sub]").forEach(el => {
    el.onclick = e => {
      e.preventDefault();
      state.sub = el.dataset.sub;
      renderToolbar();
      if (state.chartData) renderSub();
    };
  });
}

function bindUI() {
  $("btnRefresh").onclick = () => { loadSnapshot(); loadChart(false); loadMTF(); loadHealth(); };
  $("btnReport").onclick = openReport;
  $("rpClose").onclick = closeReport;
  $("rpCopy").onclick = copyReport;
  $("rpDownload").onclick = downloadReport;
  $("rpAsk").onclick = askAI;
  $("rpClearAi").onclick = () => { $("rpAiOut").innerHTML = ""; };
  $("rpQuestion").addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); askAI(); }
  });
  $("rpModal").onclick = e => { if (e.target.id === "rpModal") closeReport(); };
  $("btnBacktest").onclick = openBacktest;
  $("btClose").onclick = closeBacktest;
  $("btRun").onclick = runBacktest;
  $("btModal").onclick = e => { if (e.target.id === "btModal") closeBacktest(); };
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (!$("rpModal").hidden) closeReport();
    if (!$("btModal").hidden) closeBacktest();
  });
  $("wallTabs").querySelectorAll(".ptab").forEach(btn => {
    btn.onclick = () => {
      state.wallTier = btn.dataset.tier;
      $("wallTabs").querySelectorAll(".ptab").forEach(b => b.classList.toggle("active", b === btn));
      if (state.snap) renderWalls(state.snap);
    };
  });
}

/* ---------------------------------------------------------------- 回测 */
function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function openBacktest() { $("btModal").hidden = false; runBacktest(); }
function closeBacktest() { $("btModal").hidden = true; }

async function runBacktest() {
  const box = $("btBody");
  box.innerHTML = '<div class="empty">回测计算中，首次约 10~20 秒…</div>';
  try {
    const h = $("btHorizon").value;
    const d = await api(`/api/backtest?symbol=${state.symbol}&tf=${state.tf}&horizon=${h}&bars=3000`);
    renderBacktest(d);
  } catch (e) {
    box.innerHTML = `<div class="empty">回测失败：${escapeHtml(e.message)}</div>`;
  }
}

function renderBacktest(d) {
  const days = Math.round((d.span.to - d.span.from) / 86400000);
  $("btMeta").textContent =
    `${d.symbol} · ${d.base_tf} · ${days} 天 · ${d.samples} 样本 · 周期 ${d.tfs_used.join("/")}`;
  const c = v => (v === null || v === undefined) ? "" : v > 0.05 ? "up" : v < -0.05 ? "down" : "";
  const s = d.strategy || {};
  let html = `<div class="bt-verdict">${escapeHtml(d.verdict)}</div>
    <div class="bt-cards">
      <div class="bt-card"><label>共振 IC</label><b class="${c(d.ic.mtf)}">${d.ic.mtf ?? "--"}</b><small>|IC| &gt; 0.05 有参考价值</small></div>
      <div class="bt-card"><label>单周期 IC（对照）</label><b class="${c(d.ic.single)}">${d.ic.single ?? "--"}</b><small>只看 ${d.base_tf}</small></div>
      <div class="bt-card"><label>强信号次数</label><b>${d.strong.up_n + d.strong.dn_n}</b><small>|分| &gt; 50</small></div>
      <div class="bt-card"><label>策略 / 买入持有</label><b class="${(s.ret_pct ?? 0) >= 0 ? "up" : "down"}">${s.ret_pct ?? "--"}%</b><small>同期持有 ${s.buyhold_pct ?? "--"}%</small></div>
    </div>`;

  html += `<div class="bt-sec">各周期单独预测力（IC）</div>`;
  for (const tf of Object.keys(d.ic.per_tf)) {
    const v = d.ic.per_tf[tf];
    const w = Math.min(50, Math.abs(v || 0) * 200);
    const left = (v || 0) >= 0 ? "50%" : `${50 - w}%`;
    const color = (v || 0) >= 0 ? C.up : C.down;
    html += `<div class="bt-tfrow"><span class="tf">${tf}</span>
      <span class="bt-tfbar"><i style="left:${left};width:${w}%;background:${color}"></i></span>
      <span class="v" style="color:${color}">${v ?? "--"}</span></div>`;
  }

  const row = b => {
    const bad = b.edge !== null && b.edge < -0.05;
    const good = b.edge !== null && b.edge > 0.2;
    const cc = b.edge === null ? "muted" : b.edge > 0 ? "up" : "down";
    return `<tr class="${good ? "strong" : bad ? "bad" : ""}">
      <td>${b.label}</td><td>${b.n}</td>
      <td>${b.avg_ret === null ? "--" : b.avg_ret + "%"}</td>
      <td>${b.hit_rate === null ? "--" : b.hit_rate + "%"}</td>
      <td class="${cc}">${b.edge === null ? "--" : (b.edge > 0 ? "+" : "") + b.edge + "%"}</td></tr>`;
  };
  const th = `<thead><tr><th>分数区间</th><th>样本</th><th>均值收益</th><th>方向胜率</th><th>按方向期望</th></tr></thead>`;

  html += `<div class="bt-sec">共振分 → 未来收益</div>
    <table class="bt-tbl">${th}<tbody>${d.buckets.map(row).join("")}</tbody></table>`;
  html += `<div class="bt-sec">对照：只看 ${d.base_tf} 单周期评分</div>
    <table class="bt-tbl">${th}<tbody>${d.single_buckets.map(row).join("")}</tbody></table>`;
  html += `<div class="bt-note">
    绿底 = 按方向期望 &gt; +0.2%；红底 = 期望为负（反着做更好）。<br>
    策略按「每 ${s.step || "-"} 根按分数方向换仓」模拟，不含手续费与滑点。<br>
    样本越短结论越不可靠：单一涨跌行情会把 IC 推到 ±0.3 以上，那只是行情特征。
  </div>`;
  $("btBody").innerHTML = html;
}

/* ---------------------------------------------------------------- 研判报告 */
let reportData = null;

async function openReport() {
  $("rpModal").hidden = false;
  $("rpBody").innerHTML = '<div class="empty">正在汇总多周期数据，约 3~10 秒…</div>';
  $("rpMeta").textContent = "";
  loadAiStatus();
  try {
    const d = await api(`/api/report?symbol=${state.symbol}&force=1`);
    reportData = d;
    renderReport(d);
  } catch (e) {
    $("rpBody").innerHTML = `<div class="empty">生成失败：${escapeHtml(e.message)}</div>`;
  }
}

function closeReport() { $("rpModal").hidden = true; }

function renderReport(d) {
  const t = new Date(d.ts);
  $("rpMeta").textContent =
    `${d.symbol} · ${t.toLocaleString("zh-CN", { hour12: false })}`;

  const toneCls = v => v > 0 ? "up" : v < 0 ? "down" : "";
  const H = s => `<div class="rp-h">${s}</div>`;
  let h = "";

  h += `<div class="rp-headline">${escapeHtml(d.headline)}</div>`;

  h += `<div class="rp-cards">${(d.cards || []).map(c =>
    `<div class="rp-card"><label>${escapeHtml(c.label)}</label>
      <b class="${toneCls(c.tone)}">${escapeHtml(String(c.value))}</b>
      <small>${escapeHtml(c.note || "")}</small></div>`).join("")}</div>`;

  h += H("事实层");
  h += `<table class="rp-tbl"><thead><tr><th>指标</th><th class="num">数值</th><th>说明</th></tr></thead><tbody>` +
    (d.facts || []).map(x =>
      `<tr><td>${escapeHtml(x.k)}</td><td class="num">${escapeHtml(String(x.v))}</td>
        <td class="muted">${escapeHtml(x.note || "")}</td></tr>`).join("") + "</tbody></table>";

  h += H("多周期结构");
  h += `<table class="rp-tbl"><thead><tr>
      <th>周期</th><th class="num">得分</th><th>判定</th><th class="num">权重</th>
      <th class="num">加权贡献</th><th>主要依据</th></tr></thead><tbody>` +
    (d.mtf.rows || []).map(r => {
      if (r.error) return `<tr><td>${r.tf}</td><td colspan="5" class="muted">无数据</td></tr>`;
      const contrib = (r.score * r.weight);
      const tops = r.top.filter(x => x.dir !== 0)
        .map(x => `<span class="${toneCls(x.dir)}">${x.dir > 0 ? "多" : "空"}${escapeHtml(x.label)}</span>`)
        .join("、");
      return `<tr><td>${r.tf}</td>
        <td class="num ${toneCls(r.score)}">${r.score > 0 ? "+" : ""}${r.score.toFixed(1)}</td>
        <td class="${toneCls(r.score)}">${escapeHtml(r.verdict)}</td>
        <td class="num">${r.weight}</td>
        <td class="num ${toneCls(contrib)}">${contrib > 0 ? "+" : ""}${contrib.toFixed(1)}</td>
        <td>${tops || '<span class="muted">—</span>'}</td></tr>`;
    }).join("") + "</tbody></table>";

  h += H("关键价位");
  h += `<div class="rp-levels">` + Object.entries(d.levels || {}).map(([tf, c]) => {
    const vp = c.vp || {};
    const p = c.pivot || {};
    const row = (k, v) => v ? `<div class="row"><span>${k}</span><span>${v}</span></div>` : "";
    return `<div class="rp-level">
      <div class="t">${tf} · ${escapeHtml(c.structure || "")}</div>
      ${row("支撑", (c.support || []).map(fmt).join(" / ") || "--")}
      ${row("阻力", (c.resistance || []).map(fmt).join(" / ") || "--")}
      ${row("POC", fmt(vp.poc))}${row("VAH/VAL", `${fmt(vp.vah)} / ${fmt(vp.val)}`)}
      ${p.P ? row("枢轴 P", fmt(p.P)) : ""}
      ${row("ADX / RSI", `${fx(c.adx, 1)} / ${fx(c.rsi, 1)}`)}
      ${row("云顶 / 云底", `${fmt(c.cloud_top)} / ${fmt(c.cloud_bot)}`)}
    </div>`;
  }).join("") + "</div>";

  const lq = d.liq || {};
  if (lq.ok) {
    h += H("多空损（预估清算）");
    h += `<table class="rp-tbl"><thead><tr>
        <th>位置</th><th class="num">价格</th><th class="num">距离</th>
        <th class="num">多损</th><th class="num">空损</th></tr></thead><tbody>`;
    const zrow = (name, z) => z ? `<tr><td>${name}</td><td class="num">${fmt(z.price)}</td>
        <td class="num ${toneCls(z.dist_pct)}">${spct(z.dist_pct, 2)}</td>
        <td class="num">${z.long_usd ? usd(z.long_usd) : "--"}</td>
        <td class="num">${z.short_usd ? usd(z.short_usd) : "--"}</td></tr>` : "";
    h += zrow("最近多头区", lq.nearest_long) + zrow("最近空头区", lq.nearest_short);
    h += `<tr><td colspan="3"><b>3% 内累计</b></td>
        <td class="num up">${usd(lq.near3_long)}</td>
        <td class="num down">${usd(lq.near3_short)}</td></tr>`;
    h += "</tbody></table>";
    h += `<div class="bt-note">模型：${escapeHtml(lq.model || "")}　按方向与量级参考，具体数值为估算。</div>`;
  }

  h += H("矛盾与辨析");
  h += (d.findings || []).map(f =>
    `<div class="rp-finding ${toneCls(f.tone)}">
      <div class="ft">${escapeHtml(f.title)}</div>
      <div class="fb">${escapeHtml(f.body)}</div></div>`).join("");

  h += H("观察清单");
  h += `<div class="rp-watch">${(d.watch || []).map(w =>
    `<div><div class="wt">${escapeHtml(w.title)}</div>
      <ul>${w.items.map(x => `<li>${escapeHtml(x)}</li>`).join("")}</ul></div>`).join("")}</div>`;

  h += H("可靠性边界");
  h += `<table class="rp-tbl"><thead><tr><th>环节</th><th>可信度</th><th>说明</th></tr></thead><tbody>` +
    (d.reliability || []).map(r =>
      `<tr><td>${escapeHtml(r.item)}</td><td>${escapeHtml(r.level)}</td>
        <td class="muted">${escapeHtml(r.note)}</td></tr>`).join("") + "</tbody></table>";

  h += `<div class="rp-disclaimer">${escapeHtml(d.disclaimer || "")}</div>`;

  $("rpBody").innerHTML = h;
}

async function copyReport() {
  if (!reportData || !reportData.markdown) return;
  const txt = reportData.markdown;
  try {
    await navigator.clipboard.writeText(txt);
    $("rpCopy").textContent = "已复制";
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = txt;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); $("rpCopy").textContent = "已复制"; }
    catch (e2) { $("rpCopy").textContent = "复制失败"; }
    document.body.removeChild(ta);
  }
  setTimeout(() => { $("rpCopy").textContent = "复制"; }, 1600);
}

function downloadReport() {
  if (!reportData || !reportData.markdown) return;
  const blob = new Blob([reportData.markdown], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${reportData.symbol}_研判_${new Date().toISOString().slice(0, 10)}.md`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); document.body.removeChild(a); }, 500);
}

/* ---------------------------------------------------------------- AI 解读 */
let aiStatus = null;
let aiStreaming = false;

async function loadAiStatus() {
  try {
    aiStatus = await api("/api/ai/status");
  } catch (e) {
    aiStatus = { configured: false, hint: "无法获取 AI 状态：" + e.message };
  }
  const el = $("rpAiStatus");
  const ask = $("rpAsk");
  const q = $("rpQuestion");
  if (aiStatus.configured) {
    el.textContent = `${aiStatus.provider || "custom"} · ${aiStatus.model}`;
    el.style.color = "";
    ask.disabled = false;
    q.disabled = false;
    ask.title = "";
  } else {
    el.textContent = "未配置 · 设置环境变量 TD_AI_KEY 后重启";
    el.style.color = "var(--orange)";
    ask.disabled = true;
    q.disabled = true;
    ask.title = aiStatus.hint || "";
    $("rpAiOut").innerHTML = `<div class="rp-ai-tip">${escapeHtml(aiStatus.hint || "")}</div>`;
  }
}

async function askAI() {
  if (!aiStatus || !aiStatus.configured || aiStreaming) return;
  aiStreaming = true;
  const out = $("rpAiOut");
  out.innerHTML = "";
  const cursor = document.createElement("span");
  cursor.className = "ai-cursor";
  out.appendChild(cursor);

  const q = ($("rpQuestion").value || "").trim();
  const btn = $("rpAsk");
  btn.disabled = true;
  btn.textContent = "生成中…";
  try {
    const resp = await fetch("/api/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: state.symbol, question: q }),
    });
    if (!resp.ok || !resp.body) {
      let msg = "HTTP " + resp.status;
      try { const j = await resp.json(); msg = j.error || msg; } catch (e) { /* ignore */ }
      out.innerHTML = `<div class="rp-ai-err">${escapeHtml(msg)}</div>`;
      return;
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of raw.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          let obj;
          try { obj = JSON.parse(line.slice(6)); } catch (e) { continue; }
          handleAiEvent(obj);
        }
      }
    }
  } catch (e) {
    out.innerHTML = `<div class="rp-ai-err">请求失败：${escapeHtml(e.message)}</div>`;
  } finally {
    const c = $("rpAiOut").querySelector(".ai-cursor");
    if (c) c.remove();
    aiStreaming = false;
    btn.disabled = !(aiStatus && aiStatus.configured);
    btn.textContent = "解读";
  }
}

function handleAiEvent(obj) {
  const out = $("rpAiOut");
  if (!out) return;
  if (obj.type === "text") {
    let node = out.querySelector(".ai-text");
    if (!node) {
      node = document.createElement("span");
      node.className = "ai-text";
      const cur = out.querySelector(".ai-cursor");
      if (cur) out.insertBefore(node, cur); else out.appendChild(node);
    }
    node.textContent = (node.textContent || "") + obj.content;
    return;
  }
  if (obj.type === "verify") {
    if (obj.unknown && obj.unknown.length) {
      const d = document.createElement("div");
      d.className = "rp-ai-warn";
      d.textContent = "以下数值未在原始数据中找到，请谨慎采信：" + obj.unknown.join("、");
      out.appendChild(d);
    }
    return;
  }
  if (obj.type === "error") {
    const d = document.createElement("div");
    d.className = "rp-ai-err";
    d.textContent = "生成失败：" + obj.message;
    out.appendChild(d);
    return;
  }
  if (obj.type === "done") {
    const c = out.querySelector(".ai-cursor");
    if (c) c.remove();
  }
}

/* ---------------------------------------------------------------- 启动 */
function startPolling() {
  const tick = (fn, ms) => state.timers.push(setInterval(() => { if (!document.hidden) fn(); }, ms));
  tick(loadSnapshot, 10000);
  tick(() => loadChart(true), 20000);
  tick(loadMTF, 45000);
  tick(loadLiqmap, 30000);
  tick(loadHealth, 60000);
  state.timers.push(setInterval(tickCountdown, 1000));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { loadSnapshot(); loadMTF(); }
  });
}

async function boot() {
  initCharts();
  bindUI();
  bindSearch();
  renderToolbar();
  $("symInput").value = state.symbol;
  await loadSymbols();
  loadAllSymbols();
  loadHealth();
  await Promise.all([loadSnapshot(), loadChart(false), loadMTF(), loadLiqmap()]);
  startPolling();
}

window.addEventListener("DOMContentLoaded", boot);
