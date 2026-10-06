"""对家分类器 —— **前提 P0 + 3 个独立 flag**。

## 它在回答什么

> 「这只票**在跌**，那么**谁在接**？」

不是「好不好」，不是「买不买」—— **只描述对家结构**。

## 前提 P0（先过这道门）

    P0 压价状态 = chg120 < 0

**不满足 → `label = "N"`，不做后续判断。**
理由：对家数据滞后是季度级（13F 45 天），压价窗口要对得上；20 日太短、250 日太长。

## 满足 P0 后的 3 个 flag（**可同时成立，不合并**）

| flag | 判据 | 为什么算「对家」证据 | 反例 |
|---|---|---|---|
| **I** | 内部人**净买入**（`insider_buy_count > 0`，窗内） | 内部人**只有一种理由买**；实测**一半以上股票根本没有买入** → 稀有 | 象征性小额 |
| **S** | **高空头**（`short_percent` > **同档 P75**） | 有人**押注继续跌** | 空头往往聪明；做市商双计 |
| **F** | 机构**减持**（`holder_pct_change` ≤ **同档 P25**） | 可能**被迫**（赎回），也可能**有信息** | 分不清被迫 vs 知情 |
| ~~B~~ | ~~回购~~ | ❌ **美股无此接口**（`get_corporate_actions_buybacks` 仅港股/A股） | **已废除** |

**阈值全部用「同市值档分位」** —— 实测 `short_percent` 中位：小盘 4.71 / 中盘 4.86 / **大盘 1.60**，
**绝对阈值会把大盘股系统性漏掉**。

`flags` 为空 → `label = "E"`（**在压价状态下，看不到任何一类对家**）—— 这是**事实描述**，不是「丢掉」。

## 设计原则

1. **纯函数、无 IO** —— 输入是已读好的 dict
2. **只描述结构**，**不输出「丢 / 留」**
3. **必须带原始数字与 asof** —— 让人可以推翻标签
4. **时效闸门** —— 每块数据过期则该 flag 置空并进 `stale`

用法：
    from engine.counterparty.classify import classify
    r = classify(slow, price, ref, flow=flow)
    # {"p0": True, "label": "I+S", "flags": ["I","S"], "stale": [], "detail": {...}}
"""

from __future__ import annotations

from datetime import date

from engine.counterparty.reference import (
    metrics_of,
    parse_asof,
    tier_of,
)

# 每块数据的最大允许滞后（天）—— 过期则 flag 置空并记进 `stale`
STALE_DAYS = {
    "insider": 180,
    "institutional": 120,      # 13F 本身 45 天 + 缓冲
    "short": 30,
    "flow": 5,
    "rating": 180,
}

# 阈值：全部是**同档分位**，不是绝对值
DEFAULT_THRESHOLDS = {
    "short_pct": 75,           # 空头占比 > 同档 P75
    "holder_pct_change": 25,   # 机构持股比例变化 ≤ 同档 P25
}

# 内部人计数的窗口（天）
INSIDER_WINDOW_DAYS = 180

__all__ = ["classify", "STALE_DAYS", "DEFAULT_THRESHOLDS"]


def _bucket(value, m):
    """`value` 落在哪个**分位桶**（参考分布只有 P10/25/50/75/90，所以是粗桶）。"""
    if value is None or not m:
        return None
    last = None
    for q in (10, 25, 50, 75, 90):
        p = m.get(f"p{q}")
        if p is None:
            continue
        if value < p:
            return f"<P{q}" if last is None else f"P{last}~P{q}"
        last = q
    return f">P{last}" if last is not None else None


def _age_days(asof_str, today):
    d = parse_asof(asof_str)
    return None if d is None else (today - d).days


# 块级 `asof` 缺失时，从记录里挖日期的字段（**嵌套一层的也算**）
_ASOF_FALLBACK = {
    "insider": ("max_trade_date_str",),
    "institutional": ("period_text", "holding_date_str"),
    "short": ("timestamp_str",),
    "rating": ("update_time_str",),
    "flow": ("capital_flow_item_time", "update_time"),
}


def _dig_asof(records, fields):
    """从记录里挖**最新**日期。支持嵌套一层（rating 的日期在 `institution_info` 里）。"""
    best = None
    for r in records or []:
        if not isinstance(r, dict):
            continue
        for src in (r, *(v for v in r.values() if isinstance(v, dict))):
            for f in fields:
                v = src.get(f)
                if v and parse_asof(v) is not None:
                    s = str(v)
                    if best is None or s > best:
                        best = s
    return best


def _block_asof(doc, block):
    """块的 `asof`；落盘时没写就从记录里挖。"""
    blk = (doc or {}).get(block)
    if not isinstance(blk, dict):
        return None
    return blk.get("asof") or _dig_asof(blk.get("records"), _ASOF_FALLBACK.get(block, ()))


def _staleness(slow, flow, *, as_of=None):
    """逐块检查时效。返回 `(stale_blocks, ages)`。

    ⚠️ **频率不同不能混** —— 机构是季度、卖空是双周、资金流是日频。
    一块数据过期就把它对应的 flag 置空，而不是拿旧数据硬算。
    """
    today = parse_asof(as_of) or date.today()
    stale, ages = [], {}
    for block in ("insider", "institutional", "short", "rating"):
        a = _block_asof(slow, block)
        age = _age_days(a, today)
        ages[block] = {"asof": a, "age_days": age}
        limit = STALE_DAYS.get(block)
        if limit is not None and (age is None or age > limit):
            stale.append(block)
    a = _block_asof(flow, "flow") or _dig_asof((flow or {}).get("capital_flow"),
                                               _ASOF_FALLBACK["flow"])
    age = _age_days(a, today)
    ages["flow"] = {"asof": a, "age_days": age}
    if age is None or age > STALE_DAYS["flow"]:
        stale.append("flow")
    return stale, ages


def classify(slow, price, ref, *, flow=None, as_of=None, thresholds=None):
    """给一只票打标签。

    - `slow`：`data/counterparty/<SYMBOL>.json`
    - `price`：`{"chg20","chg50","chg120","chg250","mcap"}`（`chg` 为小数，如 -0.067）
    - `ref`：`data/counterparty/_reference.json`
    - `flow`：`data/counterparty/flow/<SYMBOL>.json`（可选）

    返回 `{"p0", "label", "flags", "stale", "detail"}` —— **只描述结构，不给动作**。
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    tier = tier_of((price or {}).get("mcap"))
    tier_m = (((ref or {}).get("tiers") or {}).get(tier) or {}).get("metrics") or {}

    stale, ages = _staleness(slow, flow, as_of=as_of)
    m = metrics_of(slow, flow, as_of=as_of)

    detail = {
        "tier": tier,
        "mcap": (price or {}).get("mcap"),
        "metrics": m,
        "asof": ages,
        "thresholds": th,
        "pctl": {},
    }

    # ---- 前提 P0 -------------------------------------------------------
    chg120 = (price or {}).get("chg120")
    p0 = chg120 is not None and chg120 < 0
    detail["chg120"] = chg120
    if not p0:
        return {"p0": False, "label": "N", "flags": [], "stale": stale, "detail": detail}

    # ---- 3 个独立 flag --------------------------------------------------
    flags = []

    # I 内部人在接：**窗内**有买入。买入本身稀有（P50=0）→ 不做分位判定
    if "insider" not in stale and (m.get("insider_buy_count") or 0) > 0:
        flags.append("I")

    # S 空头在赌：同档分位 > 阈值（**直接取该档的 P{阈值}**，不做二次插值）
    sp, sm = m.get("short_percent"), tier_m.get("short_percent")
    detail["pctl"]["short_percent"] = _bucket(sp, sm)
    thr_s = (sm or {}).get(f"p{th['short_pct']}")
    detail["detail_thresholds"] = {"short_percent_cut": thr_s}
    if "short" not in stale and sp is not None and thr_s is not None and sp > thr_s:
        flags.append("S")

    # F 机构在撤：同档分位 ≤ 阈值
    hp, hm = m.get("holder_pct_change"), tier_m.get("holder_pct_change")
    detail["pctl"]["holder_pct_change"] = _bucket(hp, hm)
    thr_f = (hm or {}).get(f"p{th['holder_pct_change']}")
    detail["detail_thresholds"]["holder_pct_change_cut"] = thr_f
    if "institutional" not in stale and hp is not None and thr_f is not None and hp <= thr_f:
        flags.append("F")

    flags.sort()
    label = "+".join(flags) if flags else "E"
    return {"p0": True, "label": label, "flags": flags, "stale": stale, "detail": detail}
