"""组合净值 —— **纯函数、无 IO**。

## 为什么要有这个文件

`research/tsmom/tsmom_stats.py` 里早就算过净值（`equity()` / `nav_stats()`），
但它**只在那一个课题里**：结果是报告里的**两个数字**（`−16.1%` 回撤、`1.03` Sharpe）。

于是出现了一个荒唐的局面：**整个仓库 10 个课题，没有一条「我的账户变成多少」的曲线。**
所有结论都是 pp / 分位 / 胜率 —— 它们**没有共同的分母**，
所以越攒越多、越攒越茫然（用户 2026-10-05 原话：「我不知道我们写了这么多，最终的目标是什么」）。

**这个文件就是把它们压到同一个分母上**：钱。

## 为什么放 engine/ 而不是 research/

- `engine/` = 纯计算、不碰 IO；`research/` = 有结论、有生命周期（可删）
- **账户不能依赖一个可能被删的课题** —— 所以净值算法搬到 engine/
- **而 `tsmom_stats.py` 反过来 import 它** ⇒ **回测和账户用同一份算法，口径永远分叉不了**

口径分叉是这仓库踩过的坑（`RESTATED_CONCLUSION`：同一个东西两套口径，改了一处忘了另一处）。

## 四个函数

    month_key(d)              "20261002" -> "202610"
    compound_by_month(...)    逐月等权收益 -> (月份表, 净值表)
    drawdown_series(nav)      净值 -> 逐点回撤（全是 ≤0 的数）
    nav_stats(nav)            净值 -> 总收益 / 年化 / 波动 / Sharpe / **最大回撤**

**都不依赖任何东西** —— 只用 `statistics`。

⚠️ **`compound_by_month` 必须按「年月」聚合，不能按「精确日期」** ——
这是从 `tsmom_stats.py` 带过来的教训（第一版按精确再平衡日分组，
把一次周期拆成几百个假周期，**复利几百次数字直接爆到 +4×10¹⁰%**）。
"""

from __future__ import annotations

import random
import statistics

__all__ = ["month_key", "compound_by_month", "drawdown_series",
           "bootstrap_paths", "nav_stats"]


def month_key(d) -> str:
    """`"20261002"` → `"202610"`。**按年月聚合，不按精确日期**（见模块头）。"""
    return str(d)[:6]


def compound_by_month(by_month: dict[str, list[float]]) -> tuple[list[str], list[float]]:
    """`{月份: [该月各标的的收益]}` → `(月份表, 净值表)`。

    **每个月内等权平均，然后复利。** 净值表长度 = 月份数 + 1（含起点 1.0）。

    >>> compound_by_month({"202601": [0.1, 0.1], "202602": [-0.05, -0.05]})
    (['202601', '202602'], [1.0, 1.1, 1.0450000000000002])
    """
    months = sorted(by_month)
    nav = [1.0]
    for m in months:
        xs = by_month[m]
        nav.append(nav[-1] * (1 + statistics.fmean(xs)) if xs else nav[-1])
    return months, nav


def drawdown_series(nav: list[float]) -> list[float]:
    """净值 → **逐点回撤**（相对历史峰值，全是 ≤ 0 的数）。

    ⚠️ 峰值必须**一直向前看**（`peak = max(peak, v)`），不能用全期最高 ——
    否则 2018 年的回撤会被 2026 年的新高抹掉，**历史最坏时刻就看不见了**。

    >>> drawdown_series([1.0, 1.2, 0.9])
    [0.0, 0.0, -0.25]
    """
    out, peak = [], nav[0] if nav else 0.0
    for v in nav:
        peak = max(peak, v)
        out.append((v / peak - 1) if peak else 0.0)
    return out


def bootstrap_paths(rets: list[float], *, iters: int = 500,
                    seed: int = 20261006, block: int = 3) -> list[tuple[float, float]]:
    """月收益序列 → `[(最终净值, 最大回撤), ...]` —— **块 bootstrap**。

    ## 为什么需要它（`docs/discipline.md` §0.5）

    **一条净值 = 一次抽样。** 两个配置比「谁那一次跑得高」，
    和抛两次硬币比大小没有区别。

    ## 为什么按【连续 block 个月】抽，不是按月独立抽

    月度收益有**自相关** —— 好月份容易连在一起（趋势 / regime）。
    按月独立抽会**打散这种连续性** ⇒ 区间被人为收窄 ⇒ **过度自信**。

    ## 为什么抽的是「整月的组合收益」，不是「单只票」

    同一个月里所有票是**一起动的**（横截面相关）。
    把票拆开抽 = 假装 300 只票是 300 次独立实验 ⇒ 区间同样被收窄。

    ⇒ **一块 = 连续几个月的「整张横截面」**。抽的是块，不是行。

    ⚠️ 它假设「月度收益可交换」—— 是个近似（真实序列有 regime）。
    但它比「一条路径」强得多，而且**是把回撤也纳入分布的最省事办法**。
    """
    n = len(rets)
    if not n:
        return []
    rng = random.Random(seed)
    out: list[tuple[float, float]] = []
    for _ in range(iters):
        picks: list[float] = []
        while len(picks) < n:
            s = rng.randrange(n)
            picks.extend(rets[(s + k) % n] for k in range(block))
        nav, peak, mdd = 1.0, 1.0, 0.0
        for r in picks[:n]:
            nav *= 1.0 + r
            peak = max(peak, nav)
            mdd = min(mdd, nav / peak - 1.0)
        out.append((nav, mdd))
    return out


def nav_stats(nav: list[float]) -> dict | None:
    """净值曲线 → 总收益 / 年化 / 波动 / Sharpe / **最大回撤**。

    `months` = 净值表长度 − 1（净值表含起点）。**不到 2 个点返回 None**。
    Sharpe 按**月度**收益年化（`× √12`），不减无风险利率（可忽略，和 `tsmom` 口径一致）。
    """
    if len(nav) < 2:
        return None
    rets = [nav[i + 1] / nav[i] - 1 for i in range(len(nav) - 1)]
    years = len(rets) / 12.0
    cagr = nav[-1] ** (1 / years) - 1 if years > 0 and nav[-1] > 0 else None
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    return {
        "months": len(rets),
        "total": nav[-1] - 1,
        "cagr": cagr,
        "sd": sd,
        "sharpe": (statistics.fmean(rets) / sd * (12 ** 0.5)) if sd else None,
        "mdd": min(drawdown_series(nav)),
    }
