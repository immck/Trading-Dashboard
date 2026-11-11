"""研判生成器：把多周期打分、盘口、持仓、清算分布组织成一份可读的分析。

设计原则：
  · 所有结论都由数据条件触发，不写死文案；
  · 每个论断都必须能在返回结构里找到对应的原始数值；
  · 明确区分「事实」（交易所数据）与「推断」（模型/规则）。
"""
from __future__ import annotations

import time

from . import analysis, feed, liqmap, market

CACHE_TTL = 45
_cache: dict[str, tuple[float, dict]] = {}


# ---------------------------------------------------------------- 格式化
def _n(x, nd=2):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return "--"
    if f != f:
        return "--"
    if abs(f) >= 1000:
        return f"{f:,.{nd}f}"
    if abs(f) >= 1:
        return f"{f:.{nd}f}"
    if abs(f) >= 0.01:
        return f"{f:.4f}"
    return f"{f:.8f}"


def _pct(x, nd=2):
    try:
        return f"{float(x):+.{nd}f}%"
    except (TypeError, ValueError):
        return "--"


def _usd(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return "--"
    a = abs(f)
    if a >= 1e9:
        return f"{f / 1e9:.2f}B"
    if a >= 1e6:
        return f"{f / 1e6:.1f}M"
    if a >= 1e3:
        return f"{f / 1e3:.1f}K"
    return f"{f:.0f}"


def _sign(x):
    try:
        return 1 if float(x) > 0 else -1 if float(x) < 0 else 0
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- 主流程
def build(symbol: str, force: bool = False) -> dict:
    sym = feed.base_symbol(feed.norm_symbol(symbol))
    now = time.time()
    hit = _cache.get(sym)
    if hit and not force and now - hit[0] < CACHE_TTL:
        return hit[1]

    snap = market.snapshot(sym)
    mtf = analysis.mtf(sym)
    lq = liqmap.build(sym, 12)
    charts = {}
    for tf in ("1h", "4h", "1d"):
        try:
            charts[tf] = _chart_brief(sym, tf)
        except Exception:  # noqa: BLE001
            charts[tf] = None

    price = snap.get("price")
    pos = snap.get("positioning") or {}
    fg = snap.get("fear_greed") or {}
    of = snap.get("orderflow") or {}
    w = snap.get("walls") or {}
    rows = mtf.get("rows", [])
    ok_rows = [r for r in rows if "error" not in r]

    out = {
        "symbol": sym,
        "ts": int(now * 1000),
        "price": price,
        "headline": _headline(sym, mtf, snap, charts),
        "verdict": {
            "weighted": mtf.get("weighted_score"),
            "big": mtf.get("big_score"),
            "label": mtf.get("verdict_label"),
            "consensus": mtf.get("consensus"),
            "summary": mtf.get("summary"),
        },
        "cards": _cards(snap, mtf, lq),
        "facts": _facts(snap, pos, fg, of, w),
        "mtf": _mtf_table(rows),
        "levels": {tf: charts[tf] for tf in charts if charts[tf]},
        "liq": _liq_block(lq),
        "findings": _findings(sym, snap, mtf, lq, charts),
        "watch": _watch(snap, mtf, lq, charts),
        "reliability": _reliability(),
        "disclaimer": "以上为公开数据与模型规则生成的技术研判，不构成投资建议。"
                      "指标与打分只描述当前结构，不预测拐点。",
    }
    out["markdown"] = _to_markdown(out)
    _cache[sym] = (now, out)
    return out


def _chart_brief(sym: str, tf: str) -> dict:
    """取单个周期的关键读数（不取值序列，保持体积小）。"""
    from . import indicators as I, patterns as P
    bars = feed.klines(sym, tf, want=320)
    ind = I.compute_all(bars)
    pat = P.detect(bars)
    sn = I.snapshot(ind, bars)
    price = sn["close"]

    def last(a):
        try:
            v = float(a[-1])
            return None if v != v else v
        except Exception:  # noqa: BLE001
            return None

    piv = ind.get("pivot") or {}
    return {
        "tf": tf,
        "price": price,
        "structure": ind["structure"]["label"],
        "adx": sn["adx"],
        "rsi": sn["rsi"],
        "atr": sn["atr"],
        "kdj_k": sn["kdj_k"],
        "st_dir": sn["st_dir"],
        "st_value": sn["st_value"],
        "psar": sn["psar"],
        "cloud_top": sn["ichi_cloud_top"],
        "cloud_bot": sn["ichi_cloud_bot"],
        "squeeze_on": sn["squeeze_on"],
        "support": [x["price"] for x in ind["sr"]["support"][:3]],
        "resistance": [x["price"] for x in ind["sr"]["resistance"][:3]],
        "vp": {k: ind["volprofile"].get(k) for k in ("poc", "vah", "val") if k in ind["volprofile"]},
        "fib": ind["fib"]["levels"],
        "pivot": piv,
        "pattern": (pat["latest"] or {}).get("label"),
        "pattern_dir": (pat["latest"] or {}).get("dir"),
        "chart_patterns": [p["label"] for p in pat["chart"][:3]],
    }


# ---------------------------------------------------------------- 各段生成
def _headline(sym, mtf, snap, charts) -> str:
    d = mtf.get("direction", 0)
    w = mtf.get("weighted_score", 0)
    cons = mtf.get("consensus", "")
    price = snap.get("price")
    c1 = charts.get("1h") or {}
    val = (c1.get("vp") or {}).get("val")
    where = ""
    if val and price:
        diff = (price / val - 1) * 100
        if diff < -1:
            where = f"价格已跌破 1h 价值区下沿 {_n(val)}（{_pct(diff)}），处于价值区之外"
        elif diff > 1:
            where = f"价格位于 1h 价值区上方 {_n(val)}（{_pct(diff)}）"
        else:
            where = f"价格正贴着 1h 价值区下沿 {_n(val)}"
    tone = "偏多" if d > 0 else "偏空" if d < 0 else "方向不明"
    return f"{sym} 多周期{tone}（加权 {w:+.0f}，{cons}）{'；' + where if where else ''}。"


def _cards(snap, mtf, lq) -> list[dict]:
    pos = snap.get("positioning") or {}
    lr = pos.get("global_ratio")
    lp = lq.get("near3_long_usd") or 0
    sp = lq.get("near3_short_usd") or 0
    ratio = (lp / sp) if sp else None
    return [
        {"label": "多周期加权", "value": f"{mtf.get('weighted_score', 0):+.1f}",
         "note": mtf.get("verdict_label", "") + " · " + mtf.get("consensus", ""),
         "tone": _sign(mtf.get("weighted_score"))},
        {"label": "同向周期",
         "value": f"{len(mtf.get('aligned', []))}/{len([r for r in mtf.get('rows', []) if 'error' not in r])}",
         "note": "反向：" + ("、".join(mtf.get("conflict", [])) or "无"),
         "tone": 0},
        {"label": "全网多空比", "value": _n(lr, 2) if lr else "--",
         "note": "每 1 个空头对应 %.1f 个多头账户" % lr if lr and lr > 0 else "中性",
         "tone": -1 if (lr and lr > 2.5) else 0},
        {"label": "3%内多空损比", "value": f"{ratio:.0f} : 1" if ratio else "--",
         "note": f"多 {_usd(lp)} / 空 {_usd(sp)}",
         "tone": -1 if (ratio and ratio > 3) else 0},
    ]


def _facts(snap, pos, fg, of, w) -> list[dict]:
    price = snap.get("price")
    nd = 6 if price and abs(price) < 1 else 2
    wb, wa = w.get("bid_total") or 0, w.get("ask_total") or 0
    return [
        {"k": "价格", "v": _n(price, nd), "note": f"24h {_pct(snap.get('change_pct'))}"},
        {"k": "24h 高/低", "v": f"{_n(snap.get('high'), nd)} / {_n(snap.get('low'), nd)}", "note": ""},
        {"k": "持仓量 OI", "v": _usd(snap.get("oi_usd")), "note": f"24h {_pct(snap.get('oi_change_pct'))}"},
        {"k": "资金费率", "v": f"{(snap.get('funding') or 0) * 100:.4f}%", "note": "多头付空头" if (snap.get("funding") or 0) > 0 else "空头付多头"},
        {"k": "24h 成交额", "v": _usd(snap.get("vol_usd")), "note": f"量比 {_n(snap.get('vol_ratio'), 2)}"},
        {"k": "盘口深度", "v": f"{_usd(wb)} / {_usd(wa)}", "note": f"买/卖（±{(w.get('span_pct') or 0) / 2:.3f}%）"},
        {"k": "盘口失衡", "v": _n((snap.get("imbalance") or {}).get("ratio"), 3), "note": "±0.5% 内买盘占比"},
        {"k": "大户持仓比", "v": _n(pos.get("top_position_ratio"), 2),
         "note": f"多头占 {_n(pos.get('top_position_long_pct'), 2)}%"},
        {"k": "大户账户比", "v": _n(pos.get("top_account_ratio"), 2),
         "note": f"多头占 {_n(pos.get('top_account_long_pct'), 2)}%"},
        {"k": "全网多空比", "v": _n(pos.get("global_ratio"), 2), "note": "按账户数"},
        {"k": "主动买卖比", "v": _n(pos.get("taker_ratio"), 2), "note": "上一完整小时"},
        {"k": "CVD 累计", "v": _usd(of.get("cvd")), "note": f"1h 买 {_usd(of.get('buy_1h'))} / 卖 {_usd(of.get('sell_1h'))}"},
        {"k": "恐贪指数", "v": str(fg.get("value") or "--"), "note": fg.get("label") or ""},
    ]


def _mtf_table(rows) -> dict:
    out = []
    for r in rows:
        if "error" in r:
            out.append({"tf": r["tf"], "error": r["error"]})
            continue
        top = sorted(r["signals"], key=lambda x: -abs(x["w"] * x["dir"]))[:5]
        out.append({
            "tf": r["tf"],
            "score": r["score"],
            "verdict": r["verdict_label"],
            "weight": _tf_weight(r["tf"]),
            "top": [{"dir": x["dir"], "label": x["label"], "w": x["w"]} for x in top],
            "pattern": (r["patterns"].get("latest") or {}).get("label"),
            "price": r["price"],
        })
    return {"rows": out}


def _tf_weight(tf: str) -> float:
    from . import config as C
    return C.TF_WEIGHT.get(tf, 1.0)


def _liq_block(lq) -> dict:
    if not lq.get("ok"):
        return {"ok": False}
    return {
        "ok": True,
        "model": lq.get("model"),
        "nearest_long": lq.get("nearest_long"),
        "nearest_short": lq.get("nearest_short"),
        "near3_long": lq.get("near3_long_usd"),
        "near3_short": lq.get("near3_short_usd"),
        "top_long": lq.get("top_long", [])[:4],
        "top_short": lq.get("top_short", [])[:4],
        "long_share": lq.get("long_share"),
    }


def _findings(sym, snap, mtf, lq, charts) -> list[dict]:
    """按数据条件生成「矛盾与辨析」段落。"""
    out = []
    rows = {r["tf"]: r for r in mtf.get("rows", []) if "error" not in r}
    pos = snap.get("positioning") or {}
    of = snap.get("orderflow") or {}
    c1, c4, c1d = charts.get("1h"), charts.get("4h"), charts.get("1d")
    wscore = mtf.get("weighted_score") or 0

    # 1. 周线冲突
    w1 = rows.get("1w")
    if w1 and abs(wscore) > 15:
        s_w, s_a = _sign(w1["score"]), _sign(wscore)
        if s_w and s_a and s_w != s_a:
            out.append({
                "title": "周线与整体方向冲突",
                "tone": 0,
                "body": f"周线得分 {w1['score']:+.1f}（{w1['verdict']}），与加权方向 {wscore:+.1f} 相反。"
                        f"这意味着短期趋势在周线尺度上尚未得到确认 —— "
                        f"要么是更大级别上涨中的回调，要么是周线还没跟上。"
                        f"在周线转向之前，任何同向结论都要打折。",
            })
        elif s_w == s_a:
            out.append({
                "title": "周线与整体方向一致",
                "tone": s_a,
                "body": f"周线得分 {w1['score']:+.1f}（{w1['verdict']}），与加权方向一致。"
                        f"跨周期没有分歧，结论的稳健性较高 —— 但一致性本身也意味着拥挤。",
            })

    # 2. 极端分数
    ext = [(tf, r["score"]) for tf, r in rows.items() if abs(r["score"]) >= 70]
    if ext:
        names = "、".join(f"{tf} {s:+.0f}" for tf, s in sorted(ext, key=lambda x: -abs(x[1])))
        out.append({
            "title": "极端分数：统计可靠性下降",
            "tone": 0,
            "body": f"{names} 已进入极端区间。历史上这套打分在 |分数|>50 的区间会失效甚至反向 "
                    f"（回测：-50~-30 档按方向期望 -0.28%，+50 以上做多期望 -0.39%）。"
                    f"极端分数更应该读作「动能接近耗尽」，而不是「趋势还会加速」。",
        })

    # 3. 超卖 + 趋势仍在
    if c1d and c1d.get("rsi") is not None and c1d.get("adx") is not None:
        if c1d["rsi"] < 32 and c1d["adx"] > 20:
            out.append({
                "title": "日线超卖，但趋势未止",
                "tone": 0,
                "body": f"日线 RSI {_n(c1d['rsi'], 1)} 已进入超卖区（<30 为超卖），"
                        f"但日线 ADX {_n(c1d['adx'], 1)} 说明趋势仍在运行。"
                        f"强趋势中超卖会持续钝化 —— 超卖是「弱势确认」，不是「反转信号」。"
                        f"两个指标同时为真时，趋势类指标的可靠性高于摆动类。",
            })

    # 4. 价值区位置
    if c1:
        vp = c1.get("vp") or {}
        price = snap.get("price")
        val, poc = vp.get("val"), vp.get("poc")
        if val and price:
            diff = (price / val - 1) * 100
            if diff < -1:
                out.append({
                    "title": "价格已跌出 1h 价值区",
                    "tone": -1,
                    "body": f"1h 价值区下沿 VAL {_n(val)}，现价 {_n(price)}，低了 {abs(diff):.1f}%。"
                            f"成交量重心 POC 在 {_n(poc)}（上方 {((poc / price - 1) * 100):.1f}%）。"
                            f"价格在价值区下方运行，意味着上方存在密集的解套抛压。",
                })
            elif diff <= 1:
                out.append({
                    "title": "价格正测试 1h 价值区下沿",
                    "tone": 0,
                    "body": f"1h VAL {_n(val)} 与现价几乎重合（{_pct(diff)}）。"
                            f"这是多空分界 —— 守住则价值区继续有效，失守则下移到更低的价值区。",
                })

    # 5. 多头拥挤
    lr = pos.get("global_ratio")
    ta = pos.get("top_account_ratio")
    if lr and lr > 2.5:
        body = f"全网多空比 {_n(lr, 2)}，意味着每 1 个空头对应 {lr - 1:.1f} 个多头账户。"
        if ta and ta > 2.5:
            body += f"大户账户比 {_n(ta, 2)}，说明连大户账户也在扎堆做多。"
        body += "多头拥挤的结构下，上涨缺少新增资金，下跌容易形成连锁强平。"
        out.append({"title": "多头拥挤", "tone": -1, "body": body})

    # 6. 清算不对称
    lp, sp = lq.get("near3_long_usd") or 0, lq.get("near3_short_usd") or 0
    if sp and lp / sp >= 3:
        out.append({
            "title": "下方清算燃料远多于上方",
            "tone": -1,
            "body": f"3% 区间内，多头爆仓盘 {_usd(lp)}，空头 {_usd(sp)}，比例 {lp / sp:.0f}:1。"
                    f"最近的密集带："
                    + "、".join(f"{_n(z['price'])}({_usd(z['long_usd'])})" for z in lq.get("top_long", [])[:3])
                    + "。向下突破时容易形成连续强平的加速，向上则缺少回补燃料。",
        })
    elif lp and sp and lp / sp <= 0.35:
        out.append({
            "title": "上方清算燃料多于下方",
            "tone": 1,
            "body": f"3% 区间内，空头爆仓盘 {_usd(sp)}，多头 {_usd(lp)}。"
                    f"向上突破容易触发空头回补的加速带。",
        })

    # 7. 缩量
    vr = snap.get("vol_ratio")
    if vr is not None and vr < 0.6:
        out.append({
            "title": "当前缩量",
            "tone": 0,
            "body": f"量比 {_n(vr, 2)}，当前 1h 成交量只有过去 24 根均量的 {vr * 100:.0f}%。"
                    f"缩量下的涨跌都容易失真，突破的假信号比例会上升。",
        })

    # 8. 挤压
    if c1 and c1.get("squeeze_on") is not None:
        if c1["squeeze_on"] == 1:
            out.append({
                "title": "1h 处于波动率压缩",
                "tone": 0,
                "body": "布林带被肯特纳通道包住（TTM 挤压），波动率处在压缩状态。"
                        "压缩本身不指示方向，但意味着一旦释放，幅度会明显大于近期均值。",
            })

    # 9. 口径冲突：taker 与 CVD
    tk = pos.get("taker_ratio")
    cvd = of.get("cvd")
    if tk and cvd is not None and ((tk > 1.05 and cvd < 0) or (tk < 0.95 and cvd > 0)):
        out.append({
            "title": "两项资金指标方向相反",
            "tone": 0,
            "body": f"交易所公布的主动买卖比 {_n(tk, 2)}，与按 K 线 taker 量累计的 CVD {_usd(cvd)} 方向相反。"
                    f"主要原因是口径不同：taker 比通常是「上一个已收盘小时」，"
                    f"而 K 线最后一根是「当前未走完的小时」。判断当下状态应以 CVD 为准。",
        })

    # 10. 局部结构 vs 全局结构
    if c1 and c4 and c1.get("structure") and c4.get("structure"):
        if c1["structure"] != c4["structure"]:
            out.append({
                "title": "局部结构与全局结构不一致",
                "tone": 0,
                "body": f"1h 是「{c1['structure']}」，4h 是「{c4['structure']}」。"
                        f"结构识别基于分形摆动点，小周期的结构只是大周期趋势中的一次扰动 —— "
                        f"单看一个周期容易得出「局部正确、全局错误」的结论。",
            })

    if not out:
        out.append({"title": "暂无明显矛盾", "tone": 0,
                    "body": "各周期与各维度读数未出现显著冲突，结构相对一致。"})
    return out


def _watch(snap, mtf, lq, charts) -> list[dict]:
    c1, c4 = charts.get("1h"), charts.get("4h")
    price = snap.get("price")
    strengthen, weaken = [], []

    if c1:
        sup = (c1.get("support") or [None])[0]
        res = (c1.get("resistance") or [None])[0]
        if sup:
            strengthen.append(f"1h 收盘跌破 {_n(sup)}（1h 最近支撑）")
        if res:
            weaken.append(f"1h 收盘站上 {_n(res)}（1h 最近阻力）")
        if c1.get("st_value"):
            weaken.append(f"1h 收复超级趋势 {_n(c1['st_value'])}")
        if c1.get("cloud_bot"):
            weaken.append(f"1h 重回一目云层 {_n(c1['cloud_bot'])} 上方")
    if c4 and c4.get("st_value"):
        weaken.append(f"4h 收盘站上超级趋势 {_n(c4['st_value'])}（中期结构翻转）")
    if snap.get("high"):
        weaken.append(f"收复 24h 高点 {_n(snap['high'])}")
    if snap.get("low"):
        strengthen.append(f"跌破 24h 低点 {_n(snap['low'])}")

    lq_ok = lq.get("ok")
    if lq_ok:
        nl = lq.get("nearest_long")
        ns = lq.get("nearest_short")
        if nl:
            strengthen.append(f"进入多损密集带 {_n(nl['price'])}（下方 {abs(nl['dist_pct']):.2f}%）")
        if ns:
            weaken.append(f"突破空损密集带 {_n(ns['price'])}（上方 {ns['dist_pct']:.2f}%）")

    lr = (snap.get("positioning") or {}).get("global_ratio")
    if lr:
        strengthen.append(f"全网多空比升破 {lr + 0.5:.1f}（多头继续加仓，拥挤加剧）")
        weaken.append(f"全网多空比跌破 1.5（多头被清洗完，拥挤解除）")

    cvd = (snap.get("orderflow") or {}).get("cvd")
    if cvd is not None:
        weaken.append("CVD 由负转正并持续")

    return [
        {"title": "会强化当前判断的", "items": strengthen[:5]},
        {"title": "会削弱当前判断的", "items": weaken[:6]},
    ]


def _reliability() -> list[dict]:
    return [
        {"item": "价格 / OI / 资金费率 / 多空比", "level": "高", "note": "交易所原始数据，无加工"},
        {"item": "技术指标（ADX、RSI、一目、SAR…）", "level": "高", "note": "标准算法，与主流平台逐位比对过"},
        {"item": "K 线 / 图表形态", "level": "中", "note": "规则式识别，不理解上下文"},
        {"item": "多周期加权打分", "level": "中低", "note": "权重为经验值，回测 IC 约 0.03"},
        {"item": "多空损（清算分布）", "level": "低-中", "note": "模型估算：杠杆分布与开仓价均为假设"},
    ]


# ---------------------------------------------------------------- Markdown
def _to_markdown(d: dict) -> str:
    L = []
    L.append(f"# {d['symbol']} 多周期技术研判\n")
    L.append(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M', time.localtime(d['ts'] / 1000))}\n")
    L.append(f"> {d['disclaimer']}\n")
    L.append(f"\n## 结论\n\n{d['headline']}\n")
    L.append("\n".join(f"- **{c['label']}**：{c['value']}（{c['note']}）" for c in d["cards"]))
    L.append("\n\n## 事实层\n")
    L.append("| 指标 | 数值 | 说明 |")
    L.append("|---|---|---|")
    for x in d["facts"]:
        L.append(f"| {x['k']} | {x['v']} | {x['note']} |")
    L.append("\n\n## 多周期结构\n")
    L.append("| 周期 | 得分 | 判定 | 权重 | 主要依据 |")
    L.append("|---|---|---|---|---|")
    for r in d["mtf"]["rows"]:
        if "error" in r:
            L.append(f"| {r['tf']} | -- | 无数据 | -- | {r['error']} |")
            continue
        tops = "、".join(("多" if t["dir"] > 0 else "空" if t["dir"] < 0 else "·") + t["label"]
                        for t in r["top"] if t["dir"] != 0)
        L.append(f"| {r['tf']} | {r['score']:+.1f} | {r['verdict']} | {r['weight']} | {tops} |")
    L.append("\n\n## 关键价位\n")
    for tf, c in d["levels"].items():
        vp = c.get("vp") or {}
        L.append(f"\n**{tf}**（结构：{c['structure']}）\n")
        L.append(f"- 支撑：{' / '.join(_n(x) for x in c['support']) or '--'}")
        L.append(f"- 阻力：{' / '.join(_n(x) for x in c['resistance']) or '--'}")
        if vp:
            L.append(f"- 成交量分布：POC {_n(vp.get('poc'))} / VAH {_n(vp.get('vah'))} / VAL {_n(vp.get('val'))}")
        if c.get("pivot"):
            p = c["pivot"]
            L.append(f"- 枢轴：P {_n(p.get('P'))} / R1 {_n(p.get('R1'))} / S1 {_n(p.get('S1'))}")
        L.append(f"- 指标：ADX {_n(c.get('adx'), 1)} · RSI {_n(c.get('rsi'), 1)} · "
                 f"ATR {_n(c.get('atr'))} · 云顶 {_n(c.get('cloud_top'))} · 云底 {_n(c.get('cloud_bot'))}")
    if d["liq"].get("ok"):
        lq = d["liq"]
        L.append("\n## 多空损（预估清算）\n")
        L.append(f"- 模型：{lq.get('model')}")
        if lq.get("nearest_long"):
            z = lq["nearest_long"]
            L.append(f"- 最近多头区 {_n(z['price'])}（{_pct(z['dist_pct'])}）{_usd(z['long_usd'])}")
        if lq.get("nearest_short"):
            z = lq["nearest_short"]
            L.append(f"- 最近空头区 {_n(z['price'])}（{_pct(z['dist_pct'])}）{_usd(z['short_usd'])}")
        L.append(f"- 3% 内：多损 {_usd(lq.get('near3_long'))} / 空损 {_usd(lq.get('near3_short'))}")
    L.append("\n\n## 矛盾与辨析\n")
    for f in d["findings"]:
        L.append(f"\n**{f['title']}**\n\n{f['body']}")
    L.append("\n\n## 观察清单\n")
    for w in d["watch"]:
        L.append(f"\n**{w['title']}**\n")
        for it in w["items"]:
            L.append(f"- {it}")
    L.append("\n\n## 可靠性边界\n")
    L.append("| 环节 | 可信度 | 说明 |")
    L.append("|---|---|---|")
    for r in d["reliability"]:
        L.append(f"| {r['item']} | {r['level']} | {r['note']} |")
    return "\n".join(L)
