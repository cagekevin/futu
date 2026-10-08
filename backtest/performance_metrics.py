"""性能指标：年化因子（承 B2）+ 风险调整（承 R2/R3）。

根因（每条指标背后的翻车形态，不修就会"指标骗你"）：
  - **B2 年化因子自动估计**：写死 `6240`（外汇 H1）→ 用日线数据时 Sharpe 被放大 26×。
  - **R2 Sortino 分母带地板**：策略只在上涨时交易 → 下行 std → 0 → `mean/~0` = 天文数字。
  - **R3 年化 = mean × ppy**（**不再除样本长度**）：多除一次 T → 年化被低估 T 倍。

为什么"年化因子"与"风险指标"放**同一个**模块（判过、决定**不拆**）：两者都是"把 PnL
折成一个数"，且审计**一次遍历同时要**它们（年化 + Sharpe/Sortino/MDD）。拆开只多一个
文件 + 一条 import，功能一点不变 ⇒ 复杂度**上升**，故不拆。
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import backtest_config

SECONDS_PER_YEAR = 365.25 * 86400.0
_EPS = 1e-12

# Sortino 的硬上限：任何超过它的值都不可信（撞上限本身是一道防线，见 sortino_ratio）。
SORTINO_CLAMP = 20.0

# ppy 的合理区间（每年 bar 数）。越界 → **报错**，不静默截断。
# 根因：时间戳单位错（秒/毫秒/秒÷1000，承 D7）会让 ppy 差 1000 倍；
# 静默 clamp 会把"单位错"伪装成"正常值"，比报错危险得多（承 P1/P6）。
MIN_PERIODS_PER_YEAR = 10
MAX_PERIODS_PER_YEAR = 600_000


# ── 年化因子（承 B2）────────────────────────────────────────────────────

def periods_per_year(times: Sequence[int]) -> int:
    """从时间戳序列估计「每年 bar 数」（承 B2）。

    不写死周期 —— H1 / D1 / 15min 自动适配。
    估计值越界（[MIN, MAX] 之外）→ **显式报错**，既不静默回退默认值，
    也不静默截断（两者都会掩盖"时间戳单位错"这类静默故障，承 P1/P6）。
    """
    if len(times) < 2:
        raise ValueError("periods_per_year：时间戳少于 2 个，无法估计（承 P1）")
    span_seconds = float(times[-1] - times[0])
    if span_seconds <= 0:
        raise ValueError(f"periods_per_year：时间跨度非正（{span_seconds}）（承 P1）")
    span_years = span_seconds / SECONDS_PER_YEAR
    estimate = len(times) / span_years
    if not (MIN_PERIODS_PER_YEAR <= estimate <= MAX_PERIODS_PER_YEAR):
        raise ValueError(
            f"periods_per_year 估计值 {estimate:.1f} 越界 "
            f"[{MIN_PERIODS_PER_YEAR}, {MAX_PERIODS_PER_YEAR}] —— "
            f"时间戳单位/周期可疑（承 D7/P1：不静默截断）"
        )
    return int(round(estimate))


# ── 风险调整（承 R2/R3）─────────────────────────────────────────────────

def annualized_return(pnl: Sequence[float], periods_per_year_: int) -> float:
    """年化 = 单 bar 平均收益 × 每年 bar 数（承 R3：**不再除样本长度**）。"""
    return _mean(pnl) * periods_per_year_


def sharpe_ratio(pnl: Sequence[float], periods_per_year_: int) -> float:
    std = _std(pnl)
    if std < _EPS:
        return 0.0
    return _mean(pnl) / std * math.sqrt(periods_per_year_)


def sortino_ratio(pnl: Sequence[float], periods_per_year_: int) -> float:
    """下行标准差**带地板**（承 R2），并**硬性截断在 [-SORTINO_CLAMP, +SORTINO_CLAMP]**。

    地板 = 全序列 std 的 20%。没有它，稀疏 PnL 会靠极小分母刷出天文数字；
    上限本身就是一道防线：**任何撞到 ±SORTINO_CLAMP 的 Sortino 都不可信**
    （它不是"真实值"，是被截断的信号）。
    """
    full_std = _std(pnl)
    losses = [x for x in pnl if x < 0]
    downside_std = _std(losses) if len(losses) > 1 else 0.0
    downside_std = max(downside_std, max(full_std * 0.2, _EPS))
    raw = _mean(pnl) / downside_std * math.sqrt(periods_per_year_)
    return max(-SORTINO_CLAMP, min(SORTINO_CLAMP, raw))


def max_drawdown(pnl: Sequence[float]) -> float:
    """最大回撤（以累计 PnL 计）。"""
    cumulative = peak = drawdown = 0.0
    for x in pnl:
        cumulative += x
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    return drawdown


def long_short_ratio(positions: Sequence[float]) -> tuple[float, float]:
    """(多占比, 空占比) —— 承 V3：单边 > 85% → 疑似 beta。"""
    if not positions:
        return 0.0, 0.0
    threshold = backtest_config.MIN_TRADE_EXPOSURE
    long_ratio = sum(1 for p in positions if p > threshold) / len(positions)
    short_ratio = sum(1 for p in positions if p < -threshold) / len(positions)
    return long_ratio, short_ratio


def count_trades(positions: Sequence[float]) -> int:
    """交易笔数（开仓 / 方向变化算一笔）。"""
    n = 0
    previous = 0.0
    for p in positions:
        if p != 0.0 and (previous == 0.0 or (p > 0) != (previous > 0)):
            n += 1
        previous = p
    return n


# ── 内部 ────────────────────────────────────────────────────────────────

def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))
