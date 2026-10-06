"""服务器端条件选股 —— 走 V2 通道（`fetch/opend/screen_v2.py`），**不吃历史K线额度**。

**为什么不用 V1**：V1（`get_stock_filter`）**只回代码**，必须本地再拉快照补字段 ——
美股 OTC 会让整批快照失败 → 拆批重试 → 撞限频（**实测 90 秒**）。
V2 **直接回字段值**，零 enrich，同样的筛选几秒就完。

⚠️ **内置预设是从 `futu_algo` 示例改写的，不是我们的口径** —— 拿来验证通道能跑通。
要筛什么自己改 `PRESETS`（filter 形状见 `screen_v2.add_filters`）。

⚠️ **这个脚本不落盘** —— 日常落盘由 `scan.py` 的 `screener` 步骤统一管
（`data/scan/<日期>/screener.json`）。想自己存就加 `--json` 重定向。

用法：
    python3 engine/screener/screener.py --list                              # 列出预设
    python3 engine/screener/screener.py value                               # 跑预设
    python3 engine/screener/screener.py macd-cross --json
    python3 engine/screener/screener.py --field PE_TTM --max 15 --limit 20  # 临时条件
    python3 engine/screener/screener.py value --notify                      # 推到通知渠道

filter 里能用的字段名（V2 实测支持的枚举，**别自己猜**）：

    简单属性  PRICE / MARKET_CAP / PE_TTM / PE_ANNUAL / PB / LISTED_DAYS / VOLUME_RATIO …
    区间累计  PRICE_CHANGE_PCT / AVG_TURNOVER / AVG_VOLUME / TURNOVER_RATIO / AMPLITUDE …
    技术形态  MA_LONG / MA_SHORT / MACD_GOLD_CROSS / MACD_BOTTOM_DIVERGE / KDJ_GOLD_CROSS /
              RSI_BOTTOM_DIVERGE / BOLL_BREAK_UPPER / BULLISH / BEARISH …
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fetch.opend.screen_v2 import DEFAULT_DAYS, DEFAULT_MARKET, run_screen  # noqa: E402

# 预设形状：`filters`（见 screen_v2.add_filters）/ `sort`（决定截断取谁）/ `limit`
PRESETS: dict[str, dict] = {
    "value": {
        "description": "PE(TTM) 0~15 且市值 ≥ 50 亿美元（按市值降序）",
        "filters": [
            {"type": "simple_property", "name": "PE_TTM", "lower": 0, "upper": 15},
            {"type": "simple_property", "name": "MARKET_CAP", "lower": 5e9},
        ],
        "sort": {"property_type": "simple", "name": "MARKET_CAP", "direction": "DESC"},
        "limit": 100,
    },
    "ma-align": {
        "description": "日线均线多头排列（MA5 > MA10 > MA20 > MA30 > MA60）",
        "filters": [
            {"type": "indicator_pattern", "name": "MA_LONG", "period": "DAY"},
            {"type": "simple_property", "name": "MARKET_CAP", "lower": 1e9},
        ],
        "limit": 100,
    },
    "macd-cross": {
        "description": "日线 MACD 金叉，且近 5 日平均成交额 ≥ 5000 万",
        "filters": [
            {"type": "indicator_pattern", "name": "MACD_GOLD_CROSS", "period": "DAY"},
            {"type": "cumulative_property", "name": "AVG_TURNOVER", "days": 5, "lower": 5e7},
        ],
        "limit": 100,
    },
}


def run_preset(name, *, gateway=None, verbose=True, limit=None):
    """跑一个预设，返回 `(rows, matched)`。

    `matched` 是服务器端命中总数；`rows` 最多 `limit` 条（**排序在请求里**，
    所以截断取的是排在前面的那些）。
    """
    spec = PRESETS[name]
    rows, matched, _ = run_screen(
        DEFAULT_MARKET,
        DEFAULT_DAYS,
        filters=spec["filters"],
        sort=spec.get("sort"),
        max_rows=limit or spec.get("limit", 100),
        gateway=gateway,
        verbose=verbose,
    )
    return rows, matched


def show(rows, matched, as_json=False):
    if as_json:
        print(json.dumps({"matched": matched, "rows": rows}, ensure_ascii=False, indent=2, default=str))
        return
    print(f"\n命中 {matched} 只，返回 {len(rows)} 只\n")
    if not rows:
        return
    cols = [c for c in ("code", "name", "industry", "price", "mcap") if any(c in r for r in rows)]

    def cell(r, c):
        v = r.get(c)
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:,.0f}" if c == "mcap" else f"{v:,.2f}"
        return str(v)

    widths = {c: max(len(c), *(len(cell(r, c)) for r in rows)) for c in cols}
    print("  ".join(f"{c:<{widths[c]}}" for c in cols))
    print("-" * (sum(widths.values()) + 2 * (len(cols) - 1)))
    for r in rows:
        print("  ".join(f"{cell(r, c):<{widths[c]}}" for c in cols))


def notify(rows, matched, name):
    """把结果推到 `config/notify.yaml` 里启用的渠道（复用 futu_algo 的 EventBus）。"""
    from stock.features.notify import load_channels, missing_env
    from stock.features.notify import load_config as load_notify_config

    cfg, bark = load_notify_config()
    channels = load_channels(cfg, bark)
    if not channels:
        print("⚠ config/notify.yaml 里没有启用任何渠道，本次不推送。")
        return
    if need := missing_env(cfg, bark):
        print(f"⚠ 通知缺少密钥 {', '.join(need)}，本次不推送。")
        return

    from futu_algo.events import EventBus
    from futu_algo.notify.dispatcher import Notifier

    bus = EventBus()
    notifier = Notifier(cfg, bus, channels=channels)
    notifier.start()
    try:
        bus.emit("screener", f"Screener {name}: {len(rows)} stock(s) ({matched} matched)",
                 preset=name, count=len(rows))
    finally:
        notifier.stop()  # 队列是 FIFO，会先把已排队的发完再退出


def main():
    ap = argparse.ArgumentParser(description="服务器端条件选股（V2 通道，不吃历史K线额度）")
    ap.add_argument("preset", nargs="?", choices=sorted(PRESETS), help="内置预设名")
    ap.add_argument("--list", action="store_true", help="列出内置预设")
    ap.add_argument("--field", help="临时条件：字段名（如 PE_TTM）")
    ap.add_argument("--min", type=float)
    ap.add_argument("--max", type=float)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true", help="推送到 config/notify.yaml 启用的渠道")
    args = ap.parse_args()

    if args.list or (not args.preset and not args.field):
        print("内置预设（**从 futu_algo 示例改写，不是我们的口径**）：")
        for name, spec in sorted(PRESETS.items()):
            print(f"  {name:<12} {spec['description']}")
        return

    if args.field:
        spec = {
            "description": f"{args.field} {args.min}~{args.max}",
            "filters": [{"type": "simple_property", "name": args.field,
                         "lower": args.min, "upper": args.max}],
            "sort": None,
            "limit": args.limit,
        }
        name = args.field.lower()
        PRESETS[name] = spec  # 临时预设：只在这次进程里有效
    else:
        name = args.preset

    rows, matched = run_preset(name, limit=args.limit)
    show(rows, matched, args.json)
    if args.notify:
        notify(rows, matched, name)


if __name__ == "__main__":
    main()
