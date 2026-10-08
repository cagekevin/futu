"""M2 暴露 —— industry（分组后的行业标签）构造。

## ★ 为什么阈值必须**逐日**统计（承 U3 的决策）

中性化是**逐日** OLS —— 阈值该反映"**该日**该行业的样本量"。
用全局样本量会让"某日只剩 1 只"的行业漏过阈值 → 它的哑变量**完全吸收**该标的
（残差恒为 0，标的静默"消失"，不报错）。

## ⚠️ 已知近似（承 U4）

行业分类来自**一个时点**的快照，用在历史日 = 假设"行业从未变更"。
对多数标的成立，但并购 / 重组会破坏它 —— **显形**，不掩饰。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from exposure.exposure_types import INDUSTRY_OTHER, MIN_INDUSTRY_COUNT_FLOOR

__all__ = ["industry_labels", "build_industry_exposure"]


def industry_labels(snapshot_rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """快照行 → `{symbol: 行业名}`。缺 / 空行业 → **不产出**（缺就是缺，承 K4）。"""
    out: dict[str, str] = {}
    for row in snapshot_rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"快照行不是映射：{row!r}（承 P2：不猜结构）")
        symbol = str(row.get("symbol") or "")
        industry = row.get("industry")
        if not symbol or industry is None:
            continue
        name = str(industry).strip()
        if not name:
            continue
        out[symbol] = name
    return out


def build_industry_exposure(industry_of: Mapping[str, str],
                            dates: Sequence[str], symbols: Sequence[str],
                            present_by_day: Mapping[str, Sequence[str]],
                            *, min_industry_count: int) -> "pd.DataFrame":
    """逐日行业标签宽表（低频行业归 `other`）。

    ⚠️ `min_industry_count` **必须显式**，且 `< MIN_INDUSTRY_COUNT_FLOOR` → **报错**
       （n=1 时哑变量会完全吸收该标的 —— 承 U3）。
    ⚠️ 缺行业的标的 → `None`（缺就是缺，承 K4）。
    ⚠️ 「该日样本量」按 `present_by_day`（M1 的每日票池）统计 —— 这是**近似**：
       真正的样本是"该日有因子值的标的"，但那时因子还没算出来（循环依赖），
       故用票池近似（差异会在覆盖率里显形）。
    """
    if min_industry_count < MIN_INDUSTRY_COUNT_FLOOR:
        raise ValueError(
            f"min_industry_count={min_industry_count} 低于硬底线 "
            f"{MIN_INDUSTRY_COUNT_FLOOR} —— 某行业只有 1 只标的时，它的哑变量会"
            f"**完全吸收**该标的（残差恒为 0），这在数学上就是错的（承 U3）"
        )
    symbol_set = set(str(s) for s in symbols)
    rows: list[dict[str, str]] = []
    for day in dates:
        members = [str(s) for s in (present_by_day.get(str(day)) or ())
                   if str(s) in symbol_set and str(s) in industry_of]
        if not members:
            continue
        counts: dict[str, int] = {}
        for symbol in members:
            group = industry_of[symbol]
            counts[group] = counts.get(group, 0) + 1
        for symbol in members:
            group = industry_of[symbol]
            rows.append({
                "day": str(day), "symbol": symbol,
                "label": group if counts[group] >= min_industry_count
                         else INDUSTRY_OTHER,
            })

    index = [str(d) for d in dates]
    columns = sorted(symbol_set)
    if not rows:
        return pd.DataFrame(np.full((len(index), len(columns)), None, dtype=object),
                            index=index, columns=columns)
    return (
        pd.DataFrame(rows)
        .pivot(index="day", columns="symbol", values="label")
        .reindex(index=index, columns=columns)
    )
