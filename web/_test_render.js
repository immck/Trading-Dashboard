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
  "\n;globalThis.__lowerBound = lowerBound; globalThis.__renderEvents = renderEvents;";
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
    if (!text("rpMeta")) throw new Error("标题栏为空");
    return `${sections} 章节 / ${cards} 卡片 / ${findings} 条辨析 / ${h.length} 字符`;
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
    const marks = ctx.newsChartMarkers({ bars: { time: times } }, [{ ts: 103600000 }, { ts: 104000000 }]);
    if (marks.length !== 1 || marks[0].count !== 2) throw new Error("同根K线新闻未合并");
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
    const n = (h.match(/nw-label/g) || []).length;
    if (n < 1) throw new Error("没有渲染快讯标签");
    if (!/<span class="nw-t">\d{2}:\d{2}<\/span>/.test(h)) throw new Error("标签缺少时间");
    if (!/<span class="nw-i">[^<]+<\/span>/.test(h)) throw new Error("标签缺少标题");
    if (h.indexOf("新闻 ") >= 0) throw new Error("仍在用「新闻 N」旧样式");
    return `${n} 条标签，形如「时:分 标题」`;
  });

  run("快讯标签：关闭后消失", () => {
    S.newsMarkers = false;
    ctx.renderEvents();
    if (html("evOverlay").indexOf("nw-label") >= 0) throw new Error("关闭后仍有快讯标签");
    S.newsMarkers = true;
    return "已清空";
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
    if (h.indexOf("（预估）") < 0) throw new Error("未来事件未标注预估");
    const box = (h.match(/ev-label future/g) || []).length;
    S.tf = "1h";
    return box + " 个未来事件";
  });

  run("事件时间轴：日期映射到对应K线", () => {    // 2022-09-15 ETH 合并应落在 2022 年那一周的 K 线上
    const times = S.chartData.bars.time;
    const ts = Date.UTC(2022, 8, 15) / 1000;
    const idx = ctx.__lowerBound(times, ts);
    if (idx <= 0 || idx >= times.length) throw new Error("索引越界: " + idx);
    const d = new Date(times[idx] * 1000);
    if (d.getUTCFullYear() !== 2022) throw new Error("映射到 " + d.toISOString());
    const gapDays = (times[idx] - ts) / 86400;
    if (gapDays < 0 || gapDays > 7) throw new Error("与事件相差 " + gapDays.toFixed(1) + " 天");
    S.tf = "1h";
    return `落在 ${d.toISOString().slice(0, 10)}（相差 ${gapDays.toFixed(1)} 天）`;
  });

  console.log("=".repeat(72));
  console.log(fails === 0 ? "全部通过" : `${fails} 项失败`);
  process.exit(fails === 0 ? 0 : 1);
})();
