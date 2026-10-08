"""M4 跨进程 / 网络访问入口（承 D）—— 把对外查询暴露成 JSON CLI。

下游（脚本 / 别的语言 / 网络服务）通过它拿数据，**不需要 import 本仓库**
（承 D）。输出永远是 JSON，契约稳定（承 P4）。

用法：
    python -m provide.cli days
    python -m provide.cli symbols --day 2026-10-06
    python -m provide.cli stocks --day 2026-10-06
    python -m provide.cli items --day 2026-10-06 --symbol SPX
    python -m provide.cli get --day 2026-10-06 --symbol SPX --item net_gex
    python -m provide.cli matrix --day 2026-10-06 --items net_gex zero_gamma
    python -m provide.cli timeseries --symbol SPX --item net_gex
    python -m provide.cli export --day 2026-10-06 --items net_gex spot
    python -m provide.cli align --symbols SPY QQQ IWM --item kline      # 时序对齐（交集）
    python -m provide.cli panel --symbols AAPL MSFT --item kline --adjust hfq   # 截面面板
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from store.read import Missing
from .api import Access


def _emit(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, default=str, allow_nan=True)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="trading-desk 对外数据接口（JSON）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("days")
    p = sub.add_parser("symbols"); p.add_argument("--day", required=True)
    p = sub.add_parser("stocks"); p.add_argument("--day", required=True)
    p = sub.add_parser("items"); p.add_argument("--day", required=True); p.add_argument("--symbol", required=True)
    p = sub.add_parser("get"); p.add_argument("--day", required=True); p.add_argument("--symbol", required=True); p.add_argument("--item", required=True)
    p = sub.add_parser("matrix"); p.add_argument("--day", required=True); p.add_argument("--items", nargs="+", required=True); p.add_argument("--symbols", nargs="*", default=None)
    p = sub.add_parser("timeseries"); p.add_argument("--symbol", required=True); p.add_argument("--item", required=True)
    p = sub.add_parser("export"); p.add_argument("--day", required=True); p.add_argument("--items", nargs="+", required=True); p.add_argument("--symbols", nargs="*", default=None)
    p = sub.add_parser("align")
    p.add_argument("--symbols", nargs="+", required=True)
    p.add_argument("--item", default="kline")
    p.add_argument("--days", nargs="*", default=None)
    p.add_argument("--min-bars", type=int, default=None, help="交集小于它才降级并集+ffill")
    p.add_argument("--adjust", choices=("hfq", "qfq"), default=None,
                   help="复权口径（**不传 = 原样 raw**；hfq 因果、回测用）")

    # ★ 截面面板（与 align 的**时序对齐**语义相反：按日分组、组内可缺、不做对齐）
    p = sub.add_parser("panel")
    p.add_argument("--symbols", nargs="+", required=True)
    p.add_argument("--item", default="kline")
    p.add_argument("--days", nargs="*", default=None)
    p.add_argument("--adjust", choices=("hfq", "qfq"), default=None,
                   help="复权口径（**不传 = 原样 raw**；hfq 因果、回测用）")

    a = ap.parse_args(argv)
    acc = Access()

    if a.cmd == "days":
        _emit(acc.days())
    elif a.cmd == "symbols":
        _emit(acc.symbols(a.day))
    elif a.cmd == "stocks":
        _emit(acc.stocks(a.day))
    elif a.cmd == "items":
        _emit(acc.items(a.day, a.symbol))
    elif a.cmd == "get":
        try:
            _emit(acc.record(a.day, a.symbol, a.item))
        except Missing as e:
            _emit({"error": "missing", "detail": str(e)})
            return 3
    elif a.cmd == "matrix":
        _emit(acc.matrix(a.day, a.items, a.symbols))
    elif a.cmd == "timeseries":
        _emit([r.as_dict() for r in acc.timeseries(a.symbol, a.item)])
    elif a.cmd == "export":
        _emit(acc.export_rows(a.day, a.items, a.symbols))
    elif a.cmd == "align":
        kw = {} if a.min_bars is None else {"min_bars": a.min_bars}
        _emit(acc.align_panel(a.symbols, item=a.item, days=a.days,
                              adjust=a.adjust, **kw))
    elif a.cmd == "panel":
        _emit(acc.panel(a.symbols, a.item, days=a.days, adjust=a.adjust))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
