"""R3 组装 —— `run_placebo`：R1 → R2 → R3（本能力的**对外唯一入口**）。

★ **已声明偏离**（承 PRD §五 R3）：这是本能力的**第 4 个文件** ——
照 `evaluate/evaluator.py` 的先例（01-PRD §五 M5「新增第 6 个文件，已声明偏离」），
组装点**不可省**：否则「加规则零改动」无处落地。

## 一个必须写清的判定：「同池」到底是哪个池

`forward_return` 在**边界两天**是 `NaN`（`t+2` 越界），停牌也没有值。
若拿"含 NaN 的池子"去比，规则选中一只没收益的股票就会被算成 0 —— **那是造数据**。

⇒ 本模块的「池」= **该日票池 ∩ 有收益的标的**（`eligible`），并且：

- **真实侧**：`规则选中 ∩ eligible`，个数记为 `k_eff`；
- **陪考侧**：从 `eligible` 里抽 **同一个 `k_eff`**。

⇒ 两侧**同池同数量**仍然成立（承 N1）；差异（规则选中有多少只没收益）在报告里**显形**
（`k_per_day` 与规则的原始选中数可对不上，那是数据现实，不是 bug）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

import numpy as np
import pandas as pd

from evaluate.forward_return import forward_return
from panel.stock_universe import universe_diagnostics

from . import paired_statistics as ps
from .null_sampler import SamplingConfig, sample_indices
from .placebo_report import PlaceboReport
from .selection_registry import params_fingerprint, run_selection

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注
    from panel.panel_types import CrossSectionPanel

__all__ = ["run_placebo"]


def run_placebo(
    *,
    selection_name: str,
    panel: "CrossSectionPanel",
    factors: Mapping[str, "pd.DataFrame"],
    cost_rate: float,
    sampling: SamplingConfig,
) -> PlaceboReport:
    """跑完一次完整对照 —— R1（选）→ R2（抽）→ R3（判）。

    参数全部**必填无默认**（承 C3 / Z3）：
    - `selection_name`：R1 注册名；
    - `panel`：M1 的面板（**票池的唯一来源**，承 N3）；
    - `factors`：R1 依赖的因子表（宽表 `{名: DataFrame}`）；
    - `cost_rate`：**单边**成本率（往返记 `2×`）—— 两侧**同一口径**（承 Z3）；
    - `sampling`：抽样参数（次数 / 种子）。

    ⚠️ 标签只用 `evaluate/forward_return.py`（承 Z5：全仓唯一实现点）。
    """
    if cost_rate < 0:
        raise ValueError(f"cost_rate 不能为负：{cost_rate}（承 Z3）")

    target = forward_return(panel)          # ← Z5：唯一定义，不另写

    days: list[str] = []
    real: list[float] = []
    null_rows: list[np.ndarray] = []
    k_per_day: list[int] = []
    pool_per_day: list[int] = []

    for day in panel.dates:
        pool = tuple(panel.universe_by_day.get(day, ()))
        if not pool:
            continue

        sel = run_selection(selection_name, day, factors, pool)
        if not sel.picked:
            continue                        # 该日规则没选任何标的 → 无对照可言

        if day not in target.index:
            continue
        row = target.loc[day]
        in_row = {s for s in pool if s in row.index and not pd.isna(row[s])}
        if not in_row:
            continue

        picked_eff = [s for s in sel.picked if s in in_row]
        k_eff = len(picked_eff)
        if k_eff < 1:
            continue                        # 选中的全无收益 → 该日无效（显形在总数差里）

        eligible = tuple(s for s in pool if s in in_row)

        idx = sample_indices(len(eligible), k_eff, config=sampling, day=day)
        ret_vec = row.loc[list(eligible)].to_numpy(dtype=float)

        days.append(day)
        real.append(float(np.mean([row[s] for s in picked_eff])))
        null_rows.append(ret_vec[idx].mean(axis=1))     # (R,)
        k_per_day.append(k_eff)
        pool_per_day.append(len(eligible))

    if not days:
        raise ValueError(
            "没有任何一个交易日构成有效对照 —— 检查规则是否选中过标的、"
            "以及 `forward_return` 是否可用（承 Z4：样本量为 0 时不出报告）"
        )

    real_arr = np.asarray(real, dtype=float)
    null_mat = np.vstack(null_rows)
    if null_mat.shape != (real_arr.size, sampling.iterations):
        raise RuntimeError(
            f"配对形状不符：null {null_mat.shape} vs ({real_arr.size}, "
            f"{sampling.iterations})（承 Z1：必须逐日对齐）"
        )

    summary = ps.summarize(
        real_arr, null_mat,
        k_per_day=k_per_day, pool_per_day=pool_per_day, cost_rate=cost_rate,
    )
    verdict, issues = ps.judge(
        rank_pct=summary.rank_pct,
        first_half_delta=summary.first_half_delta,
        second_half_delta=summary.second_half_delta,
        real_mean_after_cost=summary.real_mean_after_cost,
    )

    # 幸存者偏差告警来自 M1 的诊断（承 Z4 / G-1：**必须显形**）
    diagnostics = universe_diagnostics(panel)
    warning = str(diagnostics.get("caveat") or "").strip()
    if not warning:
        warning = "（M1 未给出幸存者偏差告警文本 —— 请检查 universe_diagnostics）"

    return PlaceboReport(
        selection_name=selection_name,
        selection_params_fingerprint=_selection_fp(selection_name),
        sampling_params_fingerprint=params_fingerprint(sampling.params),
        n_days=len(panel.dates),
        n_days_valid=len(days),
        k_per_day_median=summary.k_median,
        universe_median=summary.pool_median,
        real_mean=summary.real_mean,
        null_mean=summary.null_mean,
        cost_rate=cost_rate,
        real_mean_after_cost=summary.real_mean_after_cost,
        null_mean_after_cost=summary.null_mean_after_cost,
        delta_mean=summary.delta_mean,
        delta_median=summary.delta_median,
        rank_percentile=summary.rank_pct,
        win_days_ratio=summary.win_days_ratio,
        first_half_delta=summary.first_half_delta,
        second_half_delta=summary.second_half_delta,
        verdict=verdict,
        rule_frozen_at=ps.JUDGE_RULE_FROZEN_AT,
        issues=issues,
        survivorship_warning=warning,
    )


def _selection_fp(name: str) -> str:
    """取规则参数的指纹（从注册表读，不重复跑选择）。"""
    from .selection_registry import get_selection

    return params_fingerprint(get_selection(name).params)
