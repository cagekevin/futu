"""取数 CLI —— 探查"我们能拿到什么数据"（诊断 / 手动取数用）。

用法：
    python -m fetch.fetch_cli sources
    python -m fetch.fetch_cli chain SPX                       # 期权链
    python -m fetch.fetch_cli kline AAPL --ktype K_DAY        # 日线（默认 REST）
    python -m fetch.fetch_cli kline AAPL --ktype K_240M       # 4H（需显式走 OpenD）
    python -m fetch.fetch_cli snapshot --max-pages 1          # 全市场快照
    python -m fetch.fetch_cli watchlist                       # 自选
    python -m fetch.fetch_cli trading-days --market US --start .. --end ..
    python -m fetch.fetch_cli ktypes                          # 列出各周期

输出永远是 JSON（可直接喂给别的程序）。
"""
from __future__ import annotations

import argparse
import json
import sys

from . import fetch_api


def _emit(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, default=str, allow_nan=True)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="trading-desk 取数探查 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sources")
    sub.add_parser("ktypes")

    p = sub.add_parser("chain"); p.add_argument("symbol"); p.add_argument("--as-of", default=None)
    p = sub.add_parser("kline")
    p.add_argument("symbol"); p.add_argument("--ktype", default="K_DAY")
    p.add_argument("--as-of", default=None); p.add_argument("--years", type=int, default=1)
    p.add_argument("--months", type=int, default=None); p.add_argument("--source", default=None)
    p.add_argument("--index", action="store_true")
    p = sub.add_parser("snapshot"); p.add_argument("--max-pages", type=int, default=1)
    sub.add_parser("watchlist")
    p = sub.add_parser("trading-days")
    p.add_argument("--market", default="US")
    p.add_argument("--start", required=True); p.add_argument("--end", required=True)

    a = ap.parse_args(argv)

    if a.cmd == "sources":
        _emit(fetch_api.available_sources())
    elif a.cmd == "ktypes":
        from .sources.futu.opend_source import NATIVE_KTYPES
        from .sources.futu.rest_source import KTYPE as REST_KTYPE
        _emit({"opend_native": list(NATIVE_KTYPES), "rest_ktype": REST_KTYPE})
    elif a.cmd == "chain":
        r = fetch_api.chain(a.symbol, as_of=a.as_of)
        _emit({"symbol": r.symbol, "spot": r.spot,
               "feed_timestamp": r.feed_timestamp, "rows": len(r.rows),
               "sample": r.rows[0]})
    elif a.cmd == "kline":
        r = fetch_api.kline(a.symbol, as_of=a.as_of, ktype=a.ktype, years=a.years,
                            months=a.months, index=a.index, source=a.source)
        _emit({"symbol": r.symbol, "rows": len(r.rows), "extra": r.extra,
               "first": r.rows[0] if r.rows else None,
               "last": r.rows[-1] if r.rows else None})
    elif a.cmd == "snapshot":
        from .sources.futu.opend_source import FutuSource
        from .fetch_types import Request
        r = FutuSource().fetch_snapshot(Request(symbol="UNIVERSE"),
                                        max_pages=a.max_pages)
        _emit({"rows": len(r.rows), "extra": r.extra, "sample": r.rows[0]})
    elif a.cmd == "watchlist":
        r = fetch_api.watchlist()
        _emit({"rows": len(r.rows), "extra": r.extra, "sample": r.rows[:3]})
    elif a.cmd == "trading-days":
        r = fetch_api.trading_days(a.market, a.start, a.end)
        _emit({"rows": len(r.rows), "extra": r.extra,
               "first": r.rows[0] if r.rows else None,
               "last": r.rows[-1] if r.rows else None})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
