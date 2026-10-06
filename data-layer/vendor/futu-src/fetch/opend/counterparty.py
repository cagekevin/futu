"""对家层取数 —— 内部人 / 机构 / 卖空 / 资金流 / 评级。

**为什么有这一层**：见 `docs/plan-counterparty.md`。
一句话 —— 判断「这个下跌是不是机会」需要的信息（**谁在卖、他为什么卖、他卖完了没有**）
**根本不在价格序列里**，所以给价格筛选器接上第二个信息源。

## 只做 5 类（不是 6）

| 类目 | 接口 | 美股 |
|---|---|---|
| 内部人 | `get_insider_trade_list` | ✅ 仅美股 |
| 机构 | `get_shareholders_institutional` / `_holding_changes` | ✅ |
| 卖空 | `get_short_interest` / `get_daily_short_volume` | ✅ |
| 资金流 | `get_capital_flow` / `get_capital_distribution` | ✅ |
| 评级 | `get_research_rating_summary` | ✅ 仅美股 |
| ~~回购~~ | ~~`get_corporate_actions_buybacks`~~ | ❌ **仅港股/A股 → 废除** |
| ~~解禁~~ | ~~`get_ipo_list`~~ | ❌ 是 IPO 列表，**不是解禁日历**，废除 |

## 落盘（按频率分存，见计划 4.2）

    data/counterparty/<SYMBOL>.json        慢变：内部人 / 机构 / 卖空 / 评级
    data/counterparty/flow/<SYMBOL>.json   日频：资金流
    data/counterparty/_manifest.json       跑批记录（`ok` 就是计划里说的 count）

## 实测出来的三个「文档没写」的坑

1. **返回值个数不固定** —— `get_short_interest` / `get_daily_short_volume` 返回 **3 个**
   （`[2]` 是另一个 DataFrame），其余返回 2 个。**不能 `ret, data = fn()`**。
2. **返回类型不固定** —— 多数是 DataFrame，但 `get_research_rating_summary` 返回 **dict**
   （`{"next_key":…, "inst_rating_summary_list":[…]}`）。
3. **资金流不吃订阅** —— `get_capital_flow` 在**没有 subscribe** 的情况下直接返回了 250 行。
   所以下面**先试订阅、失败也不致命**（订阅额度可能根本没被占用）。

## 上游不替下游判断

只存**原始记录** + **描述性汇总**（按 `transaction_type` 分组计数之类），
**不判断「买/卖」「看多/看空」** —— 那留给 `engine/counterparty/classify.py`。

用法：
    python3 fetch/opend/counterparty.py US.UNH
    python3 fetch/opend/counterparty.py UNH RDDT CSCO MU
    python3 fetch/opend/counterparty.py --from-picks 2026-10-04
    python3 fetch/opend/counterparty.py --from-picks 2026-10-04 --set strong
    python3 fetch/opend/counterparty.py US.UNH --offline
    python3 fetch/opend/counterparty.py --quota
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data" / "counterparty"
MANIFEST_NAME = "_manifest.json"

# 慢变：(落盘键, 方法名, 参数)。`num` 上限来自技能文档。
SLOW_BLOCKS = [
    ("insider", "get_insider_trade_list", {"num": 50}),
    ("institutional", "get_shareholders_institutional", {"num": 50}),
    ("institutional", "get_shareholders_holding_changes", {"num": 50}),
    ("short", "get_short_interest", {"num": 50}),
    ("short", "get_daily_short_volume", {"num": 50}),
    ("rating", "get_research_rating_summary", {"num": 20}),
]

# 日频资金流
FLOW_BLOCKS = [
    ("capital_flow", "get_capital_flow", {"period_type": "DAY"}),
    ("capital_distribution", "get_capital_distribution", {}),
]

# 「asof」取哪个字段（实测得出）
ASOF_FIELD = {
    "get_insider_trade_list": "max_trade_date_str",
    "get_shareholders_institutional": "period_text",
    "get_shareholders_holding_changes": "period_text",
    "get_short_interest": "timestamp_str",
    "get_daily_short_volume": "timestamp_str",
    "get_research_rating_summary": "update_time_str",
    "get_capital_flow": "capital_flow_item_time",       # unix 时间戳
    "get_capital_distribution": "update_time",
}

SUBTYPE_FOR = {
    "get_capital_flow": "CAPITAL_FLOW",
    "get_capital_distribution": "CAPITAL_DISTRIBUTION",
}

__all__ = ["fetch_one", "fetch_many", "save_one", "load_one", "quota_report", "codes_from_picks"]


# ---------------------------------------------------------------- 技能通道
_COMMON = None


def _common():
    """导入 futu 技能的 `common.py`（复用连接 / 错误分类 / DataFrame 转换）。"""
    global _COMMON
    if _COMMON is not None:
        return _COMMON
    skill = os.environ.get("FUTU_SKILL_DIR") or os.path.expanduser("~/.codebuddy/skills/futu")
    scripts = Path(skill) / "futuapi" / "scripts"
    if not (scripts / "common.py").exists():
        raise SystemExit(
            f"找不到 futu 技能脚本目录：{scripts}\n"
            "（安装 futu 技能，或用 FUTU_SKILL_DIR 指定技能根目录）"
        )
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import common  # type: ignore  # noqa: PLC0415

    _COMMON = common
    return common


# ---------------------------------------------------------------- 限频闸门
# ⚠️ 实测：每 30 秒最多 30 次，超了直接报「频率太高，请求失败」。
# 100 只 × 8 次 = 800 次 → 不限频的话第 ~40 只就开始全失败。
_RATE_MAX = 28          # 留 2 次余量
_RATE_WINDOW = 30.0
_rate_calls: deque = deque()


def _rate_gate():
    """滑动窗口限频 —— 保证 30 秒内不超过 30 次。"""
    while _rate_calls and time.monotonic() - _rate_calls[0] > _RATE_WINDOW:
        _rate_calls.popleft()
    if len(_rate_calls) >= _RATE_MAX:
        wait = _RATE_WINDOW - (time.monotonic() - _rate_calls[0]) + 0.05
        if wait > 0:
            time.sleep(wait)
        while _rate_calls and time.monotonic() - _rate_calls[0] > _RATE_WINDOW:
            _rate_calls.popleft()
    _rate_calls.append(time.monotonic())


# ---------------------------------------------------------------- 代码归一化
def normalize_code(code, market="US"):
    """`GRFS` → `US.GRFS`；已有前缀的原样返回。

    ⚠️ **必须归一化** —— 实测 `universe.json` 里的代码是**裸的**（`GRFS`），
    直接喂接口会报 `format of code GRFS is wrong`；
    而 `picks.json['signals']` 里是**带前缀的**（`US.VSAT`）。两个来源不一致。
    """
    c = str(code or "").strip().upper()
    return c if "." in c else f"{market}.{c}"


# ---------------------------------------------------------------- 形状适配
def _records(common, data):
    """SDK 返回可能是 DataFrame / dict / list —— 统一成记录列表。"""
    if data is None:
        return []
    if hasattr(data, "iloc"):                                    # DataFrame
        return [] if common.is_empty(data) else common.df_to_records(data)
    if isinstance(data, dict):                                   # rating 是 dict
        for key in ("items", "list", "inst_rating_summary_list", "data"):
            if isinstance(data.get(key), list):
                return [common.to_jsonable(x) for x in data[key]]
        return [common.to_jsonable(data)]
    if isinstance(data, (list, tuple)):
        return [common.to_jsonable(x) for x in data]
    return [common.to_jsonable(data)]


def _call(ctx, common, method, code, kwargs):
    """调一个接口 → `(records, meta, error)`。

    **失败不抛，只记 error** —— 单块失败不该中断整只。
    """
    fn = getattr(ctx, method, None)
    if fn is None:
        return None, None, f"SDK 无此方法：{method}"
    _rate_gate()                                     # 每 30 秒最多 30 次
    args = dict(kwargs)
    # 资金流两个接口的参数名是 stock_code，其余是 code
    args["stock_code" if method.startswith("get_capital_") else "code"] = code
    try:
        result = fn(**args)
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"

    # ⚠️ 返回值个数不固定：short_interest / daily_short_volume 是 3 个
    if not isinstance(result, tuple) or len(result) < 2:
        return None, None, f"返回值形状异常：{type(result).__name__}"
    ret, data = result[0], result[1]
    if ret != common.RET_OK:
        return None, None, str(data)

    records = _records(common, data)
    meta: dict = {}
    attrs = getattr(data, "attrs", None) or {}
    for k in ("all_count", "next_key"):
        if k in attrs:
            meta[k] = common.to_jsonable(attrs[k])
    if isinstance(data, dict) and "next_key" in data:
        meta["next_key"] = common.to_jsonable(data["next_key"])
    if len(result) > 2:
        meta["extra"] = [len(x) if hasattr(x, "__len__") else None for x in result[2:]]
    return records, meta, None


def _num(v):
    try:
        f = float(v)
        return 0.0 if f != f else f          # NaN → 0
    except (TypeError, ValueError):
        return 0.0


def _asof(method, records):
    field = ASOF_FIELD.get(method)
    if not field:
        return None
    vals = [str(r.get(field)) for r in records if r.get(field)]
    return max(vals) if vals else None


def _summarize(method, records):
    """**描述性**汇总 —— 只做分组计数/取最新，**不做买卖判断**。"""
    if not records:
        return {}
    if method == "get_insider_trade_list":
        by = {}
        for r in records:
            t = str(r.get("transaction_type") or "未知")
            e = by.setdefault(t, {"count": 0, "shares": 0.0})
            e["count"] += 1
            e["shares"] += _num(r.get("trade_shares"))
        return {"by_type": by}

    if method == "get_shareholders_institutional":
        latest = records[0]                      # 接口按 period 倒序返回
        return {
            "latest_period": latest.get("period_text"),
            "institution_quantity": latest.get("institution_quantity"),
            "institution_quantity_change": latest.get("institution_quantity_change"),
            "holder_pct": latest.get("holder_pct"),
            "holder_pct_change": latest.get("holder_pct_change"),
            "periods": len({r.get("period_text") for r in records}),
        }

    if method == "get_shareholders_holding_changes":
        periods = {}
        for r in records:
            p = str(r.get("period_text") or "未知")
            e = periods.setdefault(p, {"count": 0, "net_shares": 0.0, "up": 0, "down": 0})
            e["count"] += 1
            d = _num(r.get("share_change_num"))
            e["net_shares"] += d
            e["up" if d > 0 else "down"] += 1
        return {"by_period": periods}

    if method in ("get_short_interest", "get_daily_short_volume"):
        latest = records[0]
        return {
            "latest_date": latest.get("timestamp_str"),
            "short_percent": latest.get("short_percent"),
            "days_to_cover": latest.get("days_to_cover"),
            "daily_trade_avg_ratio": latest.get("daily_trade_avg_ratio"),
            "n": len(records),
        }

    if method == "get_research_rating_summary":
        return {"institutions": len(records)}
    return {}


# ---------------------------------------------------------------- 单只取数
def fetch_one(ctx, code, *, verbose=True):
    """拉一只票的全部对家数据。返回 `(slow_doc, flow_doc)`，**失败只进 errors**。"""
    common = _common()
    slow = {
        "code": code,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "insider": None, "institutional": None, "short": None, "rating": None,
        "errors": [],
    }
    flow = {"code": code, "fetched_at": slow["fetched_at"], "errors": []}

    for block, method, kwargs in SLOW_BLOCKS:
        records, meta, err = _call(ctx, common, method, code, kwargs)
        if err:
            slow["errors"].append({"block": block, "method": method, "error": err})
            continue
        entry = slow.get(block) or {"records": [], "calls": [], "summaries": {}}
        entry["records"].extend(records or [])
        entry["calls"].append({"method": method, "n": len(records or []), **(meta or {})})
        entry["summaries"][method] = _summarize(method, records or [])
        a = _asof(method, records or [])
        if a:
            entry["asof"] = max(entry.get("asof") or "", a)
        slow[block] = entry

    # 资金流：实测**不订阅也有数据** → 先试订阅，失败不致命
    sub_types = [getattr(common.SubType, SUBTYPE_FOR[m], None)
                 for _b, m, _k in FLOW_BLOCKS if m in SUBTYPE_FOR]
    sub_types = [s for s in sub_types if s is not None]
    subscribed = False
    if sub_types:
        try:
            ret, err = ctx.subscribe([code], sub_types)
            subscribed = ret == common.RET_OK or "already" in str(err).lower() or "已订阅" in str(err)
        except Exception:  # noqa: BLE001
            pass                                   # 不致命：实测不订阅也能拿到

    for block, method, kwargs in FLOW_BLOCKS:
        records, meta, err = _call(ctx, common, method, code, kwargs)
        if err:
            flow["errors"].append({"block": block, "method": method, "error": err})
            continue
        flow[block] = records or []
        flow.setdefault("summaries", {})[method] = _summarize(method, records or [])
        a = _asof(method, records or [])
        if a:
            flow["asof"] = max(flow.get("asof") or "", a)

    if subscribed:
        try:
            ctx.unsubscribe([code], sub_types)
        except Exception:  # noqa: BLE001
            pass

    if verbose:
        bad = len(slow["errors"]) + len(flow["errors"])
        print(f"  {code:<11} 内部人 {_n(slow, 'insider'):>3}  机构 {_n(slow, 'institutional'):>3}  "
              f"卖空 {_n(slow, 'short'):>3}  评级 {_n(slow, 'rating'):>3}  "
              f"资金流 {len(flow.get('capital_flow') or []):>3}  "
              f"{'OK' if bad == 0 else f'{bad} 处失败'}")
    return slow, flow


def _n(doc, block):
    entry = doc.get(block)
    return len(entry.get("records") or []) if entry else 0


# ---------------------------------------------------------------- 落盘 / 读取
def _symbol_path(code, root=None):
    return (Path(root) if root else DATA) / f"{code}.json"


def _flow_path(code, root=None):
    return (Path(root) if root else DATA) / "flow" / f"{code}.json"


def save_one(code, slow, flow, *, root=None):
    """按频率分存 —— 慢变按代码，资金流另开序列文件。"""
    p = _symbol_path(code, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(slow, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    fp = _flow_path(code, root)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(flow, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return p, fp


def load_one(code, *, root=None):
    """读本地（`--offline` 用）。不存在返回 `(None, None)`。"""
    p, fp = _symbol_path(code, root), _flow_path(code, root)
    slow = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    flow = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else None
    return slow, flow


def record_run(entry, *, root=None):
    """把一次跑批记进 `_manifest.json`（记「跑批」不是「产物」，见计划 4.5）。"""
    path = (Path(root) if root else DATA) / MANIFEST_NAME
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"runs": []}
    doc["runs"].append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 额度
def quota_report(ctx, *, verbose=True):
    """查两种额度。**订阅余量是可查的**（v4 曾写「无接口」，是错的）。"""
    common = _common()
    out = {}
    ret, data = ctx.get_history_kl_quota(get_detail=True)
    if ret == common.RET_OK:
        try:
            used, remain, _d = data
        except (TypeError, ValueError):
            used, remain = data[0], data[1]
        out["history_kline"] = {"used": int(used), "remaining": int(remain)}
    else:
        out["history_kline"] = {"error": str(data)}

    try:
        r2, d2 = ctx.query_subscription()
        out["subscription"] = ({"used": common.to_jsonable(d2)} if r2 == common.RET_OK
                               else {"error": str(d2)})
    except Exception as exc:  # noqa: BLE001
        out["subscription"] = {"error": f"{type(exc).__name__}: {exc}"}

    if verbose:
        hk = out.get("history_kline", {})
        print(f"历史K线额度  used={hk.get('used')}  remaining={hk.get('remaining')}")
        print(f"订阅额度      {json.dumps(out.get('subscription', {}), ensure_ascii=False)[:220]}")
    return out


# ---------------------------------------------------------------- 候选集
def _quota_light(common):
    """轻量查额度 —— 判断「这一批有没有真的吃额度」。"""
    try:
        ctx = common.create_quote_context()
        try:
            return quota_report(ctx, verbose=False)
        finally:
            common.safe_close(ctx)
    except Exception:  # noqa: BLE001
        return None


def codes_from_picks(date, which="signals", *, root=None):
    """从 `picks.json` 取候选 —— **只能用既有字段**（不许临时脚本，见计划 3.2）。"""
    path = (Path(root) if root else ROOT) / "data" / "scan" / date / "picks.json"
    if not path.exists():
        raise SystemExit(f"找不到 {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get(which) or []
    if which not in ("signals", "strong"):
        raise SystemExit(f"--set 只能是 signals 或 strong，收到 {which}")
    return [r["code"] for r in rows if r.get("code")]


# ---------------------------------------------------------------- 分层抽样 + 参考分布
# 市值档 —— **必须与 engine/counterparty/reference.py 的 TIERS 一致**
TIERS = [("<50亿", 0.0, 5e9), ("50-500亿", 5e9, 5e10), (">500亿", 5e10, float("inf"))]


def _tier_name(row):
    m = float(row.get("mcap") or 0)
    for name, lo, hi in TIERS:
        if lo <= m < hi:
            return name
    return TIERS[-1][0]


def _latest_scan_date(root=None):
    d = (Path(root) if root else ROOT) / "data" / "scan"
    dates = sorted(p.name for p in d.iterdir() if p.is_dir() and p.name[:1].isdigit())
    if not dates:
        raise SystemExit(f"{d} 下没有日期目录")
    return dates[-1]


def sample_universe(n=100, *, date=None, root=None, min_per_tier=15):
    """从 `universe.json` 分层抽 n 只 —— **确定性**（同输入必同输出）。

    3 档市值按占比分配名额，**档内按行业轮转取**（保证行业散开），每档至少 `min_per_tier` 只。
    """
    date = date or _latest_scan_date(root)
    path = (Path(root) if root else ROOT) / "data" / "scan" / date / "universe.json"
    if not path.exists():
        raise SystemExit(f"找不到 {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))["rows"]

    groups: dict[str, list] = {name: [] for name, _l, _h in TIERS}
    for r in rows:
        m = float(r.get("mcap") or 0)
        for name, lo, hi in TIERS:
            if lo <= m < hi:
                groups[name].append(r)
                break

    total = sum(len(g) for g in groups.values()) or 1
    # 名额：先取整（floor），再把差额补到最大的一档 —— 保证**总数正好是 n**
    quota = {name: max(min_per_tier, int(n * len(g) / total))
             for name, g in groups.items() if g}
    while sum(quota.values()) < n:
        quota[max(quota, key=lambda k: quota[k])] += 1
    while sum(quota.values()) > n:
        k = max(quota, key=lambda k: quota[k])
        if quota[k] <= min_per_tier:
            break
        quota[k] -= 1

    picked = []
    for name, g in groups.items():
        if not g:
            continue
        want = min(quota.get(name, 0), len(g))
        buckets: dict[str, list] = {}
        for r in g:
            buckets.setdefault(r.get("industry") or "未知", []).append(r)
        for k in buckets:
            buckets[k].sort(key=lambda r: r["code"])       # 确定性
        names, got = sorted(buckets), []
        while len(got) < want:
            added = False
            for ind in names:
                if buckets[ind]:
                    got.append(buckets[ind].pop(0))
                    added = True
                    if len(got) >= want:
                        break
            if not added:
                break
        picked.extend(got)
    return picked


def build_and_save_reference(codes, *, date=None, root=None, verbose=True):
    """读**本地已落盘**的对家数据 + universe 的市值 → 参考分布。

    `mcap` 从 `universe.json` 取（对家数据里没有市值字段）。
    """
    from engine.counterparty.reference import build_reference  # noqa: PLC0415

    date = date or _latest_scan_date(root)
    upath = (Path(root) if root else ROOT) / "data" / "scan" / date / "universe.json"
    mcap: dict = {}
    if upath.exists():
        for r in json.loads(upath.read_text(encoding="utf-8"))["rows"]:
            mcap[normalize_code(r["code"])] = r.get("mcap")      # ⚠️ 必须归一化

    docs = []
    for raw in codes:
        # ⚠️ **两处都要归一化** —— `sample_universe` 返回的是**裸代码**（`GRFS`），
        # 而 `mcap` 的键是归一化后的（`US.GRFS`）。少归一化一处就是**静默错档**（不报错）。
        code = normalize_code(raw)
        slow, flow = load_one(code, root=root)
        if slow is None:
            continue
        docs.append({"code": code, "mcap": mcap.get(code), "slow": slow, "flow": flow})

    ref = build_reference(docs)
    path = (Path(root) if root else DATA) / "_reference.json"
    path.write_text(json.dumps(ref, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    if verbose:
        print(f"\n参考分布：{ref['n_total']} 只")
        for name, t in ref["tiers"].items():
            sp = ((t.get("metrics") or {}).get("short_percent") or {})
            hp = ((t.get("metrics") or {}).get("holder_pct_change") or {})
            print(f"  {name:<10} n={t['n']:>3}   "
                  f"short_percent P50={_f(sp.get('p50'))} P90={_f(sp.get('p90'))}   "
                  f"holder_pct_change P50={_f(hp.get('p50'))}")
        print(f"  内部人类型词表：{json.dumps(ref['insider_type_vocabulary'], ensure_ascii=False)}")
        print(f"  落盘：{path}")
    return ref, path


def _f(v):
    return "—" if v is None else f"{v:.2f}"


def local_codes(root=None):
    """本地已有的对家数据标的（`--rebuild-reference` 用，**不联网**）。"""
    d = Path(root) if root else DATA
    return sorted(f[:-5] for f in os.listdir(d)
                  if f.startswith("US.") and f.endswith(".json"))


# ---------------------------------------------------------------- 主流程
def fetch_many(codes, *, batch_size=120, root=None, offline=False, verbose=True,
               skip_existing=True):
    """分批取数 + 每批显式断开 + 等归还。

    ⚠️ **单连接** —— 技能文档：「反订阅后需所有连接都反订阅同一标的，额度才会释放」。
    """
    started = datetime.now().isoformat(timespec="seconds")
    codes = [normalize_code(c) for c in codes]      # 统一加市场前缀
    ok, failed = [], []
    blocks_stat = {"insider": 0, "institutional": 0, "short": 0, "rating": 0}

    if offline:
        for code in codes:
            slow, _flow = load_one(code, root=root)
            (ok if slow else failed).append(code)
        return {"started_at": started, "finished_at": datetime.now().isoformat(timespec="seconds"),
                "requested": len(codes), "ok": len(ok), "failed": failed,
                "blocks": blocks_stat, "batches": 0, "offline": True}

    common = _common()
    ctx = None
    batches = 0
    quota_before = None
    try:
        for start in range(0, len(codes), batch_size):
            chunk = codes[start:start + batch_size]
            batches += 1
            if verbose:
                print(f"[第 {batches} 批] {len(chunk)} 只"
                      f"（{start + 1}~{start + len(chunk)} / {len(codes)}）")
            ctx = common.create_quote_context()          # 每批新连接 —— 保证额度能归还
            if quota_before is None:
                quota_before = quota_report(ctx, verbose=False)
            for code in chunk:
                # 增量：本地已有且**无错误**就跳过（限频下这很关键 —— 可断点续跑）
                if skip_existing:
                    old, _of = load_one(code, root=root)
                    if old is not None and not old.get("errors"):
                        ok.append(code)
                        if verbose:
                            print(f"  {code:<11} 已有，跳过")
                        continue
                try:
                    slow, flow = fetch_one(ctx, code, verbose=verbose)
                    save_one(code, slow, flow, root=root)
                    ok.append(code)
                    for b in blocks_stat:
                        blocks_stat[b] += 1 if slow.get(b) else 0
                except Exception as exc:  # noqa: BLE001
                    failed.append(code)
                    if verbose:
                        print(f"  {code:<11} 整体失败：{type(exc).__name__}: {exc}")
            common.safe_close(ctx)
            ctx = None
            # ⚠️ 实测：对家数据**不吃任何额度**（历史K线 298→298、订阅 0→0）。
            # 所以只在**额度真的变了**的时候才等 —— 不无谓地睡 60 秒。
            if start + batch_size < len(codes):
                now = _quota_light(common)
                if now is not None and now != quota_before:
                    if verbose:
                        print("  额度有变化，等 60 秒归还 …")
                    time.sleep(60)
                elif verbose:
                    print("  额度未变（对家数据不吃额度），继续 …")
    finally:
        if ctx is not None:
            common.safe_close(ctx)

    quota_after = None
    if codes:
        try:
            c2 = common.create_quote_context()
            quota_after = quota_report(c2, verbose=False)
            common.safe_close(c2)
        except Exception:  # noqa: BLE001
            pass

    entry = {
        "started_at": started,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "requested": len(codes),
        "ok": len(ok),                      # ← 这就是计划 4.5 说的「count」
        "failed": failed,
        "blocks": blocks_stat,
        "batches": batches,
        "quota": {"before": quota_before, "after": quota_after},
    }
    record_run(entry, root=root)
    return entry


def main():
    ap = argparse.ArgumentParser(description="对家层取数（内部人/机构/卖空/资金流/评级）")
    ap.add_argument("codes", nargs="*", help="代码，如 US.UNH 或 UNH")
    ap.add_argument("--from-picks", metavar="DATE", help="从 data/scan/<DATE>/picks.json 取候选")
    ap.add_argument("--set", default="signals", choices=("signals", "strong"),
                    help="配合 --from-picks：signals（默认 41 只）/ strong（590 只）")
    ap.add_argument("--sample", type=int, metavar="N",
                    help="从 universe.json **分层抽** N 只（3 档市值 × 档内按行业轮转）")
    ap.add_argument("--universe-date", help="universe.json 的日期（默认最新）")
    ap.add_argument("--build-reference", action="store_true",
                    help="取完算参考分布 → data/counterparty/_reference.json")
    ap.add_argument("--rebuild-reference", action="store_true",
                    help="**不取数**，用**分层抽样名单**的本地数据重算参考分布（改口径后用）")
    ap.add_argument("--batch-size", type=int, default=120,
                    help="每批只数（**保险** —— 实测对家数据不吃额度）")
    ap.add_argument("--offline", action="store_true", help="只读本地，不连 OpenD")
    ap.add_argument("--quota", action="store_true", help="只查额度，不取数")
    args = ap.parse_args()

    if args.quota:
        common = _common()
        ctx = common.create_quote_context()
        try:
            quota_report(ctx)
        finally:
            common.safe_close(ctx)
        return

    if args.rebuild_reference:
        # ⚠️ **必须用「分层抽样」的名单，不能用「本地所有文件」** ——
        # 本地文件里混着候选集（信号股 / 强势股），拿它们定阈值就是**循环论证**：
        # 用候选集去定义候选集自己的「高分位」，阈值会被候选集拉偏。
        picked = sample_universe(args.sample or 100, date=args.universe_date)
        codes = [r["code"] for r in picked]
        print(f"用**分层抽样的 {len(codes)} 只**重算参考分布（不联网，只读本地）")
        build_and_save_reference(codes, date=args.universe_date)
        return

    if args.sample:
        picked = sample_universe(args.sample, date=args.universe_date)
        codes = [r["code"] for r in picked]
        dist: dict = {}
        for r in picked:
            dist[_tier_name(r)] = dist.get(_tier_name(r), 0) + 1
        print(f"分层抽样 {len(codes)} 只（universe {args.universe_date or _latest_scan_date()}）")
        print(f"  档位分布：{dist}")
    else:
        codes = [c if "." in c else f"US.{c}" for c in args.codes]
        if args.from_picks:
            codes = codes_from_picks(args.from_picks, args.set)
    if not codes:
        ap.error("要给代码，或用 --from-picks / --sample")

    print(f"\n对家层取数：{len(codes)} 只"
          f"{'（离线）' if args.offline else f'，批大小 {args.batch_size}'}\n")
    entry = fetch_many(codes, batch_size=args.batch_size, offline=args.offline)
    print(f"\n完成：成功 {entry['ok']} / {entry['requested']}"
          f"，失败 {len(entry['failed'])}，批数 {entry['batches']}")
    print(f"落盘：{DATA}/<SYMBOL>.json + flow/<SYMBOL>.json + {MANIFEST_NAME}")

    if args.build_reference:
        build_and_save_reference(codes, date=args.universe_date)


if __name__ == "__main__":
    main()
