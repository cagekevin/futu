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
from panel.provide_reader import read_days, read_snapshot, read_stocks  # noqa: E402
from strategies.tugboat_breakout import (  # noqa: E402
    DEFAULTS, ENTER_MODES, REQUIRED_FACTORS, TugboatBreakout, TugboatExposure,
)
from trade_simulator import (  # noqa: E402
    ExitPolicy, StaticExposure, reconcile, simulate,
)

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
                    "股数", "R倍数", "收益率", "出场原因", "持有天数",
                    "是否部分止盈", "部分止盈价", "部分止盈股数"])
        for t in result.trades:
            w.writerow([t.symbol, t.entry_day, f"{t.entry_price:.4f}",
                        f"{t.initial_stop:.4f}", t.exit_day, f"{t.exit_price:.4f}",
                        f"{t.shares:.2f}", f"{t.r_multiple:.4f}",
                        f"{t.return_pct:.6f}", t.exit_reason, t.hold_days,
                        int(t.took_partial),
                        "" if not np.isfinite(t.partial_price)
                        else f"{t.partial_price:.4f}",
                        f"{t.partial_shares:.2f}"])
    print(f"逐笔清单: {path}（{len(result.trades)} 笔）")


def _attach_labels(trades, cand: pd.DataFrame, panel, stocks_day: str) -> pd.DataFrame:
    """给每笔交易贴上**信号日 / 形态 / 行业**。

    ⚠️ 两个坑：
    1. **`Trade.entry_day` 是成交日，不是信号日**（市价单晚一天、限价单可能晚两天）
       ⇒ 用"同标的、信号日 < 成交日、且最近的那一条"去匹配，**不能直接按日相等 join**。
    2. **行业只有快照那 1–2 天有** ⇒ 只能拿**今天**的行业贴到四年前的交易上。
       对"半导体/软件"这种**不怎么变的**够用；对**改过主业**的会错。
       ⇒ **这条必须显形**（下面打印时会写）。
    """
    if not trades:
        return pd.DataFrame()
    # 信号日 → 该日各标的的形态
    form_of = {(r.day, r.symbol): r.form for r in cand.itertuples(index=False)}
    order = {d: i for i, d in enumerate(panel.dates)}
    rows = []
    for t in trades:
        ei = order.get(t.entry_day)
        form = ""
        if ei is not None:                       # 往回找最近的一条同标的候选
            for k in range(ei - 1, max(ei - 6, -1), -1):
                if (panel.dates[k], t.symbol) in form_of:
                    form = form_of[(panel.dates[k], t.symbol)]
                    break
        rows.append({"symbol": t.symbol, "entry_day": t.entry_day,
                     "form": form, "r": t.r_multiple,
                     "reason": t.exit_reason, "hold": t.hold_days,
                     "win": t.r_multiple > 0})
    out = pd.DataFrame(rows)
    try:
        snap = read_snapshot(stocks_day)["value"]["rows"]
        ind = {r["symbol"]: (r.get("industry") or "?") for r in snap}
    except Exception:                            # noqa: BLE001
        ind = {}
    out["industry"] = out["symbol"].map(ind).fillna("?")
    return out


def _group_table(df: pd.DataFrame, by: str, *, top: int = 12) -> list[str]:
    """分组统计：笔数 / 胜率 / 平均 R / 总 R。**样本小的组必须显形**。"""
    lines = [f"  {'分组':26s}{'笔数':>6s}{'胜率':>8s}{'平均R':>9s}{'总R':>9s}{'占比':>8s}"]
    total_r = float(df["r"].sum())
    g = (df.groupby(by)
           .agg(n=("r", "size"), win=("win", "mean"),
                avg=("r", "mean"), tot=("r", "sum"))
           .sort_values("tot", ascending=False))
    for name, row in g.head(top).iterrows():
        flag = "  ⚠️样本小" if row["n"] < 10 else ""
        lines.append(
            f"  {str(name)[:26]:26s}{int(row['n']):>6d}{row['win'] * 100:>7.1f}%"
            f"{row['avg']:>9.3f}{row['tot']:>9.1f}"
            f"{(row['tot'] / total_r * 100 if total_r else 0):>7.1f}%{flag}")
    if len(g) > top:
        lines.append(f"  （还有 {len(g) - top} 组未列出）")
    return lines


def _yearly_table(result, spy_close) -> str:
    """★ **逐年**：策略 vs SPY。

    为什么把它做成**默认输出**：这是最能把"策略有本事"和"那一年行情好"
    分开看的一栏 —— 也最能暴露「**近期持续性弱**」。
    """
    eq = np.asarray(result.equity_values, dtype=float)
    days = list(result.equity_days)
    spy = spy_close.to_numpy(dtype=float)
    out = [f"  {'年份':8s}{'策略':>10s}{'基准(SPY)':>11s}{'笔数':>6s}"
           f"{'胜率':>8s}{'总R':>9s}{'年内最大回撤':>13s}"]
    for y in sorted({d[:4] for d in days}):
        idx = [i for i, d in enumerate(days) if d.startswith(y)]
        if len(idx) < 2:
            continue
        i0, i1 = idx[0], idx[-1]
        strat = eq[i1] / eq[i0] - 1.0
        base = float("nan")
        if np.isfinite(spy[i0]) and np.isfinite(spy[i1]) and spy[i0] > 0:
            base = spy[i1] / spy[i0] - 1.0
        seg = eq[i0:i1 + 1]
        dd = float((seg / np.maximum.accumulate(seg) - 1.0).min())
        ty = [t for t in result.trades if t.exit_day.startswith(y)]
        rs = np.array([t.r_multiple for t in ty], dtype=float)
        out.append(
            f"  {y:8s}{strat * 100:>9.2f}%{base * 100:>10.2f}%{len(ty):>6d}"
            f"{(rs > 0).mean() * 100 if rs.size else float('nan'):>7.1f}%"
            f"{rs.sum():>9.1f}{dd * 100:>12.2f}%")
    return "\n".join(out)


def _hold_table(result) -> str:
    """★ **按持有天数** —— 这条最能说明"利润从哪来"。"""
    trades = result.trades
    rs = np.array([t.r_multiple for t in trades], dtype=float)
    holds = np.array([t.hold_days for t in trades], dtype=float)
    out = [f"  {'持有':10s}{'笔数':>6s}{'胜率':>8s}{'平均R':>9s}{'总R':>9s}"]
    for lo, hi, label in ((-1, 2, "≤2 天"), (2, 5, "3–5 天"), (5, 10, "6–10 天"),
                          (10, 20, "11–20 天"), (20, 10 ** 9, ">20 天")):
        m = (holds > lo) & (holds <= hi)
        if not m.any():
            continue
        out.append(f"  {label:10s}{int(m.sum()):>6d}{float((rs[m] > 0).mean()) * 100:>7.1f}%"
                   f"{float(rs[m].mean()):>9.3f}{float(rs[m].sum()):>9.1f}")
    return "\n".join(out)


def _render_curve(result, bench_rets, spy_close) -> str:
    """净值曲线（按季取样）+ R 倍数分布（逐年 / 持有期已进默认报告）。"""
    eq = np.asarray(result.equity_values, dtype=float)
    days = list(result.equity_days)
    out: list[str] = []

    # ── 净值曲线（按季取样，画成柱状）──
    out.append("")
    out.append("  净值曲线（每季末，柱长按当季末净值 / 初始资金 − 1）")
    q_idx = [i for i, d in enumerate(days)
             if i == len(days) - 1 or (d[5:7] in ("03", "06", "09", "12")
                                       and days[i + 1][5:7] != d[5:7])]
    base0 = eq[0] if eq[0] else 1.0
    for i in q_idx:
        rel = eq[i] / base0 - 1.0
        bar = "█" * max(0, int(round(rel * 40))) if rel >= 0 else ""
        bar = ("·" * max(0, int(round(-rel * 40))) + "█") if rel < 0 else bar
        out.append(f"  {days[i]}  {eq[i] / 1e6:6.3f}M  {rel * 100:+7.2f}%  {bar}")

    # ── R 倍数分布（直方图）──
    trades = result.trades
    rs = np.array([t.r_multiple for t in trades], dtype=float)
    out.append("")
    out.append("  R 倍数分布（每笔）")
    bins = [(-1e9, -2), (-2, -1.5), (-1.5, -1), (-1, -0.5), (-0.5, 0),
            (0, 0.5), (0.5, 1), (1, 2), (2, 3), (3, 5), (5, 1e9)]
    for lo, hi in bins:
        n = int(((rs > lo) & (rs <= hi)).sum())
        label = (f"≤{hi:.0f}" if lo < -1e8 else f"{lo:.1f} ~ {hi:.0f}"
                 if hi < 1e8 else f">{lo:.0f}")
        out.append(f"  {label:>12s}  {n:>4d}  {'▇' * min(n, 60)}")
    out.append(f"  {'合计':>12s}  {rs.size:>4d}｜平均 {rs.mean():+.3f}R｜"
               f"中位 {np.median(rs):+.3f}R｜最好 {rs.max():+.2f}R｜最差 {rs.min():+.2f}R")
    return "\n".join(out)


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
    ap.add_argument("--breakdown", action="store_true",
                    help="★ 把成交按 形态 / 行业 / 出场原因 / 持有期 分组，看钱是哪类挣的")
    ap.add_argument("--matrix", action="store_true",
                    help="★ 把 入场×曝险×VCP 的组合**全跑完**（一次不留尾巴）")
    ap.add_argument("--curve", action="store_true",
                    help="★ 净值曲线 + 逐年表现 + R 倍数分布")
    ap.add_argument("--free-params", action="store_true",
                    help="★★ **验我自创的近似**（入场时点 / 止损位 / 6 个阈值）会不会翻掉结论")
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

    # ★ **每次跑都自检** —— 记账错不会报错，只会让所有报告数字安静地错掉。
    #   独立公式逐笔重算 R，不符就**直接退出**（不给你一份错数）。
    chk = reconcile(result, backtest_config.COST_RATE)
    if chk["bad"]:
        print(f"\n⛔ 逐笔对账失败：{chk['bad']} / {chk['n']} 笔与独立重算不符"
              f"（最大差 {chk['max_abs_diff']:.3e}）")
        for line in chk["examples"]:
            print("   ", line)
        print("   ⇒ 记账有错，报告不可信 —— 已中止。")
        return 2
    print(f"自检   : {chk['n']} 笔逐笔对账通过（含 {chk['n_partial']} 笔部分止盈，"
          f"最大差 {chk['max_abs_diff']:.1e}）")
    print()

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

    # ★ **默认就出**这两张表 —— 它们最能把问题暴露出来，不该藏在开关后面
    print()
    print("  ── ★ 逐年（策略 vs SPY）──")
    print(_yearly_table(result, spy_close))
    print()
    print("  ── ★ 按持有天数（利润从哪来）──")
    print(_hold_table(result))
    print()
    print("  ⚠️ 逐年看很重要：某一年特别赚，往往说明那一年行情适合这类策略，")
    print("     而不是策略本身强。**若最近一两年为负，那是最该警惕的信号。**")

    if args.trades:
        _dump_trades(result, args.trades)

    if args.free_params:
        # ★★ **这个测试才有意义**：验"我自创的近似"会不会把结论翻掉。
        #
        # 他的资料只给方向（"够紧""靠近支撑""不要太远"），**没给数字**；
        # 他的入场是**日内**，而我们只有日线。⇒ 这些**全是我的选择**。
        # 若结论对它们敏感 ⇒ 那结论是**关于我的近似**，不是关于**他的系统**。
        from dataclasses import replace as _replace

        def row(label: str, sp: dict, *, on_close: bool = False,
                cap: int | None = None) -> None:
            st = TugboatBreakout(**{**base_over, **sp})
            c = st.candidates(panel, factors)
            if c.empty:
                print(f"  {label:34s}       —— 候选 0")
                return
            ac = _replace(account, trade_on_close=on_close,
                          **({"max_positions": cap} if cap else {}))
            rr = simulate(panel.dates, panel.symbols, bars, c,
                          strategy_name=st.name, strategy_params=st.params,
                          ma_exit_level=st.ma_exit_level(panel, factors),
                          exit_policy=st.exit_policy, account=ac, exposure=exposure)
            mm = trade_metrics.summarize(rr, benchmark=bench)
            print(f"  {label:34s}{len(c):>6d}{mm['n_trades']:>6d}"
                  f"{mm['total_return'] * 100:>9.2f}%{mm['sharpe']:>9.2f}"
                  f"{mm['max_drawdown'] * 100:>9.2f}%{mm['expectancy_r']:>9.3f}"
                  f"{mm['ir_stripped']:>9.2f}")

        base_over = {"entry_mode": args.entry_mode, "vcp_filter": args.vcp}
        print()
        print("═" * 78)
        print("★ 我自创的近似 —— 换一换，结论会不会翻？")
        print("─" * 78)
        print(f"  {'情形':34s}{'候选':>6s}{'笔数':>6s}{'总收益':>10s}"
              f"{'Sharpe':>9s}{'MDD':>10s}{'每笔R':>9s}{'IR剥离':>9s}")
        row("【基准】我的默认", {})

        print("\n  ① **入场时点**（他做日内，我们只有日线）")
        row("次日开盘（我的默认）", {})
        row("当日收盘（更接近他的日内）", {}, on_close=True)

        print("\n  ② **止损位**（他给了四个候选，我挑了一个）")
        for b, lab in (("breakout_low", "突破那根 K 线的 low（我的默认）"),
                       ("prior_low", "前一日 low"),
                       ("range_bottom", "区间底部"),
                       ("range_mid", "区间中部（原文没有，上界）")):
            row(lab, {"stop_basis": b})

        print("\n  ③ **6 个选股阈值**（他**只给方向、没给数字**）")
        for key, vals in (("tight_range_max", (0.04, 0.06, 0.09)),
                          ("ma_converge_max", (0.01, 0.02, 0.04)),
                          ("near_ma_max_atr", (0.5, 1.0, 2.0)),
                          ("rise_12m_min", (0.30, 0.50, 0.80)),
                          ("no_dump_adr", (1.5, 2.0, 3.0)),
                          ("overextend_max", (0.10, 0.15, 0.25))):
            for v in vals:
                mark = "  ←默认" if abs(v - DEFAULTS[key]) < 1e-12 else ""
                row(f"{key} = {v}{mark}", {key: v})

        print("─" * 78)
        print("  ★ 怎么读这张表：")
        print("     · 若**换一换就翻**（正负/量级大变）⇒ 结论是**关于我的近似**的，")
        print("       不是关于**他的系统**的 ⇒ 那个负结论**不能算数**。")
        print("     · 若**换一换都差不多**（都在 0 附近、IR 剥离都为负）⇒")
        print("       结论**稳健** ⇒ 可以归因到系统本身。")
        return 0

    if args.curve:
        print()
        print("═" * 70)
        print("★ 净值曲线 / 逐年 / R 分布")
        print("─" * 70)
        print(_render_curve(result, bench, spy_close))
        print("─" * 70)
        print("  ⚠️ **逐年看**很重要：某一年特别赚，往往说明那一年行情适合这类策略，")
        print("     而不是策略本身强（`qsx` 的「近期持续性」告警就是查这个）。")
        return 0

    if args.matrix:
        from dataclasses import replace as _replace

        print()
        print("══ 全组合矩阵（入场 × 曝险 × VCP）══")
        print(f"  {'组合':40s}{'候选':>6s}{'笔数':>6s}{'总收益':>10s}"
              f"{'Sharpe':>9s}{'MDD':>10s}{'每笔R':>9s}{'IR剥离':>9s}")
        for mode in ENTER_MODES:
            for vcp in (False, True):
                for exp_on in (True, False):
                    st = TugboatBreakout(entry_mode=mode, vcp_filter=vcp)
                    c = st.candidates(panel, factors)
                    tag = (f"{mode}｜VCP={'开' if vcp else '关'}｜"
                           f"四阶段={'开' if exp_on else '关'}")
                    if c.empty:
                        print(f"  {tag:40s}{0:>6d}      —— 候选 0")
                        continue
                    ex = exposure if exp_on else StaticExposure()
                    rr = simulate(
                        panel.dates, panel.symbols, bars, c,
                        strategy_name=st.name, strategy_params=st.params,
                        ma_exit_level=st.ma_exit_level(panel, factors),
                        exit_policy=st.exit_policy, account=account, exposure=ex)
                    mm = trade_metrics.summarize(rr, benchmark=bench)
                    print(f"  {tag:40s}{len(c):>6d}{mm['n_trades']:>6d}"
                          f"{mm['total_return'] * 100:>9.2f}%{mm['sharpe']:>9.2f}"
                          f"{mm['max_drawdown'] * 100:>9.2f}%"
                          f"{mm['expectancy_r']:>9.3f}{mm['ir_stripped']:>9.2f}")
        print("  ⚠️ 这是**同一份数据上的多个变体** —— 按预注册纪律，")
        print("     若要挑一个「最好」的，p 值必须**一起过 BH 校正**。")
        print("  ⚠️ 候选 0 的组合 = **该变体在本票池/本时段没出现过**，不是「测出来无效」。")
        return 0

    if args.breakdown:
        lab = _attach_labels(result.trades, cand, panel, stock_day)
        if lab.empty:
            print("\n（没有成交，分组无意义）")
            return 0
        print()
        print("═" * 70)
        print("★ 分组：钱是哪一类挣的")
        print("─" * 70)
        for by, title in (("form", "按**形态**（他 §6.1 的三种）"),
                          ("industry", "按**行业**"),
                          ("reason", "按**出场原因**"),
                          ("hold_bucket", "按**持有天数**")):
            if by == "hold_bucket":
                lab["hold_bucket"] = pd.cut(
                    lab["hold"], bins=[-1, 2, 5, 10, 20, 10 ** 6],
                    labels=["≤2 天", "3–5 天", "6–10 天", "11–20 天", ">20 天"])
            print(f"\n{title}：")
            print("\n".join(_group_table(lab, by)))
        print()
        print("─" * 70)
        print("  ⚠️ **这是事后切片（exploratory），不是预先注册的检验** ——")
        print("     分组一多，总有一组「看起来最赚」。它只能用来**生成假设**，")
        print("     不能当成「这一类有优势」的证据。要当真，得**另开一次预注册实验**。")
        print("  ⚠️ 行业来自**今天的快照**，贴到四年前的成交上 ——")
        print("     「半导体 / 软件」这种够用，**改过主业的会贴错**。")
        return 0

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
