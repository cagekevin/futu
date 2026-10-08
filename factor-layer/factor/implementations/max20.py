"""`max20` —— 20 日**最大单日收益**（MAX 效应）。

```
max20_t = max(log_ret_{t-19 … t})
```

**设计意图（先验，可被 IC 推翻）**：`direction = -1` ——
近期有过暴涨日的股票吸引**彩票型买盘** ⇒ 后续跑输
（Bali, Cakici & Whitelaw 2011, "Maxing out"）。**这是先验，不是结论。**

⚠️ **为什么有了 `vol20` / `skew20` 还要它**：MAX 是**极值统计量**，不是矩 ——
它对**单个异常日**敏感，而 `vol20` 会被其余 19 天的平稳"摊平"，
`skew20` 需要足够多的样本才稳定（20 个点的三阶矩估计噪声很大）。
**MAX 在 20 个点上是最稳的尾部度量** —— 这是它单独存在的理由。

## ★ 但实测**推翻**了上面这条理由（2026-10-08）

全窗口（1110 个有效日）因子间截面相关（逐日 Spearman 中位数）：

| | `max20` | `vol20` |
|---|---|---|
| `max20` | 1.00 | **+0.819** |
| `vol20` | +0.819 | 1.00 |

**ρ = 0.82 ⇒ 它和 `vol20` 高度冗余，不是独立因子。**
两者的表现也印证了这点（净年化 −0.00% vs −0.95%）。

**结论**：这条因子**留在注册表里但价值有限** —— 它进 BH family 会让门槛变严
（family 越大越严），却几乎不带来新信息。

⚠️ **这是一处"先验被数据推翻"的示范**：`direction` 与"为什么需要它"都写成了
**可被推翻的显式声明**，所以被推翻时**看得出来**，而不是悄悄留着一个冗余因子。

**warm-up**：`diff()` 让第 1 行变 `NaN`，再 `rolling(20)` → 前 **20** 行必然 `NaN`（承 A4）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec, ROLE_ALPHA

_WINDOW = 20


class Max20Factor:
    """20 日最大单日收益（MAX 效应的先验）。"""

    spec = FactorSpec(
        name="max20",
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
        return log_return.rolling(self.spec.min_window).max()


register_factor(Max20Factor())
