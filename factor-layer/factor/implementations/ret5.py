"""`ret5` —— 5 日收益率（超短期反转）。

```
ret5_t = close_t / close_{t-5} - 1
```

**设计意图（先验，可被 IC 推翻）**：`direction = -1` ——
**超短期反转**比 20 日反转更强（Jegadeesh 1990；Lehmann 1990）。
**这是先验，不是结论。**

⚠️ **与 `ret20` 的关系**：同族不同窗口。**相关性会很高** ——
它进这批的主要作用是**给 BH family 增加一个"已知会重复"的成员**，
用来观察「同一族因子在 BH 下如何互相挤压」。
⚠️ 若它与 `ret20` 的截面相关 > 0.9，那它**没有独立价值** —— 这必须被看见，不能被假设。

**warm-up**：`shift(5)` → 前 **5** 行必然 `NaN`（承 A4）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec, ROLE_ALPHA


class Ret5Factor:
    """5 日收益率（超短期反转的先验）。"""

    spec = FactorSpec(
        name="ret5",
        inputs=("close",),
        min_window=5,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        close = data.field("close").astype("float64")
        return close / close.shift(self.spec.min_window) - 1.0


register_factor(Ret5Factor())
