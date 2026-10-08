"""全窗口实跑：M1 → M2 → M3 → M4 → M5，并把「89 天 vs 全窗口」并排输出。

用途：审计后确认「不显著」是否只是**样本太短**（89 天时 ret60 仅 27 个有效日）。

跑法：.venv/bin/python run_full_window.py [--days N]
"""
from __future__ import annotations

import sys
import time

import factor.implementations  # noqa: F401  —— 导入即注册
from evaluate.evaluator import EvaluateConfig, evaluate_many
from evaluate.judgement import JudgeThresholds
from exposure.exposure_builder import read_exposures
from factor.factor_registry import alpha_factors, run_factor
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
        # ⚠️ 成本是**使用者的事实**，不是代码的默认值（承 Q4）——
        #    这里 5bp 只是一个**示例假设**，换市场/换券商就该改它。
        cost_bps_per_turnover=5.0,
        min_net_annual_return=0.0,     # 至少要打平
        annualization_days=250,
    ),
)


def run(n_days: int | None) -> list:
    days = read_days()
    stocks = read_stocks("2026-09-30")
    symbols = stocks["stocks"]
    window = days if n_days is None else days[-n_days:]
    label = "全窗口" if n_days is None else f"{n_days} 天"
    print(f"=== {label}：{len(window)} 天 × {len(symbols)} 只 ===", flush=True)

    t0 = time.time()
    panel = read_panel(symbols, days=window, stocks_day="2026-09-30")
    t1 = time.time()
    exposures = read_exposures(panel)
    t2 = time.time()
    prepared = [read_preprocessed_factor(run_factor(name, panel), exposures,
                                         PREPROCESS)
                for name in alpha_factors()]         # 只跑 alpha（见 factor_spec 的 role）
    t3 = time.time()
    reports = evaluate_many(prepared, panel, config=EVALUATE)
    t4 = time.time()
    print(f"  M1 {t1-t0:.0f}s | M2 {t2-t1:.0f}s | M3+M4 {t3-t2:.0f}s "
          f"| M5 {t4-t3:.0f}s | 合计 {t4-t0:.0f}s", flush=True)

    for report in reports:
        stats, verdict = report.ic_stats, report.judgement
        print(f"  {report.factor_name:14s} dir={report.direction:+d} "
              f"ICIR={stats['icir']:+.3f} p={stats['p_value']:.3g} "
              f"天={stats['n_days']:4d} 单调={report.monotonicity:+.3f}",
              flush=True)
        print(f"      经济面：毛年化={verdict['gross_annual_return']:+.2%} "
              f"成本={verdict['cost_annual_return']:.2%} "
              f"**净年化={verdict['net_annual_return']:+.2%}** "
              f"(换手={verdict['turnover_mean']:.3f}/日, "
              f"成本假设={verdict['cost_bps_per_turnover']:.1f}bp)",
              flush=True)
        print(f"      → {verdict['verdict']}", flush=True)
    universe = reports[0].universe
    print(f"  票池诊断：n_symbols_ever={universe['n_symbols_ever']} "
          f"n_ended_early={universe['n_ended_early']} "
          f"no_symbol_ever_left={universe['no_symbol_ever_left']}", flush=True)
    return reports


if __name__ == "__main__":
    arg = sys.argv[1:] if len(sys.argv) > 1 else []
    n = int(arg[0]) if arg else None
    run(n)
