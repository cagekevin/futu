"""**判决** —— 用**仓库现成的 8 条硬判定**给结果定级。

## 这个文件的第一版是错的（如实记录）

我第一版**自己写了一套** 5 条判据（`MIN_ANN_RET` / `MIN_SHARPE` / 两档回撤 /
单边占比），**而仓库里早就有 `walk_forward_validation.judge_verdict`** ——
它是**8 条**（多了前后半段、分段一致性、2 倍成本、交易数门槛）。

⇒ **同一件事两份实现** —— 正是仓库铁律「**PnL / 判据只能有一处**」要禁止的。
（讽刺的是：我在 `18-改动裁定` 里刚批评过 `trade_metrics` 与 `performance_metrics`
  同名不同口径，**转头自己又犯了一次**。）

⇒ 现在本文件**只剩两件事**：

| 做什么 | 说明 |
|---|---|
| **补 `judge_verdict` 要的输入** | 前后半段年化 / 分段一致性 / 2 倍成本 —— 这些要**真的跑**才能拿到 |
| **渲染** | 把它的 `(verdict, issues)` 变成报告里那一段 |

判据本身**一条都不在这里**。
"""
from __future__ import annotations

from typing import Any

import numpy as np

import backtest_config as cfg
import walk_forward_validation as wf

__all__ = ["Verdict", "compute_segments", "render_verdict", "run_verdict"]

INVALID = "INVALID"
SUSPICIOUS = "SUSPICIOUS"
VALID = "VALID"

#: 报告里那句"这不是说不清"的措辞（`INVALID` 时）。
_NOT_UNSURE = ("⚠️ **这不是「说不清」，是「不合格」** —— "
               "「样本太短所以说不清」不能用来**盖过**自己那道门。")


class Verdict(dict):
    """`{"verdict": str, "issues": list[str], "inputs": dict}` 的薄包装。"""

    @property
    def level(self) -> str:
        return str(self["verdict"])

    @property
    def issues(self) -> list[str]:
        return list(self["issues"])


def compute_segments(result, *, n_folds: int = cfg.WF_FOLDS) -> dict[str, Any]:
    """算 `judge_verdict` 要的四样：前后半段年化 / 分段一致性。

    ★ 为什么必须**真的跑**：`judge_verdict` 的判据里
      「前后半段同号」「分段多数为正」**只有拿到净值序列才能算** ——
      这也是"光看总年化会骗人"的落点（一个前半段 +40%、后半段 −30% 的策略，
      总年化可能还是正的）。
    """
    eq = np.asarray(result.equity_values, dtype=float)
    n = eq.size
    if n < 4:
        return {"h1_ann": float("nan"), "h2_ann": float("nan"),
                "wf_positive": 0, "wf_total": 0}
    half = n // 2

    def _ann(seg: np.ndarray) -> float:
        if seg.size < 2 or seg[0] <= 0:
            return float("nan")
        total = seg[-1] / seg[0] - 1.0
        years = (seg.size - 1) / 252.0
        if years <= 0:
            return float("nan")
        return float((1.0 + total) ** (1.0 / years) - 1.0) if total > -1 else -1.0

    folds = wf.segment_consistency_folds(n, n_folds=n_folds)
    pos = tot = 0
    for f in folds:
        # ⚠️ 键名是 `val`（`(start, end)` 元组），**不是** `start`/`end`
        #    —— 我第一版写错了，直接 `KeyError` 才发现的。
        a, b = (int(x) for x in f.get("val", (0, 0)))
        if b <= a or b > n:
            continue
        r = _ann(eq[a:b])
        if not np.isfinite(r):
            continue
        tot += 1
        pos += int(r > 0)
    return {"h1_ann": _ann(eq[:half]), "h2_ann": _ann(eq[half:]),
            "wf_positive": pos, "wf_total": tot}


def run_verdict(result, report: dict[str, Any], *,
                side_ratio: float | None = None,
                cost2x_profitable: bool | None = None,
                min_trades: int = 120) -> Verdict:
    """补齐输入 → 调**仓库自己的** `judge_verdict`。

    `cost2x_profitable`：**2 倍成本下还赚不赚** —— 它要**重跑一次**才能得到
    （成本是执行假设，不是策略参数；这一条问的是"结果有多依赖成本假设"）。
    `None` ⇒ 传 `False`（**保守**：不知道就当没通过，不假装通过）。
    """
    seg = compute_segments(result)
    inputs = {
        "ann_ret": float(report.get("cagr", float("nan"))),
        "sharpe": float(report.get("sharpe", float("nan"))),
        "mdd": abs(float(report.get("max_drawdown", float("nan")))),
        "max_side": float(side_ratio) if side_ratio is not None else 1.0,
        "h1_ann": seg["h1_ann"], "h2_ann": seg["h2_ann"],
        "wf_positive": seg["wf_positive"], "wf_total": seg["wf_total"],
        "cost2x_profitable": bool(cost2x_profitable),
        "n_trades": int(report.get("n_trades", 0)),
        "min_trades": int(min_trades),
    }
    # ⚠️ `NaN` 传进判据会**静默判错**（比较全为 False）⇒ 先替成保守值
    for k in ("ann_ret", "sharpe", "mdd", "h1_ann", "h2_ann"):
        if not np.isfinite(inputs[k]):
            inputs[k] = 0.0 if k != "mdd" else 1.0
    if inputs["wf_total"] == 0:
        inputs["wf_total"] = 1              # 避免除零；`wf_positive=0` ⇒ 视为未通过
    verdict, issues = wf.judge_verdict(**inputs)
    return Verdict(verdict=verdict, issues=list(issues), inputs=inputs)


def render_verdict(v: Verdict) -> str:
    """渲染成报告里那一段。**判据不在这里** —— 只在 `judge_verdict`。"""
    icon = {INVALID: "⛔", SUSPICIOUS: "⚠️", VALID: "✅"}
    lines = ["", "  ── ★ 判决（**仓库预注册的 8 条硬判定**，`walk_forward_validation`）──"]
    i = v["inputs"]
    lines.append(
        f"  输入：年化 {i['ann_ret'] * 100:.2f}%｜Sharpe {i['sharpe']:.3f}｜"
        f"MDD {i['mdd'] * 100:.2f}%｜单边 {i['max_side'] * 100:.0f}%｜"
        f"前半段 {i['h1_ann'] * 100:.1f}% / 后半段 {i['h2_ann'] * 100:.1f}%｜"
        f"分段为正 {i['wf_positive']}/{i['wf_total']}｜"
        f"2×成本仍赚 {'是' if i['cost2x_profitable'] else '**否**'}｜"
        f"{i['n_trades']} 笔")
    for msg in v.issues:
        lines.append(f"  {icon[v.level]} {msg}")
    lines.append(f"  ⇒ **总判决：{v.level}**")
    if v.level == INVALID:
        lines.append(f"     {_NOT_UNSURE}")
    elif v.level == SUSPICIOUS:
        lines.append("     可疑 —— 需要解释，**不等于有优势**。")
    else:
        lines.append("     过了这道门 —— **不等于「有优势」**，只是没被这 8 条拦下。")
    return "\n".join(lines)
