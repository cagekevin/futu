"""策略契约 + 注册表 —— 回测层与"你的策略"之间的**唯一**接口（扩展缝）。

## 回测是"通过什么"回测的

回测 = 把**你的策略**放到**历史事实**上，用**成本假设**折算成 PnL，再用**硬规则**判决。
三个输入，归属各不同（承 01 §0.2 的判据：**关于市场 vs 关于你**）：

| 输入 | 是什么 | 归谁 | 从哪进 |
|---|---|---|---|
| **事实** | K线 OHLCV（含参数化派生） | **数据层** | `data_source` |
| **策略** | 给定 ≤t 的行情 → t 时刻的**因子值** | **你（策略自己）** | 本模块的契约 |
| **假设** | 成本率 / 成交时点 / 阈值 / 窗口 | **回测层** | `backtest_config` |

## 为什么要有这个契约（根因）

如果像一次性脚本那样把策略**硬编码**在入口里，"加一个新指标"就得改入口、改框架 ——
指标会越加越多，框架迟早被改烂。抽成**契约 + 注册表**之后：

- **加新指标 = 加一个策略文件 + 登记一个名字**，框架其余部分一行不改；
- **"市场环境适不适合买卖"** 这类判断 = 一个**策略**（环境不利时输出 0 → 空仓），
  而不是写死进框架的规则 —— 判断属"你"，框架只负责**承载它、判决它**。

## 契约（你只需要实现这一个方法）

    class YourStrategy:
        name = "your_strategy"                          # 注册名，命令行 --strategy 用它
        def compute_factors(self, facts: MarketFacts) -> list[float]:
            ...                                         # 与 facts.times 等长

约束（承 V7/V9）：**factor[t] 只能用到 ≤ t 的事实**。

★ 关于"因果性怎么保证"（诚实说明，修 B5 的措辞）：
  - 本框架给策略的是**整条数组**（向量化，快），**没有**做 V7 的"物理切片隔离"
    （把 `>t` 从内存里切掉）—— 因为向量化接口无法逐 t 切片而不退化成 O(n²)。
  - 因此因果性靠**自动检验**兜底：`tests/test_causality.py` 做 **V9 污染未来测试**
    （把 t 之后换成垃圾，断言 `[0..t]` 逐位不变）。这是"检验"，**不是**"物理隔离"。
  - 若将来要求 V7 物理隔离，需把接口改成逐 t 调用（代价：慢），本模块已把
    `MarketFacts` 收敛为唯一入口，改造点集中在这里。

因子 → 仓位 / 成本 / PnL / 判决，全由框架统一做 —— 你**不要**在策略里算这些，
否则"训练 / 回测 / 实盘"就会走成两条路径（承 E5：禁 train-serve skew）。

## 为什么 `strategies/` 不单独成层（判过、决定**不拆**）

概念上，策略"关于你"、与回测框架是两回事（01 §0.2 的判据）；但**物理上不拆**：
契约（本模块）与实现（`strategies/`）被 `run_backtest` 同时消费。一旦把 `strategies/`
挪出 `backtest/`：
  - 要么 strategies 反向 import 框架 → **目录级循环依赖**；
  - 要么连契约一起挪 → 多一层 + 跨目录 import。
两者都让**改动半径 / 跨层跳跃**变大，而功能一点不变 ⇒ 复杂度**上升**，故不拆。
（触发再拆的条件：策略需要**独立版本 / 独立发布**时。）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MarketFacts:
    """框架喂给策略的**唯一**输入：t 时刻可得的**历史事实**。

    序列一律用 **tuple** —— `frozen=True` 只挡"重新赋值"，挡不住"就地改 list"；
    用不可变序列才能让"事实"真正只读，避免策略顺手改坏框架的数组。
    """

    times: tuple[int, ...]
    open: tuple[float, ...]
    high: tuple[float, ...]
    low: tuple[float, ...]
    close: tuple[float, ...]
    volume: tuple[float, ...]
    # 数据层算好的**客观派生量**（net_gex / spot / rps …），用来让策略判断
    # "市场环境适不适合买卖"。**判断本身属策略，原料由数据层给**（承 01 §0.2）。
    #
    # ⚠️ 本字段【未接通】—— 而且不是"没人用"，是**契约对不上、现在接不通**：
    #   · `data_source.align_daily_items` 产出的是**按交易日**对齐的面板（键 = 日期）；
    #   · 本字段要求与 `times`（**按 bar** 的 Unix 秒）等长。
    #   一个"每天一条"、一个"每根 bar" —— **"日 → bar"怎么合，从未定义过**。
    #   ⇒ 只要这个映射没定死，任何"吃 net_gex/rps 的策略"都无法安全接入（不是等需求的问题）。
    #
    # 接通前必须先定死两件事（属**不可逆**的契约决策，承铁律 6）：
    #   1. 一根 bar 归属哪个交易日？（bar 自带 `date`，可直接用）；
    #   2. 某交易日**无值**时怎么办？—— 承 D3/P6：**只许 ffill、禁 bfill；缺就不补**，
    #      所以默认应是"缺 → 该 bar 无值"，而非"沿用上一日"。
    # 定完再实现 `align_daily_items`，并把结果按上述规则铺到 bar 轴上喂这里。
    extra: dict[str, tuple[float, ...]] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.times)

    def require_extra(self, name: str) -> tuple[float, ...]:
        """取一个 extra 序列；**没接就报错**，不静默给空（承 P1/P6）。"""
        if name not in self.extra:
            raise KeyError(
                f"extra['{name}'] 未提供 —— 该数据项尚未接入（承 P1：不静默兜底）"
            )
        return self.extra[name]


class Strategy(Protocol):
    """策略协议：一个名字 + 一个"事实 → 因子"的方法。"""

    name: str

    def compute_factors(self, facts: MarketFacts) -> list[float]:
        ...


# ── 注册表 ──────────────────────────────────────────────────────────────

_STRATEGY_REGISTRY: dict[str, Strategy] = {}


def register_strategy(strategy: Strategy) -> Strategy:
    """把策略实例登记进注册表。

    重复名字 / 空名字 → **报错**，不静默覆盖（承 P2：不猜、不掩盖）。
    """
    name = getattr(strategy, "name", "")
    if not name:
        raise ValueError("策略必须定义非空 name")
    if name in _STRATEGY_REGISTRY:
        raise ValueError(f"策略名重复：{name}（承 P2：不静默覆盖）")
    _STRATEGY_REGISTRY[name] = strategy
    return strategy


def get_strategy(name: str) -> Strategy:
    """按注册名取策略；未知名字 → 报错并列出可用名字（承 P1：不静默兜底）。"""
    if name not in _STRATEGY_REGISTRY:
        raise KeyError(f"未知策略 '{name}'；可用：{available_strategies()}")
    return _STRATEGY_REGISTRY[name]


def available_strategies() -> list[str]:
    """已注册的策略名（升序）。"""
    return sorted(_STRATEGY_REGISTRY)
