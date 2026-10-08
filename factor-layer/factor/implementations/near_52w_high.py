"""`near_52w_high` —— 距 **52 周新高**有多近（`1.0` = 正好在新高，越低越远）。

```
near_52w_high = close / max(high, 过去 250 天)
```

口径出处：Tugboat §7.1 VCP 六要点第 3 条
「**股价接近 52 周新高（最好不低于 52 周新高的 15%）**」
⇒ 他的判据是 **`≥ 0.85`**。

⚠️ **分母用 `high` 的最高价，不是收盘的最高价** ——
"新高"在图表上指**最高价**；用收盘会**低估**距离（把已经触及的高点抹掉）。

**`direction = +1`**：越接近新高越看多（52 周高点效应 / 动量）。

**warm-up**：窗口 250 ⇒ 前 250 行 `NaN`。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_ALPHA
from factor.technical_indicators import HIGH_52W_WINDOW, proximity_to_rolling_high


class Near52wHighFactor:
    """`close / 过去 250 天最高价`。"""

    spec = FactorSpec(
        name="near_52w_high",
        inputs=("high", "close"),
        min_window=HIGH_52W_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_LONG,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        return proximity_to_rolling_high(data.field("close"), data.field("high"),
                                         self.spec.min_window)


register_factor(Near52wHighFactor())
