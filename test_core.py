"""自检：用独立算法交叉验证指标，并跑通单周期/多周期研判。

不依赖任何第三方库，直接 python test_core.py
"""
from __future__ import annotations

import sys
import time

import numpy as np

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from core import analysis, feed, indicators as I, patterns as P, config as C  # noqa: E402


def check(name, got, want, tol=1e-6):
    ok = got is not None and want is not None and abs(got - want) <= tol
    print(f"  [{'OK ' if ok else 'FAIL'}] {name:<28} got={got}  want={want}")
    return ok


def main():
    print("=" * 70)
    print("1) 抓取 BTC 1h")
    print("=" * 70)
    t0 = time.time()
    bars = feed.klines("BTC", "1h")
    print(f"  bars={len(bars['close'])}  用时 {time.time()-t0:.0f} ms  "
          f"stale={bars['stale']}  最后价={bars['close'][-1]:.2f}")

    c, h, l, v = bars["close"], bars["high"], bars["low"], bars["volume"]
    ind = I.compute_all(bars)

    print("\n" + "=" * 70)
    print("2) 指标交叉验证（与手算比对）")
    print("=" * 70)
    ok = True
    ok &= check("MA20", I._f(ind["ma"]["20"][-1]), float(np.mean(c[-20:])), 1e-6)
    ok &= check("MA60", I._f(ind["ma"]["60"][-1]), float(np.mean(c[-60:])), 1e-6)
    ok &= check("BOLL_mid == MA20", I._f(ind["boll"]["mid"][-1]), I._f(ind["ma"]["20"][-1]), 1e-6)
    boll_sd = float(np.std(c[-20:]))
    ok &= check("BOLL_upper", I._f(ind["boll"]["upper"][-1]),
                I._f(ind["boll"]["mid"][-1]) + 2 * boll_sd, 1e-6)

    # MACD 恒等式
    dif = ind["macd"]["dif"][-1]
    dea = ind["macd"]["dea"][-1]
    ok &= check("MACD hist = 2*(DIF-DEA)", I._f(ind["macd"]["hist"][-1]), I._f(2 * (dif - dea)), 1e-6)

    # RSI 手工 Wilder
    n = C.RSI_PERIOD
    d = np.diff(c)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag = gain[:n].mean()
    al = loss[:n].mean()
    for k in range(n, len(d)):
        ag = (ag * (n - 1) + gain[k]) / n
        al = (al * (n - 1) + loss[k]) / n
    manual_rsi = 100 - 100 / (1 + ag / al) if al > 0 else 100
    ok &= check("RSI14 (Wilder)", I._f(ind["rsi"][-1]), round(manual_rsi, 8), 1e-5)

    # 布林带内位置自洽
    bu, bl = ind["boll"]["upper"][-1], ind["boll"]["lower"][-1]
    ok &= abs(c[-1] - I._f(ind["boll"]["mid"][-1])) <= (bu - bl) / 2 * 1.01

    r = I._f(ind["rsi"][-1])
    print(f"  [{'OK ' if 0 <= r <= 100 else 'FAIL'}] RSI 值域                got={r}")
    adx_v = I._f(ind["adx"]["adx"][-1])
    print(f"  [{'OK ' if 0 <= adx_v <= 100 else 'FAIL'}] ADX 值域                got={adx_v}")
    mfi_v = I._f(ind["mfi"][-1])
    print(f"  [{'OK ' if 0 <= mfi_v <= 100 else 'FAIL'}] MFI 值域                got={mfi_v}")
    wr_v = I._f(ind["wr"][-1])
    print(f"  [{'OK ' if -100 <= wr_v <= 0 else 'FAIL'}] WR 值域                 got={wr_v}")

    # 长度一致性
    lens = {len(a) for a in [ind["rsi"], ind["atr"], ind["mfi"], ind["cci"], ind["wr"], ind["cmf"]]}
    print(f"  [{'OK ' if lens == {len(c)} else 'FAIL'}] 序列长度一致            got={lens} want={{{len(c)}}}")

    # 无 NaN 尾巴
    tail_nan = [k for k in ("rsi", "atr", "mfi", "cci", "wr", "cmf") if np.isnan(ind[k][-1])]
    print(f"  [{'OK ' if not tail_nan else 'FAIL'}] 末值无 NaN               got={tail_nan}")

    print("\n" + "=" * 70)
    print("3) BTC 1h 快照")
    print("=" * 70)
    snap = I.snapshot(ind, bars)
    for k in ("close", "ma20", "ma60", "ma200", "ema12", "ema21", "macd_dif", "rsi",
              "boll_upper", "boll_lower", "kdj_k", "adx", "atr", "mfi", "cci", "wr",
              "st_dir", "vwap"):
        print(f"  {k:<14} {snap[k]}")

    print("\n" + "=" * 70)
    print("4) 形态识别 BTC 1h")
    print("=" * 70)
    pat = P.detect(bars)
    print(f"  K线形态 {len(pat['candles'])} 个, 结构形态 {len(pat['chart'])} 个, bias={pat['bias']}")
    for p in pat["candles"][-5:]:
        print(f"    idx={p['idx']:<5} {p['label']:<8} dir={p['dir']:+d} conf={p['conf']:.2f}  {p['note']}")
    for p in pat["chart"]:
        print(f"    {p['label']:<16} dir={p['dir']:+d} conf={p['conf']:.2f}  {p['note']}")

    print("\n" + "=" * 70)
    print("5) 单周期研判 BTC 1h")
    print("=" * 70)
    t0 = time.time()
    a = analysis.analyze("BTC", "1h")
    print(f"  用时 {time.time()-t0:.0f} ms  score={a['score']}  verdict={a['verdict_label']}")
    for s in a["signals"]:
        arrow = {1: "多", -1: "空", 0: "—"}[s["dir"]]
        print(f"    {arrow} w={s['w']:<4} {s['label']:<18} {str(s['value']):<12} {s['note']}")
    print(f"  支撑: {[x['price'] for x in a['levels']['support'][:3]]}")
    print(f"  阻力: {[x['price'] for x in a['levels']['resistance'][:3]]}")
    print(f"  VP:   {a['levels']['volprofile']}")

    print("\n" + "=" * 70)
    print("6) 多周期共振 BTC")
    print("=" * 70)
    t0 = time.time()
    m = analysis.mtf("BTC")
    print(f"  用时 {time.time()-t0:.0f} ms")
    print(f"  加权分={m['weighted_score']}  大周期分={m['big_score']}  "
          f"结论={m['consensus']}  同向周期={m['aligned']}  反向={m['conflict']}")
    print(f"  {m['summary']}")
    for r in m["rows"]:
        if "error" in r:
            print(f"    {r['tf']:<4} ERROR {r['error'][:60]}")
            continue
        bar = "█" * int(abs(r["score"]) / 10)
        sign = "+" if r["score"] >= 0 else "-"
        print(f"    {r['tf']:<4} {r['score']:>6.1f} {sign}{bar:<10} {r['verdict_label']:<4} "
              f"价={r['price']}")

    print("\n" + "=" * 70)
    print("7) 缓存命中")
    print("=" * 70)
    t0 = time.time()
    feed.klines("BTC", "1h")
    print(f"  二次读取用时 {(time.time()-t0)*1000:.1f} ms")
    print(f"  {feed.cache_info()}")

    print("\n发现：", "全部通过" if ok else "存在失败项")


if __name__ == "__main__":
    main()
