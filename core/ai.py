"""AI 解读层（可选）：把结构化研判交给大模型，生成叙述 + 回答追问。

设计原则：
  · 只喂「已经算好的数据」，不让模型自己去取数或推算；
  · 提示词明确禁止编造数字、禁止给点位；
  · 输出后做一次数值核对，把模型「自己造的数」标出来；
  · 没配 Key 时整体降级，不影响任何现有功能。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from . import config as C
from . import feed

# 常见供应商的预设（只需改 TD_AI_BASE / TD_AI_MODEL）
PRESETS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    "moonshot": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    "dashscope": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "ollama": ("http://127.0.0.1:11434/v1", "qwen2.5:7b"),
    "siliconflow": ("https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct"),
}

SYSTEM_PROMPT = """你是一名加密货币技术分析助手，负责解读一份由量化规则生成的研判报告。

硬性约束（违反即视为错误）：
1. 只能使用用户消息中 JSON 提供的数字与结论，禁止引入任何外部数据、新闻或你的记忆。
2. 禁止编造数字。需要引用数值时，必须与 JSON 中完全一致。
3. 禁止给出开仓价、止损、止盈、仓位建议，禁止预测具体价格。
4. 数据不足以支撑某个论断时，直接说「数据不足」，不要猜。
5. 如果 JSON 中的结论互相矛盾，要指出矛盾而不是强行统一。

输出要求：
- 用中文，结构清晰，段落短。
- 先给 2~3 句话的核心判断。
- 再挑出数据中最值得注意的 3~5 个点，每点都要引用具体数值。
- 最后指出这份研判的局限或不可靠之处。
- 总长度控制在 500 字以内。"""


# ---------------------------------------------------------------- 配置
def status() -> dict:
    key = (C.AI_API_KEY or "").strip()
    provider = "custom"
    for name, (base, model) in PRESETS.items():
        if C.AI_BASE_URL.rstrip("/").startswith(base.rstrip("/")) and C.AI_MODEL == model:
            provider = name
            break
    for name, (base, _model) in PRESETS.items():
        if C.AI_BASE_URL.rstrip("/").startswith(base.rstrip("/")):
            provider = name
            break
    return {
        "enabled": bool(C.AI_ENABLED),
        "configured": bool(key),
        "provider": provider,
        "base_url": C.AI_BASE_URL,
        "model": C.AI_MODEL,
        "has_key": bool(key),
        "presets": {k: {"base_url": v[0], "model": v[1]} for k, v in PRESETS.items()},
        "hint": ("已配置" if key else
                 "未配置 API Key。设置环境变量 TD_AI_KEY（可选 TD_AI_BASE / TD_AI_MODEL）后重启服务即可。"),
    }


# ---------------------------------------------------------------- 上下文
def _compact(rep: dict) -> dict:
    """把研判报告压成适合喂模型的摘要，保留全部可引用数字。"""
    rows = []
    for r in (rep.get("mtf") or {}).get("rows", []):
        if "error" in r:
            continue
        rows.append({
            "tf": r["tf"], "score": r["score"], "verdict": r["verdict"],
            "top": [("多" if t["dir"] > 0 else "空" if t["dir"] < 0 else "·") + t["label"]
                    for t in r.get("top", []) if t["dir"] != 0],
        })
    levels = {}
    for tf, c in (rep.get("levels") or {}).items():
        levels[tf] = {
            "structure": c.get("structure"),
            "support": c.get("support"), "resistance": c.get("resistance"),
            "poc": (c.get("vp") or {}).get("poc"),
            "vah": (c.get("vp") or {}).get("vah"),
            "val": (c.get("vp") or {}).get("val"),
            "pivot": c.get("pivot"),
            "adx": c.get("adx"), "rsi": c.get("rsi"), "atr": c.get("atr"),
            "cloud_top": c.get("cloud_top"), "cloud_bot": c.get("cloud_bot"),
        }
    liq = rep.get("liq") or {}
    return {
        "symbol": rep.get("symbol"),
        "price": rep.get("price"),
        "verdict": rep.get("verdict"),
        "headline": rep.get("headline"),
        "cards": rep.get("cards"),
        "facts": rep.get("facts"),
        "mtf_rows": rows,
        "levels": levels,
        "liq": {
            "nearest_long": liq.get("nearest_long"),
            "nearest_short": liq.get("nearest_short"),
            "near3_long": liq.get("near3_long"),
            "near3_short": liq.get("near3_short"),
            "top_long": liq.get("top_long"),
            "top_short": liq.get("top_short"),
        } if liq.get("ok") else None,
        "findings": [{"title": f.get("title"), "body": f.get("body")}
                     for f in (rep.get("findings") or [])],
        "watch": rep.get("watch"),
        "reliability": rep.get("reliability"),
    }


def build_messages(rep: dict, question: str = "") -> list[dict]:
    ctx = _compact(rep)
    body = json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))
    q = (question or "").strip()
    if q:
        user = (f"下面是 {ctx.get('symbol')} 的研判数据：\n\n```json\n{body}\n```\n\n"
                f"请基于以上数据回答这个问题：{q}\n"
                f"（如果没有配置 Key 你不需要回答，这只是占位）")
    else:
        user = (f"下面是 {ctx.get('symbol')} 的研判数据：\n\n```json\n{body}\n```\n\n"
                f"请解读这份研判。")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user}]


# ---------------------------------------------------------------- 数值核对
# 支持负号与 K/M/B/% 单位，避免把 "-46" 当成 46、把 "138.9M" 当成 138.9
_NUM_RE = re.compile(
    r"(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)\s*(K|M|B|%)?")


def _flatten_numbers(obj, out: set[float]):
    if isinstance(obj, dict):
        for v in obj.values():
            _flatten_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _flatten_numbers(v, out)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in _NUM_RE.finditer(obj):
            try:
                out.add(float(m.group(1).replace(",", "")))
            except ValueError:
                pass


def verify(text: str, rep: dict, max_report: int = 8) -> dict:
    """把模型输出里的数字和原始数据比对，找出「数据里没有的数」。

    这是提示性的软校验：允许四舍五入、正负号、K/M/B 单位换算，
    只把差得比较远、且量级像价格/金额的数字标出来。
    """
    known: set[float] = set()
    _flatten_numbers(_compact(rep), known)

    def close_to_known(x: float) -> bool:
        for k in known:
            if k == 0:
                if abs(x) < 1e-9:
                    return True
                continue
            tol = max(abs(k) * 0.012, 0.01)
            if abs(x - k) <= tol or abs(abs(x) - abs(k)) <= tol:
                return True
        return False

    unknown = []
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        unit = m.group(2) or ""
        try:
            v = float(raw)
        except ValueError:
            continue
        # 过滤年份与小整数
        if 2000 <= abs(v) <= 2100 and "." not in raw and not unit:
            continue
        if abs(v) < 10 and not unit:
            continue

        # 单位换算后逐一尝试（同时试原值）
        cands = {v}
        if unit == "K":
            cands |= {v * 1e3}
        elif unit == "M":
            cands |= {v * 1e6, v * 1e3}
        elif unit == "B":
            cands |= {v * 1e9, v * 1e6}
        elif unit == "%":
            cands |= {v / 100.0}

        if not any(close_to_known(c) for c in cands):
            unknown.append(raw + unit)
        if len(unknown) >= max_report:
            break
    return {"unknown": unknown, "checked": len(known)}


# ---------------------------------------------------------------- 调用
class AIError(RuntimeError):
    pass


def _endpoint() -> str:
    base = (C.AI_BASE_URL or "").rstrip("/")
    if not base:
        raise AIError("未配置 TD_AI_BASE")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def stream_chat(messages: list[dict]):
    """以生成器形式产出 SSE 事件：("text", 片段) / ("done", meta)。

    走与行情相同的代理通道，兼容任何 OpenAI 风格的 /chat/completions。
    """
    key = (C.AI_API_KEY or "").strip()
    if not key:
        raise AIError("未配置 API Key（环境变量 TD_AI_KEY）")

    payload = {
        "model": C.AI_MODEL,
        "messages": messages,
        "temperature": C.AI_TEMPERATURE,
        "max_tokens": C.AI_MAX_TOKENS,
        "stream": True,
    }
    req = urllib.request.Request(
        _endpoint(),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {key}",
            "User-Agent": C.UA,
        },
        method="POST",
    )
    try:
        resp = feed.opener().open(req, timeout=C.AI_TIMEOUT)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        raise AIError(f"上游返回 {e.code}: {body or e.reason}") from e
    except Exception as e:  # noqa: BLE001
        raise AIError(f"连接失败: {e}") from e

    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if "error" in obj:
                raise AIError(str(obj["error"])[:300])
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                yield ("text", piece)
    yield ("done", {"model": C.AI_MODEL})
