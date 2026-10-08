"""R3 配对判决 —— 逐日配对 + 三道判据 + 三档判决（承 PRD §五 R3 / §6.4）。

## 为什么是「配对」而不是「两组取平均」（承 Z1）

同一天所有股票**共享同一次市场冲击**（大盘急跌那天，什么方法都难看）。
若把"真实的所有日"和"陪考的所有日"各取总平均再比，
比出来的东西里**混着"哪天行情好"**，不全是选股能力。

配对把日期效应**消掉**：

    delta[t] = real[t] − mean_r(null[t][r])      ← 只在"同一天"内部比
    判决只建立在 { delta[t] } 之上

## 「排位」怎么算才对（承 §6.4）

不是"真实 vs 全部随机值"一锅端，而是**平行世界**：

    第 r 个平行世界 = 「每一天都改用随机选」→ null_avg[r] = mean_t( null[t][r] )
    真实的成绩      = mean_t( real[t] )
    排位            = 真实成绩落在 { null_avg[r] } 里的位置

这样每个 `r` 都是一个**内部自洽**的"如果全是瞎选"，与真实**逐日对齐**。

## 一个必须说清的推论（**成本对配对差值无效**）

    (real − c) − (null − c) = real − null = delta

⇒ **扣成本不会改变配对差值**。所以 §6.4 的第三条判据**不能**看 delta，
   只能看「**真实侧扣成本后还剩多少**」（绝对收益）。
   本文件把两件事**分别报出**，不混（承 Z2）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

__all__ = [
    "JUDGE_RULE_FROZEN_AT",
    "RANK_THRESHOLD",
    "VERDICT_FAIL",
    "VERDICT_PASS",
    "VERDICT_UNCLEAR",
    "PairedSummary",
    "judge",
    "paired_deltas",
    "rank_percentile",
    "summarize",
]

# ── §6.4 判决规则（**冻结**，改动 = 开新实验）────────────────────────────

#: 「通过」所需的排位下限（%）。**只许在此定义一次**（承 C4）。
RANK_THRESHOLD: float = 95.0

#: 规则写进契约的时刻 —— 进报告，回答"当时用的是哪一版规则"（承 C4）。
JUDGE_RULE_FROZEN_AT: str = "2026-10-08"

VERDICT_PASS = "通过"
VERDICT_UNCLEAR = "说不清"
VERDICT_FAIL = "不通过"


def paired_deltas(real: np.ndarray, null_matrix: np.ndarray) -> np.ndarray:
    """逐日配对差值 `delta[t] = real[t] − mean_r(null[t][r])`（承 Z1）。

    `real` 形状 `(T,)`；`null_matrix` 形状 `(T, R)`。
    """
    real = np.asarray(real, dtype=float)
    null_matrix = np.asarray(null_matrix, dtype=float)
    if null_matrix.ndim != 2:
        raise ValueError(f"null_matrix 必须是 (T, R) 二维，收到 {null_matrix.shape}")
    if real.shape != (null_matrix.shape[0],):
        raise ValueError(
            f"real 形状 {real.shape} 与 null_matrix 的 T={null_matrix.shape[0]} 不一致"
        )
    return real - null_matrix.mean(axis=1)


def rank_percentile(real: np.ndarray, null_matrix: np.ndarray) -> float:
    """真实成绩落在「全随机平行世界」分布的第几百分位（0–100）。

    每个 `r` = 一个"每天都改用随机选"的平行世界；真实与它在**同一天**对齐（承 Z1）。
    """
    real = np.asarray(real, dtype=float)
    null_matrix = np.asarray(null_matrix, dtype=float)
    if real.size == 0:
        raise ValueError("没有有效交易日，无法计算排位")
    parallel_worlds = null_matrix.mean(axis=0)          # (R,)
    observed = float(real.mean())
    # 用"严格小于"的占比：真实恰好等于某个平行世界时**不算赢**（保守）
    return float((parallel_worlds < observed).mean() * 100.0)


@dataclass(frozen=True)
class PairedSummary:
    """配对汇总（报告的原料，不含任何判决）。"""

    n_days_valid: int
    k_median: float
    pool_median: float
    real_mean: float
    null_mean: float
    delta_mean: float
    delta_median: float
    rank_pct: float
    win_days_ratio: float
    first_half_delta: float
    second_half_delta: float
    real_mean_after_cost: float
    null_mean_after_cost: float


def summarize(
    real: np.ndarray,
    null_matrix: np.ndarray,
    *,
    k_per_day: Sequence[int],
    pool_per_day: Sequence[int],
    cost_rate: float,
) -> PairedSummary:
    """算齐 §6.2 要求的每一个数（**缺一即报错**，承 Z2/Z4）。

    `cost_rate` 是**单边**成本；一次完整往返（买 + 卖）记 `2 × cost_rate`。
    """
    if cost_rate < 0:
        raise ValueError(f"cost_rate 不能为负：{cost_rate}（承 Z3）")

    real = np.asarray(real, dtype=float)
    null_matrix = np.asarray(null_matrix, dtype=float)
    if real.size == 0:
        raise ValueError("没有任何有效交易日 —— 无法出报告（承 Z4：样本量必须显形）")

    deltas = paired_deltas(real, null_matrix)
    half = real.size // 2
    roundtrip_cost = 2.0 * cost_rate

    return PairedSummary(
        n_days_valid=int(real.size),
        k_median=float(np.median(np.asarray(k_per_day, dtype=float))),
        pool_median=float(np.median(np.asarray(pool_per_day, dtype=float))),
        real_mean=float(real.mean()),
        null_mean=float(null_matrix.mean()),
        delta_mean=float(deltas.mean()),
        delta_median=float(np.median(deltas)),
        rank_pct=rank_percentile(real, null_matrix),
        win_days_ratio=float((deltas > 0).mean()),
        first_half_delta=float(deltas[:half].mean()) if half else float("nan"),
        second_half_delta=float(deltas[half:].mean()) if half else float("nan"),
        real_mean_after_cost=float(real.mean() - roundtrip_cost),
        null_mean_after_cost=float(null_matrix.mean() - roundtrip_cost),
    )


def judge(
    *,
    rank_pct: float,
    first_half_delta: float,
    second_half_delta: float,
    real_mean_after_cost: float,
) -> tuple[str, tuple[str, ...]]:
    """三档判决（承 §6.4，**规则冻结**）。

    三条判据（**同时**看）：

    ① 排位 `≥ RANK_THRESHOLD`
    ② 前半段与后半段的配对差值**都为正**
    ③ **真实侧扣成本后为正**（不是看 delta —— 成本对 delta 无效，见模块 docstring）

    返回 `(verdict, issues)`；`issues` 列出**没满足的那几条**（显形，不掩盖）。
    """
    issues: list[str] = []

    cond_rank = rank_pct >= RANK_THRESHOLD
    if not cond_rank:
        issues.append(
            f"排位 {rank_pct:.1f}% < {RANK_THRESHOLD:.0f}%（未能明显优于随机）"
        )

    cond_halves = first_half_delta > 0 and second_half_delta > 0
    if not cond_halves:
        issues.append(
            f"前后半段未同为正（前半 {first_half_delta:+.4f}，后半 {second_half_delta:+.4f}）"
        )

    cond_cost = real_mean_after_cost > 0
    if not cond_cost:
        issues.append(f"扣成本后不赚钱（{real_mean_after_cost:+.4f}）")

    if cond_rank and cond_halves and cond_cost:
        return VERDICT_PASS, ()

    # 异号（只在某一段成立）→ 直接「不通过」（承 §6.4 + `10-测试` §五）
    if first_half_delta * second_half_delta < 0:
        issues.append("前后半段**异号** —— 只在某一段时间成立")
        return VERDICT_FAIL, tuple(issues)

    satisfied = sum((cond_rank, cond_halves, cond_cost))
    if satisfied == 0:
        return VERDICT_FAIL, tuple(issues)
    return VERDICT_UNCLEAR, tuple(issues)
