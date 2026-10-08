"""交易级结果统计 —— 回答「**这套系统赚不赚钱、扛不扛得住、是不是只是行情**」。

## 为什么不能只看"总收益"

规格 §0.5 与外部资源（`awesome-systematic-trading` 的行业级统计）都指向同一件事：

> 中位策略对 S&P500 有 **+0.17 的 beta**，剥离后中位 IR 从 ~0.37 掉到 **0.21**
> ——「**相当一部分已发表的 edge 是指数暴露，不是选股能力**」。

⇒ 所以这里**必须同时**给 `beta` 与**剥离 beta 后的 IR**。只报总收益 = 把行情当本事。

## 内含的"危险信号"自查（抄自 `qsx-strategy-score` 的 9 条清单）

它列的信号里，这几条**我们原来没有**，本模块补上：

| 信号 | 怎么算 |
|---|---|
| **收益集中度** | Top-5 交易占总利润的比例 |
| **近期持续性弱** | 最后三分之一时段单独看 |
| **样本量不足** | `n_trades` 与"至少 120 个配对区间"对照 |

## 蒙特卡洛

抄 `Kevin Davey` 的做法：**把成交顺序重排** ⇒ 得到"净值/MDD 的分布"，
用于回答「这套结果**有多依赖运气**」。
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

__all__ = ["monte_carlo", "render_report", "summarize"]

#: 年化用的交易日数（与 `backtest_config` 的口径一致：日线）。
TRADING_DAYS = 252


def _equity_returns(equity: Sequence[float]) -> np.ndarray:
    e = np.asarray(equity, dtype=float)
    if e.size < 2:
        return np.zeros(0)
    return e[1:] / e[:-1] - 1.0


def _max_drawdown(equity: Sequence[float]) -> float:
    e = np.asarray(equity, dtype=float)
    if e.size == 0:
        return 0.0
    peak = np.maximum.accumulate(e)
    return float((e / peak - 1.0).min())


def _sharpe(rets: np.ndarray) -> float:
    if rets.size < 2:
        return float("nan")
    sd = float(rets.std(ddof=1))
    if sd == 0:
        return float("nan")
    return float(rets.mean() / sd * np.sqrt(TRADING_DAYS))


def summarize(result, *, benchmark: Sequence[float] | None = None) -> dict[str, Any]:
    """把 `SimulationResult` 汇总成一份可读的字典。

    `benchmark`：基准的**逐日收益**（与 `result.equity_days` 等长，可为 `None`）。
    """
    rs = np.array([t.r_multiple for t in result.trades], dtype=float)
    equity = np.asarray(result.equity_values, dtype=float)
    rets = _equity_returns(result.equity_values)
    days = len(result.equity_days)

    wins, losses = rs[rs > 0], rs[rs <= 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    reasons: dict[str, int] = {}
    for t in result.trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    # ── 收益集中度（`qsx` 的危险信号之一）──
    r_sorted = np.sort(rs)[::-1] if rs.size else rs
    total_r = float(rs.sum())
    top5 = float(r_sorted[:5].sum()) if rs.size else 0.0

    # ── 近期持续性（`qsx` 的危险信号之一）──
    third = max(1, len(result.trades) // 3)
    recent = rs[-third:] if rs.size else rs

    # ── 基准依赖（**必须剥离 beta 再看**）──
    beta = corr = ir_raw = ir_stripped = float("nan")
    bench_total = float("nan")
    if benchmark is not None and len(benchmark) == rets.size and rets.size > 2:
        b = np.asarray(benchmark, dtype=float)
        ok = np.isfinite(b) & np.isfinite(rets)
        if ok.sum() > 2 and float(b[ok].var()) > 0:
            beta = float(np.cov(rets[ok], b[ok])[0, 1] / b[ok].var())
            corr = float(np.corrcoef(rets[ok], b[ok])[0, 1])
            ir_raw = _sharpe(rets[ok])
            ir_stripped = _sharpe(rets[ok] - beta * b[ok])
            bench_total = float(np.prod(1.0 + b[ok]) - 1.0)

    ann_factor = TRADING_DAYS / days if days else float("nan")
    return {
        "n_trades": len(result.trades),
        "n_days": days,
        "final_equity": float(equity[-1]) if equity.size else float("nan"),
        "total_return": float(equity[-1] / equity[0] - 1.0) if equity.size else float("nan"),
        "cagr": float((equity[-1] / equity[0]) ** ann_factor - 1.0) if equity.size and equity[0] > 0 else float("nan"),
        "ann_vol": float(np.nanstd(rets) * np.sqrt(TRADING_DAYS)) if rets.size else float("nan"),
        "sharpe": _sharpe(rets),
        "max_drawdown": _max_drawdown(result.equity_values),
        # ── 交易统计（**核心：R 分布**）──
        "win_rate": float((rs > 0).mean()) if rs.size else float("nan"),
        "avg_r": float(rs.mean()) if rs.size else float("nan"),
        "median_r": float(np.median(rs)) if rs.size else float("nan"),
        "payoff_ratio": (gross_win / len(wins)) / (gross_loss / len(losses))
        if len(wins) and len(losses) and gross_loss > 0 else float("nan"),
        "expectancy_r": float(rs.mean()) if rs.size else float("nan"),
        "total_r": total_r,
        "best_r": float(rs.max()) if rs.size else float("nan"),
        "worst_r": float(rs.min()) if rs.size else float("nan"),
        "avg_hold_days": float(np.mean([t.hold_days for t in result.trades]))
        if result.trades else float("nan"),
        "exit_reasons": reasons,
        # ── 危险信号自查 ──
        "top5_profit_share": (top5 / total_r) if total_r > 0 else float("nan"),
        "recent_avg_r": float(recent.mean()) if recent.size else float("nan"),
        # ── 基准依赖 ──
        "beta": beta, "corr": corr,
        "ir_raw": ir_raw, "ir_stripped": ir_stripped,
        "benchmark_return": bench_total,
        # ── 被跳过的 ──
        "skipped_no_slot": result.skipped_no_slot,
        "skipped_exposure": result.skipped_exposure,
        "skipped_expired": result.skipped_expired,
        "stages": _stage_summary(result.stages),
    }


def _stage_summary(stages: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in stages or ():
        out[str(s)] = out.get(str(s), 0) + 1
    return out


def monte_carlo(result, *, iterations: int = 2000, seed: int = 20261008,
                ) -> dict[str, float]:
    """**bootstrap 重采样**（有放回抽同样多笔）⇒ 看"最终总 R / 最大回撤"的分布。

    ## ★ 为什么是 bootstrap，不是"重排顺序"（这条是我第一版写错的）

    第一版我写的是**重排成交顺序** —— 结果 `p05 / p50 / p95` **三个数完全相同**。
    根因：**总和是求和，与顺序无关** ⇒ "最终总 R"**按构造就是常数**。
    那看起来像"结果极其稳定"，**其实是方法没在测东西**（假象）。

    ⇒ 改用**有放回重采样**：它同时改变**总和**与**路径**，
      回答的才是那个真问题 ——「**这套结果有多依赖具体的那些成交**」。

    ⚠️ 与"重排"相比，bootstrap 的**代价**：它假设成交之间**独立同分布**。
       对交易结果这是**强假设**（连续亏损会聚集），所以读的时候要知道
       这个区间**偏窄** —— 它给的是**下界**意义上的稳定度。
    """
    rs = np.array([t.r_multiple for t in result.trades], dtype=float)
    nan = float("nan")
    if rs.size < 2:
        return {"n": float(rs.size), "p05": nan, "p50": nan, "p95": nan,
                "p_profit": nan, "dd_p50": nan, "dd_p95": nan}
    rng = np.random.default_rng(seed)
    n = rs.size
    draws = rng.integers(0, n, size=(iterations, n))
    paths = np.cumsum(rs[draws], axis=1)
    finals = paths[:, -1]
    peaks = np.maximum.accumulate(paths, axis=1)
    dds = (paths - peaks).min(axis=1)
    return {
        "n": float(n),
        "p05": float(np.percentile(finals, 5)),
        "p50": float(np.percentile(finals, 50)),
        "p95": float(np.percentile(finals, 95)),
        # ★ 这条才是最该看的：重采样后**还是赚钱**的比例
        "p_profit": float((finals > 0).mean()),
        "dd_p50": float(np.percentile(dds, 50)),
        "dd_p95": float(np.percentile(dds, 5)),      # 5% 分位 = 最差那一侧
    }


def render_report(report: dict[str, Any], mc: dict[str, float] | None = None,
                  *, title: str = "") -> str:
    """把汇总渲染成文本（**唯一产出物**）。"""

    def pct(x: float, nd: int = 2) -> str:
        return "n/a" if not np.isfinite(x) else f"{x * 100:.{nd}f}%"

    def num(x: float, nd: int = 3) -> str:
        return "n/a" if not np.isfinite(x) else f"{x:.{nd}f}"

    lines = []
    if title:
        lines += [title, "=" * len(title), ""]
    lines += [
        f"  样本            : {report['n_trades']} 笔交易 / {report['n_days']} 个交易日",
        f"  平均持有        : {num(report['avg_hold_days'], 1)} 天",
        "",
        "  ── 净值 ──",
        f"  总收益          : {pct(report['total_return'])}",
        f"  年化            : {pct(report['cagr'])}",
        f"  年化波动        : {pct(report['ann_vol'])}",
        f"  Sharpe          : {num(report['sharpe'])}",
        f"  最大回撤        : {pct(report['max_drawdown'])}",
        "",
        "  ── ★ R 倍数分布（他系统的核心）──",
        f"  胜率            : {pct(report['win_rate'], 1)}",
        f"  平均 R          : {num(report['avg_r'])}",
        f"  中位 R          : {num(report['median_r'])}",
        f"  盈亏比          : {num(report['payoff_ratio'])}  （平均赚 / 平均亏）",
        f"  最好 / 最差 R   : {num(report['best_r'], 2)} / {num(report['worst_r'], 2)}",
        f"  **期望值**      : {num(report['expectancy_r'])} R / 笔",
        "",
        "  ── 出场原因 ──",
    ]
    for k, v in sorted(report["exit_reasons"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {k:16s}: {v:>4d}  ({v / max(report['n_trades'], 1) * 100:4.1f}%)")
    lines += [
        "",
        "  ── ★ 是不是只是行情（**必须剥离 beta 再看**）──",
        f"  基准总收益      : {pct(report['benchmark_return'])}",
        f"  beta            : {num(report['beta'])}   （≥0.15 即「高基准依赖」，qsx 的告警线）",
        f"  相关系数        : {num(report['corr'])}",
        f"  IR（原始）      : {num(report['ir_raw'])}",
        f"  **IR（剥离 beta）**: {num(report['ir_stripped'])}",
        "",
        "  ── 危险信号自查（qsx 的清单）──",
        f"  收益集中度      : Top-5 占 {pct(report['top5_profit_share'], 1)}（越高越靠少数几笔）",
        f"  近期持续性      : 最后 1/3 平均 R = {num(report['recent_avg_r'])}",
        f"  样本量          : {report['n_trades']} 笔（qsx 参照线：≥120 个观测）",
        "",
        "  ── 被跳过的 ──",
        f"  仓位满 / 曝险上限 / 限价到期 : "
        f"{report['skipped_no_slot']} / {report['skipped_exposure']} / "
        f"{report['skipped_expired']}",
        "",
        "  ── 曝险档位（他 §2.2 的四阶段）──",
    ]
    for k, v in sorted(report["stages"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {k:16s}: {v:>4d} 天  ({v / max(report['n_days'], 1) * 100:4.1f}%)")
    if mc:
        lines += [
            "",
            "  ── 蒙特卡洛（bootstrap 重采样 2,000 次）──",
            f"  最终总 R：p05 {num(mc['p05'], 1)} | **中位 {num(mc['p50'], 1)}** | "
            f"p95 {num(mc['p95'], 1)}",
            f"  **重采样后仍赚钱的比例：{pct(mc['p_profit'], 1)}**"
            f"  ← 这条最该看",
            f"  最大回撤（R 计）：中位 {num(mc['dd_p50'], 1)} | 最差 5% {num(mc['dd_p95'], 1)}",
            "  （⚠️ bootstrap 假设成交独立同分布 —— 连续亏损会聚集 ⇒ 区间**偏窄**）",
        ]
    return "\n".join(lines)
