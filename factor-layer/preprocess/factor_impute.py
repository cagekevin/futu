"""M4 预处理 —— ② **补缺**。

## 为什么这里"填"是合法的

`K4`（缺就是缺）禁的是**静默兜底** —— 悄悄把缺的补上、让人以为数据是全的。

本模块不同：补缺是**显式配置的步骤**（`impute_method` 必填、无默认），
而且**填了多少必须报出来**（承 T2）。若某日填了 40%，那一日的 IC 基本无意义 ——
这个事实必须让下游看得见，而不是被埋起来。

⚠️ 这也是本层**唯一**允许出现填充的地方。其余模块静态检查禁 `fillna`/`ffill`/`bfill`。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

__all__ = ["impute"]


def impute(values: "pd.DataFrame", *, method: str, threshold: float
           ) -> tuple["pd.DataFrame", dict[str, Any]]:
    """逐日补缺 → `(新宽表, 改动量)`。

    `cross_section_median`：逐日填**当日横截面中位数**。

    ⚠️ 填充比例 ≥ `threshold` 的交易日 → 记进 `days_over_threshold`（显形，承 T2）。
    ⚠️ 某日**全部**缺失 → 中位数也是 NaN ⇒ 不填（本来就无信息可借）。
    ⚠️ **不原地改**（承 T5）。
    """
    if method == "none":
        return values.copy(), {"impute_filled": 0, "impute_empty_days": 0,
                               "impute_days_over_threshold": []}
    if method != "cross_section_median":
        raise ValueError(
            f"未知补缺方法：{method!r}（可选 cross_section_median / none）"
        )
    if not (isinstance(threshold, (int, float)) and 0 < threshold <= 1):
        raise ValueError(f"填充比例阈值必须在 (0, 1]：{threshold!r}（承 T4）")

    frame = values.astype("float64")
    missing = frame.isna()
    median = frame.median(axis=1)
    # 用 `mask` 而不是 `fillna` —— 语义是"把缺失位置换成中位数"，
    # 且能一眼看出"只动缺失位置"（fillna 会被误读成"什么都填"）。
    filled = frame.mask(missing, median, axis=0)

    n_missing = missing.to_numpy().sum(axis=1)
    n_cols = frame.shape[1] or 1
    # ⚠️ 区分两类日（承 T2：显形要精确，别把两件事混成一个数）：
    #   · **空日**（全 NaN，如 warm-up）—— 借不到中位数，本来就**没填**；
    #   · **缺得多但有值可借**的日 —— 那才是"这一天基本是编出来的"，必须单独报。
    empty = missing.to_numpy().all(axis=1)
    over = [str(day) for day, count, is_empty
            in zip(frame.index, n_missing, empty)
            if count / n_cols >= threshold and not is_empty]
    return filled, {
        "impute_filled": int((missing & filled.notna()).to_numpy().sum()),
        "impute_empty_days": int(empty.sum()),
        "impute_days_over_threshold": over,
    }
