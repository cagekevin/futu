"""`ma_dist_ema50` —— 到 **50EMA** 的距离，**以 ATR 为单位**。

```
ma_dist_ema50_t = (close_t − EMA50_t) / ATR(14)_t
```

口径与设计理由**同 `ma_dist_ema10`**（含"为什么登记距离而不是均线值"、
"为什么除以 ATR"、"为什么不留给 R1 算"）—— 只差均线窗口。

11.5 条件② 的候选均线共 5 条（10EMA / 20EMA / 50EMA / 150SMA / 200SMA），
**5 条各登记一个因子**，让报告能区分"贴近的是哪一条"：

| 贴近哪条 | 形态含义 |
|---|---|
| 10 / 20 EMA | **短期**紧密盘整（A 级特征）|
| 50 EMA | 中期整理 |
| 150 / 200 SMA | 长期均线附近 —— ⚠️ 若 200MA **仍向下**，Tugboat 视为 **pass**（落后股）|

**`direction` 无先验含义**（screening 类不参与 IC）。

**warm-up**：`max(50, 14) = 50`。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_SCREENING
from factor.technical_indicators import ATR_WINDOW, ema, ma_distance_in_atr, wilder_atr

_MA_WINDOW = 50


class MaDistEma50Factor:
    """到 50EMA 的 ATR 归一距离（screening：11.5 条件② 的原料）。"""

    spec = FactorSpec(
        name="ma_dist_ema50",
        inputs=("high", "low", "close"),
        min_window=max(_MA_WINDOW, ATR_WINDOW),
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_LONG,        # 占位：screening 类不看方向
        role=ROLE_SCREENING,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        high, low, close = (data.field("high"), data.field("low"),
                            data.field("close"))
        atr = wilder_atr(high, low, close, ATR_WINDOW)
        return ma_distance_in_atr(close, ema(close, _MA_WINDOW), atr)


register_factor(MaDistEma50Factor())
