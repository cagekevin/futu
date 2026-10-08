"""M1 面板 —— 票池切片（承 PRD M1-2 / Q2）+ 票池诊断（承审计 A4）。

## ★ 铁律：本层**不实现任何**「标的类型」判定

哪些代码是股票（排除板块 / ETF / 非美股）由 **数据层**给（`provide.cli stocks`）。
本层只做**一步**：`股票集合 ∩ 该日面板里有值的标的`。

**为什么**（`data-layer/universe.py` 的 docstring 已写死这条判据）：

> 收敛到**这一处**：日常跑不带参数就用它，结果**可复现**……
> （这是刻意的：**两处都能改 = 两份真相 = 迟早对不上**）

本层若自己判断代码前缀 / ETF 名单，就是那个"第二处"。
"""
from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注（保持本模块零同层依赖）
    from panel.panel_types import CrossSectionPanel

__all__ = ["stocks_from_payload", "universe_by_day", "universe_diagnostics"]


def stocks_from_payload(payload: Mapping) -> set[str]:
    """`provide.cli stocks` 的返回 → 股票集合。结构不符 → **报错**（承 P2）。"""
    if not isinstance(payload, Mapping) or "stocks" not in payload:
        raise ValueError("stocks 契约不符（无 stocks）（承 P2：不猜结构）")
    stocks = payload["stocks"]
    if not isinstance(stocks, list):
        raise ValueError(
            f"stocks 契约不符（stocks 不是列表，是 {type(stocks).__name__}）（承 P2）"
        )
    return {str(s) for s in stocks}


def universe_by_day(dates: Iterable[str], symbols: Iterable[str],
                    fields: Mapping[str, "pd.DataFrame"],
                    stocks: set[str]) -> dict[str, tuple[str, ...]]:
    """每日票池 = **股票集合** ∩ 该日**面板里有值**的标的（升序）。

    ⚠️ 「有值」= 该 `(日, 标的)` 在**任一字段**上非 `NaN`
    （不绑定某个特定字段，避免"只看 close"的隐含假设）。

    ⚠️ 承 Q2：**每日单独切片** —— 不用"全局票池"跑历史。
        `stocks` 里没出现的标的（板块 / ETF / 保留代码）在这里被自然排除，
        **本层不做任何类型判定**。
    """
    dates = [str(d) for d in dates]
    columns = sorted(str(s) for s in symbols if str(s) in stocks)
    if not columns:
        return {d: () for d in dates}

    present: "pd.DataFrame | None" = None
    for frame in fields.values():
        mask = frame.reindex(columns=columns).notna()
        present = mask if present is None else (present | mask)
    if present is None:
        return {d: () for d in dates}

    out: dict[str, tuple[str, ...]] = {}
    for day in dates:
        if day not in present.index:
            out[day] = ()
            continue
        row = present.loc[day]
        out[day] = tuple(s for s in columns if bool(row.get(s, False)))
    return out


def universe_diagnostics(panel: "CrossSectionPanel") -> dict[str, Any]:
    """票池诊断 —— 显形「**票池是不是幸存者集合**」。

    ## ★ 为什么必须有它（2026-10-07 审计 A4）

    实测 `data-layer` 的库：

    | 事实 | 数值 |
    |---|---|
    | 库内标的总数 | 325 |
    | **提前结束（退市 / 消失）的标的** | **0** ⚠️ |
    | 票池随时间 | 16 → 20 → 287 → 292 → 305 → 315 → 323（**只增不减**）|

    ⇒ 库 = 「**今天的自选名单，回填历史**」⇒ **没有退市样本**。

    **偏差方向（决定结论怎么读）**：幸存者偏差**高估** IC
    （活下来的公司通常表现更好）⇒
    **「不显著」的结论可信**（真实只会更不显著）；
    **「显著」的结论不可信**（可能是偏差造出来的）。

    ## ⚠️ 判据只用**面板自身**的信息

    真实的退市名单**不在库里** —— 所以只能从"有没有标的退出"反推。
    这是**启发式，不是证明**：因此本函数**只报事实 + 含义**，不做"通过/不通过"的判决。

    ⚠️ 已知误判方向：某标的**只在最后一天停牌** → 会被算成"提前结束" →
       于是 `no_symbol_ever_left` 变 `False` → **漏报**（偏向说"没问题"）。
       所以 `no_symbol_ever_left is True` 是**强信号**，
       而 `False` 只说明"至少有标的提前结束"，不保证没有幸存者偏差。
    """
    dates = [str(d) for d in panel.dates]
    if not dates:
        return {"n_days": 0, "no_symbol_ever_left": None, "caveat": "面板为空"}

    last_seen: dict[str, str] = {}
    for day in dates:
        for symbol in panel.universe_by_day.get(day, ()):
            last_seen[str(symbol)] = day
    ever = set(last_seen)
    last_day = dates[-1]
    ended_early = sorted(s for s, d in last_seen.items() if d < last_day)

    # `last_seen` 在时间轴上的位置（0 = 首日，1 = 末日）—— 分布描述，不是判决
    position = {d: i / max(len(dates) - 1, 1) for i, d in enumerate(dates)}
    median_position = (
        statistics.median(position[last_seen[s]] for s in ever) if ever else 0.0
    )
    no_left = len(ended_early) == 0
    return {
        "n_days": len(dates),
        "n_symbols_ever": len(ever),
        "n_symbols_first_day": len(panel.universe_by_day.get(dates[0], ())),
        "n_symbols_last_day": len(panel.universe_by_day.get(last_day, ())),
        "n_ended_early": len(ended_early),
        "ended_early_sample": ended_early[:20],
        "last_seen_position_median": float(median_position),
        "no_symbol_ever_left": bool(no_left),
        "caveat": (
            "⚠️ 面板里**没有任何标的退出** ⇒ 大概率是**幸存者集合** ⇒ "
            "截面 IC 被**高估** ⇒ 「不显著」可信、「显著」不可信（承审计 A4）"
            if no_left else
            f"有 {len(ended_early)} 个标的提前结束 —— 不像是纯幸存者集合"
            f"（但此判据是启发式，见 `universe_diagnostics` 的 docstring）"
        ),
    }
