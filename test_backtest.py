"""回测自检：验证无未来函数 + 统计口径，并给出真实校准结论。"""
from __future__ import annotations

import sys
import time

import numpy as np

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from core import backtest, feed  # noqa: E402


def main():
    print("=" * 74)
    print("1) 无未来函数校验")
    print("=" * 74)
    bars = feed.klines("BTC", "1h", want=300)
    c = bars["close"]
    # 用「到 i 为止」的数据算打分，与「用全量数据算 i 处」必须完全一致
    from core import indicators as I, patterns as P
    from core.analysis import eval_at

    full_ind = I.compute_all(bars)
    inconsistencies = 0
    for i in (150, 200, 250):
        s = max(0, i - 129)
        win_full = {k: bars[k][s:i + 1] for k in ("open", "high", "low", "close", "volume")}
        pat = P.detect(win_full)
        _, sc_full = eval_at(bars, full_ind, pat, i)

        trunc = {k: bars[k][:i + 1] for k in ("open", "high", "low", "close", "volume")}
        t_ind = I.compute_all(trunc)
        t_pat = P.detect(trunc)
        _, sc_trunc = eval_at(trunc, t_ind, t_pat, i)
        same = abs(sc_full - sc_trunc) < 1e-9
        inconsistencies += 0 if same else 1
        print(f"  i={i:<4} 切片算={sc_full:+8.4f}  全量算={sc_trunc:+8.4f}  "
              f"{'一致' if same else '不一致 <<< 有未来函数'}")
    print(f"  → {'通过' if inconsistencies == 0 else f'{inconsistencies} 处不一致'}")

    print("\n" + "=" * 74)
    print("2) 回测 BTC 1h（未来 24 根 ≈ 1 天）")
    print("=" * 74)
    t0 = time.time()
    r = backtest.run("BTC", "1h", horizon=24, bars=1500)
    dt = time.time() - t0
    print(f"  耗时 {dt:.1f}s   样本 {r['samples']}   周期跨度 {r['bars']} 根")
    print(f"  IC(多周期) = {r['ic']['mtf']}    IC(单周期1h) = {r['ic']['single']}")
    print(f"  各周期 IC: {r['ic']['per_tf']}")
    print(f"  最强周期: {r['best_tf']}")

    print("\n  分桶（多周期共振分 → 未来 24 根收益）:")
    print(f"  {'分数区间':<16}{'样本':>6}{'均值收益':>11}{'方向胜率':>10}{'期望':>10}")
    for b in r["buckets"]:
        print(f"  {b['label']:<16}{b['n']:>6}"
              f"{(str(b['avg_ret'])+'%') if b['avg_ret'] is not None else '--':>11}"
              f"{(str(b['hit_rate'])+'%') if b['hit_rate'] is not None else '--':>10}"
              f"{(str(b['edge'])+'%') if b['edge'] is not None else '--':>10}")

    print("\n  对照：只看单周期 1h 评分的分桶:")
    for b in r["single_buckets"]:
        print(f"  {b['label']:<16}{b['n']:>6}"
              f"{(str(b['avg_ret'])+'%') if b['avg_ret'] is not None else '--':>11}"
              f"{(str(b['hit_rate'])+'%') if b['hit_rate'] is not None else '--':>10}"
              f"{(str(b['edge'])+'%') if b['edge'] is not None else '--':>10}")

    print(f"\n  强信号: 做多 {r['strong']['up_n']} 次 / 均值 {r['strong']['up_ret']}%   "
          f"做空 {r['strong']['dn_n']} 次 / 均值 {r['strong']['dn_ret']}%")
    print(f"\n  结论: {r['verdict']}")

    print("\n" + "=" * 74)
    print("3) 换个标的验证稳健性")
    print("=" * 74)
    for sym in ("ETH", "SOL"):
        try:
            t0 = time.time()
            x = backtest.run(sym, "1h", horizon=24, bars=1500)
            print(f"  {sym}: IC(多周期)={x['ic']['mtf']}  IC(1h)={x['ic']['single']}  "
                  f"样本={x['samples']}  用时 {time.time()-t0:.1f}s")
            print(f"       {x['verdict'][:90]}")
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: 失败 {e}")


if __name__ == "__main__":
    main()
