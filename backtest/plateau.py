"""**邻域稳定性（plateau）** —— 小样本下唯一能做的过拟合检验。

## 为什么不是"样本内挑、样本外评"

那是最标准的做法，**但它需要样本**。本项目的实测：

```
成交 29 笔｜入场日 26 个
切成 IS/OOS ⇒ 各 ~14 笔
18 点网格 ⇒ **1.6 笔/点**
门槛「每点 ≥10 笔」⇒ 全窗口最多 2.9 个点，**切完只剩 1.4 个点**
```

⇒ **在这个样本量下，IS/OOS 在数学上就做不了** —— 硬跑出来的"排名"是纯噪声。
（我第一版就是硬跑了，**方向错了**。）

## 那能做什么：**看"最好"的点周围是什么样**

| 形状 | 含义 | 能不能信 |
|---|---|---|
| **尖峰**（spike）| 只有那一个值好，左右邻居都差 | ⛔ **不许信** —— 那是噪声 |
| **平台**（plateau）| 一片连续的值都好 | ✅ **可能可信** —— 参数不敏感 |

★ **这个检验不需要更多样本** —— 它问的是"**形状**"，不是"**绝对值**"。

## 但必须配一条底线

`29 笔` 时，单个值的指标标准误极大（簇级 bootstrap 实测见输出）。
⇒ 所以本模块**同时报**：

| 报什么 | 为什么 |
|---|---|
| 每个值的指标 | —— |
| ★ **簇级 bootstrap 的标准误** | 没有它，读者会把噪声当差别 |
| ★ **"最好"与邻居的差 vs 2×SE** | **差在噪声内 ⇒ 那个"最好"没有意义** |

⚠️ 结论的三档：
  · 尖峰 ⇒ **明确是噪声**
  · 平台且最好点超出邻居 2×SE ⇒ **形状支持**（仍不等于有优势）
  · 全都挤在 2×SE 内 ⇒ **参数根本没影响**（那就不该挑）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

__all__ = ["PlateauPoint", "PlateauResult", "cluster_se", "render_plateau", "scan"]

#: 经验门槛：每个网格点至少要有这么多笔，指标才有意义。
MIN_TRADES_PER_POINT = 10


def cluster_se(trades) -> float:
    """**簇级 bootstrap 的标准误**（簇 = 交易日）。

    ⚠️ 为什么不能按笔算：同一**交易日**的多笔共享当天行情 ⇒ **不独立**，
       按笔算会**系统性低估**标准误（`statistics` 的文档里有实测：
       4,076 笔只落在 992 天，按笔 vs 按日**结论符号翻转**）。
    """
    by_day: dict[str, list[float]] = {}
    for t in trades:
        by_day.setdefault(t.entry_day, []).append(float(t.r_multiple))
    k = len(by_day)
    if k < 3:
        return float("nan")
    rng = np.random.default_rng(20261009)
    keys = list(by_day)
    means = np.empty(2000)
    for i in range(means.size):
        pick = rng.integers(0, k, size=k)
        vals = [v for j in pick for v in by_day[keys[j]]]
        means[i] = float(np.mean(vals)) if vals else np.nan
    means = means[np.isfinite(means)]
    return float(means.std(ddof=1)) if means.size > 1 else float("nan")


@dataclass
class PlateauPoint:
    """参数网格上的一个值。"""

    value: Any
    n_trades: int = 0
    metric: float = float("nan")
    se: float = float("nan")
    total_return: float = float("nan")


@dataclass
class PlateauResult:
    """一条参数轴上的形状。"""

    param: str
    metric: str
    points: list[PlateauPoint] = field(default_factory=list)

    @property
    def scored(self) -> list[PlateauPoint]:
        return [p for p in self.points if np.isfinite(p.metric) and p.n_trades > 0]

    @property
    def best(self) -> PlateauPoint | None:
        s = self.scored
        return max(s, key=lambda p: p.metric) if s else None

    def neighbours(self, p: PlateauPoint) -> list[PlateauPoint]:
        """按**值的大小**取左右邻居（不是按列表顺序 —— 网格顺序不可靠）。"""
        s = sorted(self.scored, key=lambda q: float(q.value))
        i = next((j for j, q in enumerate(s) if q is p), None)
        if i is None:
            return []
        out = []
        for j in (i - 1, i + 1):
            if 0 <= j < len(s):
                out.append(s[j])
        return out

    @property
    def is_spike(self) -> bool:
        """★ **尖峰判据**：最好的点**超出它所有邻居 2×SE** ⇒ 形状不支持。

        为什么用 `2×SE`：约 95% 的置信水平。差在噪声内 ⇒ 那个"最好"没意义。
        """
        b = self.best
        if b is None:
            return True
        nb = self.neighbours(b)
        if not nb:
            return True                      # 连邻居都没有 ⇒ 更不该信
        se = max(b.se, 1e-12) if np.isfinite(b.se) else float("nan")
        if not np.isfinite(se):
            return True
        return all(b.metric - q.metric > 2.0 * se for q in nb)

    @property
    def all_within_noise(self) -> bool:
        """★ 所有点都挤在 `2×SE` 内 ⇒ **参数根本没影响**（那就不该挑）。"""
        s = self.scored
        if len(s) < 2:
            return True
        vals = [p.metric for p in s]
        se = max((p.se for p in s if np.isfinite(p.se)), default=float("nan"))
        if not np.isfinite(se):
            return False
        return (max(vals) - min(vals)) <= 2.0 * se

    def verdict(self) -> str:
        if not self.scored:
            return "没有能算的点"
        if self.all_within_noise:
            return ("⛔ **参数没有影响**（全部点挤在 2×SE 内）"
                    "⇒ 那就不该挑 —— 挑出来的必然是噪声")
        if self.is_spike:
            return ("⛔ **尖峰** —— 只有那一个值好、邻居都差"
                    "⇒ **形状不支持**，那个\"最好\"是噪声")
        return ("⚠️ **平台** —— 最好点的邻居也在噪声范围内"
                "⇒ 形状支持（**仍不等于有优势**）")


def scan(
    param: str,
    values: Sequence[Any],
    *,
    build_candidates: Callable[[dict[str, Any]], Any],
    simulate: Callable[..., Any],
    summarize: Callable[..., dict[str, Any]],
    metric: str = "expectancy_r",
    run_kwargs: dict[str, Any] | None = None,
) -> PlateauResult:
    """扫**一条**参数轴 —— 每个值给指标 + **簇级 SE**。

    `build_candidates({param: value})` ⇒ 该值下的候选 DataFrame
    `simulate(candidates=..., **run_kwargs)` ⇒ 该值下的模拟结果
    """
    out = PlateauResult(param=param, metric=metric)
    kw = dict(run_kwargs or {})
    for v in values:
        pt = PlateauPoint(value=v)
        params = {param: v}
        cand = build_candidates(params)
        if cand is not None and len(cand):
            # ⚠️ **必须把 params 显式传下去** —— 第一版只传 `candidates`，
            #    于是「属于出场规则 / 账户」的参数（`target_r` /
            #    `max_total_exposure`）**根本没被改**，
            #    五行的数值一模一样（看起来像"参数没影响"）。
            r = simulate(candidates=cand, params=params, **kw)
            m = summarize(r, benchmark=None)
            pt.n_trades = int(m.get("n_trades", 0))
            pt.metric = float(m.get(metric, float("nan")))
            pt.total_return = float(m.get("total_return", float("nan")))
            pt.se = cluster_se(r.trades)
        out.points.append(pt)
    return out


def render_plateau(res: PlateauResult) -> str:
    """渲染 —— **重点是 SE 与"最好 vs 邻居"的差**。"""
    out: list[str] = []
    out.append("")
    out.append("═" * 78)
    out.append(f"★ 邻域稳定性：扫 `{res.param}`（**小样本下唯一能做的过拟合检验**）")
    out.append("─" * 78)
    out.append(f"  {'值':>12s}{'笔数':>7s}{'总收益':>11s}"
               f"{res.metric:>12s}{'SE(簇级)':>11s}{'':>3s}")
    b = res.best
    for p in sorted(res.points, key=lambda q: float(q.value)):
        mark = "  ← 最好" if p is b else ""
        out.append(f"  {str(p.value):>12s}{p.n_trades:>7d}"
                   f"{p.total_return * 100:>10.2f}%{p.metric:>12.3f}"
                   f"{p.se:>11.3f}{mark}")
    out.append("─" * 78)

    # ★ 可行性判据（**先报这个** —— 它决定上面那张表能不能读）
    n_pts = len(res.scored)
    tot = sum(p.n_trades for p in res.scored)
    per = tot / n_pts if n_pts else float("nan")
    out.append(f"  **可行性**：{n_pts} 个点｜合计 {tot} 笔｜"
               f"**{per:.1f} 笔/点**（门槛 ≥{MIN_TRADES_PER_POINT}）")
    if np.isfinite(per) and per < MIN_TRADES_PER_POINT:
        out.append(f"  ⛔ **每点笔数不足** ⇒ 上面的差别**大部分是噪声**，")
        out.append(f"     而且 **IS/OOS 切分在这里做不了**（切完每边再减半）。")
    out.append("")
    if b is not None:
        out.append(f"  **最好**：`{res.param} = {b.value}`"
                   f"（{res.metric} {b.metric:+.3f} ± {b.se:.3f}）")
        nb = res.neighbours(b)
        if nb:
            for q in nb:
                gap = b.metric - q.metric
                out.append(f"    与邻居 `{q.value}` 的差：**{gap:+.3f}**"
                           f"（2×SE = {2 * b.se:.3f}）"
                           f"{'  ← 在噪声内' if abs(gap) <= 2 * b.se else ''}")
    out.append("")
    out.append(f"  ⇒ {res.verdict()}")
    out.append("─" * 78)
    out.append("  ★ 为什么看**形状**而不是**绝对值**：")
    out.append("     绝对值依赖样本（这里样本很小）；")
    out.append("     而**尖峰 vs 平台**是结构性的 —— **尖峰一定是噪声**。")
    out.append("  ⚠️ 平台也**不等于有优势** —— 它只说明\"这个参数不敏感\"。")
    out.append("  ⚠️ 扫一条轴 = **多次检验** ⇒ 要挑\"最好\"的，p 值必须一起过 BH。")
    return "\n".join(out)
