"""V2 全市场快照 —— 一次拿到全市场（美股 9000+）的 N 日涨幅 / 行业 / 价格 / 市值。

**为什么需要它**：RPS 的定义是「**全市场**涨幅排名」。拿自选股那 300 多只排出来的
不叫 RPS。而筛选在富途服务器完成 —— **不吃历史K线额度**，
也不用把 9000 只的 K 线拉下来（那样一次就耗尽 300 的额度）。

**通道细节全在 `fetch/opend/screen_v2.py`**（单位、分页、protobuf 版本、两个坑）。
这里只管三件事：

    1. 默认过滤 `ScreenSpec` —— 9421 只 / 48 页 → 2954 只 / 15 页
    2. 落盘 `data/scan/<日期>/universe.json`（由 `fetch/day.py` 统一放）
    3. 当日缓存 —— 同一天不重复拉

用法：
    python3 fetch/opend/universe.py                    # 拉当日快照（当天已拉过就复用）
    python3 fetch/opend/universe.py --refresh          # 强制重拉
    python3 fetch/opend/universe.py --no-filter        # 不过滤，拉全市场 9421 只
    python3 fetch/opend/universe.py --max-pages 3      # 只拉前 3 页（调试用）
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fetch import day as day_store  # noqa: E402
from fetch.opend.screen_v2 import (  # noqa: E402
    DEFAULT_DAYS,
    DEFAULT_MARKET,
    DEFAULT_SPEC,
    PAGE_MAX,
    ScreenSpec,
    filters_from_spec,
    run_screen,
)

__all__ = [
    "DEFAULT_DAYS",
    "DEFAULT_MARKET",
    "DEFAULT_SPEC",
    "ScreenSpec",
    "market_today",
    "fetch_universe",
    "snapshot_path",
    "load_or_fetch",
]

# 「没传」和「显式传 None（不过滤）」是两回事，用哨兵区分 ——
# 之前用 `spec=None` 表示默认值，结果 `--no-filter` 被静默忽略了。
_UNSET = object()


def market_today(market=DEFAULT_MARKET):
    """市场所在地的今天 —— 落盘目录用同一个日期，实现统一在 `fetch/day.py`。"""
    return day_store.market_today(market)


def fetch_universe(
    market=DEFAULT_MARKET,
    days=DEFAULT_DAYS,
    *,
    spec=_UNSET,
    gateway=None,
    page_count=PAGE_MAX,
    max_pages=None,
    verbose=True,
):
    """分页拉完整个市场，返回快照 dict（还没落盘）。

    `spec`：`ScreenSpec` = 套默认过滤；`None` = **不过滤**；不传 = `DEFAULT_SPEC`。
    """
    spec = DEFAULT_SPEC if spec is _UNSET else spec
    rows, total, pages = run_screen(
        market,
        days,
        filters=filters_from_spec(spec) if spec is not None else None,
        page_count=page_count,
        max_pages=max_pages,
        gateway=gateway,
        verbose=verbose,
    )
    return {
        "market": market,
        "as_of": market_today(market),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "days": [int(d) for d in days],
        "spec": asdict(spec) if spec is not None else None,  # 缓存按它判有效性
        "count": total,
        "pages": pages,
        "rows": rows,
    }


# ---------------------------------------------------------------- 缓存
def snapshot_path(market=DEFAULT_MARKET, as_of=None, root=None):
    """快照落在**当天文件夹**里：`data/scan/<日期>/universe.json`。"""
    return day_store.day_dir(as_of or market_today(market), market, root) / "universe.json"


def load_or_fetch(
    market=DEFAULT_MARKET,
    days=DEFAULT_DAYS,
    *,
    spec=_UNSET,
    as_of=None,
    refresh=False,
    root=None,
    gateway=None,
    verbose=True,
    **fetch_kwargs,
):
    """当天快照存在就直接复用（同一天不用重复拉），否则拉一次落盘。

    缓存有效性看两件事：**周期够不够**、**过滤条件一不一样** ——
    条件变了必须重拉，否则下游拿到的是旧口径的数据还不知道。
    """
    spec = DEFAULT_SPEC if spec is _UNSET else spec
    want = {int(d) for d in days}
    date = as_of or market_today(market)
    snap = day_store.load("universe", date=date, market=market, root=root)
    if snap is not None and not refresh:
        have = {int(d) for d in snap.get("days") or []}
        same_spec = snap.get("spec") == (asdict(spec) if spec is not None else None)
        if want <= have and same_spec:
            if verbose:
                print(f"复用快照 {date}/universe.json（{snap.get('count')} 只）")
            return snap
        if verbose:
            why = [] if want <= have else [f"缺周期 {sorted(want - have)}"]
            if not same_spec:
                why.append("过滤条件变了")
            print(f"快照失效（{'；'.join(why)}），重新拉取")

    snap = fetch_universe(
        market, days, spec=spec, gateway=gateway, verbose=verbose, **fetch_kwargs
    )
    path = day_store.save(
        "universe", snap, date=date, market=market, root=root,
        note=f"{len(snap['rows'])} 只 / {snap.get('pages')} 页",
    )
    if verbose:
        print(f"落盘 {path}（{len(snap['rows'])} 只）")
    return snap


def main():
    ap = argparse.ArgumentParser(description="拉全市场快照（富途 V2 选股）")
    ap.add_argument("--market", default=DEFAULT_MARKET)
    ap.add_argument("--days", default=",".join(map(str, DEFAULT_DAYS)), help="逗号分隔")
    ap.add_argument("--as-of", default=None, help="快照日期（默认市场所在地今天）")
    ap.add_argument("--refresh", action="store_true", help="忽略当日缓存，强制重拉")
    ap.add_argument("--max-pages", type=int, default=None, help="只拉前 N 页（调试用）")
    ap.add_argument("--min-price", type=float, default=DEFAULT_SPEC.min_price)
    ap.add_argument("--min-mcap", type=float, default=DEFAULT_SPEC.min_mcap)
    ap.add_argument("--min-turnover", type=float, default=DEFAULT_SPEC.min_avg_turnover)
    ap.add_argument("--min-listed-days", type=int, default=DEFAULT_SPEC.min_listed_days)
    ap.add_argument("--no-filter", action="store_true", help="不过滤，拉全市场 9421 只")
    args = ap.parse_args()

    spec = None if args.no_filter else ScreenSpec(
        min_price=args.min_price,
        min_mcap=args.min_mcap,
        min_avg_turnover=args.min_turnover,
        min_listed_days=args.min_listed_days,
    )
    days = tuple(int(d) for d in args.days.split(",") if d.strip())
    snap = load_or_fetch(
        args.market, days, spec=spec, as_of=args.as_of,
        refresh=args.refresh, max_pages=args.max_pages,
    )
    print(json.dumps({k: v for k, v in snap.items() if k != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
