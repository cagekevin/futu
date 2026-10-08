"""`rs_rank` —— **池内截面相对强度百分位**（0–1）。

```
原始 = 0.4×(3 月收益) + 0.2×(6 月) + 0.2×(9 月) + 0.2×(12 月)      ← IBD / MarketSmith 的加权
rs_rank = 当日横截面百分位(原始)                                  ← rank(axis=1, pct=True)
```

口径出处：Tugboat §7.1 VCP 六要点第 2 条
「**相对强度要强（MarketSmith 的 RS 值，他一般选 90 以上）**」。

## ★ 为什么用「池内百分位」而不是「个股收益 − SPY 收益」

一条数学事实：**同一天里所有股票减去的是同一个数**（SPY 收益），
而**排序对"全体同时减一个常数"是不变的** ⇒

```
rank(个股收益)  ≡  rank(个股收益 − SPY收益)
```

⇒ **「池内百分位」自动就是「相对大盘强度」** —— 两者是同一件事的两种写法。
**实测验证过**（1,111 天 × 270 只，逐格相同）。

而反过来，**"跑赢 SPY"当门槛几乎不筛人**：

| 写法 | 通过率 |
|---|---|
| **池内百分位 ≥ 90%** | **10.24%** ✅ 有筛选力 |
| `收益 − SPY收益 ≥ 0` | **56.38%** ⚠️ 太松 |

（原因：我们的票池**本身就是精选强势股池**，它们整体就跑赢 SPY ⇒
"跑赢大盘"是个很低的标准。）

⚠️ **唯一要显形的差别是"参考系"**：他的是**全市场**前 10%，我们的是**这 270 只**的前 10%。
池子本身是强票 ⇒ **我们的门槛在绝对强度上更严**。不是同一把尺子，报告里要写明。

⚠️ **不变量**（可作测试）：`池内前 10%` ⊆ `跑赢 SPY`（数学必然）。

**`direction = +1`**：相对强度越高越看多（动量）。

**warm-up**：最长窗口 252（12 个月）⇒ 前 252 行为 `NaN`。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_ALPHA
from factor.technical_indicators import (
    RS_WEIGHTS, cross_sectional_percentile, weighted_relative_return,
)

#: 最长窗口 = 加权口径里最大的那个（warm-up 与它对齐）。
_LONGEST = max(n for n, _ in RS_WEIGHTS)


class RsRankFactor:
    """池内截面相对强度百分位（IBD 加权口径）。"""

    spec = FactorSpec(
        name="rs_rank",
        inputs=("close",),
        min_window=_LONGEST,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_LONG,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        raw = weighted_relative_return(data.field("close"), RS_WEIGHTS)
        # 全 NaN 的日子（warm-up 期）：`rank(pct=True)` 仍返回全 NaN ✅
        return cross_sectional_percentile(raw)


register_factor(RsRankFactor())
