"""PnL 引擎（承 R1）：全系统**唯一**的 PnL 公式。

根因（R1）：PnL 一旦有第二份实现 = 两个真相 —— 搜索与审计各按一份算，
"训练时钻的空子"在审计时会再次被钻，你永远发现不了。所以只在这里算。

四行，每行都有坑：
  ① `tanh(factor)` 封顶 ±1 —— 不封 → 可能开 100 倍仓；
  ② `|pos| < 阈值 → 0` —— 回测/实盘共用同一阈值（避免口径漂移）；
  ③ 换手用**上一根**仓位算 —— 用当前仓位 → 未来函数；
  ④ 换手成本必须算 —— 只算持仓成本 → 高频策略虚高。
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import backtest_config


def positions_from_factors(factors: Sequence[float]) -> list[float]:
    """因子 → 仓位。

    策略只产出**因子**，仓位由这里统一换算 —— 保证训练/回测/实盘走同一条路径
    （承 E5：禁 train-serve skew）。
    """
    out: list[float] = []
    for factor in factors:
        position = math.tanh(factor) * backtest_config.POSITION_CAP
        if abs(position) < backtest_config.MIN_TRADE_EXPOSURE:
            position = 0.0
        out.append(position)
    return out


def per_bar_pnl(positions: Sequence[float], target_returns: Sequence[float],
                cost_rate: float = backtest_config.COST_RATE) -> list[float]:
    """逐 bar PnL：`pos * target_ret - |Δpos| * cost`（承 R1）。

    ③ 换手用**上一根**仓位算（首根之前视为空仓）；
    ④ 换手成本必须算。
    """
    n = len(positions)
    if len(target_returns) != n:
        raise ValueError(
            f"positions({n}) 与 target_returns({len(target_returns)}) 长度不等"
        )
    out = [0.0] * n
    previous_position = 0.0                       # ③ 首根之前视为空仓
    for t in range(n):
        turnover = abs(positions[t] - previous_position)
        out[t] = positions[t] * target_returns[t] - turnover * cost_rate
        previous_position = positions[t]
    return out


def turnover_per_bar(positions: Sequence[float]) -> list[float]:
    """逐 bar 换手 `|Δpos|`（供审计/统计）。与 `per_bar_pnl` 用同一套换手口径。"""
    out = [0.0] * len(positions)
    previous_position = 0.0
    for t in range(len(positions)):
        out[t] = abs(positions[t] - previous_position)
        previous_position = positions[t]
    return out
