"""拉任意标的的完整读数，用于人工判读。

用法：python tools/read.py ETH
      python tools/read.py 1000PEPE
"""
from __future__ import annotations

import sys
import urllib.request

BASE = "http://127.0.0.1:8899"


def get(path, timeout=240):
    import json
    return json.load(urllib.request.urlopen(BASE + path, timeout=timeout))


def f(x, d=2):
    try:
        return f"{float(x):,.{d}f}"
    except (TypeError, ValueError):
        return "--"


def pct(x, d=2):
    try:
        return f"{float(x):+.{d}f}%"
    except (TypeError, ValueError):
        return "--"


def main():
    sym = (sys.argv[1] if len(sys.argv) > 1 else "BTC").upper()
    s = get(f"/api/snapshot?symbol={sym}")
    lq = get(f"/api/liqmap?symbol={sym}&range=12")
    m = get(f"/api/mtf?symbol={sym}")

    print("=" * 74)
    print(f"{sym} 行情快照")
    print("=" * 74)
    print(f"  价格      {f(s['price'], 6 if (s['price'] or 1) < 1 else 2)}   "
          f"涨跌 {pct(s['change_pct'])}")
    print(f"  24h 高/低 {f(s['high'])} / {f(s['low'])}")
    print(f"  OI        {f(s['oi_usd']/1e9, 3)}B   24h 变化 {pct(s['oi_change_pct'])}")
    print(f"  资金费率  {(s['funding'] or 0)*100:.4f}%")
    print(f"  24h 量    {f(s['vol_usd']/1e9, 2)}B   量比 {s['vol_ratio']}")
    print(f"  盘口失衡  {s['imbalance']['ratio']}  (买 {f(s['imbalance']['bid_usd']/1e6,1)}M / "
          f"卖 {f(s['imbalance']['ask_usd']/1e6,1)}M)")
    p = s["positioning"]
    print(f"  大户持仓比 {p.get('top_position_ratio')}  (多头占 {p.get('top_position_long_pct')}%)")
    print(f"  大户账户比 {p.get('top_account_ratio')}  (多头占 {p.get('top_account_long_pct')}%)")
    print(f"  全网多空比 {p.get('global_ratio')}   主动买卖比 {p.get('taker_ratio')}")
    print(f"  恐贪指数   {s['fear_greed'].get('value')} {s['fear_greed'].get('label')}")
    of = s["orderflow"]
    print(f"  CVD 累计   {f(of.get('cvd')/1e6,1)}M   1h 主动买 {f(of.get('buy_1h')/1e6,1)}M / "
          f"卖 {f(of.get('sell_1h')/1e6,1)}M")
    w = s["walls"]
    print(f"  盘口深度   买 {f(w['bid_total']/1e6,1)}M / 卖 {f(w['ask_total']/1e6,1)}M "
          f"(±{w['span_pct']/2:.3f}%)")
    print("  分所行情：")
    for v in s["venues"]:
        print(f"     {v['name']:<9} {f(v['last']):<14} {pct(v.get('change')):<9} "
              f"量 {f((v.get('vol_usd') or 0)/1e9,2)}B")

    print("\n" + "=" * 74)
    print("多空损（预估清算）")
    print("=" * 74)
    if lq.get("ok"):
        for name, z in (("最近多头区", lq["nearest_long"]), ("最近空头区", lq["nearest_short"])):
            print(f"  {name} {f(z['price'])}  距现价 {pct(z['dist_pct'])}  "
                  f"多损 {f((z['long_usd'] or 0)/1e6,1)}M / 空损 {f((z['short_usd'] or 0)/1e6,1)}M")
        print(f"  3% 内累计   多损 {f(lq['near3_long_usd']/1e9,2)}B / "
              f"空损 {f(lq['near3_short_usd']/1e6,1)}M")
        print("  最大多头区：" + ", ".join(
            f"{f(z['price'])}({f(z['long_usd']/1e6,0)}M,{pct(z['dist_pct'],1)})"
            for z in lq["top_long"][:4]))
        print("  最大空头区：" + ", ".join(
            f"{f(z['price'])}({f(z['short_usd']/1e6,0)}M,{pct(z['dist_pct'],1)})"
            for z in lq["top_short"][:4]))
    else:
        print("  数据不足")

    print("\n" + "=" * 74)
    print("多周期研判")
    print("=" * 74)
    print(f"  加权 {m['weighted_score']:+.1f}  大周期 {m['big_score']:+.1f}  "
          f"{m['verdict_label']} · {m['consensus']}")
    print(f"  {m['summary']}")
    print()
    for r in m["rows"]:
        if "error" in r:
            print(f"  {r['tf']:<4} 无数据")
            continue
        top = sorted(r["signals"], key=lambda x: -abs(x["w"] * x["dir"]))[:6]
        tops = " ".join(f"{'多' if x['dir']>0 else '空' if x['dir']<0 else '·'}{x['label']}"
                        for x in top if x["dir"] != 0)
        pat = r["patterns"]["latest"]["label"] if r["patterns"].get("latest") else ""
        print(f"  {r['tf']:<4} {r['score']:>6.1f} {r['verdict_label']:<3} | {tops}")
        if pat:
            print(f"       形态: {pat}")

    print("\n" + "=" * 74)
    print("关键价位（按周期）")
    print("=" * 74)
    for tf in ("1h", "4h", "1d"):
        c = get(f"/api/chart?symbol={sym}&tf={tf}&limit=320")
        sup = [x["price"] for x in c["sr"]["support"][:3]]
        res = [x["price"] for x in c["sr"]["resistance"][:3]]
        vp = c["volprofile"]
        fib = c["fib"]["levels"]
        piv = c.get("pivot", {})
        print(f"\n  [{tf}]  结构 {c['structure']['label']}   现价 {f(c['snapshot']['close'])}")
        print(f"    支撑 {[f(x) for x in sup]}")
        print(f"    阻力 {[f(x) for x in res]}")
        print(f"    VP   POC {f(vp['poc'])} VAH {f(vp['vah'])} VAL {f(vp['val'])}")
        print(f"    Fib  0.382 {f(fib['0.382'])}  0.5 {f(fib['0.5'])}  0.618 {f(fib['0.618'])}")
        if piv:
            print(f"    枢轴 P {f(piv['P'])} R1 {f(piv['R1'])} S1 {f(piv['S1'])}")
        sn = c["snapshot"]
        print(f"    指标 ADX {f(sn['adx'],1)} RSI {f(sn['rsi'],1)} ATR {f(sn['atr'])} "
              f"云顶 {f(sn['ichi_cloud_top'])} 云底 {f(sn['ichi_cloud_bot'])}")
        print(f"        SAR {f(sn['psar'])} KDJ-K {f(sn['kdj_k'],1)} "
              f"ST {'多' if sn['st_dir']>0 else '空'} {f(sn['st_value'])} "
              f"挤压{'中' if sn['squeeze_on'] else '释放'}")


if __name__ == "__main__":
    main()
