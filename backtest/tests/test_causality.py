"""因果性检验套件：**T2 污染未来（承 V9）+ T1 截断不变（承 TD-05-6）**。

## 为什么两件都要做（承 futu `pitfalls.md` 的实测结论）

| 测试 | 抓什么 |
| --- | --- |
| **T2** 污染未来 | 把 `t` 之后换成垃圾 → `[0..t]` 必须逐位不变 ⇒ 抓「窗口内偷看后面」 |
| **T1** 截断不变 | `fn(S)[:t+1]` 必须等于 `fn(S[:t+1])` ⇒ 抓「按**全序列**归一化」（`FULL_SERIES_NORM`） |

**两个都过，才算因果。** 只做 T2 会漏掉「除以全样本极值 / 均值」这一类：
它偷看的是**统计量**而不是某一根 bar，垃圾替换未必能稳定地把它抖出来，
而「截断后重算」是对它的**直接**检验。

## 为什么这两个测试都在这里（判过，收口到唯一实现）

T1 与 T2 是**同一件事的两面**（t 时刻的输出有没有用到 t 之后的数据），
放在同一个文件、共用同一套序列构造与「策略 → 仓位」管线 ——
分开写两份 = 两份真相（承 `Agent.md` §4 X1）。

★ T2 污染**整条 OHLCV**（不只是 close）：若只污染 close，策略改用 open 偷看未来
就测不出来（修 C7）。

## ⚠️ 为什么必须写成 `unittest.TestCase`（本轮修的缺陷）

原先本文件是「函数 + `__main__`」风格 —— `python tests/test_causality.py` 能跑，
但门禁口径 `python -m unittest discover -s tests` **扫不到它**
（discover 只收 `TestCase` 子类）。
⇒ 本仓的 V9 曾经是「**名义上的 auto**」：测试存在、门禁不执行。
本轮（TD-05-6）连同 T1 一起收口为 `TestCase`，两者才真正进闸。
"""
from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pnl_engine  # noqa: E402
import strategy_contract  # noqa: E402
import strategies  # noqa: E402,F401  触发策略注册

# T1 的截断点 / T2 的污染点（同一批坐标，两个测试用同一套口径）。
# ★ 承 V7 的**目的**（让偷看未来必然暴露）：**逐点**查，不抽样 ——
#   原先只查 80/150/260/320 四个点 ⇒ **"只在 t=200 泄漏"的实现能溜过去**。
#   代价只在**离线测试**里（O(n²)），生产零代价。
SERIES_LENGTH = 400
CHECK_POINTS = tuple(range(1, SERIES_LENGTH))


def price_series(n: int = SERIES_LENGTH, seed: int = 7) -> list[float]:
    """几何随机游走 —— **有趋势、有波动**，比单调 / 周期序列更能暴露泄漏。"""
    rng = random.Random(seed)
    out: list[float] = []
    price = 100.0
    for _ in range(n):
        price *= math.exp(rng.gauss(0.0, 0.01))
        out.append(round(price, 4))
    return out


def _facts_from_series(series: list[float]) -> strategy_contract.MarketFacts:
    """一条价格序列 → MarketFacts：OHLC 同源（这样污染整条序列即污染全部四价）。"""
    n = len(series)
    values = tuple(series)
    return strategy_contract.MarketFacts(
        times=tuple(i * 3600 for i in range(n)),
        open=values, high=values, low=values, close=values,
        volume=tuple(1.0 for _ in range(n)),
    )


def _positions_of(strategy: strategy_contract.Strategy,
                  series: list[float]) -> list[float]:
    factors = strategy.compute_factors(_facts_from_series(series))
    return pnl_engine.positions_from_factors(factors)


class TestCausality(unittest.TestCase):
    """对**所有已注册策略**验证因果性 —— 策略才是"你的决策"，最可能在这里偷看未来。

    ⚠️ **逐点**查（`CHECK_POINTS` = 每个 t），不抽样：抽样会让"只在某个 t 泄漏"的实现
    溜过去。这是 V7（物理隔离）在我们**向量化契约**下的等价达成方式 ——
    V7 的目的是"让偷看未来**必然暴露**"，而不是"必须切片"（切片要么 O(n²)、
    要么把契约改成流式，两条都让系统更复杂）。
    """

    def test_t2_污染未来后前缀逐位不变(self) -> None:
        """**T2**（承 V9）：把 t 之后的**整条 OHLCV** 换成随机垃圾 → 仓位 `[0..t]` 逐位不变。

        这是"因果性"的终极检验 —— 比"追加未来数据看变不变"更彻底：
        **任何偷看未来的实现，输出必然改变。**
        """
        rng = random.Random(11)
        series = price_series()
        for name in strategy_contract.available_strategies():
            strategy = strategy_contract.get_strategy(name)
            baseline = _positions_of(strategy, series)
            for t in CHECK_POINTS:
                polluted = series[: t + 1] + [
                    rng.uniform(20.0, 500.0) for _ in range(len(series) - t - 1)
                ]
                after = _positions_of(strategy, polluted)
                self.assertEqual(
                    after[: t + 1],
                    baseline[: t + 1],
                    f"{name} 在 t={t} 处污染未来后，[0..t] 的仓位变了 —— "
                    f"该策略偷看了未来（承 V9）",
                )

    def test_t1_截断不变性(self) -> None:
        """**T1**（承 TD-05-6）：`fn(S)[:t+1]` 必须等于 `fn(S[:t+1])`。

        抓 **`FULL_SERIES_NORM`** —— 例如 `close / max(close)`、`(x - mean(全序列)) / std(全序列)`
        这类「拿**全样本**统计量做归一化」的写法：在回测里它就是未来函数，
        而 T2 未必稳定地把它抖出来（垃圾替换可能改变不了归一化的方向性）。
        """
        series = price_series()
        for name in strategy_contract.available_strategies():
            strategy = strategy_contract.get_strategy(name)
            full = _positions_of(strategy, series)
            for cut in CHECK_POINTS:
                truncated = _positions_of(strategy, series[: cut + 1])
                self.assertEqual(
                    full[: cut + 1],
                    truncated,
                    f"{name} 在 t={cut} 处截断不变性被破坏 —— "
                    f"前 {cut + 1} 个位置依赖了 t 之后的数据（承 TD-05-6）",
                )

    def test_r7_momentum_warmup_is_neutral(self) -> None:
        """承 R7：warm-up 期输出中性值（0），不产生与输入无关的常数。"""
        strategy = strategy_contract.get_strategy("momentum")
        series = [100.0 + i for i in range(50)]
        factors = strategy.compute_factors(_facts_from_series(series))
        for t in range(strategy.lookback):
            self.assertEqual(
                factors[t], 0.0,
                f"momentum warm-up 第 {t} 根应为中性值 0（承 R7）",
            )


if __name__ == "__main__":
    unittest.main()
