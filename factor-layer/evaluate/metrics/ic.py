"""M5 评估 —— IC 类指标：RankIC 序列 / ICIR / IC 胜率 / t 检验。

## ★ J4：有效样本必须显形

因子值有 `NaN`（warm-up 期），标签也有 `NaN`（`t+2` 越界、停牌、退市）。
**只在两者的交集上算 IC** —— 且**每日的有效样本数必须报出来**。

为什么这条是硬约束：`pandas.corr` 默认会**悄悄丢掉**配对不全的样本 ——
你永远不知道那天到底用了 3 只还是 300 只股票。样本数悄悄塌了，IC 还是"算出来了"。

⚠️ **禁止 `fillna(0)`**：那会把"没有标签"变成"收益为 0"，
让 IC 被一堆**编出来的 0** 稀释（承 J4 / K4）。

## 为什么用 RankIC 而不是 Pearson IC

因子值常有极端值（未 winsorize 的原始值）与非线性关系。
Spearman（秩相关）对**单调变换不变**、对极端值稳健 —— 它回答的是
"**排序**对不对"，而截面因子的全部意义就是排序。
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

__all__ = ["rank_ic_series", "ic_statistics", "MIN_SAMPLES_FLOOR"]

#: 硬底线：当日有效样本少于 2 只时相关系数**无定义**（不是"接近 0"）。
MIN_SAMPLES_FLOOR = 2


def rank_ic_series(factor: "pd.DataFrame", target: "pd.DataFrame", *,
                   min_samples: int) -> "pd.DataFrame":
    """逐日 RankIC（Spearman）→ `DataFrame`（`rank_ic` + `n_valid`）。

    ⚠️ 承 J4：**只在对齐后的有效样本上算**（两者都非 `NaN`），且 `n_valid` 必须报出。
    ⚠️ `n_valid < min_samples` → 该日 `rank_ic = NaN`（**不硬算** —— 2 只股票的
       "相关系数"恒为 ±1，那是噪声不是信号）。
    ⚠️ 当日因子值全同（秩全相等）→ Spearman 无定义 → `NaN`（不填 0）。
    """
    if min_samples < MIN_SAMPLES_FLOOR:
        raise ValueError(
            f"min_samples={min_samples} 低于硬底线 {MIN_SAMPLES_FLOOR} —— "
            f"少于 2 个样本时相关系数无定义（承 J4）"
        )
    rows: list[dict[str, Any]] = []
    for day in factor.index:
        f = factor.loc[day].astype("float64")
        t = target.loc[day].astype("float64")
        valid = f.notna() & t.notna()
        n_valid = int(valid.sum())
        ic = float("nan")
        if n_valid >= min_samples:
            fv, tv = f[valid], t[valid]
            if fv.nunique() > 1 and tv.nunique() > 1:
                value = fv.corr(tv, method="spearman")
                if value == value:                      # 不是 NaN
                    ic = float(value)
        rows.append({"trade_date": str(day), "rank_ic": ic, "n_valid": n_valid})
    return pd.DataFrame(rows).set_index("trade_date")


def ic_statistics(ic: "pd.Series", *, min_days: int) -> dict[str, Any]:
    """IC 序列 → `ICIR` / `IC 胜率` / `t 检验` / 有效天数（承 J2）。

    - `ICIR = mean(IC) / std(IC)`（**逐日 IC 的稳定性**，不是 IC 的大小）
    - `IC 胜率 = P(IC > 0)`
    - `t = mean / (std / √n)`，双侧 p 值（`scipy.stats.ttest_1samp`）

    ⚠️ `有效天数 < min_days` → 统计量全 `NaN` 且 `verdict_hint = "insufficient_data"`
       （**不硬算** —— 30 天的 IC 序列算 ICIR 是自欺）。
    """
    clean = ic.dropna()
    n_days = int(len(clean))
    out: dict[str, Any] = {
        "n_days": n_days,
        "n_days_total": int(len(ic)),
        "min_days": int(min_days),
        "ic_mean": float("nan"), "ic_std": float("nan"), "icir": float("nan"),
        "ic_win_rate": float("nan"), "t_stat": float("nan"), "p_value": float("nan"),
    }
    if n_days < min_days:
        return out

    mean = float(clean.mean())
    std = float(clean.std(ddof=1))
    out["ic_mean"] = mean
    out["ic_std"] = std
    out["ic_win_rate"] = float((clean > 0).mean())
    if std > 0:
        out["icir"] = mean / std
        out["t_stat"] = mean / (std / math.sqrt(n_days))
        from scipy import stats  # 局部 import：只在真的做检验时才需要 scipy
        out["p_value"] = float(stats.ttest_1samp(clean.to_numpy(), 0.0).pvalue)
    return out
