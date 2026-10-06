"""顾比突破选股 —— 从本地日 K 里筛出最近**突破顾比线**的股票

顾比线（通达信原式，实现在 `engine/indicators/guppy.py`）：

    GB_T    := LLV(L, 61);
    GB_LINE := REF(H, BARSLAST(L = GB_T));
    →  「**最近一次创 61 日新低**」那根 K 线的**最高价**

**突破** = 收盘价**上穿**顾比线（今天在上、昨天在下），且底座没换过。
底座换了一根就解锁 —— 创新低之后第一次上穿才算新突破。

**和谁是一对**：`cd_scan.py`（CD 底背离）—— 都是**自选股**、读本地 K 线。
跟 `market_scan.py` 不同，那个是**全市场**、走服务器端快照、不读 K 线。

用法：
    cd ~/Documents/futu
    python3 engine/screener/guppy_scan.py                  # 默认：**最新一根 + 前一根**突破（初筛）
    python3 engine/screener/guppy_scan.py --within 5       # 放宽：最近 5 个交易日内突破过的
    python3 engine/screener/guppy_scan.py --within 9999    # 不限制，列出每只最近一次突破
    python3 engine/screener/guppy_scan.py --types STOCK    # 只看股票（默认=**能交易的**）
    python3 engine/screener/guppy_scan.py --json           # 输出 JSON

数据来源：data/kline/daily/*.json（只读不写）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.indicators.guppy import PERIOD  # noqa: E402
from engine.indicators.guppy import scan as guppy_scan  # noqa: E402
from engine.screener.local import TRADABLE_TYPES, load_all  # noqa: E402
from engine.screener.table import pad  # noqa: E402


def scan(types=None, within=20, min_bars=None):
    """最近一次突破距今 ≤ `within` 个交易日的候选。

    `min_bars` 默认 `PERIOD + 1` —— 预热不够时 `LLV` 是截断窗口，顾比线不可信。
    """
    need = min_bars or PERIOD + 1
    hits = []
    for doc in load_all(types):
        bars = doc.get("bars") or []
        if len(bars) <= need:
            continue
        breakouts, _ = guppy_scan(bars)
        if not breakouts:
            continue
        last = breakouts[-1]
        hits.append({
            "code": doc["symbol"],
            "name": doc.get("name") or "",
            "type": doc.get("stock_type"),
            "date": last["date"],
            "gap": len(bars) - 1 - last["index"],  # 距今多少个交易日
            "close": bars[-1]["close"],  # 现价
            "trigger_close": last["close"],  # **突破那天**的收盘价，别和现价混
            "guppy": last["guppy"],
            "base_low": last["base_low"],
            "base_date": last["base_date"],
            "breakouts": len(breakouts),  # 历史上共突破过几次
        })
    hits.sort(key=lambda x: (x["gap"], x["code"]))
    return [h for h in hits if h["gap"] <= within]


def main():
    ap = argparse.ArgumentParser(description="顾比突破选股（读本地日 K）")
    # 默认 1（最新一根 + 前一根）：初筛就看「今天和昨天」。
    # 为什么不是 0：盘中拉数据时最新那根可能是半成品，只认最新一根会漏。
    ap.add_argument("--within", type=int, default=1,
                    help="只看最近 N 个交易日内突破的；默认 1 = 最新一根 + 前一根")
    ap.add_argument("--types", default=",".join(TRADABLE_TYPES),
                    help="类型过滤，逗号分隔；all = 不过滤。默认=**能交易的**（排除指数 / 板块）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    types = None if args.types == "all" else [t.strip() for t in args.types.split(",") if t.strip()]
    hits = scan(types, within=args.within)

    if args.json:
        print(json.dumps(
            {"within": args.within, "count": len(hits), "hits": hits},
            ensure_ascii=False, indent=2,
        ))
        return

    scope = "最新一根" if args.within == 0 else f"最近 {args.within + 1} 根"
    print(f"{scope}内突破顾比线：{len(hits)} 只\n")
    if not hits:
        print("（无）")
        return

    cols = [
        ("代码", 12, False), ("名称", 20, False), ("突破日", 10, False),
        ("距今", 4, True), ("突破价", 10, True), ("现价", 10, True),
        ("顾比线", 10, True), ("底座日", 10, False), ("底座最低", 10, True),
        ("历史突破", 8, True),
    ]
    print(" ".join(pad(t, w, r) for t, w, r in cols))  # 列间留缝：数字右对齐会贴住下一列
    print("-" * 116)
    for h in hits:
        cells = [
            pad(h["code"], 12),
            pad(h["name"][:18], 20),
            pad(str(h["date"]), 10),
            pad(str(h["gap"]), 4, True),
            pad(f"{h['trigger_close']:.2f}", 10, True),
            pad(f"{h['close']:.2f}", 10, True),
            pad(f"{h['guppy']:.2f}", 10, True),
            pad(str(h["base_date"]), 10),
            pad(f"{h['base_low']:.2f}", 10, True),
            pad(str(h["breakouts"]), 8, True),
        ]
        print(" ".join(cells))


if __name__ == "__main__":
    main()
