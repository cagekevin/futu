"""全市场扫描 —— RPS 榜 + 行业集体行为榜。读 `fetch/opend/universe.py` 落盘的快照，纯本地算。

**和 `engine/screener/rps.py` 的区别（别搞混）**：

| | 样本 | 算什么 | 要下载 K 线吗 |
|---|---|---|---|
| `rps.py` | **自选股**（几百只） | RPS + RS 新高 + Mansfield | **要**（读本地日K） |
| `market_scan.py` | **全市场**（9000+ → 过滤后 ~3000） | RPS + 行业集体行为 | **不要**（服务器端筛） |

**RPS 的定义是「全市场排名」** —— 所以 `rps.py` 那份的真实含义是「在这批票里排第几」，
达标线在那儿要重新标定（`RPS_MIN` 是按全市场样本定的）。**要真正的 RPS 用这个。**

RPS 算法复用 `engine/indicators/rs.py::rps()`，两边同一套口径。

用法：
    python3 engine/screener/market_scan.py                          # 个股 RPS 榜（默认 250 日）
    python3 engine/screener/market_scan.py --days 120 --top 30
    python3 engine/screener/market_scan.py --min-price 5 --min-mcap 1e9   # 本地再剔一层
    python3 engine/screener/market_scan.py --view industry                # 行业集体行为榜
    python3 engine/screener/market_scan.py --view industry --direction down    # 只看一起跌
    python3 engine/screener/market_scan.py --view industry --min-agreement 0.9 # 只看齐涨齐跌的
    python3 engine/screener/market_scan.py --view both --top 15
    python3 engine/screener/market_scan.py --refresh                # 强制重拉快照
    python3 engine/screener/market_scan.py --json

**RPS 一律按全市场算，`--min-price` / `--min-mcap` 只影响显示** ——
过滤之后重算排名就变成「在一小撮里排第一」，那不是 RPS。

数据来源：`data/scan/<日期>/universe.json`（由 `fetch/opend/universe.py` 拉取，本脚本只读不写）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.indicators import rs  # noqa: E402
from engine.indicators.industry import industry_stats  # noqa: E402
from engine.screener.table import pad  # noqa: E402
from fetch.opend.universe import DEFAULT_DAYS, DEFAULT_MARKET, load_or_fetch  # noqa: E402


def _pct(value):
    return "—" if value is None else f"{value * 100:+.2f}%"


def _num(value, unit=1.0, digits=2):
    return "—" if value is None else f"{value / unit:.{digits}f}"


# ---------------------------------------------------------------- 个股 RPS 榜
def scan_rps(rows, days, min_price=None, min_mcap=None):
    """全市场 RPS 排名。

    `min_price` / `min_mcap` **只影响返回结果，不影响排名** ——
    过滤之后重算排名就变成「在一小撮里排第一」，那不是 RPS。
    """
    key = f"chg{days}"
    changes = {
        r["code"]: r[key] for r in rows if r.get("code") and r.get(key) is not None
    }
    ranks = rs.rps(changes)  # ← 全市场排名，先算
    ranked = sorted(
        ((r, ranks[r["code"]]) for r in rows if r.get("code") in ranks),
        key=lambda t: t[1],
        reverse=True,
    )

    def keep(row):
        if min_price is not None and (row.get("price") or 0) < min_price:
            return False
        if min_mcap is not None and (row.get("mcap") or 0) < min_mcap:
            return False
        return True

    shown = [t for t in ranked if keep(t[0])]
    head = (
        f"样本 {len(ranks)} 只（服务端已过滤；RPS 按样本内排名）"
        + (f"｜本地再过滤后 {len(shown)} 只" if len(shown) != len(ranked) else "")
    )
    return head, shown, key


def rps_multi(rows, periods):
    """**多周期** RPS —— 返回 `list[dict]`，每只一条：

        {"code", "name", "industry", "price", "mcap",
         "rps": {20: .., 50: .., 120: .., 250: ..},
         "chg": {20: .., 50: .., 120: .., 250: ..}}

    ⚠️ 每个周期**各自在全市场排一次名** —— 别拿一个周期的名次当所有周期的，
    20 日和 250 日的强弱是两回事（这也是为什么要一起看）。

    排序按最后一个周期（调用方传升序，如 `(20, 50, 120, 250)` → 按 250 日）。
    """
    merged: dict[str, dict] = {}
    for n in periods:
        key = f"chg{n}"
        changes = {
            r["code"]: r[key] for r in rows if r.get("code") and r.get(key) is not None
        }
        for code, score in rs.rps(changes).items():
            entry = merged.setdefault(code, {"rps": {}, "chg": {}})
            entry["rps"][n] = round(float(score), 4)
            entry["chg"][n] = changes[code]

    out = []
    for row in rows:
        code = row.get("code")
        got = merged.get(code)
        if not got:
            continue
        out.append({
            "code": code,
            "name": row.get("name"),
            "industry": row.get("industry"),
            "price": row.get("price"),
            "mcap": row.get("mcap"),
            "rps": got["rps"],
            "chg": got["chg"],
        })
    out.sort(key=lambda x: -(x["rps"].get(periods[-1]) or -1))
    return out


def print_rps(head, shown, key, top):
    print(f"{head}｜周期 {key[3:]} 日\n")
    if not shown:
        print("（无）")
        return
    cols = [
        ("#", 4, True),
        ("代码", 11, False),
        ("名称", 22, False),
        ("RPS", 7, True),
        ("涨幅", 11, True),
        ("现价", 9, True),
        ("市值(亿$)", 11, True),
        ("行业", 0, False),
    ]
    print("".join(pad(t, w, r) for t, w, r in cols))
    print("-" * 100)
    for i, (row, score) in enumerate(shown[: top or len(shown)], start=1):
        cells = [
            pad(str(i), 4, True),
            pad(str(row.get("code")), 11),
            pad((row.get("name") or "")[:20], 22),
            pad(f"{score:.2f}", 7, True),
            pad(_pct(row.get(key)), 11, True),
            pad(_num(row.get("price")), 9, True),
            pad(_num(row.get("mcap"), 1e8, 1), 11, True),
            pad((row.get("industry") or "")[:18], 0),
        ]
        print("".join(cells))


# ---------------------------------------------------------------- 行业集体行为
def scan_industry(rows, days, min_count=5, direction="all", min_agreement=0.0):
    """行业集体行为。`direction` 传 `down` 时**最弱在前**（找一起跌的）。

    `min_agreement` 是**同向占比**门槛 —— 它**不含方向**，所以只在
    「找齐涨齐跌的板块」时用（0.9 = 九成同向）。**判断涨跌要用 `median` / `up_ratio`。**
    """
    stats = industry_stats(rows, key=f"chg{days}", min_count=min_count)
    if direction == "up":
        stats = [s for s in stats if s.median >= 0]
    elif direction == "down":
        stats = [s for s in stats if s.median < 0]
    if min_agreement:
        stats = [s for s in stats if s.agreement >= min_agreement]

    stats.sort(key=lambda s: s.rps, reverse=direction != "down")

    head = (
        f"行业 {len(stats)} 个（成分股 ≥ {min_count} 只）"
        f"｜周期 {days} 日｜方向 {direction}"
        + (f"｜同向占比 ≥ {min_agreement}" if min_agreement else "")
    )
    return head, stats


def print_industry(head, stats, top):
    print(head + "\n")
    if not stats:
        print("（无）")
        return
    cols = [
        ("行业", 26, False),
        ("家数", 5, True),
        ("中位涨幅", 10, True),
        ("平均涨幅", 10, True),
        ("上涨占比", 9, True),
        ("波动幅度", 9, True),
        ("行业RPS", 8, True),
        ("同向占比", 8, True),
    ]
    print(" ".join(pad(t, w, r) for t, w, r in cols))  # 列间留缝，中文列宽会顶满
    print("-" * 92)
    for s in stats[: top or len(stats)]:
        cells = [
            pad(s.industry[:24], 26),
            pad(str(s.count), 5, True),
            pad(_pct(s.median), 10, True),
            pad(_pct(s.mean), 10, True),
            pad(f"{s.up_ratio * 100:.1f}%", 9, True),
            pad(f"{s.std * 100:.2f}", 9, True),
            pad(f"{s.rps:.2f}", 8, True),
            pad(f"{s.agreement:.2f}", 8, True),
        ]
        print(" ".join(cells))
    print(
        "\n两列别混："
        "\n  **上涨占比** —— 成分股里**涨的**比例，**自带方向**。0.9 = 九成在涨，0.1 = 九成在跌。"
        "\n  **同向占比** —— 跟**中位**同向的比例，**不含方向**。中位为负时它就是下跌占比。"
        "\n     0.83 在中位为负的行业里读作「83% 在跌」，别当成「83% 在涨」。"
        "\n  涨得均不均 → 看**波动幅度**（涨跌幅标准差）。小 = 齐步走，大 = 有龙头拉得远。"
        "\n  **波动幅度大 ≠ 不集体** —— 都涨、只是幅度差得多，仍然是集体涨。"
    )


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="全市场扫描：RPS 榜 + 行业集体行为榜")
    ap.add_argument("--market", default=DEFAULT_MARKET)
    ap.add_argument("--days", type=int, default=250, help="用哪个周期的涨幅排名")
    ap.add_argument("--view", choices=["rps", "industry", "both"], default="rps")
    ap.add_argument("--top", type=int, default=30, help="显示前 N 行；0 = 全部")
    ap.add_argument("--min-price", type=float, default=None, help="只看现价 ≥ 这个的")
    ap.add_argument("--min-mcap", type=float, default=None, help="只看市值 ≥ 这个的（美元）")
    ap.add_argument("--min-count", type=int, default=5, help="行业成分股下限（默认 5）")
    ap.add_argument("--min-agreement", type=float, default=0.0,
                    help="同向占比下限（**不含方向**），如 0.9 = 只看齐涨齐跌的板块")
    ap.add_argument("--direction", choices=["all", "up", "down"], default="all")
    ap.add_argument("--refresh", action="store_true", help="忽略当日快照，强制重拉")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    snap = load_or_fetch(args.market, DEFAULT_DAYS, refresh=args.refresh)
    rows = snap["rows"]
    if args.days not in [int(d) for d in snap.get("days") or []]:
        raise SystemExit(
            f"快照里没有 {args.days} 日周期（有 {snap.get('days')}）。"
            f"用 --refresh 重拉，或在 fetch/opend/universe.py 的 DEFAULT_DAYS 里加。"
        )

    if args.json:
        payload = {"as_of": snap["as_of"], "market": snap["market"], "days": args.days}
        if args.view in ("rps", "both"):
            _, shown, key = scan_rps(rows, args.days, args.min_price, args.min_mcap)
            payload["rps"] = [
                {
                    "rank": i,
                    "code": r.get("code"),
                    "name": r.get("name"),
                    "industry": r.get("industry"),
                    "rps": score,
                    "chg": r.get(key),
                    "price": r.get("price"),
                    "mcap": r.get("mcap"),
                }
                for i, (r, score) in enumerate(shown[: args.top or len(shown)], start=1)
            ]
        if args.view in ("industry", "both"):
            _, stats = scan_industry(
                rows, args.days, args.min_count, args.direction, args.min_agreement
            )
            payload["industry"] = [
                {
                    "industry": s.industry,
                    "count": s.count,
                    "median": s.median,
                    "mean": s.mean,
                    "std": s.std,
                    "up_ratio": s.up_ratio,
                    "rps": s.rps,
                    "agreement": s.agreement,
                    "direction": s.direction,
                }
                for s in stats[: args.top or len(stats)]
            ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.view in ("rps", "both"):
        head, shown, key = scan_rps(rows, args.days, args.min_price, args.min_mcap)
        print_rps(head, shown, key, args.top)
        if args.view == "both":
            print()
    if args.view in ("industry", "both"):
        head, stats = scan_industry(
            rows, args.days, args.min_count, args.direction, args.min_agreement
        )
        print_industry(head, stats, args.top)


if __name__ == "__main__":
    main()
