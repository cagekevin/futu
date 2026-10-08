"""R3 报告 —— 结构与渲染（本能力的**唯一产出物**）。

承 PRD §6.2：**字段定死，缺一即报错**（Z2/Z4）。
报告是**结论**，不是数据 —— 故它不出现在数据层的库里，只被打印 / 返回。

## 三条纪律（承 PRD）

1. **不输出二元结论**（承 C4）：出的是**三档** + **离门槛多远**，不是"显著/不显著"。
2. **两侧的数都报、不混**（承 Z2）：
   `delta_mean`（相对优势）与 `real_mean`（绝对收益）**分列两行** ——
   否则读者分不清"比随机好"和"真的赚了"。
3. **样本量必须显形**（承 Z4）：有效天数 / 每日 K / 票池规模 / **幸存者偏差告警**。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from . import paired_statistics as ps

__all__ = ["PlaceboReport", "render_report"]

#: §6.2 要求的字段（缺一 → 构造时就不该发生；`__post_init__` 兜一层）。
_REQUIRED_NUMERIC = (
    "n_days",
    "n_days_valid",
    "k_per_day_median",
    "universe_median",
    "real_mean",
    "null_mean",
    "delta_mean",
    "delta_median",
    "rank_percentile",
    "win_days_ratio",
    "first_half_delta",
    "second_half_delta",
    "cost_rate",
    "real_mean_after_cost",
)


@dataclass(frozen=True)
class PlaceboReport:
    """一次对照实验的完整结论（**不可变**）。"""

    # ── 谁被验 / 用的哪套参数（承 H3 / P3：可复现）──
    selection_name: str
    selection_params_fingerprint: str
    sampling_params_fingerprint: str

    # ── 样本量（承 Z4：必须显形）──
    n_days: int
    n_days_valid: int
    k_per_day_median: float
    universe_median: float

    # ── 两侧的绝对水平（不混，承 Z2）──
    real_mean: float
    null_mean: float
    cost_rate: float
    real_mean_after_cost: float
    null_mean_after_cost: float

    # ── 配对结果（相对优势）──
    delta_mean: float
    delta_median: float
    rank_percentile: float
    win_days_ratio: float

    # ── 三道判据的分档 ──
    first_half_delta: float
    second_half_delta: float

    # ── 判决（预注册，承 §6.4 / C4）──
    verdict: str
    rule_frozen_at: str
    issues: tuple[str, ...]

    # ── 已知偏差（承 Z4 / G-1）──
    survivorship_warning: str

    def __post_init__(self) -> None:
        if self.n_days_valid < 1:
            raise ValueError("n_days_valid 必须 ≥ 1（承 Z4：没有有效样本就不该出报告）")
        if self.n_days_valid > self.n_days:
            raise ValueError(f"有效天数 {self.n_days_valid} 不应超过总天数 {self.n_days}")
        if self.verdict not in (ps.VERDICT_PASS, ps.VERDICT_UNCLEAR, ps.VERDICT_FAIL):
            raise ValueError(
                f"判决必须是三档之一，收到 {self.verdict!r}"
                f"（承 C4：只许 {ps.VERDICT_PASS}/{ps.VERDICT_UNCLEAR}/{ps.VERDICT_FAIL}）"
            )
        if not 0.0 <= self.rank_percentile <= 100.0:
            raise ValueError(f"排位必须在 [0, 100]，收到 {self.rank_percentile}")
        if not self.survivorship_warning:
            raise ValueError("幸存者偏差告警不能为空（承 Z4 / G-1：必须显形）")


def _pct(x: float) -> str:
    return f"{x * 100:+.3f}%"


def render_report(r: PlaceboReport) -> str:
    """报告 → 文本。**唯一**的对外呈现形式。"""
    lines = [
        f"══ 随机对照报告：{r.selection_name} ══",
        f"  参数指纹      : 规则 {r.selection_params_fingerprint}"
        f"  抽样 {r.sampling_params_fingerprint}",
        "",
        "  ── 样本量（先看这个）──",
        f"  交易日        : {r.n_days_valid} / {r.n_days}（有效 / 总数）",
        f"  每日选中数    : 中位 {r.k_per_day_median:.0f} 只",
        f"  每日票池      : 中位 {r.universe_median:.0f} 只",
        "",
        "  ── 两侧的绝对水平（不混）──",
        f"  真实平均收益  : {_pct(r.real_mean)}",
        f"  陪考平均收益  : {_pct(r.null_mean)}",
        f"  单边成本      : {r.cost_rate * 100:.4f}%  "
        f"（往返 {r.cost_rate * 200:.4f}%）",
        f"  真实扣成本后  : {_pct(r.real_mean_after_cost)}   ← ★ 判据③看这个",
        f"  陪考扣成本后  : {_pct(r.null_mean_after_cost)}",
        "",
        "  ── 配对结果（相对优势，★ 同一天内比）──",
        f"  配对差值均值  : {_pct(r.delta_mean)}   ← 判据②看它",
        f"  配对差值中位  : {_pct(r.delta_median)}",
        f"  排位          : ★ {r.rank_percentile:.1f}%"
        f"（门槛 {ps.RANK_THRESHOLD:.0f}%，判据①）",
        f"  胜率          : {r.win_days_ratio * 100:.1f}%  （跑赢当天陪考的天数占比，",
        f"                  描述统计，**不作门槛**）",
        "",
        "  ── 判据②的分档 ──",
        f"  前半段        : {_pct(r.first_half_delta)}",
        f"  后半段        : {_pct(r.second_half_delta)}",
        "",
        f"  ── 判决：{r.verdict} ──（规则冻结于 {r.rule_frozen_at}）",
    ]
    for issue in r.issues:
        lines.append(f"    ! {issue}")
    if not r.issues:
        lines.append("    （三条判据全部满足）")

    lines += [
        "",
        f"  已知偏差      : {r.survivorship_warning}",
    ]
    return "\n".join(lines)
