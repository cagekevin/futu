"""全窗口批量实跑：7 个因子一次评估 + **因子间截面相关**。

★ 为什么必须算因子间相关：
   「换因子类型」的价值，取决于新因子**是否真的独立** ——
   若 `ret5` 与 `ret20` 的截面相关 > 0.9，那它只是**换了名字的同一个因子**，
   进 BH family 只会让门槛变严（拖累所有人），不带来新信息。
   **相关性必须被测量，不能被名字假设。**

跑法：.venv/bin/python run_factor_batch.py [--days N]
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

import factor.implementations  # noqa: F401
from evaluate.evaluator import EvaluateConfig, evaluate_many
from evaluate.judgement import JudgeThresholds
from exposure.exposure_builder import read_exposures
from factor.factor_registry import available_factors, run_factor
from panel.panel_builder import read_panel
from panel.provide_reader import read_days, read_stocks
from preprocess.preprocess_pipeline import PreprocessConfig, read_preprocessed_factor

PREPROCESS = PreprocessConfig(
    winsorize_method="mad", winsorize_n=5.0,
    impute_method="cross_section_median", impute_threshold=0.5,
    standardize_method="zscore", neutralize_method="size_industry",
)
EVALUATE = EvaluateConfig(
    bins=5, min_samples=30,
    thresholds=JudgeThresholds(
        alpha=0.05, min_days=60,
        min_abs_icir=0.05, min_abs_monotonicity=0.3,
        cost_bps_per_turnover=5.0,   # ⚠️ 示例假设，不是代码默认值
        min_net_annual_return=0.0,
        annualization_days=250,
    ),
)


def cross_correlation(factors: dict[str, pd.DataFrame], *,
                      sample_every: int = 5) -> pd.DataFrame:
    """因子间**截面相关**：逐日算 Spearman 再取中位数（承 J5 的同一套纪律）。

    ⚠️ 用**逐日中位数**而不是"把全样本堆起来算一个相关" ——
       后者会把"时间维的共动"混进"截面相似度"，两回事。

    ⚠️ **抽样**（每 `sample_every` 天取一天）：中位数对抽样稳健，
       而全量算 28 对 × 1480 天 ≈ 4 万次 Spearman —— 慢且吃内存，
       实测会**把进程拖死**（只写了表头就没了）。抽样是**成本控制**，不是偷懒。
    """
    names = sorted(factors)
    out = pd.DataFrame(np.nan, index=names, columns=names, dtype="float64")
    sampled = list(factors[names[0]].index)[::sample_every]
    for i, a in enumerate(names):
        for b in names[i:]:
            daily: list[float] = []
            for day in sampled:
                x, y = factors[a].loc[day], factors[b].loc[day]
                valid = x.notna() & y.notna()
                if int(valid.sum()) < 30:
                    continue
                xv, yv = x[valid], y[valid]
                if xv.nunique() > 1 and yv.nunique() > 1:
                    value = xv.corr(yv, method="spearman")
                    if value == value:
                        daily.append(float(value))
            rho = float(np.median(daily)) if daily else float("nan")
            out.loc[a, b] = out.loc[b, a] = rho
            print(f"    {a} ↔ {b}: ρ = {rho:+.3f}", flush=True)
    return out


def main(n_days: int | None = None) -> None:
    days = read_days()
    symbols = read_stocks("2026-09-30")["stocks"]
    window = days if n_days is None else days[-n_days:]
    label = "全窗口" if n_days is None else f"{n_days} 天"
    print(f"=== {label}：{len(window)} 天 × {len(symbols)} 只 × "
          f"{len(available_factors())} 个因子 ===", flush=True)

    t0 = time.time()
    panel = read_panel(symbols, days=window, stocks_day="2026-09-30")
    exposures = read_exposures(panel)
    names = available_factors()
    prepared = [read_preprocessed_factor(run_factor(n, panel), exposures, PREPROCESS)
                for n in names]
    reports = evaluate_many(prepared, panel, config=EVALUATE)
    print(f"  耗时 {time.time()-t0:.0f}s\n", flush=True)

    print(f"{'因子':8s} {'dir':>4s} {'ICIR':>7s} {'p':>8s} {'天':>5s} "
          f"{'单调':>6s} {'毛年化':>8s} {'净年化':>8s} {'换手':>6s}  判决")
    for r in reports:
        s, j = r.ic_stats, r.judgement
        print(f"{r.factor_name:8s} {r.direction:>+4d} {s['icir']:>+7.3f} "
              f"{s['p_value']:>8.4g} {s['n_days']:>5d} {r.monotonicity:>+6.2f} "
              f"{j['gross_annual_return']:>+7.2%} {j['net_annual_return']:>+7.2%} "
              f"{j['turnover_mean']:>6.3f}  {j['verdict']}", flush=True)

    print("\n=== 因子间截面相关（逐日 Spearman 的中位数）===", flush=True)
    # ⚠️ 用 `prepared`（`PreprocessedFactor`）而不是 `reports` ——
    #    `EvaluationReport` 只带**指标**，不带因子值本身。
    corr = cross_correlation({p.name: p.values for p in prepared})
    print("        " + " ".join(f"{n[:7]:>8s}" for n in corr.columns), flush=True)
    for name in corr.index:
        cells = " ".join(f"{corr.loc[name, c]:>+8.2f}" for c in corr.columns)
        print(f"{name:8s}{cells}", flush=True)

    print("\n=== 高相关对（|ρ| ≥ 0.7 ⇒ 很可能只是换了名字的同一个因子）===",
          flush=True)
    found = False
    for i, a in enumerate(corr.index):
        for b in corr.columns[i + 1:]:
            if abs(corr.loc[a, b]) >= 0.7:
                print(f"  {a} ↔ {b}: ρ = {corr.loc[a, b]:+.3f}", flush=True)
                found = True
    if not found:
        print("  （无）", flush=True)

    u = reports[0].universe
    print(f"\n票池诊断：n_ended_early={u['n_ended_early']} "
          f"no_symbol_ever_left={u['no_symbol_ever_left']}", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    main(int(args[0]) if args else None)
