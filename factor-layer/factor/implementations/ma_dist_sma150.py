"""`ma_dist_sma150` —— 到 **150SMA** 的距离，**以 ATR 为单位**。

```
ma_dist_sma150_t = (close_t − SMA150_t) / ATR(14)_t
```

口径与设计理由**同 `ma_dist_ema10`** —— 只差均线窗口，且这里是 **SMA**（不是 EMA）：
**11.5 条件② 原文对长周期用的是 `150SMA` / `200SMA`**（简单均线），
对短周期用的是 `10EMA` / `20EMA` / `50EMA`（指数均线）。
**这不是笔误，照原文分开实现** —— 把两者统一成一种会**悄悄改掉他的规则**。

**`direction` 无先验含义**（screening 类不参与 IC）。

**warm-up**：`max(150, 14) = 150`。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_SCREENING
from factor.technical_indicators import ATR_WINDOW, ma_distance_in_atr, sma, wilder_atr

_MA_WINDOW = 150


class MaDistSma150Factor:
    """到 150SMA 的 ATR 归一距离（screening：11.5 条件② 的原料）。"""

    spec = FactorSpec(
        name="ma_dist_sma150",
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
        return ma_distance_in_atr(close, sma(close, _MA_WINDOW), atr)


register_factor(MaDistSma150Factor())
