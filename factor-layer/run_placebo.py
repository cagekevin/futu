"""跑一次**随机对照**（R1 选择集 → R2 抽样 → R3 判决）—— 本能力的对外唯一命令。

对应 `docs/PRD/02-随机对照-PRD-2026-10-08.md`；操作细节见
`../docs/流程-如何做一次验证-2026-10-08.md`。

## 用法

```bash
cd factor-layer
.venv/bin/python run_placebo.py                              # 默认规则 + 全窗口
.venv/bin/python run_placebo.py --iterations 200             # 冒烟（快，但零分布粗）
.venv/bin/python run_placebo.py --selection <规则名> --start 2023-01-01
```

## 三个必须想清楚的事（都已内建，但要知道）

1. **窗口要留够预热**：最长预热的因子决定"有效天数"。
   默认从 `PANEL_START`（2022-05-03，票池跳到 287 只的那天）起 —— 用满才有足够样本。
2. **票池必须取一个有快照的交易日**：未收盘那天的快照还没生成，
   `stocks()` 会**静默返回 1–2 只**（不报错）。本脚本会自动往前找到像样的一天并**打印出来**。
3. **成本是预注册的一部分**：`--cost` 会**印在报告头部**；
   改它等于改实验（判决③"扣成本后为正"直接受它影响）。
"""
from __future__ import annotations

import argparse
import sys
import time

import evaluate.placebo.implementations  # noqa: F401  —— 导入即注册选择规则
import factor.implementations  # noqa: F401  —— 导入即注册因子
from evaluate.placebo.baseline_runner import run_placebo
from evaluate.placebo.null_sampler import SamplingConfig
from evaluate.placebo.placebo_report import render_report
from evaluate.placebo.selection_registry import get_selection
from factor.factor_registry import run_factor
from panel.panel_builder import read_panel
from panel.provide_reader import read_days, read_stocks

#: 宽面板起点 —— 票池在这一天从 16 只跳到 **287 只**（见 `关于策略/11-验证-数据体检报告`）。
#: 再往前票池太窄，不构成截面。
PANEL_START = "2022-05-03"

#: 单边成本率（手续费 + 滑点）。**会印在报告里**，改它 = 改实验。
DEFAULT_COST = 0.0003

#: 票池小于这个数 ⇒ 判定为"那天还没快照"，往前找。
MIN_SANE_UNIVERSE = 50

#: 找不到像样票池时，最多往前找几天。
MAX_LOOKBACK_DAYS = 5


def resolve_stocks_day(all_days: list[str], explicit: str | None) -> tuple[str, int]:
    """定下用哪一天取票池 —— 取不到像样的就**报错**，不静默用 1 只跑完全程。"""
    if explicit:
        n = len(read_stocks(explicit)["stocks"])
        if n < MIN_SANE_UNIVERSE:
            raise SystemExit(
                f"❌ {explicit} 的票池只有 {n} 只（< {MIN_SANE_UNIVERSE}）——"
                f"那天多半还没有快照。换一个交易日，或先跑 data-layer 的更新。"
            )
        return explicit, n

    for day in reversed(all_days[-MAX_LOOKBACK_DAYS:]):
        n = len(read_stocks(day)["stocks"])
        if n >= MIN_SANE_UNIVERSE:
            if day != all_days[-1]:
                print(f"ℹ️  票池取 **{day}**（库里最新的 {all_days[-1]} 还没有快照，"
                      f"只能取到 {len(read_stocks(all_days[-1])['stocks'])} 只）")
            return day, n

    raise SystemExit(
        f"❌ 最近 {MAX_LOOKBACK_DAYS} 个交易日都取不到 ≥{MIN_SANE_UNIVERSE} 只的票池 ——"
        f"数据可能没更新，先跑 `.venv/bin/python ../data-layer/pipeline.py --daily`"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="随机对照：某选择规则选出的股票，是否优于**同日随机**。")
    ap.add_argument("--selection", default="rsi_tight_consolidation",
                    help="选择规则的注册名（默认 rsi_tight_consolidation）")
    ap.add_argument("--start", default=PANEL_START,
                    help=f"面板起点（默认 {PANEL_START}）")
    ap.add_argument("--stocks-day", default=None,
                    help="用哪天的票池（默认自动取：库里最后一个有快照的交易日）")
    ap.add_argument("--iterations", type=int, default=1000,
                    help="每天抽多少次（默认 1000，与「1000 只猴子」同量级）")
    ap.add_argument("--seed", type=int, default=20261008, help="随机种子（可复现）")
    ap.add_argument("--cost", type=float, default=DEFAULT_COST,
                    help=f"单边成本率（默认 {DEFAULT_COST}；往返记 2×）。"
                         f"**会印在报告里** —— 改它 = 改实验")
    args = ap.parse_args(argv)

    if args.cost < 0:
        raise SystemExit(f"❌ --cost 不能为负：{args.cost}")

    t0 = time.time()
    all_days = read_days()
    days = [d for d in all_days if d >= args.start]
    if not days:
        raise SystemExit(f"❌ 起点 {args.start} 之后没有交易日")

    stocks_day, n_stocks = resolve_stocks_day(all_days, args.stocks_day)
    symbols = read_stocks(stocks_day)["stocks"]
    rule = get_selection(args.selection)

    print(f"══ 随机对照：{rule.name} ══")
    print(f"  窗口      : {days[0]} → {days[-1]}（{len(days)} 天）")
    print(f"  票池      : {stocks_day} 的 {n_stocks} 只")
    print(f"  抽样      : {args.iterations} 次/天，seed={args.seed}")
    print(f"  成本      : 单边 {args.cost}（往返 {args.cost * 2}）")
    print(f"  依赖因子  : {len(rule.requires)} 个 — {', '.join(rule.requires)}")
    print()

    t1 = time.time()
    panel = read_panel(symbols, days=days, stocks_day=stocks_day)
    t2 = time.time()
    factors = {name: run_factor(name, panel).values for name in rule.requires}
    t3 = time.time()
    report = run_placebo(
        selection_name=rule.name,
        panel=panel,
        factors=factors,
        cost_rate=args.cost,
        sampling=SamplingConfig(iterations=args.iterations, seed=args.seed),
    )
    t4 = time.time()

    print(render_report(report))
    print()
    print(f"⏱  面板 {t2 - t1:.0f}s | 因子 {t3 - t2:.0f}s | 对照 {t4 - t3:.0f}s"
          f" | 合计 {t4 - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
