"""M4 预处理 —— ③ **标准化**。

## 为什么放在中性化**之前**

中性化是逐日 OLS 回归 —— 回归对**量纲**敏感（不然 `size` 的系数会被
"市值是 1e12 量级"这件事主导，而不是被它的解释力主导）。

## 标准差为 0 的交易日

当日横截面上所有值相同（或只剩 1 个样本）⇒ `std == 0` ⇒ 无法标准化。
此时输出 **`NaN`**，**不返回 0** —— 返回 0 等于宣称"这天的因子值都在均值上"，
那是**编**出来的信息（承 P1/P6：缺就是缺，不造）。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

__all__ = ["standardize"]


def standardize(values: "pd.DataFrame", *, method: str
                ) -> tuple["pd.DataFrame", dict[str, Any]]:
    """逐日标准化 → `(新宽表, 改动量)`。

    `zscore`：逐日 `(x − mean) / std`。

    ⚠️ `std == 0` 的交易日 → 该日整行 **`NaN`**（不返回 0），并记进
       `standardize_degenerate_days`（显形）。
    ⚠️ **不原地改**（承 T5）。
    """
    if method == "none":
        return values.copy(), {"standardize_degenerate_days": 0,
                               "standardize_empty_days": 0}
    if method != "zscore":
        raise ValueError(f"未知标准化方法：{method!r}（可选 zscore / none）")

    frame = values.astype("float64")
    mean = frame.mean(axis=1)
    std = frame.std(axis=1, ddof=1)
    alive = std > 0
    z = frame.sub(mean, axis=0).div(std.where(alive), axis=0)
    # 退化日整行置 NaN —— `std` 为 0/NaN 时上面已经算成 NaN，这里显式再压一次，
    # 防止"恰好只有一个样本 ⇒ std=NaN ⇒ 除完还是 NaN"这类边界被误当成有效值。
    z = z.mask(~alive, other=np.nan)
    # ⚠️ 区分"空日"（全 NaN，如 warm-up）与"退化日"（有值但 std == 0）——
    # 前者不是问题，后者**必须被看见**（承 T2：显形要精确）。
    empty = ~frame.notna().any(axis=1)
    return z, {
        "standardize_degenerate_days": int((~alive & ~empty).sum()),
        "standardize_empty_days": int(empty.sum()),
    }
