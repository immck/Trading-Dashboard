"""静态检查：app.js 里 $("id") 引用的元素是否都存在于 index.html。"""
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
js = (root / "web" / "app.js").read_text(encoding="utf-8")
html = (root / "web" / "index.html").read_text(encoding="utf-8")

ids_used = set(re.findall(r'\$\("([A-Za-z0-9_\-]+)"\)', js))
ids_defined = set(re.findall(r'id="([A-Za-z0-9_\-]+)"', html))

# 这些是运行时动态生成的
dynamic = {  # 运行时由 innerHTML 生成，静态检查看不到
    "toolTitle", "toolBody", "toolModal", "toolClose",
    "calcEntry", "calcLev", "calcMmr", "calcResult", "calcSide",
    "wiPrice", "wiResult", "wiSlider", "closeNewsInfo",
    "cvdSpark", "oiSpark", "qCountdown",
}
missing = sorted(ids_used - ids_defined - dynamic)
print("JS 引用 id:", len(ids_used), "| HTML 定义:", len(ids_defined))
if missing:
    print("缺失（JS 会拿到 null）：")
    for m in missing:
        print("  -", m)
    sys.exit(1)
print("全部存在")
unused = sorted(ids_defined - ids_used)
print("HTML 里未被 JS 引用的 id:", len(unused))
