"""M5 评估 —— 换手（承 PRD §6.4，J2 的第七项）。

## 为什么必须报换手

一个 IC 显著、单调性 0.9 的因子，如果**月换手 300%**，扣掉成本后可能是**负的**。
不报换手，判决就是**不完整的** —— 这是 J2 把它列为必报项的理由。

## 口径（写死，承 §6.4）

多空两边各半仓：`top` 分位每只 `+0.5 / n_top`，`bottom` 分位每只 `−0.5 / n_bottom`。

```
turnover_t = Σ_i | w_t,i − w_{t−1},i |
```

⚠️ 边界（首日 / 前一交易日被跳过）→ **该日不计入**（`NaN`），
   且**跳过多少天必须显形** —— 不能悄悄把换手算低。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

__all__ = ["turnover"]


def _weights(factor_day: "pd.Series", target_day: "pd.Series", *,
             bins: int, min_samples: int) -> "pd.Series | None":
    """某日的多空权重（`index` = 标的，`value` = 权重）。样本不足 / 退化 → `None`。"""
    valid = factor_day.notna() & target_day.notna()
    if int(valid.sum()) < min_samples:
        return None
    fv = factor_day[valid]
    try:
        labels = pd.qcut(fv, bins, labels=False, duplicates="drop")
    except ValueError:
        return None
    if labels.nunique() < bins:
        return None
    top = list(fv.index[labels == bins - 1])
    bottom = list(fv.index[labels == 0])
    if not top or not bottom:
        return None
    out = pd.Series(0.0, index=fv.index, dtype="float64")
    out[top] = 0.5 / len(top)
    out[bottom] = -0.5 / len(bottom)
    return out


def turnover(factor: "pd.DataFrame", target: "pd.DataFrame", *,
             bins: int, min_samples: int) -> dict[str, Any]:
    """逐日换手 → `{"series", "mean", "median", "n_days", "skipped_days"}`。

    ⚠️ 承 J2：换手是**必报项**；`skipped_days` 必须显形（跳过会让换手看起来更低）。
    """
    series: dict[str, float] = {}
    skipped: list[str] = []
    previous: "pd.Series | None" = None
    for day in factor.index:
        current = _weights(factor.loc[day], target.loc[day],
                           bins=bins, min_samples=min_samples)
        if current is None:
            skipped.append(str(day))
            previous = None                    # 断了 → 下一次不跨缺口算
            continue
        if previous is not None:
            aligned = current.reindex(
                current.index.union(previous.index)).fillna(0.0)
            before = previous.reindex(aligned.index).fillna(0.0)
            series[str(day)] = float((aligned - before).abs().sum())
        previous = current

    values = pd.Series(series, dtype="float64", name="turnover")
    return {
        "series": values,
        "mean": float(values.mean()) if len(values) else float("nan"),
        "median": float(values.median()) if len(values) else float("nan"),
        "n_days": int(len(values)),
        "skipped_days": skipped,
    }
