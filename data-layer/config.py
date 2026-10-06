"""trading-desk 全局配置。

只放"跨模块共享的常量 / 路径 / 参数"。不放业务逻辑，不读写数据。
"""
from __future__ import annotations

import os
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ── 时区 ──────────────────────────────────────────────────────────────────
# 交易日一律用**美东日期**（承 L1：不是本机日期）。
ET = ZoneInfo("America/New_York")
UTC = timezone.utc

# ── 路径 ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
# 库的物理落盘根目录：一天一个目录（承 §6.3）。
DATA_DIR = Path(os.getenv("TRADING_DESK_DATA_DIR", ROOT / "data"))

# ── 合约参数 ──────────────────────────────────────────────────────────────
# 期权合约乘数（SPX/NDX/SPY/QQQ 均为 100）。
CONTRACT_MULTIPLIER = 100

# ── 利率 ──────────────────────────────────────────────────────────────────
# 无风险利率 r，仅作**取数失败时的显式回退**（承 P1：回退必须显形）。
RISK_FREE_RATE_FALLBACK = 0.045
# SOFR 隔夜利率（NY Fed，公开接口，无 key）。
SOFR_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json"

# ── 指标参数（GEX / zero gamma）────────────────────────────────────────────
# zero gamma 搜索网格：围绕 spot ±zg_range，共 zg_steps 步。
ZG_RANGE = 0.08
ZG_STEPS = 161
# 到期时间下限（秒）：避免临收盘时 gamma 爆炸。
MIN_T_YEARS = 300.0 / (365.0 * 24 * 3600)
YEAR_SECONDS = 365.0 * 24 * 3600
