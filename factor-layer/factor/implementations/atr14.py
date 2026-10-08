"""`atr14` —— 14 日平均真实波幅（Wilder 平滑）—— **价格单位**（元 / 美元）。

```
TR  = max(high−low, |high−prev_close|, |low−prev_close|)
ATR = Wilder 平滑（alpha = 1/14）
```

## 两个身份（都要，且不是同一件事）

| 身份 | 谁在用 |
|---|---|
| **① 波幅水平的度量** | 本因子本身（横向比较"谁在动"）——与 `vol20` / `adr20` 同族 |
| **② "贴近"的尺子** | 11.5 条件②「离均线**误差 ≤ 1 个 ATR**」+ `ma_dist_*` 的分母 |

**为什么要归一化**：ATR 让"贴近"对**高波动股票自动放宽** ——
若改用固定百分比，高 ATR 的票会被误排除，**那就是换了规则**。

⚠️ **别和 `adr20` 混**（见 `technical_indicators` 的说明）：
ATR **含跳空**（把 `prev_close` 算进真实波幅），ADR% **不含**。
11.5 条件④ 原文写的是「ATR / 收盘价」，而 §10.6 的选股过滤器用的是 ADR%
—— **他的资料里两种说法都出现过**，所以两个都实现、分开报，不替读者选。

**`direction = -1`**：与 `vol20` 同族（**低波动异象**）。先验，可被 IC 推翻。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec, ROLE_ALPHA
from factor.technical_indicators import ATR_WINDOW, wilder_atr


class Atr14Factor:
    """14 日 ATR（价格单位）。"""

    spec = FactorSpec(
        name="atr14",
        inputs=("high", "low", "close"),
        min_window=ATR_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        return wilder_atr(data.field("high"), data.field("low"),
                          data.field("close"), self.spec.min_window)


register_factor(Atr14Factor())
