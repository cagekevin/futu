"""`turn20` —— 20 日**量能异常**（成交量相对自身基线）。

```
turn20_t = volume_t / mean(volume_{t-20 … t-1})
```

**为什么用「比值」而不是成交量本身**

⚠️ 数据层的 `volume` **不做复权**（拆股会等比放大成交量，而"该不该调"口径未定 ——
见数据层 `provide/api.py::_scale_bar`）。用**比值**让分子分母同比例缩放 ⇒
**对拆股不变** ✅ 这样才敢直接用未复权的 volume。

**为什么基线要 `shift(1)`**

用 `mean(volume_{t-20…t-1})` 而不是含 `t` 自身的窗口 ——
否则 `volume_t` 进了自己的分母，量能越高、比值越被拉平（**自我稀释**）。

**设计意图（先验，可被 IC 推翻）**：`direction = -1` ——
量能异常高 ⇒ **关注度高**（散户追捧 / 新闻热度）⇒ 后续跑输
（"高关注度折价"，Barber & Odean 2008）。**这是先验，不是结论。**

**warm-up**：`rolling(20).mean().shift(1)` → 前 **20** 行必然 `NaN`（承 A4）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec, ROLE_ALPHA

_WINDOW = 20


class Turn20Factor:
    """20 日量能异常（高关注度折价的先验）。"""

    spec = FactorSpec(
        name="turn20",
        inputs=("volume",),
        min_window=_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        volume = data.field("volume").astype("float64")
        baseline = volume.rolling(self.spec.min_window).mean().shift(1)
        return volume / baseline


register_factor(Turn20Factor())
