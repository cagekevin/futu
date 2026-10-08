"""`off_low150` —— 当前价**从 150 日最低点**涨了多少。

```
off_low150_t = close_t / min(low, 过去 150 天) − 1
```

## 这才是 11.5 条件③

原文：**「过去 150 或 260 个交易日，**从最低点**涨 > 30–50%
（强势股的休息，不是弱势股苟延残喘）」**。

⚠️ **它不是 `ret150`**（"当前价 vs 150 天前"）。两者在**阴跌后反弹**的票上差得很远：

| 走势 | `ret150` | `off_low150` |
|---|---|---|
| 一路阴跌、最近小幅反弹 | **负**（比 150 天前低）| **可为正**（已离最低点一段）|
| 单边上涨后横盘 | 正 | 正（且更大）|

**必须用哪个，取决于条件③到底想问什么** —— 原文的**意图**（"强势股的休息"）
更贴近 `off_low*`（**离底部多远**），所以按 `off_low*` 实现，并把 `ret*` 也留着，
让报告能**同时**给出两种读数、不替读者选。

**`direction = +1`**：离底部越远 ⇒ 越强（动量先验）。

**warm-up**：`rolling(150).min()` 需 150 个样本，故前 **150** 行必然 `NaN`（承 A4）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_ALPHA
from factor.technical_indicators import rise_from_low

_WINDOW = 150


class OffLow150Factor:
    """距 150 日最低点的涨幅（条件③ 的口径）。"""

    spec = FactorSpec(
        name="off_low150",
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


register_factor(OffLow150Factor())
