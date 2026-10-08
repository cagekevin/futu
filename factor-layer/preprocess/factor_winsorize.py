"""M4 预处理 —— ① **去极值**。

## 为什么必须放最前

极端值会把**均值 / 标准差**带偏 —— 后面标准化用的是它们。
先标准化再去极值，等于"用被污染的尺度量过之后才修数据"，结果完全不同。

## `mad` 的定义

逐日 `median ± n × MAD` 后 clip，其中 `MAD = median(|x − median(x)|)`。

⚠️ **不乘 1.4826**：那个常数是"让 MAD 成为正态分布的一致估计量"用的。
本层要的是**稳健尺度**，不是估计量 —— 乘不乘只改变 `n` 的标定。不乘更直白。
（实测阈值 `n = 5.0` 来自 `zer0factor` 的默认，不是猜的。）
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

__all__ = ["winsorize"]


def winsorize(values: "pd.DataFrame", *, method: str, n: float
              ) -> tuple["pd.DataFrame", dict[str, Any]]:
    """逐日去极值 → `(新宽表, 改动量)`。

    ⚠️ **不原地改**：返回**新**宽表（承 T5：不许覆盖原始值）。
    ⚠️ `MAD == 0` 的交易日（当日值几乎全相同）→ **不做 clip**，并记进日志 ——
       否则会把整日压成一条直线（`median` 唯一值），那是"猜"。
    """
    if method == "none":
        return values.copy(), {"winsorize_clipped": 0,
                               "winsorize_degenerate_days": 0,
                               "winsorize_empty_days": 0}
    if method != "mad":
        raise ValueError(f"未知去极值方法：{method!r}（可选 mad / none）")
    if not (isinstance(n, (int, float)) and n > 0):
        raise ValueError(f"去极值阈值必须为正：{n!r}（承 T4：非法参数早报）")

    frame = values.astype("float64")
    median = frame.median(axis=1)
    mad = frame.sub(median, axis=0).abs().median(axis=1)
    span = n * mad
    alive = span > 0
    lower = (median - span).where(alive, other=-np.inf)
    upper = (median + span).where(alive, other=np.inf)

    clipped = frame.clip(lower=lower, upper=upper, axis=0)
    changed = clipped.ne(frame) & frame.notna() & clipped.notna()
    # ⚠️ 区分两种"没 clip"的日（承 T2：显形要精确，别把两件事混成一个数）：
    #   · **空日**（当日全 NaN，如 warm-up 期）—— 没数据可处理，不是问题；
    #   · **退化日**（当日有值但 MAD == 0）—— 尺度算不出来，**必须被看见**。
    empty = ~frame.notna().any(axis=1)
    return clipped, {
        "winsorize_clipped": int(changed.to_numpy().sum()),
        "winsorize_degenerate_days": int((~alive & ~empty).sum()),
        "winsorize_empty_days": int(empty.sum()),
    }
