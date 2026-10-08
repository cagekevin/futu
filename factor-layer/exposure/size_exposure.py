"""M2 暴露 —— size（log 市值）构造。

## 公式与近似（承 U4：**显形**，不掩饰）

```
隐含股本_i = market_cap_now_i / price_now_i          （快照时点）
市值_t,i   = 隐含股本_i × raw_close_t,i              （假设股本不变）
size_t,i   = log(市值_t,i)
```

## ⚠️ 为什么必须用 **raw** close，不能用 hfq

hfq 比值 = `raw_t·cum_i(t) / raw_now·cum_i(now)` —— 多了一个**因股而异**的
累积复权因子 `cum_i`。而 size 的全部意义就是**横截面排序**：
`cum_i` 因股而异 ⇒ 排序被系统性拧歪（分红多的股票整体偏移）。

⇒ 本层必须**另取一次 raw 面板**。这不是冗余，是正确性的代价。

## ⚠️ 近似声明（**不可消除**）

"股本不变"假设：真实的增发 / 回购会让市值偏离。
库里**没有历史股本**（快照只有一个时点），故只能显形 —— 不做任何"修正"。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

__all__ = ["implied_shares", "build_size_exposure"]


def implied_shares(snapshot_rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """快照行 → 隐含股本 `market_cap / price`（快照时点）。

    ⚠️ 缺 `price` / `market_cap`，或 `price <= 0` → **不产出**该标的。
       不用 0 兜底、不用别的标的中位数替代 —— 缺就是缺（承 P1/P6）。
    """
    out: dict[str, float] = {}
    for row in snapshot_rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"快照行不是映射：{row!r}（承 P2：不猜结构）")
        symbol = str(row.get("symbol") or "")
        price = row.get("price")
        market_cap = row.get("market_cap")
        if not symbol or price is None or market_cap is None:
            continue
        price_f, cap_f = float(price), float(market_cap)
        if price_f <= 0 or cap_f <= 0:
            continue
        out[symbol] = cap_f / price_f
    return out


def build_size_exposure(raw_close: "pd.DataFrame",
                        shares: Mapping[str, float]) -> "pd.DataFrame":
    """`size = log(股本 × raw_close)` 的逐日宽表。

    ⚠️ 承 U1：市值缺失 → **`NaN`**（不填 0、不前值填充）。
    ⚠️ `raw_close <= 0` → `NaN`（log 无定义；**不取绝对值、不加 epsilon** ——
       那是"猜"，承 P2）。
    ⚠️ 股本缺失 / 非正 → 该标的**整列 `NaN`**。
    """
    frame = raw_close.astype("float64")
    frame = frame.where(frame > 0)                       # 非正价 → NaN
    share_series = pd.Series(shares, dtype="float64").reindex(frame.columns)
    share_series = share_series.where(share_series > 0)  # 缺股本 / 非正 → NaN
    market_cap = frame.mul(share_series, axis=1)
    return np.log(market_cap)
