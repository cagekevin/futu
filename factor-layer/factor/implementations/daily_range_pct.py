"""`daily_range_pct` —— **当日振幅**：`(high − low) / close`。

用途：Tugboat §7.1 VCP 六要点第 5 条
「**收缩到最后，股价波动 < 1%**」。

## ⚠️ 为什么是"当日"而不是"多日区间"

原文只说「**股价波动 < 1%**」，没说是哪一段。第一版我实现成
"过去 **10 天**的极差 ≤ 1%" —— 但那只票**两周总共动了不到 1%**，
那不是「紧密盘整」，是**停牌**。⇒ **那个读法在语义上就是错的**（不是"太严"）。

⇒ 取**当日振幅**：`(high − low) / close`。
这与 §10.6 的 ADR% 是**同一族的当日量**，区别只是前者是**单日**、后者是 **20 日均值**。

**`role = screening`**：它是 VCP 判定链（收缩序列的最后一段）的原料，不当 alpha 评估。
（它的 20 日均值 `adr20` 已在评估里。）

**`direction = -1`**：仅为声明（`screening` 不参与排序评估）。

**warm-up**：O(1)（当行即有值）⇒ 用 `mask_warmup` 显式丢掉前 1 行，
与 `min_window = 1` 对齐（承 A4：宁可明说少用一行，也不偷偷换定义）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec, ROLE_SCREENING
from factor.technical_indicators import mask_warmup

#: 显式丢掉的前导行数（当行即有值，丢 1 行只为满足 A4 的 warm-up 不变量）。
WARMUP_ROWS = 1


class DailyRangePctFactor:
    """当日振幅占收盘价之比。"""

    spec = FactorSpec(
        name="daily_range_pct",
        inputs=("high", "low", "close"),
        min_window=WARMUP_ROWS,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
        role=ROLE_SCREENING,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        raw = (data.field("high") - data.field("low")) / data.field("close")
        return mask_warmup(raw, WARMUP_ROWS)


register_factor(DailyRangePctFactor())
