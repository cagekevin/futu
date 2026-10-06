"""前瞻验证 —— 把今天的标签存下来，N 天后回看。

## 为什么必须有它

**n=15 太小，今天无论怎么算都下不了结论。** 唯一能回答
「这些标签到底有没有信息」的，是把标签存档、然后等。

**而且它必须今天就存** —— 60 天后补不回来。

## 为什么不能用历史价格「回填」存档

看起来可以：拿 60 天前的价格当买入价，不就有结果了？
**不行。** 因为**对家指标是今天的**（内部人 180 天窗、机构 13F、卖空双周），
把今天的指标配上 60 天前的价格 —— 那是**未来函数**，算出来的「优势」是假的。

所以：**存档只能往前走。** 本模块负责「存了之后怎么算」。

## 边界

**纯函数、无 IO** —— 输入是已读好的 `bars` / `entries`。
读文件的活在外壳 `stock/info/counterparty_review.py`。

用法：
    from engine.counterparty.forward import forward_returns, compare, verdict
    fr = forward_returns(12.34, 20261002, bars)        # {30: {...}, 60: {...}, 90: {...}}
    cmp = compare([(label, fr), ...])                  # 分组中位收益
    v = verdict(cmp["diff_60"])                        # 按 §8 判「证伪 / 未证伪」
"""

from __future__ import annotations

import statistics

HORIZONS = (30, 60, 90)

# 第 8 节写死的判据：**有 flag 组 − E 组的中位收益** 要 ≥ +2 个百分点
VERDICT_THRESHOLD = 0.02

__all__ = ["HORIZONS", "VERDICT_THRESHOLD", "forward_returns", "compare", "verdict"]


def _to_int(d):
    """`20261002` / `'2026-10-02'` / `'20261002'` → `20261002`；认不出返回 None。"""
    if isinstance(d, int):
        return d
    s = str(d or "").strip().replace("-", "").replace("/", "")
    return int(s) if len(s) == 8 and s.isdigit() else None


def _index_of(bars, date_int):
    """找该日期在 bars 里的下标；没有就取**第一个 ≥ 它的**。"""
    for i, b in enumerate(bars):
        if _to_int(b.get("date")) == date_int:
            return i
    for i, b in enumerate(bars):
        d = _to_int(b.get("date"))
        if d is not None and d >= date_int:
            return i
    return None


def forward_returns(entry_price, entry_date, bars, *, horizons=HORIZONS):
    """从 `entry_date` **之后第 h 根**的收盘算收益。

    返回 `{h: {"date":…, "ret":…}}` —— **不够 h 根的就不给**（不填、不外推）。
    """
    ei = _to_int(entry_date)
    if ei is None or not bars or not entry_price:
        return {}
    idx = _index_of(bars, ei)
    if idx is None:
        return {}
    out = {}
    for h in horizons:
        j = idx + h
        if j < len(bars):
            out[h] = {"date": bars[j]["date"], "ret": bars[j]["close"] / entry_price - 1.0}
    return out


def compare(groups, *, horizons=HORIZONS):
    """分组统计 —— `groups` 是 `[(is_flagged: bool, forward_returns_dict), ...]`。

    返回每组的笔数 / 中位 / 均值，以及**有 flag 组 − 无 flag 组** 的中位差。
    """
    out = {"horizons": {}, "n_flagged": 0, "n_plain": 0}
    for h in horizons:
        f = [fr[h]["ret"] for flag, fr in groups if flag and h in fr]
        p = [fr[h]["ret"] for flag, fr in groups if not flag and h in fr]
        out["horizons"][h] = {
            "n_flagged": len(f), "n_plain": len(p),
            "median_flagged": statistics.median(f) if f else None,
            "median_plain": statistics.median(p) if p else None,
            "mean_flagged": statistics.fmean(f) if f else None,
            "mean_plain": statistics.fmean(p) if p else None,
            "diff_median": (statistics.median(f) - statistics.median(p)) if (f and p) else None,
        }
    out["n_flagged"] = out["horizons"].get(60, {}).get("n_flagged") or \
        (out["horizons"].get(max(horizons), {}) or {}).get("n_flagged", 0)
    out["n_plain"] = out["horizons"].get(60, {}).get("n_plain") or \
        (out["horizons"].get(max(horizons), {}) or {}).get("n_plain", 0)
    return out


def verdict(diff, *, threshold=VERDICT_THRESHOLD):
    """按第 8 节写死的判据给结论。**这是「跑出来是 0 还是 1 不由解释决定」的那一步。**

    - `diff ≥ +threshold` → `"not_falsified"`（继续观察，**不据此下注**）
    - 否则 → `"falsified"`（**本方向关闭**）
    - `diff is None` → `"insufficient"`（样本不够，不下结论）
    """
    if diff is None:
        return "insufficient"
    return "not_falsified" if diff >= threshold else "falsified"
