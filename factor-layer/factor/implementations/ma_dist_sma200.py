"""`ma_dist_sma200` —— 到 **200SMA** 的距离，**以 ATR 为单位**。

```
ma_dist_sma200_t = (close_t − SMA200_t) / ATR(14)_t
```

口径与设计理由**同 `ma_dist_ema10`**（含为什么用 SMA、为什么除以 ATR）。

⚠️ **200SMA 在这套规则里有个特殊身份**：Tugboat 的 **pass 条件**之一是
「**200MA 仍向下**」= 落后股，不做（§11.5 的看图评级表）。

但**本因子只有"距离"，没有"斜率"** —— 所以它**答不了"200MA 是否向下"**。
⇒ 规则若要用那条 pass 条件，**还需要一个斜率因子**（尚未实现）。
**在它到位之前，规则不得声称自己过滤了"200MA 向下"** —— 那是**做不到**，不是"做了没说"。

⇒ 本因子的作用是**中性的**：只提供"离 200SMA 多远"。

**`direction` 无先验含义**（screening 类不参与 IC）。

**warm-up**：`max(200, 14) = 200`（本族里最长的 warm-up）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_SCREENING
from factor.technical_indicators import ATR_WINDOW, ma_distance_in_atr, sma, wilder_atr

_MA_WINDOW = 200


class MaDistSma200Factor:
    """到 200SMA 的 ATR 归一距离（screening：11.5 条件② 的原料）。"""

    spec = FactorSpec(
        name="ma_dist_sma200",
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


register_factor(MaDistSma200Factor())
