"""`ret20` —— 20 日收益率。

```
ret20_t = close_t / close_{t-20} - 1
```

**设计意图（先验，可被 IC 推翻）**：`direction = -1` —— 假设**短期反转**
（20 日内涨得多的，接下来回落）。这是学术文献里更常见的短期效应，
但**不是结论**：M5 的 IC 检验会告诉我们真相。

**warm-up**：`shift(20)` → 前 **20** 行必然 `NaN`（承 A4）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec


class Ret20Factor:
    """20 日收益率（短期反转的先验）。"""

    spec = FactorSpec(
        name="ret20",
        inputs=("close",),
        min_window=20,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        close = data.field("close")
        return close / close.shift(self.spec.min_window) - 1.0


register_factor(Ret20Factor())
