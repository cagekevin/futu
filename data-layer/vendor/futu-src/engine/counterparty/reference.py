"""参考分布 —— **按市值档**算各对家指标的分布（分位点）。

## 为什么需要它

`short_percent` 这类指标跨市值差一个数量级 —— 拿一个**绝对阈值**去判「高空头」
在大盘股上会漏、在小盘股上会滥。所以分类器判的是**同市值档内的分位**，
而不是绝对值。这个模块产出那张「同档分位表」。

## 边界（重要）

**纯函数、无 IO** —— 输入是已经读好的 dict 列表，不读文件。
**不做判断** —— 只算分布，不判「高/低」。阈值由 `classify.py` 定。

## 只收录**无量纲**指标

| 指标 | 单位 | 无量纲？ |
|---|---|---|
| `short_percent` | 百分比 | ✅ |
| `days_to_cover` | 天 | ✅ |
| `holder_pct_change` | 百分点 | ✅ |
| `institution_quantity_change` | 家数 | ✅（家数，不随市值缩放） |
| `main_net_ratio` | 比例 | ✅（本模块现算，见下） |
| `insider_buy_count` / `insider_sell_count` | 笔数 | ✅（**用笔数不用股数** —— 股数随市值缩放） |
| ~~`trade_shares`~~ | 股 | ❌ 随市值缩放，不收录 |

`main_net_ratio` 从 `capital_distribution` 现算（无量纲）：
    (超大单+大单 净流入) / (总流入 + 总流出)

用法：
    from engine.counterparty.reference import build_reference, tier_of
    ref = build_reference([{"code": "US.UNH", "mcap": 4.7e10, "slow": slow, "flow": flow}, ...])
"""

from __future__ import annotations

import re
import statistics
from datetime import date, datetime, timedelta

# 市值档 —— 与 `fetch/opend/counterparty.py` 的 TIERS 必须一致
TIERS = [("<50亿", 0.0, 5e9), ("50-500亿", 5e9, 5e10), (">500亿", 5e10, float("inf"))]

QUANTILES = (10, 25, 50, 75, 90)

# 内部人交易类型 —— **实测词表**（100 只样本，共 11 种）：
#   其他获得 393 · 出售给发行人 178 · 卖出 156 · 衍生品获得 130 · 行权获得 94
#   初始声明 55 · 行权卖出 53 · 意向出售 51 · **买入 25** · 其他处置 18 · 衍生品处置 12
#
# ⚠️ **只有 `买入` 算买入** —— 宁漏勿错：
#   · `其他获得` / `衍生品获得` / `行权获得` 是**非市场取得**（不是掏钱买）
#   · `初始声明` 是持股申报，不是交易
# 白名单之外的类型**一律不计入买入**。
INSIDER_BUY_TYPES = ("买入",)
INSIDER_SELL_TYPES = ("卖出", "出售给发行人", "行权卖出", "意向出售", "其他处置", "衍生品处置")
INSIDER_NEUTRAL_TYPES = ("其他获得", "衍生品获得", "行权获得", "初始声明")

__all__ = ["TIERS", "QUANTILES", "tier_of", "build_reference", "percentile",
           "metrics_of", "insider_counts", "main_net_ratio", "parse_asof"]


def tier_of(mcap) -> str:
    """市值 → 档名。"""
    m = float(mcap or 0)
    for name, lo, hi in TIERS:
        if lo <= m < hi:
            return name
    return TIERS[-1][0]


def percentile(values, p: float):
    """线性插值分位 —— **样本 ≥1 就能算**（`statistics.quantiles` 要 ≥2）。"""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _num(v):
    try:
        f = float(v)
        return None if f != f else f          # NaN → None
    except (TypeError, ValueError):
        return None


_ASOF_PATTERNS = (
    # 不锚结尾 —— 要能吞掉 `2026-10-02 00:00:00`（资金流用的是带时间的字符串）
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})"), lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"^(\d{8})$"), lambda m: date(int(m[1][:4]), int(m[1][4:6]), int(m[1][6:8]))),
    # 报告期：`2026/Q3` → 季末（近似）
    (re.compile(r"^(\d{4})[/-]Q([1-4])$"),
     lambda m: date(int(m[1]), int(m[2]) * 3, 31 if int(m[2]) in (1, 4) else 30)),
    # unix 时间戳（资金流块用这个）
    (re.compile(r"^(\d{10})$"),
     lambda m: datetime.fromtimestamp(int(m[1])).date()),
)


def parse_asof(s):
    """`2026-09-21` / `20260921` / `2026/Q3` → `date`；认不出返回 None。

    ⚠️ 机构块的 `asof` 是**报告期字符串**（`2026/Q3`），不是日期 —— 所以必须两套都认。
    """
    if not s:
        return None
    t = str(s).strip()
    for pat, fn in _ASOF_PATTERNS:
        m = pat.match(t)
        if m:
            try:
                return fn(m)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------- 指标抽取
def main_net_ratio(flow_doc):
    """资金分布 → 无量纲的「主力净流入占比」。

    `capital_distribution` 返回 1 行，字段是 super/big/mid/small 的 in/out。
    比值 = (超大单+大单 净) / (总流入 + 总流出)，落在 [-1, 1]。
    """
    rows = (flow_doc or {}).get("capital_distribution") or []
    if not rows:
        return None
    r = rows[0]
    ins = sum(_num(r.get(f"capital_in_{k}")) or 0.0 for k in ("super", "big", "mid", "small"))
    outs = sum(_num(r.get(f"capital_out_{k}")) or 0.0 for k in ("super", "big", "mid", "small"))
    main_in = (_num(r.get("capital_in_super")) or 0.0) + (_num(r.get("capital_in_big")) or 0.0)
    main_out = (_num(r.get("capital_out_super")) or 0.0) + (_num(r.get("capital_out_big")) or 0.0)
    denom = ins + outs
    return ((main_in - main_out) / denom) if denom else None


def insider_counts(slow_doc, *, as_of=None, window_days=180):
    """内部人**笔数**（不是股数）—— 按白名单分类，**并加时间窗**。

    ⚠️ **必须加窗**：接口返回的是「最近 N 笔」，可能横跨好几年 ——
    不筛日期的话，一只票**两年前的买入**会被当成今天的信息。

    `as_of` 为窗口右端（默认取该股内部人记录里的最新日期）。
    返回 `(buy_count, sell_count, types_seen)`；`types_seen` 是**窗内**的全量类型计数。
    """
    entry = (slow_doc or {}).get("insider") or {}
    records = entry.get("records") or []
    if not records:
        return 0, 0, {}

    dates = [parse_asof(r.get("max_trade_date_str")) for r in records]
    dates = [d for d in dates if d]
    end = parse_asof(as_of) or (max(dates) if dates else None)
    if end is None:
        return 0, 0, {}
    start = end - timedelta(days=window_days)

    buy = sell = 0
    seen: dict[str, int] = {}
    for r, d in zip(records, [parse_asof(x.get("max_trade_date_str")) for x in records]):
        if d is None or not (start <= d <= end):
            continue
        t = str(r.get("transaction_type") or "未知")
        seen[t] = seen.get(t, 0) + 1
        if t in INSIDER_BUY_TYPES:
            buy += 1
        elif t in INSIDER_SELL_TYPES:
            sell += 1
    return buy, sell, seen


def metrics_of(slow_doc, flow_doc, *, as_of=None):
    """从一只票的落盘数据里抽**无量纲**指标。**classify 也用这个**（保证口径一致）。"""
    short_s = (((slow_doc or {}).get("short") or {}).get("summaries") or {})
    inst_s = (((slow_doc or {}).get("institutional") or {}).get("summaries") or {})
    buy, sell, _types = insider_counts(slow_doc, as_of=as_of)
    return {
        "short_percent": _num((short_s.get("get_short_interest") or {}).get("short_percent")),
        "days_to_cover": _num((short_s.get("get_short_interest") or {}).get("days_to_cover")),
        "holder_pct_change": _num((inst_s.get("get_shareholders_institutional") or {}).get("holder_pct_change")),
        "institution_quantity_change": _num(
            (inst_s.get("get_shareholders_institutional") or {}).get("institution_quantity_change")),
        "main_net_ratio": main_net_ratio(flow_doc),
        "insider_buy_count": float(buy),
        "insider_sell_count": float(sell),
    }


# ---------------------------------------------------------------- 主函数
def build_reference(docs, *, quantiles=QUANTILES, as_of=None):
    """按市值档建参考分布。

    `docs`：`[{"code": str, "mcap": float, "slow": dict, "flow": dict}]` —— **已读好的 dict**。
    `as_of`：内部人计数的时间窗右端（默认取各股自己的最新交易日）。
    返回可直接落盘 `data/counterparty/_reference.json` 的结构。
    """
    by_tier: dict[str, list[dict]] = {name: [] for name, _lo, _hi in TIERS}
    types_all: dict[str, int] = {}

    for d in docs:
        tier = tier_of(d.get("mcap"))
        by_tier.setdefault(tier, []).append(metrics_of(d.get("slow"), d.get("flow"), as_of=as_of))
        _b, _s, seen = insider_counts(d.get("slow"), as_of=as_of)
        for t, cnt in (seen or {}).items():
            types_all[t] = types_all.get(t, 0) + int(cnt or 0)

    tiers_out = {}
    for name, items in by_tier.items():
        if not items:
            tiers_out[name] = {"n": 0, "metrics": {}}
            continue
        metrics = {}
        for key in items[0]:
            vals = [it.get(key) for it in items]
            present = [v for v in vals if v is not None]
            metrics[key] = {
                "n": len(present),
                "missing": len(vals) - len(present),
                **{f"p{q}": percentile(present, q) for q in quantiles},
                "mean": (statistics.fmean(present) if present else None),
            }
        tiers_out[name] = {"n": len(items), "metrics": metrics}

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_total": len(docs),
        "quantiles": list(quantiles),
        "tiers": tiers_out,
        "insider_type_vocabulary": dict(sorted(types_all.items(), key=lambda kv: -kv[1])),
        "insider_buy_types": list(INSIDER_BUY_TYPES),
        "insider_sell_types": list(INSIDER_SELL_TYPES),
        "insider_neutral_types": list(INSIDER_NEUTRAL_TYPES),
        "note": ("分位点按**同市值档**算 —— 分类器判分位，不判绝对值。"
                 "内部人用**笔数**不用股数（股数随市值缩放）。"
                 "`insider_type_vocabulary` 是全量类型计数，"
                 "白名单外的类型**不计入买入**（宁漏勿错）。"),
    }
