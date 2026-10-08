"""`ret60` —— 60 日收益率。

```
ret60_t = close_t / close_{t-60} - 1
```

**设计意图（先验，可被 IC 推翻）**：`direction = +1` —— 假设**中期动量**
（强者续强）。

⚠️ 与 `ret20`（`direction = -1`）**方向相反** —— 这是刻意的：
短期反转 / 中期动量是两条被反复观察到的效应，同时放进来还能看出
"同一族因子在不同窗口上符号翻转"这件事。

**warm-up**：`shift(60)` → 前 **60** 行必然 `NaN`（承 A4）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec


class Ret60Factor:
    """60 日收益率（中期动量的先验）。"""

    spec = FactorSpec(
        name="ret60",
        inputs=("close",),
        min_window=60,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_LONG,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        close = data.field("close")
        return close / close.shift(self.spec.min_window) - 1.0


register_factor(Ret60Factor())
