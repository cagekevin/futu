"""**指标原语** —— 三个"各写各的就会打架"的约定，**在这里各定一次**。

## 为什么需要这个文件（第三轮独立复审第 8 条）

复审指出：`trade_metrics.py` 与 `performance_metrics.py` 是
**同名不同口径的两份实现**：

| 指标 | `performance_metrics.py`（旧）| `trade_metrics.py`（新）|
|---|---|---|
| 标准差 | `_std` = **ddof 0** | `_sharpe` = **ddof 1** |
| 年化 | **算术** `mean × ppy` | **几何** CAGR |
| `max_drawdown` | 返回**正数**（累计 PnL 口径）| 返回**负数**（净值比例口径）|

⇒ 而仓库铁律是「**PnL 只能有一份实现**」。
（讽刺的是 §3.3 说"抓到过真 bug：`np.cov` 默认 ddof=1 而 `.var()` 默认 ddof=0" ——
 **同一个 ddof 陷阱，在两份 metrics 之间又存在一次**，只是这次没人对照。）

## 本文件定哪三件事

### ① 标准差一律 **ddof = 1**（样本标准差）

| 选 | 理由 |
|---|---|
| **ddof=1（定这个）** | 我们拿到的是**样本**（历史收益），不是总体 ⇒ 无偏估计 |
| ~~ddof=0~~ | 会让 Sharpe **系统性偏高**（×`√(n/(n−1))`）；n 小时明显 |

⚠️ **一次算清，不许各写各的** —— 这个数只要有两处，就必然漂移。

### ② 年化**分两个名字**，不许混用

| 名字 | 输入 | 算法 | 用在哪 |
|---|---|---|---|
| `cagr_from_equity` | **净值** | **几何** `(末/初)^(252/区间数) − 1` | 组合级（我们真在复利）|
| `ann_return_from_pnl` | **逐笔 PnL** | **算术** `mean × ppy` | 逐笔序列（无复利语义）|

⇒ 两个都合法，但**必须名字不同**。混用是"同名不同口径"的根因。

### ③ 回撤**符号跟着输入走**，且名字带出来

| 名字 | 输入 | 符号 |
|---|---|---|
| `max_drawdown_from_equity` | 净值 | **负数**（`−0.25` = 回撤 25%）|
| `max_drawdown_from_pnl` | 累计 PnL | **正数**（`25.0` = 回撤 25 个单位）|

⇒ 名字里带 `equity` / `pnl` ⇒ 调用方**不可能拿错**。
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

__all__ = [
    "STD_DDOF", "TRADING_DAYS",
    "ann_return_from_pnl", "cagr_from_equity",
    "max_drawdown_from_equity", "max_drawdown_from_pnl",
    "sharpe_from_returns", "std", "to_returns",
]

#: 年化用的交易日数（**唯一来源**）。
TRADING_DAYS = 252

#: ★ 标准差一律用**样本**标准差（`ddof=1`）。
#:
#: ⚠️ **这一个数只许出现在这里。** 出现过一次的事故：
#:    `np.cov(x, y)[0,1] / y.var()` —— 前者默认 ddof=1、后者默认 ddof=0，
#:    ⇒ beta 被放大 `n/(n−1)`（手算序列上 `r=2b` 报出 2.2857，而 corr=1.0，**自相矛盾**）。
STD_DDOF = 1


def std(xs: Sequence[float] | np.ndarray) -> float:
    """样本标准差（`ddof = STD_DDOF`）。**唯一实现。**"""
    a = np.asarray(xs, dtype=float)
    a = a[np.isfinite(a)]
    if a.size <= STD_DDOF:
        return float("nan")
    return float(a.std(ddof=STD_DDOF))


def to_returns(equity: Sequence[float] | np.ndarray) -> np.ndarray:
    """净值序列 → **逐期简单收益**（长度 = 净值点数 − 1）。"""
    eq = np.asarray(equity, dtype=float)
    if eq.size < 2:
        return np.empty(0, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = eq[1:] / eq[:-1] - 1.0
    return r[np.isfinite(r)]


def sharpe_from_returns(rets: Sequence[float] | np.ndarray) -> float:
    """**逐期收益 → 年化 Sharpe**。

    `mean / std(ddof=1) × √252`。标准差为 0 或样本不足 ⇒ `NaN`（不是 inf）。
    """
    a = np.asarray(rets, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 2:
        return float("nan")
    s = std(a)
    if not np.isfinite(s) or s <= 0:
        return float("nan")
    return float(a.mean() / s * np.sqrt(TRADING_DAYS))


def cagr_from_equity(equity: Sequence[float] | np.ndarray) -> float:
    """**净值 → 几何年化**（组合级；我们真在复利）。

    ⚠️ 区间数是 `净值点数 − 1`（**收益区间数**），不是净值点数 ——
       差 1，1,111 天时约 0.09%。小，但那是**定义**不是近似。
    """
    eq = np.asarray(equity, dtype=float)
    eq = eq[np.isfinite(eq)]
    if eq.size < 2 or eq[0] <= 0:
        return float("nan")
    periods = eq.size - 1
    years = periods / TRADING_DAYS
    total = eq[-1] / eq[0]
    if years <= 0 or total <= 0:
        return float("nan")
    return float(total ** (1.0 / years) - 1.0)


def ann_return_from_pnl(pnl: Sequence[float], periods_per_year: int) -> float:
    """**逐笔 PnL → 算术年化**（逐笔序列**没有复利语义**）。

    ⚠️ 它与 `cagr_from_equity` **不是同一个东西**，所以名字必须不同。
    """
    a = np.asarray(pnl, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0 or periods_per_year <= 0:
        return float("nan")
    return float(a.mean() * periods_per_year)


def max_drawdown_from_equity(equity: Sequence[float] | np.ndarray) -> float:
    """**净值 → 最大回撤（负数）**。`−0.25` = 从峰值回撤 25%。"""
    eq = np.asarray(equity, dtype=float)
    if eq.size < 2:
        return float("nan")
    peak = np.maximum.accumulate(eq)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = eq / peak - 1.0
    dd = dd[np.isfinite(dd)]
    return float(dd.min()) if dd.size else float("nan")


def max_drawdown_from_pnl(pnl: Sequence[float]) -> float:
    """**累计 PnL → 最大回撤（正数，单位同 PnL）**。

    ⚠️ 与 `max_drawdown_from_equity` **符号与量纲都不同** ⇒ 名字必须不同。
    """
    a = np.asarray(pnl, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return float("nan")
    curve = np.cumsum(a)
    peak = np.maximum.accumulate(curve)
    return float((peak - curve).max())
