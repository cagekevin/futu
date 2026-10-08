"""`atr_pct14` —— ATR(14) **除以收盘价**（**比值**：`0.025` = 2.5%）。

```
atr_pct14_t = ATR(14)_t / close_t
```

## 为什么它必须与 `atr14` 分开存在

11.5 **条件④** 的原话是「**ATR / 收盘价 > 2.5%**」——
这是一个**比值**，而 `atr14` 是**价格单位**（苹果的 5 美元 ≠ 某小票的 5 美元）。

⚠️ **为什么不在选择规则（R1）里现算 `atr14 / close`**：
   R1 只能读**声明过的因子**，而 `close` 是**面板字段**、不是因子
   —— 让 R1 去拿面板字段会**越过因子层**（打破"选择规则只做阈值判断"的干净边界）。
   ⇒ 归一化在**因子层**做完（此处），R1 只做阈值比较。

**与 `adr20` 的关系**：两者都测"波幅占价格的比例"，但
- `atr_pct14` **含跳空**（Wilder 真实波幅）
- `adr20` **不含跳空**（只用当日 `high−low`）
⇒ **不是同一个数**，两个都留着、分开报（承本文件族的口径纪律）。

**`direction = -1`**：与 `vol20` 同族（低波动异象）。先验，可被 IC 推翻。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec, ROLE_ALPHA
from factor.technical_indicators import ATR_WINDOW, wilder_atr


class AtrPct14Factor:
    """14 日 ATR 占收盘价的比例（比值，含跳空）。"""

    spec = FactorSpec(
        name="atr_pct14",
        inputs=("high", "low", "close"),
        min_window=ATR_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        high, low, close = (data.field("high"), data.field("low"),
                            data.field("close"))
        atr = wilder_atr(high, low, close, self.spec.min_window)
        return atr / close


register_factor(AtrPct14Factor())
