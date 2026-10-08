"""验证层：分段一致性（承 V2/B5）+ 8 条硬判定（承 V3）。

判决**只用 PnL + 硬规则**，不掺任何"搜索期的启发式加分"（承 V1）。
"""
from __future__ import annotations

import backtest_config


# ── 分段一致性 + purge gap（承 V2 / B5）─────────────────────────────────
# ⚠️ 名字要诚实：固定策略**没有训练**，所以这不是真正的 Walk-Forward
# （WF = 在 train 上拟合、在 val 上评）。这里 `train` 段**从不被使用**，
# `gap` 也因此**是空操作**。它真实的作用是：把评估区间切成连续 N 段、数"几段为正"
# —— 即**分段一致性检验**。将来引入训练/参数搜索后，再升级为真 WF。

def segment_consistency_folds(n_bars: int, *, n_folds: int = backtest_config.WF_FOLDS,
                              gap: int = backtest_config.WF_GAP,
                              warmup: int = backtest_config.EVALUATION_WARMUP) -> list[dict]:
    """把**评估区间** `[warmup, n_bars)` 均分成 `n_folds` 段（val），供"分段一致性"统计。

    ★ 与初版的差别（修 C1）：初版 `range(1, n_folds)` 只产出 `n_folds-1` 段，
    且**第一块 `[warmup, warmup+block)` 从不参与** —— 静默丢了 1/n 的数据。
    现在**铺满全区间**：val 段 tiling 整个 `[warmup, n_bars)`，一段都不漏。

    fold k 的 train = `[warmup, val_start - gap)`（purge gap），供**将来**引入训练时直接用
    —— 标签是 log(open[t+2]/open[t+1])，train 尾部 gap 根的标签会看到 val，不丢就是泄漏。
    固定策略下 train 不被使用；第 1 段 train 为空（`train[1] == train[0]`），属正常。
    """
    if n_folds < 2:
        raise ValueError("n_folds 至少 2")
    span = n_bars - warmup
    if span <= 0:
        raise ValueError(f"样本 {n_bars} 根不足以容纳 warmup={warmup}（承 P1）")
    block = span // n_folds
    if block <= gap:
        raise ValueError(
            f"每块 {block} bar 不足以容纳 gap={gap}（承 B5：fold_size 应 >> gap）"
        )
    folds: list[dict] = []
    for k in range(n_folds):
        val_start = warmup + k * block
        val_end = n_bars if k == n_folds - 1 else warmup + (k + 1) * block
        train_end = max(warmup, val_start - gap)     # ← purge；第 1 段为空
        folds.append({
            "fold": k + 1,
            "train": (warmup, train_end),
            "val": (val_start, val_end),
            "gap": gap,
        })
    return folds


# ── 8 条硬判定（承 V3）──────────────────────────────────────────────────

# 判级序：取最严重的一条。公开此表，供审计层复用（不重复定义口径）。
VERDICT_ORDER = {"VALID": 0, "SUSPICIOUS": 1, "INVALID": 2}


def judge_verdict(*, ann_ret: float, sharpe: float, mdd: float, max_side: float,
                  h1_ann: float, h2_ann: float,
                  wf_positive: int, wf_total: int,
                  cost2x_profitable: bool,
                  n_trades: int, min_trades: int) -> tuple[str, list[str]]:
    """**全部 8 条**硬判定（承 V3）集中在这里。返回 `(verdict, issues)`。

    `verdict ∈ {VALID, SUSPICIOUS, INVALID}`，取最严重的一条。
    根因：**光看年化会骗人** —— 一个 91% 做多、年化 +7.68% 的因子看着"还行"，
    但那只是美股自己涨；8 条规则一起上才暴露"它是 beta"。

    ★ 为什么 V6（交易数门槛）也在这里（修一.2）：判决规则**只能有一处**，
    否则改一条规则要动两个文件、两处降级逻辑各写一遍 → 迟早漂移（B1 同类错误）。
    """
    verdict = "VALID"
    issues: list[str] = []

    def downgrade(level: str) -> None:
        nonlocal verdict
        if VERDICT_ORDER[level] > VERDICT_ORDER[verdict]:
            verdict = level

    if ann_ret < backtest_config.MIN_ANN_RET:
        downgrade("INVALID")
        issues.append(f"年化 {ann_ret*100:.2f}% < {backtest_config.MIN_ANN_RET*100:.0f}%")

    if sharpe < backtest_config.MIN_SHARPE:
        downgrade("SUSPICIOUS")
        issues.append(f"Sharpe {sharpe:.3f} < {backtest_config.MIN_SHARPE}")

    if mdd > backtest_config.MDD_SUSPICIOUS:
        downgrade("SUSPICIOUS")
        issues.append(f"MDD {mdd*100:.2f}% > {backtest_config.MDD_SUSPICIOUS*100:.0f}%")
        if mdd > backtest_config.MDD_INVALID:
            downgrade("INVALID")
            issues.append(f"MDD {mdd*100:.2f}% > {backtest_config.MDD_INVALID*100:.0f}%")

    if max_side > backtest_config.MAX_SIDE_RATIO:
        downgrade("SUSPICIOUS")
        issues.append(
            f"单边占比 {max_side*100:.1f}% > "
            f"{backtest_config.MAX_SIDE_RATIO*100:.0f}%（疑似 beta）"
        )

    if h1_ann * h2_ann < 0:
        downgrade("SUSPICIOUS")
        issues.append(f"前后半段异号 H1={h1_ann*100:+.2f}% H2={h2_ann*100:+.2f}%")

    if wf_total and wf_positive < wf_total * 0.5:
        downgrade("SUSPICIOUS")
        issues.append(f"分段仅 {wf_positive}/{wf_total} 段为正")

    if not cost2x_profitable:
        downgrade("SUSPICIOUS")
        issues.append("2x 成本下亏损")

    # V6：交易数不足 → **INVALID**。
    # ⚠️ 这条标准在 01 §4.2/§4.4（"视为无效"）与 02 §7（"加 SUSPICIOUS"）里
    #    **各写了一份、且互相打架**。以 **01** 为准，根因是这条规则的用途：
    #    交易 3 次 → Sharpe/Sortino 全是统计噪声 → **根本判不了**；
    #    判不了就不能说"可能还行"，只能说"无效"。
    # ⇒ **本处代码即最终标准**（02 是手册，其文件名已与实现分叉，只作参考）。
    if n_trades < min_trades:
        downgrade("INVALID")
        issues.append(f"交易 {n_trades} 笔 < 门槛 {min_trades}（承 V6：样本不足，无法判决）")

    return verdict, issues
