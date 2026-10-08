"""M5 评估 —— 分组收益 / 多空收益 / 分组单调性（承 PRD §6.4）。

## ★ 分箱必须**等频**，不是等距

269 只股票、`bins = 5` → 等频是 54/54/54/54/53。
若误用**等距**分箱，因子值的长尾分布会让**中间组塞进大部分样本**
（比如第 3 组 200 只、第 1 组 2 只）—— 多空收益完全不可比，且**不会报错**。

⚠️ 承 J2：**每组样本数必须报出来**（`quantile_counts`）——
   这是"等频真的生效了"的唯一证据。

## 单调性

```
monotonicity = Spearman(分位序号, 分位平均收益) × direction
```

⚠️ 承 J5：乘的 `direction` 来自**因子对象**（`FactorSpec`），不由调用方传 ——
   方向搞反会把有效因子判成无效（承 M3-A3）。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

__all__ = ["quantile_returns", "monotonicity"]


def quantile_returns(factor: "pd.DataFrame", target: "pd.DataFrame", *,
                     bins: int, min_samples: int
                     ) -> tuple["pd.DataFrame", "pd.DataFrame", "pd.Series", list[str]]:
    """逐日等频分箱 → `(分组平均收益, 每组样本数, 多空收益序列, 跳过的日)`。

    - `分组平均收益`：index = 交易日，columns = `0..bins-1`（0 = 因子值最小）
    - `每组样本数`：同形状，用来**证明等频生效**
    - `多空收益`：`(top 分位 − bottom 分位) / 2`（**两边各半仓**，承 §6.4）
    - `跳过的日`：样本不足 / 分箱退化（并列值太多）→ 该日跳过并**显形**

    ⚠️ `min_samples` 必须 ≥ `bins`（否则分不出 `bins` 个非空组）。
    """
    if bins < 2:
        raise ValueError(f"分箱数必须 ≥ 2：{bins}（承 §6.4）")
    if min_samples < bins:
        raise ValueError(
            f"min_samples={min_samples} < bins={bins} —— 分不出非空组（承 §6.4）"
        )

    index: list[str] = []
    returns: list[list[float]] = []
    counts: list[list[int]] = []
    long_short: list[float] = []
    skipped: list[str] = []

    for day in factor.index:
        f = factor.loc[day].astype("float64")
        t = target.loc[day].astype("float64")
        valid = f.notna() & t.notna()
        if int(valid.sum()) < min_samples:
            skipped.append(str(day))
            continue
        fv, tv = f[valid], t[valid]
        try:
            # `duplicates="drop"` 会在并列值过多时**减少**组数 → 下面显式检查
            labels = pd.qcut(fv, bins, labels=False, duplicates="drop")
        except ValueError:
            skipped.append(str(day))
            continue
        if labels.nunique() < bins:
            skipped.append(str(day))            # 分箱退化（并列值太多）→ 不硬算
            continue
        grouped = tv.groupby(labels)
        # 上面已确认 `labels.nunique() == bins` ⇒ `reindex` 不会产生缺口，
        # 所以**不需要** fillna（本层任何填充都必须显形，能不填就不填）。
        mean = grouped.mean().reindex(range(bins))
        count = grouped.size().reindex(range(bins))
        if bool(mean.isna().any()) or bool(count.isna().any()):
            skipped.append(str(day))
            continue
        index.append(str(day))
        returns.append([float(x) for x in mean.to_numpy()])
        counts.append([int(x) for x in count.to_numpy()])
        long_short.append((float(mean.iloc[-1]) - float(mean.iloc[0])) / 2.0)

    columns = list(range(bins))
    return (
        pd.DataFrame(returns, index=index, columns=columns, dtype="float64"),
        pd.DataFrame(counts, index=index, columns=columns, dtype="int64"),
        pd.Series(long_short, index=index, dtype="float64", name="long_short"),
        skipped,
    )


def monotonicity(quantile_mean: "pd.DataFrame", direction: int) -> float:
    """`Spearman(分位序号, 分位平均收益) × direction`（承 §6.4 / J5）。

    ⚠️ `direction` 来自**因子对象**（承 J5）；搞反 → 有效因子被判无效。
    ⚠️ 分位平均收益是**跨日平均**（不是逐日再平均相关系数）。
    """
    if direction not in (1, -1):
        raise ValueError(f"direction 必须是 +1 或 -1：{direction!r}（承 J5）")
    if quantile_mean.empty or quantile_mean.shape[1] < 2:
        return float("nan")
    profile = quantile_mean.mean(axis=0)
    if profile.nunique() < 2:
        return float("nan")
    rank = pd.Series(range(len(profile)), index=profile.index, dtype="float64")
    corr = rank.corr(profile, method="spearman")
    if corr != corr:
        return float("nan")
    return float(corr) * int(direction)
