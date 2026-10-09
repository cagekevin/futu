"""M5 评估 —— BH 多重检验校正 + **判决出口**（承 Q5 / J3）。

## ★ 为什么判决必须过 BH

试 20 个因子，最好的那个 `p = 0.03` 看起来显著 —— 但 `0.05 × 20 = 1`，
**期望就有 1 个假阳性**。不校正就报「找到 alpha」= 自欺。

**你在 `backtest` 里已有这条判据**：V18「报 p 值必报检验次数 →
判决只经 `benjamini_hochberg`（无裸判出口）」。

## ★ 为什么入口是 `judge_batch`（**一批**）而不是 `judge`（单个）

BH 的分母是「**这一批试了多少个因子**」。只拿一个 p 值做"BH"在**数学上是错的**：

> 把同一个 p 重复 `m` 次再跑 BH —— `p_(m) = p ≤ m/m × α = α` 恒成立
> ⇒ **BH 退化成"不校正"**，且不会报错。

（这是本模块写码时被测试抓到的真 bug：`test_j3_bh_correction_actually_changes_verdict`。）
⇒ 唯一正确的做法是**收齐全批的 p 值，一次校正**。

## 为什么本层自带一份 BH（不 import `backtest/panel_statistics.py`）

承 PRD §二 E6：`backtest/panel_statistics.py` 的 fan-in **曾是 1（仅自测）** ——
主链路从未用过它。（**2026-10-09 起两点变化**：① 它已被 `run_tugboat.py` 接线；
② 它**已改名** —— 原 `statistics.py`，与 Python 标准库同名的歧义**已消除**，见 TD-05-18。
但本层**仍不 import 它**，理由不变：承 §7.2「并列下游互不 import」。）
按 §7.2（并列下游互不 import），本层自带一份，一致性由**跨层契约测试**锁死。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

__all__ = [
    "benjamini_hochberg", "JudgeEntry", "JudgeThresholds", "judge_batch",
    "VERDICT_SIGNIFICANT", "VERDICT_NOT_SIGNIFICANT", "VERDICT_INSUFFICIENT",
]

VERDICT_SIGNIFICANT = "significant"
VERDICT_NOT_SIGNIFICANT = "not_significant"
VERDICT_INSUFFICIENT = "insufficient_data"


def benjamini_hochberg(pvalues: Sequence[float], *, alpha: float) -> list[bool]:
    """BH 校正 → 每个 p 值是否通过（**保持输入顺序**）。

    步骤（Benjamini–Hochberg 1995）：
      1. p 升序排；
      2. 找**最大**的 `k` 使 `p_(k) ≤ k/m × alpha`；
      3. 排名 `≤ k` 的全部拒绝。

    ⚠️ 第 2 步的「**最大** k」不是"第一个不满足就停" ——
       中间夹一个大 p 值不该阻断它前面的（见 `test_bh_known_case_max_k_rule`）。
    ⚠️ `alpha` 必须显式传（承 Q4：口径消耗必填，无默认）。
    ⚠️ `NaN` 的 p 值 → **报错**，不许静默丢弃（承 P5）。

    ## ★ NaN 为什么必须报错（2026-10-07 审计修正）

    初版对 `NaN` 是**静默跳过**（当作"不拒绝"）。审计发现两处问题：

    1. **与 `backtest/panel_statistics.py::benjamini_hochberg` 语义分歧** ——
       那边对 NaN **raise**（原文：「缺失/NaN 必须显形，不许静默丢弃（承 P5）」）。
       两处同名函数、语义不同 = **两份真相**。
    2. **掩盖了一类真实的"无法检验"** —— `p = NaN` 意味着"检验做不了"
       （IC 序列无波动 ⇒ `std = 0`），把它当成"不显著"是把
       **"没证据"** 和 **"证据说无效"** 混为一谈。

    ⇒ 现在：**无法检验的条目不许进 family**（由 `judge_batch` 先标
       `insufficient_data`），混进来就报错。
    """
    if not (isinstance(alpha, (int, float)) and 0 < alpha < 1):
        raise ValueError(f"alpha 必须在 (0, 1)：{alpha!r}（承 Q4）")
    values = list(pvalues)
    m = len(values)
    if m == 0:
        raise ValueError("pvalues 为空 —— 没有检验就没有校正（承 P1：缺就是缺）")
    for index, value in enumerate(values):
        if value != value:
            raise ValueError(
                f"pvalues[{index}] 是 NaN —— 缺就是缺，**不许静默丢弃**（承 P5）。"
                f"无法检验的条目应由 `judge_batch` 先标为 insufficient_data，"
                f"不要混进 family（否则分母虚高）"
            )

    order = sorted(range(m), key=lambda i: values[i])
    cutoff_rank = 0
    for rank, index in enumerate(order, start=1):
        if values[index] <= rank / m * alpha:
            cutoff_rank = rank
    # ⚠️ 必须按**原始下标**回填 —— 直接 `for ... in order` 建列表会得到
    #    "按 p 值排序"的结果，调用方拿到的顺序全错（这是被测试抓到的真 bug）。
    passed = [False] * m
    for rank, index in enumerate(order, start=1):
        passed[index] = rank <= cutoff_rank
    return passed


@dataclass(frozen=True)
class JudgeThresholds:
    """判决阈值 —— **全部必填、无默认**（承 Q4 / T4 的同一判据）。

    ## ★ 经济幅度门槛（2026-10-08 审计补）

    PRD §6.4 写换手必报的理由是「IC 显著但月换手 300% 的因子**扣成本后可能为负**」——
    但初版判决**只报了换手，从未用它**，于是：

    > **一个统计显著、但扣成本后为负的因子，会被判成「显著」。**

    实测（全窗口 1108 个有效日）：`z_neu_vol20` 判为 `significant`，
    但多空日均仅 **+0.00003**、日均换手 **0.144** ⇒ 保本成本只有 **2.1 bp**
    —— 低于真实成本即**净亏损**。

    同类的还有「**单调性对线性缩放不变**」：`z_neu_ret60` 的单调性是
    **−1.000（完美）**，而 5 个分位的平均收益全距只有 **0.00015**（1.5 bp/日）。
    只判形状不判幅度 ⇒ 空壳因子照样过。

    ⇒ 新增门槛：**扣成本后年化收益 ≥ `min_net_annual_return`**。

    ⚠️ **成本假设必须显式传入**（承 Q4：口径消耗必填）——
       我不替使用者假设交易成本，那是**他的**事实，不是代码的默认值。
    """

    alpha: float
    min_days: int
    min_abs_icir: float
    min_abs_monotonicity: float
    #: 换手 1.0 的成本（**基点**，单边）。换手按 `Σ|Δw|` 计，见 `metrics/turnover.py`。
    cost_bps_per_turnover: float
    #: 扣成本后年化收益的下限（如 `0.0` = 至少要打平）。
    min_net_annual_return: float
    #: 年化天数（美股 ~250；不同市场不同 ⇒ 必填，不写死）。
    annualization_days: int

    def __post_init__(self) -> None:
        if not (isinstance(self.alpha, (int, float)) and 0 < self.alpha < 1):
            raise ValueError(f"alpha 必须在 (0, 1)：{self.alpha!r}")
        if not isinstance(self.min_days, int) or self.min_days < 2:
            raise ValueError(f"min_days 必须是 ≥2 的整数：{self.min_days!r}")
        if self.min_abs_icir < 0 or self.min_abs_monotonicity < 0:
            raise ValueError("阈值不能为负")
        if self.cost_bps_per_turnover < 0:
            raise ValueError(
                f"cost_bps_per_turnover 不能为负：{self.cost_bps_per_turnover!r}"
            )
        if not isinstance(self.annualization_days, int) or self.annualization_days < 1:
            raise ValueError(
                f"annualization_days 必须是 ≥1 的整数：{self.annualization_days!r}"
            )

    @property
    def cost_rate_per_turnover(self) -> float:
        """成本率（小数）—— `bps / 10000`，**只在这里换算一次**。"""
        return self.cost_bps_per_turnover / 10000.0


@dataclass(frozen=True)
class JudgeEntry:
    """一个待判决的因子 —— `judge_batch` 的输入单元。

    ⚠️ `long_short_mean` / `turnover_mean` 是**必填**（2026-10-08 审计补）：
       经济幅度类指标必须进入判决，否则"统计显著但净亏损"会被判成显著。
    """

    name: str
    p_value: float
    icir: float
    n_days: int
    monotonicity: float
    #: 多空组合的**日均**收益（已按 `direction` 归一，见 `evaluator.py`）。
    long_short_mean: float
    #: **日均**换手（`Σ|Δw|`）。
    turnover_mean: float


def _testable(entry: JudgeEntry, thresholds: JudgeThresholds) -> bool:
    """这条能不能做检验：**天数够** 且 **p 值不是 NaN**（IC 有波动）。

    ⚠️ 两者缺一就是"检验做不了"，与"检验做了但没通过"是**两件事**
       （审计修正：初版把 p=NaN 当成 `not_significant`，
        等于把"没证据"说成"证据说无效"）。
    """
    return (entry.n_days >= thresholds.min_days
            and entry.p_value == entry.p_value)


def judge_batch(entries: Sequence[JudgeEntry], *,
                thresholds: JudgeThresholds) -> list[dict[str, Any]]:
    """**判决出口** —— 一次 BH 校正**整批**，每个因子一条判决（承 Q5 / J3）。

    三个门槛**全部**满足才判「显著」：
      1. 能检验（天数 ≥ `min_days` **且** 有 p 值）——
         不能 → `insufficient_data`，**不硬判**，也不假装"不显著"
      2. **BH 校正后**仍显著（承 Q5）
      3. `|ICIR| ≥ min_abs_icir` **且** `|单调性| ≥ min_abs_monotonicity`

    ⚠️ **无法检验的条目不进 BH family** —— 它们没有 p 值，混进去会让分母虚高
       （而 `benjamini_hochberg` 现在对 NaN 直接报错，承 P5）。
       实际进 family 的个数以 `n_tests_in_bh` 显形。

    ⚠️ 承 J3：每条输出**必含** `n_tests` 与校正后 p 值 —— 没有这两个数，
       "显著"是无法被复核的。

    ⚠️ **第 4 条门槛（2026-10-08 审计补）**：扣成本后年化收益 ≥
       `min_net_annual_return`。前三门只判"是不是真的"，不判"值不值得做"。
    """
    entries = list(entries)
    n_tests = len(entries)
    if n_tests == 0:
        return []

    testable_index = [i for i, e in enumerate(entries) if _testable(e, thresholds)]
    flags = benjamini_hochberg([entries[i].p_value for i in testable_index],
                               alpha=thresholds.alpha) if testable_index else []
    passed = dict(zip(testable_index, flags))

    out: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        bh_ok = passed.get(index, False)
        cost = entry.turnover_mean * thresholds.cost_rate_per_turnover
        net_annual = (entry.long_short_mean - cost) * thresholds.annualization_days
        gross_annual = entry.long_short_mean * thresholds.annualization_days
        if not _testable(entry, thresholds):
            verdict = VERDICT_INSUFFICIENT
        elif (bh_ok
              and abs(entry.icir) >= thresholds.min_abs_icir
              and abs(entry.monotonicity) >= thresholds.min_abs_monotonicity
              and net_annual >= thresholds.min_net_annual_return):
            verdict = VERDICT_SIGNIFICANT
        else:
            verdict = VERDICT_NOT_SIGNIFICANT
        out.append({
            "name": entry.name,
            "verdict": verdict,
            # ★ J3：这两个数**必须**在输出里
            "n_tests": n_tests,
            "n_tests_in_bh": len(testable_index),
            "p_value_raw": float(entry.p_value),
            "p_value_bh_significant": bool(bh_ok),
            "alpha": float(thresholds.alpha),
            # 各项门槛的实测值（复核用）
            "n_days": int(entry.n_days),
            "min_days": int(thresholds.min_days),
            "icir": float(entry.icir),
            "min_abs_icir": float(thresholds.min_abs_icir),
            "monotonicity": float(entry.monotonicity),
            "min_abs_monotonicity": float(thresholds.min_abs_monotonicity),
            # ★ 经济幅度门槛的实测值（复核用）—— 与上面四组同等待遇
            "gross_annual_return": float(gross_annual),
            "cost_annual_return": float(cost * thresholds.annualization_days),
            "net_annual_return": float(net_annual),
            "min_net_annual_return": float(thresholds.min_net_annual_return),
            "cost_bps_per_turnover": float(thresholds.cost_bps_per_turnover),
            "turnover_mean": float(entry.turnover_mean),
        })
    return out
