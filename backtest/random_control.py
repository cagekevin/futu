"""**随机对照** —— 「真实选股」到底比「同日同数量的随机票」好多少。

## 为什么需要这个模块（第三轮独立复审的第 4 条）

复审指出两件事：

1. **随机对照没有留下任何可复现代码** ——
   §3.5 把它列为核心手段、§4.2 报它的数字，**但仓库里没有入口**
   （我是在 `/tmp` 里跑的，**跑完就没了**）。
2. ★ **n=20 没有分辨力，而且两次结果方向相反**：

   | 出处 | 真实排位 | 我当时的读法 |
   |---|---|---|
   | 第一次（**有 T8 bug 的候选**）| 70–85 分位 | 「真实**高于**随机」|
   | 重做（正确候选）| 30 分位 | 「真实**低于**随机」|

   ⇒ **「互相印证」不成立**（那是我编的）。
   而 n=20 下分位的标准误 ≈ **11 个百分点** ⇒
   **30 / 50 / 70 分位互相不可区分**。

## 本模块的三条纪律

| # | 纪律 | 为什么 |
|---|---|---|
| **1** | **必须报出分辨力**（分位步长 + 标准误）| 否则读者会把噪声当信号 |
| **2** | **随机侧与真实侧用同一套规则**（同日、同数量、同止损来源、同出场/仓位/曝险）| 唯一的差别必须只剩"选了哪几只" |
| **3** | ★ **持有期分布也要比** | §4.4「42% 第 2 天就死」**有可能只是出场规则的性质** —— 若随机侧也一样，那张表就**只证明了"止损很紧"**，跟选股无关 |

⚠️ **票池偏差不影响本对照**：两侧共用同一个池子
（这正是它**最抗偏差**的原因 —— 见 `17` §四）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

__all__ = ["ControlResult", "random_candidates", "run_control"]


@dataclass(frozen=True)
class ControlResult:
    """真实 vs 随机的对照结果（**含分辨力**）。"""

    n_iter: int
    real: dict[str, float]
    rand: dict[str, np.ndarray]          # 每个指标一条 n_iter 长的数组
    hold_real: dict[str, float]
    hold_rand: dict[str, float]

    # ── 分辨力 ──

    @property
    def step(self) -> float:
        """分位步长 = `1/(n+1)` —— n=20 时只能取 1/21 的倍数。"""
        return 1.0 / (self.n_iter + 1)

    def percentile(self, key: str) -> float:
        """真实值在随机分布里的分位（**真实高于多少比例的随机**）。"""
        a = self.rand[key]
        a = a[np.isfinite(a)]
        if a.size == 0 or not np.isfinite(self.real[key]):
            return float("nan")
        return float((a < self.real[key]).mean())

    def se(self, key: str) -> float:
        """分位的标准误（二项近似 `√(p(1−p)/n)`）—— **分辨率**的另一半。"""
        p = self.percentile(key)
        if not np.isfinite(p) or self.n_iter < 2:
            return float("nan")
        return float(np.sqrt(max(p * (1.0 - p), 1e-12) / self.n_iter))

    def verdict(self, key: str, *, threshold: float = 0.95) -> str:
        """★ 三档结论 —— **不许**说"高于/低于随机"，除非超过分辨力。"""
        p, se = self.percentile(key), self.se(key)
        if not np.isfinite(p):
            return "无数据"
        if p >= threshold:
            return "真实**优于**随机（过 95% 门槛）"
        if p <= 1.0 - threshold:
            return "真实**差于**随机（低于 5% 分位）"
        # ⚠️ 关键：把"分辨力"写进结论 —— 30 / 50 / 70 在 n=20 下不可区分
        return (f"**与随机不可区分**（分位 {p * 100:.0f}% ± {se * 100:.0f}pp；"
                f"分位步长 {self.step * 100:.1f}pp ⇒ "
                f"需要 n≳{_need_n(se):.0f} 才有 {se:.2f} 的分辨力）")


def _need_n(se: float) -> float:
    """要把分位标准误压到 `se`，大约需要多少次重抽（`n ≈ 0.25/se²`）。"""
    return 0.25 / max(se * se, 1e-12)


# ── 抽样 ────────────────────────────────────────────────────────────────

def random_candidates(cand: pd.DataFrame, panel, *,
                      rng: np.random.Generator,
                      universe_by_day: Mapping[str, tuple[str, ...]] | None = None,
                      stop_from_low: bool = True) -> pd.DataFrame:
    """**同日、同数量、同票池**抽随机标的。

    | 维度 | 怎么对齐 |
    |---|---|
    | 日期 | 完全照真实候选的日期分布（每天几条就抽几条）|
    | 数量 | 同上 |
    | 票池 | 该日**在池里且有行情**的标的（默认全池）|
    | 止损 | **用同样的规则**：当日 `low`（他的第一个候选）|

    ⇒ 唯一的差别就是「**选了哪几只**」。
    """
    low = panel.field("low")
    idx_of = {d: i for i, d in enumerate(panel.dates)}
    cnt = cand.groupby("day").size()
    rows: list[dict[str, Any]] = []
    for day, k in cnt.items():
        if day not in idx_of:
            continue
        if universe_by_day is not None:
            pool = [s for s in universe_by_day.get(day, ()) if s in low.columns]
        else:
            pool = [s for s in panel.symbols if np.isfinite(low.loc[day, s])]
        if len(pool) < k:
            continue
        for s in rng.choice(np.asarray(pool, dtype=object), size=int(k),
                            replace=False):
            stop = float(low.loc[day, s]) if stop_from_low else float("nan")
            if not np.isfinite(stop):
                continue
            rows.append({"day": day, "symbol": str(s), "stop_price": stop,
                         "form": "random", "limit_price": None, "valid_days": 1})
    return pd.DataFrame(rows)


# ── 主流程 ──────────────────────────────────────────────────────────────

#: 对照里要逐次统计的指标（都是 `trade_metrics.summarize` 的输出键）。
_METRICS = ("expectancy_r", "total_return", "sharpe", "ir_stripped")

#: 持有期分桶（**与报告一致**：0/1/2 天必须拆开）。
HOLD_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (-1, 0, "0 天"), (0, 1, "1 天"), (1, 2, "2 天"), (2, 5, "3–5 天"),
    (5, 10, "6–10 天"), (10, 20, "11–20 天"), (20, 10 ** 9, ">20 天"),
)


def _hold_dist(trades) -> dict[str, float]:
    """持有期分布（各桶的**占比**）+ 平均持有。"""
    holds = np.array([t.hold_days for t in trades], dtype=float)
    out: dict[str, float] = {"平均持有": float(holds.mean()) if holds.size else float("nan")}
    for lo, hi, label in HOLD_BUCKETS:
        m = (holds > lo) & (holds <= hi)
        out[label] = float(m.mean()) if holds.size else float("nan")
    return out


def run_control(cand: pd.DataFrame, panel, bars, *, simulate: Callable,
                summarize: Callable, make_exit_policy: Callable,
                make_account: Callable, exposure, benchmark,
                n_iter: int = 200, seed: int = 20261009,
                real_result=None) -> ControlResult:
    """跑 n 次随机对照，返回**真实 vs 随机 + 分辨力**。

    ⚠️ **n 默认 200**（不是 20）：n=20 时标准误 ≈11pp，连"50 分位"都测不准。
       200 次时 ≈3.5pp，才能把"略优于/略差于"分开。
    """
    rng = np.random.default_rng(seed)
    ep = make_exit_policy()

    if real_result is None:
        real_result = simulate(panel.dates, panel.symbols, bars, cand,
                               strategy_name="real", strategy_params={},
                               ma_exit_level=ep["ma"], exit_policy=ep["policy"],
                               account=make_account(), exposure=exposure)
    real_m = summarize(real_result, benchmark=benchmark)
    real = {k: float(real_m[k]) for k in _METRICS}
    hold_real = _hold_dist(real_result.trades)

    acc: dict[str, list[float]] = {k: [] for k in _METRICS}
    hold_acc: dict[str, list[float]] = {k: [] for k in hold_real}
    for _ in range(n_iter):
        rc = random_candidates(cand, panel, rng=rng)
        if rc.empty:
            continue
        rr = simulate(panel.dates, panel.symbols, bars, rc,
                      strategy_name="random", strategy_params={},
                      ma_exit_level=ep["ma"], exit_policy=ep["policy"],
                      account=make_account(), exposure=exposure)
        rm = summarize(rr, benchmark=benchmark)
        for k in _METRICS:
            acc[k].append(float(rm[k]))
        for k, v in _hold_dist(rr.trades).items():
            hold_acc[k].append(v)

    return ControlResult(
        n_iter=len(acc["expectancy_r"]),
        real=real,
        rand={k: np.asarray(v, dtype=float) for k, v in acc.items()},
        hold_real=hold_real,
        hold_rand={k: float(np.nanmean(v)) if v else float("nan")
                   for k, v in hold_acc.items()},
    )


def render_control(c: ControlResult) -> str:
    """把对照渲染成报告里那一段（**含分辨力**，这是重点）。"""
    out = ["", "  ── ★ 随机对照（同日 / 同数量 / 同票池 / 同一套出场与仓位）──",
           f"  重抽次数 **n = {c.n_iter}**｜分位步长 {c.step * 100:.1f}pp"
           f"｜标准误 ≈{c.se('expectancy_r') * 100:.1f}pp" if c.n_iter else ""]
    out.append(f"  {'指标':14s}{'真实':>10s}{'随机中位':>10s}{'随机 p05':>10s}"
               f"{'随机 p95':>10s}{'真实分位':>10s}")
    for k in _METRICS:
        a = c.rand[k][np.isfinite(c.rand[k])]
        if a.size == 0:
            continue
        out.append(f"  {k:14s}{c.real[k]:>10.3f}{float(np.median(a)):>10.3f}"
                   f"{float(np.percentile(a, 5)):>10.3f}"
                   f"{float(np.percentile(a, 95)):>10.3f}"
                   f"{c.percentile(k) * 100:>9.0f}%")
    out.append("")
    out.append(f"  ⇒ **{c.verdict('expectancy_r')}**")
    out.append("")
    out.append("  ── ★ 持有期分布：真实 vs 随机（**§4.4 那条发现有没有信息，看这里**）──")
    out.append(f"  {'持有':12s}{'真实':>10s}{'随机':>10s}{'差':>10s}")
    for label in ("平均持有", *[b[2] for b in HOLD_BUCKETS]):
        rv, nv = c.hold_real.get(label, float("nan")), c.hold_rand.get(label, float("nan"))
        if not (np.isfinite(rv) or np.isfinite(nv)):
            continue
        out.append(f"  {label:12s}{rv * 100 if label != '平均持有' else rv:>10.2f}"
                   f"{nv * 100 if label != '平均持有' else nv:>10.2f}"
                   f"{(rv - nv) * 100 if label != '平均持有' else rv - nv:>+10.2f}")
    out.append("")
    out.append("  ★ 怎么读持有期那一栏：")
    out.append("     · 若**随机侧也一样**（差 ≈ 0）⇒ 「42% 第 2 天就死」")
    out.append("       **只是「止损很紧」的性质**，跟选股**无关** ⇒ §四的推论不成立。")
    out.append("     · 若**真实侧明显更少早死**（差 < 0）⇒ 那才说明选股在起作用。")
    return "\n".join(x for x in out if x)
