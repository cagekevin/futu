"""R1 选择集 —— 协议 + 输入容器（承本能力 PRD §五 R1）。

## 为什么输入要包一层 `SelectionInput`（与 M3 的 `FactorInput` 同构）

三条理由，每条对应一个真实事故：

1. **物理防泄漏**（H3，承 backtest V7）：
   因子表在**送进来之前**就被切成 `[:day]`，规则**想偷看未来也拿不到数据**
   —— 比"约定不要看未来"强一个量级（纪律会被违反，数据结构不会）。

2. **依赖显式**（H4，承 M3-A3）：
   规则只能读 `requires` 里**声明过**的因子；读未声明的 → **报错**。
   否则「依赖清单」就变成谎言，下游（缓存 / 重算 / 口径审计）全建在错的前提上。

3. **可复现**（H3，承 P3）：
   `params` 随规则实例冻结、进报告 —— 半年后能回答"当时用的是哪个阈值"。

## 与 M3 `factor/` 的边界（承本 PRD §二 额外检查）

| | M3 `factor/` | R1 `selection/` |
|---|---|---|
| 产物 | **因子值**（连续、可排序）| **选中集**（0/1 决策）|
| 关系 | **原料** | **决定** |

⇒ **不重叠**。R1 消费 M3 的产出，不重算任何原始指标（承 H4）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注
    import pandas as pd

__all__ = ["Selection", "SelectionInput"]


@dataclass(frozen=True)
class SelectionInput:
    """选择规则**唯一**能看到的东西（已物理切片，承 H3）。

    `factors` 里的每一张宽表：index = 交易日（升序）、columns = 标的，
    且**已经切到 `[:day]`** —— 规则拿不到 `day` 之后的数据。
    """

    day: str
    factors: Mapping[str, "pd.DataFrame"]
    universe: tuple[str, ...]

    def factor(self, name: str) -> "pd.DataFrame":
        """取一张因子宽表；**没声明过 → 报错**（承 H4，照 M3 的 `FactorInput.field`）。"""
        if name not in self.factors:
            raise KeyError(
                f"选择规则没声明要因子 {name!r} —— 它只能用它 `requires` 里声明的："
                f"{sorted(self.factors)}（承 H4：声明与使用必须一致）"
            )
        return self.factors[name]

    def today(self, name: str) -> "pd.Series":
        """取该日的**横截面**（`day` 那一行，已按 `universe` 过滤）。

        ⚠️ 若该因子在 `day` 无值（warm-up / 停牌）→ 该标的**不在返回里**，
           不填 0、不前值填充（承 P6 / M1-3）。
        """
        table = self.factor(name)
        if self.day not in table.index:
            raise KeyError(f"因子 {name!r} 在 {self.day} 无数据（承 P6：不补造）")
        row = table.loc[self.day]
        kept = [s for s in self.universe if s in row.index]
        return row.loc[kept].dropna()


class Selection(Protocol):
    """选择规则协议：一个 `name` + 一个「输入 → 选中集合」的方法（承 H1）。

    ⚠️ 实现**不许**碰 IO、不许重算原始指标（承 H4：RSI 本体归 M3）。
    """

    #: 注册名（唯一）。
    name: str

    #: 依赖的**因子名**（已预处理或原始值均可）—— 缺任一 → 报错（承 H1）。
    requires: tuple[str, ...]

    #: 可复现参数（阈值等）—— 冻结在实例上，报告里带它的指纹（承 H3 / P3）。
    params: Mapping[str, Any]

    def select(self, data: SelectionInput) -> set[str]:
        """返回**该日选中的标的集合**（必须是 `data.universe` 的子集）。"""
        ...
