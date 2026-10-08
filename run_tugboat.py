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


def _load_entries(path: str, panel) -> pd.DataFrame:
    """读**你自己的入场清单**（CSV：`day,symbol`，可选 `stop_price`）。

    ## 为什么要有这个入口

    你要测的不是"机器会不会选股"，而是「**给定你选的入场，他那套框架有没有用**」。
    ⇒ 入场必须是**你的**，机器只负责套框架。

    · `day` —— **信号日**（那天收盘你决定要买）；成交在**次日开盘**。
    · `symbol` —— 标的。
    · `stop_price` —— 不给就按**他的规则**算：那根 K 线的低点（他第一个候选）。
    """
    import csv as _csv

    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in _csv.DictReader(fh):
            day = (r.get("day") or r.get("date") or "").strip()
            sym = (r.get("symbol") or r.get("ticker") or "").strip().upper()
            if not day or not sym:
                continue
            raw_stop = (r.get("stop_price") or "").strip()
            rows.append((day, sym, float(raw_stop) if raw_stop else None))
    if not rows:
        raise SystemExit(f"{path} 里没有可用的行（需要列 day,symbol[,stop_price]）")

    low = panel.field("low")
    known = {d: i for i, d in enumerate(panel.dates)}
    out, dropped = [], 0
    for day, sym, stop in rows:
        if day not in known or sym not in low.columns:
            dropped += 1
            continue
        s = stop if stop is not None else float(low.loc[day, sym])
        if not np.isfinite(s):
            dropped += 1
            continue
        out.append({"day": day, "symbol": sym, "stop_price": s,
                    "form": "user", "limit_price": None, "valid_days": 1})
    print(f"入场清单: {path} → 用上 {len(out)} 条"
          f"{f'（丢掉 {dropped} 条：不在面板里 / 无止损）' if dropped else ''}")
    return pd.DataFrame(out)


def _no_framework_arm(exit_policy: ExitPolicy, hold_days: int) -> ExitPolicy:
    """对照臂：**同样的股数**，但**只按固定天数持有**，不要那套出场规则。

    ★ 唯一变化的就是"有没有框架"（承规格 §6 的对照设计）：

    | | 有框架 | 无框架（本臂）|
    |---|---|---|
    | 股数 | 1R ÷ 止损距离 | **完全相同** |
    | 止损 | 按止损价出 | **不止损** |
    | 止盈 | 3R 减半 + 移到保本 | **不止盈** |
    | 均线 | 跌破 10/20EMA 清仓 | **不看** |
    | 持有 | 5 天无进展 / 60 天上限 | **固定 `hold_days` 天** |

    `hold_days` 取**有框架那一臂的平均持有天数**（四舍五入）——
    否则两边持有期不同，比出来的是"持有期"的差，不是"框架"的差。
    """
    from dataclasses import replace
    return replace(exit_policy, use_stop=False, use_ma_exit=False,
                   target_r=1e9, partial_fraction=0.0,
                   breakeven_after_partial=False,
                   no_progress_days=10 ** 9, no_progress_min_r=-1e9,
                   partial_after_days=None, max_hold_days=max(1, int(hold_days)))


def _dump_trades(result, path: str) -> None:
    """把**逐笔交易**写成 CSV —— 这是"他到底在做什么"最直接的东西。"""
    import csv as _csv

    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["标的", "入场日", "入场价", "止损", "出场日", "出场价",
                    "股数", "R倍数", "收益率", "出场原因", "持有天数"])
        for t in result.trades:
            w.writerow([t.symbol, t.entry_day, f"{t.entry_price:.4f}",
                        f"{t.initial_stop:.4f}", t.exit_day, f"{t.exit_price:.4f}",
                        f"{t.shares:.2f}", f"{t.r_multiple:.4f}",
                        f"{t.return_pct:.6f}", t.exit_reason, t.hold_days])
    print(f"逐笔清单: {path}（{len(result.trades)} 笔）")


def sensitivity(base_params: dict, hold: int) -> list[tuple[str, dict, dict, int]]:
    """要扫的参数（**一次只动一个**）—— 回答"哪个参数最要紧"。

    返回 `(标签, 策略参数覆盖, 出场规则覆盖, 持仓上限)`。
    """
    out: list[tuple[str, dict, dict, int]] = []
    for v in (1.5, 2.0, 3.0, None):
        out.append((f"止损宽度上限={v or '不限'}×ADR", {"max_stop_adr": v}, {}, 5))
    for v in (2.0, 3.0, 4.0, 6.0):
        out.append((f"止盈={v}R 减半", {}, {"target_r": v}, 5))
    for v in (0.0, 0.33, 0.5, 1.0):
        out.append((f"部分止盈比例={v:.2f}",
                    {}, {"partial_fraction": v,
                         "breakeven_after_partial": v > 0}, 5))
    for v in (3, 5, 8):
        out.append((f"最多同时持仓={v} 笔", {}, {}, v))
    for v in (3, 5, 10, 10 ** 9):
        out.append((f"5 天无进展离场：{'关' if v > 100 else f'{v} 天'}",
                    {}, {"no_progress_days": v}, 5))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tugboat 突破交易 2.0 回测")
    ap.add_argument("--entry-mode", choices=ENTER_MODES, default="breakout",
                    help="他的三种入场（默认 breakout = 入场①）")
    ap.add_argument("--entries", default=None,
                    help="★ **你自己的入场清单**（CSV: day,symbol[,stop_price]）"
                         "—— 给了它就不用内置扫描器")
    ap.add_argument("--compare", action="store_true",
                    help="★ 跑两臂对照：**有框架 vs 无框架**（同一批入场、同样股数）")
    ap.add_argument("--trades", default=None,
                    help="把**逐笔交易清单**写成 CSV（给这个路径）")
    ap.add_argument("--sensitivity", action="store_true",
                    help="★ 一次只动一个参数，看**哪个最要紧**")
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

    if args.entries:
        # ★ 用**你自己的入场** —— 机器不选股，只套框架
        cand = _load_entries(args.entries, panel)
    else:
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
    account = AccountPolicy(cost_rate=backtest_config.COST_RATE)
    result = simulate(
        panel.dates, panel.symbols, bars, cand,
        strategy_name=strategy.name, strategy_params=strategy.params,
        ma_exit_level=ma_exit, exit_policy=strategy.exit_policy,
        account=account,
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

    if args.trades:
        _dump_trades(result, args.trades)

    if args.sensitivity:
        # ★ 一次只动一个参数 ⇒ 回答「**哪个参数最要紧**」
        from dataclasses import replace as _replace

        base_over = {"entry_mode": args.entry_mode, "vcp_filter": args.vcp}
        hold_days = (int(round(report["avg_hold_days"]))
                     if np.isfinite(report["avg_hold_days"]) else 10)
        print()
        print("══ 敏感性：一次只动一个参数（其余固定）══")
        print(f"  {'':30s}{'笔数':>6s}{'总收益':>10s}{'Sharpe':>9s}"
              f"{'MDD':>10s}{'每笔R':>9s}")
        base_line = (f"{'【基准】原样':30s}{report['n_trades']:>6d}"
                     f"{report['total_return'] * 100:>9.2f}%{report['sharpe']:>9.2f}"
                     f"{report['max_drawdown'] * 100:>9.2f}%"
                     f"{report['expectancy_r']:>9.3f}")
        print("  " + base_line)
        for label, sparams, eparams, cap in sensitivity(strategy.params, hold_days):
            st = TugboatBreakout(**{**base_over, **sparams})
            try:
                c = st.candidates(panel, factors)
            except Exception as exc:  # noqa: BLE001
                print(f"  {label:30s} 跳过（{exc}）")
                continue
            if c.empty:
                print(f"  {label:30s} 候选 0")
                continue
            r = simulate(
                panel.dates, panel.symbols, bars, c,
                strategy_name=st.name, strategy_params=st.params,
                ma_exit_level=st.ma_exit_level(panel, factors),
                exit_policy=_replace(st.exit_policy, **eparams),
                account=_replace(account, max_positions=cap),
                exposure=exposure)
            m = trade_metrics.summarize(r, benchmark=bench)
            print(f"  {label:30s}{m['n_trades']:>6d}"
                  f"{m['total_return'] * 100:>9.2f}%{m['sharpe']:>9.2f}"
                  f"{m['max_drawdown'] * 100:>9.2f}%{m['expectancy_r']:>9.3f}")
        print("  ⚠️ 一次只动一个 ⇒ 看到的是**该参数单独的**影响，"
              "不是你同时改几个的效果")

    if args.compare:
        hold = int(round(report["avg_hold_days"])) if np.isfinite(
            report["avg_hold_days"]) else 10
        bare_ep = _no_framework_arm(strategy.exit_policy, hold)

        def arm(exit_policy, max_positions: int):
            a = AccountPolicy(cost_rate=backtest_config.COST_RATE,
                              max_positions=max_positions)
            r = simulate(panel.dates, panel.symbols, bars, cand,
                         strategy_name=strategy.name, strategy_params={},
                         ma_exit_level=ma_exit, exit_policy=exit_policy,
                         account=a, exposure=exposure)
            return trade_metrics.summarize(r, benchmark=bench)

        def p(x: float, nd: int = 2) -> str:
            return "n/a" if not np.isfinite(x) else f"{x * 100:.{nd}f}%"

        rows = (("笔数", "n_trades", lambda x: f"{int(x)}"),
                ("总收益", "total_return", p),
                ("年化", "cagr", p),
                ("年化波动", "ann_vol", p),
                ("Sharpe", "sharpe", lambda x: f"{x:.2f}"),
                ("★ 最大回撤", "max_drawdown", p),
                ("胜率", "win_rate", lambda x: f"{x * 100:.1f}%"),
                ("每笔平均 R", "expectancy_r", lambda x: f"{x:.3f}"))

        r1 = simulate(panel.dates, panel.symbols, bars, cand,
                      strategy_name=strategy.name, strategy_params={},
                      ma_exit_level=ma_exit, exit_policy=strategy.exit_policy,
                      account=account, exposure=exposure)
        r2 = simulate(panel.dates, panel.symbols, bars, cand,
                      strategy_name=strategy.name + "·无框架", strategy_params={},
                      ma_exit_level=ma_exit, exit_policy=bare_ep,
                      account=account, exposure=exposure)
        m1, m2 = (trade_metrics.summarize(r1, benchmark=bench),
                  trade_metrics.summarize(r2, benchmark=bench))

        print()
        print("═" * 66)
        print("★ 两臂对照 —— **同一批入场、同样的股数**，唯一差别是出场规则")
        print(f"   入场 {len(cand)} 条（来源：{args.entries or '内置扫描器'}）")
        print("   有框架 = 止损 + 3R 减半 + 跌破均线清仓 + 5 天无进展 + 60 天上限")
        print(f"   无框架 = 只按 **{hold} 天**收盘卖（= 有框架那臂的平均持有）")
        print("─" * 66)
        print(f"  {'':16s}{'有框架':>14s}{'无框架':>14s}{'差':>14s}")
        for label, key, fmt in rows:
            a, b = float(m1[key]), float(m2[key])
            d = "" if not (np.isfinite(a) and np.isfinite(b)) else fmt(a - b)
            print(f"  {label:16s}{fmt(a):>14s}{fmt(b):>14s}{d:>14s}")
        print("─" * 66)
        print("  ⚠️ 两臂**笔数可能不同**（出场规则会改变什么时候腾出持仓位）")
        print("     ⇒ 上面的差里**混进了「挑了哪几笔」**。下面是**配对**的做法：")

        # ★ 配对：同一笔交易（同标的 + 同入场日）在两臂里的 R 直接比
        key = lambda t: (t.symbol, t.entry_day)          # noqa: E731
        a1 = {key(t): t.r_multiple for t in r1.trades}
        a2 = {key(t): t.r_multiple for t in r2.trades}
        common = sorted(set(a1) & set(a2))
        if common:
            d = np.array([a1[k] - a2[k] for k in common], dtype=float)
            better = int((d > 0).sum())
            worse = int((d < 0).sum())
            t_stat = (d.mean() / (d.std(ddof=1) / np.sqrt(d.size))
                      if d.size > 1 and d.std(ddof=1) > 0 else float("nan"))
            print()
            print(f"  ── ★ 配对对比（{len(common)} 笔**两臂都做了**的交易）──")
            print(f"  平均 R 差（有框架 − 无框架）: **{d.mean():+.3f} R**"
                  f"｜中位 {np.median(d):+.3f}")
            print(f"  框架更好的笔数 {better}｜更差的 {worse}｜持平 {len(common) - better - worse}")
            print(f"  配对 t 值: {t_stat:.2f}"
                  f"（|t| > 2 才算像样；⚠️ 这 {len(common)} 笔**不独立**"
                  f"—— 同日多笔、连续重叠持有，真实显著性**比 t 值低**）")
        print()
        print("  ⚠️ '无框架'那一臂**没有止损** ⇒ 它的回撤**可以无限深**（正是框架要管的）")
        return 0
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n（已写入 {args.out}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
