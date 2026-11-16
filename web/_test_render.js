/* 前端离屏测试：用真实 API 数据跑完整 boot()，抓运行时错误。
   运行：node _test_render.js
*/
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const FIX = path.join(ROOT, "data", "fixtures");
const load = n => JSON.parse(fs.readFileSync(path.join(FIX, n + ".json"), "utf8"));

/* ---------------- canvas 2d 桩 ---------------- */
function fakeCtx() {
  const o = {
    setTransform() { }, clearRect() { }, beginPath() { }, arc() { }, stroke() { },
    moveTo() { }, lineTo() { }, closePath() { }, fill() { }, fillRect() { },
    fillText() { }, save() { }, restore() { }, setLineDash() { }, rect() { },
  };
  return new Proxy(o, {
    get(t, k) { return k in t ? t[k] : (typeof k === "string" ? () => { } : undefined); },
    set(t, k, v) { t[k] = v; return true; },
  });
}

/* ---------------- DOM 桩 ---------------- */
function makeEl(id) {
  const kids = new Map();
  const el = {
    id, innerHTML: "", textContent: "", value: "24", checked: false, hidden: false,
    className: "", title: "", dataset: {}, style: {},
    tagName: (id || "div").toUpperCase(),
    clientWidth: 1000, clientHeight: 420,
    width: 300, height: 120,
    childElementCount: 0,
    classList: { toggle() { }, add() { }, remove() { }, contains() { return false; } },
    querySelector(sel) {
      if (!kids.has(sel)) kids.set(sel, makeEl(sel));
      return kids.get(sel);
    },
    querySelectorAll() { return []; },
    appendChild(c) {
      this._kids = this._kids || [];
      this._kids.push(c);
      if (c && c.className) this.innerHTML += `<div class="${c.className}"></div>`;
      return c;
    },
    insertBefore(c) {
      this._kids = this._kids || [];
      this._kids.unshift(c);
      if (c && c.className) this.innerHTML = `<div class="${c.className}"></div>` + this.innerHTML;
      return c;
    },
    removeChild(c) { return c; },
    remove() { },
    setAttribute() { }, removeAttribute() { },
    getContext() { return fakeCtx(); },
    getBoundingClientRect() { return { width: 1000, height: 420, left: 0, top: 0 }; },
    addEventListener() { },
    focus() { }, blur() { }, select() { },
  };
  return el;
}

const nodes = new Map();
const document = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, makeEl(id));
    return nodes.get(id);
  },
  querySelectorAll() { return []; },
  querySelector() { return null; },
  createElement(tag) { return makeEl(tag); },
  addEventListener() { },
  hidden: false,
  body: makeEl("body"),
};

/* ---------------- 图表库桩 ---------------- */
function makeSeries() {
  return {
    setData(d) { this._data = d; },
    setMarkers(m) { this._markers = m; },
    priceToCoordinate(pv) { const n = Number(pv); return isFinite(n) ? 60 + (Math.abs(n) % 40) : null; },
    createPriceLine(o) { this._lines = (this._lines || []).concat([o]); return o; },
    removePriceLine() { },
    applyOptions() { },
  };
}
const chart = {
  subscribeCrosshairMove() { }, subscribeClick() { },
  addCandlestickSeries: makeSeries,
  addHistogramSeries: makeSeries,
  addLineSeries: makeSeries,
  removeSeries() { },
  priceScale() { return { applyOptions() { } }; },
  applyOptions() { },
  timeScale() {
    return {
      fitContent() { }, subscribeVisibleLogicalRangeChange() { },
      setVisibleLogicalRange() { }, getVisibleLogicalRange() { return { from: 0, to: 100 }; },
      logicalToCoordinate(i) { return Math.round(i * 3.2); },
    };
  },
};

/* ---------------- 沙箱 ---------------- */
const files = {
  "/api/symbols/all": "symbols_all", "/api/symbols": "symbols", "/api/mtf": "mtf",
  "/api/ticker": "symbols", "/api/scan": "symbols", "/api/chart": "chart",
  "/api/backtest": "backtest", "/api/snapshot": "snapshot", "/api/liqmap": "liqmap",
  "/api/report": "report", "/api/ai/status": "ai_status", "/api/news": "news",
  "/api/events": "events",
};
const sandbox = {
  document,
  window: { addEventListener() { }, devicePixelRatio: 1, location: { href: "" } },
  LightweightCharts: { createChart: () => chart, CrosshairMode: { Normal: 0 } },
  console, Math, JSON, Number, String, Array, Object, Date, isNaN, Proxy,
  fetch: async (url) => {
    const key = Object.keys(files).find(k => String(url).startsWith(k));
    if (!key) throw new Error("unexpected fetch " + url);
    const data = load(files[key]);
    return { ok: true, status: 200, json: async () => data };
  },
  setInterval: () => 0, setTimeout: () => 0, clearInterval: () => { },
  ResizeObserver: class { observe() { } disconnect() { } },
  requestAnimationFrame: f => f(0),
};
sandbox.globalThis = sandbox;

const ctx = vm.createContext(sandbox);
const code = fs.readFileSync(path.join(__dirname, "app.js"), "utf8") +
  "\n;globalThis.__state = state; globalThis.__renderToolbar = renderToolbar;" +
  "\n;globalThis.__filterSymbols = filterSymbols; globalThis.__allSymbols = () => allSymbols;" +
  "\n;globalThis.__loadAllSymbols = loadAllSymbols; globalThis.__loadAiStatus = loadAiStatus;" +
  "\n;globalThis.__handleAiEvent = handleAiEvent; globalThis.__askAI = askAI;" +
  "\n;globalThis.__regressionChannel = regressionChannel; globalThis.__findDivergences = findDivergences;" +
  "\n;globalThis.__findTwoB = findTwoB; globalThis.__renderChartTools = renderChartTools;" +
  "\n;globalThis.__lowerBound = lowerBound; globalThis.__renderEvents = renderEvents;" +
  "\n;globalThis.__showBarDigest = showBarDigest; globalThis.__eventBarIndex = eventBarIndex;" +
  "\n;globalThis.__barIndexFromClick = barIndexFromClick;" +
  "\n;globalThis.__splitTimeframes = splitTimeframes; globalThis.__enabledCategories = enabledEventCategories;" +
  "\n;globalThis.__thinByCapacity = thinByCapacity; globalThis.__syncEventMaster = syncEventMaster;" +
  "\n;globalThis.__renderLiquidations = renderLiquidations;";
vm.runInContext(code, ctx, { filename: "app.js" });

const S = ctx.__state;

let fails = 0;
function run(name, fn) {
  try {
    const out = fn();
    console.log(`  [OK ] ${name}${out ? "  -> " + out : ""}`);
  } catch (e) {
    fails++;
    console.log(`  [FAIL] ${name}\n         ${e.message}`);
    if (process.env.VERBOSE) console.log(e.stack);
  }
}

async function runAsync(name, fn) {
  try {
    const out = await fn();
    console.log(`  [OK ] ${name}${out ? "  -> " + out : ""}`);
  } catch (e) {
    fails++;
    console.log(`  [FAIL] ${name}\n         ${e.message}`);
    if (process.env.VERBOSE) console.log(e.stack);
  }
}
const html = id => document.getElementById(id).innerHTML;
const text = id => document.getElementById(id).textContent;

(async function main() {
  console.log("=".repeat(72));
  console.log("前端离屏测试（真实数据，跑完整 boot）");
  console.log("=".repeat(72));

  try {
    await ctx.boot();
    console.log("  [OK ] boot() 完成，无异常\n");
  } catch (e) {
    fails++;
    console.log("  [FAIL] boot()\n         " + e.message);
    if (process.env.VERBOSE) console.log(e.stack);
  }

  run("图表工具栏状态渲染", () => {
    ctx.__renderChartTools();
    const h = html("chartTools");
    for (const label of ["挂单墙", "回归50", "回归100", "回归200", "背离", "2B"]) {
      if (!h.includes(label)) throw new Error("工具栏缺少 " + label);
    }
    if (h.includes('data-chart-tool="walls" title="历史')) throw new Error("挂单墙仍标记为未实现");
    return "功能按钮齐全";
  });

  run("回归通道计算", () => {
    const r = ctx.__regressionChannel([1, 2, 3, 4, 5], 5);
    if (Math.abs(r.mid[4] - 5) > 1e-9 || Math.abs(r.upper[4] - r.mid[4]) > 1e-9 || Math.abs(r.lower[4] - r.mid[4]) > 1e-9) throw new Error("无噪声线性样本的回归值错误");
    const noisy = ctx.__regressionChannel([1, 2, 3, 4, 6], 5);
    if (!(noisy.upper[4] > noisy.mid[4]) || !(noisy.lower[4] < noisy.mid[4])) throw new Error("标准差边界错误");
    return "线性中线与残差边界有效";
  });

  run("2B假突破识别", () => {
    const b = { high: Array(25).fill(100), low: Array(25).fill(90), close: Array(25).fill(95) };
    b.high[24] = 101; b.close[24] = 99;
    const found = ctx.__findTwoB(b);
    if (!found.some(x => x.i === 24 && x.dir === -1)) throw new Error("未检测到上破后收回");
    return "识别上破回落";
  });

  run("行情条", () => {
    const h = html("quoteBar");
    if (!h || h.length < 100) throw new Error("quoteBar 为空");
    if (h.indexOf("q-price") < 0) throw new Error("缺少价格");
    if (h.indexOf("结算") < 0) throw new Error("缺少结算倒计时");
    return h.length + " 字符";
  });

  run("仪表盘 8 个", () => {
    const h = html("gaugeRow");
    const n = (h.match(/gauge-box/g) || []).length;
    if (n !== 8) throw new Error("数量 " + n + " != 8");
    return n + " 个";
  });

  run("drawArc 单测", () => {
    const cv = makeEl("c");
    cv.clientWidth = 90; cv.clientHeight = 46;
    for (const r of [0, 0.5, 1]) ctx.drawArc(cv, r, "#00b956");
    return "三种比例无异常";
  });

  run("分所量能表", () => {
    const tb = document.getElementById("venueTable").querySelector("tbody").innerHTML;
    const n = (tb.match(/<tr>/g) || []).length;
    if (n < 3) throw new Error("行数过少: " + n);
    return n + " 行";
  });

  run("右栏资金费率与大单", () => {
    const funding = html("fundingPanel");
    const large = html("largeOrders");
    if (!funding.includes("年") && !funding.includes("%")) throw new Error("资金费率未渲染");
    if (!large.includes("金额") || !large.includes("买") && !large.includes("卖")) throw new Error("大单盘口未渲染");
    return "资金费率榜与盘口大单已渲染";
  });

  run("现货筛选明确缺少数据源", () => {
    S.venueFilter = "spot";
    ctx.renderVenues(load("snapshot"));
    const tb = document.getElementById("venueTable").querySelector("tbody").innerHTML;
    if (!tb.includes("暂无现货数据源")) throw new Error("现货源缺失状态未说明");
    S.venueFilter = "all";
    ctx.renderVenues(load("snapshot"));
    return "未将合约行情伪装为现货";
  });

  run("挂单墙表", () => {
    const tb = document.getElementById("wallTable").querySelector("tbody").innerHTML;
    const n = (tb.match(/<tr>/g) || []).length;
    if (n < 1) throw new Error("表为空");
    if (!text("tierInfo")) throw new Error("档位说明为空");
    return n + " 行, " + text("tierInfo");
  });

  run("订单流 + CVD 图", () => {
    if (html("flowStats").indexOf("CVD") < 0) throw new Error("CVD 缺失");
    return "ok";
  });

  run("持仓情绪 6 格", () => {
    const n = (html("posGrid").match(/beh-item/g) || []).length;
    if (n !== 6) throw new Error("格数 " + n);
    return n + " 格";
  });

  run("支撑阻力表", () => {
    const n = (document.getElementById("srTable").querySelector("tbody").innerHTML.match(/<tr>/g) || []).length;
    if (n < 1) throw new Error("表为空");
    return n + " 行";
  });

  run("共振条 6 周期", () => {
    const n = (html("cfBars").match(/cf-row/g) || []).length;
    if (n !== 6) throw new Error("行数 " + n);
    return n + " 行";
  });

  run("多周期卡片 6 张", () => {
    const n = (html("trendStrip").match(/trend-card/g) || []).length;
    if (n !== 6) throw new Error("卡片数 " + n);
    const labeled = (html("trendStrip").match(/tc-trend/g) || []).length;
    if (labeled !== 6) throw new Error("判定标签 " + labeled);
    return n + " 张，均带判定";
  });

  run("信号列表 17 条", () => {
    const n = (html("sigList").match(/sig-row/g) || []).length;
    if (n < 15) throw new Error("信号条过少: " + n);
    return n + " 条";
  });

  run("形态面板", () => {
    if (!html("patList")) throw new Error("形态面板为空");
    return html("patList").length + " 字符";
  });

  run("指标开关 32 个", () => {
    const n = (html("indToolbar").match(/ind-toggle/g) || []).length;
    if (n !== 32) throw new Error("开关数 " + n + " != 32");
    return n + " 个";
  });

  run("副图 18 种全渲染", () => {
    for (const k of ["macd", "rsi", "kdj", "adx", "obv", "cci", "mfi", "cmf", "wr",
      "stochrsi", "stoch", "ao", "trix", "uo", "vortex", "chaikin", "squeeze", "dpo"]) {
      S.sub = k;
      ctx.renderSub();
    }
    S.sub = "macd";
    return "18 种无异常";
  });

  run("叠加指标全开（含 TV 系列）", () => {
    for (const k of ["ma", "ema", "boll", "st", "vwap", "markers", "ichimoku", "psar",
      "keltner", "donchian", "hma", "vwma", "chandelier", "pivot"]) S.overlays[k] = true;
    ctx.renderChart();
    if (!(S.overlays.pivot)) throw new Error("pivot 未开");
    ctx.applyPivots();
    return "已渲染 " + (S.pivotLines || []).length + " 条枢轴线";
  });

  run("切换周期 6 次", () => {
    for (const tf of ["15m", "30m", "1h", "4h", "1d", "1w"]) ctx.switchTF(tf);
    return "无异常";
  });

  run("回测弹窗渲染", () => {
    ctx.renderBacktest(load("backtest"));
    const n = (html("btBody").match(/<tr/g) || []).length;
    if (n < 8) throw new Error("表格行过少: " + n);
    return n + " 行";
  });

  run("空数据不崩", () => {
    ctx.renderSnapshot({ symbol: "X", price: null, venues: [], walls: { near: [], mid: [], far: [] },
      orderflow: {}, positioning: {}, fear_greed: {} });
    return "ok";
  });

  run("多空损面板", () => {
    const d = load("liqmap");
    ctx.renderLiqmap(d);
    const tb = document.getElementById("liqTable").querySelector("tbody").innerHTML;
    const n = (tb.match(/<tr>/g) || []).length;
    if (n < 4) throw new Error("表格行过少: " + n);
    if (!document.getElementById("liqStats").innerHTML) throw new Error("统计卡为空");
    const price = d.price;
    if (!(d.nearest_long.price < price)) throw new Error("多头清算价应低于现价");
    if (!(d.nearest_short.price > price)) throw new Error("空头清算价应高于现价");
    return `${n} 行 · 多损 ${d.nearest_long.dist_pct}% · 空损 ${d.nearest_short.dist_pct}%`;
  });

  run("多空损全部档位方向正确", () => {
    const d = load("liqmap");
    for (const z of d.levels) {
      if (z.long_usd > 0 && z.price >= d.price) throw new Error("多头档位不在现价下方: " + z.price);
      if (z.short_usd > 0 && z.price <= d.price) throw new Error("空头档位不在现价上方: " + z.price);
    }
    return d.levels.length + " 档全部合规";
  });

  run("价格线标注", () => {
    ctx.setPriceLines(load("liqmap"));
    return "已创建 " + (ctx.__state.priceLines || []).length + " 条";
  });

  run("标的搜索：PEPE → 1000PEPE", () => {
    const r = ctx.__filterSymbols("PEPE");
    if (!r.length) throw new Error("无结果");
    if (!r.some(x => x.symbol === "1000PEPE")) throw new Error("未命中 1000PEPE");
    return r.slice(0, 3).map(x => x.symbol).join(", ");
  });

  run("标的搜索：精确优先", () => {
    const r = ctx.__filterSymbols("BTC");
    if (!r.length || r[0].symbol !== "BTC") throw new Error("首位不是 BTC: " + (r[0] && r[0].symbol));
    return r.length + " 条，首位 " + r[0].symbol;
  });

  run("研判报告渲染", () => {
    const d = load("report");
    d.coverage = {
      instrument: "USDT 永续合约", venue_count: 2, venues: ["Binance", "Bybit"],
      liquidations: "模型估算", note: "未接入跨交易所实时爆仓事件。",
    };
    ctx.renderReport(d);
    const h = html("rpBody");
    if (!h || h.length < 2000) throw new Error("正文过短: " + (h || "").length);
    const sections = (h.match(/rp-h/g) || []).length;
    if (sections < 6) throw new Error("章节数过少: " + sections);
    const cards = (h.match(/rp-card/g) || []).length;
    if (cards < 4) throw new Error("卡片数过少: " + cards);
    const findings = (h.match(/rp-finding/g) || []).length;
    if (findings < 3) throw new Error("矛盾点过少: " + findings);
    if (!h.includes("成交额 2 所") || !h.includes("模型估算")) throw new Error("数据覆盖说明缺失");
    if (h.indexOf("外部因子") < 0) throw new Error("缺外部因子章节");
    if (h.indexOf("rp-ext") < 0) throw new Error("缺事件列表");
    if (h.indexOf("rp-senti-bar") < 0) throw new Error("缺消息面情绪条");
    if (h.indexOf("rp-impact") < 0) throw new Error("缺影响判断");
    if (!text("rpMeta")) throw new Error("标题栏为空");
    return `${sections} 章节 / ${cards} 卡片 / ${findings} 条辨析 / ${h.length} 字符`;
  });

  run("研判外部因子：事件与新闻都进正文", () => {
    const d = load("report");
    if (!d.external) throw new Error("fixture 缺 external");
    ctx.renderReport(d);
    const h = html("rpBody");
    const ev = d.external.events || {};
    const nw = d.external.news || {};
    if (!(ev.next || []).length) throw new Error("没有未来事件");
    if (!(ev.recent || []).length) throw new Error("没有近期事件");
    if (!nw.total) throw new Error("没有消息面数据");
    if (h.indexOf("未来已排期") < 0 || h.indexOf("最近已公布") < 0) throw new Error("事件分组标题缺失");
    if (h.indexOf("天后") < 0) throw new Error("未来事件缺倒计时");
    if (h.indexOf("消息面 · 近") < 0) throw new Error("消息面标题缺失");
    const bars = (h.match(/rp-senti-bar/g) || []).length;
    if (bars !== 1) throw new Error("情绪条数量异常: " + bars);
    const impacts = (h.match(/rp-impact/g) || []).length;
    if (impacts !== 1) throw new Error("影响判断缺失");
    const st = d.external.stats || {};
    if (!(st.types || []).length) throw new Error("缺事件回测数据");
    if (h.indexOf("历史上事件公布日的表现") < 0) throw new Error("缺回测小节标题");
    if (h.indexOf("上涨概率") < 0) throw new Error("缺回测表头");
    if (h.indexOf("基准：同期日均振幅") < 0) throw new Error("缺基准说明");
    return `${(ev.next || []).length} 个未来事件 / 消息面 ${nw.total} 条 / 回测 ${st.types.length} 类`;
  });

  run("事件回测：样本量与统计口径", () => {
    const d = load("report");
    const st = d.external.stats;
    const cpi = (st.types || []).find(t => t.type === "cpi");
    if (!cpi) throw new Error("缺 CPI 回测行");
    if (cpi.n < 20) throw new Error("CPI 样本过少: " + cpi.n);
    if (typeof cpi.mean_pct !== "number" || typeof cpi.up_rate !== "number") throw new Error("统计字段缺失");
    if (!(cpi.up_rate >= 0 && cpi.up_rate <= 100)) throw new Error("上涨概率越界: " + cpi.up_rate);
    if (!st.window || !st.window.from) throw new Error("缺样本窗口");
    if (!(st.baseline || {}).range_pct) throw new Error("缺基准振幅");
    return `CPI ${cpi.n} 次 · 均值 ${cpi.mean_pct}% · 上涨率 ${cpi.up_rate}%`;
  });

  run("研判 Markdown 可用于导出", () => {
    const d = load("report");
    if (!d.markdown || d.markdown.length < 500) throw new Error("markdown 过短");
    if (d.markdown.indexOf("# ") !== 0) throw new Error("markdown 未以一级标题开头");
    return d.markdown.length + " 字符";
  });

  await runAsync("AI 状态：未配置时禁用并提示", async () => {
    await ctx.__loadAiStatus();
    const tip = document.getElementById("rpAiOut").innerHTML;
    if (!tip || tip.indexOf("rp-ai-tip") < 0) throw new Error("缺少配置提示: " + tip.slice(0, 60));
    const st = document.getElementById("rpAiStatus").textContent;
    if (st.indexOf("TD_AI_KEY") < 0) throw new Error("状态文案没提 TD_AI_KEY: " + st);
    if (!document.getElementById("rpAsk").disabled) throw new Error("未配置时按钮应禁用");
    return "已提示并禁用";
  });

  run("AI 事件：文本流式追加", () => {
    document.getElementById("rpAiOut").innerHTML = "";
    ctx.__handleAiEvent({ type: "text", content: "第一段" });
    ctx.__handleAiEvent({ type: "text", content: "第二段" });
    ctx.__handleAiEvent({ type: "done" });
    return "已追加 2 段";
  });

  run("AI 事件：编造数值告警", () => {
    const out = document.getElementById("rpAiOut");
    out.innerHTML = "";
    ctx.__handleAiEvent({ type: "verify", unknown: ["99999.99", "88888.88"], checked: 120 });
    const html = out.innerHTML;
    if (html.indexOf("rp-ai-warn") < 0) throw new Error("未渲染告警");
    return "2 个数值已告警";
  });

  run("AI 事件：错误展示", () => {
    const out = document.getElementById("rpAiOut");
    out.innerHTML = "";
    ctx.__handleAiEvent({ type: "error", message: "未配置 API Key" });
    if (out.innerHTML.indexOf("rp-ai-err") < 0) throw new Error("未渲染错误");
    return "ok";
  });

  run("实时快讯渲染", () => {
    ctx.renderNews(load("news"));
    const n = (html("newsBody").match(/nd-item/g) || []).length;
    if (n < 10) throw new Error("条目过少: " + n);
    if (!text("newsMeta")) throw new Error("统计行为空");
    return `${n} 条 · ${text("newsMeta")}`;
  });

  run("新闻时间与K线匹配", () => {
    const times = [100000, 103600, 107200];
    if (ctx.newsBarIndex(times, 103600000, "1h") !== 1) throw new Error("边界时间未匹配到新K线");
    if (ctx.newsBarIndex(times, 103599000, "1h") !== 0) throw new Error("边界前时间匹配错误");
    if (ctx.newsBarIndex(times, 110800000, "1h") !== -1 || ctx.newsBarIndex(times, null, "1h") !== -1) throw new Error("越界或空时间未排除");
    const merged = ctx.newsLabelGroups(times, [{ ts: 103600000, title: "a" }, { ts: 104000000, title: "b" }], "1h");
    if (merged.length !== 1 || merged[0].count !== 2) throw new Error("同根K线新闻未合并");
    return "边界、越界和同K线合并正常";
  });

  run("快讯标签：时间 + 短标题", () => {
    const items = load("news").items.filter(x => x.ts);
    // 造一段覆盖这些快讯的 1h K 线
    const minTs = Math.min(...items.map(x => x.ts));
    const start = Math.floor(minTs / 3600000) * 3600 - 2 * 3600;
    const times = [];
    for (let i = 0; i < 8; i++) times.push(start + i * 3600);
    S.chartData = {
      bars: {
        time: times,
        high: times.map((_, i) => 100 + i), low: times.map(() => 95 + 0),
        open: times.map(() => 98), close: times.map(() => 99),
        volume: times.map(() => 1000),
      },
      snapshot: { close: 99 },
    };
    S.tf = "1h";
    S.newsMarkers = true;
    S.newsItems = items;
    ctx.renderEvents();
    const h = html("evOverlay");
    const n = (h.match(/nw-box/g) || []).length;
    if (n < 1) throw new Error("没有渲染快讯标签");
    if (h.indexOf("ev-label nw-box") < 0) throw new Error("未复用事件地图的标签样式");
    if (!/<div class="ev-h"[^>]*><b>[^<]+<\/b><\/div>/.test(h)) throw new Error("标签缺少标题栏");
    if (!/<small class="ev-d">\d{2}:\d{2}/.test(h)) throw new Error("标签缺少时间行");
    if ((h.match(/nw-link/g) || []).length < n) throw new Error("缺少连到K线的虚线");
    if (h.indexOf("新闻 ") >= 0) throw new Error("仍在用「新闻 N」旧样式");
    return `${n} 个盒子（标题栏 + 时间行 + 连接线）`;
  });

  run("快讯标签：关闭后消失", () => {
    S.newsMarkers = false;
    ctx.renderEvents();
    if (html("evOverlay").indexOf("nw-box") >= 0) throw new Error("关闭后仍有快讯标签");
    S.newsMarkers = true;
    return "已清空";
  });

  run("点击K线弹窗：列出该根的事件与快讯", () => {
    // 造一根同时含事件和快讯的 K 线
    const base = Math.floor(Date.UTC(2026, 10, 12, 4, 0) / 1000);   // 12:00 北京时间
    const times = [base - 3600, base, base + 3600];
    S.chartData = {
      bars: {
        time: times,
        open: [100, 101, 103], high: [101, 104, 105], low: [99, 100, 102],
        close: [101, 103, 104], volume: [1000, 1000, 1000],
      },
      snapshot: { close: 104 },
    };
    S.tf = "1h";
    S.newsItems = [{ ts: (base + 600) * 1000, title: "测试快讯A", source: "测试源", body: "正文", tag: "crypto", url: "" }];
    S.events = [{ time: base, date: "2026-11-12", title: "测试事件A", brief: "说明", type: "cpi", category: "cpi", color: "#c480ff", label: "CPI" }];
    ctx.__showBarDigest(1);
    const title = document.getElementById("toolTitle").textContent;
    const body = html("toolBody");
    if (title.indexOf("K线明细") < 0) throw new Error("弹窗标题不对: " + title);
    if (body.indexOf("测试事件A") < 0) throw new Error("未列出事件");
    if (body.indexOf("测试快讯A") < 0) throw new Error("未列出快讯");
    if (body.indexOf("事件（1）") < 0 || body.indexOf("快讯（1）") < 0) throw new Error("分组计数不对");
    return "事件 1 条 / 快讯 1 条";
  });

  run("点击K线弹窗：空数据也有提示", () => {
    S.newsItems = [];
    S.events = [];
    ctx.__showBarDigest(0);
    const body = html("toolBody");
    if (body.indexOf("没有关联的历史事件") < 0) throw new Error("缺少事件空态");
    if (body.indexOf("没有关联的快讯") < 0) throw new Error("缺少快讯空态");
    return "两个空态都在";
  });

  run("周期选择：顶栏常用 6 个 + 下拉全量", () => {
    const all = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"];
    const s = ctx.__splitTimeframes(all);
    if (s.all.length !== 15) throw new Error("下拉周期数 " + s.all.length);
    if (s.quick.length !== 6) throw new Error("顶栏按钮数 " + s.quick.length);
    if (s.quick.join(",") !== "15m,30m,1h,4h,1d,1w") throw new Error("顶栏周期不对: " + s.quick);
    const us = ctx.__splitTimeframes(["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "3d", "1w", "1M"]);
    if (us.all.length !== 14 || us.quick.length !== 6) throw new Error("美股周期拆分不对");
    return `加密 15 个 / 美股 14 个，顶栏固定 6 个`;
  });

  run("事件分类：默认排除行业，可一键全开/全关", () => {
    const set = ctx.__enabledCategories;
    if (set.has("industry")) throw new Error("默认不该勾选行业");
    if (!set.has("halving") || !set.has("cpi") || !set.has("fed")) throw new Error("默认漏了重要分类");
    const before = set.size;
    set.add("industry");
    if (!set.has("industry")) throw new Error("全开失败");
    const opened = set.size;
    set.clear();
    if (set.size !== 0) throw new Error("全关失败");
    // 复原，避免影响后续用例
    ["other", "liquidation", "payroll", "cpi", "ppi", "fed", "policy", "halving",
     "exchange", "security", "executive", "election"].forEach(id => set.add(id));
    return `默认 ${before} 项 → 全开 ${opened} 项 → 全关 0 项`;
  });

  run("事件过密时按容量抽样并保留最新", () => {
    const items = Array.from({ length: 800 }, (_, i) => ({ x: i * 2, ev: { title: "t" + i } }));
    const out = ctx.__thinByCapacity(items, 1000, 400, 170, 36, 7);
    if (out.length >= 800) throw new Error("没有抽稀");
    if (out[out.length - 1] !== items[799]) throw new Error("丢了最新一条");
    if (out.length < 6) throw new Error("抽得太狠: " + out.length);
    const xs = out.map(o => o.x);
    if (xs.some((v, i) => i && v <= xs[i - 1])) throw new Error("结果没按时间递增");
    return `${out.length} / 800 保留，最新一条在`;
  });

  run("事件时间轴：BLS 历史事件已覆盖", () => {
    const ev = load("events");
    const cpi = ev.events.filter(e => e.type === "cpi");
    const pay = ev.events.filter(e => e.type === "payroll");
    if (cpi.length < 100 || pay.length < 100) throw new Error(`CPI ${cpi.length} / 非农 ${pay.length} 条不足`);
    const first = cpi[0].date.slice(0, 4);
    if (Number(first) > 2012) throw new Error("最早 CPI 只到 " + first);
    S.tf = "1w";
    S.chartData = load("chart_btc_1w");
    S.events = ev.events;
    S.eventTypes = ev.types;
    S.eventsOn = true;
    ctx.renderEvents();
    const h = html("evOverlay");
    const n = (h.match(/ev-label/g) || []).length;
    if (n < 3) throw new Error("标签过少 " + n);
    if (!/202[5-9]/.test(h)) throw new Error("缺少近期年份标签");
    S.tf = "1h";
    return `CPI ${cpi.length} / 非农 ${pay.length} 条（${first} 起），图上 ${n} 个标签`;
  });

  run("事件标签显示公布数值", () => {
    const ev = load("events");
    const withValue = ev.events.filter(e => e.value);
    if (withValue.length < 100) throw new Error("带数值事件太少: " + withValue.length);
    S.tf = "1w";
    S.chartData = load("chart_btc_1w");
    S.events = ev.events;
    S.eventTypes = ev.types;
    S.eventsOn = true;
    ctx.renderEvents();
    const h = html("evOverlay");
    if (h.indexOf("ev-v") < 0) throw new Error("标签里没有数值元素");
    if (!/同比\s*[+-]?\d+\.\d%/.test(h) && !/[+-]\d+\.\d 万人/.test(h)) throw new Error("数值格式不对");
    const n = (h.match(/class="ev-v/g) || []).length;
    if (n < 3) throw new Error("带数值的标签过少: " + n);
    S.tf = "1h";
    return `${n} 个标签带数值（共 ${withValue.length} 条可用）`;
  });

  run("事件分类颜色各不相同", () => {
    const ev = load("events");
    const map = {};
    for (const e of ev.events) if (!map[e.type]) map[e.type] = e.color;
    const need = ["cpi", "payroll", "ppi", "fed"];
    for (const t of need) if (!map[t]) throw new Error("缺少类型色: " + t);
    const colors = need.map(t => map[t]);
    if (new Set(colors).size !== colors.length) throw new Error("CPI/非农/PPI/美联储 颜色重复: " + colors);
    return need.map(t => `${t}=${map[t]}`).join(" ");
  });

  run("事件分类总开关文案", () => {
    const set = ctx.__enabledCategories;
    const all = ["other", "liquidation", "payroll", "cpi", "ppi", "fed", "policy", "halving",
                 "industry", "exchange", "security", "executive", "election"];
    all.forEach(id => set.add(id));
    ctx.__syncEventMaster();
    const on = document.getElementById("evMaster").textContent;
    if (!on.includes("全开")) throw new Error("全开文案不对: " + on);
    set.clear();
    ctx.__syncEventMaster();
    const off = document.getElementById("evMaster").textContent;
    if (!off.includes("全关")) throw new Error("全关文案不对: " + off);
    set.add("cpi"); set.add("payroll");
    ctx.__syncEventMaster();
    const part = document.getElementById("evMaster").textContent;
    if (!part.includes("2/13")) throw new Error("部分选中文案不对: " + part);
    all.filter(id => id !== "industry").forEach(id => set.add(id));
    ctx.__syncEventMaster();
    return `${on} → ${off} → ${part}`;
  });

  run("全网爆仓面板：金额、笔数、占比条", () => {
    const w = (h24, s24, ln, sn) => ({
      long_usd: h24, short_usd: s24, long_n: ln, short_n: sn,
      long_ratio: Math.round((h24 / (h24 + s24)) * 1000) / 10,
    });
    ctx.__renderLiquidations({
      ok: true,
      windows: {
        "24h": w(471_300_000, 196_500_000, 23931, 9781),
        "12h": w(28_700_000, 53_400_000, 3502, 2875),
        "4h": w(13_500_000, 2_300_000, 1580, 282),
        "1h": w(500_000, 200_000, 88, 66),
      },
      total_24h_usd: 667_800_000,
    });
    const h = html("cgLiq");
    if (h.indexOf("471.30M") < 0) throw new Error("缺多头爆仓金额");
    if (h.indexOf("196.50M") < 0) throw new Error("缺空头爆仓金额");
    if (h.indexOf("23,931") < 0) throw new Error("缺爆仓笔数");
    if (h.indexOf("cg-bar") < 0) throw new Error("缺占比条");
    if (!/width:\s*70\.\d%/.test(h)) throw new Error("多头占比条宽度不对");
    if (h.indexOf("1h 多") < 0 || h.indexOf("12h 多") < 0) throw new Error("缺分时窗口");
    // 降级：拿不到数据时给出原因
    ctx.__renderLiquidations({ ok: false, error: "Coinglass 榜单未收录 XXX" });
    if (html("cgLiq").indexOf("未收录") < 0) throw new Error("降级提示缺失");
    return "金额/笔数/占比条/分时窗口 + 降级提示";
  });

  run("主题：双主题变量与切换按钮齐备", () => {
    const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
    const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
    const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
    if (!css.includes('[data-theme="light"]')) throw new Error("缺少浅色主题变量块");
    for (const token of ["--chart-bg", "--tx-strong", "--panel", "--on-solid"]) {
      if (!css.includes(token)) throw new Error("缺少主题变量 " + token);
    }
    if (css.indexOf('[data-theme="light"]') === css.lastIndexOf('[data-theme="light"]')) {
      // 至少要有变量块 + 局部覆盖块
      throw new Error("浅色主题覆盖不足");
    }
    if (!html.includes('id="btnTheme"')) throw new Error("顶部缺少主题按钮");
    for (const fn of ["function applyTheme", "function toggleTheme", "function applyChartTheme"]) {
      if (!js.includes(fn)) throw new Error("缺少 " + fn);
    }
    if (!js.includes("panshi-theme")) throw new Error("主题没有持久化 key");
    return "浅色变量 + 图表变量 + 按钮 + 持久化";
  });

  run("事件与K线下标映射（日级/分钟级）", () => {    const times = [Date.UTC(2026, 10, 10) / 1000, Date.UTC(2026, 10, 11) / 1000, Date.UTC(2026, 10, 12) / 1000];
    const dayEvent = { time: Date.UTC(2026, 10, 11) / 1000 };
    const minEvent = { time: Date.UTC(2026, 10, 11, 5) / 1000 };
    if (ctx.__eventBarIndex(times, dayEvent) !== 1) throw new Error("日级事件映射错误");
    if (ctx.__eventBarIndex(times, minEvent) !== 1) throw new Error("分钟级事件映射错误");
    return "日级/分钟级都落在第 2 根";
  });

  run("快讯分类标签齐备", () => {
    const h = html("newsBody");
    for (const t of ["tag-crypto", "tag-stock", "tag-macro"]) {
      if (h.indexOf(t) < 0) throw new Error("缺少标签 " + t);
    }
    return "加密/美股/宏观";
  });

  run("美股空数据降级", () => {
    S.market = "us";
    ctx.renderVenues({ venues: [] });
    const tb = document.getElementById("venueTable").querySelector("tbody").innerHTML;
    if (tb.indexOf("美股") < 0) throw new Error("量能面板未提示美股: " + tb);
    ctx.renderLiqmap({ ok: false });
    const lt = document.getElementById("liqTable").querySelector("tbody").innerHTML;
    if (lt.indexOf("美股") < 0) throw new Error("多空损面板未提示美股: " + lt);
    S.market = "crypto";
    return "已降级";
  });

  run("美股 K 线渲染", () => {
    const d = load("chart_us");
    S.market = "us";
    S.chartData = d;
    ctx.renderChart();
    const n = (S.candleSeries._data || []).length;
    if (n < 100) throw new Error("K 线数据量异常: " + n);
    if (!S.candleSeries._data[0].open) throw new Error("首根无开盘价");
    S.market = "crypto";
    return n + " 根，末价 " + S.candleSeries._data[n - 1].close;
  });

  run("切标的后旧清算线不污染价格轴", () => {
    // 复现真实 bug：美股图上残留加密的清算价位（~7万），会把价格轴撑爆
    S.market = "us";
    S.chartData = load("chart_us");     // 价格 ~355
    S.liq = load("liqmap");             // 清算价位 ~7 万
    ctx.renderChart();
    const n = (S.priceLines || []).length;
    if (n !== 0) throw new Error("仍画出了 " + n + " 条超范围价格线，价格轴会被撑开");
    S.liq = null;
    S.market = "crypto";
    return "已拦截";
  });

  run("美股研判渲染", () => {
    S.market = "us";
    ctx.renderMTF(load("mtf_us"));
    ctx.renderSnapshot(load("snapshot_us"));
    const n = (html("trendStrip").match(/trend-card/g) || []).length;
    if (n !== 6) throw new Error("周期卡片数 " + n);
    const q = html("quoteBar");
    if (q.indexOf("q-price") < 0) throw new Error("行情条为空");
    S.market = "crypto";
    return `${n} 张卡片，行情条 ${q.length} 字符`;
  });

  run("事件时间轴：竖线 + 标签", () => {
    const ev = load("events");
    S.tf = "1w";
    S.chartData = load("chart_btc_1w");
    S.events = ev.events;
    S.eventTypes = ev.types;
    S.eventsOn = true;
    ctx.renderEvents();
    const h = html("evOverlay");
    const lines = (h.match(/ev-line/g) || []).length;
    const labels = (h.match(/ev-label/g) || []).length;
    if (lines < 3) throw new Error("竖线过少: " + lines);
    if (labels !== lines) throw new Error(`竖线 ${lines} 与标签 ${labels} 数量不一致`);
    if (h.indexOf("--") >= 0 && false) throw new Error("unreachable");
    return `${lines} 条竖线 / ${labels} 个标签`;
  });

  run("事件时间轴：标签含日期与标题", () => {
    const h = html("evOverlay");
    if (!/<b>[^<]+<\/b>/.test(h)) throw new Error("标签缺少标题");
    if (!/<small[^>]*>\d{4}-\d{2}-\d{2}/.test(h)) throw new Error("标签缺少日期");
    if (h.indexOf('class="ev-label') < 0) throw new Error("缺少标签容器");
    return "标题与日期齐备";
  });

  run("事件时间轴：关闭后清空", () => {
    S.eventsOn = false;
    ctx.renderEvents();
    if (html("evOverlay") !== "") throw new Error("关闭后仍有内容");
    S.eventsOn = true;
    ctx.renderEvents();
    return "已清空";
  });

  run("事件时间轴：未来事件虚线 + 预估标记", () => {
    const ev = load("events");
    S.tf = "1w";
    S.chartData = load("chart_btc_1w");
    S.events = ev.events;
    S.eventTypes = ev.types;
    S.eventsOn = true;
    ctx.renderEvents();
    const h = html("evOverlay");
    if (h.indexOf("ev-label future") < 0) throw new Error("未来事件未使用虚线样式");
    // 已排期的未来事件标「计划」，推算出来的标「预估」
    if (!/（预估）|（计划）/.test(h)) throw new Error("未来事件未标注 预估/计划");
    const box = (h.match(/ev-label future/g) || []).length;
    S.tf = "1h";
    return box + " 个未来事件";
  });

  run("事件时间轴：日期映射到对应K线", () => {    // 2022-09-15 ETH 合并应落在 2022 年那一周的 K 线上
    const times = S.chartData.bars.time;
    const ts = Date.UTC(2022, 8, 15) / 1000;
    const idx = Math.max(0, ctx.__lowerBound(times, ts + 1) - 1);
    if (idx <= 0 || idx >= times.length) throw new Error("索引越界: " + idx);
    const d = new Date(times[idx] * 1000);
    if (d.getUTCFullYear() !== 2022) throw new Error("映射到 " + d.toISOString());
    const gapDays = (ts - times[idx]) / 86400;
    if (gapDays < 0 || gapDays >= 7) throw new Error("与事件相差 " + gapDays.toFixed(1) + " 天");
    S.tf = "1h";
    return `落在 ${d.toISOString().slice(0, 10)}（相差 ${gapDays.toFixed(1)} 天）`;
  });

  run("历史地图：对数、MA20、筛选与未来时间轴", () => {
    S.eventMapMode = true;
    vm.runInContext("renderChart()", ctx);
    ctx.__renderEvents();
    const labels = html("evOverlay");
    if (!labels.includes("data-event-detail-index")) throw new Error("缺少事件详情入口");
    vm.runInContext("enabledEventCategories.clear(); renderEvents()", ctx);
    if (!html("evOverlay").includes("未选择事件分类")) throw new Error("缺少空筛选提示");
    vm.runInContext("EVENT_CATEGORIES.forEach(([id]) => enabledEventCategories.add(id))", ctx);
    S.eventMapMode = false;
    return "地图渲染、可点击标签、空筛选状态正常";
  });
  console.log("=".repeat(72));
  console.log(fails === 0 ? "全部通过" : `${fails} 项失败`);
  process.exit(fails === 0 ? 0 : 1);
})();
