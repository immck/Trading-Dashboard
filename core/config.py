"""全局配置。改这里就能调整看板行为，不用动代码。"""
from __future__ import annotations

import os

# ---------------------------------------------------------------- 服务
HOST = "127.0.0.1"
# 注意：Windows 的 Hyper-V 会保留若干端口段（可用
#   netsh interface ipv4 show excludedportrange protocol=tcp
# 查看），被保留的端口 bind 会报 WinError 10013。改端口时先确认不在保留段里。
PORT = 9101
APP_NAME = "Trading Dashboard"

# ---------------------------------------------------------------- 网络
# 直连失败时自动走本地代理（国内环境）。留空表示只用直连。
PROXY = os.environ.get("TD_PROXY", "http://127.0.0.1:7897")
# 直连探测超时（秒）。探测失败即切换到代理。
DIRECT_PROBE_TIMEOUT = 1.5
REQUEST_TIMEOUT = 15
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- 数据源
# futures = U 本位永续（默认，贴合实际交易）; spot = 现货
SOURCE = "futures"
BINANCE_FUT = "https://fapi.binance.com"
BINANCE_SPOT = "https://api.binance.com"

# ---------------------------------------------------------------- 标的
SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
QUOTE = "USDT"

# ---------------------------------------------------------------- 美股
# 用 US: 前缀可以强制按美股解析；不写前缀时，命中下面列表的也会按美股处理
US_PREFIX = "US:"
US_SYMBOLS = [
    "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "ORCL", "CRM", "ADBE", "QCOM", "MU", "INTC", "PLTR", "COIN", "MSTR", "HOOD",
    "JPM", "BAC", "GS", "V", "MA", "BRK-B", "UNH", "XOM", "CVX", "WMT",
    "COST", "PG", "KO", "PEP", "DIS", "BA", "CAT", "GE", "F", "GM",
    "UBER", "ABNB", "SHOP", "SQ", "SOFI", "RBLX", "SNOW", "SMCI", "ARM", "TSM",
]
# 指数（Yahoo 代码）
US_INDICES = {
    "^GSPC": "标普 500",
    "^IXIC": "纳斯达克",
    "^DJI": "道琼斯",
    "^VIX": "VIX 恐慌指数",
}
US_TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d", "1w"]

# 周期：周期名 -> 秒数（用于缓存 TTL 与共振加权）
TIMEFRAMES = {
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}
DEFAULT_TF = "1h"

# 多周期共振权重：大周期说话更算数
TF_WEIGHT = {
    "15m": 0.8,
    "30m": 1.0,
    "1h": 1.3,
    "4h": 1.6,
    "1d": 2.0,
    "1w": 2.2,
}

# 每个周期拉多少根 K 线（>=250 才能算 MA200）
BARS = 600
# 单次最多可取（合约 1500，现货 1000；feed 会按数据源自动收敛）
MAX_BARS = 1500

# ---------------------------------------------------------------- 缓存
# 缓存存活时间 = max(MIN_TTL, 周期秒数 / TTL_DIVISOR)
MIN_TTL = 8
TTL_DIVISOR = 8
# 落盘快照：断网时仍能看图
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DISK_CACHE = True

# ---------------------------------------------------------------- 指标参数
MA_PERIODS = (5, 10, 20, 60, 120, 200)
EMA_PERIODS = (12, 21, 55, 144)
MACD = (12, 26, 9)
RSI_PERIOD = 14
BOLL = (20, 2.0)
KDJ = (9, 3, 3)
ADX_PERIOD = 14
ATR_PERIOD = 14
MFI_PERIOD = 14
CCI_PERIOD = 20
WR_PERIOD = 14
CMF_PERIOD = 20
AROON_PERIOD = 25
SUPERTREND = (10, 3.0)
STOCHRSI = (14, 14, 3, 3)
VWAP_PERIOD = 50
VOLPROFILE_BINS = 40
SR_LOOKBACK = 120
SR_CLUSTER_PCT = 0.25

# 指标输出的 JSON 最多带多少根序列（前端画图够用即可，避免体积爆炸）
SERIES_OUT = 320

# ---------------------------------------------------------------- AI（可选）
# 不配置也能正常使用全部功能；配上 Key 后「研判」弹窗里会多出「AI 解读」。
# 任何 OpenAI 兼容接口都可以：DeepSeek / 智谱 GLM / Moonshot / 通义 / OpenAI / Ollama ...
# 优先读环境变量，其次读下面的默认值。
#
#   set TD_AI_KEY=sk-xxxx
#   set TD_AI_BASE=https://api.deepseek.com/v1
#   set TD_AI_MODEL=deepseek-chat
#
AI_ENABLED = True
AI_BASE_URL = os.environ.get("TD_AI_BASE", "https://api.deepseek.com/v1")
AI_API_KEY = os.environ.get("TD_AI_KEY", "")
AI_MODEL = os.environ.get("TD_AI_MODEL", "deepseek-chat")
AI_TEMPERATURE = 0.3
AI_MAX_TOKENS = 1400
AI_TIMEOUT = 120
# 是否把完整报告 JSON 喂给模型（关掉则只给精简摘要，省 token）
AI_FULL_CONTEXT = True
