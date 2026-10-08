"""`vol_ratio10_50` —— **量能趋势**（短期均量 / 长期均量，`< 1` = 在缩量）。

```
vol_ratio10_50 = mean(volume, 10) / mean(volume, 50)
```

用途：Tugboat §7.1 VCP 六要点第 6 条「**最后配合成交量下跌**」
⇒ 判据是 **`< 1`**（盘整末期缩量）。

**`role = screening`**：它是筛选原料（供规则判"缩量"），不是独立可评估的 alpha。
（与 `turn20` 的区别：`turn20` 是"当日量 / 基线量"的**水平**，
 本因子是**短长期均量之比**的**趋势** —— 一个说"现在量大量小"，一个说"在放量还是缩量"。）

**自带抗拆股**：分子分母同为量，任何**恒定**的拆股比例都会约掉。
⚠️ 但**窗口内**发生拆股（前后比例不一致）仍会失真 —— 那是数据口径的事，不是本函数的。

**`direction = -1`**：仅为声明（`screening` 不参与排序评估）。先验同 `turn20`（高量能偏空）。

**warm-up**：取**长窗口 50** ⇒ 前 50 行 `NaN`。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec, ROLE_SCREENING
from factor.technical_indicators import (
    VOLUME_LONG_WINDOW, VOLUME_SHORT_WINDOW, volume_ratio,
)


class VolRatio10To50Factor:
    """10 日均量 / 50 日均量。"""

    spec = FactorSpec(
        name="vol_ratio10_50",
        inputs=("volume",),
        min_window=VOLUME_LONG_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
        role=ROLE_SCREENING,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        return volume_ratio(data.field("volume"), VOLUME_SHORT_WINDOW,
                            VOLUME_LONG_WINDOW)


register_factor(VolRatio10To50Factor())
