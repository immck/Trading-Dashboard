/* 多周期交易看板 */
"use strict";

const $ = id => document.getElementById(id);
const C = { up: "#00b956", down: "#ff4976", accent: "#4da6ff", dim: "#8b949e",
            purple: "#c480ff", orange: "#ff9500" };
const TF_ORDER = ["15m", "30m", "1h", "4h", "1d", "1w"];

const state = {
  symbol: "BTC", tf: "1h", sub: null, wallTier: "near", venueFilter: "all", market: "crypto", newsTag: "all",
  overlays: {
    ma: false, ema: false, boll: false, st: false, vwap: false, markers: false,
    ichimoku: false, psar: false, keltner: false, donchian: false,
    hma: false, vwma: false, chandelier: false, pivot: false,
  },
  snap: null, mtf: null, chartData: null, liq: null, priceLines: [], wallLines: [],
  mainChart: null, subChart: null, candleSeries: null, volSeries: null,
  overlaySeries: [], subSeries: [], syncing: false, loading: 0,
  nextFunding: 0, timers: [], analysisHidden: true, gaugesHidden: true, chartToolsHidden: true,
  trendHidden: true, derivHidden: true, chartMaximized: false, chartLiq: false,
  chartTools: { walls: false, reg50: false, reg100: false, reg200: false, divergence: false, twoB: false },
  newsItems: [], newsMarkers: true, selectedNews: null,
  events: [], eventTypes: {}, eventsOn: true, eventMapMode: false,
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

/* 美股标的一律加 US: 前缀发给后端，避免与同名加密标的混淆 */
function apiSym() {
  return state.market === "us" ? "US:" + state.symbol : state.symbol;
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
  // 事件竖线要跟着缩放/平移实时重算
  state.mainChart.timeScale().subscribeVisibleLogicalRangeChange(() => scheduleEvents());
  state.mainChart.subscribeClick(handleChartClick);

  const ro = new ResizeObserver(() => {
    if (state.mainChart) state.mainChart.applyOptions({ width: m.clientWidth, height: m.clientHeight });
    if (state.subChart) state.subChart.applyOptions({ width: s.clientWidth, height: s.clientHeight });
    if (state.snap) drawGauges();
    scheduleEvents();
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

/* ---------------------------------------------------------------- 事件时间轴 */
const TF_SECONDS = {
  "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
  "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400, "3d": 259200,
  "1w": 604800, "1M": 2592000,
};
// 顶栏只放常用的 6 个，其余走下拉
const QUICK_TF = ["15m", "30m", "1h", "4h", "1d", "1w"];

/** 把后端返回的周期表拆成「顶栏按钮」和「下拉全量」两份 */
function splitTimeframes(all) {
  const list = Array.isArray(all) ? all : [];
  return { quick: list.filter(tf => QUICK_TF.includes(tf)), all: list };
}
const NEWS_COLORS = { crypto: "#c480ff", stock: "#4da6ff", macro: "#ff9500", other: "#8b949e" };

/** 事件 → 对应 K 线下标。
 *  日级事件（UTC 零点）挂到当天第一根（股票日线从开盘时刻起算也能对上）；
 *  分钟级事件挂到「包含该时刻」的那一根。 */
function eventBarIndex(times, ev) {
  if (!times || !times.length || ev == null) return -1;
  const t = Number(ev.time);
  if (!isFinite(t)) return -1;
  const midnight = t % 86400 === 0;
  const i = midnight ? lowerBound(times, t) : Math.max(0, lowerBound(times, t + 1) - 1);
  return (i >= 0 && i < times.length) ? i : -1;
}

function lowerBound(times, ts) {
  let lo = 0, hi = times.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (times[mid] < ts) lo = mid + 1; else hi = mid; }
  return lo;
}

async function loadEvents() {
  try {
    // 只请求已勾选的分类，未勾的（默认含「行业」）在服务端就过滤掉，省带宽
    const off = EVENT_CATEGORIES.map(([id]) => id).filter(id => !enabledEventCategories.has(id));
    const d = await api(`/api/events?symbol=${apiSym()}&exclude=${off.join(",")}`);
    state.events = d.events || [];
    state.eventTypes = d.types || {};
    renderMapArchive();
    renderEventLegend();
    scheduleEvents();
    if (state.eventMapMode && state.chartData) updateEventMapStats(state.chartData);
  } catch (e) {
    state.events = [];
    $("evLegend").textContent = "事件加载失败";
    scheduleEvents();
  }
}

function renderEventLegend() {
  const used = new Set((state.events || []).map(e => e.type));
  const types = state.eventTypes || {};
  const html = Object.keys(types)
    .filter(k => used.has(k))
    .map(k => `<span><i style="background:${types[k].color}"></i>${types[k].label}</span>`)
    .join("");
  // 地图模式用 evLegend，首页用 evLegendFallback，两边都写
  for (const id of ["evLegend", "evLegendFallback"]) {
    const el = $(id);
    if (el) el.innerHTML = html;
  }
}

let evRaf = 0;
function scheduleEvents() {
  if (evRaf) return;
  evRaf = requestAnimationFrame(() => { evRaf = 0; renderEvents(); });
}

/**
 * 把事件画成竖线 + 顶部标签。
 * lightweight-charts v4 没有内置竖线，所以用绝对定位的 HTML 覆盖层，
 * 坐标通过 timeScale().logicalToCoordinate(barIndex) 换算，随缩放平移实时重算。
 */
function renderEvents() {
  const box = $("evOverlay");
  if (!box) return;
  const chart = state.mainChart;
  const d = state.chartData;
  if (!chart || !d) { box.innerHTML = ""; return; }

  const main = $("mainChart");
  const w = (main && main.clientWidth) || box.clientWidth || 0;
  const h = (main && main.clientHeight) || 0;
  if (w <= 0 || h <= 0) { box.innerHTML = ""; return; }
  box.style.height = h + "px";

  const ts = chart.timeScale();
  const out = [];
  if (state.eventMapMode && mapSettings.future && d.bars.time.length) {
    const boundary = ts.logicalToCoordinate(d.bars.time.length);
    if (boundary !== null && boundary < w - 65) out.push(`<div class="map-future-region" style="left:${Math.max(0,boundary)}px;width:${Math.max(0,w - 65 - boundary)}px"><span>未来时间轴<br>尚无行情 · 不绘制预测 K 线</span></div>`);
  }
  if (state.eventsOn && (state.events || []).length) out.push(...renderEventLines(d, ts, w, h));
  if (!state.eventMapMode && state.newsMarkers && (state.newsItems || []).length) out.push(...renderNewsLabels(d, ts, w, h));
  if (state.eventMapMode && !enabledEventCategories.size) out.push('<div class="map-empty">未选择事件分类</div>');
  box.innerHTML = out.join("");
}

function eventCategory(ev) {
  const type = ev.category || ev.type || "other";
  return ({ macro: "policy", regulation: "policy", etf: "industry", upgrade: "industry", incident: "other", peak: "other" })[type] || type;
}

/** 按可视区能放下的标签数做等距抽样，保证首尾都保留（最新的那条一定在） */
function thinByCapacity(items, w, h, boxW, boxH, gap) {
  if (items.length <= 1) return items;
  const cols = Math.max(1, Math.floor(w / (boxW * 0.55)));
  const rows = Math.max(2, Math.floor((h - 60) / (boxH + gap)));
  const cap = Math.max(6, cols * rows);
  if (items.length <= cap) return items;
  const step = items.length / cap;
  const picked = [];
  for (let i = 0; i < cap; i++) picked.push(items[Math.min(items.length - 1, Math.round(i * step))]);
  // 保证最后一条（最新事件）一定被选中
  if (picked[picked.length - 1] !== items[items.length - 1]) picked[picked.length - 1] = items[items.length - 1];
  return [...new Set(picked)];
}

/** 历史事件：竖虚线 + 顶部「标题栏 + 日期」双层标签（参考 TradingView/traderax 的画法） */
function renderEventLines(d, ts, w, h) {
  const times = d.bars.time;
  if (!times.length) return [];
  const lastIdx = times.length - 1;
  const tfSec = TF_SECONDS[state.tf] || 86400;
  const BOX_W = Math.min(state.eventMapMode ? 225 : 170, Math.max(110, w - 90)), BOX_H = state.eventMapMode ? 34 : 36, GAP = 7;
  const placed = [];
  const out = [];

  const items = [];
  for (const ev of state.events) {
    if (!enabledEventCategories.has(eventCategory(ev))) continue;
    const future = ev.time > Date.now() / 1000;
    if (state.eventMapMode && future && !mapSettings.future) continue;
    let x = null;
    if (future) {
      // 未来事件按周期推算逻辑位置；落到可视区外就贴右边并标注「预估」
      const steps = Math.ceil((ev.time - times[lastIdx]) / tfSec);
      try { x = ts.logicalToCoordinate(lastIdx + Math.min(steps, 400)); } catch (e) { x = null; }
      if (state.eventMapMode && (x === null || x === undefined || x > w - 65)) continue;
      if (x === null || x === undefined || x > w - 4) x = w - 6;
    } else {
      if (ev.time < times[0] || ev.time >= times[lastIdx] + tfSec) continue;
      const idx = eventBarIndex(times, ev);
      if (idx < 0) continue;
      try { x = ts.logicalToCoordinate(idx); } catch (e) { x = null; }
    }
    if (x === null || x === undefined || x < -40 || x > w + 40) continue;
    items.push({ ev, x, future });
  }
  items.sort((a, b) => a.x - b.x);

  // 事件可能上千条（BLS 归档回到 2008 年），先按可视容量均匀抽样，
  // 否则左边早期的标签会占满所有行，最近发生的事件反而被挤掉。
  const list = thinByCapacity(items, w, h, BOX_W, BOX_H, GAP);
  // 未来事件（预估）优先占位：它们通常在最右边，容易被历史标签抢光行
  const order = [...list].sort((a, b) => (b.future ? 1 : 0) - (a.future ? 1 : 0) || a.x - b.x);

  for (const { ev, x, future } of order) {
    const color = ev.color || "#8b949e";
    // 默认放在线右侧，靠右就翻到左侧
    let left = x + 3;
    if (left + BOX_W > w - 2) left = Math.max(0, x - BOX_W - 3);

    let top = 8;
    const maxRows = Math.max(1, Math.floor(Math.min(h * .48, 260) / (BOX_H + GAP)));
    let available = false;
    for (let k = 0; k < maxRows; k++) {
      const hit = placed.some(o => !(left + BOX_W < o.x1 || left > o.x2 || top + BOX_H < o.y1 || top > o.y2));
      if (!hit) { available = true; break; }
      // 先试翻到另一侧，再不行就往下换行
      const alt = left === x + 3 ? Math.max(0, x - BOX_W - 3) : x + 3;
      const altHit = alt + BOX_W <= w && !placed.some(o =>
        !(alt + BOX_W < o.x1 || alt > o.x2 || top + BOX_H < o.y1 || top > o.y2));
      if (altHit) { left = alt; available = true; break; }
      top += BOX_H + GAP;
    }
    if (!available || top + BOX_H > h - 6) continue;
    placed.push({ x1: left, x2: left + BOX_W, y1: top, y2: top + BOX_H });

    const title = future ? `${ev.title}（${ev.scheduled ? "计划" : "预估"}）` : ev.title;
    const date = future ? `${ev.date} 预计` : ev.date;
    out.push(`<div class="ev-line${future ? " future" : ""}" style="left:${x.toFixed(1)}px;top:${(top + BOX_H).toFixed(1)}px;color:${color}"></div>`);
    const priceY = state.candleSeries.priceToCoordinate(d.bars.close[Math.max(0, Math.min(lastIdx, lowerBound(times, ev.time + 1) - 1))]);
    if (!future && priceY !== null) out.push(`<span class="ev-price-dot" style="left:${x.toFixed(1)}px;top:${priceY.toFixed(1)}px;background:${color}"></span>`);
    // 有实际公布值就换成大字显示，一眼看到「公布了多少」
    const num = parseFloat(String(ev.value || "").replace(/[^\d.+-]/g, ""));
    const tone = isFinite(num) ? (num > 0 ? "up" : num < 0 ? "down" : "") : "";
    const value = ev.value
      ? `<span class="ev-v ${tone}">${escapeHtml(String(ev.value).replace(/^同比\s*/, ""))}</span>`
      : "";
    out.push(`<div class="ev-label${future ? " future" : ""}${ev.value ? " has-value" : ""}" data-event-detail-index="${state.events.indexOf(ev)}" role="button" tabindex="0" style="left:${left.toFixed(1)}px;top:${top.toFixed(1)}px;width:${BOX_W}px;color:${color}">
      <div class="ev-h" style="background:${color}30;border-left:3px solid ${color}"><b>${escapeHtml(title || "")}</b></div>
      <small class="ev-d">${value}${escapeHtml(date || "")}${ev.label && !ev.value ? " · " + escapeHtml(ev.label) : ""}</small>
    </div>`);
  }
  return out;
}

function truncate(s, n) {
  s = String(s || "");
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function eventEnabled() {
  return true;
}

/** 把同一条 K 线上的多条快讯合并，保留最重要/最新的一条 */
function newsLabelGroups(times, items, tf) {
  const byIdx = new Map();
  for (const item of (items || [])) {
    if (!eventEnabled(item)) continue;
    const i = newsBarIndex(times, item.ts, tf);
    if (i < 0) continue;
    const cur = byIdx.get(i);
    if (!cur) {
      byIdx.set(i, { i, best: item, count: 1 });
    } else {
      cur.count++;
      if ((item.important ? 1 : 0) > (cur.best.important ? 1 : 0)) cur.best = item;
      else if ((item.important ? 1 : 0) === (cur.best.important ? 1 : 0) && item.ts > cur.best.ts) cur.best = item;
    }
  }
  return [...byIdx.values()];
}

/** 快讯：与历史事件地图同一套样式（标题栏 + 时间行），并用虚线连到对应 K 线 */
function renderNewsLabels(d, ts, w, h) {
  const groups = newsLabelGroups(d.bars.time, state.newsItems, state.tf);
  if (!groups.length || !state.candleSeries || typeof state.candleSeries.priceToCoordinate !== "function") return [];

  const BOX_W = 156, BOX_H = 32;
  const occ = [];
  const out = [];
  groups.sort((a, b) => (b.best.important ? 1 : 0) - (a.best.important ? 1 : 0) || (b.best.ts - a.best.ts));

  for (const g of groups.slice(0, 24)) {
    const x = ts.logicalToCoordinate(g.i);
    if (x === null || x === undefined || x < -20 || x > w + 20) continue;
    let y = null;
    try { y = state.candleSeries.priceToCoordinate(d.bars.high[g.i]); } catch (e) { y = null; }
    if (y === null || y === undefined || !isFinite(y)) continue;

    let left = x + 3;
    if (left + BOX_W > w) left = Math.max(0, x - BOX_W - 3);
    let top = y - BOX_H - 8;
    for (let k = 0; k < 6; k++) {
      const hit = occ.some(o => !(left + BOX_W < o.x1 || left > o.x2 || top + BOX_H < o.y1 || top > o.y2));
      if (!hit) break;
      top -= BOX_H + 4;
    }
    if (top < 0) continue;
    occ.push({ x1: left, x2: left + BOX_W, y1: top, y2: top + BOX_H });

    const t = new Date(g.best.ts).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" });
    const color = NEWS_COLORS[g.best.tag] || "#8b949e";
    const more = g.count > 1 ? ` +${g.count - 1}` : "";
    const full = g.count > 1
      ? `${t} ${g.best.title || ""}（同一K线上共 ${g.count} 条快讯）`
      : `${t} ${g.best.title || ""}`;
    const newsIndex = state.newsItems.indexOf(g.best);
    // 从标签底部连到 K 线高点，视觉上和事件竖线一致
    const lineTop = top + BOX_H;
    const lineH = Math.max(3, y - lineTop);
    out.push(`<div class="nw-link" style="left:${x.toFixed(1)}px;top:${lineTop.toFixed(1)}px;height:${lineH.toFixed(1)}px;color:${color}"></div>`);
    out.push(`<div class="ev-label nw-box" data-news-label-index="${newsIndex}" role="button" tabindex="0"
        style="left:${left.toFixed(1)}px;top:${top.toFixed(1)}px;width:${BOX_W}px;color:${color}"
        title="点击查看新闻详情：${escapeHtml(full)}">
      <div class="ev-h" style="background:${color}22"><b>${escapeHtml(truncate(g.best.title || "快讯", 16))}</b></div>
      <small class="ev-d">${t} · ${escapeHtml(g.best.source || "")}${more}</small>
    </div>`);
  }
  return out;
}

function renderChart() {
  const d = state.chartData;
  if (!d) return;
  const t = d.bars.time;
  const candles = t.map((time, i) => ({
    time, open: d.bars.open[i], high: d.bars.high[i], low: d.bars.low[i], close: d.bars.close[i],
  }));
  if (state.eventMapMode && mapSettings.future && t.length) {
    const step = TF_SECONDS[state.tf] || 604800;
    const end = Math.min(Math.max(t.at(-1), ...state.events.filter(e => e.time > t.at(-1) && enabledEventCategories.has(eventCategory(e))).map(e => e.time)), t.at(-1) + step * 400);
    for (let time = t.at(-1) + step; time <= end + step; time += step) candles.push({time});
  }
  state.candleSeries.setData(candles);
  state.mainChart.applyOptions({rightPriceScale:{mode:state.eventMapMode && mapSettings.log ? 1 : 0, scaleMargins:{top:state.eventMapMode ? .32 : .06,bottom:.16}}});
  state.volSeries.setData(t.map((time, i) => ({
    time, value: d.bars.volume[i],
    color: d.bars.close[i] >= d.bars.open[i] ? "#00b95630" : "#ff497630",
  })));

  clearOverlays();
  const ov = state.overlays;
  const legend = [];
  if (state.eventMapMode && mapSettings.ma) addOverlay("#e2bc63", t.map((_, i) => i < 19 ? null : d.bars.close.slice(i - 19, i + 1).reduce((a,b) => a+b,0)/20));

  if (ov.ma) {
    Object.keys(d.ma).forEach((p, i) => {
      addOverlay(MA_COLORS[i % MA_COLORS.length], d.ma[p], { lineWidth: (p === "20" || p === "60") ? 1.5 : 1 });
    });
  }
  if (ov.ema) Object.keys(d.ema).forEach((p, i) => addOverlay(EMA_COLORS[i % EMA_COLORS.length], d.ema[p]));
  for (const [key, period, color] of [["reg50", 50, "#f0b90b"], ["reg100", 100, C.accent], ["reg200", 200, C.purple]]) {
    if (!state.chartTools[key]) continue;
    const band = regressionChannel(d.bars.close, period);
    addOverlay(color, band.mid, { lineWidth: 1.5 });
    addOverlay(color + "77", band.upper, { lineWidth: 1 });
    addOverlay(color + "77", band.lower, { lineWidth: 1 });
  }
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
  if (state.chartTools.divergence) markers.push(...findDivergences(d).map(x => ({ time: t[x.i], position: x.dir > 0 ? "belowBar" : "aboveBar", color: x.dir > 0 ? C.up : C.down, shape: x.dir > 0 ? "arrowUp" : "arrowDown", text: `RSI背离 ${x.dir > 0 ? "底" : "顶"}` })));
  if (state.chartTools.twoB) markers.push(...findTwoB(d.bars).map(x => ({ time: t[x.i], position: x.dir > 0 ? "belowBar" : "aboveBar", color: x.dir > 0 ? C.up : C.down, shape: x.dir > 0 ? "arrowUp" : "arrowDown", text: `2B${x.dir > 0 ? "多" : "空"}` })));
  // 快讯不再用圆点标记，改为 evOverlay 里的「时间 + 短标题」标签（见 renderNewsLabels）

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
  setWallLines(state.snap);
  applyPivots();
  scheduleEvents();
}

const CHART_TOOLS = [
  ["liq", "清算", "估算清算价位", true], ["walls", "挂单墙", "当前订单簿挂单墙快照（非历史轨迹）", true],
  ["estimated", "预估清算", "预估清算价位", true], ["reg50", "回归50", "50根K线线性回归 ±1标准差", true],
  ["reg100", "回归100", "100根K线线性回归 ±1标准差", true], ["reg200", "回归200", "200根K线线性回归 ±1标准差", true],
  ["ema", "EMA带", "切换 EMA 均线", true], ["kc", "双KC", "切换肯特纳通道", true],
  ["div", "背离", "RSI 与价格的局部高低点背离", true], ["patterns", "形态", "切换K线形态标记", true],
  ["twoB", "2B", "20根区间假突破并在3根内收回", true],
];

function renderChartTools() {
  $("chartTools").innerHTML = CHART_TOOLS.map(([key, label, tip, enabled]) => {
    const active = key === "liq" || key === "estimated" ? state.chartLiq
      : key === "walls" ? state.chartTools.walls
      : key === "ema" ? state.overlays.ema : key === "kc" ? state.overlays.keltner
        : key === "patterns" ? state.overlays.markers
          : key === "div" ? state.chartTools.divergence : state.chartTools[key] || false;
    return `<button class="chart-tool ${active ? "active" : ""}" data-chart-tool="${key}" title="${tip}" ${enabled ? "" : "disabled"}>${label}</button>`;
  }).join("");
}

function regressionChannel(values, period) {
  const n = values.length, start = Math.max(0, n - period), len = n - start;
  const mid = Array(n).fill(null), upper = Array(n).fill(null), lower = Array(n).fill(null);
  if (len < 3) return { mid, upper, lower };
  let sx = 0, sy = 0, sxx = 0, sxy = 0;
  for (let j = 0; j < len; j++) { const y = num(values[start + j]); sx += j; sy += y; sxx += j * j; sxy += j * y; }
  const slope = (len * sxy - sx * sy) / (len * sxx - sx * sx), intercept = (sy - slope * sx) / len;
  const residuals = [];
  for (let j = 0; j < len; j++) residuals.push(num(values[start + j]) - (intercept + slope * j));
  const sd = Math.sqrt(residuals.reduce((sum, x) => sum + x * x, 0) / len);
  for (let j = 0; j < len; j++) { const i = start + j, v = intercept + slope * j; mid[i] = v; upper[i] = v + sd; lower[i] = v - sd; }
  return { mid, upper, lower };
}

function swingPoints(values, high = true, radius = 3) {
  const out = [];
  for (let i = radius; i < values.length - radius; i++) {
    const v = num(values[i]); if (v === null) continue;
    let extreme = true;
    for (let j = i - radius; j <= i + radius; j++) if (j !== i && (high ? num(values[j]) >= v : num(values[j]) <= v)) { extreme = false; break; }
    if (extreme) out.push({ i, v });
  }
  return out;
}

function findDivergences(d) {
  const lows = swingPoints(d.bars.low, false).filter(x => num(d.rsi[x.i]) !== null);
  const highs = swingPoints(d.bars.high, true).filter(x => num(d.rsi[x.i]) !== null);
  const found = [];
  for (const [points, dir] of [[lows, 1], [highs, -1]]) {
    const a = points.at(-2), b = points.at(-1); if (!a || !b) continue;
    const rsiA = num(d.rsi[a.i]), rsiB = num(d.rsi[b.i]);
    if ((dir > 0 && b.v < a.v && rsiB > rsiA) || (dir < 0 && b.v > a.v && rsiB < rsiA)) found.push({ i: b.i, dir });
  }
  return found;
}

function findTwoB(bars) {
  const found = [], n = bars.close.length, lookback = 20, maxReturnBars = 3;
  for (let i = lookback; i < n; i++) {
    const top = Math.max(...bars.high.slice(i - lookback, i)), bottom = Math.min(...bars.low.slice(i - lookback, i));
    if (bars.high[i] > top && bars.close[i] < top) found.push({ i, dir: -1 });
    else if (bars.low[i] < bottom && bars.close[i] > bottom) found.push({ i, dir: 1 });
    else if (i >= maxReturnBars) {
      for (let k = 1; k < maxReturnBars; k++) {
        const j = i - k, prevTop = Math.max(...bars.high.slice(j - lookback, j)), prevBottom = Math.min(...bars.low.slice(j - lookback, j));
        if (bars.high[j] > prevTop && bars.close[i] < prevTop) { found.push({ i, dir: -1 }); break; }
        if (bars.low[j] < prevBottom && bars.close[i] > prevBottom) { found.push({ i, dir: 1 }); break; }
      }
    }
  }
  return found;
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
  const hidden = !state.sub;
  $("subChart").hidden = hidden;
  $("subLegend").hidden = hidden;
  for (const s of state.subSeries) { try { state.subChart.removeSeries(s); } catch (e) { /* ignore */ } }
  state.subSeries = [];
  if (hidden) {
    $("subLegend").textContent = "";
    setTimeout(() => { if (state.mainChart) state.mainChart.applyOptions({}); }, 0);
    return;
  }
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
  renderDerivStrip(s);
}

function drawSpark(canvas, points, color) {
  const ctx = canvas && canvas.getContext && canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.clientWidth || 120, h = canvas.clientHeight || 28;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
  const values = (points || []).map(x => num(Array.isArray(x) ? x[1] : x)).filter(x => x !== null);
  if (values.length < 2) return;
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
  values.forEach((v, i) => {
    const x = 2 + (w - 4) * i / (values.length - 1);
    const y = h - 2 - (h - 4) * (v - lo) / span;
    if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
  });
  ctx.stroke();
}

function renderDerivStrip(s) {
  const p = s.positioning || {}, flow = s.orderflow || {};
  const oi = s.oi_series || [];
  const ratioParts = [
    ["大户持仓", p.top_position_ratio], ["大户账户", p.top_account_ratio], ["全网多空", p.global_ratio],
  ].filter(x => num(x[1]) !== null);
  const ratioText = ratioParts.length
    ? ratioParts.map(([k, v]) => `${k} ${fx(v, 2)}`).join(" · ")
    : "暂无多空比数据";
  $("derivStrip").innerHTML = `
    <div class="deriv-card"><div><span>OI 持仓量 · 24h</span><b class="${cls(s.oi_change_pct)}">${usd(s.oi_usd)} · ${pct(s.oi_change_pct)}</b></div><canvas id="oiSpark" aria-label="OI 24小时走势"></canvas><small>来源：Binance U 本位永续 · ${oi.length} 点</small></div>
    <div class="deriv-card"><div><span>主动买卖 CVD</span><b class="${cls(flow.cvd)}">${usd(flow.cvd)}</b></div><canvas id="cvdSpark" aria-label="CVD 历史走势"></canvas><small>近 ${flow.series && flow.series.length || 0} 根 1h K 线估算</small></div>
    <div class="deriv-card deriv-ratios"><div><span>多空比快照</span><b>${ratioParts.length ? "当前值" : "--"}</b></div><p>${ratioText}</p><small>公开账户比例，不代表全市场真实持仓</small></div>`;
  drawSpark($("oiSpark"), oi, C.accent);
  drawSpark($("cvdSpark"), flow.series, num(flow.cvd) >= 0 ? C.up : C.down);
}

function openTool(title, html) {
  $("toolTitle").textContent = title;
  $("toolBody").innerHTML = html;
  $("toolModal").hidden = false;
}

function openWhatIf() {
  if (state.market !== "crypto") {
    openTool("目标价推演", '<div class="empty">美股没有永续合约持仓量，无法估算清算风险。</div>'); return;
  }
  const price = num(state.snap && state.snap.price);
  const levels = state.liq && state.liq.ok ? state.liq.levels || [] : [];
  if (!price || !levels.length) {
    openTool("目标价推演", '<div class="empty">清算分布尚不可用，暂时无法进行情景估算。</div>'); return;
  }
  const lo = price * 0.88, hi = price * 1.12, target = price;
  openTool(`目标价推演 · ${escapeHtml(state.symbol)}`, `<div class="tool-intro">按当前 ${escapeHtml(state.liq.model || "模型")} 估算分布，计算目标价路径上可能触发的清算名义金额。不是交易所实时强平订单。</div>
    <div class="tool-input-row"><input id="wiSlider" type="range" min="${lo}" max="${hi}" step="${Math.max(price * 0.0001, 0.00000001)}" value="${target}"><input id="wiPrice" type="number" min="${lo}" max="${hi}" step="any" value="${target}"></div>
    <div id="wiResult" class="tool-result"></div>`);
  const calc = () => {
    const t = num($("wiPrice").value), out = $("wiResult");
    if (!t || t <= 0) { out.textContent = "请输入有效目标价。"; return; }
    $("wiSlider").value = Math.max(lo, Math.min(hi, t));
    const falling = t < price;
    const hit = levels.filter(z => falling ? z.price < price && z.price >= t : z.price > price && z.price <= t);
    const amount = hit.reduce((sum, z) => sum + (num(falling ? z.long_usd : z.short_usd) || 0), 0);
    const oi = num(state.snap.oi_usd);
    out.innerHTML = `<b>${falling ? "下跌触发多头" : t > price ? "上涨触发空头" : "目标价等于现价"} · ${usd(amount)}</b><span>目标价 ${fmt(t)} · 距现价 ${spct((t / price - 1) * 100, 2)} · 覆盖 ${hit.length} 个估算价位${oi ? ` · 约占 OI ${(amount / oi * 100).toFixed(2)}%` : ""}</span><small>模型估算会受杠杆分布和维持保证金假设影响；未覆盖区间及真实账户风险可能不同。</small>`;
  };
  $("wiSlider").addEventListener("input", () => { $("wiPrice").value = $("wiSlider").value; calc(); });
  $("wiPrice").addEventListener("input", calc); calc();
}

function openLiqCalc() {
  const price = num(state.snap && state.snap.price);
  if (state.market !== "crypto") {
    openTool("爆仓价计算", '<div class="empty">爆仓价仅适用于杠杆衍生品，美股现货不适用。</div>'); return;
  }
  openTool(`爆仓价计算 · ${escapeHtml(state.symbol)}`, `<div class="tool-intro">简化估算公式：多头 = 开仓价 × (1 - 1/杠杆 + 维持保证金率)，空头 = 开仓价 × (1 + 1/杠杆 - 维持保证金率)。未计手续费、逐仓/全仓差异和交易所分档规则。</div>
    <div class="calc-grid"><label>方向<select id="calcSide" class="model-select"><option value="long">做多</option><option value="short">做空</option></select></label><label>杠杆<input id="calcLev" type="number" min="1" max="125" step="1" value="10"></label><label>开仓价<input id="calcEntry" type="number" min="0" step="any" value="${price || ""}"></label><label>维持保证金率 (%)<input id="calcMmr" type="number" min="0" max="50" step="0.1" value="0.4"></label></div><div id="calcResult" class="tool-result"></div>`);
  const calc = () => {
    const side = $("calcSide").value, lev = num($("calcLev").value), entry = num($("calcEntry").value), mmr = num($("calcMmr").value);
    const out = $("calcResult");
    if (!entry || entry <= 0 || !lev || lev < 1 || mmr === null || mmr < 0 || mmr >= 100) { out.textContent = "请填写有效开仓价、杠杆和保证金率。"; return; }
    const rate = mmr / 100;
    const liq = entry * (side === "long" ? 1 - 1 / lev + rate : 1 + 1 / lev - rate);
    const distance = price ? (liq / price - 1) * 100 : null;
    out.innerHTML = `<b>${side === "long" ? "多头" : "空头"}估算清算价 · ${fmt(liq)}</b><span>相对现价 ${distance === null ? "--" : spct(distance, 2)} · ${lev}x · 维持保证金 ${mmr}%</span><small>这是简化数学估算，不是交易所强平价；请以交易所仓位页面为准。</small>`;
  };
  ["calcSide", "calcLev", "calcEntry", "calcMmr"].forEach(id => $(id).addEventListener("input", calc)); calc();
}

function closeTool() { $("toolModal").hidden = true; }

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
  const all = s.venues || [];
  const vs = state.venueFilter === "spot" ? [] : all;
  const tbody = $("venueTable").querySelector("tbody");
  const fundingRows = all.filter(v => num(v.funding) !== null)
    .map(v => ({ ...v, annual: Number(v.funding) * 3 * 365 * 100 }))
    .sort((a, b) => b.annual - a.annual);
  $("fundingPanel").innerHTML = fundingRows.length
    ? `<table class="tbl funding-table"><thead><tr><th>交易所</th><th>费率/期</th><th>年化估算</th></tr></thead><tbody>${fundingRows.map(v => `<tr><td>${v.name}</td><td class="${cls(v.funding)}">${(v.funding * 100).toFixed(4)}%</td><td class="${cls(v.annual)}">${v.annual > 0 ? "+" : ""}${v.annual.toFixed(1)}%</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty compact">${state.market === "us" ? "美股无永续资金费率" : "暂无资金费率数据"}</div>`;
  if (!vs.length) {
    $("volLegend").textContent = state.market === "us" ? "美股无多所聚合" : state.venueFilter === "spot" ? "现货源未接入" : "无数据";
    $("volSummary").innerHTML = state.market === "us" ? "" : `<div class="empty compact">现货行情源未接入，当前量能仅统计合约</div>`;
    tbody.innerHTML = `<tr><td colspan="7" class="muted">${state.market === "us" ? "美股不做多交易所量能聚合" : state.venueFilter === "spot" ? "暂无现货数据源" : "无数据"}</td></tr>`;
    return;
  }
  const totalVol = vs.reduce((a, v) => a + (v.vol_usd || 0), 0);
  $("volLegend").textContent = `${vs.length} 所合约 · ${usd(totalVol)}`;

  tbody.innerHTML = vs.map(v => `<tr>
    <td>${v.name}</td>
    <td>合约</td>
    <td>${fmt(v.last)}</td>
    <td class="${cls(v.change)}">${pct(v.change)}</td>
    <td>${usd(v.vol_usd)}</td>
    <td>${num(v.oi_usd) !== null ? usd(v.oi_usd) : "--"}</td>
    <td class="${cls(v.funding)}">${num(v.funding) !== null ? (v.funding * 100).toFixed(4) + "%" : "--"}</td>
  </tr>`).join("") || '<tr><td colspan="7" class="muted">无数据</td></tr>';

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

  const large = ["near", "mid", "far"].flatMap(key => (w[key] || []).map(row => ({ ...row, tier: labels[key] })))
    .filter(row => num(row.total) !== null && Number(row.total) >= 50000)
    .sort((a, b) => b.total - a.total)
    .filter((row, i, list) => list.findIndex(x => x.price === row.price && x.side === row.side) === i)
    .slice(0, 10);
  $("largeOrders").innerHTML = large.length
    ? `<table class="tbl large-order-table"><thead><tr><th>价格</th><th>金额</th><th>距离</th><th>档位</th><th>方向</th></tr></thead><tbody>${large.map(row => `<tr><td>${fmt(row.price)}</td><td>${usd(row.total)}</td><td>${spct(row.dist_pct, 3)}</td><td>${row.tier}</td><td class="${row.side === "bid" ? "up" : "down"}">${row.side === "bid" ? "买" : "卖"}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty compact">当前盘口没有 ≥$50K 的分层挂单</div>`;
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
    const s = await api(`/api/snapshot?symbol=${apiSym()}`);
    renderSnapshot(s);
  } catch (e) {
    console.error(e);
    $("flowStats").innerHTML = `<div class="empty">快照失败: ${e.message}</div>`;
  }
}

async function loadMTF() {
  try {
    const m = await api(`/api/mtf?symbol=${apiSym()}`);
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
    const limit = state.eventMapMode ? 1500 : (state.tf === "1w" ? 1200 : (state.tf === "1d" ? 1200 : 320));
    const historyQuery = state.eventMapMode ? "&history=1&want=1500" : "";
    const d = await api(`/api/chart?symbol=${apiSym()}&tf=${state.tf}&limit=${limit}${historyQuery}`);
    state.chartData = d;
    renderChart();
    if (range && !state.eventMapMode) { try { state.mainChart.timeScale().setVisibleLogicalRange(range); } catch (e) { /* ignore */ } }
    if (state.eventMapMode) state.mainChart.timeScale().fitContent();
    updateEventMapStats(d);
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
    const d = await api(`/api/liqmap?symbol=${apiSym()}&range=12`);
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
    const msg = state.market === "us"
      ? "美股没有持仓量/杠杆数据，多空损仅适用于加密永续"
      : "该标的持仓量不足，无法估算";
    tb.innerHTML = `<tr><td colspan="4" class="muted">${msg}</td></tr>`;
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
  if (!state.chartLiq || !d || !d.ok) return;

  // 关键：价格线必须落在当前 K 线的范围内，否则会把价格轴撑开，
  // 导致蜡烛被压成一条线（切标的时旧的清算线残留就会这样）。
  const bars = state.chartData && state.chartData.bars;
  let lo = -Infinity, hi = Infinity;
  if (bars && bars.low && bars.high) {
    const lows = bars.low.filter(x => x !== null && x !== undefined);
    const highs = bars.high.filter(x => x !== null && x !== undefined);
    if (lows.length && highs.length) {
      lo = Math.min(...lows);
      hi = Math.max(...highs);
      const pad = (hi - lo) * 0.5 || hi * 0.1;
      lo -= pad;
      hi += pad;
    }
  }

  const add = (z, color, title) => {
    if (!z) return;
    const p = num(z.price);
    if (p === null || p <= 0 || p < lo || p > hi) return;   // 超出可视范围，跳过
    state.priceLines.push(state.candleSeries.createPriceLine({
      price: p, color, lineWidth: 1, lineStyle: 2,
      axisLabelVisible: true, title,
    }));
  };
  add(d.nearest_short, C.down, `空损 ${usd(d.nearest_short && d.nearest_short.short_usd)}`);
  add(d.nearest_long, C.up, `多损 ${usd(d.nearest_long && d.nearest_long.long_usd)}`);
}

function setWallLines(s) {
  if (!state.candleSeries) return;
  for (const line of state.wallLines) { try { state.candleSeries.removePriceLine(line); } catch (e) { /* ignore */ } }
  state.wallLines = [];
  if (!state.chartTools.walls || !s || !s.walls || state.market !== "crypto") return;
  const bars = state.chartData && state.chartData.bars;
  if (!bars) return;
  const lows = bars.low.filter(Number.isFinite), highs = bars.high.filter(Number.isFinite);
  if (!lows.length || !highs.length) return;
  const lo = Math.min(...lows) * 0.95, hi = Math.max(...highs) * 1.05;
  for (const wall of (s.walls.near || [])) {
    const price = num(wall.price);
    if (!price || price < lo || price > hi) continue;
    const bid = wall.side === "bid";
    state.wallLines.push(state.candleSeries.createPriceLine({ price, color: bid ? C.up : C.down, lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: `挂墙 ${usd(wall.total)}` }));
  }
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
  const sel = $("tfSelect");
  if (sel && sel.value !== tf) sel.value = tf;
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
  state.liq = null;          // 换标的必须清掉旧的清算数据，否则价位会串到新图上
  setPriceLines(null);
  $("symInput").value = sym;
  document.querySelectorAll("#symbolGroup .tb-btn").forEach(el => el.classList.toggle("active", el.dataset.sym === sym));
  loadSnapshot();
  loadChart(false);
  loadMTF();
  loadLiqmap();
  loadEvents();
}

function switchMarket(mkt) {
  if (mkt === state.market) return;
  state.market = mkt;
  document.querySelectorAll("#marketGroup .tb-btn").forEach(b => b.classList.toggle("active", b.dataset.mkt === mkt));
  // 默认标的：加密 BTC / 美股 AAPL
  state.symbol = mkt === "us" ? "AAPL" : "BTC";
  state.tf = mkt === "us" ? "1d" : "1h";
  state.liq = null;          // 切市场同样要清空，避免加密的清算价位画到美股图上
  state.snap = null;
  setPriceLines(null);
  $("symInput").value = state.symbol;
  loadSymbols();
  loadSnapshot();
  loadChart(false);
  loadMTF();
  loadLiqmap();
  loadEvents();
}

async function loadSymbols() {
  try {
    const d = await api(`/api/symbols?market=${state.market}`);
    const list = d.symbols || [];

    // 加密市场显示 6 个常用；美股显示指数 + 热门股，超出滚动
    const head = state.market === "us"
      ? list.filter(s => s.index)
      : list.slice(0, 6);
    const body = state.market === "us"
      ? list.filter(s => !s.index).slice(0, 22)
      : [];
    const shown = head.concat(body);
    if (!shown.some(s => s.symbol === state.symbol) && list.length) {
      state.symbol = list[0].symbol;
    }

    $("symbolGroup").innerHTML = shown.map(s => {
      const title = s.last ? `${s.name || s.symbol} ${fmt(s.last)} ${pct(s.change_pct)}` : (s.name || s.symbol);
      const name = s.index ? (s.name || s.symbol) : s.symbol;
      return `<button class="tb-btn ${s.symbol === state.symbol ? "active" : ""}" data-sym="${s.symbol}"
        title="${title}">${name}</button>`;
    }).join("");
    $("symbolGroup").querySelectorAll(".tb-btn").forEach(el => {
      el.onclick = () => switchSymbol(el.dataset.sym);
    });

    const tfSets = splitTimeframes(d.timeframes);
    $("tfGroup").innerHTML = tfSets.quick.map(tf =>
      `<button class="tf-btn ${tf === state.tf ? "active" : ""}" data-tf="${tf}">${tf}</button>`).join("");
    $("tfGroup").querySelectorAll(".tf-btn").forEach(el => { el.onclick = () => switchTF(el.dataset.tf); });

    // 完整周期下拉（1m ~ 1M），顶栏放不下那么多按钮
    const sel = $("tfSelect");
    if (sel) {
      sel.innerHTML = tfSets.all.map(tf =>
        `<option value="${tf}" ${tf === state.tf ? "selected" : ""}>${tf}</option>`).join("");
      sel.value = state.tf;
      sel.onchange = () => switchTF(sel.value);
    }

    // 美股搜索用股票列表；切回加密时恢复全量加密标的
    if (state.market === "us") {
      allSymbols = list.map(s => ({ symbol: s.symbol, pair: s.name || s.symbol }));
    } else {
      allSymbols = [];
      loadAllSymbols();
    }
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
  $("indToolbar").innerHTML = ov + `<span class="ind-divider" title="分隔：主图叠加 与 副图指标"></span>` + sub;

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
      state.sub = state.sub === el.dataset.sub ? null : el.dataset.sub;
      renderToolbar();
      if (state.chartData) renderSub();
    };
  });
}

function bindUI() {
  $("aiCol").classList.toggle("collapsed", state.analysisHidden);
  renderChartTools();
  $("venueFilter").querySelectorAll("[data-market-filter]").forEach(btn => {
    btn.onclick = () => {
      state.venueFilter = btn.dataset.marketFilter;
      $("venueFilter").querySelectorAll("[data-market-filter]").forEach(b => b.classList.toggle("active", b === btn));
      if (state.snap) renderVenues(state.snap);
    };
  });
  document.querySelectorAll("[data-detail]").forEach(btn => {
    btn.onclick = () => {
      const snap = state.snap || {};
      if (btn.dataset.detail === "volume") {
        const rows = snap.venues || [];
        openTool("全网量能详情", `<div class="bt-note">当前来源为各交易所 USDT 永续合约 24h 成交额；现货数据源未接入。成交额口径可能因交易所接口定义略有差异。</div><table class="tbl"><thead><tr><th>交易所</th><th>成交额</th><th>持仓</th><th>资金费率</th></tr></thead><tbody>${rows.map(v => `<tr><td>${escapeHtml(v.name)}</td><td>${usd(v.vol_usd)}</td><td>${usd(v.oi_usd)}</td><td>${num(v.funding) === null ? "--" : (v.funding * 100).toFixed(4) + "%"}</td></tr>`).join("")}</tbody></table>`);
      } else {
        const walls = snap.walls || {};
        const rows = ["near", "mid", "far"].flatMap(key => (walls[key] || []).map(row => ({ ...row, tier: key })));
        openTool("挂单墙详情", `<div class="bt-note">以下为最近一次盘口深度快照，不是逐笔成交或历史挂单。挂单可能随时撤销。</div><table class="tbl"><thead><tr><th>档位</th><th>价格</th><th>金额</th><th>距离</th><th>方向</th></tr></thead><tbody>${rows.map(row => `<tr><td>${row.tier}</td><td>${fmt(row.price)}</td><td>${usd(row.total)}</td><td>${spct(row.dist_pct, 3)}</td><td class="${row.side === "bid" ? "up" : "down"}">${row.side === "bid" ? "买" : "卖"}</td></tr>`).join("")}</tbody></table>`);
      }
    };
  });
  $("chartTools").onclick = e => {
    const btn = e.target.closest("[data-chart-tool]");
    if (!btn || btn.disabled) return;
    const key = btn.dataset.chartTool;
    if (key === "liq" || key === "estimated") {
      state.chartLiq = !state.chartLiq;
      setPriceLines(state.liq);
    } else if (key === "walls") {
      state.chartTools.walls = !state.chartTools.walls;
      setWallLines(state.snap);
    } else if (key === "ema") state.overlays.ema = !state.overlays.ema;
    else if (key === "kc") state.overlays.keltner = !state.overlays.keltner;
    else if (key === "patterns") state.overlays.markers = !state.overlays.markers;
    else if (key === "div") state.chartTools.divergence = !state.chartTools.divergence;
    else if (["reg50", "reg100", "reg200", "twoB"].includes(key)) state.chartTools[key] = !state.chartTools[key];
    if (["ema", "kc", "patterns", "div", "reg50", "reg100", "reg200", "twoB"].includes(key) && state.chartData) renderChart();
    renderChartTools();
  };
  $("btnWhatIf").onclick = openWhatIf;
  $("btnLiqCalc").onclick = openLiqCalc;
  const newsToggle = $("newsToggle");
  if (newsToggle) {
    newsToggle.checked = state.newsMarkers;
    newsToggle.onchange = () => {
      state.newsMarkers = newsToggle.checked;
      renderEvents();          // 只重画覆盖层，不必重建整个图表
    };
  }
  const mapBarBtn = $("btnEventMapBar");
  if (mapBarBtn) mapBarBtn.onclick = () => setEventMapMode(true);

  // 事件分类一键开关（和「新闻标记」同一种交互）
  const evMaster = $("evMaster");
  if (evMaster) evMaster.onclick = () => {
    const allOn = EVENT_CATEGORIES.every(([id]) => enabledEventCategories.has(id));
    if (allOn) enabledEventCategories.clear();
    else EVENT_CATEGORIES.forEach(([id]) => enabledEventCategories.add(id));
    syncEventMaster();
    initEventFilters(); renderEvents(); loadEvents();
  };
  $("btnExitEventMap").onclick = () => setEventMapMode(false);
  $("btnMapLatest").onclick = () => {
    if (!state.mainChart) return;
    state.mainChart.timeScale().scrollToRealTime();
  };
  $("btnMapFit").onclick = () => {
    if (state.mainChart) state.mainChart.timeScale().fitContent();
  };
  $("newsBody").onclick = e => {
    const button = e.target.closest("[data-news-index]");
    if (button) focusNews(Number(button.dataset.newsIndex));
    else {
      const row = e.target.closest("[data-news-detail-index]");
      if (row) showNewsDetail(state.newsItems[Number(row.dataset.newsDetailIndex)]);
    }
  };
  $("newsBody").onkeydown = e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const row = e.target.closest("[data-news-detail-index]");
    if (!row || e.target.closest("button")) return;
    e.preventDefault(); showNewsDetail(state.newsItems[Number(row.dataset.newsDetailIndex)]);
  };
  $("evOverlay").onclick = e => {
    const label = e.target.closest("[data-news-label-index]");
    if (label) showNewsDetail(state.newsItems[Number(label.dataset.newsLabelIndex)]);
    const event = e.target.closest("[data-event-detail-index]");
    if (event) showEventDetail(Number(event.dataset.eventDetailIndex));
  };
  $("evOverlay").onkeydown = e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const label = e.target.closest("[data-news-label-index]");
    const event = e.target.closest("[data-event-detail-index]");
    if (!label && !event) return;
    e.preventDefault();
    if (label) showNewsDetail(state.newsItems[Number(label.dataset.newsLabelIndex)]);
    if (event) showEventDetail(Number(event.dataset.eventDetailIndex));
  };
  $("toolClose").onclick = closeTool;
  $("toolModal").onclick = e => { if (e.target.id === "toolModal") closeTool(); };
  // 面板开关：按钮只加 .on 高亮，文案保持短标签
  const panelToggles = [
    ["btnToggleGauge", "gaugesHidden", "gaugeRow"],
    ["btnToggleChartTools", "chartToolsHidden", "chartTools"],
    ["btnToggleTrend", "trendHidden", "trendStrip"],
    ["btnToggleDeriv", "derivHidden", "derivStrip"],
  ];
  for (const [btnId, key, rowId] of panelToggles) {
    const btn = $(btnId), row = $(rowId);
    if (!btn || !row) continue;
    state[key] = row.hidden;
    btn.classList.toggle("on", !row.hidden);
    btn.onclick = () => {
      state[key] = !state[key];
      row.hidden = state[key];
      btn.classList.toggle("on", !state[key]);
      setTimeout(() => { if (state.mainChart) state.mainChart.applyOptions({}); }, 0);
    };
  }
  $("btnToggleAnalysis").onclick = () => {
    state.analysisHidden = !state.analysisHidden;
    $("aiCol").classList.toggle("collapsed", state.analysisHidden);
    $("btnToggleAnalysis").textContent = state.analysisHidden ? "展开" : "收起";
    $("btnToggleAnalysis").title = state.analysisHidden ? "展开多周期研判" : "收起多周期研判";
  };
  
  $("btnResetChart").onclick = () => {
    if (state.mainChart) state.mainChart.timeScale().fitContent();
    if (state.subChart) state.subChart.timeScale().fitContent();
  };
  $("btnMaxChart").onclick = () => {
    state.chartMaximized = !state.chartMaximized;
    $("centerPane").classList.toggle("chart-maximized", state.chartMaximized);
    $("btnMaxChart").textContent = state.chartMaximized ? "还原图表" : "最大化图表";
    if (state.mainChart) setTimeout(() => state.mainChart.applyOptions({}), 0);
  };
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
    if (!$("toolModal").hidden) closeTool();
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

  $("marketGroup").querySelectorAll(".tb-btn").forEach(b => {
    b.onclick = () => switchMarket(b.dataset.mkt);
  });

  $("newsTabs").querySelectorAll(".nd-tab").forEach(b => {
    b.onclick = () => {
      state.newsTag = b.dataset.tag;
      $("newsTabs").querySelectorAll(".nd-tab").forEach(x => x.classList.toggle("active", x === b));
      loadNews();
    };
  });
  $("newsToggle").onclick = () => {
    const collapsed = $("newsDock").classList.toggle("collapsed");
    $("newsToggle").textContent = collapsed ? "展开" : "收起";
    if (state.mainChart) state.mainChart.applyOptions({});
  };

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
    const d = await api(`/api/backtest?symbol=${apiSym()}&tf=${state.tf}&horizon=${h}&bars=3000`);
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
    const d = await api(`/api/report?symbol=${apiSym()}&force=1`);
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

  const coverage = d.coverage || {};
  if (coverage.note) {
    const venues = (coverage.venues || []).map(escapeHtml).join("、") || "暂无可用源";
    h += `<div class="bt-note"><b>数据来源与覆盖</b>　${escapeHtml(coverage.instrument || "行情")} · 成交额 ${Number(coverage.venue_count || 0)} 所（${venues}）<br>` +
      `清算数据：${escapeHtml(coverage.liquidations || "未知")}。${escapeHtml(coverage.note)}</div>`;
  }

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
      body: JSON.stringify({ symbol: apiSym(), question: q }),
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

/* ---------------------------------------------------------------- 实时快讯 */
let newsRetry = 0;
async function loadNews() {
  try {
    const d = await api(`/api/news?tag=${state.newsTag}&limit=80`);
    renderNews(d);
    if (d.warming && newsRetry < 3) { newsRetry++; setTimeout(loadNews, 4000); }
    else if (!d.warming) newsRetry = 0;
  } catch (e) {
    $("newsBody").innerHTML = `<div class="nd-empty">快讯加载失败：${escapeHtml(e.message)}</div>`;
  }
}

const TAG_NAME = { crypto: "加密", stock: "美股", macro: "宏观", other: "其他" };

const EVENT_CATEGORIES = [
  ["other", "其他事件", "#d5a33c"], ["liquidation", "爆仓事件", "#ff4976"],
  ["payroll", "非农", "#4da6ff"], ["cpi", "CPI", "#ff9f45"], ["ppi", "PPI", "#38c6b0"],
  ["fed", "美联储利率", "#c480ff"], ["policy", "政策 / 宏观", "#8b9dff"],
  ["halving", "比特币减半", "#f0b90b"], ["industry", "行业", "#6e7681"],
  ["exchange", "交易所暴雷", "#f85149"], ["security", "安全 / 被盗", "#ffa198"],
  ["executive", "高管 / CEO", "#d2a8ff"], ["election", "选举 / 政治", "#3fb950"],
];
// 默认不勾选「行业」：日历归档里有 260+ 条行业快讯，全开会把时间轴淹没
const enabledEventCategories = new Set(
  EVENT_CATEGORIES.map(([id]) => id).filter(id => id !== "industry"));
let eventMapPrevious = null;
const mapSettings = {log:true, ma:true, future:true};

/** 事件分类总开关的文案要跟着勾选状态走 */
function syncEventMaster() {
  const el = $("evMaster");
  if (!el) return;
  const on = EVENT_CATEGORIES.every(([id]) => enabledEventCategories.has(id));
  const none = enabledEventCategories.size === 0;
  el.textContent = on ? "事件分类：全开" : none ? "事件分类：全关" : `事件分类：${enabledEventCategories.size}/${EVENT_CATEGORIES.length}`;
}

function initEventFilters() {
  const container = $("eventFilters");
  renderMapArchive();
  container.innerHTML = EVENT_CATEGORIES.map(([id, label, color]) => `<label class="${enabledEventCategories.has(id) ? "on" : ""}" title="筛选历史事件"><input type="checkbox" data-event-category="${id}" ${enabledEventCategories.has(id) ? "checked" : ""}><i style="background:${color}"></i>${label}</label>`).join("");
  container.onchange = event => {
    const input = event.target;
    if (!input.dataset.eventCategory) return;
    if (input.checked) enabledEventCategories.add(input.dataset.eventCategory);
    else enabledEventCategories.delete(input.dataset.eventCategory);
    input.parentElement.classList.toggle("on", input.checked);
    renderEvents();
    renderMapArchive();
    syncEventMaster();
    loadEvents();
  };
  syncEventMaster();
  for (const [id, key] of [["mapLog","log"],["mapMA","ma"],["mapFuture","future"]]) {
    $(id).onchange = e => { mapSettings[key] = e.target.checked; renderChart(); };
  }
  $("mapSelectAll").onclick = () => { EVENT_CATEGORIES.forEach(([id]) => enabledEventCategories.add(id)); initEventFilters(); syncEventMaster(); renderChart(); };
  $("mapSelectNone").onclick = () => { enabledEventCategories.clear(); initEventFilters(); syncEventMaster(); renderEvents(); };
  $("mapTimeframe").onchange = async e => { state.tf = e.target.value; await loadChart(false); };
  $("mapLocate").onclick = () => {
    const time = Date.parse($("mapDate").value + "T00:00:00Z") / 1000;
    const times = state.chartData?.bars.time;
    if (!Number.isFinite(time) || !times?.length) return;
    const index = time > times.at(-1) ? times.length - 1 + (time - times.at(-1)) / (TF_SECONDS[state.tf] || 604800) : lowerBound(times,time);
    state.mainChart.timeScale().setVisibleLogicalRange({from:index - 30,to:index + 30});
    scheduleEvents();
  };
  $("mapArchiveList").onclick = e => {
    if (e.target.closest("[data-archive-more]")) {
      mapArchiveLimit += 150;
      renderMapArchive();
      return;
    }
    const row = e.target.closest("[data-archive-index]");
    if (row) showEventDetail(Number(row.dataset.archiveIndex));
  };
}

let mapArchiveLimit = 150;

function renderMapArchive() {
  const records = state.events.map((event, index) => ({ event, index }))
    .filter(({ event }) => enabledEventCategories.has(eventCategory(event)));
  // 上千条事件一次性铺进 DOM 会卡，先渲染最近 150 条，其余按需加载
  const shown = records.slice(-mapArchiveLimit).reverse();
  const more = records.length > mapArchiveLimit;
  $("mapArchiveCount").textContent = `全部时间标记 · ${records.length} 条` + (more ? `（已显示最近 ${shown.length}）` : "");
  $("mapArchiveList").innerHTML = shown.map(({ event, index }) =>
    `<button data-archive-index="${index}"><time>${escapeHtml(event.date || "")}</time><span>${escapeHtml(event.title)}</span><small>${escapeHtml(event.label || "")}</small></button>`).join("")
    + (more ? `<button class="archive-more" data-archive-more="1">加载更早的 ${Math.min(150, records.length - mapArchiveLimit)} 条</button>` : "");
}

async function setEventMapMode(enabled) {
  if (enabled && !state.eventMapMode) eventMapPrevious = { market: state.market, symbol: state.symbol, tf: state.tf };
  state.eventMapMode = enabled;
  if (enabled) {
    state.market = "crypto";
    state.symbol = "BTC";
    state.tf = "1w";
    $("mapTimeframe").value = "1w";
    $("symInput").value = "BTC";
    document.body.classList.add("event-map-mode");
    $("eventMapHead").hidden = false;
    $("eventMapControls").hidden = false;
    document.querySelectorAll("#marketGroup .tb-btn").forEach(b => b.classList.toggle("active", b.dataset.mkt === "crypto"));
    await loadEvents();
  } else {
    if (eventMapPrevious) {
      state.market = eventMapPrevious.market;
      state.symbol = eventMapPrevious.symbol;
      state.tf = eventMapPrevious.tf;
      $("symInput").value = state.symbol;
      $("marketGroup").querySelectorAll(".tb-btn").forEach(b => b.classList.toggle("active", b.dataset.mkt === state.market));
      eventMapPrevious = null;
    }
    document.body.classList.remove("event-map-mode");
    $("eventMapHead").hidden = true;
    $("eventMapControls").hidden = true;
  }
  await loadChart(false);
  if (!enabled) {
    loadSymbols();
    loadSnapshot(); loadMTF(); loadLiqmap();
  }
  setTimeout(() => { if (state.mainChart) state.mainChart.applyOptions({}); if (state.subChart) state.subChart.applyOptions({}); }, 0);
}

function updateEventMapStats(chart) {
  if (!state.eventMapMode || !chart) return;
  const events = state.events || [];
  const historical = events.filter(ev => !ev.date || ev.date <= new Date().toISOString().slice(0, 10));
  const nowSec = Date.now() / 1000;
  const upcoming = events.filter(ev => ev.time > nowSec).sort((a, b) => a.time - b.time)[0];
  const earliest = chart.bars.time.length ? new Date(chart.bars.time[0] * 1000).toISOString().slice(0, 10) : "--";
  const latest = chart.bars.time.length ? new Date(chart.bars.time.at(-1) * 1000).toISOString().slice(0, 10) : "--";
  const stats = [
    ["事件档案", `${historical.length} 条`, "历史档案 / 日历 / 已归档快讯"],
    ["下次公布", upcoming ? upcoming.date : "暂无", upcoming ? `${upcoming.label || ""} · ${(upcoming.title || "").slice(0, 16)}` : "日历里没有未来日程"],
    ["K 线范围", `${earliest} — ${latest}`, `${chart.count || 0} 根 K 线`],
    ["事件类型", `${new Set(historical.map(ev => ev.category || ev.type)).size} 类`, `最新事件 ${historical.at(-1)?.date || "--"}`],
  ];
  $("eventMapStats").innerHTML = stats.map(([label, value, note], i) => `<div class="event-stat"><small>${label}</small><b>${value}</b><span>${note}</span><i>${String(i + 1).padStart(2, "0")}</i></div>`).join("");
  $("eventMapRange").textContent = `${earliest} — ${latest} · ${chart.count || 0} 根 K 线`;
}

function renderNews(d) {
  const items = d.items || [];
  state.newsItems = items;
  const c = d.counts || {};
  $("newsMeta").textContent =
    `共 ${d.total || 0} 条 · 加密 ${c.crypto || 0} / 美股 ${c.stock || 0} / 宏观 ${c.macro || 0}`;
  if (!items.length) {
    $("newsBody").innerHTML = '<div class="nd-empty">暂无快讯</div>';
    if (state.chartData && state.candleSeries && state.mainChart) {
      const range = state.mainChart.timeScale().getVisibleLogicalRange();
      renderChart();
      if (range) state.mainChart.timeScale().setVisibleLogicalRange(range);
    }
    return;
  }
  $("newsBody").innerHTML = items.map((it, index) => {
    const t = new Date(it.ts).toLocaleTimeString("zh-CN",
      { hour12: false, hour: "2-digit", minute: "2-digit" });
    const tagCls = `tag-${it.tag || "other"}`;
    const tagName = TAG_NAME[it.tag] || "其他";
    const title = escapeHtml(it.title || "");
    return `<div class="nd-item ${it.important ? "important" : ""}" data-news-detail-index="${index}" role="button" tabindex="0" title="点击查看新闻详情">
      <span class="t">${t}</span>
      <span class="tag ${tagCls}">${tagName}</span>
      <span class="ti">${title}</span>
      <span class="src">${escapeHtml(it.source || "")}</span>
      <button class="news-locate" data-news-index="${index}" title="定位到对应K线">定位</button>
    </div>`;
  }).join("");
  if (state.chartData && state.candleSeries && state.mainChart) {
    const range = state.mainChart.timeScale().getVisibleLogicalRange();
    renderChart();
    if (range) state.mainChart.timeScale().setVisibleLogicalRange(range);
  }
}

function newsBarIndex(times, ts, tf) {
  if (!times || !times.length || ts === null || ts === undefined || !Number.isFinite(Number(ts))) return -1;
  const seconds = Number(ts) / 1000;
  const duration = { "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800 }[tf];
  if (!duration || seconds < times[0] || seconds >= times[times.length - 1] + duration) return -1;
  let lo = 0, hi = times.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (times[mid] <= seconds) lo = mid + 1; else hi = mid; }
  const index = lo - 1;
  return seconds < times[index] + duration ? index : -1;
}




/** 点击 K 线：把这一根上的历史事件与快讯一次性列出来 */
function barIndexFromClick(param, d) {
  if (!param || !d) return -1;
  const times = d.bars.time;
  if (typeof param.time === "number" && isFinite(param.time)) {
    const i = lowerBound(times, param.time);
    if (i < times.length && times[i] === param.time) return i;
    return i - 1 >= 0 ? i - 1 : -1;
  }
  if (typeof param.logical === "number" && isFinite(param.logical)) {
    const i = Math.round(param.logical);
    return (i >= 0 && i < times.length) ? i : -1;
  }
  return -1;
}

function handleChartClick(param) {
  if (!state.chartData) return;
  const idx = barIndexFromClick(param, state.chartData);
  if (idx < 0) return;
  showBarDigest(idx);
}

function showBarDigest(idx) {
  const d = state.chartData;
  if (!d || idx < 0 || idx >= d.bars.time.length) return;
  const times = d.bars.time;
  const t = times[idx];
  const tf = state.tf;

  const news = (state.newsItems || [])
    .filter(it => newsBarIndex(times, it.ts, tf) === idx)
    .sort((a, b) => b.ts - a.ts);
  const evs = (state.events || [])
    .filter(ev => eventBarIndex(times, ev) === idx)
    .sort((a, b) => a.time - b.time);

  const when = new Date(t * 1000).toLocaleString("zh-CN", { hour12: false });
  const o = d.bars.open[idx], hi = d.bars.high[idx], lo = d.bars.low[idx], c = d.bars.close[idx];
  const chg = o ? (c / o - 1) * 100 : 0;
  const head = `<div class="bd-head">
      <span>开 ${fmt(o)} · 高 ${fmt(hi)} · 低 ${fmt(lo)} · 收 ${fmt(c)}</span>
      <b class="${chg >= 0 ? "up" : "down"}">${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</b>
    </div>`;

  const evHtml = evs.length ? evs.map(ev => `
      <article class="bd-item">
        <div class="bd-t"><i style="background:${ev.color}"></i>${escapeHtml(ev.title || "")}
          <span class="bd-tag">${escapeHtml(ev.label || "")}</span></div>
        <div class="bd-m">${escapeHtml(ev.date || "")} · ${escapeHtml(ev.source || "")}</div>
        ${ev.brief ? `<p>${escapeHtml(ev.brief)}</p>` : ""}
      </article>`).join("")
    : '<div class="bd-empty">这根 K 线没有关联的历史事件</div>';

  const newsHtml = news.length ? news.map(it => {
    let url = "";
    try {
      const p = new URL(it.url);
      if (["http:", "https:"].includes(p.protocol)) url = p.href;
    } catch (e) { /* 无有效原文地址 */ }
    const hhmm = new Date(it.ts).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" });
    return `<article class="bd-item">
        <div class="bd-t"><i style="background:${NEWS_COLORS[it.tag] || "#8b949e"}"></i>${escapeHtml(it.title || "快讯")}</div>
        <div class="bd-m">${hhmm} · ${escapeHtml(it.source || "")}</div>
        ${it.body ? `<p>${escapeHtml(truncate(it.body, 180))}</p>` : ""}
        ${url ? `<a href="${escapeHtml(url).replace(/"/g, "&quot;")}" target="_blank" rel="noopener noreferrer">打开原文</a>` : ""}
      </article>`;
  }).join("")
    : '<div class="bd-empty">这根 K 线没有关联的快讯</div>';

  openTool(`K线明细 · ${when}`, `${head}
    <h4 class="bd-h">事件（${evs.length}）</h4>${evHtml}
    <h4 class="bd-h">快讯（${news.length}）</h4>${newsHtml}`);
}


function showNewsDetail(item) {
  if (!item) return;
  let url = "";
  try { const parsed = new URL(item.url); if (["http:", "https:"].includes(parsed.protocol)) url = parsed.href; } catch (e) { /* 无有效原文地址 */ }
  const when = new Date(item.ts).toLocaleString("zh-CN", { hour12: false });
  openTool("新闻详情", `<article class="news-detail"><h3>${escapeHtml(item.title || "快讯")}</h3><div class="news-detail-meta">${escapeHtml(item.source || "未知来源")} · ${when}</div><p>${escapeHtml(item.body || "该来源未提供正文。")}</p>${url ? `<a href="${escapeHtml(url).replace(/"/g, "&quot;")}" target="_blank" rel="noopener noreferrer">打开原始新闻</a>` : '<span class="muted">该来源未提供原文链接</span>'}</article>`);
}

function showEventDetail(index) {
  const ev = state.events[index];
  if (!ev) return;
  showNewsDetail({ title: ev.title, body: `${ev.date} · ${ev.label || "事件"}${ev.scheduled ? " · 计划日程（请核对实际结果）" : ""}\n${ev.brief || ""}`, source: ev.source, url: ev.url, ts: ev.time * 1000 });
}

function focusNews(index) {
  const item = state.newsItems[index], d = state.chartData;
  if (!item || !d) return;
  const i = newsBarIndex(d.bars.time, item.ts, state.tf), info = $("newsEventInfo");
  info.hidden = false;
  if (i < 0) {
    info.innerHTML = `<b>${escapeHtml(item.title || "快讯")}</b><span>该时间不在当前K线范围内，请切换更长周期。</span>`;
    return;
  }
  const open = num(d.bars.open[i]), close = num(d.bars.close[i]);
  const change = open && close !== null ? (close / open - 1) * 100 : null;
  const next = i + 1 < d.bars.time.length ? num(d.bars.close[i + 1]) : null;
  const nextChange = close && next !== null ? (next / close - 1) * 100 : null;
  info.innerHTML = `<button class="news-info-close" id="closeNewsInfo" title="关闭">×</button><b>${escapeHtml(item.title || "快讯")}</b><span>${escapeHtml(item.source || "")} · ${new Date(item.ts).toLocaleString("zh-CN", {hour12:false})}</span><span>事件所在K线：开 ${fmt(open)} / 收 ${fmt(close)} · ${pct(change)}　下一根：${pct(nextChange)}</span>`;
  $("closeNewsInfo").onclick = () => { info.hidden = true; };
  state.mainChart.timeScale().setVisibleLogicalRange({ from: Math.max(0, i - 25), to: i + 25 });
}

/* ---------------------------------------------------------------- 启动 */
function startPolling() {
  const tick = (fn, ms) => state.timers.push(setInterval(() => { if (!document.hidden) fn(); }, ms));
  tick(loadSnapshot, 10000);
  // K 线轮询按周期自适应：小周期盯得紧，大周期没必要 20 秒一次
  const CHART_POLL = { "15m": 20000, "30m": 30000, "1h": 45000, "4h": 90000, "1d": 120000, "1w": 120000 };
  let chartAt = 0;
  tick(() => {
    if (Date.now() - chartAt < (CHART_POLL[state.tf] || 45000)) return;
    chartAt = Date.now();
    loadChart(true);
  }, 5000);
  tick(loadMTF, 45000);
  tick(loadLiqmap, 30000);
  tick(loadHealth, 60000);
  tick(loadNews, 30000);
  tick(loadEvents, 300000);
  state.timers.push(setInterval(tickCountdown, 1000));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { loadSnapshot(); loadMTF(); }
  });
}

async function boot() {
  initCharts();
  initEventFilters();
  bindUI();
  bindSearch();
  renderToolbar();
  $("symInput").value = state.symbol;
  await loadSymbols();
  loadAllSymbols();
  loadHealth();
  loadNews();
  await Promise.all([loadSnapshot(), loadChart(false), loadMTF(), loadLiqmap()]);
  await loadEvents();
  startPolling();
}

window.addEventListener("DOMContentLoaded", boot);
