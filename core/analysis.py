"""研判层：单周期信号打分 + 多周期共振。

设计要点：打分的核心是 eval_at(bars, ind, pat, i) —— 对任意历史下标 i 求值，
回测与实时共用同一套规则，保证「回测的就是你看到的」。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import time

import numpy as np

from . import config as C
from . import feed, indicators as I, patterns as P

VERDICT = [
    (55, "strong_bull", "强多"),
    (20, "bull", "偏多"),
    (-20, "neutral", "中性"),
    (-55, "bear", "偏空"),
    (-101, "strong_bear", "强空"),
]

# 指标稳定所需的最少前置 K 线
WARMUP = 60


def _verdict(score: float) -> tuple[str, str]:
    for th, key, label in VERDICT:
        if score > th:
            return key, label
    return "strong_bear", "强空"


def _sig(key, label, d, w, value=None, note=""):
    return {"key": key, "label": label, "dir": int(d), "w": float(w),
            "value": value, "note": note}


def _num(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(f) or np.isinf(f)) else f


def _divergence(h, l, c, rsi_arr, i) -> dict | None:
    """在下标 i 处判断 RSI 背离（只看 i 之前的数据）。"""
    if i < 40:
        return None
    lo_i = max(0, i - 79)
    hi, lo = I.swing_points(h[lo_i:i + 1], l[lo_i:i + 1])
    hi, lo = hi + lo_i, lo + lo_i
    if len(hi) >= 2:
        a, b = hi[-2], hi[-1]
        ra, rb = _num(rsi_arr[a]), _num(rsi_arr[b])
        if h[b] > h[a] and ra is not None and rb is not None and rb < ra - 1:
            return {"dir": -1, "label": "顶背离", "note": "价格新高但 RSI 未新高",
                    "conf": min(1.0, 0.5 + (ra - rb) / 30.0)}
    if len(lo) >= 2:
        a, b = lo[-2], lo[-1]
        ra, rb = _num(rsi_arr[a]), _num(rsi_arr[b])
        if l[b] < l[a] and ra is not None and rb is not None and rb > ra + 1:
            return {"dir": 1, "label": "底背离", "note": "价格新低但 RSI 未新低",
                    "conf": min(1.0, 0.5 + (rb - ra) / 30.0)}
    return None


# ================================================================ 核心：按下标求值
def eval_at(bars: dict, ind: dict, pat: dict, i: int) -> tuple[list[dict], float]:
    """在下标 i 处评估全部信号，返回 (信号列表, 打分 -100~100)。

    bars / ind 为全量序列，pat 必须是「以 i 结尾」的形态检测结果。
    """
    o, h, l, c, v = bars["open"], bars["high"], bars["low"], bars["close"], bars["volume"]
    price = float(c[i])

    def av(a):
        return _num(a[i]) if a is not None and len(a) > i else None

    sigs: list[dict] = []

    ma20, ma60, ma120 = av(ind["ma"]["20"]), av(ind["ma"]["60"]), av(ind["ma"]["120"])
    ema12, ema21, ema55 = av(ind["ema"]["12"]), av(ind["ema"]["21"]), av(ind["ema"]["55"])

    # 1. 均线排列
    if ma20 is not None and ma60 is not None:
        if price > ma20 > ma60 and (ma120 is None or ma60 > ma120):
            sigs.append(_sig("ma_align", "均线多头排列", 1, 1.5, round(ma20, 2), f"价>{ma20:.2f}>MA60"))
        elif price < ma20 < ma60 and (ma120 is None or ma60 < ma120):
            sigs.append(_sig("ma_align", "均线空头排列", -1, 1.5, round(ma20, 2), f"价<{ma20:.2f}<MA60"))
        else:
            sigs.append(_sig("ma_align", "均线交织", 0, 0.8, round(ma20, 2), "方向未明"))

    # 2. EMA 排列
    if ema12 is not None and ema21 is not None and ema55 is not None:
        d = 1 if ema12 > ema21 > ema55 else -1 if ema12 < ema21 < ema55 else 0
        sigs.append(_sig("ema_align", "EMA 排列", d, 1.2, round(ema12, 2),
                         "12>21>55" if d > 0 else "12<21<55" if d < 0 else "交叉"))

    # 3. MACD
    dif, dea, hist = av(ind["macd"]["dif"]), av(ind["macd"]["dea"]), av(ind["macd"]["hist"])
    if dif is not None and dea is not None:
        prev = _num(ind["macd"]["hist"][i - 1]) if i >= 1 else None
        rising = prev is not None and hist is not None and hist > prev
        if dif > dea and (dif > 0 or rising):
            sigs.append(_sig("macd", "MACD 金叉向上", 1, 1.5, round(dif, 4),
                             f"DIF {dif:.2f} > DEA {dea:.2f}"))
        elif dif < dea and (dif < 0 or not rising):
            sigs.append(_sig("macd", "MACD 死叉向下", -1, 1.5, round(dif, 4),
                             f"DIF {dif:.2f} < DEA {dea:.2f}"))
        else:
            sigs.append(_sig("macd", "MACD 收敛", 0, 0.9, round(dif, 4), "动能转换中"))

    # 4. RSI
    r = av(ind["rsi"])
    if r is not None:
        if r >= 70:
            sigs.append(_sig("rsi", "RSI 超买", -1, 0.9, round(r, 1), ">70，注意回落"))
        elif r <= 30:
            sigs.append(_sig("rsi", "RSI 超卖", 1, 0.9, round(r, 1), "<30，注意反弹"))
        elif r > 55:
            sigs.append(_sig("rsi", "RSI 偏强", 1, 1.0, round(r, 1), "50 上方"))
        elif r < 45:
            sigs.append(_sig("rsi", "RSI 偏弱", -1, 1.0, round(r, 1), "50 下方"))
        else:
            sigs.append(_sig("rsi", "RSI 中性", 0, 0.6, round(r, 1), "40~60"))

    # 5. KDJ
    k, d_ = av(ind["kdj"]["k"]), av(ind["kdj"]["d"])
    if k is not None and d_ is not None:
        pk = _num(ind["kdj"]["k"][i - 1]) if i >= 1 else None
        pd_ = _num(ind["kdj"]["d"][i - 1]) if i >= 1 else None
        if pk is not None and pd_ is not None and pk <= pd_ and k > d_:
            sigs.append(_sig("kdj", "KDJ 金叉", 1, 0.8, round(k, 1), "K 上穿 D"))
        elif pk is not None and pd_ is not None and pk >= pd_ and k < d_:
            sigs.append(_sig("kdj", "KDJ 死叉", -1, 0.8, round(k, 1), "K 下穿 D"))
        else:
            sigs.append(_sig("kdj", "KDJ 运行中", 1 if k > d_ else -1, 0.4, round(k, 1), ""))

    # 6. BOLL
    bu, bm, bl = av(ind["boll"]["upper"]), av(ind["boll"]["mid"]), av(ind["boll"]["lower"])
    if bu and bm and bl and bu > bl:
        pos = (price - bl) / (bu - bl)
        if pos > 0.95:
            sigs.append(_sig("boll", "布林上轨外", -1, 0.9, round(pos, 2), "超涨，回归概率高"))
        elif pos < 0.05:
            sigs.append(_sig("boll", "布林下轨外", 1, 0.9, round(pos, 2), "超跌，回归概率高"))
        else:
            d = 1 if price > bm else -1
            sigs.append(_sig("boll", "布林带内", d, 0.7, round(pos, 2),
                             "中轨上方" if d > 0 else "中轨下方"))

    # 7. ADX + DI
    adx_v, pdi, mdi = av(ind["adx"]["adx"]), av(ind["adx"]["plus_di"]), av(ind["adx"]["minus_di"])
    if adx_v is not None and pdi is not None and mdi is not None:
        if adx_v >= 25:
            d = 1 if pdi > mdi else -1
            sigs.append(_sig("adx", "趋势明确", d, 1.3, round(adx_v, 1),
                             f"ADX {adx_v:.1f}，{'+DI 占优' if d > 0 else '-DI 占优'}"))
        else:
            sigs.append(_sig("adx", "趋势偏弱", 0, 1.0, round(adx_v, 1), f"ADX {adx_v:.1f} < 25"))

    # 8. 超级趋势
    st_dir = int(ind["supertrend"]["direction"][i])
    st_val = av(ind["supertrend"]["value"])
    sigs.append(_sig("supertrend", "超级趋势", st_dir, 1.4,
                     round(st_val, 2) if st_val else None,
                     "多头运行" if st_dir > 0 else "空头运行"))

    # 9. Aroon
    au, ad = av(ind["aroon"]["up"]), av(ind["aroon"]["down"])
    if au is not None and ad is not None:
        if au >= 70 and au > ad:
            sigs.append(_sig("aroon", "Aroon 强多", 1, 0.8, round(au, 0), "近期新高不断"))
        elif ad >= 70 and ad > au:
            sigs.append(_sig("aroon", "Aroon 强空", -1, 0.8, round(ad, 0), "近期新低不断"))
        else:
            sigs.append(_sig("aroon", "Aroon 中性", 0, 0.5, round(au, 0), ""))

    # 10. 价格结构
    # 必须用「以 i 结尾」的窗口现算：ind["structure"] 是全量数组末尾算的，
    # 在 i 处用它会偷看未来（回测里的未来函数）。
    lb = min(120, i + 1)
    st = I.trend_structure(h[i - lb + 1:i + 1], l[i - lb + 1:i + 1])
    sigs.append(_sig("structure", "价格结构", max(-1, min(1, st["score"] / 2)), 1.5,
                     st["label"], f"{st['label']}（HH/HL 计分 {st['score']}）"))

    # 11. K 线形态
    if pat["latest"]:
        lp = pat["latest"]
        sigs.append(_sig("candle", f"K线：{lp['label']}", lp["dir"], 1.2,
                         round(lp["conf"], 2), lp["note"]))
    else:
        sigs.append(_sig("candle", "K线：无显著形态", 0, 0.4, None, ""))

    # 12. 图表形态
    if pat["chart"]:
        cs = pat["chart"]
        chart_bias = int(np.sign(sum(p["dir"] * p["conf"] for p in cs)))
        names = "、".join(p["label"] for p in cs[-3:])
        sigs.append(_sig("chart", f"结构：{names}", chart_bias, 1.3, len(cs), "图表形态"))
    else:
        sigs.append(_sig("chart", "结构：无明显形态", 0, 0.4, None, ""))

    # 13. 资金流
    cmf_v = av(ind["cmf"])
    if cmf_v is not None:
        d = 1 if cmf_v > 0.05 else -1 if cmf_v < -0.05 else 0
        sigs.append(_sig("cmf", "资金流 CMF", d, 0.9, round(cmf_v, 3),
                         "资金流入" if d > 0 else "资金流出" if d < 0 else "均衡"))

    # 14. MFI
    mfi_v = av(ind["mfi"])
    if mfi_v is not None:
        d = -1 if mfi_v > 80 else 1 if mfi_v < 20 else (1 if mfi_v > 50 else -1)
        sigs.append(_sig("mfi", "MFI 资金指标", d, 0.7, round(mfi_v, 1),
                         "超买" if mfi_v > 80 else "超卖" if mfi_v < 20 else ""))

    # 15. CCI
    cci_v = av(ind["cci"])
    if cci_v is not None:
        d = 1 if cci_v > 100 else -1 if cci_v < -100 else (1 if cci_v > 0 else -1)
        sigs.append(_sig("cci", "CCI 顺势", d, 0.6, round(cci_v, 1),
                         "强势区" if cci_v > 100 else "弱势区" if cci_v < -100 else "常区"))

    # 16. WR
    wr_v = av(ind["wr"])
    if wr_v is not None:
        d = -1 if wr_v > -20 else 1 if wr_v < -80 else (1 if wr_v > -50 else -1)
        sigs.append(_sig("wr", "威廉指标", d, 0.5, round(wr_v, 1), ""))

    # 17. VWAP
    vw = av(ind["vwap"])
    if vw:
        d = 1 if price > vw else -1
        sigs.append(_sig("vwap", "VWAP 之上" if d > 0 else "VWAP 之下", d, 0.9,
                         round(vw, 2), f"均价 {vw:.2f}"))

    # 18. 背离
    div = _divergence(h, l, c, ind["rsi"], i)
    if div:
        sigs.append(_sig("divergence", div["label"], div["dir"], 1.2,
                         round(div["conf"], 2), div["note"]))

    # ======================== TradingView 系列 ========================
    # 19. 一目均衡表
    ichi = ind["ichimoku"]
    ctop, cbot = av(ichi["cloud_top"]), av(ichi["cloud_bot"])
    tk, kj = av(ichi["tenkan"]), av(ichi["kijun"])
    if ctop is not None and cbot is not None and tk is not None and kj is not None:
        if price > ctop and tk > kj:
            sigs.append(_sig("ichimoku", "一目：云上金叉", 1, 1.4, round(ctop, 2), "价在云上 + 转换>基准"))
        elif price < cbot and tk < kj:
            sigs.append(_sig("ichimoku", "一目：云下死叉", -1, 1.4, round(cbot, 2), "价在云下 + 转换<基准"))
        elif price > ctop:
            sigs.append(_sig("ichimoku", "一目：云上持稳", 1, 0.9, round(ctop, 2), "站上云层"))
        elif price < cbot:
            sigs.append(_sig("ichimoku", "一目：云下承压", -1, 0.9, round(cbot, 2), "跌破云层"))
        else:
            sigs.append(_sig("ichimoku", "一目：云中震荡", 0, 0.7, round((ctop + cbot) / 2, 2), "云层内部"))

    # 20. 抛物线 SAR
    ps = av(ind["psar"])
    if ps is not None:
        d = 1 if price > ps else -1
        sigs.append(_sig("psar", "抛物线SAR", d, 1.1, round(ps, 2),
                         "SAR 在下方（多头）" if d > 0 else "SAR 在上方（空头）"))

    # 21. 肯特纳通道
    ku, kl = av(ind["keltner"]["upper"]), av(ind["keltner"]["lower"])
    if ku is not None and kl is not None:
        if price > ku:
            sigs.append(_sig("keltner", "突破肯特纳上轨", 1, 1.0, round(ku, 2), "强势突破"))
        elif price < kl:
            sigs.append(_sig("keltner", "跌破肯特纳下轨", -1, 1.0, round(kl, 2), "弱势破位"))
        else:
            d = 1 if price > av(ind["keltner"]["mid"]) else -1
            sigs.append(_sig("keltner", "肯特纳通道内", d, 0.5, round(ku, 2), ""))

    # 22. 唐奇安通道突破
    du, dl = av(ind["donchian"]["upper"]), av(ind["donchian"]["lower"])
    if du is not None and dl is not None:
        if price >= du:
            sigs.append(_sig("donchian", "唐奇安上轨突破", 1, 1.1, round(du, 2), "20 周期新高"))
        elif price <= dl:
            sigs.append(_sig("donchian", "唐奇安下轨跌破", -1, 1.1, round(dl, 2), "20 周期新低"))
        else:
            sigs.append(_sig("donchian", "唐奇安通道内", 0, 0.4, round((du + dl) / 2, 2), ""))

    # 23. HMA 方向
    hm = av(ind["hma"])
    if hm is not None and i >= 1:
        prev = _num(ind["hma"][i - 1])
        if prev is not None:
            d = 1 if hm > prev else -1
            sigs.append(_sig("hma", "HMA 方向", d, 0.9, round(hm, 2),
                             "均线上行" if d > 0 else "均线下行"))

    # 24. 经典随机指标
    sk, sd = av(ind["stoch"]["k"]), av(ind["stoch"]["d"])
    if sk is not None and sd is not None and i >= 1:
        pk = _num(ind["stoch"]["k"][i - 1]); pd_ = _num(ind["stoch"]["d"][i - 1])
        if pk is not None and pd_ is not None and pk <= pd_ and sk > sd:
            sigs.append(_sig("stoch", "随机指标金叉", 1, 0.7, round(sk, 1), "K 上穿 D"))
        elif pk is not None and pd_ is not None and pk >= pd_ and sk < sd:
            sigs.append(_sig("stoch", "随机指标死叉", -1, 0.7, round(sk, 1), "K 下穿 D"))
        else:
            sigs.append(_sig("stoch", "随机指标运行", 1 if sk > sd else -1, 0.4, round(sk, 1), ""))

    # 25. 奇妙振荡器 AO
    ao_v = av(ind["ao"])
    if ao_v is not None:
        d = 1 if ao_v > 0 else -1
        sigs.append(_sig("ao", "奇妙振荡 AO", d, 0.7, round(ao_v, 4),
                         "零轴上方" if d > 0 else "零轴下方"))

    # 26. 涡旋指标
    vip, vim = av(ind["vortex"]["vi_plus"]), av(ind["vortex"]["vi_minus"])
    if vip is not None and vim is not None:
        d = 1 if vip > vim else -1
        sigs.append(_sig("vortex", "涡旋指标", d, 0.7, round(vip, 2),
                         "VI+ 占优" if d > 0 else "VI- 占优"))

    # 27. 挤压动量
    sq = av(ind["squeeze"]["momentum"])
    if sq is not None:
        on = int(ind["squeeze"]["on"][i])
        d = 1 if sq > 0 else -1
        sigs.append(_sig("squeeze", "挤压动量", d, 0.6, round(sq, 4),
                         ("挤压中 · " if on else "") + ("动能偏多" if d > 0 else "动能偏空")))

    # 28. TRIX
    tx, tsig = av(ind["trix"]["trix"]), av(ind["trix"]["signal"])
    if tx is not None and tsig is not None:
        d = 1 if tx > tsig else -1
        sigs.append(_sig("trix", "TRIX 趋势", d, 0.6, round(tx, 3),
                         "TRIX 上穿信号线" if d > 0 else "TRIX 下穿信号线"))

    # 29. 终极振荡器
    uo_v = av(ind["uo"])
    if uo_v is not None:
        d = -1 if uo_v > 70 else 1 if uo_v < 30 else (1 if uo_v > 50 else -1)
        sigs.append(_sig("uo", "终极振荡器", d, 0.5, round(uo_v, 1),
                         "超买" if uo_v > 70 else "超卖" if uo_v < 30 else ""))

    # 30. 蔡金振荡
    ch = av(ind["chaikin"]["osc"])
    if ch is not None and abs(ch) > 1e-12:
        d = 1 if ch > 0 else -1
        sigs.append(_sig("chaikin", "蔡金资金流", d, 0.6, round(ch, 2),
                         "资金流入" if d > 0 else "资金流出"))

    tw = sum(s["w"] for s in sigs) or 1.0
    score = 100.0 * sum(s["dir"] * s["w"] for s in sigs) / tw
    return sigs, score


def _pct(c, i):
    if i < 1:
        return None
    return round((float(c[i]) / float(c[i - 1]) - 1) * 100, 3)


# ================================================================ 单周期
def analyze(symbol: str, tf: str) -> dict:
    bars = feed.klines(symbol, tf)
    c = bars["close"]
    i = len(c) - 1
    ind = I.compute_all(bars)
    pat = P.detect(bars)
    sigs, score = eval_at(bars, ind, pat, i)
    snap = I.snapshot(ind, bars)
    key, label = _verdict(score)
    price = float(c[i])

    return {
        "symbol": symbol,
        "tf": tf,
        "price": round(price, 6),
        "bars": int(len(c)),
        "stale": bool(bars.get("stale")),
        "score": round(score, 1),
        "verdict": key,
        "verdict_label": label,
        "signals": sigs,
        "bull_signals": [s["label"] for s in sigs if s["dir"] > 0 and s["w"] >= 0.8],
        "bear_signals": [s["label"] for s in sigs if s["dir"] < 0 and s["w"] >= 0.8],
        "patterns": pat,
        "snapshot": snap,
        "levels": {
            "support": ind["sr"]["support"],
            "resistance": ind["sr"]["resistance"],
            "fib": ind["fib"],
            "volprofile": {k: ind["volprofile"][k]
                           for k in ("poc", "vah", "val") if k in ind["volprofile"]},
        },
        "change_pct": _pct(c, i),
    }


# ================================================================ 多周期
def _summary(sym, direction, weighted, consensus, aligned, conflict, big_score) -> str:
    if direction == 0:
        head = f"{sym} 多周期方向不一致，暂无共振"
    elif direction > 0:
        head = f"{sym} 多周期偏多（加权 {weighted:+.0f}）"
    else:
        head = f"{sym} 多周期偏空（加权 {weighted:+.0f}）"
    parts = [head]
    if aligned:
        parts.append("看多周期：" + "、".join(aligned) if direction > 0
                     else "看空周期：" + "、".join(aligned))
    if conflict:
        parts.append("反向周期：" + "、".join(conflict))
    if big_score > 8:
        parts.append("大周期（4h/1d/1w）偏多")
    elif big_score < -8:
        parts.append("大周期（4h/1d/1w）偏空")
    parts.append(f"结论：{consensus}")
    return "；".join(parts) + "。"


_mtf_last: dict[str, tuple[float, dict]] = {}


def mtf(symbol: str) -> dict:
    sym = feed.base_symbol(feed.norm_symbol(symbol))
    tfs = list(C.MTF_TIMEFRAMES)
    rows_map: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=len(tfs)) as pool:
        futs = {pool.submit(analyze, sym, tf): tf for tf in tfs}
        for fut in futs:
            tf = futs[fut]
            try:
                rows_map[tf] = fut.result()
            except Exception as e:  # noqa: BLE001
                rows_map[tf] = {"symbol": sym, "tf": tf, "error": str(e), "score": 0,
                                "verdict": "neutral", "verdict_label": "无数据", "signals": []}
    rows = [rows_map[tf] for tf in tfs]
    # 全部周期都失败 = 通道断了：拿上一次成功的结果顶住，并标记延迟
    if not any("error" not in r for r in rows):
        prev = _mtf_last.get(sym)
        if prev:
            stale = dict(prev[1])
            stale["stale"] = True
            stale["stale_age_s"] = int(time.time() - prev[0])
            return stale

    ok = [r for r in rows if "error" not in r]
    total_w = sum(C.TF_WEIGHT[r["tf"]] for r in ok) or 1.0
    weighted = sum(r["score"] * C.TF_WEIGHT[r["tf"]] for r in ok) / total_w

    big = [r for r in ok if r["tf"] in ("4h", "1d", "1w")]
    big_score = (sum(r["score"] * C.TF_WEIGHT[r["tf"]] for r in big) /
                 (sum(C.TF_WEIGHT[r["tf"]] for r in big) or 1.0)) if big else 0.0

    direction = 1 if weighted > 8 else -1 if weighted < -8 else 0
    aligned = [r["tf"] for r in ok
               if (r["score"] > 8 and direction > 0) or (r["score"] < -8 and direction < 0)]
    conflict = [r["tf"] for r in ok
                if (r["score"] < -8 and direction > 0) or (r["score"] > 8 and direction < 0)]

    key, label = _verdict(weighted)
    if direction == 0:
        consensus = "多周期分歧"
    elif len(aligned) >= 5:
        consensus = "强共振"
    elif len(aligned) >= 3:
        consensus = "部分共振"
    else:
        consensus = "弱信号"

    big_ok = (big_score > 8 and direction > 0) or (big_score < -8 and direction < 0)

    result = {
        "symbol": sym,
        "price": ok[-1]["price"] if ok else None,
        "rows": rows,
        "weighted_score": round(weighted, 1),
        "big_score": round(big_score, 1),
        "verdict": key,
        "verdict_label": label,
        "consensus": consensus,
        "direction": direction,
        "aligned": aligned,
        "conflict": conflict,
        "alignment_ratio": round(len(aligned) / len(ok), 2) if ok else 0,
        "big_tf_agree": bool(big_ok),
        "summary": _summary(sym, direction, weighted, consensus, aligned, conflict, big_score),
        "stale": False,
    }
    _mtf_last[sym] = (time.time(), result)
    return result
