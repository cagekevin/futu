"""M4 预处理 —— ④ **中性化**（逐日横截面回归取残差）。

## 为什么必须放最后

中性化是"扣掉行业和市值的影响"。扣完**不该**再做任何会重新引入暴露的处理
（再做标准化就会把尺度和暴露一起搅动）—— 承 PRD §6.2 的顺序。

## 模型

逐日：`factor ~ 1 + log市值 + 行业哑变量` → **取残差**。

⚠️ 行业哑变量**已去一列**（由 M2 的 `ExposureSet.industry_dummies` 保证）——
   完整 one-hot 与截距**完全共线** ⇒ 矩阵奇异 ⇒ 结果依赖求解器（不依赖数据）。

## ★ T3：残差与回归元的正交性

OLS 残差与**所有回归元**正交（数学性质）⇒ `corr(残差, log市值) ≈ 0`
**在正确实现下必然成立**。

所以 T3 不是"可能失败的验收"，而是**实现正确性的探针** —— 它会被这些错法打破：
  · size 的缺失样本被剔除、但算相关时又算进来；
  · 只做了行业去均值（没真的回归）；
  · 哑变量没去列 ⇒ 矩阵奇异 ⇒ 求解器给出奇怪的 β。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from exposure.exposure_types import ExposureSet

__all__ = ["neutralize", "NEUTRALIZE_METHODS"]

NEUTRALIZE_METHODS = ("size_industry", "size", "industry", "none")

#: 每个回归元至少要有几个样本才做回归（少于"元数 + 2"自由度不够）。
_MIN_EXTRA_SAMPLES = 2


def _uses(method: str) -> tuple[bool, bool]:
    if method == "none":
        return False, False
    if method == "size":
        return True, False
    if method == "industry":
        return False, True
    if method == "size_industry":
        return True, True
    raise ValueError(f"未知中性化方法：{method!r}（可选 {NEUTRALIZE_METHODS}）")


def neutralize(values: "pd.DataFrame", exposures: ExposureSet, *, method: str
               ) -> tuple["pd.DataFrame", dict[str, Any]]:
    """逐日回归取残差 → `(新宽表, 改动量)`。

    ⚠️ 承 M2 的 U1/U2：`size` 缺失 / 行业缺失的标的 → **当日剔除并计数**
       （承 K4：缺就是缺，不猜一个组）。
    ⚠️ 自由度不足的交易日 → 该日整行 `NaN`，并记进日志（不硬回归出垃圾）。
    ⚠️ **不原地改**（承 T5）。
    """
    use_size, use_industry = _uses(method)
    out = pd.DataFrame(np.nan, index=values.index, columns=values.columns,
                       dtype="float64")
    n_dropped = 0
    skipped_days: list[str] = []
    deficient_days: list[str] = []
    max_corr = 0.0

    for day in values.index:
        y = values.loc[day].astype("float64")
        present = y.notna()
        if not bool(present.any()):
            continue

        columns: dict[str, pd.Series] = {}
        usable = present.copy()
        if use_size:
            size = exposures.size.loc[day].reindex(y.index).astype("float64")
            columns["size"] = size
            usable &= size.notna()
        dummies = None
        if use_industry:
            dummies = exposures.industry_dummies(str(day)).reindex(index=y.index)
            columns.update({f"ind:{c}": dummies[c].astype("float64")
                            for c in dummies.columns})
            usable &= dummies.notna().any(axis=1)

        n_dropped += int((present & ~usable).sum())
        if not bool(usable.any()):
            skipped_days.append(str(day))
            continue

        yv = y.loc[usable]
        if columns:
            design = pd.DataFrame({k: v.loc[usable] for k, v in columns.items()},
                                  index=yv.index)
            matrix = np.column_stack([np.ones(len(yv)),
                                      design.to_numpy(dtype="float64")])
        else:
            matrix = np.ones((len(yv), 1))
        if len(yv) < matrix.shape[1] + _MIN_EXTRA_SAMPLES:
            deficient_days.append(str(day))
            continue

        beta, _res, rank, _sv = np.linalg.lstsq(matrix, yv.to_numpy(dtype="float64"),
                                                rcond=None)
        if rank < matrix.shape[1]:
            deficient_days.append(str(day))          # 秩亏 ⇒ 结果依赖求解器，显形
            continue
        residual = yv.to_numpy(dtype="float64") - matrix @ beta
        out.loc[day, yv.index] = residual

        if use_size:
            size_v = columns["size"].loc[yv.index].to_numpy(dtype="float64")
            if len(yv) > 2 and float(np.std(size_v)) > 0:
                corr = float(np.corrcoef(residual, size_v)[0, 1])
                if abs(corr) > abs(max_corr):
                    max_corr = corr

    return out, {
        "neutralize_dropped": n_dropped,
        "neutralize_skipped_days": skipped_days,
        "neutralize_deficient_days": deficient_days,
        # ★ T3 的实测验收值：正确实现下应 ≈ 0（OLS 残差与回归元正交）
        "neutralize_max_abs_corr_size": abs(max_corr),
    }
