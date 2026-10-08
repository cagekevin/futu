#!/usr/bin/env python3
"""Tugboat「突破交易 2.0」—— **回测入口（跨层组装点）**。

需求级规格：`backtest/docs/design/03-策略规格-Tugboat突破交易2.0-2026-10-08.md`

---

## ★ 为什么这个文件在**仓库根**，而不在某一层里

它要同时用两样东西：

| 要什么 | 在哪层 |
|---|---|
| 面板 + 因子（`rsi14` / `rs_rank` / `ma_dist_*`…）| `factor-layer` |
| 交易级组合模拟器 + 策略 + 统计 | `backtest` |

而**两层互不 import 是仓库铁律**（规格 §3.0b 的代码级审计）。
⇒ 组装必须发生在**最上面** —— 这是**组装点**，不是某一层的一部分。
铁律管的是**层与层**之间，组装本来就该在这儿。

## 用法

```bash
# 用 factor-layer 的 venv 跑（它装了 pandas / numpy）
factor-layer/.venv/bin/python run_tugboat.py                 # 默认：入场① + 四阶段曝险
factor-layer/.venv/bin/python run_tugboat.py --entry-mode pullback
factor-layer/.venv/bin/python run_tugboat.py --vcp           # 变体：只挑 VCP 形态
factor-layer/.venv/bin/python run_tugboat.py --no-exposure   # 关掉四阶段（做对照）
```

## ⚠️ 它**必须**打印出来的三件事（否则结果没法读）

1. **用的是哪一天的票池**（`stocks()` 静默返回 1 只的坑）
2. **起点是哪天**（`near_52w_high` 要 250 天预热，选错起点会吃掉大半个窗口）
3. **成本率**（判据③直接受它影响）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "factor-layer"))
sys.path.insert(0, str(ROOT / "backtest"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import backtest_config  # noqa: E402
import factor.implementations  # noqa: E402
import trade_metrics  # noqa: E402
from factor.factor_registry import run_factor  # noqa: E402
from panel.panel_builder import read_panel  # noqa: E402
from panel.provide_reader import read_days, read_stocks  # noqa: E402
from strategies.tugboat_breakout import (  # noqa: E402
    ENTER_MODES, REQUIRED_FACTORS, TugboatBreakout, TugboatExposure,
)
from trade_simulator import ExitPolicy, StaticExposure, simulate  # noqa: E402

#: 票池跳到 287 只那天 —— 比它更早的日子只有 16 只，构不成截面。
DEFAULT_START = "2022-05-03"


def _pick_stock_day(days: list[str], requested: str | None) -> str:
    """取一个**真有快照**的交易日（否则 `stocks()` 会静默返回 1 只）。"""
    for day in reversed(days if requested is None else [requested]):
        try:
            got = read_stocks(day)["stocks"]
        except Exception:  # noqa: BLE001
            continue
        if len(got) > 20:
            return day
    raise SystemExit(f"找不到有像样票池的交易日（试过 {requested or days[-1]}）")


def _market_state(panel, spy_panel) -> pd.DataFrame:
    """按日的市场状态（**四阶段曝险的输入**，见规格 §11.1 的 A2/A3）。

    两条代理（⚠️ 情绪维度 NAAIM/AAII/COT **无数据**，只能用这个）：

    | 列 | 含义 | 出处 |
    |---|---|---|
    | `breadth` | 票池里 **`close > SMA50` 的比例** | 他 §7.3 条件④ 原话「50 日线以上比例 < 20% ⇒ 可能反转」|
    | `index_dist_200ma` | **SPY 距 200 日线的偏离** | 他「大环境：大盘在 30 周均线之上」的连续版 |
    """
    close = panel.field("close")
    above = close > close.rolling(50).mean()
    breadth = above.sum(axis=1) / above.notna().sum(axis=1).replace(0, np.nan)

    spy_close = spy_panel.field("close")["SPY"]
    dist = spy_close / spy_close.rolling(200).mean() - 1.0

    return pd.DataFrame({
        "breadth": breadth.reindex(panel.dates),
        "index_dist_200ma": dist.reindex(panel.dates),
    })


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tugboat 突破交易 2.0 回测")
    ap.add_argument("--entry-mode", choices=ENTER_MODES, default="breakout",
                    help="他的三种入场（默认 breakout = 入场①）")
    ap.add_argument("--vcp", action="store_true",
                    help="变体：只挑 VCP 那一个形态（§7.1 六要点）")
    ap.add_argument("--no-exposure", action="store_true",
                    help="关掉四阶段曝险（做对照用）")
    ap.add_argument("--start", default=DEFAULT_START, help="起点（默认票池成型那天）")
    ap.add_argument("--stocks-day", default=None, help="票池快照日（默认自动找）")
    ap.add_argument("--iterations", type=int, default=2000, help="蒙特卡洛次数")
    ap.add_argument("--out", default=None, help="把报告写到文件")
    args = ap.parse_args(argv)

    days_all = read_days()
    stock_day = _pick_stock_day(days_all, args.stocks_day)
    symbols = read_stocks(stock_day)["stocks"]
    window = [d for d in days_all if d >= args.start]
    if not window:
        raise SystemExit(f"起点 {args.start} 之后没有交易日")

    print(f"票池日 : {stock_day}（{len(symbols)} 只）"
          f"{'  ← 自动选的（避免 stocks() 静默返回 1 只）' if args.stocks_day is None else ''}")
    print(f"窗口   : {window[0]} → {window[-1]}（{len(window)} 天）")
    print(f"成本   : 单边 {backtest_config.COST_RATE:.5f}"
          f"（往返 ×2 = {backtest_config.COST_RATE * 2:.5f}）")
    print(f"入场   : {args.entry_mode}｜VCP 过滤: {args.vcp}｜"
          f"四阶段曝险: {not args.no_exposure}")
    print()

    panel = read_panel(symbols, days=window, stocks_day=stock_day)
    spy_panel = read_panel(["SPY"], days=window, stocks_day=stock_day)
    print(f"面板   : {len(panel.dates)} 天 × {len(panel.symbols)} 只")

    strategy = TugboatBreakout(entry_mode=args.entry_mode, vcp_filter=args.vcp)
    factors = {n: run_factor(n, panel).values for n in REQUIRED_FACTORS}

    funnel = strategy.diagnose(panel, factors)
    steps = list(funnel.items())
    print(f"漏斗   : {steps[0][1]:,} → " +
          " → ".join(f"{v:,}" for _k, v in steps[1:]))
    print()

    cand = strategy.candidates(panel, factors)
    print(f"候选   : {len(cand)} 个"
          f"（日均 {len(cand) / len(panel.dates):.2f}，"
          f"有信号 {cand['day'].nunique() if len(cand) else 0} 天）")
    if cand.empty:
        print("\n⚠️ 没有任何候选 —— 先别读结论，去看漏斗卡在哪一条。")
        return 1

    bars = {k: panel.field(k) for k in ("open", "high", "low", "close")}
    ma_exit = strategy.ma_exit_level(panel, factors)

    exposure = StaticExposure()
    if not args.no_exposure:
        exposure = TugboatExposure()
        exposure.attach_market_state(_market_state(panel, spy_panel))

    from trade_simulator import AccountPolicy
    result = simulate(
        panel.dates, panel.symbols, bars, cand,
        strategy_name=strategy.name, strategy_params=strategy.params,
        ma_exit_level=ma_exit, exit_policy=strategy.exit_policy,
        account=AccountPolicy(cost_rate=backtest_config.COST_RATE),
        exposure=exposure,
    )

    spy_close = spy_panel.field("close")["SPY"].reindex(panel.dates)
    bench = (spy_close / spy_close.shift(1) - 1.0).to_numpy()[1:]

    report = trade_metrics.summarize(result, benchmark=bench)
    mc = trade_metrics.monte_carlo(result, iterations=args.iterations)
    footer = (
        "\n⚠️ 签三个已经显形的偏差（规格 §11.4）：\n"
        "  ① 催化剂/叙事**测不了** —— 那是他称「最核心」的筛选条件 ⇒ 对他不利\n"
        "  ② 日内入场**测不了**（无分钟数据）⇒ 入场与止损都用日线近似\n"
        "  ③ 四阶段的市场状态只能用「票池宽度 + 指数偏离」代理（情绪无数据）\n"
        "\n⚠️ 检出下限：4.4 年样本只能证明 Sharpe ≥ 1.96/√4.4 ≈ 0.93 的策略\n"
        "    ⇒ 中等优势**测不出来**，「说不清」不等于「没优势」（规格 §0.5）\n"
    )
    text = trade_metrics.render_report(
        report, mc, title=f"Tugboat 突破交易 2.0 · 入场={args.entry_mode} · "
                          f"VCP={args.vcp} · 四阶段={not args.no_exposure}") + footer
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n（已写入 {args.out}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
