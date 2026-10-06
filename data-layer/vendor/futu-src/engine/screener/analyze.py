"""综合分析 —— 把当天文件夹里的所有粗筛产物**合成一份候选清单**。

读 `data/scan/<日期>/`（由 `scan.py` 采集），**不联网、不重算**。
换个口径反复试，改这里就行，采集不用重跑。

## 合成的思路：三层叠加

    ① 技术信号  —— 自选股里 CD / 顾比 触发的（`cd.json` / `guppy.json`）
    ② 市场强度  —— 全市场 RPS（`rps.json`），**≥ `RPS_MIN`（默认 80）才算达标**
    ③ 行业顺势  —— 所属行业在不在集体涨（`industry.json`）

**①是两张表**：**一个信号一张表**（CD 底背离 / 顾比突破**分开**），各自列出**全部**命中的 ——
信号是结论，结论不能只列前几名（`--top` 只管后面的榜）。
每条补上它在**全市场**的 RPS（**四个周期**）和**所属行业**的多周期状态 ——
这样一眼看得出「这个信号是逆势还是顺势、是短期冲的还是长期就强」。

每张表还带**自己的**字段：CD 显示触发价、顾比显示突破价 / 顾比线 ——
别再硬凑成一张大表（那样每个信号都得留一半空格）。

## 四个周期一起看，别只看一个

快照里本来就带 20 / 50 / 120 / 250 日涨幅，所以 RPS 和行业统计**每个周期各算一遍**：

    RPS20 高、RPS250 低  →  最近才起来（可能是短炒）
    RPS20 低、RPS250 高  →  长期强、最近歇着（可能是回踩）
    四个都高            →  一路强（欧奈尔要的就是这种）

排序用「**满足几项**」（0~2），**不加权** —— 加权就得解释权重怎么来的，说不清不如不加。

纯读文件 + 组装，**不改任何指标口径**。

---

## `## 解读（AI 写）` 那一段

**管线只管把数字算全**（`picks.json` 的 `evidence`），**AI 只管把它讲成人话**。

- 解读里**别现算数字** —— 缺什么就在 `_evidence()` 里加，让脚本直接生成
- 数字要**能被核对**：报告里紧跟着 `### 解读引用的数字` 那张表
- **样本别错位**：写「X 在 Y」之前先看「这句话的主语是谁、我数的是谁」
  （`evidence["industries"]` 的 `long_up` / `long_up_and_down_20d` 就是干这个的）
- **用词别超出数据**：数据说 250 日在跌，就写「250 日在跌」，别推成「一直跌」

就这些 —— **不加校验闸**：AI 怎么写是 AI 的事，管线不卡。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fetch import day as day_store  # noqa: E402
from engine.indicators.industry import STATE_NOTE, STATE_ORDER, classify  # noqa: E402
from engine.indicators.rs import RPS_MIN  # noqa: E402
from engine.screener.local import load_all as load_local  # noqa: E402
from engine.screener.pick import cross_markdown, print_cross  # noqa: E402
from engine.screener.table import md_table as _md_table  # noqa: E402

PRODUCTS = ("universe", "rps", "industry", "plates", "cd", "guppy", "screener")
FALLBACK_PERIODS = (20, 50, 120, 250)
SIGNAL_LABELS = {"cd": "CD底背离", "guppy": "顾比突破"}
AI_SECTION = "## 解读（AI 写）"
# 这段说明是**管线自己写的**，不是 AI 写的 —— 抠旧解读时要把它一起去掉，
# 否则每重跑一次就多叠一层（踩过：报告里叠了 7 遍）。
AI_NOTE = (
    "> **表格是机器整理的，这一段是人话。** 管线只负责把数据摆好 ——"
    "「今天发生了什么、哪些值得看、为什么」得由 AI 读 `picks.json` 后写在这里。\n"
    "> 重跑分析时这段会被**保留**（见 `analyze.keep_ai_section`）。"
)
# 「解读」到哪儿为止。**必须显式标出**：证据小节是 `###` 级（比 `## 解读` 低一级），
# 光靠「找下一个 `## `」会把机器写的证据也当成 AI 正文一起保留 —— 每重跑一次叠一层。
AI_END = "### 解读引用的数字（可核对）"

__all__ = ["load_day", "analyze", "render", "to_markdown", "keep_ai_section"]


def load_day(date=None, market="US", root=None):
    """把当天文件夹读进来 —— 缺哪步给 None，**不报错**（分析要能容忍只跑了一部分）。"""
    return {name: day_store.load(name, date=date, market=market, root=root) for name in PRODUCTS}


def watch_plates():
    """自选里的**富途板块指数**（`PLATE`）—— 「我重点观察的行业」就是这些。

    名字和行业状态机**是同一套**（`半导体设备与材料` / `太阳能` / `航空航天与国防` …），
    所以能直接对上；对不上的是富途的**概念板块**（加密货币概念股 / 无人机概念股…），
    那些本来就不是行业 —— 单列，**不硬凑**。

    读的是 `data/kline/daily/`（自选那批），不是当天的粗筛文件夹。
    """
    return [{"symbol": d.get("symbol"), "name": d.get("name")}
            for d in load_local(types=("PLATE",)) if d.get("name")]


def plate_members():
    """自选板块的**成分股**（`data/plate_members.json`，由 `fetch/opend/plates.py` 拉）。

    快照里只有富途**行业**字段，没有概念板块 —— 所以概念板块的成分股只能这样拿。
    **只读缓存、不联网**；没有就返回 `{}`，分析照样跑。
    """
    from fetch.opend.plates import read_members

    return (read_members() or {}).get("plates") or {}


def _as_int_keys(d):
    """JSON 落盘的字典键都变成字符串了，转回 int（`{"20": 88}` → `{20: 88}`）。"""
    return {int(k): v for k, v in (d or {}).items()}


def _evidence(signals, sectors, ind_all, rps_rows, periods, days, rps_min,
              state_counts=None, small=None):
    """解读要引用的数字 —— **先算好放这儿**。

    **结论必须带证据**：「普遍偏低」「绝大多数是负的」这种话，没有具体个数撑着就是空话。
    让 AI 手数既慢又会数错，数据一变还全失效 —— 所以由管线算。

    放在 `picks.json` 的 `evidence` 里，解读直接引用。
    """

    def _r(entry, n):
        """取某周期的 RPS —— 信号里是 int 键，从 JSON 读的行里是 str 键，都得认。"""
        got = entry.get("rps") or {}
        return got.get(n) if n in got else got.get(str(n))

    def _med(row, n):
        got = row.get(n) if n in row else row.get(str(n))
        return (got or {}).get("median")

    def _count(entries, fn):
        return sum(1 for e in entries if fn(e))

    def _all_min(entries):
        """四个周期**全部** ≥ 门槛 —— 用 `rps_min`，**别在这里写死 90**。"""
        return _count(entries, lambda e: all((_r(e, n) or 0) >= rps_min for n in periods))

    off_market = [e for e in signals if not e.get("rps")]  # ETF / 期货 / 非美股
    market_strong = [r for r in rps_rows if (_r(r, days) or 0) >= rps_min]

    # **交叉统计** —— 说「长期涨的最近在回调」，样本就必须是「长期涨的那批」。
    # 拿「全部行业」去数（比如「20 日中位 16/19 为负」）是**样本错位**：里面一半长期根本没涨。
    long_up = [s for s in ind_all if (_med(s, days) or 0) > 0]
    long_dn = [s for s in ind_all if (_med(s, days) or 0) <= 0]

    def _down(group, n):
        return _count(group, lambda s: (_med(s, n) or 0) < 0)

    return {
        "periods": list(periods),
        "main": days,
        # 行业状态机 —— **解读时要连着家数看**：家数少的行业上涨占比不稳定，
        # 5 只全涨就是 1.00。所以这里把「哪些行业家数少」也一并给出。
        # **空状态也留着** —— 0 是信息（「今天没有行业在启动」），别过滤掉。
        "states": dict(state_counts or {}),
        "small_industries": small or [],
        "signals": {
            "total": len(signals),
            "by_signal": {
                label: _count(signals, lambda e, L=label: L in (e.get("signals") or []))
                for label in SIGNAL_LABELS.values()
            },
            "off_market": len(off_market),
            "off_market_codes": sorted(e["code"] for e in off_market),
            "rps_main_ge_min": _count(signals, lambda e: (_r(e, days) or 0) >= rps_min),
            "rps50_lt_50": _count(signals, lambda e: (_r(e, 50) or 999) < 50),
            "rps50_ge_min": _count(signals, lambda e: (_r(e, 50) or 0) >= rps_min),
            "all_periods_ge_min": _all_min(signals),
        },
        "sectors": {
            "total": len(sectors),
            **{
                f"median_lt_0_{n}d": _count(sectors, lambda s, n=n: (_med(s, n) or 0) < 0)
                for n in periods
            },
        },
        "industries": {
            "total": len(ind_all),
            **{
                f"median_lt_0_{n}d": _count(ind_all, lambda s, n=n: (_med(s, n) or 0) < 0)
                for n in periods
            },
            "long_up": len(long_up),
            "long_up_and_down_20d": _down(long_up, 20),
            "long_up_and_down_50d": _down(long_up, 50),
            "long_dn": len(long_dn),
            "long_dn_and_down_20d": _down(long_dn, 20),
        },
        "market_strong": {
            "total": len(market_strong),
            "rps50_lt_50": _count(market_strong, lambda r: (_r(r, 50) or 999) < 50),
            "rps50_ge_min": _count(market_strong, lambda r: (_r(r, 50) or 0) >= rps_min),
            "all_periods_ge_min": _all_min(market_strong),
        },
    }


def _screener_index(doc):
    """服务器端条件选股结果 → `{预设: {描述, 命中数, 代码集合}}`（代码取裸代码）。

    行里的代码字段叫 `symbol`（`futu_algo` 的 `_enrich` 用这个名），
    但也兼容 `code` —— 别写死一个。
    """
    out = {}
    for name, block in ((doc or {}).get("presets") or {}).items():
        codes = set()
        for row in block.get("rows") or []:
            raw = row.get("symbol") or row.get("code") or ""
            if raw:
                codes.add(str(raw).split(".")[-1])
        out[name] = {
            "description": block.get("description"),
            "matched": block.get("matched"),
            "codes": codes,
        }
    return out


def _fmt(value, digits=1, dash="—", scale=1.0, sign="", suffix=""):
    return dash if value is None else f"{value * scale:{sign}.{digits}f}{suffix}"


def _num(value, digits=2):
    return "—" if value is None else f"{value:.{digits}f}"


# 每个信号那张表的**特有列** —— 一个信号一张表，各自显示各自的关键字段。
# 取值函数收 `(entry, hit)`：`entry` 是合成后的一条，`hit` 是该信号的原始记录
# （`cd.json` 有 DIF/DEA/MACD，`guppy.json` 有顾比线/底座 —— 字段名不通用，所以分开）。
SIGNAL_EXTRA = {
    "CD底背离": [
        ("▲日期", 10, False, lambda e, h: str(h.get("date") or "—")),
        ("触发价", 10, True, lambda e, h: _num(h.get("trigger_close"))),
    ],
    "顾比突破": [
        ("突破日", 10, False, lambda e, h: str(h.get("date") or "—")),
        ("突破价", 10, True, lambda e, h: _num(h.get("trigger_close"))),
        ("顾比线", 10, True, lambda e, h: _num(h.get("guppy"))),
    ],
}


def _hit_type(entry, label):
    """这条命中记录的证券类型 —— 从**该信号自己的**原始记录里取（个股 `STOCK`）。"""
    return ((entry.get("details") or {}).get(label) or {}).get("type") or ""


def signal_rows(signals, label, *, kind=None):
    """命中某个信号的全部 —— **不截断**。信号是结论，结论不能只列前 20 个。

    `kind="stock"` 只要个股；`kind="other"` 只要非个股（ETF / 期货 / 加密…）。

    **为什么要分**：ETF 突破是**板块 / 大盘**的事（而且它的 RPS 补不上 —— 不在正股快照里），
    个股突破是**这只票**的事。混在一张表里会让人以为「ETF 也能当票买」。
    """
    rows = [e for e in signals if label in (e.get("signals") or [])]
    if kind is None:
        return rows
    want_stock = kind == "stock"
    return [e for e in rows if (_hit_type(e, label) == "STOCK") == want_stock]


def signal_counts(signals, label):
    """`(个股数, 非个股数)` —— 表头要用。"""
    return (
        len(signal_rows(signals, label, kind="stock")),
        len(signal_rows(signals, label, kind="other")),
    )


def signal_columns(label, periods):
    """某张信号表的列定义：`(标题, 宽度, 右对齐, 取值)`。

    **终端和 Markdown 共用这一份** —— 改列只改这里，别两边各写一遍。
    通用列（代码 / 距今 / 现价 / 250日涨幅 / RPS×4 / 行业）所有信号一样，
    特有列由 `SIGNAL_EXTRA` 提供。
    """
    return (
        [("代码", 12, False, lambda e, h: e["code"]),
         ("名称", 18, False, lambda e, h: (e.get("name") or "")[:16])]
        + SIGNAL_EXTRA.get(label, [])
        # 「距今」按**该信号**算 —— 一只票可能 CD 在 5 天前、顾比在昨天，
        # 用全局最小 gap 会让 CD 表显示成 1，错。
        + [("距今", 4, True, lambda e, h: "—" if h.get("gap") is None else str(h["gap"])),
           ("现价", 9, True, lambda e, h: _num(e.get("price"))),
           ("250日涨幅", 10, True,
            lambda e, h: _fmt((e.get("chg") or {}).get(250), 1, scale=100, sign="+", suffix="%"))]
        + [(f"RPS{n}", 6, True, lambda e, h, n=n: _fmt(e["rps"].get(n))) for n in periods]
        # **行业只给名字，不给数值** —— 数值在「二、信号涉及的板块」那节，
        # 一行里塞两个主体（个股 + 行业）最容易读错。
        + [("行业", 20, False, lambda e, h: (e.get("industry") or "—")[:18])]
    )


def prev_day(date, market="US", root=None):
    """找**上一个有数据的日期** —— 用来对比行业状态的变化。

    数据本来就按日期落盘（`data/scan/<日期>/`），所以「每日跟踪」不用另建机制：
    读前一天的 `picks.json` 比一下就行。

    **为什么关心这个**：「走弱·横盘 → 启动·横盘」那一跳就是**行业整体触底反弹**，
    也正是「该重视」的时刻 —— 值得每天盯。
    """
    base = day_store.day_dir(date, market, root).parent
    if not base.is_dir():
        return None
    days = sorted(p.name for p in base.iterdir() if p.is_dir() and p.name < str(date))
    return days[-1] if days else None


def state_changes(states, prev_states):
    """`[{行业, 从, 到, 好坏}]` —— 状态变了哪些。

    `好坏`：`up` 变好 / `down` 变差 / `flat` 横移。**变好的排前面**。
    """
    GOOD = {"启动·横盘", "启动·跌后", "持续涨", "回调转强", "回调"}
    out = []
    for name, now in states.items():
        was = prev_states.get(name)
        if was is None or was == now:
            continue
        out.append({
            "industry": name, "from": was, "to": now,
            # 只标「**进入了**好状态」，**不标「变差」** ——
            # 「走弱·跌后 → 走弱·横盘」是变好（蓄势 vs 次优），但都还在「走弱」里，
            # 硬判好坏就会判错。**只给变化，判断留给读的人。**
            "good": now in GOOD,
        })
    out.sort(key=lambda c: (c["good"] != "up", c["industry"]))
    return out


def spec_desc(spec):
    """把快照里的 `ScreenSpec` 翻成人话 —— **报告必须写清 RPS 的样本怎么来的**。

    早先报告写「全市场百分位」，但样本其实是服务端过滤后的池子（2954 只），
    不是全市场 9421 只 —— 名字错了，读的人和 AI 都会误读。
    """
    if not spec:
        return "不过滤（全市场）"
    bits = []
    if spec.get("min_price") is not None:
        bits.append(f"价 ≥ ${spec['min_price']:g}")
    if spec.get("min_mcap") is not None:
        bits.append(f"市值 ≥ {spec['min_mcap'] / 1e8:g} 亿$")
    if spec.get("min_avg_turnover") is not None:
        bits.append(
            f"近 {spec.get('avg_turnover_days', 20)} 日均成交额"
            f" ≥ {spec['min_avg_turnover'] / 1e7:g} 千万$"
        )
    if spec.get("min_listed_days") is not None:
        bits.append(f"上市 ≥ {spec['min_listed_days']} 天")
    return " · ".join(bits) or "不过滤"


# 行业状态表 —— **只显示判据本身**（三个周期的上涨占比）。
# 不堆中位/平均/波动那些 —— 状态机只看这三列，堆多了又会看串行。
STATE_COLUMNS = [
    ("行业", 20, False),
    ("家数", 4, True),
    ("250日上涨", 10, True),
    ("120日上涨", 10, True),
    ("20日上涨", 10, True),
]


def state_cells(industry):
    """一行状态表的数据 —— 终端和报告共用，别各写一遍。"""
    return [
        (industry.get("industry") or "")[:18],
        str(industry.get("count") or ""),
        _fmt((industry.get(250) or {}).get("up_ratio"), 2),
        _fmt((industry.get(120) or {}).get("up_ratio"), 2),
        _fmt((industry.get(20) or {}).get("up_ratio"), 2),
    ]


def take(rows, top):
    """排名型榜单取前 `top` 条；**`top <= 0` 表示全列**。

    门槛型名单（技术信号 / 强势榜）不走这个 —— 它们永远全列（见 `render` / `to_markdown`）。
    """
    return rows if top is None or top <= 0 else rows[:top]


def empty_states(picks):
    """计数为 0 的状态 —— **空状态也是信息**。

    「今天没有行业在『启动』」是个**结论**（没有新方向在起 = 存量行情），
    不该因为表里没行就消失。`state_counts` 按 `STATE_ORDER` 全量算，0 也在里面，
    这里只是挑出来给渲染层用。
    """
    counts = picks.get("state_counts")
    if not counts:
        return []
    return [st for st in STATE_ORDER if not counts.get(st)]


def by_signal_counts(signals):
    """各信号各命中多少只 —— 表头和证据表都用这一份，别两处各数一遍。"""
    return {label: len(signal_rows(signals, label)) for label in SIGNAL_LABELS.values()}


def signal_table(label, signals, periods, kind=None):
    """渲染一张信号表需要的 `(列定义, 数据行)` —— 终端和 Markdown 都从这里取。

    `kind` 透传给 `signal_rows`：`"stock"` 个股段 / `"other"` 非个股段。
    """
    cols = signal_columns(label, periods)
    rows = []
    for e in signal_rows(signals, label, kind=kind):
        hit = (e.get("details") or {}).get(label) or {}
        rows.append([str(fn(e, hit)) for _, _, _, fn in cols])
    return cols, rows


def print_signal_table(label, signals, periods, *, kind=None):
    """终端打印一张信号表 —— 个股段和非个股段共用，别写两遍。"""
    from engine.screener.table import pad

    cols, cells_all = signal_table(label, signals, periods, kind=kind)
    print("  " + " ".join(pad(t, w, r) for t, w, r, _ in cols))
    print("  " + "-" * (sum(w for _, w, _, _ in cols) + len(cols) - 1))
    for cells in cells_all:
        print("  " + " ".join(pad(v, w, r) for v, (_, w, r, _) in zip(cells, cols)))


def analyze(
    date=None,
    market="US",
    *,
    days=250,
    rps_min=RPS_MIN,

    top=30,
    root=None,
):
    """合成候选清单。返回可直接落盘的 dict。

    `days` 是**主周期** —— 判「RPS 达标」和「行业排序」用它；
    四个周期都会算并展示（见模块注释）。

    **榜单全部返回**（不按 `top` 截断）—— 数据不该被显示逻辑砍掉，
    否则想「看全 295 只」就得重算。`top` 只在渲染时用，且**只管排名型榜单**；
    门槛型名单（技术信号 / 强势榜）渲染时也全列。
    """
    data = load_day(date, market, root)
    universe, rps_doc, industry_doc = data["universe"], data["rps"], data["industry"]

    periods = [int(p) for p in ((rps_doc or {}).get("days") or FALLBACK_PERIODS)]
    if days not in periods:
        raise SystemExit(f"主周期 {days} 不在快照周期 {periods} 里；换 --days 或重拉快照")

    # 全市场索引：快照里的 code 是 `UNH`，自选股是 `US.UNH` —— 比对时取裸代码
    market_rows = {r["code"]: r for r in ((universe or {}).get("rows") or []) if r.get("code")}
    rps_rows = {r["code"]: r for r in ((rps_doc or {}).get("rows") or []) if r.get("code")}
    # 行业 → {周期: 统计}；`ind_meta` 留着记家数（报表要显示，别丢）
    ind_meta = {s["industry"]: s for s in ((industry_doc or {}).get("stats") or [])}
    ind_by_days = {name: _as_int_keys(s.get("by_days")) for name, s in ind_meta.items()}
    # 自选板块（含概念板块）—— 同一套统计口径，`industry_stats` 算的（见 `scan.step_plates`）
    plate_doc = data.get("plates") or {}
    plate_meta = {s["industry"]: s for s in (plate_doc.get("stats") or [])}
    plate_by_days = {n: _as_int_keys(s.get("by_days")) for n, s in plate_meta.items()}
    # 服务器端条件选股：预设 → 命中代码集合
    screener = _screener_index(data["screener"])

    # ── ① 技术信号：自选股的 CD / 顾比，逐条补全市场 RPS + 行业多周期状态
    signals: dict[str, dict] = {}
    for key, label in SIGNAL_LABELS.items():
        for hit in ((data[key] or {}).get("hits") or []):
            code = str(hit.get("code"))
            # **只剥市场前缀** —— 用 `split(".")[-1]` 会把 `US.MOG.A` 切成 `A`
            # （和 `stock/info/report.py::plain_code` 保持同一个口径）
            bare = code.split(".", 1)[1] if "." in code else code
            row = market_rows.get(bare)
            got = rps_rows.get(bare) or {}
            entry = signals.setdefault(code, {
                "code": code,
                "name": hit.get("name"),
                "signals": [],
                "signal_date": hit.get("date"),
                "gap": hit.get("gap"),
                "in_market": row is not None,  # 不在全市场快照里（ETF / 非美股）
                "price": (row or {}).get("price"),
                "mcap": (row or {}).get("mcap"),
                "industry": (row or {}).get("industry"),
                "rps": _as_int_keys(got.get("rps")),  # {周期: 样本内百分位}
                "chg": _as_int_keys(got.get("chg")),
                # 同时也命中服务器端条件选股的话，记下是哪个预设
                "screener": [n for n, blk in screener.items() if bare in blk["codes"]],
                # 每个信号**自己的**原始记录 —— 两张表各取所需（CD 要 DIF，顾比要底座）
                "details": {},
            })
            entry["details"][label] = hit
            if label not in entry["signals"]:
                entry["signals"].append(label)
            gap = hit.get("gap")
            if gap is not None and (entry.get("gap") is None or gap < entry["gap"]):
                entry["gap"] = gap
                entry["signal_date"] = hit.get("date")

    # **只补事实，不下判断。**
    # 早先这里有 `strong`（RPS 达标）/ `tailwind`（行业顺势）/ `score`（两项相加）——
    # 三个布尔值，每个都把「用哪个周期、哪个阈值、什么方向」藏了起来，
    # 读的人和 AI 只能信、不能看，于是反复误读（`tailwind` 只看主周期、
    # `consensus` 不含方向，都是这么来的）。
    # **现在：数字全在表里，判断留给解读。**
    for entry in signals.values():
        entry["rps_main"] = entry["rps"].get(days)

    # 排序：**主周期 RPS 降序**（样本内百分位），同分看信号新鲜度。
    # 不再按「强 + 顺」打分 —— 那是把判断藏进排序里。
    ordered = sorted(
        signals.values(),
        key=lambda e: (
            -(e["rps_main"] if e["rps_main"] is not None else -1),
            e.get("gap") if e.get("gap") is not None else 99,
            e["code"],
        ),
    )

    # ── ① 行业状态 —— **选股的第一层**（先选行业 → 再选个股 → 再看信号）
    #
    # 判据是**双门槛**（全用上涨占比，自带方向）：
    #     长期健不健康 → 250 日上涨占比     现在动不动 → 20 日上涨占比
    # 中间用 120 日区分「一路强」和「刚转强」。详见 `industry.classify`。
    state_of = {name: classify(by_days) for name, by_days in ind_by_days.items()}
    states = []
    for st in STATE_ORDER:
        members = sorted(
            (
                dict(ind_by_days[name], industry=name, count=ind_meta[name].get("count"))
                for name, s in state_of.items()
                if s == st
            ),
            key=lambda s: -(s.get(250, {}).get("up_ratio") or 0),
        )
        if members:
            states.append({"state": st, "note": STATE_NOTE.get(st, ""), "industries": members})

    # 和**上一个有数据的日期**比：谁换了状态。
    # 「走弱·横盘 → 启动·横盘」那一跳就是「行业整体触底反弹」—— 每天盯这个。
    as_of = (universe or {}).get("as_of") or date
    prev = prev_day(as_of, market, root)
    changes = []
    if prev:
        old = day_store.load("picks", date=prev, market=market, root=root)
        if old:
            changes = state_changes(
                state_of,
                {s["industry"]: g["state"]
                 for g in (old.get("states") or [])
                 for s in g["industries"]},
            )

    # ── ② 信号涉及的板块（多周期）—— 「它所在的板块什么情况」
    sectors = sorted(
        (
            dict(ind_by_days[name], industry=name, count=ind_meta[name].get("count"))
            for name in {e.get("industry") for e in ordered}
            if name and name in ind_by_days
        ),
        key=lambda s: -(s.get(days, {}).get("rps") or -1),
    )

    # ── ③ 全市场强势（主周期 RPS 达标的）
    strong = sorted(
        (r for r in rps_rows.values() if (r.get("rps", {}).get(str(days)) or 0) >= rps_min),
        key=lambda r: -(r["rps"][str(days)]),
    )

    # ── ④ 行业：集体涨 / 集体跌（按主周期排序）
    ranked = sorted(ind_by_days.items(), key=lambda kv: -(kv[1].get(days, {}).get("rps") or -1))
    hot, cold = [], []
    for name, by_days in ranked:
        row = dict(by_days, industry=name, count=ind_meta[name].get("count"))
        (hot if (by_days.get(days, {}).get("median") or 0) > 0 else cold).append(row)

    # ── ⑤ 条件选股：每个预设命中多少、其中几个同时有技术信号
    presets = [
        {
            "preset": name,
            "description": blk["description"],
            "matched": blk["matched"],
            "with_signal": sum(1 for e in ordered if name in e.get("screener", [])),
        }
        for name, blk in sorted(screener.items())
    ]

    return {
        "as_of": (universe or {}).get("as_of") or date,
        "market": market,
        "params": {
            "days": days,
            "periods": periods,
            "rps_min": rps_min,
            "top": top,
            # RPS 的**样本** —— 必须落盘并写进报告，否则「百分位」无从解释
            "sample": spec_desc((universe or {}).get("spec")),
        },
        "counts": {
            "universe": len(market_rows),
            "signals": len(ordered),
            "sectors": len(sectors),
            "strong": len(strong),
            "industries": len(ind_by_days),
            "presets": len(presets),
        },
        "signals": ordered,
        "sectors": sectors,
        # 行业状态机 —— 按状态分组，从强到弱（见 `industry.STATE_ORDER`）
        "states": states,
        # 和上一个有数据的日期比，谁换了状态（`state_prev` 是那个日期，没有就是 None）
        "state_changes": changes,
        "state_prev": prev,
        "state_counts": {
            st: sum(1 for s in state_of.values() if s == st) for st in STATE_ORDER
        },
        # **榜单全部落盘** —— 显示层再决定切多少。
        # 数据不该被显示逻辑截断，否则想「看全 295 只」就得重算一遍。
        "strong": strong,
        "hot": hot,
        "cold": cold,
        "presets": presets,
        # 自选里的富途板块指数 —— 「我重点观察的行业」。名字就是行业名，能直接对上。
        "watch_plates": watch_plates(),
        # 自选板块（**含概念板块**）—— **和行业同一套算法、同一个 `classify`**。
        # 快照里只有富途行业字段，概念板块的成分股来自 `data/plate_members.json`。
        "plates": {
            "members_fetched_at": plate_doc.get("members_fetched_at"),
            "members": plate_members(),
            "stats": [
                {
                    "plate": name,
                    "plate_code": plate_meta[name].get("plate_code"),
                    "members": plate_meta[name].get("members"),
                    "count": plate_meta[name].get("count"),
                    "state": classify(by_days),
                    "by_days": by_days,
                }
                for name, by_days in plate_by_days.items()
            ],
        },
        # 解读要引用的数字 —— 结论必须带证据，所以先算好（别让 AI 手数）
        "evidence": _evidence(
            ordered,
            sectors,
            [dict(v, industry=k, count=ind_meta[k].get("count")) for k, v in ind_by_days.items()],
            list(rps_rows.values()),
            periods,
            days,
            rps_min,
            # 家数 < 10 的行业清单（含家数）—— 解读时要提醒「这些数不稳定」
            state_counts={
                st: sum(1 for s in state_of.values() if s == st) for st in STATE_ORDER
            },
            small=[
                {"industry": k, "count": v.get("count")}
                for k, v in sorted(ind_meta.items(), key=lambda kv: kv[1].get("count") or 0)
                if (v.get("count") or 0) < 10
            ],
        ),
    }


# ---------------------------------------------------------------- 终端
def render(picks, *, top=None):
    from engine.screener.table import pad

    top = top or picks["params"]["top"]
    c, p = picks["counts"], picks["params"]
    periods = p["periods"]
    main = p["days"]

    print(f"粗筛综合分析 {picks['as_of']}   主周期 {main} 日（四个周期都算）")
    print(f"RPS 样本 {c['universe']} 只（{p['sample']}）—— RPS 是**样本内**百分位，不是全市场")
    print(
        f"技术信号去重 {c['signals']} 只"
        f"（{' + '.join(f'{k} {v}' for k, v in by_signal_counts(picks['signals']).items())}）"
        f"｜涉及板块 {c['sectors']} 个"
        f"｜RPS{main} ≥ {p['rps_min']:g} 共 {c['strong']} 只"
        f"｜行业 {c['industries']} 个（成分股 ≥ 5 只）\n"
    )

    # ⓪ 行业状态 —— **先看哪里**。选股顺序是「行业 → 个股 → 信号」，
    # 所以状态机放在最前面，信号是最后的择时确认。
    if picks.get("states"):
        total = sum((picks.get("state_counts") or {}).values())
        print(f"\n【行业状态】{total} 个行业（成分股 ≥ 5 只）—— 先看哪里")
        for g in picks["states"]:
            print(f"\n  ── {g['state']}（{len(g['industries'])} 个）｜{g['note']}")
            print("  " + " ".join(pad(t, w, r) for t, w, r in STATE_COLUMNS))
            print("  " + "-" * (sum(w for _, w, _ in STATE_COLUMNS) + len(STATE_COLUMNS) - 1))
            for s in g["industries"]:
                cells = state_cells(s)
                print("  " + " ".join(
                    pad(v, w, r) for v, (_, w, r) in zip(cells, STATE_COLUMNS)
                ))
        empties = empty_states(picks)
        if empties:
            print(f"\n  ── 空状态（0 个行业）：{'、'.join(empties)}")
            print("     **没有成员也是信息** —— 如「启动」为空 = 今天没有行业在启动，没有新方向在起")
        if picks.get("state_prev") and picks.get("state_changes"):
            print(f"\n  ── 状态变化（vs {picks['state_prev']}）")
            for ch in picks["state_changes"]:
                mark = "★ 进入好状态" if ch["good"] else "  "
                print(f"    {mark}  {ch['industry'][:18]:<20}{ch['from']} → {ch['to']}")
        elif picks.get("state_prev"):
            print(f"\n  ── 状态变化（vs {picks['state_prev']}）：无")
        else:
            print("\n  ── 状态变化：没有更早的数据可对比（明天再跑就有了）")
        print("\n  上涨占比 = 成分股里在涨的比例（**自带方向**）——"
              " 250 日看长期健不健康，20 日看现在动不动，120 日看中期转过没有")
        print("  **家数** = 该行业**全部**成分股数（不是抽样）—— 比例就是比例，没有置信区间。"
              "但家数少的要当心：5 只的行业**一只票翻身比例就跳 20 个百分点**，"
              "而且 5 家公司算不上一个「板块」")
        print("  上涨占比是**过去 N 天的结果**，不是一天的事 —— 它变起来是慢的")

    # ① 信号 × 多周期 RPS —— **一个信号一张表**，各自全量列出（信号是结论，不截断）
    by_sig = "｜".join(f"{k} {v} 只" for k, v in by_signal_counts(picks["signals"]).items())
    print(f"【技术信号】去重 {c['signals']} 只｜{by_sig}（**全部列出**）")
    if not picks["signals"]:
        print("  （无）")
    else:
        for label in SIGNAL_LABELS.values():
            rows_ = signal_rows(picks["signals"], label)
            n_stock, n_other = signal_counts(picks["signals"], label)
            print(f"\n  ── {label}（{len(rows_)} 只｜个股 {n_stock} + 非个股 {n_other}）")
            if not rows_:
                print("    （无）")
                continue
            print_signal_table(label, picks["signals"], periods, kind="stock")
            if n_other:
                print(
                    f"\n  ── {label} · 非个股（{n_other} 只，ETF / 期货 ——"
                    " **板块 / 大盘**的信号，不是个股）"
                )
                print_signal_table(label, picks["signals"], periods, kind="other")
        print("\n  RPS = **样本内百分位**（样本见报告表头）；「—」= 不在样本里（ETF / 期货 / 非美股）")
        print("  250日涨幅 和 RPS 一起看 —— 涨幅大不代表排名高（样本里有涨得更大的）")

    # ② 信号涉及的板块
    print(f"\n【信号涉及的板块】{c['sectors']} 个（多周期）")
    cols = [("行业", 20, False), ("家数", 4, True)]
    cols += [(f"{n}日中位", 10, True) for n in periods]
    cols += [(f"{n}日上涨", 9, True) for n in periods]
    print("  " + " ".join(pad(t, w, r) for t, w, r in cols))
    for s in picks["sectors"]:
        cells = [pad(s["industry"][:18], 20), pad(str(s.get("count") or ""), 4, True)]
        cells += [
            pad(_fmt((s.get(n) or {}).get("median"), 2, scale=100, sign="+"), 10, True)
            for n in periods
        ]
        cells += [
            pad(_fmt((s.get(n) or {}).get("up_ratio"), 2), 9, True) for n in periods
        ]
        print("  " + " ".join(cells))

    # ③ 交叉筛选：行业 → 个股 → 信号 —— **行业优先**
    # （原来的「样本内强势」榜删了 —— 它就是同一批票按 RPS 排的**个股优先**视角，
    #   这里全包含它、且顺序才对。两份都列 = 把几百只票印两遍。）
    print_cross(picks)

    # ④ 行业：集体涨 / 集体跌
    def _industries(title, rows):
        shown = take(rows, top)
        print(f"\n【{title}】共 {len(rows)} 个，列 {len(shown)} 个")
        cols = [("行业", 20, False), ("家数", 4, True)]
        cols += [(f"{n}日中位", 10, True) for n in periods]
        cols += [(f"{n}日上涨", 9, True) for n in periods]
        print("  " + " ".join(pad(t, w, r) for t, w, r in cols))
        for s in shown:
            cells = [pad(s["industry"][:18], 20), pad(str(s.get("count") or ""), 4, True)]
            cells += [
                pad(_fmt((s.get(n) or {}).get("median"), 2, scale=100, sign="+"), 10, True)
                for n in periods
            ]
            cells += [pad(_fmt((s.get(n) or {}).get("up_ratio"), 2), 9, True) for n in periods]
            print("  " + " ".join(cells))

    _industries("集体涨", picks["hot"])
    _industries("集体跌", picks["cold"])

    if picks.get("presets"):
        print(f"\n【条件选股】{c.get('presets', 0)} 个预设（服务器端筛，不下 K 线）")
        cols = [("预设", 14, False), ("命中", 6, True), ("有信号", 7, True), ("说明", 0, False)]
        print("  " + " ".join(pad(t, w, r) for t, w, r in cols))
        for x in picks["presets"]:
            cells = [
                pad(str(x["preset"]), 14),
                pad(str(x.get("matched") or ""), 6, True),
                pad(str(x.get("with_signal") or 0), 7, True),
                pad((x.get("description") or "")[:40], 0),
            ]
            print("  " + " ".join(cells))

    print(
        "\n两个轴别混：**上涨占比**说的是「是不是集体」**且自带方向**；"
        "**中位涨幅**说的是「多强」——中位抗妖股，平均会被一只妖股拉飞。"
    )


# ---------------------------------------------------------------- Markdown 报告
def keep_ai_section(path):
    """把报告里已有的「解读」**正文**抠出来 —— 报告会重新生成，AI 写的东西不该丢。

    只返回正文：标题和 `AI_NOTE`（管线自己写的说明）都要去掉，
    否则每重跑一次就叠一层说明。
    """
    path = Path(path)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    start = text.find(AI_SECTION)
    if start < 0:
        return None
    body = text[start + len(AI_SECTION):]
    nxt = body.find("\n## ")
    if nxt >= 0:
        body = body[:nxt]
    body = body.split(AI_END)[0]  # 证据小节是机器写的，到它就停
    return body.replace(AI_NOTE, "").strip() or None


def to_markdown(picks, *, top=None, keep_ai=None):
    """把合成结果写成 **Markdown 报告** —— 给人看的。

    终端表格是给人**扫**的（一次一屏，关了就没），报告是给人**存 / 贴 / 打印**的。
    内容一样，用途不同：`picks.json` 给机器，这份给人。

    `keep_ai`：上一轮 AI 写的「解读」段，原样放回去（表格会刷新，人话不该丢）。
    """
    top = top or picks["params"]["top"]
    c, p = picks["counts"], picks["params"]
    periods, main = p["periods"], p["days"]

    out = [
        f"# 粗筛报告 {picks['as_of']}",
        "",
        f"> **RPS 样本 {c['universe']} 只**（可投资域：{p['sample']}）",
        f"> **RPS = 该样本内的百分位，不是全市场** —— 剔掉的是你不会买的"
        "（仙股 / 微盘 / 没量的 / 新股），它们留在样本里只会**占掉高名次**。",
        f"> 技术信号去重 {c['signals']} 只"
        f"（{' + '.join(f'{k} {v}' for k, v in by_signal_counts(picks['signals']).items())}）"
        f"｜涉及板块 {c['sectors']} 个"
        f"｜RPS{main} ≥ {p['rps_min']:g} 共 {c['strong']} 只"
        f"｜行业 {c['industries']} 个（成分股 ≥ 5 只）",
        f"> 口径：**{' / '.join(str(n) for n in periods)} 日**涨幅排名，主周期 **{main} 日**"
        "｜行业分类来自**富途**（不是 GICS）",
        f"> 原始数据：`data/scan/{picks['as_of']}/`",
        "",
        AI_SECTION,
        "",
        AI_NOTE,
        "",
        keep_ai or "（待填）",
        "",
    ]

    # **结论必须带证据** —— 把解读引用的数字摆出来，让人能核对
    ev = picks.get("evidence") or {}
    if ev:
        s, sec, mk = ev["signals"], ev["sectors"], ev["market_strong"]
        main = ev["main"]
        rows = [
            ["行业状态", ""],
            *[[f"　{st}", n] for st, n in (ev.get("states") or {}).items()],
            ["　其中家数 < 10（上涨占比不稳定）", len(ev.get("small_industries") or [])],
            ["信号总数（去重）", s["total"]],
            *[[f"　{label}", n] for label, n in (s.get("by_signal") or {}).items()],
            ["其中不在正股快照里（ETF / 期货 / 非美股）", s["off_market"]],
            [f"RPS{main} ≥ {p['rps_min']:g}", s["rps_main_ge_min"]],
            ["RPS50 < 50", s["rps50_lt_50"]],
            [f"RPS50 ≥ {p['rps_min']:g}", s["rps50_ge_min"]],
            [f"四周期都 ≥ {p['rps_min']:g}", s["all_periods_ge_min"]],
            ["信号涉及的板块数", sec["total"]],
        ]
        rows += [[f"　{n} 日中位 < 0", sec[f"median_lt_0_{n}d"]] for n in periods]
        ind = ev.get("industries") or {}
        if ind:
            rows += [
                ["全部行业数（成分股 ≥ 5）", ind["total"]],
                [f"　{main} 日中位 > 0（长期在涨）", ind["long_up"]],
                ["　　└ 其中 20 日中位 < 0", ind["long_up_and_down_20d"]],
                ["　　└ 其中 50 日中位 < 0", ind["long_up_and_down_50d"]],
                [f"　{main} 日中位 ≤ 0（长期没涨）", ind["long_dn"]],
                ["　　└ 其中 20 日中位 < 0", ind["long_dn_and_down_20d"]],
            ]
        rows += [
            [f"全市场 RPS{main} ≥ {p['rps_min']:g}", mk["total"]],
            ["　其中 RPS50 < 50", mk["rps50_lt_50"]],
            [f"　其中 RPS50 ≥ {p['rps_min']:g}", mk["rps50_ge_min"]],
            [f"　其中四周期都 ≥ {p['rps_min']:g}", mk["all_periods_ge_min"]],
        ]
        out += ["### 解读引用的数字（可核对）", "", _md_table(["指标", "值"], rows), ""]

    # 一、行业状态 —— **选股第一层**，所以放最前面
    if picks.get("states"):
        total = sum((picks.get("state_counts") or {}).values())
        out += [
            f"## 一、行业状态（{total} 个，成分股 ≥ 5 只）—— 先看哪里",
            "",
            "**选股顺序是「行业 → 个股 → 信号」**，所以先看行业。",
            "判据是**双门槛**，全用**上涨占比**（自带方向）："
            "**250 日**看长期健不健康，**20 日**看现在动不动，**120 日**看中期转过没有。",
            "",
            "> ⚠️ **表里全是原始值，没做过任何加工**（不收缩、不平滑、不打折扣）——"
            " 有多少只就写多少只。",
            "> **`家数` = 该行业的全部成分股数，不是抽样** —— 所以这里没有「置信区间」问题，"
            "比例就是比例，别去「修正」它。",
            "> **但家数少的行业要当心，原因是两个**：",
            "> ① **取值粗** —— 5 只只能取 0/0.2/0.4/0.6/0.8/1.0，**一只票翻身比例就跳 20 个百分点**"
            "（100 只的行业一只票只跳 1%）；",
            "> ② **行业太小** —— 5 家公司算不上一个「板块」，数字是真的，"
            "但「这个行业在涨」的代表性弱。",
            "> **上涨占比是过去 N 天的结果，不是一天的事** —— 它变起来是慢的，"
            "不会今天 1.00 明天 0.20。",
            "> 分档线（0.6 / 中位 5%）是**约定不是定律** —— 换个视角而已，"
            "**漏了错了都无所谓**，这是粗筛，真正的判断在解读那一步。",
            "",
        ]
        if picks.get("state_changes"):
            out += [
                f"**状态变化（vs {picks['state_prev']}）** ——"
                "「走弱·横盘 → 启动·横盘」那一跳就是**行业整体触底反弹**：",
                "",
            ]
            out += [_md_table(
                ["", "行业", "从", "到"],
                [["★" if c["good"] else "", c["industry"], c["from"], c["to"]]
                 for c in picks["state_changes"]],
            ), ""]
        elif picks.get("state_prev"):
            out += [f"**状态变化（vs {picks['state_prev']}）**：无", ""]
        else:
            out += ["**状态变化**：没有更早的数据可对比（明天再跑就有了）", ""]

        for i, g in enumerate(picks["states"], 1):  # 按实际有的状态连续编号（空状态不占号）
            out += [f"### 1.{i} {g['state']}（{len(g['industries'])} 个）", ""]
            out += [f"*{g['note']}*", ""]
            rows = [state_cells(s) for s in g["industries"]]
            out += [_md_table([t for t, _, _ in STATE_COLUMNS], rows), ""]

        empties = empty_states(picks)
        if empties:
            out += [
                f"**空状态（0 个行业）：{'、'.join(empties)}**",
                "",
                "> **没有成员也是信息** —— 比如「启动」为空 = **今天没有行业在启动**，"
                "没有新方向在起，只有「持续涨」在扛 —— 这是**存量行情**，不是增量。",
                "",
            ]

    out += [
        f"## 二、技术信号（去重 {c['signals']} 只）",
        "",
        "自选股里触发的信号 —— **一个信号一张表**，各自补上它在**样本内**的 RPS（四个周期）"
        "和**所属行业**名。",
        "排序：**主周期 RPS 降序**，同分看信号新鲜度 —— 不按「强 + 顺」打分（那会把判断藏进排序）。",
        "**信号是结论，全部列出**（`--top` 只管后面的榜）。",
        "",
    ]

    if picks["signals"]:
        for i, label in enumerate(SIGNAL_LABELS.values(), 1):
            rows_ = signal_rows(picks["signals"], label)
            n_stock, n_other = signal_counts(picks["signals"], label)
            out += [f"### 2.{i} {label}（{len(rows_)} 只｜个股 {n_stock} + 非个股 {n_other}）", ""]
            if not rows_:
                out += ["（无）", ""]
                continue
            cols, cells_all = signal_table(label, picks["signals"], periods, kind="stock")
            out += [f"个股 {n_stock} 只：", "", _md_table([t for t, _, _, _ in cols], cells_all), ""]
            if n_other:
                cols2, cells2 = signal_table(label, picks["signals"], periods, kind="other")
                out += [
                    f"**非个股 {n_other} 只（ETF / 期货）** —— 这是**板块 / 大盘**的信号，"
                    "不是个股，别当票买：",
                    "",
                    _md_table([t for t, _, _, _ in cols2], cells2),
                    "",
                ]
        out += [
            "**RPS = 样本内百分位**（样本见报告表头，不是全市场）；"
            "`—` = 不在样本里（ETF / 期货 / 非美股）。",
            "**涨幅大不代表排名高** —— 样本里有涨得更猛的，两个数一起看。",
            "",
            "> 四个周期一起看：**都高** = 一路强；"
            "**20 高、250 低** = 最近才起来（可能短炒）；"
            "**20 低、250 高** = 长期强、最近歇着（可能回踩）。",
        ]
    else:
        out += ["（无）"]

    # 二、信号涉及的板块
    out += ["", f"## 三、信号涉及的板块（{c['sectors']} 个，多周期）", "",
            "「这些信号所在的板块，整体是什么状态」—— 信号逆势还是顺势，看这里。", ""]
    if picks["sectors"]:
        headers = ["行业", "家数"]
        headers += [f"{n}日中位" for n in periods]
        headers += [f"{n}日上涨" for n in periods]
        rows = [
            [s["industry"], s.get("count") or ""]
            + [_fmt((s.get(n) or {}).get("median"), 2, scale=100, sign="+") for n in periods]
            + [_fmt((s.get(n) or {}).get("up_ratio"), 2) for n in periods]
            for s in picks["sectors"]
        ]
        out += [_md_table(headers, rows)]
        out += ["", "**中位** = 该行业成分股涨幅的中位数；**上涨** = 成分股里涨的比例，"
                "**自带方向**（0.9 ≈ 九成在涨，0.1 ≈ 九成在跌）。"]
    else:
        out += ["（无）"]

    # 四、交叉筛选：行业 → 个股 → 信号 —— **行业优先**
    # （原来的「样本内强势」榜删了 —— 它就是同一批票按 RPS 排的**个股优先**视角，
    #   这里全包含它、且顺序才对。两份都列 = 把几百只票印两遍。）
    out += cross_markdown(picks)

    def _industry_block(title, stats):
        shown = take(stats, top)
        block = ["", f"## {title}（共 {len(stats)} 个，列 {len(shown)} 个）", ""]
        if not shown:
            return block + ["（无）"]
        headers = ["行业", "家数"] + [f"{n}日中位" for n in periods] + [f"{n}日上涨" for n in periods]
        rows = [
            [s["industry"], s.get("count") or ""]
            + [_fmt((s.get(n) or {}).get("median"), 2, scale=100, sign="+") for n in periods]
            + [_fmt((s.get(n) or {}).get("up_ratio"), 2) for n in periods]
            for s in shown
        ]
        return block + [_md_table(headers, rows)]

    period_label = " / ".join(str(n) for n in periods)
    out += _industry_block(f"五、行业：集体涨（{period_label} 日涨幅）", picks["hot"])
    out += _industry_block(f"六、行业：集体跌（{period_label} 日涨幅）", picks["cold"])

    out += [
        "",
        f"## 七、条件选股（{c.get('presets', 0)} 个预设）",
        "",
        "服务器端按条件筛（PE / 市值 / 形态…），**不下 K 线**。"
        "「**有信号**」= 命中里同时有技术信号的（两条路都指向它）。",
        "",
    ]
    if picks.get("presets"):
        rows = [
            [x["preset"], x.get("matched") or "", x.get("with_signal") or 0,
             x.get("description") or ""]
            for x in picks["presets"]
        ]
        out += [_md_table(["预设", "命中", "有信号", "说明"], rows)]
    else:
        out += ["（没跑 `screener` 步骤）"]

    out += [
        "",
        "---",
        "",
        "**两列别混**：**上涨占比** = 成分股里涨的比例，**自带方向**"
        "（0.9 ≈ 九成在涨，0.1 ≈ 九成在跌）；"
        "**同向占比** = 跟中位同向的比例，**不含方向**（中位为负时 = 下跌占比）。",
        "**中位涨幅**说的是「多强」—— 中位抗妖股，平均会被一只妖股拉飞。",
        "**波动幅度**（涨跌幅标准差）大 **不等于** 不集体 —— 都涨、只是幅度差得多。",
        "",
        "*由 `scan.py` 生成 · 口径可调：`--days`（主周期）/ `--rps-min` / `--top`*",
    ]
    return "\n".join(out)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="粗筛综合分析（读 data/scan/<日期>/，不联网）")
    ap.add_argument("--date", default=None, help="哪一天（默认市场所在地今天）")
    ap.add_argument("--days", type=int, default=250, help="主周期（判 RPS 达标 / 行业排序）")
    ap.add_argument("--rps-min", type=float, default=RPS_MIN,
                    help=f"RPS 达标线（默认 {RPS_MIN:g}；欧奈尔经典口径是 90）")
    ap.add_argument("--top", type=int, default=30,
                    help="**排名型**榜单（行业集体涨 / 集体跌）显示前 N 条，0 = 全列；"
                         "门槛型名单（技术信号 / 强势榜）**永远全列**，不受它影响")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    picks = analyze(
        args.date, days=args.days, rps_min=args.rps_min, top=args.top,
    )
    if args.json:
        print(json.dumps(picks, ensure_ascii=False, indent=2))
    else:
        render(picks)
