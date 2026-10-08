"""**判决** —— 用**仓库预注册的阈值**给结果定级。

## 为什么需要这个文件（第三轮独立复审的第 3 条）

复审指出：

> `backtest_config.py` 里**早就写好了判据**（`MIN_ANN_RET` / `MIN_SHARPE` /
> `MDD_SUSPICIOUS` / `MDD_INVALID` / `MAX_SIDE_RATIO`），
> **而 Tugboat 这条路一条都没用。**
>
> ⇒ **在说"无法下结论"之前，应该先过自己那道门。**
> **按门算，答案是 `INVALID`，不是"说不清"。**

它说得对。我一直在讲"样本太短 ⇒ 说不清"，
可**仓库早就把判据写死了** —— 拿自己的尺子量自己的结果，
这一步是**免费的**，我却没做。

## 设计：判据**只有一个来源**

阈值一律从 `backtest_config` 取，**这里不写任何数字**。
⇒ 改判据只改一处；报告里的结论**不可能与判据漂移**。

## 三种等级（与 `independent_audit` 的口径一致）

| 等级 | 含义 |
|---|---|
| `INVALID` | 明确不合格 —— **不是"说不清"，是不合格** |
| `SUSPICIOUS` | 可疑（需要解释）|
| `PASS` | 过了这道门（**不等于"有优势"**，只是没被这条判据拦下）|
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import backtest_config as cfg

__all__ = ["Judgement", "judge", "render_verdict"]

INVALID = "INVALID"
SUSPICIOUS = "SUSPICIOUS"
PASS = "PASS"

#: 等级排序（取最坏的那条当总判决）。
_SEVERITY = {PASS: 0, SUSPICIOUS: 1, INVALID: 2}


@dataclass(frozen=True)
class Judgement:
    """一条判据的结论。"""

    rule: str
    level: str
    detail: str


def judge(report: dict[str, Any], *, side_ratio: float | None = None,
          ) -> list[Judgement]:
    """按 `backtest_config` 的**预注册阈值**逐条判。

    `report` 是 `trade_metrics.summarize()` 的输出。
    `side_ratio`：**单边占比**（持仓市值 / 净值 的时间均值）——
    它不在 `summarize` 里（那是逐日曝险，来自模拟器结果），由调用方传入。
    """
    out: list[Judgement] = []
    cagr = float(report.get("cagr", float("nan")))
    sharpe = float(report.get("sharpe", float("nan")))
    mdd = abs(float(report.get("max_drawdown", float("nan"))))   # 取绝对值比阈值
    n = int(report.get("n_trades", 0))

    # ── 年化 ──
    if cagr < cfg.MIN_ANN_RET:
        out.append(Judgement("MIN_ANN_RET", INVALID,
                             f"年化 {cagr * 100:.2f}% < {cfg.MIN_ANN_RET * 100:.0f}%"))

    # ── Sharpe ──
    if sharpe < cfg.MIN_SHARPE:
        out.append(Judgement("MIN_SHARPE", SUSPICIOUS,
                             f"Sharpe {sharpe:.3f} < {cfg.MIN_SHARPE}"))

    # ── 最大回撤（两档）──
    if mdd > cfg.MDD_INVALID:
        out.append(Judgement("MDD_INVALID", INVALID,
                             f"最大回撤 {mdd * 100:.2f}% > "
                             f"{cfg.MDD_INVALID * 100:.0f}%"))
    elif mdd > cfg.MDD_SUSPICIOUS:
        out.append(Judgement("MDD_SUSPICIOUS", SUSPICIOUS,
                             f"最大回撤 {mdd * 100:.2f}% > "
                             f"{cfg.MDD_SUSPICIOUS * 100:.0f}%"))

    # ── 单边占比（疑似 beta）──
    if side_ratio is not None and side_ratio > cfg.MAX_SIDE_RATIO:
        out.append(Judgement("MAX_SIDE_RATIO", SUSPICIOUS,
                             f"单边占比 {side_ratio * 100:.1f}% > "
                             f"{cfg.MAX_SIDE_RATIO * 100:.0f}% ⇒ 疑似 beta"))

    # ── 样本量（**这条是我加的**：`backtest_config` 没有，但 qsx 参照线是 120）──
    if n < 120:
        out.append(Judgement("MIN_TRADES(自加)", SUSPICIOUS,
                             f"{n} 笔 < 120（qsx 参照线）⇒ 判决能力有限"))

    if not out:
        out.append(Judgement("（无）", PASS, "没有触发任何预注册判据"))
    return out


def worst(judgements: list[Judgement]) -> str:
    """总判决 = **最坏的那一条**。"""
    if not judgements:
        return PASS
    return max(judgements, key=lambda j: _SEVERITY[j.level]).level


def render_verdict(judgements: list[Judgement]) -> str:
    """渲染成报告里的那一段。"""
    lines = ["", "  ── ★ 判决（**仓库预注册的判据**，`backtest_config`）──"]
    icon = {INVALID: "⛔", SUSPICIOUS: "⚠️", PASS: "✅"}
    for j in judgements:
        lines.append(f"  {icon[j.level]} {j.rule:22s} {j.level:11s} {j.detail}")
    total = worst(judgements)
    lines.append(f"  ⇒ **总判决：{total}**")
    if total == INVALID:
        lines.append("     ⚠️ **这不是「说不清」，是「不合格」** ——")
        lines.append("        「样本太短所以说不清」不能用来**盖过**自己那道门。")
    elif total == SUSPICIOUS:
        lines.append("     可疑 —— 需要解释，**不等于有优势**。")
    else:
        lines.append("     过了这道门 —— **不等于「有优势」**，只是没被这几条判据拦下。")
    return "\n".join(lines)
