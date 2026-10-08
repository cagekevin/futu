"""`off_low260` —— 当前价**从 260 日最低点**涨了多少。

```
off_low260_t = close_t / min(low, 过去 260 天) − 1
```

与 `off_low150` **同口径同向**（`direction = +1`），只差窗口 ——
对应 11.5 条件③ 的「150 **或** 260」两个选择。

⚠️ **保留两个窗口而不是挑一个**：原文没定死，**挑了就等于悄悄改规则**。
两个都跑、分开报，让数字说话。

**warm-up**：`rolling(260).min()` ⇒ 前 **260** 行必然 `NaN`（承 A4）。

> 副作用（要显形）：本因子需要 260 个交易日才出第一个值。
> 在 2022-05-03 → 2026-10-07（约 1,112 天）的可用面板上，
> 有效样本约 **850 天** —— 比 `off_low150` 少约 110 天。
> 报告里的 `n_days_valid` 会如实反映这个差异。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_ALPHA
from factor.technical_indicators import rise_from_low

_WINDOW = 260


class OffLow260Factor:
    """距 260 日最低点的涨幅（条件③ 的另一个窗口）。"""

    spec = FactorSpec(
        name="off_low260",
        inputs=("close", "low"),
        min_window=_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_LONG,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        return rise_from_low(data.field("close"), data.field("low"),
                             self.spec.min_window)


register_factor(OffLow260Factor())
