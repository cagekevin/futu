"""M5 评估 —— 标签构造（**全仓唯一的实现点**，承 Q3）。

## 口径（写死，承 PRD §6.1 / `backtest/target_label.py`）

```
target_ret[t] = log(open[t+2] / open[t+1])
```

**为什么是 `t+1 → t+2` 而不是 `t → t+1`**：
你看到 `t` 收盘 → 决策 → 实际成交在 `t+1` **开盘** → 收益算到 `t+2` 开盘。
用 `close[t] → close[t+1]` 是错的（收盘瞬间无法成交）——
这半个 bar 的差异在高频策略上就是生与死。

## ⚠️ 与 `backtest/target_label.py` 的唯一差异：**边界处理**

| | 边界（最后 `TARGET_HORIZON` 个交易日）|
|---|---|
| `backtest/target_label.py` | 置 **0**（它要喂 PnL 引擎一个 float 序列，0 = 不参与）|
| **本模块** | 置 **`NaN`**（0 会被当成"收益为 0"混进 IC —— 承 J4/K4：缺就是缺）|

**公式与成交约定完全一致**（由跨层契约测试锁死：非边界部分逐位相等）；
**边界处理各自**，因为下游需求不同（PnL 序列 vs 截面相关）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from panel.panel_types import CrossSectionPanel

__all__ = ["TARGET_HORIZON", "ENTRY_OFFSET", "forward_return"]

#: 收益算到第几个 bar（`t + 2`）。
TARGET_HORIZON = 2
#: 成交发生在第几个 bar 的开盘（`t + 1`）。
ENTRY_OFFSET = 1


def forward_return(panel: CrossSectionPanel, *, field: str = "open"
                   ) -> "pd.DataFrame":
    """标签：`log(open[t+2] / open[t+1])`（承 §6.1，**全仓唯一实现点**）。

    ⚠️ **用 `open` 而不是 `close`** —— 见模块 docstring 的口径论证。
    ⚠️ 边界（最后 `TARGET_HORIZON` 个交易日）→ **`NaN`**，不填 0、不前值填充（承 J4）。
    ⚠️ `open <= 0` → `NaN`（`log` 无定义；不取绝对值、不加 epsilon —— 承 P2）。

    ⚠️ **一处必须显形的限制**：`t+1` / `t+2` 指**面板交易日轴**上的下一天/下两天。
       若某标的中间停牌（面板上缺那几天），`shift` 会**跨越**停牌日 ——
       即标签把停牌期间的收益算进了"下一个交易日"。
       这是"按面板轴对齐"的固有结果，**不是 bug**；停牌样本的标签本来就没有干净的定义。
    """
    open_ = panel.field(field).astype("float64")
    numerator = open_.shift(-TARGET_HORIZON)
    denominator = open_.shift(-ENTRY_OFFSET)
    usable = (numerator > 0) & (denominator > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.log(numerator / denominator)
    return out.where(usable)
