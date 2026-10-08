"""`rsi14` —— 14 日相对强弱指标（Wilder 平滑，0–100）。

```
RSI_t = 100 − 100 / (1 + avg_gain / avg_loss)
```
`avg_*` 用 **Wilder 平滑**（`alpha = 1/14`），不是简单均值 ——
这是 TradingView / StockCharts 的标准口径，也是 Tugboat 用的那个。

## 它为什么是"筛选原料"里最核心的一个

出处：`关于策略/9-资料-TradingTugboat.md` §10.8。

原文的关键洞察是 **RSI 的"指数化 / 标准化"特性**：
> SNDK 单日涨 **8.25%** 和 FTNT 涨 **2.35%**，**RSI 的单日变化都是 ≈ 4.3**
> ⇒ 它把不同波动率的股票放到**同一把尺子**上做横向比较。

11.5 的筛选器**两条**条件都落在它上面：
- 条件①「最近 3–4 天 RSI 每日变化 **< 3**、累计 ≤ 5」→ **定义"紧密盘整"**；
- 条件⑤「RSI **> 50**」→ 突破确认。

（逐日变化由**选择规则**在 R1 里算差分，见 `02-随机对照-PRD` 的 H4。）

## `direction`：一处**必须显式说出的张力**

本因子取 **`-1`**（值越大越看空）—— 理由是**与 `ret5` / `ret20` 保持一致**：
RSI(14) 本质是"近 14 日涨跌幅的相对强度"，和短周期收益是**同一族**，
而那一族的先验是**短期反转**。

⚠️ 但 Tugboat 用的是**动量**读法（"RSI > 50 = 突破确认"）—— **方向相反**。

两者**不是矛盾**：他用的是"**突破时点**的延续"，本层测的是"**横截面**上 RSI 高低的后续收益"。
**这是先验，不是结论** —— M5 会同时给出"按 direction 归一"与"原始符号"的单调性，
**方向猜错时看得出来**（承 J5）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec, ROLE_ALPHA
from factor.technical_indicators import RSI_WINDOW, wilder_rsi


class Rsi14Factor:
    """14 日 RSI（短期反转的先验，与 Tugboat 的动量读法相反）。"""

    spec = FactorSpec(
        name="rsi14",
        inputs=("close",),
        min_window=RSI_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        return wilder_rsi(data.field("close"), self.spec.min_window)


register_factor(Rsi14Factor())
