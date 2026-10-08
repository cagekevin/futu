"""`skew20` —— 20 日收益**偏度**（彩票偏好 / lottery preference）。

```
log_ret_t = log(close_t / close_{t-1})
skew20_t  = skew(log_ret, 20)
```

**设计意图（先验，可被 IC 推翻）**：`direction = -1` ——
偏度高（少数暴涨日主导）的股票像**彩票**，投资者**过度追捧** ⇒ 后续跑输
（Barberis & Huang 2008；"lottery preference"）。**这是先验，不是结论。**

⚠️ **与 `vol20` 的关系**：两者都从 `log_ret` 来，但测的是**不同的矩** ——
`vol20` 是二阶（离散度），`skew20` 是三阶（不对称性）。
一个低波动但右偏的股票与一个高波动但对称的股票，`vol20` 会把它们分开，
`skew20` 会把它们分到一起。**所以它们是不同的因子，不是换了名字的同一个。**

**warm-up**：`diff()` 让第 1 行变 `NaN`，再 `rolling(20)` → 前 **20** 行必然 `NaN`（承 A4）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec, ROLE_ALPHA

_WINDOW = 20


class Skew20Factor:
    """20 日收益偏度（彩票偏好的先验）。"""

    spec = FactorSpec(
        name="skew20",
        inputs=("close",),
        min_window=_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        close = data.field("close").astype("float64")
        log_return = np.log(close).diff()
        return log_return.rolling(self.spec.min_window).skew()


register_factor(Skew20Factor())
