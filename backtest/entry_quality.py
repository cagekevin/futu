"""**进场质量** —— 用**信号日当天可观测**的量，预测"这笔能不能活下来"。

## 为什么只做这个（而不是继续调出场参数）

前面的分组分析指得很清楚：

| 持有 | 笔数 | 胜率 | 平均 R |
|---|---|---|---|
| ≤2 天 | 71（42%）| **1.4%** | −1.022 |
| 11–20 天 | 22 | **95.5%** | +2.222 |
| >20 天 | 10 | **100%** | +4.391 |

⇒ 系统的**全部利润来自"活下来的那一小部分"**，
而**能不能活过前几天，在进场那一刻就基本定了** —— 那正是他的**催化剂 / 图表**那一层。

## ⚠️ 三条纪律（否则这个模块本身就是在过拟合）

| # | 纪律 | 为什么 |
|---|---|---|
| **1** | 特征**只能是信号日当天可观测的** | 用 `hold_days` 自己当特征 = **同义反复**（活过 5 天的人当然"持有 >5 天"）|
| **2** | ★ **只做单变量筛查 + BH，不拟合多元模型** | 样本 ~30 笔，拟合 5 个系数的多元模型**必然过拟合**。单变量 + BH 是**这个样本量下唯一诚实的做法** |
| **3** | ★ **同时报"多重检验的分母"** | 我筛 20 个特征，总有一个 AUC 看着高 ⇒ **不报分母的 p 值没有意义** |

## ★ 怎么读结果

| 情形 | 含义 |
|---|---|
| **有特征过 BH** | 那个特征**值得**进一步验（但仍需**另开样本**确认）|
| **全都没过 BH** | ⇒ **在这份样本里，没有可观测的"进场质量"信号** —— 这也是一个结论（**而且是有用的**：它说明"钱在进场质量"这句话，**靠现有可观测的量拿不到**）|

⚠️ **AUC = 0.5 表示没有分辨力**。`p` 用**置换检验**（不是 t 检验）——
   小样本下 t 检验的正态假设不成立，置换检验只依赖"可交换性"。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd

import panel_statistics as _stats

__all__ = ["FeatureResult", "ScreenResult", "render_screen", "screen"]

#: 置换检验次数（小样本下够用）。
N_PERM = 2000


@dataclass
class FeatureResult:
    """一个特征的筛查结果。"""

    name: str
    n: int = 0
    auc: float = float("nan")
    p_value: float = float("nan")
    adj_p: float = float("nan")
    significant: bool = False
    mean_pos: float = float("nan")
    mean_neg: float = float("nan")


@dataclass
class ScreenResult:
    """一次筛查的全部结果（**含分母**）。"""

    target: str
    n_trades: int
    n_features: int
    results: list[FeatureResult]

    @property
    def survivors(self) -> list[FeatureResult]:
        return [r for r in self.results if r.significant]

    def verdict(self) -> str:
        if not self.results:
            return "没有可筛的特征"
        if self.survivors:
            names = ", ".join(r.name for r in self.survivors)
            return (f"⚠️ **{len(self.survivors)} / {self.n_features} 个特征过了 BH**"
                    f"（{names}）—— 值得另开样本再验，**不能当结论**")
        return (f"⛔ **{self.n_features} 个特征**里**没有一个**过 BH"
                f"（族大小 {self.n_features}）"
                f"⇒ **这份样本里没有可观测的「进场质量」信号**")


def _auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """AUC（`labels` 为 1/0）—— 用**秩**算，等价于 Mann-Whitney U。"""
    pos, neg = scores[labels == 1], scores[labels == 0]
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    # 并列取平均秩
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(order.size, dtype=float)
    ranks[order] = np.arange(1, order.size + 1, dtype=float)
    s = np.concatenate([pos, neg])
    uniq, inv, cnt = np.unique(s, return_inverse=True, return_counts=True)
    if (cnt > 1).any():
        for u in np.flatnonzero(cnt > 1):
            m = inv == u
            ranks[m] = ranks[m].mean()
    r_pos = ranks[:pos.size].sum()
    return float((r_pos - pos.size * (pos.size + 1) / 2.0) / (pos.size * neg.size))


def screen(features: pd.DataFrame, labels: Sequence[int], *,
           target: str = "survive", n_perm: int = N_PERM,
           alpha: float = 0.05, seed: int = 20261009,
           min_cover: float = 0.8) -> ScreenResult:
    """**单变量筛查 + BH**。

    `features`：每行一笔交易、每列一个**信号日可观测**的特征。
    `labels`：1 = 该笔"活下来"（目标），0 = 没有。
    `min_cover`：特征覆盖率低于它就跳过（缺失太多 ⇒ 算出来的是噪声）。

    ⚠️ **不做多元拟合** —— 样本这么小，拟合就是过拟合（见模块 docstring 纪律 2）。
    """
    y = np.asarray(labels, dtype=int)
    rng = np.random.default_rng(seed)
    out: list[FeatureResult] = []

    for col in features.columns:
        x = pd.to_numeric(features[col], errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(x) & np.isfinite(y)
        n = int(ok.sum())
        if n < 8 or n < min_cover * len(features):
            continue
        xs, ys = x[ok], y[ok]
        if ys.min() == ys.max():
            continue
        auc = _auc(xs, ys)
        if not np.isfinite(auc):
            continue
        # ── 置换检验：打乱标签，看 AUC 偏离 0.5 这么远有多常见 ──
        perm = np.empty(n_perm)
        for i in range(n_perm):
            perm[i] = _auc(xs, rng.permutation(ys))
        # 双侧
        dev = abs(auc - 0.5)
        p = float((np.abs(perm - 0.5) >= dev).mean())
        out.append(FeatureResult(
            name=str(col), n=n, auc=auc, p_value=max(p, 1.0 / n_perm),
            mean_pos=float(xs[ys == 1].mean()), mean_neg=float(xs[ys == 0].mean())))

    # ── BH 校正（**仓库自己的**，判据只有一处）──
    if out:
        res = _stats.benjamini_hochberg([r.p_value for r in out], alpha=alpha)
        for r, adj, sig in zip(out, res.adjusted, res.significant):
            r.adj_p, r.significant = float(adj), bool(sig)
    return ScreenResult(target=target, n_trades=len(features),
                        n_features=len(out), results=out)


def render_screen(s: ScreenResult) -> str:
    """渲染 —— **重点是"分母"与"过没过 BH"**。"""
    out: list[str] = []
    out.append("")
    out.append("═" * 78)
    out.append(f"★ 进场质量筛查 —— 用**信号日当天可观测**的量预测「{s.target}」")
    out.append("─" * 78)
    out.append(f"  样本 **{s.n_trades} 笔**｜筛了 **{s.n_features} 个特征**"
               f"（**族大小就是它** —— 不报分母的 p 值没有意义）")
    out.append(f"  ⚠️ **只做单变量 + BH，不拟合多元模型** —— "
               f"{s.n_trades} 笔拟合多系数必然过拟合")
    out.append("")
    out.append(f"  {'特征':30s}{'覆盖':>6s}{'AUC':>8s}{'p(置换)':>10s}"
               f"{'BH p':>10s}{'':>4s}")
    for r in sorted(s.results, key=lambda q: -(q.auc if np.isfinite(q.auc) else 0)):
        mark = "  ✅" if r.significant else ""
        out.append(f"  {r.name[:30]:30s}{r.n:>6d}{r.auc:>8.3f}"
                   f"{r.p_value:>10.4f}{r.adj_p:>10.4f}{mark}")
    out.append("─" * 78)
    out.append(f"  ⇒ {s.verdict()}")
    out.append("─" * 78)
    out.append("  ★ 怎么读：")
    out.append("     · **AUC = 0.5 就是没有分辨力**；0.65 以上才值得看。")
    out.append("     · **过了 BH 也不等于能用** —— 这只说明\"在这份样本里它没被噪声解释掉\"，")
    out.append("       要当结论必须**另开样本**（同一份数据上再筛就是数据窥探）。")
    out.append("     · ⚠️ 特征**全部只用信号日及以前**的信息；")
    out.append("       `hold_days` / `r_multiple` 这类**结果**绝不能当特征（那是同义反复）。")
    return "\n".join(out)
