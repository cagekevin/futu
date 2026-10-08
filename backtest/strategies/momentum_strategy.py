"""示例策略：过去 `lookback` 根动量（**因果**）。

它同时是**模板** —— 照它写你自己的策略：
  1. 新文件放 `strategies/`；
  2. 定义 `name` + `compute_factors(self, facts)`；
  3. 模块末尾 `strategy_contract.register_strategy(YourStrategy())`；
  4. 在 `strategies/__init__.py` 加一行 import。
框架其余部分**一行不改**。
"""
from __future__ import annotations

import strategy_contract


class MomentumStrategy:
    """过去 `lookback` 根收益 × `scale` 作为因子（只用 ≤ t 的收盘价）。"""

    name = "momentum"

    def __init__(self, lookback: int = 5, scale: float = 20.0) -> None:
        if lookback < 1:
            raise ValueError("lookback 至少 1")
        self.lookback = lookback
        self.scale = scale

    def compute_factors(self, facts: strategy_contract.MarketFacts) -> list[float]:
        closes = facts.close
        n = len(closes)
        factors = [0.0] * n
        for t in range(self.lookback, n):
            base = closes[t - self.lookback]
            if base > 0:
                factors[t] = (closes[t] / base - 1.0) * self.scale
        return factors


strategy_contract.register_strategy(MomentumStrategy())
