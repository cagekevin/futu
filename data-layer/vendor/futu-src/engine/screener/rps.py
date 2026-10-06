"""相对强度排名 —— 全样本横向比较

给每只标的算四样（**参数都按权威口径，别自己调**）：

| 指标 | 口径 |
|---|---|
| **RPS(20/50/120/250)** | 区间涨幅的百分位排名；达标线见 `engine/indicators/rs.py::RPS_MIN`（默认 80，欧奈尔经典是 90） |
| **RS 新高** | RS 线（个股÷基准）创 **52 周新高** —— 即 MarketSmith 的 **Blue Dot** |
| **距最高** | 离 52 周内 RS 线最高还差多少（0% = 正在创新高） |
| **Mansfield** | **周线 + 52 周 SMA** 归一化（Weinstein 标准），正数 = 跑赢基准 |

口径出处见 `engine/indicators/rs.py` 的模块注释。

## 两个关键约束（不遵守就白算）

1. **基准日必须统一** —— 各市场最后交易日不同（A 股国庆休市、加密 7×24），
   所以只取「**出现次数最多的那个 last_date**」的标的，其余剔除。
   不然拿 10/02 的涨幅和 09/30 的涨幅比排名，没有意义。
2. **历史不足的剔除，不硬算** —— 52 周 Mansfield 要 ≈250 根日K，上市不久的票直接出局。

## 用法

```bash
python3 engine/screener/rps.py                       # 前 30 名（按 RPS250）
python3 engine/screener/rps.py --top 60 --types all
python3 engine/screener/rps.py --bench US..IXIC      # 换基准（默认 US..SPX）
python3 engine/screener/rps.py --period 120          # 按 RPS120 排序
```

⚠️ **样本是我们的自选股（几百只），不是全美股。**
所以 RPS 的含义是「**在这批票里排第几**」，不是「在全市场排第几」——
90 这个门槛在这里要重新标定。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engine.indicators import rs  # noqa: E402
from engine.screener.local import TRADABLE_TYPES, load_all, load_one  # noqa: E402
from engine.screener.table import pad  # noqa: E402

PERIODS = (20, 50, 120, 250)
MIN_BARS = 260  # 周线 Mansfield 要 52 周 ≈ 250 交易日，再留点余量


def main() -> None:
    ap = argparse.ArgumentParser(description="相对强度排名（RPS / RS 线 / Mansfield）")
    ap.add_argument("--bench", default="US..SPX", help="基准代码（默认 US..SPX 标普500）")
    ap.add_argument("--types", default=",".join(TRADABLE_TYPES),
                    help="类型过滤；all = 不过滤。默认=**能交易的**（排除指数 / 板块）")
    ap.add_argument("--top", type=int, default=30, help="显示前几名")
    ap.add_argument("--period", type=int, default=250, choices=list(PERIODS), help="按哪个周期排序")
    args = ap.parse_args()

    types = None if args.types == "all" else [t.strip() for t in args.types.split(",") if t.strip()]
    docs = {d["symbol"]: d for d in load_all(types)}  # 按代码索引，后面要查基准

    bench_doc = load_one(args.bench)
    if bench_doc is None:
        raise SystemExit(f"✗ 找不到基准 {args.bench}（data/kline/daily 下没这个文件）")
    bench_close = {int(b["date"]): float(b["close"]) for b in bench_doc["bars"]}

    # ── 约束 1：统一基准日（取众数，不是最大值 —— 加密/A股会拖偏）
    ref = Counter(int(d["bars"][-1]["date"]) for d in docs.values() if d.get("bars")).most_common(1)[0][0]
    universe = {s: d for s, d in docs.items() if d.get("bars") and int(d["bars"][-1]["date"]) == ref}
    dropped = len(docs) - len(universe)

    # ── RPS：各周期涨幅 → 百分位排名
    returns: dict[int, dict[str, float]] = {n: {} for n in PERIODS}
    for sym, doc in universe.items():
        close = [float(b["close"]) for b in doc["bars"]]
        for n in PERIODS:
            c = rs.change(close, n)
            if c is not None:
                returns[n][sym] = c
    ranks = {n: rs.rps(returns[n]) for n in PERIODS}

    # ── RS 线 / Mansfield：与基准按日期对齐
    rows = []
    for sym, doc in universe.items():
        dates, close, aligned_bench = [], [], []
        for b in doc["bars"]:
            d = int(b["date"])
            if d in bench_close:
                dates.append(d)
                close.append(float(b["close"]))
                aligned_bench.append(bench_close[d])
        if len(close) < MIN_BARS:
            continue

        line = rs.rs_line(close, aligned_bench)
        # Mansfield 标准用法是**周线 + 52 周 SMA**，所以先降到周线
        _, weekly_line = rs.to_weekly(dates, line)
        mans = rs.mansfield(weekly_line)

        rows.append({
            "code": sym,
            "name": (doc.get("name") or "")[:14],
            "rps": {n: ranks[n].get(sym) for n in PERIODS},
            "off_high": rs.off_high(line, rs.WEEKS_52_IN_DAILY),  # ← 52 周，不是全历史
            "mansfield": mans[-1],
        })

    # ── 输出
    have_all = sum(1 for r in rows if all(v is not None for v in r["rps"].values()))
    print(f"基准 {args.bench} | 基准日 {ref} | 样本 {len(universe)} 只"
          f"（剔除 {dropped} 只基准日不一致）| 历史够长 {len(rows)} 只 | 四项齐全 {have_all} 只")
    print(f"按 RPS{args.period} 降序，前 {args.top} 名"
          f"（RS 新高 = 52 周；Mansfield = 周线 52 周）\n")

    rows.sort(key=lambda r: (r["rps"][args.period] is None, -(r["rps"][args.period] or 0)))

    cols = [("代码", 13, False), ("名称", 16, False)]
    cols += [(f"RPS{n}", 7, True) for n in PERIODS]
    cols += [("RS新高", 8, True), ("距最高", 9, True), ("Mansfield", 10, True)]
    print("".join(pad(t, w, r) for t, w, r in cols))
    print("-" * sum(w for _, w, _ in cols))

    for row in rows[: args.top]:
        cells = [pad(row["code"], 13), pad(row["name"], 16)]
        cells += [
            pad(f"{row['rps'][n]:.1f}" if row["rps"][n] is not None else "—", 7, True)
            for n in PERIODS
        ]
        high = "✓" if (row["off_high"] is not None and row["off_high"] >= -0.01) else ""
        cells.append(pad(high, 8, True))
        cells.append(pad(f"{row['off_high']:.2f}%" if row["off_high"] is not None else "—", 9, True))
        cells.append(pad(f"{row['mansfield']:.1f}" if row["mansfield"] is not None else "—", 10, True))
        print("".join(cells))


if __name__ == "__main__":
    main()
