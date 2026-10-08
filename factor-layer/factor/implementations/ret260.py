"""`ret260` —— 260 日收益率（≈ 52 周，Tugboat 的"最长"档）。

```
ret260_t = close_t / close_{t-260} − 1
```

与 `ret150` **同族同向**（`direction = +1`，中期动量），只差窗口 ——
260 ≈ 一个交易年，是 11.5 条件③ 给的另一个选择。

**为什么要两个窗口都留**：条件③ 原文是「过去 **150 或 260** 个交易日」——
**他没定死**。两个都实现，跑对照时**分开报**，
而不是由我们替他挑一个（挑了就等于悄悄改规则）。

**warm-up**：`shift(260)` ⇒ 前 **260** 行必然 `NaN`（承 A4）。

⚠️ 与 `off_low260` 的区别同 `ret150` 的说明：**"N 日收益" ≠ "从最低点涨"**。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_ALPHA
from factor.technical_indicators import n_day_return

_WINDOW = 260


class Ret260Factor:
    """260 日收益率（中期动量的先验）。"""

    spec = FactorSpec(
        name="ret260",
        inputs=("close",),
        min_window=_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_LONG,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        return n_day_return(data.field("close"), self.spec.min_window)


register_factor(Ret260Factor())
