"""`ma_dist_ema20` —— 到 **20EMA** 的距离，**以 ATR 为单位**。

```
ma_dist_ema20_t = (close_t − EMA20_t) / ATR(14)_t
```

口径、设计理由、与 `direction` 的说明**同 `ma_dist_ema10`** —— 只差均线窗口。

**为什么 20EMA 值得单独一个因子**：Tugboat 的看图评级里
「在 **10/20MA 附近**紧密盘整」是 **A 级**的特征之一
（`9-资料-TradingTugboat.md` §11.5）。
10 与 20 分开登记，报告才能回答"**它贴近的是哪一条**"
—— "贴在 10EMA" 与 "贴在 200SMA" 是**两种完全不同的形态**，不能合并成一个数。

**`direction` 无先验含义**（screening 类不参与 IC）。

**warm-up**：`max(20, 14) = 20`。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_SCREENING
from factor.technical_indicators import ATR_WINDOW, ema, ma_distance_in_atr, wilder_atr

_MA_WINDOW = 20


class MaDistEma20Factor:
    """到 20EMA 的 ATR 归一距离（screening：11.5 条件② 的原料）。"""

    spec = FactorSpec(
        name="ma_dist_ema20",
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


register_factor(MaDistEma20Factor())
