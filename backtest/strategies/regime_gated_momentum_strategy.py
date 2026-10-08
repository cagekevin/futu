"""示例策略：**市场环境门控**的动量 —— 演示"市场环境适不适合买卖"怎么进回测。

关键区分（承 01 §0.2 的判据：**关于市场 vs 关于你**）：
  - "环境适不适合买卖"是一个**判断**（关于你）→ 属**策略**，不写死进框架；
  - 但判断的**原料**（波动率 / GEX / RPS …）是**客观派生**（关于市场）→ 由数据层给。

这里用**已实现波动率**当环境（纯价格、因果，无需额外数据项）：
  - 波动率过低（行情无方向）→ 因子输出 0 → 空仓 → "不买卖"；
  - 否则 → 正常动量。

这个"环境过滤"到底有没有用，交给回测的硬判定 + placebo 对照（承 V10）回答，
而不是靠感觉。
"""
from __future__ import annotations

import math

import strategy_contract


class RegimeGatedMomentumStrategy:
    """先判"环境适不适合买卖"，再决定要不要出信号。"""

    name = "regime_gated_momentum"

    def __init__(self, lookback: int = 5, vol_window: int = 20,
                 vol_floor: float = 0.0005, scale: float = 20.0) -> None:
        if lookback < 1 or vol_window < 2:
            raise ValueError("lookback 至少 1；vol_window 至少 2")
        self.lookback = lookback
        self.vol_window = vol_window
        self.vol_floor = vol_floor
        self.scale = scale

    def compute_factors(self, facts: strategy_contract.MarketFacts) -> list[float]:
        closes = facts.close
        n = len(closes)
        factors = [0.0] * n
        start = max(self.lookback, self.vol_window)
        for t in range(start, n):
            volatility = self._realized_volatility(closes, t)
            if volatility is None or volatility < self.vol_floor:
                factors[t] = 0.0                 # 环境不利 → 不买卖
                continue
            base = closes[t - self.lookback]
            if base > 0:
                factors[t] = (closes[t] / base - 1.0) * self.scale
        return factors

    def _realized_volatility(self, closes: list[float], t: int) -> float | None:
        """t 时刻的已实现波动率（**只用 ≤ t 的收益**，因果）。"""
        returns: list[float] = []
        for k in range(t - self.vol_window + 1, t + 1):
            prev, cur = closes[k - 1], closes[k]
            if prev > 0 and cur > 0:
                returns.append(math.log(cur / prev))
        if len(returns) < 2:
            return None
        mean = sum(returns) / len(returns)
        return math.sqrt(sum((r - mean) ** 2 for r in returns) / len(returns))


strategy_contract.register_strategy(RegimeGatedMomentumStrategy())
