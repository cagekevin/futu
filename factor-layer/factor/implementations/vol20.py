"""`vol20` —— 20 日已实现波动率。

```
log_ret_t = log(close_t / close_{t-1})
vol20_t   = std(log_ret, 20)
```

**设计意图（先验，可被 IC 推翻）**：`direction = -1` —— **低波动异象**
（波动低的股票风险调整后收益更好）。

**warm-up**：`diff()` 让第 1 行变 `NaN`，再 `rolling(20)` →
前 **20** 行必然 `NaN`（承 A4）。

⚠️ 注意是 **20** 不是 21：`rolling(20).std()` 在索引 20 处才拿到
20 个**有效**收益率（索引 1..20），所以前 20 行（索引 0..19）为 `NaN`。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec


class Vol20Factor:
    """20 日已实现波动率（低波动异象的先验）。"""

    spec = FactorSpec(
        name="vol20",
        inputs=("close",),
        min_window=20,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        close = data.field("close")
        log_return = np.log(close).diff()
        return log_return.rolling(self.spec.min_window).std()


register_factor(Vol20Factor())
