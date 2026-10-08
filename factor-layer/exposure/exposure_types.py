"""M2 暴露 —— 数据结构 + 契约常量。

**「零依赖」的含义**：不 import **同层其它模块**（`size_exposure` /
`industry_exposure` / `exposure_coverage` / `exposure_builder`）——
外部依赖（pandas）是允许的，且 `industry_dummies()` 运行时确实要用它。

承 PRD §五 M2。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

__all__ = [
    "ExposureSet",
    "INDUSTRY_OTHER",
    "MIN_INDUSTRY_COUNT_FLOOR",
    "MIN_INDUSTRY_COUNT_RECOMMENDED",
]

#: 低频行业的归并组名（承 PRD U3）。
INDUSTRY_OTHER = "other"

#: 阈值**硬底线**：某行业只有 1 只标的时，它的哑变量会**完全吸收**该标的
#: （残差恒为 0，标的静默"消失"）—— 所以阈值 < 2 在数学上就是错的。
MIN_INDUSTRY_COUNT_FLOOR = 2

#: 推荐阈值。实测（票池 269 只 / 68 个行业）：≥3 → 30 个行业、覆盖 81%、
#: 52 只归 `other`；≥5 的覆盖率代价过大（39% 进 `other`）。
MIN_INDUSTRY_COUNT_RECOMMENDED = 3


@dataclass(frozen=True, eq=False)
class ExposureSet:
    """风险暴露集 —— M2 的产物，供 M4 中性化使用。

    `size` / `industry` 都是宽表：index = 交易日（升序），columns = 标的（升序）。

    ⚠️ **可变性纪律**：与 `CrossSectionPanel` 同 —— `frozen=True` 挡不住就地改
    DataFrame，**消费方一律只读**。

    **显形字段**（承 P5 / U4 / U5）：
      · `size_is_approximated[day]` / `industry_is_approximated[day]`
        —— **逐日**近似标记：快照日**当天**是观测值（`False`），其余天是反推的（`True`）
      · `coverage` —— 覆盖率报告（含 `absorbed`，**必须为 0**）
    """

    dates: tuple[str, ...]
    symbols: tuple[str, ...]
    size: "pd.DataFrame"
    industry: "pd.DataFrame"
    min_industry_count: int
    coverage: Mapping[str, Any]
    size_is_approximated: Mapping[str, bool]
    industry_is_approximated: Mapping[str, bool]

    def industry_dummies(self, day: str) -> "pd.DataFrame":
        """某日的行业哑变量矩阵（标的 × (组数−1)，**去一列**，承 U2）。

        ⚠️ **必须去一列** —— 完整 one-hot 与截距完全共线，矩阵奇异
        （结果会依赖求解器，不依赖数据）。

        **去的那一列**：优先 `other` —— 它是最不稳定的杂项组，让**截距**吸收它
        最安全；没有 `other` 时取组名排序的第一个（确定性，不随机）。

        ⚠️ 只含该日**有行业标签**的标的；缺行业的标的**不在返回里**
        （承 K4：缺就是缺，不猜）。
        """
        if day not in self.industry.index:
            raise KeyError(
                f"行业暴露里没有交易日 {day!r}（承 P1：缺就报，不静默给空）"
            )
        labels = self.industry.loc[day].dropna()
        if labels.empty:
            return pd.DataFrame(index=[], columns=[], dtype="float64")
        groups = sorted({str(g) for g in labels})
        dropped = INDUSTRY_OTHER if INDUSTRY_OTHER in groups else groups[0]
        keep = [g for g in groups if g != dropped]
        out = pd.DataFrame(0.0, index=list(labels.index), columns=keep, dtype="float64")
        for symbol, group in labels.items():
            if str(group) in keep:
                out.at[symbol, str(group)] = 1.0
        return out
