"""`range_pct10` —— **10 日区间波幅**（`(最高价 − 最低价) / close`）。

用途：Tugboat §7.1 VCP 六要点第 4、5 条
「**波幅收缩，最好三次或以上**」「收缩到最后，**股价波动 < 1%**」。

## ⚠️ 为什么是 `role = screening`（不是 alpha）

它在本实验里的**唯一用途**是给"收缩形态"当**原料**：

```
连续几段 10 日区间越来越窄（≥3 次）+ 最后一段 < 1%
```
那个形态判断属**规则的派生**（R1 基于已声明因子做该日可见的派生），
**不是**本因子能算的 —— 本因子只提供**序列**。

而"波幅大小"当 alpha 评估这件事，**`vol20`（log_ret 标准差）已经在做**。
两个都拿去算 IC 会得到**高度相关**的两列（不是独立证据，却看起来像两条）。
⇒ 登记为 `screening`：**只被选择规则消费，不参与因子评估**。

（若将来确实要把它当 alpha 评，**必须先验它与 `vol20` 的截面相关** ——
 承"同原料 ≠ 同因子，是否独立靠截面相关去验，不靠名字"。）

**`direction = -1`**：仅为声明（`screening` 不参与排序评估）。先验 = 低波动异象。

**warm-up**：窗口 10 ⇒ 前 10 行 `NaN`。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec, ROLE_SCREENING
from factor.technical_indicators import range_percent

#: 区间窗口（VCP 的"收缩段"通常是 1–2 周）。
RANGE_WINDOW = 10


class RangePct10Factor:
    """10 日区间波幅占收盘价之比。"""

    spec = FactorSpec(
        name="range_pct10",
        inputs=("high", "low", "close"),
        min_window=RANGE_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
        role=ROLE_SCREENING,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        return range_percent(data.field("high"), data.field("low"),
                             data.field("close"), self.spec.min_window)


register_factor(RangePct10Factor())
