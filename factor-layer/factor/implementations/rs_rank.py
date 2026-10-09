"""`rs_rank` —— **池内截面相对强度百分位**（0–1）。

```
原始 = 0.4×(3 月收益) + 0.2×(6 月) + 0.2×(9 月) + 0.2×(12 月)      ← IBD / MarketSmith 的加权
rs_rank = 当日横截面百分位(原始)                                  ← rank(axis=1, pct=True)
```

口径出处：Tugboat §7.1 VCP 六要点第 2 条
「**相对强度要强（MarketSmith 的 RS 值，他一般选 90 以上）**」。

## ★ 口径**可参数化** —— 因为他给了不止一档（治 TD-05-10）

本类**原本只注册一档**（IBD 加权）。但 Tugboat 自己**按场合选口径**：

| 场合 | 他用的口径 | 出处 |
|---|---|---|
| **筛 VCP / 紧密盘整** | ⭐ **`RS(1M)`**（另做 3 / 6 / 12 月）| §11.4 Deepvue 筛选示范 |
| 高动能选股 | **1 个月 或 6 个月**（他专门讨论过两者的区别）| §8.1「关于『相对强度回看期』的区别」|
| VCP 六要点 | MarketSmith 的 RS（**加权**）| §7.1 第 2 条 |

> **他的原话**：「**1 个月 > 97** ⇒ 近期才有爆发性走势，目前**可能还处于短暂盘整**；
> **6 个月 > 97** ⇒ 之前有过爆发性走势，盘整时间相对较长」——**两种都可以交易**。

⇒ **只实现加权一档 = 拿一把"偏长期"的尺子去量"刚爆发 + 正盘整"的形态**，
   会**系统性筛掉**他真正想要的票（这可能就是 VCP 变体候选 = 0 的一部分原因）。

⇒ 三档**并列**：本文件（`rs_rank`，加权）／`rs_rank_1m`／`rs_rank_6m`。

⚠️ **一个文件 = 一个注册因子**（见 `tests/test_factor.py::test_a2_add_factor_is_one_file_plus_one_line`）
⇒ 本文件只注册**默认口径**；另两档住各自文件，它们 `import` 本类再带参数注册。

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

**warm-up**：`max(n for n, _ in weights)`（默认口径 = 252 ⇒ 12 个月）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_LONG, FactorSpec, ROLE_ALPHA
from factor.technical_indicators import (
    RS_WEIGHTS, cross_sectional_percentile, weighted_relative_return,
)


class RsRankFactor:
    """池内截面相对强度百分位（**口径由 `weights` 决定**）。

    `weights`：`((窗口交易日数, 权重), ...)` —— 空 ⇒ 报错（**口径必须显式**，承 P1）。
    """

    def __init__(self, *, name: str = "rs_rank",
                 weights: tuple[tuple[int, float], ...] = RS_WEIGHTS) -> None:
        if not weights:
            raise ValueError(
                "weights 不能为空 —— 相对强度的**回看口径必须显式**（承 P1：不静默兜底）")
        self.weights = tuple(weights)
        self.spec = FactorSpec(
            name=name,
            inputs=("close",),
            min_window=max(n for n, _ in self.weights),
            frequency="1d",
            adjust="hfq",
            direction=DIRECTION_LONG,
            role=ROLE_ALPHA,
        )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        raw = weighted_relative_return(data.field("close"), self.weights)
        # 全 NaN 的日子（warm-up 期）：`rank(pct=True)` 仍返回全 NaN ✅
        return cross_sectional_percentile(raw)


register_factor(RsRankFactor())
