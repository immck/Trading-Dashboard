"""把 styles.css 里写死的颜色换成 CSS 变量，为明暗主题做准备。

策略：
  1. 先按显式表处理语义色（涨跌、强调、标签底色、文字层级）；
  2. 剩下的深色按亮度分桶映射到 --bg / --bg2 / --bg3 / --line；
  3. 4/8 位带透明度的颜色（阴影）保持不动。
"""
import io
import re

PATH = r"D:\M-web\Trading-Tool\Trading-Dashboard\web\styles.css"

EXPLICIT = {
    # 页面底色
    "#0d1117": "var(--bg)",
    "#0d1117e6": "var(--bg)",
    "#0d1117dd": "var(--bg)",
    "#101620": "var(--bg)",
    # 文字
    "#ffffff": "var(--on-solid)",
    "#fff": "var(--on-solid)",
    "#e6edf3": "var(--tx-strong)",
    "#f0f6fc": "var(--tx-strong)",
    "#dbe9ff": "var(--tx-strong)",
    "#e9f2ff": "var(--tx-strong)",
    "#c9d1d9": "var(--tx)",
    "#d1d4dc": "var(--tx)",
    "#8b949e": "var(--tx2)",
    "#6e7681": "var(--tx3)",
    "#9caac0": "var(--tx2)",
    "#9cb3cb": "var(--tx2)",
    "#9bb2ca": "var(--tx2)",
    "#b7c8da": "var(--tx2)",
    "#b7c5d5": "var(--tx2)",
    "#b5c6da": "var(--tx2)",
    "#c7d9ef": "var(--tx2)",
    "#8fb5df": "var(--tx2)",
    "#9eb2c9": "var(--tx2)",
    # 强调 / 涨跌
    "#4da6ff": "var(--accent)",
    "#4da6ff22": "var(--accent-bg)",
    "#69b6ff": "var(--accent)",
    "#6cb6ff": "var(--accent-hover)",
    "#8cc7ff": "var(--accent-soft)",
    "#55a9f5": "var(--accent)",
    "#1e6fff": "var(--accent)",
    "#0053ff": "var(--accent)",
    "#00b956": "var(--up)",
    "#3fb950": "var(--up)",
    "#ff4976": "var(--down)",
    "#ff0040": "var(--down)",
    "#ff2d55": "var(--down)",
    "#ff9500": "var(--orange)",
    "#ff8c1a": "var(--orange)",
    "#c480ff": "var(--purple)",
    "#a371f7": "var(--purple)",
    "#9584b6": "var(--purple)",
    # 状态标签底色
    "#0e2412": "var(--up-bg)",
    "#17501f": "var(--up-border)",
    "#2a0f16": "var(--down-bg)",
    "#5a1f2b": "var(--down-border)",
    "#2a2109": "var(--warn-bg)",
    "#5a4408": "var(--warn-border)",
    # 分隔/描边
    "#3b4250": "var(--line-2)",
    "#3d6ea8": "var(--accent-border)",
}

HEX_RE = re.compile(r"#([0-9a-fA-F]{6})\b")


def lum(hex6: str) -> float:
    r = int(hex6[0:2], 16)
    g = int(hex6[2:4], 16)
    b = int(hex6[4:6], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


def bucket(hex6: str) -> str | None:
    value = lum(hex6)
    if value < 12:
        return "var(--bg)"
    if value < 26:
        return "var(--bg2)"
    if value < 44:
        return "var(--bg3)"
    if value < 74:
        return "var(--line)"
    return None


def main() -> None:
    css = io.open(PATH, encoding="utf-8").read()
    replaced: dict[str, str] = {}

    def sub(match: re.Match) -> str:
        raw = match.group(1).lower()
        key = "#" + raw
        if key in EXPLICIT:
            replaced[key] = EXPLICIT[key]
            return EXPLICIT[key]
        mapped = bucket(raw)
        if mapped:
            replaced[key] = mapped
            return mapped
        return match.group(0)

    # 先处理 8 位带透明度里的深色（仅限显式表里有的）
    for key, val in EXPLICIT.items():
        if len(key) == 9:
            css = css.replace(key, val)

    css = HEX_RE.sub(sub, css)
    io.open(PATH, "w", encoding="utf-8").write(css)

    print("替换色值", len(replaced), "种")
    for k in sorted(replaced):
        print(f"  {k} -> {replaced[k]}")


if __name__ == "__main__":
    main()
