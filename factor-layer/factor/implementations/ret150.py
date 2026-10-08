"""`ret150` —— 150 日收益率（≈ 7 个月，Tugboat 的"长期"档）。

```
ret150_t = close_t / close_{t-150} − 1
```

**为什么窗口是 150**：`关于策略/9-资料-TradingTugboat.md` §11.5 条件③ 与
他的均线组合（150SMA / 200SMA）都落在**约半年到一年**这一档 ——
与 `ret5` / `ret20` / `ret60` 构成连续的窗口梯度，便于观察"动量在哪个窗口最强"。

**`direction = +1`**：与 `ret60` 一致（**中期动量**）。
先验的分界线大致是：短窗口反转（`ret5` / `ret20` = −1）、长窗口动量（`ret60` 起 = +1）。
⚠️ 这是**先验**，分界线到底在哪由 IC 决定。

**warm-up**：`shift(150)` ⇒ 前 **150** 行必然 `NaN`（承 A4）。

⚠️ **本因子不是条件③** —— 条件③ 要的是"**从最低点**涨"，
那是 `off_low150`（见其 docstring：两者在"阴跌后反弹"的票上差很远）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_ALPHA
from factor.technical_indicators import n_day_return

_WINDOW = 150


class Ret150Factor:
    """150 日收益率（中期动量的先验）。"""

    spec = FactorSpec(
        name="ret150",
        inputs=("close",),
        min_window=_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_LONG,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        return n_day_return(data.field("close"), self.spec.min_window)


register_factor(Ret150Factor())
