"""诊断：为什么 `z_neu_ret60` 的 ICIR 为正、而多空收益为负？（符号矛盾）

审计原则：**结果太整齐（单调性 = −1.000）或自相矛盾时，先怀疑自己**。
"""
from __future__ import annotations

import numpy as np

import factor.implementations  # noqa: F401
from evaluate.forward_return import forward_return
from evaluate.metrics.ic import rank_ic_series
from evaluate.metrics.quantile_returns import quantile_returns
from exposure.exposure_builder import read_exposures
from factor.factor_registry import run_factor
from panel.panel_builder import read_panel
from panel.provide_reader import read_days, read_stocks
from preprocess.preprocess_pipeline import PreprocessConfig, read_preprocessed_factor

CONFIG = PreprocessConfig(
    winsorize_method="mad", winsorize_n=5.0,
    impute_method="cross_section_median", impute_threshold=0.5,
    standardize_method="zscore", neutralize_method="size_industry",
)


def main() -> None:
    days = read_days()
    symbols = read_stocks("2026-09-30")["stocks"]
    panel = read_panel(symbols, days=days, stocks_day="2026-09-30")
    exposures = read_exposures(panel)
    values = read_preprocessed_factor(run_factor("ret60", panel), exposures, CONFIG)
    target = forward_return(panel)

    ic = rank_ic_series(values.values, target, min_samples=30)
    qmean, qcounts, long_short, skipped = quantile_returns(
        values.values, target, bins=5, min_samples=30)

    print(f"IC 有效天 {ic['rank_ic'].notna().sum()} | 分位有效天 {len(qmean)} "
          f"| 分位跳过 {len(skipped)} 天")
    print(f"mean(rank_ic)      = {ic['rank_ic'].mean():+.5f}")
    print(f"mean(long_short)   = {long_short.mean():+.5f}")
    print(f"corr(rank_ic, long_short)（同日）= "
          f"{ic['rank_ic'].corr(long_short.reindex(ic.index)):+.3f}")

    profile = qmean.mean(axis=0)
    print("\n分位平均收益（0 = 因子值最小）:")
    for i, v in profile.items():
        print(f"  分位 {i}: {v:+.5f}")
    print(f"单调性（Spearman）= {profile.reset_index(drop=True).corr(
        __import__('pandas').Series(range(len(profile))), method='spearman'):+.3f}")

    print("\n每组平均样本数:", qcounts.mean().round(1).to_dict())

    # 逐日一致性：同一天里 IC 与 long_short 的符号是否同向
    both = ic["rank_ic"].notna() & long_short.reindex(ic.index).notna()
    same = (np.sign(ic["rank_ic"][both]) == np.sign(long_short.reindex(ic.index)[both]))
    print(f"\n同日 IC 与多空**同号**的比例 = {same.mean():.1%}（{both.sum()} 天）")
    print("  ⇒ 若接近 50%，说明「IC 正」与「多空负」不是矛盾，而是两者本就测不同的东西")


if __name__ == "__main__":
    main()
