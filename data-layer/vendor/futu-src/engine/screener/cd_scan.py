"""CD 底背离选股 —— 从本地日 K 里筛出最近触发 ▲（DXDX_CD = 1）的股票

**名字说明**：以前叫 `screen.py`，太含糊 —— 跟 `screener.py` 只差一个字母、功能却完全不同，
接手的人必踩。现在叫 `cd_scan.py`：一看就知道是 **CD 底背离**。

用法：
    cd ~/Documents/futu
    python3 engine/screener/cd_scan.py                    # 默认：**最新一根 + 前一根**触发 ▲（初筛）
    python3 engine/screener/cd_scan.py --within 5         # 放宽：最近 5 个交易日内触发过的
    python3 engine/screener/cd_scan.py --within 9999      # 不限制，列出每只最近一次触发
    python3 engine/screener/cd_scan.py --types STOCK      # 只看股票（默认=**能交易的**）
    python3 engine/screener/cd_scan.py --json             # 输出 JSON，便于后续处理

数据来源：data/kline/daily/*.json（由 fetch/rest/kline.mjs 拉取，本脚本只读不写）
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine.indicators.cd import calc_cd  # noqa: E402
from engine.screener.local import TRADABLE_TYPES, load_all  # noqa: E402


def scan(types=None, within=1):
    """最近一次触发 ▲ 距今 ≤ `within` 个交易日的候选。

    `len(bars) < 60` 的直接跳过 —— CD 要算 MACD 背离，历史太短算不准。
    """
    hits = []
    for doc in load_all(types):
        bars = doc.get("bars") or []
        if len(bars) < 60:
            continue
        r = calc_cd(bars)
        dxdx = r["DXDX"]
        last = -1
        for i in range(len(dxdx) - 1, -1, -1):  # 最近一次触发 ▲ 的位置
            if dxdx[i] == 1:
                last = i
                break
        if last < 0:
            continue
        hits.append({
            "code": doc["symbol"],
            "name": doc.get("name") or "",
            "type": doc.get("stock_type"),
            "date": bars[last]["date"],
            "gap": len(bars) - 1 - last,  # 距今多少个交易日
            "close": bars[-1]["close"],
            "trigger_close": bars[last]["close"],
            "dif": r["D"][-1],
            "dea": r["A"][-1],
            "macd": r["M"][-1],
        })
    hits.sort(key=lambda x: (x["gap"], x["code"]))
    return [h for h in hits if h["gap"] <= within]


def main():
    ap = argparse.ArgumentParser(description="CD 底背离选股（读本地日 K）")
    # 默认 1（最新一根 + 前一根）：初筛就看「今天和昨天」。
    # 为什么不是 0：盘中拉数据时最新那根可能是半成品，只认最新一根会漏。
    ap.add_argument("--within", type=int, default=1,
                    help="只看最近 N 个交易日内触发的；默认 1 = 最新一根 + 前一根")
    ap.add_argument("--types", default=",".join(TRADABLE_TYPES),
                    help="类型过滤，逗号分隔；all = 不过滤。默认=**能交易的**（排除指数 / 板块）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    types = None if args.types == "all" else [t.strip() for t in args.types.split(",") if t.strip()]
    items = load_all(types)
    shown = scan(types, within=args.within)

    if args.json:
        print(json.dumps({"within": args.within, "count": len(shown), "hits": shown}, ensure_ascii=False, indent=2))
        return

    scope = "最新一根" if args.within == 0 else f"最近 {args.within + 1} 根"
    print(f"扫描 {len(items)} 只（类型 {args.types}）｜{scope}内触发 ▲：{len(shown)} 只\n")
    if not shown:
        print("（无）")
        return

    print(f"{'代码':<12}{'名称':<20}{'▲日期':<10}{'距今':>4}{'触发价':>10}{'现价':>10}{'DIF':>9}{'DEA':>9}{'MACD':>9}")
    print("-" * 100)
    for h in shown:
        name = h["name"][:18]
        print(f"{h['code']:<12}{name:<20}{h['date']:<10}{h['gap']:>4}"
              f"{h['trigger_close']:>10.2f}{h['close']:>10.2f}"
              f"{h['dif']:>9.3f}{h['dea']:>9.3f}{h['macd']:>9.3f}")


if __name__ == "__main__":
    main()
