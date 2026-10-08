"""M2 暴露 —— 覆盖率统计与显形（承 U5 + U3）。

## ★ 为什么必须有 `absorbed`

「某日某个**被保留为哑变量**的组里只有 1 只标的」时，那个哑变量会**完美拟合**
该标的 → **残差恒为 0** → 该标的在中性化后"消失"（排在正中间），**且不报错**。

这是 M2 最危险的失效模式（PRD 陷阱 1）。所以：
  · `coverage["absorbed"]` 把它数出来；
  · `exposure_builder` 在它 ≠ 0 时**直接报错**（承 U3：必须为 0）。
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

import pandas as pd

from exposure.exposure_types import INDUSTRY_OTHER

__all__ = ["coverage_report"]


def coverage_report(size: "pd.DataFrame", industry: "pd.DataFrame", *,
                    min_industry_count: int) -> dict[str, Any]:
    """覆盖率报告（承 U5：缺失 / 归 other / **被吸收** 三组计数）。"""
    absorbed = 0
    min_kept = None
    n_groups_total = 0
    for day in industry.index:
        labels = industry.loc[day].dropna()
        if labels.empty:
            continue
        groups = sorted({str(g) for g in labels})
        dropped = INDUSTRY_OTHER if INDUSTRY_OTHER in groups else groups[0]
        counts = Counter(str(g) for g in labels)
        for group in groups:
            if group == dropped:
                continue                      # 被截距吸收 —— 不会"完全拟合"
            n_groups_total += 1
            n = counts[group]
            if n < 2:
                absorbed += 1                 # ★ 只有 1 只 → 哑变量完全吸收它
            min_kept = n if min_kept is None else min(min_kept, n)
    return {
        "n_days": int(size.shape[0]),
        "n_symbols": int(size.shape[1]),
        "size_missing": int(size.isna().sum().sum()),
        "industry_missing": int(industry.isna().sum().sum()),
        "industry_other": int((industry == INDUSTRY_OTHER).sum().sum()),
        "absorbed": int(absorbed),            # ★ 必须为 0
        "min_kept_group_size": int(min_kept) if min_kept is not None else 0,
        "n_kept_groups_total": int(n_groups_total),
        "min_industry_count": int(min_industry_count),
    }
