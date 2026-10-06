"""粗筛管线 —— 一条命令把所有粗筛跑完，产物全落到 `data/scan/<当天日期>/`，最后综合分析。

    python3 scan.py                      # 采集 + 综合分析（默认）
    python3 scan.py --no-analyze         # 只采集
    python3 scan.py --analyze-only       # 只分析（读已有文件夹，**不联网**）
    python3 scan.py --date 2026-10-03    # 补跑某一天
    python3 scan.py --only universe,cd   # 只跑某几步
    python3 scan.py --list               # 列出步骤

**为什么分两段**：采集要联网 / 要算（V2 快照 15 页 ≈ 45 秒 + 本地 300 多只 K 线），
分析只读文件夹。想换个筛选口径反复试，用 `--analyze-only`，**不用重新联网**。

**产物分两处**：

    data/scan/<日期>/       机器读的（分析直接读；_manifest.json 自动记录每步的条数与参数）
        universe.json       V2 全市场快照      （服务端筛，不下 K 线）
        rps.json            个股 RPS 榜（全市场口径）
        industry.json       行业集体行为
        cd.json             CD 底背离候选      （自选股，读本地 K 线）
        guppy.json          顾比突破候选       （自选股，读本地 K 线）
        screener.json       服务器端条件选股
        picks.json          综合分析

    reports/scan/<日期>.md  **给人看的报告**（Markdown，能存 / 能贴 / 能打印）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine.indicators.rs import RPS_MIN  # noqa: E402
from engine.screener.local import TRADABLE_TYPES  # noqa: E402
from fetch import day as day_store  # noqa: E402

STEPS = ("universe", "rps", "industry", "plates", "cd", "guppy")  # 默认全跑
# 可选步骤：内置预设是 futu_algo 示例改写的、不是我们的口径，所以**要点名才跑**
OPTIONAL = ("screener",)

# 快照里就带这四个周期的涨幅（`fetch/opend/universe.py::DEFAULT_DAYS`），
# 所以 RPS 和行业统计都按它们各算一遍 —— 20 日和 250 日的强弱是两回事。
PERIODS = (20, 50, 120, 250)


def _types(args):
    return None if args.types == "all" else [t.strip() for t in args.types.split(",") if t.strip()]


def _scope(within):
    return "最新一根" if within == 0 else f"最近 {within + 1} 根"


# ---------------------------------------------------------------- 各步
def step_universe(date, args):
    """全市场快照（V2 选股，服务端筛完只收结果，不吃历史K线额度）。"""
    from fetch.opend.universe import DEFAULT_DAYS, DEFAULT_MARKET, load_or_fetch

    snap = load_or_fetch(DEFAULT_MARKET, DEFAULT_DAYS, as_of=date, refresh=args.refresh)
    return f"{len(snap['rows'])} 只 / {snap.get('pages')} 页"


def step_rps(date, args):
    """个股 RPS 榜 —— **每个周期各排一次名**，落盘供分析直接查。"""
    from engine.screener import market_scan

    snap = day_store.load("universe", date=date)
    if not snap:
        return "跳过（没有 universe.json，先跑 universe）"
    rows = market_scan.rps_multi(snap["rows"], PERIODS)
    day_store.save(
        "rps", {"days": list(PERIODS), "count": len(rows), "rows": rows},
        date=date, params={"periods": list(PERIODS)},
        note=" / ".join(f"{n} 日" for n in PERIODS),
    )
    return f"{len(rows)} 只 × {len(PERIODS)} 个周期"


def step_industry(date, args):
    """行业集体行为 —— **每个周期各算一次**（行业字段就在快照里，不用另拉数据）。"""
    from engine.indicators.industry import industry_stats

    snap = day_store.load("universe", date=date)
    if not snap:
        return "跳过（没有 universe.json，先跑 universe）"

    merged: dict[str, dict] = {}
    for n in PERIODS:
        for s in industry_stats(snap["rows"], key=f"chg{n}", min_count=args.min_count):
            entry = merged.setdefault(
                s.industry, {"industry": s.industry, "count": s.count, "by_days": {}}
            )
            entry["by_days"][n] = {
                "median": s.median,
                "mean": s.mean,
                "std": s.std,
                "up_ratio": s.up_ratio,
                "rps": s.rps,
                "agreement": s.agreement,
            }
    rows = sorted(merged.values(), key=lambda x: x["industry"])
    day_store.save(
        "industry",
        {"days": list(PERIODS), "min_count": args.min_count, "count": len(rows), "stats": rows},
        date=date,
        params={"periods": list(PERIODS), "min_count": args.min_count},
        note=f"成分股 ≥ {args.min_count} / " + " / ".join(f"{n} 日" for n in PERIODS),
    )
    return f"{len(rows)} 个行业 × {len(PERIODS)} 个周期"


def step_plates(date, args):
    """自选板块（**含概念板块**）的集体行为 —— **和行业同一套算法**。

    快照里只有富途**行业**字段，没有概念板块；所以成分股要先拉一次
    （`fetch/opend/plates.py` → `data/plate_members.json`，变化很慢）。
    拿到成分股之后，**把板块名当行业名**喂给同一个 `industry_stats` —— 一行不用改。

    **概念板块和行业都是一揽子股票的集合**，不该区别对待。
    """
    from engine.indicators.industry import industry_stats
    from fetch.opend import plates as plate_store

    snap = day_store.load("universe", date=date)
    if not snap:
        return "跳过（没有 universe.json，先跑 universe）"
    doc = plate_store.read_members()
    if not doc:
        return "跳过（没有 data/plate_members.json —— 先跑 `python3 fetch/opend/plates.py`）"

    by_code = {r.get("code"): r for r in snap["rows"]}
    meta, rows = {}, []
    for code, blk in (doc.get("plates") or {}).items():
        name = blk.get("name") or code
        meta[name] = {"plate": name, "plate_code": code,
                      "members": len(blk.get("codes") or [])}
        for c in blk.get("codes") or []:
            row = by_code.get(c)
            if row:
                rows.append(dict(row, industry=name))

    # `min_count=1` —— 自选板块哪怕只剩 1 只在快照里也要留着（盯的就是它）。
    # 家数交给渲染层标出来，**别在这里悄悄丢**（板块成分股大半在样本外，很正常）。
    merged: dict[str, dict] = {}
    for n in PERIODS:
        for s in industry_stats(rows, key=f"chg{n}", min_count=1):
            entry = merged.setdefault(
                s.industry,
                dict(meta.get(s.industry) or {}, industry=s.industry,
                     count=s.count, by_days={}),
            )
            entry["by_days"][n] = {
                "median": s.median,
                "mean": s.mean,
                "std": s.std,
                "up_ratio": s.up_ratio,
                "rps": s.rps,
                "agreement": s.agreement,
            }
    out = sorted(merged.values(), key=lambda x: x["industry"])
    day_store.save(
        "plates",
        {"days": list(PERIODS), "min_count": 1, "count": len(out), "stats": out,
         "members_fetched_at": doc.get("fetched_at")},
        date=date,
        params={"periods": list(PERIODS), "min_count": 1},
        note=f"自选板块 {len(out)} 个（成分股来自 data/plate_members.json）",
    )
    return f"{len(out)} 个板块 × {len(PERIODS)} 个周期"


def step_cd(date, args):
    """CD 底背离候选（自选股，读本地日 K）。"""
    from engine.screener import cd_scan

    hits = cd_scan.scan(_types(args), within=args.within)
    day_store.save(
        "cd", {"within": args.within, "count": len(hits), "hits": hits},
        date=date, params={"within": args.within, "types": args.types},
        note=f"{_scope(args.within)}内触发",
    )
    return f"{len(hits)} 只"


def step_guppy(date, args):
    """顾比突破候选（自选股，读本地日 K）。"""
    from engine.screener import guppy_scan

    hits = guppy_scan.scan(_types(args), within=args.within)
    day_store.save(
        "guppy", {"within": args.within, "count": len(hits), "hits": hits},
        date=date, params={"within": args.within, "types": args.types},
        note=f"{_scope(args.within)}内突破",
    )
    return f"{len(hits)} 只"


def step_screener(date, args):
    """服务器端条件选股（PE / 市值 / 形态…）—— 走 V2 通道，**零 enrich**。

    ⚠️ 内置预设是 `futu_algo` 示例改写的，**不是我们的口径** ——
    所以这一步**默认不跑**，要 `--presets` 点名（见 `OPTIONAL`）。
    """
    from engine.screener.screener import PRESETS, run_preset

    names = [p.strip() for p in args.presets.split(",") if p.strip()]
    out = {}
    for name in names:
        if name not in PRESETS:
            out[name] = {"error": f"没有这个预设：{name}"}
            continue
        rows, matched = run_preset(name, verbose=False)
        out[name] = {
            "description": PRESETS[name]["description"],
            "matched": matched,
            "count": len(rows),
            "rows": rows,
        }

    day_store.save(
        "screener",
        {"presets": out, "count": sum(v.get("count", 0) for v in out.values())},
        date=date,
        params={"presets": names},
        note=" / ".join(f"{k} {v.get('count', 0)}" for k, v in out.items()),
    )
    return " / ".join(f"{k} {v.get('count', 0)}" for k, v in out.items())


STEP_FN = {
    "universe": step_universe,
    "rps": step_rps,
    "industry": step_industry,
    "plates": step_plates,
    "cd": step_cd,
    "guppy": step_guppy,
    "screener": step_screener,
}


# ---------------------------------------------------------------- 综合分析
def run_analyze(date, args):
    """综合分析 → 机器读的 `picks.json` + **人看的** `reports/scan/<日期>.md`。"""
    from engine.screener import analyze as analyzer

    picks = analyzer.analyze(
        date,
        days=args.days,
        rps_min=args.rps_min,
        top=args.top,
    )
    day_store.save(
        "picks", picks, date=date, params=picks["params"],
        note=f"信号 {picks['counts']['signals']} / 强势 {picks['counts']['strong']}",
    )
    report_dir = ROOT / "reports" / "scan"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{date}.md"
    # 表格每次重算，但 AI 写的「解读」段要留着 —— 不然重跑一次就把人话冲掉了
    keep_ai = analyzer.keep_ai_section(report_path)
    report_path.write_text(
        analyzer.to_markdown(picks, top=args.top, keep_ai=keep_ai), encoding="utf-8"
    )
    return picks, report_path


def main():
    ap = argparse.ArgumentParser(description="粗筛管线：跑完所有粗筛 → data/scan/<日期>/ → 综合分析")
    ap.add_argument("--date", default=None, help="哪一天（默认市场所在地今天）")
    ap.add_argument("--only", default=None, help="只跑某几步，逗号分隔；默认全跑")
    ap.add_argument("--no-analyze", action="store_true", help="只采集，不综合分析")
    ap.add_argument("--analyze-only", action="store_true", help="只综合分析（读已有文件夹，不联网）")
    ap.add_argument("--refresh", action="store_true", help="忽略当天快照，强制重拉")
    ap.add_argument("--list", action="store_true", help="列出步骤")
    # 各步参数
    ap.add_argument(
        "--days", type=int, default=250, choices=list(PERIODS),
        help="**主周期**：按哪个周期判 RPS 达标 / 行业排序（四个周期都会算，这个只是主排序）",
    )
    ap.add_argument("--within", type=int, default=1, help="CD / 顾比看最近几根（默认 1）")
    ap.add_argument("--types", default=",".join(TRADABLE_TYPES),
                    help="CD / 顾比的类型过滤；all = 不过滤。默认=**能交易的**（排除指数 / 板块）")
    ap.add_argument("--presets", default="",
                    help="点名跑服务器端条件选股（如 value,ma-align）；不传就跳过这一步")
    ap.add_argument("--min-count", type=int, default=5, help="行业成分股下限")
    ap.add_argument("--rps-min", type=float, default=RPS_MIN,
                    help=f"RPS 达标线（默认 {RPS_MIN:g}；欧奈尔经典口径是 90）")
    ap.add_argument("--top", type=int, default=30,
                    help="**排名型**榜单（行业集体涨 / 集体跌）显示前 N 条，0 = 全列；"
                         "门槛型名单（技术信号 / 强势榜）永远全列，不受它影响")
    ap.add_argument("--json", action="store_true", help="综合分析输出 JSON")
    args = ap.parse_args()

    if args.list:
        print("步骤（默认）：" + " → ".join(STEPS) + " → analyze")
        print("可选：" + " / ".join(OPTIONAL) + "  （服务器端条件选股，要 --presets 点名）")
        print("产物：data/scan/<日期>/  （" + " / ".join((*STEPS, *OPTIONAL, "picks")) + "）")
        print("报告：reports/scan/<日期>.md  （人看，含 AI 解读段）")
        return

    date = args.date or day_store.market_today()
    folder = day_store.day_dir(date)

    if not args.analyze_only:
        if args.only:
            wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        else:
            # screener 要点名才跑 —— 内置预设不是我们的口径
            wanted = set(STEPS) | ({"screener"} if args.presets else set())
        unknown = wanted - set(STEPS) - set(OPTIONAL)
        if unknown:
            raise SystemExit(f"未知步骤 {sorted(unknown)}；可选：{', '.join((*STEPS, *OPTIONAL))}")
        print(f"=== 粗筛管线 {date} → {folder} ===")
        for name in (*STEPS, *OPTIONAL):
            if name not in wanted:
                continue
            print(f"  [{name}] ", end="", flush=True)
            print(STEP_FN[name](date, args))

    if args.no_analyze:
        return

    picks, report_path = run_analyze(date, args)
    if args.json:
        print(json.dumps(picks, ensure_ascii=False, indent=2))
        return

    from engine.screener.analyze import render

    print(f"\n=== 综合分析 {date} ===")
    render(picks)
    print(f"\n报告已存：{report_path}")


if __name__ == "__main__":
    main()
