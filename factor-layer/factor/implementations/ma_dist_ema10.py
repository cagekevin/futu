"""`ma_dist_ema10` —— 到 **10EMA** 的距离，**以 ATR 为单位**。

```
ma_dist_ema10_t = (close_t − EMA10_t) / ATR(14)_t
```

## ★ 为什么登记的是「距离」而不是「均线值」

**均线值不是一个因子** —— 它是**价格的平滑**，直接排序 `ema10` 等于排序价格
（所以本族属 `role=screening`，见 `factor_spec` 的 role 契约变更）。

而 11.5 **条件②** 真正要的是：
**「股价贴近 10EMA / 20EMA / 50EMA / 150SMA / 200SMA 中任一条（**误差 ≤ 1 个 ATR**）」**
⇒ 要的是 **`|距离| ≤ 1`**，而距离**必须除以 ATR** 才是"以 ATR 计"。

## 为什么分母用 ATR 而不是固定百分比

**ATR 归一 = 对高波动股票自动放宽"贴近"的判定**。
若换成固定 `±2%`，高 ATR 的票会被**误排除** —— 那是**换了规则**，不是实现细节。

## 为什么归一化在这里做、而不是留给选择规则（R1）

R1 只能读**声明过的因子**；`close` 是**面板字段**、不是因子。
若让 R1 现算 `(close − ma)/atr`，它就得越过因子层去拿面板字段
—— 打破"选择规则只做阈值判断"的干净边界（承 `02-随机对照-PRD` 的 H4）。
⇒ **归一化在因子层做完，R1 只做 `abs(...) <= 1` 的比较。**

## `direction` 在本因子**无先验含义**

本因子是 `screening` 类、**不参与 IC 评估**，故 `direction` 取 `+1` 仅作占位
—— 真正有含义的是 **`abs(距离)` 与阈值比较**。**不要**把它当成看多信号。

**warm-up**：`max(10, 14) = 14` —— 受 **ATR(14) 的 warm-up 限制**（比均线的 10 更长）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_SCREENING
from factor.technical_indicators import ATR_WINDOW, ema, ma_distance_in_atr, wilder_atr

_MA_WINDOW = 10


class MaDistEma10Factor:
    """到 10EMA 的 ATR 归一距离（screening：11.5 条件② 的原料）。"""

    spec = FactorSpec(
        name="ma_dist_ema10",
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


register_factor(MaDistEma10Factor())
