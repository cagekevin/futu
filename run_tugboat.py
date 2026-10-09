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
import entry_quality  # noqa: E402
import units  # noqa: E402
import plateau  # noqa: E402
import random_control  # noqa: E402
import panel_statistics as _stats  # noqa: E402
import trade_metrics  # noqa: E402
import verdict  # noqa: E402
from factor.factor_registry import run_factor  # noqa: E402
from panel.panel_builder import read_panel  # noqa: E402
from panel.provide_reader import read_days, read_snapshot, read_stocks  # noqa: E402
from strategies.tugboat_breakout import (  # noqa: E402
    DEFAULTS, ENTER_MODES, REQUIRED_FACTORS, TugboatBreakout, TugboatExposure,
)
from trade_simulator import (  # noqa: E402
    ExitPolicy, StaticExposure, reconcile, reconcile_fills, simulate,
)

#: 票池跳到 287 只那天 —— 比它更早的日子只有 16 只，构不成截面。
DEFAULT_START = "2022-05-03"

#: 评估窗口之前**多吃多少个交易日**作因子预热。
#:
#: 最长窗口是 `ret260` / `near_52w_high`（250–260 天），再留一点余量。
#: ⚠️ 不吃这一段，预热就落在**窗口内部** ⇒ 前 ~13 个月没有信号。
#:
#: ★ **有效样本与检出下限不在这里写死** —— 由页脚按「**首个信号 → 末尾**」实算
#:   （`eff_years` 与 `1.96/√eff_years`）。这里**曾经**写死「3.4 年 / 1.06」，
#:   与结论文档的「1.04」对不上 ⇒ 治 TD-05-22：**口径数字只留一处**。
WARMUP_DAYS = 300


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


class _WindowedPanel:
    """把面板**切到评估窗口** —— 预热只用来喂因子，**不进评估**。

    ## 为什么要有这个包装（而不是在各处 `.loc[window]`）

    面板要多吃 `WARMUP_DAYS` 天（否则因子预热落在窗口内部，见那里的说明），
    但**下游每一个消费者**（策略 / 敏感性 / 矩阵 / 对照臂 / 模拟器）都只该看窗口。

    如果靠"每个调用点自己记得切"，那就必然有**某一个变体忘了切** ⇒ 分叉。
    ⇒ 用包装对象一次切好，下游拿到的是**同一个窗口**。

    只实现下游真正用到的那三个成员（`dates` / `symbols` / `field`）。
    """

    def __init__(self, panel, window) -> None:
        self._panel = panel
        self.dates = tuple(window)
        self.symbols = panel.symbols

    def field(self, name: str) -> pd.DataFrame:
        return self._panel.field(name).loc[list(self.dates)]


def _market_state(panel, spy_panel) -> pd.DataFrame:
    """按日的市场状态（**四阶段曝险的输入**，见规格 §11.1 的 A2/A3）。

    两条代理（⚠️ 情绪维度 NAAIM/AAII/COT **无数据**，只能用这个）：

    | 列 | 含义 | 出处 |
    |---|---|---|
    | `breadth` | 票池里 **`close > SMA50` 的比例** | 他 §7.3 条件④ 原话「50 日线以上比例 < 20% ⇒ 可能反转」|
    | `index_dist_200ma` | **SPY 距 200 日线的偏离** | 他「大环境：大盘在 30 周均线之上」的连续版 |

    ## ★★ 整条序列必须 `shift(1)` —— 这里曾经是**一天前视**

    曝险是在**当日开盘之前**定档的（模拟器日循环的**步骤 ⓪**），
    而它拿去放行的是**当日开盘**的入场单。

    ⇒ 所以状态只能用**截至昨天收盘**的值。第一版我按**当日 `close`** 算
      ⇒ **用今天收盘的宽度，去决定今天开盘下多少注** —— 真前视。

    （这个 bug 是**外部独立复审**抓到的：它跳出了"文档划定的检查范围"去查，
      而 `test_causality` 当时**根本不覆盖 Tugboat**，所以测试没挡住。）

    ⚠️ **实现上为什么用 `shift(1)` 而不是 `AtOpen`**：本函数是**整条序列一次性算好**
       再注入的（向量化）；`AtOpen` 那个对象是给"逐日循环里取数"用的。
       两者表达的是同一件事：**开盘前看不到今天**。
    """
    close = panel.field("close")
    above = close > close.rolling(50).mean()
    # ⚠️ 分母**必须是"当天真有数据的标的数"**。
    #    `above` 是**布尔表** ⇒ `above.notna()` **恒为真** ⇒ 分母恒等于列数，
    #    未上市/无数据的票会被算进"不在 50 日线上方" ⇒ **早期宽度被系统性低估**
    #    （更容易误判成"疑似见底"档）。第一版那句 `.replace(0, np.nan)` 是**死代码**。
    valid = close.notna() & close.rolling(50).mean().notna()
    valid_n = valid.sum(axis=1)
    # ★★ **有效标的数太少 ⇒ 宽度没有意义，取 NaN（中性），不是 0。**
    #
    #   为什么必须挡：`close > close.rolling(50).mean()` 在**冷启动期**是
    #   `NaN > NaN` = **`False`**（pandas 的语义，不是 NaN！）
    #   ⇒ 前 ~49 天 `breadth` 恒为 **0.0** ⇒ 被当成**真实的"疑似见底"**。
    #
    #   实测（复审给的数）：报告里「①疑似见底 **113 天**」中
    #   **49 天是冷启动假象** ⇒ **43% 是 bug**。
    #   （本次无害 —— 候选最早 2023-03-06，前 49 天 0 个候选；
    #    但**一旦拉长历史/扩大票池，每个窗口的头 49 天都会静默进档位 ①**。）
    min_valid = max(30, int(0.3 * len(panel.symbols)))
    breadth = (above.sum(axis=1) / valid_n.replace(0, np.nan)
               ).where(valid_n >= min_valid)

    spy_close = spy_panel.field("close")["SPY"]
    dist = spy_close / spy_close.rolling(200).mean() - 1.0

    # ── ★ §7.1「判断大市动能」的两个方法（治 TD-05-30）──────────────────────
    #   ① Stockbee Market Monitor：当日**升超 4% / 跌超 4%** 的个股数
    #      ⚠️ 原文用**绝对家数**（「跌超 4% 的股票 **> 300 个**」），而我们票池只有 **270 只**
    #      ⇒ 改用**占比之差** `net4`（显形：这是口径替换，不是照抄他的数字）。
    #   ② 标普 500 能否**维持在 20 日线之上**（我们原来用的是「距 **200** 日线偏离」——
    #      那是 §2.2「30 周均线」的代理，**不是** §7.1 的方法②）。
    #   ⚠️ 两者都**不含今天**（`pct_change` 用到今天收盘，`shift(1)` 在返回处统一做）。
    ret = close.pct_change()
    valid_ret = close.notna() & close.shift(1).notna()
    n_ret = valid_ret.sum(axis=1).replace(0, np.nan)
    net4 = ((ret > 0.04).sum(axis=1) / n_ret) - ((ret < -0.04).sum(axis=1) / n_ret)
    above20 = (spy_close > spy_close.rolling(20).mean()).astype(float)

    # ── ★ §6.1「止损结合 SA」用的**波动维度**（治 TD-05-39）────────────────
    #   原文（行 550）：「**市场开始变得波动**、动能开始下降 → **不要设得太窄**」。
    #   实现 = 票池**日振幅中位数**的 20 日均 ÷ 它的 20 天前值 ⇒ **`> 1` = 波动在扩张**。
    #   ⚠️ 它**不是** §7.1 的动能（那个管"做不做"）—— 用错输入会让"放宽档"**永远不可达**
    #      （门槛已经把动能差的日子整段排除了）。
    rng = (panel.field("high") - panel.field("low")) / close
    med = rng.median(axis=1)
    vol_ratio = med.rolling(20).mean() / med.rolling(20).mean().shift(20)

    # ★ **整条后移一天**：开盘前能看到的只有截至昨天的状态
    return pd.DataFrame({
        "breadth": breadth.reindex(panel.dates),
        "index_dist_200ma": dist.reindex(panel.dates),
        "net4": net4.reindex(panel.dates),
        "spy_above_20ma": above20.reindex(panel.dates),
        "vol_ratio": vol_ratio.reindex(panel.dates),
    }).shift(1)


def _replace_account(account, cost_rate):
    """换成本率重跑用的账户（成本是**执行假设**，不是策略参数）。"""
    from dataclasses import replace as _r
    return _r(account, cost_rate=cost_rate)


def cost_stress(run, *, base_cost_rate: float, benchmark,
                multiples=backtest_config.COST_STRESS_MULTIPLES) -> list[dict]:
    """★ **成本压力曲线**（承 V4：约束原文要求 **1/2/3/5×**）。

    ## 为什么是一条曲线，不是一个点
    V4 的**可验证标准**只要求「2x 成本下必须仍盈利」（⇒ 那是**判据**），
    但 V4 的**约束原文**是「成本压力 **1/2/3/5x**」——
    只报一个点看不出**形状**：基准已经为负时，
    「2× 仍不赚」与「成本再降一半也赚不了」是**两件事**，读者分不出来。
    （手册的验收项写的也是「报告里有 `2x 成本**年化**`」，不是一个布尔。）

    ## 接口
    `run(cost_rate) -> SimulationResult`：**注入**"按这个成本率跑一次模拟"的入口
    （与 `panel.provide_reader.Runner` 同款手法）——
    本函数只负责「换成本率 → 跑 → 取指标」，**不自己造第二套模拟**（承 R1）。
    """
    rows: list[dict] = []
    for m in multiples:
        rate = base_cost_rate * float(m)
        rep = trade_metrics.summarize(run(rate), benchmark=benchmark)
        rows.append({
            "multiple": float(m),
            "cost_rate": rate,
            "annual_return": float(rep["cagr"]),
            "total_return": float(rep["total_return"]),
            "profitable": float(rep["total_return"]) > 0.0,
        })
    return rows


def cost_stress_judge(rows: list[dict]) -> dict:
    """取 **V4 判据档**那一行（真源 = `backtest_config.COST_STRESS_JUDGE_MULTIPLE`）。

    找不到 ⇒ **报错**（承 P1：判据缺输入不许静默当"没通过"）。
    """
    want = float(backtest_config.COST_STRESS_JUDGE_MULTIPLE)
    for r in rows:
        if r["multiple"] == want:
            return r
    raise KeyError(f"成本压力表里没有判据档 {want:g}× —— 配置自检失效（承 V4）")


def render_cost_stress(rows: list[dict]) -> str:
    """渲染成本压力表 —— **单边 bp 必须显形**（否则"3bp"是黑话）。"""
    mults = "/".join(f"{r['multiple']:g}" for r in rows)
    out = ["", f"  ── ★ 成本压力（V4：{mults}×）──",
           "  倍数      单边(bp)        年化        总收益     仍赚"]
    for r in rows:
        out.append(f"  {r['multiple']:>4g}×  {r['cost_rate'] * 1e4:>8.2f}"
                   f"  {r['annual_return'] * 100:>9.2f}%"
                   f"  {r['total_return'] * 100:>9.2f}%"
                   f"  {'是' if r['profitable'] else '**否**'}")
    return "\n".join(out)


def _cluster_p_value(trades) -> tuple[float, int, int]:
    """**簇级 bootstrap 的 p 值**（检验「每笔平均 R ≠ 0」）。

    ## 为什么不能用普通的 t 检验

    同一**交易日**的多笔交易共享当天的行情 ⇒ **不独立**。
    按笔做 t 检验会**系统性高估显著性**（`statistics` 的文档里也写了这条：
    "futu 实测：4,076 笔只落在 **992 天**，按笔 vs 按日，结论**符号翻转**"）。

    ⇒ 重采样单位取**交易日**（簇），不是笔。

    返回 `(p 值, 观测数, 簇数)`；样本不足 ⇒ `(nan, n, k)`。
    """
    by_day: dict[str, list[float]] = {}
    for t in trades:
        by_day.setdefault(t.entry_day, []).append(float(t.r_multiple))
    # ⚠️ **空簇要先挡**：`effective_sample_size` 对空映射会**抛错**
    #    （承 V12：缺就是缺，不拿默认值兜底 —— 那是它的设计，不是 bug）。
    #    某个变体一笔都没成交时就会走到这里。
    if not by_day:
        return float("nan"), 0, 0
    obs, k = _stats.effective_sample_size(by_day)
    if k < 3:
        return float("nan"), obs, k
    rng = np.random.default_rng(20261009)
    keys = list(by_day)
    means = np.empty(2000)
    for i in range(means.size):
        pick = rng.integers(0, k, size=k)
        vals = [v for j in pick for v in by_day[keys[j]]]
        means[i] = float(np.mean(vals)) if vals else np.nan
    means = means[np.isfinite(means)]
    if means.size == 0:
        return float("nan"), obs, k
    # 双侧：均值落在 0 **另一侧**的比例 ×2
    lo = float((means <= 0).mean())
    p = 2.0 * min(lo, 1.0 - lo)
    return float(min(max(p, 1.0 / means.size), 1.0)), obs, k


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
        # ★ **要算"这一年的收益"，基数应是"上一年最后一天的净值"**（复审 10.6）。
        #   原来用 `eq[i0]`（= 本年第**一天收盘后**的净值）⇒
        #   **每年都漏掉第一天的 P&L** ⇒ "逐年相加 ≠ 总收益"。
        b0 = max(i0 - 1, 0)
        strat = eq[i1] / eq[b0] - 1.0
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
    out = [f"  {'持有':16s}{'笔数':>6s}{'胜率':>8s}{'平均R':>9s}{'总R':>9s}"]
    # ★ **0 / 1 / 2 天必须拆开**（复审 10.7）：
    #   `hold = 0` 是「**成交当日就止损**」（成交在开盘、止损在同一天），
    #   和「第 2 天止损」是**两件不同的事**。混在一桶里，
    #   "42% 在第 2 天就死"这个说法本身是**三个东西的混合**。
    for lo, hi, label in ((-1, 0, "0 天（成交当日）"), (0, 1, "1 天"), (1, 2, "2 天"),
                          (2, 5, "3–5 天"), (5, 10, "6–10 天"),
                          (10, 20, "11–20 天"), (20, 10 ** 9, ">20 天")):
        m = (holds > lo) & (holds <= hi)
        if not m.any():
            continue
        out.append(f"  {label:16s}{int(m.sum()):>6d}{float((rs[m] > 0).mean()) * 100:>7.1f}%"
                   f"{float(rs[m].mean()):>9.3f}{float(rs[m].sum()):>9.1f}")
    early = holds <= 2
    if early.any():
        out.append(f"  {'（≤2 天 合计）':16s}{int(early.sum()):>6d}"
                   f"{float((rs[early] > 0).mean()) * 100:>7.1f}%"
                   f"{float(rs[early].mean()):>9.3f}{float(rs[early].sum()):>9.1f}")
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


def sensitivity(base_params: dict,
                hold: int) -> list[tuple[str, dict, dict, int, float | None]]:
    """要扫的参数（**一次只动一个**）—— 回答"哪个参数最要紧"。

    返回 `(标签, 策略参数覆盖, 出场规则覆盖, 持仓上限)`。
    """
    out: list[tuple[str, dict, dict, int]] = []
    # ⚠️ 参数名在 RuleSet 重写时从 `max_stop_adr` 改成了 `stop_width_adr`
    #    —— 这个块**当时没重跑**，所以一直抛 `未知参数`（这次才抓到）。
    #    `None` 也不能用了（它是 float）⇒ 用一个大数表示"不限"。
    for v in (1.0, 1.5, 2.0, 10.0):
        lab = "不限" if v > 9 else f"{v}×ADR"
        out.append((f"止损宽度上限={lab}", {"stop_width_adr": v}, {}, 5, None))
    for v in (2.0, 3.0, 4.0, 6.0):
        out.append((f"止盈={v}R 减半", {}, {"target_r": v}, 5, None))
    for v in (0.0, 0.33, 0.5, 1.0):
        out.append((f"部分止盈比例={v:.2f}",
                    {}, {"partial_fraction": v,
                         "breakeven_after_partial": v > 0}, 5, None))
    for v in (3, 5, 8):
        out.append((f"最多同时持仓={v} 笔", {}, {}, v, None))
    for v in (3, 5, 10, 10 ** 9):
        out.append((f"5 天无进展离场：{'关' if v > 100 else f'{v} 天'}",
                    {}, {"no_progress_days": v}, 5, None))
    # ★ **`max_total_exposure`（我加的，不是他的规则）** —— 第三轮复审第 6 条：
    #   它的敏感度**比表里多数项都大**，却**不在表里**。
    #   复审实测：1.0（默认）+4.59%｜1.5 **−10.24%**｜2.0 +1.63%｜5.0 −3.25%
    #   ⇒ 而且它和四阶段的 `max_positions` **强耦合**（1.5 那个点最差，
    #     因为一放松曝险上限，"仓位满"重新变成约束）。
    #   ⚠️ 它**不是他的规则** —— 是我加的"不许杠杆"约束（原文没给）。
    for v in (0.5, 1.0, 1.5, 2.0, 5.0):
        out.append((f"总曝险上限={v:.1f}（⚠️我加的）", {}, {}, 5, v))
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
    ap.add_argument("--entry-quality", action="store_true",
                    help="★★ **进场质量筛查**：信号日可观测的量 → 能不能活过 5 天"
                         "（单变量 + BH，不拟合多元）")
    ap.add_argument("--eq-alpha", type=float, default=0.05,
                    help="BH 的 alpha（默认 0.05）")
    ap.add_argument("--plateau", action="store_true",
                    help="★ **邻域稳定性**：扫一条参数轴，看「最好」是尖峰还是平台")
    ap.add_argument("--plateau-metric", default="expectancy_r",
                    help="看哪个指标（默认 expectancy_r）")
    ap.add_argument("--bh", action="store_true",
                    help="★ **多重检验校正**（BH）—— 同一数据上跑 N 个变体，必须报分母")
    ap.add_argument("--bh-alpha", type=float, default=0.05,
                    help="BH 的 alpha（默认 0.05）")
    ap.add_argument("--decompose", action="store_true",
                    help="★ 四阶段**拆四臂**（只持仓数 / 只出场规则 / 两个 / 都不）")
    ap.add_argument("--random", action="store_true",
                    help="★ 随机对照（同日/同数量/同票池/同一套规则）+ **分辨力**"
                         " + **持有期分布对照**")
    ap.add_argument("--random-n", type=int, default=200,
                    help="随机重抽次数（默认 200；n=20 时分辨力只有 ~11pp）")
    ap.add_argument("--vcp", action="store_true",
                    help="变体：只挑 VCP 那一个形态（§7.1 六要点）")
    ap.add_argument("--no-exposure", action="store_true",
                    help="关掉四阶段曝险（做对照用）")
    ap.add_argument("--no-market-gate", action="store_true",
                    help="关掉 §7.1 的**大市动能门槛**（做对照用）")
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
          f"四阶段曝险: {not args.no_exposure}｜"
          f"大市动能门槛: {not args.no_market_gate}")
    print()

    # ★★ **面板必须多吃一段预热** —— 否则因子预热落在**评估窗口内部**。
    #
    #   因子要 250–260 个交易日（`ret260` / `sma200` / `near_52w_high`），
    #   若面板正好从窗口起点开始，**前 ~13 个月一笔信号都不会有**：
    #   实测旧版第一笔成交在 **2023-05-19**（距窗口起点 2022-05-03 约 13 个月）。
    #
    #   ⇒ 于是"可用历史"实际是 **~3.4 年**，不是文档里反复用的 **4.4 年**，
    #     检出下限应是 `1.96/√3.4 ≈ 1.06`，不是 **0.93**。
    #
    #   库里 2022-05-03 之前有 **3,367 天**可用（最早到 1987-06-16）——
    #   **够得很**，之前只是没用。
    warmup = [d for d in days_all if d < args.start][-WARMUP_DAYS:]
    panel = read_panel(symbols, days=warmup + window, stocks_day=stock_day)
    spy_panel = read_panel(["SPY"], days=warmup + window, stocks_day=stock_day)
    # ⚠️ 面板**不一定**给出 `read_days()` 里的每一天（**末日常缺** —— 那天的 K 线
    #    还没落库）⇒ 窗口取**交集**，否则 `factors.loc[window]` 会 `KeyError`。
    #    （这也解释了文档里"→ 2026-10-08"与面板实际到 10-07 的矛盾。）
    have = set(panel.dates)
    dropped = [d for d in window if d not in have]
    window = [d for d in window if d in have]
    if dropped:
        print(f"⚠️ 面板缺这些日子，已从窗口剔除：{dropped}")
    if not window:
        raise SystemExit("窗口与面板没有交集 —— 检查 --start")
    print(f"面板   : {len(panel.dates)} 天 × {len(panel.symbols)} 只"
          f"（含 **{len(warmup)} 天预热**，评估窗口 {len(window)} 天）")

    strategy = TugboatBreakout(entry_mode=args.entry_mode, vcp_filter=args.vcp,
                               market_gate=not args.no_market_gate)
    # ★ **出处分布从数据生成**（承 R3：出处只有一处 ⇒ 不可能漂移）——
    #   `RuleSet.summary()` 是 `rules.py` 里**早就有的**能力，这里只是**接线**。
    #   ⇒ 手写「几个阈值是我定的」那类声明**全部作废**，以这一行为准（治 TD-05-21）。
    print(f"规则集 : {strategy.ruleset.name}｜{strategy.ruleset.summary()}")
    # ★ 因子在**长面板**上算（吃满预热），再把**因子与面板一起切到窗口**
    factors = {n: run_factor(n, panel).values.loc[list(window)]
               for n in REQUIRED_FACTORS}
    market_state = _market_state(panel, spy_panel)      # 也要**在长面板上算**
    # ★ §7.1 的两个方法 → **入场门槛**（治 TD-05-30）：市况不对 ⇒ 不做突破。
    #   与 `exposure.attach_market_state` 是**两份用途**：这里管「做不做」，
    #   那里管「做几笔」（§2.2 四阶段）。同一个 state 喂两处，但判据各取所需。
    strategy.attach_market_state(market_state)
    panel = _WindowedPanel(panel, window)               # ⇒ 下游自动只看窗口

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
        exposure.attach_market_state(market_state)

    from trade_simulator import AccountPolicy
    account = AccountPolicy(cost_rate=backtest_config.COST_RATE)
    result = simulate(
        panel.dates, panel.symbols, bars, cand,
        strategy_name=strategy.name, strategy_params=strategy.params,
        ma_exit_level=ma_exit, exit_policy=strategy.exit_policy, adr_ratio=factors["adr20"], market_state=market_state,
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
          f"最大差 {chk['max_abs_diff']:.1e}）"
          + (f"｜**跳过 {chk['skipped']} 笔**（记录不完整）" if chk["skipped"] else ""))

    # ★ **成交逻辑对账**（复审第 9 条）—— 上面那条只验"记录自洽"，
    #   这一条从 bar **独立反推**成交价，验"**算得对不对**"。
    chk2 = reconcile_fills(result, panel.dates, panel.symbols, bars,
                           trade_on_close=account.trade_on_close)
    if chk2["bad"]:
        print(f"\n⛔ **成交逻辑对账失败**：入场 {chk2['bad_entry']} 笔 / "
              f"止损 {chk2['bad_stop']} 笔 / 收盘结算 {chk2['bad_close']} 笔"
              f"与按规则重推不符")
        for line in chk2["examples"]:
            print("   ", line)
        print("   ⇒ 成交价与规则不符 ⇒ 报告不可信 —— 已中止。")
        return 2
    print(f"成交对账: 入场 {chk2['checked_entry']} 笔 / 止损 {chk2['checked_stop']} 笔 / "
          f"收盘结算 {chk2['checked_close']} 笔，**全部与规则一致**")
    # ★ **未覆盖必须显形**（治 TD-05-11）：不许用"对账通过"盖过"有一类没对"。
    _unver = chk2.get("unverifiable") or {}
    if _unver:
        _txt = " / ".join(f"{k} {v} 笔" for k, v in sorted(_unver.items()))
        print(f"成交对账: ⚠️ **未覆盖 {sum(_unver.values())} 笔**（{_txt}）"
              f" —— 这几类的成交价**无法从 bar 独立反推**"
              f"（理由见 `trade_simulator._UNVERIFIABLE_FILLS`）")
    print()

    spy_close = spy_panel.field("close")["SPY"].reindex(panel.dates)
    bench = (spy_close / spy_close.shift(1) - 1.0).to_numpy()[1:]

    report = trade_metrics.summarize(result, benchmark=bench)
    mc = trade_metrics.monte_carlo(result, iterations=args.iterations)

    # ★ **有效样本**从**首个信号**算起，不是从窗口起点 ——
    #   窗口前段虽然因子已经预热好了，但**一条信号都没有**（条件太稀），
    #   拿"窗口长度"当样本长度会**高估检出能力**（这正是复审 C 指出的）。
    entries = sorted(t.entry_day for t in result.trades)
    first_signal = entries[0] if entries else "（无成交）"
    if entries and first_signal in panel.dates:
        used = len(panel.dates) - panel.dates.index(first_signal)
    else:
        used = len(panel.dates)
    eff_years = max(used / 252.0, 1e-9)

    footer = (
        "\n⚠️ 签六个已经显形的偏差（规格 §11.4）：\n"
        "  ① 催化剂/叙事**测不了** —— 那是他称「最核心」的筛选条件 ⇒ 对他不利\n"
        "  ② 日内入场**测不了**（无分钟数据）⇒ 入场与止损都用日线近似\n"
        "  ③ 四阶段的市场状态只能用「票池宽度 + 指数偏离」代理（情绪无数据）\n"
        "  ④ ★ **幸存者偏差** —— 票池是「这几年券商 App 上的热门股」快照回溯使用，\n"
        "     **不是当年的时点名单**（池内有 2024–26 才上市的票）⇒ 方向是**高估**。\n"
        "     实测池子等权买入持有 **+443.91%** vs SPY +86.66%。\n"
        "     ⚠️ **它不污染「真实 vs 随机」那个对照**（两侧共用同一个池子）\n"
        "        ⇒ 那个对照是整份工作里**最抗偏差**的证据。\n"
        "  ⑤ ★★ **没有样本外** —— 阈值与出场参数都是**在同一段数据上**定的；\n"
        "     `walk_forward_validation` 只做「分段一致性」（`train` 段从不被使用、\n"
        "     `gap` 是空操作，见其文件头）⇒ 本报告里**任何一个「最好的参数」\n"
        "     都不是样本外结论**（治 TD-05-13）。\n"
        "  ⑥ ★ **成本假设**（**执行假设**，不是策略参数）—— 本报告按 "
        f"**{backtest_config.COST_RATE * 1e4:.1f}bp 单边**估；成交时点是\n"
        "     「突破日**次日开盘的市价单**」⇒ 其中**滑点部分偏乐观**\n"
        "     （理由见 `backtest_config.COST_RATE` 注释）。\n"
        "     ⇒ **结论对成本的依赖看紧随本报告之后的 V4 成本压力表**，\n"
        "       不要只读基准那一行。\n"
        f"\n⚠️ 检出下限：**{eff_years:.1f} 年**（不是窗口的 {len(window) / 252:.1f} 年）\n"
        f"    —— 因子预热 {WARMUP_DAYS} 天落在窗口外，但**首个信号**在\n"
        f"    {first_signal}，此后才有交易 ⇒ 有效样本从那时算。\n"
        f"    能证明的最小 Sharpe ≈ **1.96/√{eff_years:.1f} ≈ "
        f"{1.96 / np.sqrt(eff_years):.2f}**\n"
        "    ⇒ 中等优势**测不出来**，「说不清」不等于「没优势」（规格 §0.5）\n"
    )
    text = trade_metrics.render_report(
        report, mc, title=f"Tugboat 突破交易 2.0 · 入场={args.entry_mode} · "
                          f"VCP={args.vcp} · 四阶段={not args.no_exposure}") + footer
    print(text)

    # ★ **过仓库自己那道门**（第三轮独立复审第 3 条）：
    #   `backtest_config` 里早就写死了判据，而这条路一条都没用。
    #   ⇒ 在说"说不清"之前先过门；**按门算可能是 INVALID，不是说不清**。
    side_ratio = (float(np.mean(result.daily_exposure))
                  if result.daily_exposure else None)
    # ★ **成本压力曲线**（承 V4：1/2/3/5×）—— 成本是**执行假设**，不是策略参数，
    #   且它进的是**现金流**（影响后续每一笔的股数）⇒ 每个档位必须**真的重跑一次**，
    #   不能拿基准结果按比例缩放（那是第二份、更弱的实现）。
    def _run_at_cost(cost_rate: float):
        return simulate(panel.dates, panel.symbols, bars, cand,
                        strategy_name=strategy.name, strategy_params=strategy.params,
                        ma_exit_level=ma_exit, exit_policy=strategy.exit_policy, adr_ratio=factors["adr20"], market_state=market_state,
                        account=_replace_account(account, cost_rate), exposure=exposure)

    stress = cost_stress(_run_at_cost, base_cost_rate=account.cost_rate, benchmark=bench)
    # ★ **1× 档必须与主报告逐位一致** —— 否则报告与压力表就是**两份真相**。
    #   （它顺手把"主报告那次跑"与"压力表那次跑"钉成同一个口径。）
    _base_row = next(r for r in stress if r["multiple"] == 1.0)
    if abs(_base_row["total_return"] - float(report["total_return"])) > 1e-9:
        print(f"\n⛔ 成本压力表的 1× 档（总收益 {_base_row['total_return']:.8f}）与"
              f"主报告（{float(report['total_return']):.8f}）**不一致**"
              " ⇒ 两条路径口径不同，报告不可信 —— 已中止。")
        return 2
    _judge = cost_stress_judge(stress)
    cost2x_ok = bool(_judge["profitable"])
    print(render_cost_stress(stress))
    # ★ **判据只有一个来源**：`walk_forward_validation.judge_verdict`。
    #   `independent_audit`（`run_backtest.py` 那条路）吃**逐 bar 仓位**，
    #   本路径吃**交易级结果** ⇒ 两条入口**形态不同**，但**共用同一个判据**。
    #   ⇒ TD-05-12 的「未接线」**不构成缺陷** —— 那不是"应该接"，是"形态不同"。
    v = verdict.run_verdict(result, report, side_ratio=side_ratio,
                            cost2x_profitable=cost2x_ok)
    print(verdict.render_verdict(v))

    # ── ★ 目标价口径**同报两个**（治 TD-05-25）────────────────────────────
    #   `target_r` 用的是**我们自己的 R 刻度**（R = 入场价 − **初始**止损价）；
    #   他的止盈口径是「**至少 2–3 倍 ADR**」（§10.6③）—— **两者不等价**。
    #   ⇒ 同报，谁也不假装是另一个（换算唯一实现 = `units.target_price_from_adr`）。
    if result.trades:
        _adr, _cls = factors["adr20"], panel.field("close")
        _ratios = []
        for _t in result.trades:
            if _t.entry_day in _adr.index and _t.symbol in _adr.columns:
                _a, _c = _adr.loc[_t.entry_day, _t.symbol], _cls.loc[_t.entry_day, _t.symbol]
                if np.isfinite(_a) and np.isfinite(_c) and _a > 0:
                    _ratios.append((_t.entry_price - _t.initial_stop) / (_a * _c))
        if _ratios:
            _m = float(np.median(_ratios))
            print("  ── ★ 目标价口径（治 TD-05-25）──")
            print(f"  `target_r` 用**我们的 R 刻度**（R = 入场价 − 初始止损价）；"
                  f"他的口径是「至少 2–3 倍 ADR」（§10.6③）。")
            print(f"  ⇒ 两者**不等价**：本样本 R / (1×ADR) 的**中位比 = {_m:.2f}**"
                  f"（1.00 才是恰好等价）。**同报，不混用**。")

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

    if args.entry_quality:
        # ★ **进场质量筛查**（用户要的第 3 条）。
        #
        #   为什么只做"单变量 + BH"：样本只有几十笔，拟合多元模型**必然过拟合**。
        #   为什么特征只能是**信号日可观测**的：用 `hold_days` 当特征 = **同义反复**。
        order = {d: i for i, d in enumerate(panel.dates)}
        have = set(zip(cand["day"], cand["symbol"]))
        feats: list[dict] = []
        for t in result.trades:
            ei = order.get(t.entry_day)
            sig = None
            if ei is not None:
                for k in range(ei - 1, max(ei - 6, -1), -1):
                    if (panel.dates[k], t.symbol) in have:
                        sig = panel.dates[k]
                        break
            if sig is None:
                continue
            row: dict = {"_symbol": t.symbol, "_entry": t.entry_day,
                         "_hold": t.hold_days, "_r": t.r_multiple}
            for name in REQUIRED_FACTORS:
                v = factors[name].loc[sig, t.symbol]
                row[name] = float(v) if np.isfinite(v) else np.nan
            # ── 结构性特征（信号日可观测）：止损有多宽 ──
            row["止损宽度_ADR"] = float(units.stop_distance_adr(
                float(panel.field("close").loc[sig, t.symbol]),
                float(cand[(cand["day"] == sig) & (cand["symbol"] == t.symbol)]
                      ["stop_price"].iloc[0]),
                float(panel.field("close").loc[sig, t.symbol]),
                float(factors["adr20"].loc[sig, t.symbol])))
            # ── 市场状态（也是信号日可观测的；已 shift(1)）──
            if market_state is not None and sig in market_state.index:
                row["市场_宽度"] = float(market_state.loc[sig, "breadth"])
                row["市场_SPY距200MA"] = float(
                    market_state.loc[sig, "index_dist_200ma"])
            feats.append(row)
        fd = pd.DataFrame(feats)
        if fd.empty:
            print("\n（没有可用的成交，无法筛查）")
            return 0
        targets = {
            "活过 5 天": (fd["_hold"] > 5).astype(int).to_numpy(),
            "活过 2 天": (fd["_hold"] > 2).astype(int).to_numpy(),
            "最终赚钱（R>0）": (fd["_r"] > 0).astype(int).to_numpy(),
        }
        cols = [c for c in fd.columns if not c.startswith("_")]
        for tname, y in targets.items():
            if len(set(y.tolist())) < 2:
                print(f"\n（「{tname}」只有一类，无法筛查）")
                continue
            res = entry_quality.screen(fd[cols], y, target=tname,
                                       alpha=args.eq_alpha)
            print(entry_quality.render_screen(res))
        return 0

    if args.plateau:
        # ★ **小样本下唯一能做的过拟合检验**：看「最好」的点周围是尖峰还是平台。
        #
        #   为什么不做 IS/OOS：实测 **29 笔**，切完每边 ~14 笔 ⇒ 那个「排名」是纯噪声。
        #   （我第一版硬跑了 IS/OOS，**方向错了**，已删。）
        #
        #   ★★ 关键设计：**参数属于哪个对象，必须显式声明** ——
        #      策略参数影响**候选**；`target_r` 属于**出场规则**；
        #      `max_total_exposure` 属于**账户**。
        #      混为一谈会让「改参数」变成空操作（第一版就是这么错的：
        #      五行数值一模一样，看起来像「参数没影响」）。
        from dataclasses import replace as _r4

        axes: dict[str, tuple[str, tuple]] = {
            "tight_range_max": ("strategy", (0.03, 0.045, 0.06, 0.075, 0.09)),
            "stop_width_adr": ("strategy", (1.0, 1.25, 1.5, 2.0, 3.0)),
            "target_r": ("exit", (2.0, 3.0, 4.0, 5.0, 6.0)),
            "max_total_exposure": ("account", (0.5, 0.75, 1.0, 1.5, 2.0)),
        }

        def _build(params, _w):
            # 只有「策略参数」才需要重算候选（其余三类候选不变）
            prm = params if _w == "strategy" else {}
            st = TugboatBreakout(entry_mode=args.entry_mode,
                                 **{k: v for k, v in prm.items()
                                    if k in DEFAULTS})
            return st.candidates(panel, factors)

        def _sim(candidates, params, _w):
            key, val = next(iter(params.items()))
            sparams = params if _w == "strategy" else {}
            st = TugboatBreakout(entry_mode=args.entry_mode,
                                 **{k: v for k, v in sparams.items()
                                    if k in DEFAULTS})
            ep, ac = strategy.exit_policy, account
            if _w == "exit":
                ep = _r4(ep, **{key: float(val)})
            elif _w == "account":
                ac = _r4(ac, **{key: float(val)})
            return simulate(panel.dates, panel.symbols, bars, candidates,
                            strategy_name=st.name, strategy_params=st.params,
                            ma_exit_level=st.ma_exit_level(panel, factors), adr_ratio=factors["adr20"], market_state=market_state,
                            exit_policy=ep, account=ac, exposure=exposure)

        for _name, (_where, _vals) in axes.items():
            _res = plateau.scan(
                _name, _vals,
                build_candidates=lambda prm, w=_where: _build(prm, w),
                simulate=lambda candidates, params, w=_where: _sim(candidates, params, w),
                summarize=lambda r, benchmark=None: trade_metrics.summarize(
                    r, benchmark=None),
                metric=args.plateau_metric)
            print(plateau.render_plateau(_res))
        return 0

    if args.bh:
        # ★★ **多重检验校正 —— 把「我试过的全部变体」当一个族报出来**（用户要的第 2 条）。
        #
        #   复审的原话：「策略注册表 + 阈值网格 ⇒ 必然试 N 次；
        #   `1 − 0.95¹⁸ ≈ **60%**` ⇒ **只报单个 p<0.05 而不报分母，等于没做统计。**」
        #
        #   ⚠️ 关键不是"跑了 BH 函数"，而是 **族要收全**：
        #      前面 `--matrix` / `--sensitivity` / `--free-params` / `--plateau`
        #      各自报数字，**谁也不报分母** ⇒ 合起来看就是"挑了最好的那个"。
        #      ⇒ 这里把**它们全部**收进一个族。
        from dataclasses import replace as _r5

        def _mk_strategy(prm: dict):
            return TugboatBreakout(
                entry_mode=args.entry_mode,
                **{k: v for k, v in prm.items() if k in DEFAULTS})

        def _run(prm: dict, where: str):
            """`where` ∈ {strategy, exit, account} —— **参数属于哪必须显式**。"""
            st = _mk_strategy(prm if where == "strategy" else {})
            c = st.candidates(panel, factors)
            if c.empty:
                return None
            ep, ac = st.exit_policy, account
            if where == "exit":
                ep = _r5(ep, **{k: float(v) for k, v in prm.items()})
            elif where == "account":
                ac = _r5(ac, **{k: float(v) for k, v in prm.items()})
            return simulate(panel.dates, panel.symbols, bars, c,
                            strategy_name=st.name, strategy_params=st.params,
                            ma_exit_level=st.ma_exit_level(panel, factors), adr_ratio=factors["adr20"], market_state=market_state,
                            exit_policy=ep, account=ac, exposure=exposure)

        # ── ★ **族**：我在这份数据上试过的全部变体 ──
        family: list[tuple[str, dict, str]] = []
        for mode in ENTER_MODES:                      # 三种入场 × 曝险开关
            for on in (True, False):
                family.append((f"入场={mode}｜四阶段={'开' if on else '关'}",
                               {"entry_mode": mode, "_exposure_on": on}, "entry"))
        for v in (1.0, 1.25, 1.5, 2.0, 3.0):          # 止损宽度（他的规则，1–1.5）
            family.append((f"止损宽度={v}×ADR", {"stop_width_adr": v}, "strategy"))
        for v in (2.0, 3.0, 4.0, 5.0, 6.0):           # 止盈
            family.append((f"止盈={v:g}R", {"target_r": v}, "exit"))
        for v in (0.0, 0.33, 0.5, 1.0):               # 部分止盈比例
            family.append((f"部分止盈={v:.2f}",
                           {"partial_fraction": v}, "exit"))
        for v in (0.03, 0.045, 0.06, 0.075, 0.09):    # 紧区间（**唯一我定的**）
            family.append((f"紧区间≤{v:.3f}", {"tight_range_max": v}, "strategy"))
        for v in (0.01, 0.02, 0.04):                  # 均线收拢（推断）
            family.append((f"均线收拢≤{v:.2f}", {"ma_converge_max": v}, "strategy"))
        for v in (0.0, 0.025, 0.04):                  # §10.6① ADR%（原文）
            family.append((f"ADR%≥{v:.3f}", {"adr_floor": v}, "strategy"))
        for v in (0.80, 0.85, 0.90, 0.97):            # §8.1 RS（原文；含默认 0.85）
            family.append((f"RS≥{v:.2f}", {"rs_min": v}, "strategy"))
        for rs in ("base", "vcp", "rsi_tight"):       # 三套并列的规则集
            family.append((f"规则集={rs}", {"ruleset": rs}, "strategy"))

        ps, tags, notes = [], [], []
        print()
        print("═" * 84)
        print("★ 多重检验校正 —— **把「我试过的全部变体」当一个族报出来**")
        print(f"  p 值口径：**簇级 bootstrap**（簇 = 交易日）—— 同日多笔**不独立**，"
              f"按笔算会高估显著性")
        print("  ⚠️ 这不是「跑了 BH 函数」，是 **族收全了**：前面 --matrix / --sensitivity /")
        print(f"     --free-params / --plateau 各自报数字、**谁也不报分母**")
        print("─" * 84)
        print(f"  {'变体':34s}{'候选':>6s}{'笔数':>6s}{'簇数':>6s}"
              f"{'每笔R':>9s}{'p(原始)':>10s}")
        for tag, prm, where in family:
            if where == "entry":
                # ⚠️ **必须把 `entry_mode` 与曝险开关真的传下去** ——
                #    第一版这里写的是 `_mk_strategy({})` + 恒用 `exposure`，
                #    于是 6 行**数字一模一样**（看着像"入场没影响"，其实是**没改**）。
                #    同一个错我在 `--plateau` 上犯过一次（`target_r` 五行相同）。
                mode = prm["entry_mode"]
                on = prm["_exposure_on"]
                st = TugboatBreakout(entry_mode=mode)
                c = st.candidates(panel, factors)
                if c.empty:
                    print(f"  {tag:34s}      —— 候选 0")
                    continue
                ex = exposure
                if not on:
                    ex = StaticExposure()
                elif hasattr(ex, "attach_market_state"):
                    ex.attach_market_state(market_state)
                rr = simulate(panel.dates, panel.symbols, bars, c,
                              strategy_name=st.name, strategy_params=st.params,
                              ma_exit_level=st.ma_exit_level(panel, factors), adr_ratio=factors["adr20"], market_state=market_state,
                              exit_policy=st.exit_policy, account=account,
                              exposure=ex)
                n_c = len(c)
            else:
                rr = _run(prm, where)
                if rr is None:
                    print(f"  {tag:34s}      —— 候选 0")
                    continue
                n_c = len(cand) if where != "strategy" else len(
                    _mk_strategy(prm).candidates(panel, factors))
            pv, obs, k = _cluster_p_value(rr.trades)
            m = trade_metrics.summarize(rr, benchmark=bench)
            ps.append(pv)
            tags.append(tag)
            notes.append(f"{n_c}|{obs}|{k}|{m['expectancy_r']}")
            print(f"  {tag:34s}{n_c:>6d}{obs:>6d}{k:>6d}"
                  f"{m['expectancy_r']:>9.3f}{pv:>10.4f}")

        finite = [i for i, v in enumerate(ps) if np.isfinite(v)]
        print("─" * 84)
        if finite:
            res = _stats.benjamini_hochberg([ps[i] for i in finite],
                                            alpha=args.bh_alpha)
            thr = res.threshold
            thr_txt = f"{thr:.4f}" if np.isfinite(thr) else "无（没有变体过线）"
            print(f"  **BH 校正**：族大小 = **{res.family_size}**｜"
                  f"alpha = {res.alpha}｜阈值 = {thr_txt}")
            print(f"  ⇒ **通过 BH 的变体数：{sum(res.significant)} / {res.family_size}**")
            if not any(res.significant):
                print()
                print("  ★★ **一个都没过。** 这正说明为什么必须报分母 ——")
                print("     若只看原始 p 值，下面这些会「看起来显著」：")
                for j, i in enumerate(finite):
                    if ps[i] < args.bh_alpha:
                        print(f"       {tags[i]:34s} p={ps[i]:.4f} → "
                              f"**BH p={res.adjusted[j]:.4f}** ✗")
        else:
            print("  （所有变体的 p 值都算不出来 —— 样本/簇数不足）")
        print("─" * 84)
        print("  ⚠️ 不报分母的 p 值是**没有意义**的 —— 这就是为什么这张表要一起给。")
        return 0

    if args.decompose:
        # ★★ **四阶段拆解**（第三轮独立复审第 5 条）。
        #   复审拆开跑过：只调持仓数 +2.29%｜只改阶段④出场规则 −4.69%｜
        #   两个一起 +4.59%｜都不做 −0.86% ⇒ **交互项主导**。
        #   而我当时把它列进「机制级结论（幅度大、方向一致）」—— **恰好搞反了**。
        print()
        print("═" * 74)
        print("★ 四阶段**拆解** —— 两个组成部分单独跑，看是不是交互项主导")
        print("   （`TugboatExposure` 调两样东西：**持仓数上限** 与 **阶段④的出场规则**）")
        print("─" * 74)
        print(f"  {'臂':36s}{'笔数':>6s}{'总收益':>10s}{'Sharpe':>9s}"
              f"{'MDD':>10s}{'每笔R':>9s}")
        for label, ex in (("都不做（= 四阶段关）", StaticExposure()),
                          ("**只调持仓数**（不改出场规则）",
                           TugboatExposure(use_exit=False)),
                          ("**只改阶段④出场规则**（不调持仓数）",
                           TugboatExposure(use_slots=False)),
                          ("**两个一起**（= 现状）", exposure)):
            # ⚠️ `StaticExposure`（"都不做"那一臂）**没有** `attach_market_state`
            #    —— 它不读市场状态。所以按**能力**判断，不按 `--no-exposure`。
            if hasattr(ex, "attach_market_state"):
                ex.attach_market_state(market_state)
            rr = simulate(panel.dates, panel.symbols, bars, cand,
                          strategy_name=strategy.name, strategy_params={},
                          ma_exit_level=ma_exit, exit_policy=strategy.exit_policy, adr_ratio=factors["adr20"], market_state=market_state,
                          account=account, exposure=ex)
            mm = trade_metrics.summarize(rr, benchmark=bench)
            print(f"  {label:36s}{mm['n_trades']:>6d}"
                  f"{mm['total_return'] * 100:>9.2f}%{mm['sharpe']:>9.2f}"
                  f"{mm['max_drawdown'] * 100:>9.2f}%{mm['expectancy_r']:>9.3f}")
        print("─" * 74)
        print("  ★ 怎么读：**若两个组成部分单独都小或负、合起来才大** ⇒")
        print("     那是**交互项主导**，**不是**「四阶段有正贡献」。")
        print("     ⚠️ 交互项主导是**不稳定**的标志 —— `A×B` 产生的效应")
        print("        换个窗口几乎必然翻转，**不能**当\"机制级结论\"。")
        return 0

    if args.random:
        # ★ 随机对照 —— 唯一能隔离"选股贡献"的参照。
        #   ⚠️ n 默认 **200**（不是 20）：n=20 时分位标准误 ≈11pp，
        #      连"50 分位"都测不准，更别说"高于/低于随机"。
        def _ep():
            return {"ma": ma_exit, "policy": strategy.exit_policy}

        ctl = random_control.run_control(
            cand, panel, bars, simulate=simulate,
            summarize=trade_metrics.summarize,
            make_exit_policy=_ep, make_account=lambda: account,
            exposure=exposure, benchmark=bench,
            n_iter=args.random_n, seed=20261009, real_result=result)
        print(random_control.render_control(ctl))
        return 0

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
                          ma_exit_level=st.ma_exit_level(panel, factors), adr_ratio=factors["adr20"], market_state=market_state,
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
                        ma_exit_level=st.ma_exit_level(panel, factors), adr_ratio=factors["adr20"], market_state=market_state,
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
        for label, sparams, eparams, cap, exp_cap in sensitivity(
                strategy.params, hold_days):
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
                ma_exit_level=st.ma_exit_level(panel, factors), adr_ratio=factors["adr20"], market_state=market_state,
                exit_policy=_replace(st.exit_policy, **eparams),
                account=_replace(account, max_positions=cap,
                                 **({} if exp_cap is None
                                    else {"max_total_exposure": exp_cap})),
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
                         ma_exit_level=ma_exit, exit_policy=exit_policy, adr_ratio=factors["adr20"], market_state=market_state,
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
                      ma_exit_level=ma_exit, exit_policy=strategy.exit_policy, adr_ratio=factors["adr20"], market_state=market_state,
                      account=account, exposure=exposure)
        r2 = simulate(panel.dates, panel.symbols, bars, cand,
                      strategy_name=strategy.name + "·无框架", strategy_params={},
                      ma_exit_level=ma_exit, exit_policy=bare_ep, adr_ratio=factors["adr20"], market_state=market_state,
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
