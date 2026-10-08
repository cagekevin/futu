"""独立审计（承 V1）：**目标独立** —— 判决只认 PnL + 硬规则，不掺搜索期奖励。

根因（V1）：如果审计用的**目标函数**和训练搜索用的是同一个，
那么"训练时钻的评分空子"在审计时会**再次被钻**，你永远发现不了自欺。
必须分离的是**目标**，不是**实现**：
  - 训练侧：用于**搜索**（可以有惩罚项、门控、启发式）；
  - 审计侧：用于**判决**（只有纯粹的 PnL + 硬判定）。

★ 关于"共享 PnL 函数"（易错点）：
  - PnL 公式全系统唯一（R1）→ 生产路径**必须共享**（两个实现 = 两个真相，更糟）；
  - 但**共享不产生独立性** —— 同一个函数**不能自证**（拿 per_bar_pnl 验 per_bar_pnl 恒真）；
  - ⇒ 独立证据必须来自**带外**：手算期望值的已知答案测试 + **污染未来测试**（见 tests/）。
    本模块的"独立"在于：① 判决**不 import** 任何评分/搜索模块；② 证据在 tests/ 带外。

★ 评估窗口（修一.1）：**所有**统计都基于**同一个**区间 `[EVALUATION_WARMUP, n)` ——
指标 / 前后半段 / 单边占比 / 分段一致性，一处也不多、一处也不少。
根因：若某处算全样本、某处排除 warm-up，年化会被稀释、单边占比会被摊薄，
恰好在 V3 该抓 beta 时漏判。
"""
from __future__ import annotations

from collections.abc import Sequence

import backtest_config
import performance_metrics
import pnl_engine
import walk_forward_validation


def audit_strategy(positions: Sequence[float], target_returns: Sequence[float],
                   times: Sequence[int], *, label: str = "strategy") -> dict:
    """对一个策略做完整审计，返回报告 dict（不落盘数据，只出结论）。"""
    n = len(positions)
    if len(target_returns) != n or len(times) != n:
        raise ValueError("positions / target_returns / times 长度必须一致")

    warmup = backtest_config.EVALUATION_WARMUP
    if n - warmup < 2:
        raise ValueError(f"样本 {n} 根不足以评估（warm-up={warmup}）（承 P1）")

    # ① 基准成本 / ② 成本压力 2x（承 V4）—— 先在**全样本**上算，再统一切到评估区间
    full_pnl = pnl_engine.per_bar_pnl(positions, target_returns, backtest_config.COST_RATE)
    full_pnl_2x = pnl_engine.per_bar_pnl(
        positions, target_returns, backtest_config.COST_RATE * 2.0
    )

    # ★ 唯一的评估区间：前 warmup 根不参与任何统计
    pnl = full_pnl[warmup:]
    pnl_2x = full_pnl_2x[warmup:]
    evaluated_positions = positions[warmup:]
    evaluated_times = times[warmup:]
    m = len(pnl)

    periods_per_year_ = performance_metrics.periods_per_year(evaluated_times)

    # ③ 前后半段（承 V3：异号 → 过拟合嫌疑）
    half = m // 2
    h1_ann = performance_metrics.annualized_return(pnl[:half], periods_per_year_)
    h2_ann = performance_metrics.annualized_return(pnl[half:], periods_per_year_)

    # ④ 分段一致性（承 V2）—— 折的 val 段铺满评估区间（用全样本坐标索引）
    wf_positive = wf_total = 0
    for fold in walk_forward_validation.segment_consistency_folds(n):
        val_start, val_end = fold["val"]
        segment = full_pnl[val_start:val_end]
        if segment:
            wf_total += 1
            if performance_metrics.annualized_return(segment, periods_per_year_) > 0:
                wf_positive += 1

    # ⑤ 指标（全部基于评估区间）
    ann = performance_metrics.annualized_return(pnl, periods_per_year_)
    sharpe = performance_metrics.sharpe_ratio(pnl, periods_per_year_)
    sortino = performance_metrics.sortino_ratio(pnl, periods_per_year_)
    mdd = performance_metrics.max_drawdown(pnl)
    long_ratio, short_ratio = performance_metrics.long_short_ratio(evaluated_positions)
    max_side = max(long_ratio, short_ratio)
    trades = performance_metrics.count_trades(evaluated_positions)

    # ⑥ 判决（承 V3；8 条规则**全部**在 judge_verdict 一处）
    min_trades = max(5, int(m * backtest_config.MIN_TRADES_PER_100_BARS))
    verdict, issues = walk_forward_validation.judge_verdict(
        ann_ret=ann, sharpe=sharpe, mdd=mdd, max_side=max_side,
        h1_ann=h1_ann, h2_ann=h2_ann,
        wf_positive=wf_positive, wf_total=wf_total,
        cost2x_profitable=performance_metrics.annualized_return(
            pnl_2x, periods_per_year_
        ) > 0,
        n_trades=trades, min_trades=min_trades,
    )

    return {
        "label": label, "n_bars": m, "n_bars_total": n, "warmup": warmup,
        "ppy": periods_per_year_,
        "ann_ret": ann, "sharpe": sharpe, "sortino": sortino, "mdd": mdd,
        "long_ratio": long_ratio, "short_ratio": short_ratio, "max_side": max_side,
        "n_trades": trades,
        "h1_ann": h1_ann, "h2_ann": h2_ann,
        "wf_positive": wf_positive, "wf_total": wf_total,
        "cost2x_ann": performance_metrics.annualized_return(pnl_2x, periods_per_year_),
        "verdict": verdict, "issues": issues,
    }


def format_audit_report(report: dict) -> str:
    """报告 → 文本（回测层唯一的"产出"，是结论不是数据）。"""
    lines = [
        f"══ 审计报告：{report['label']} ══",
        f"  评估 bar 数   : {report['n_bars']}"
        f"（总 {report['n_bars_total']}，前 {report['warmup']} 根 warm-up 不计）"
        f"   每年 bar 数: {report['ppy']}",
        f"  年化          : {report['ann_ret']*100:+.2f}%",
        f"  Sharpe        : {report['sharpe']:+.3f}",
        f"  Sortino       : {report['sortino']:+.3f}",
        f"  MDD           : {report['mdd']*100:.2f}%",
        f"  多/空占比     : {report['long_ratio']*100:.1f}% / {report['short_ratio']*100:.1f}%"
        f"  (max_side {report['max_side']*100:.1f}%)",
        f"  交易笔数      : {report['n_trades']}",
        f"  前后半段年化  : H1 {report['h1_ann']*100:+.2f}%  H2 {report['h2_ann']*100:+.2f}%",
        f"  分段为正      : {report['wf_positive']}/{report['wf_total']} 段",
        f"  2x 成本年化   : {report['cost2x_ann']*100:+.2f}%",
        f"  判定          : {report['verdict']}",
    ]
    for issue in report["issues"]:
        lines.append(f"    ! {issue}")
    if not report["issues"]:
        lines.append("    （全部检查通过）")
    return "\n".join(lines)
