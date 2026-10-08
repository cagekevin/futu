"""`adr20` —— 20 日平均日波幅占收盘价的比例（**比值**：`0.025` = 2.5%）。

```
ADR%_t = mean( (high − low) / close , 过去 20 天 )
```

口径出处：`关于策略/9-资料-TradingTugboat.md` §10.6 ——
**他认为最被忽略的一个指标**，原文给的三档用法：

| 用法 | 规则 |
|---|---|
| **选股过滤器** | **< 2.5% → 直接排除**（波幅太低，不值得做动能）；2.5–5% → 可做但控仓；**> 5% 最佳** |
| 止损距离 | 1–1.5 倍 ADR 之内（给正常呼吸空间）|
| 部分止盈 | 至少 2–3 倍 ADR（盈亏比 ≥ 2:1）|

**本层只用第 ① 条**（它是能证明/证伪的那条）；止损 / 止盈属执行层，不在本实验范围。

⚠️ **与 `atr_pct14` 的区别**：ADR% 只看当日 `high−low`，**不含跳空**；
ATR 把 `prev_close` 也算进真实波幅。**两者不是同一个数**，都留着、分开报。

**`direction = -1`**：与 `vol20` 同族（低波动异象）。先验，可被 IC 推翻。

**warm-up**：`(high−low)/close` 第 0 行就有效，故用 `mask_warmup` **显式**丢掉前 20 行
（不靠 `shift(1)` 凑 —— 那会把它悄悄变成"昨天的 ADR"，见 `technical_indicators`）。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import FactorInput
from factor.factor_registry import register_factor
from factor.factor_spec import DIRECTION_SHORT, FactorSpec, ROLE_ALPHA
from factor.technical_indicators import ADR_WINDOW, adr_percent


class Adr20Factor:
    """20 日 ADR%（不含跳空）。"""

    spec = FactorSpec(
        name="adr20",
        inputs=("high", "low", "close"),
        min_window=ADR_WINDOW,
        frequency="1d",
        adjust="hfq",
        direction=DIRECTION_SHORT,
        role=ROLE_ALPHA,
    )

    def compute(self, data: FactorInput) -> pd.DataFrame:
        return adr_percent(data.field("high"), data.field("low"),
                           data.field("close"), self.spec.min_window)


register_factor(Adr20Factor())
