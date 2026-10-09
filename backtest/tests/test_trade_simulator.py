"""交易级组合模拟器 —— 测试。

重点盯**四条容易写错、且直接决定结论真假**的判据（规格 §3.0c）：

1. **无前视**：信号在 `t` 收盘，成交在 `t+1` 开盘。
2. **跳空**：止损被跳空越过时，成交价取 **`min(止损, 开盘)`**（更差）。
   —— 按止损价成交会**系统性高估**收益。
3. **同 bar 冲突**：止损与止盈都触 ⇒ **判止损**（取对他不利的一侧）。
4. **★ 跨引擎一致性**：与 `pnl_engine` 的公式在重叠情形下**必须对得上**
   —— 这是"两种模型不是两个真相"的**唯一凭据**（承铁律 R1）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import math  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import pnl_engine  # noqa: E402
from trade_simulator import (  # noqa: E402
    EXIT_STOP, AccountPolicy, ExitPolicy, simulate,
)

COST = 0.0003
SYM = "AAA"


def _make(bars, symbols=(SYM,)):
    """把逐 bar 的 `(o, h, l, c)` 摊成模拟器要的入参（**不依赖任何一层的类型**）。"""
    dates = tuple(f"d{i + 1}" for i in range(len(bars)))
    frames = {
        name: pd.DataFrame(
            {s: [b[idx] for b in bars] for s in symbols},
            index=list(dates), dtype="float64")
        for idx, name in enumerate(("open", "high", "low", "close"))
    }
    return dates, tuple(symbols), frames


def _run(bars, candidates, *, exit_policy=None, account=None, ma=None,
         symbols=(SYM,), exposure=None):
    dates, syms, frames = _make(bars, symbols)
    if candidates and isinstance(candidates[0], dict):
        frame = pd.DataFrame(candidates)          # 含 limit_price / valid_days
    else:
        frame = pd.DataFrame(candidates, columns=["day", "symbol", "stop_price"])
    if ma is None:
        ma = pd.DataFrame(np.nan, index=list(dates), columns=list(syms))
    return simulate(
        dates, syms, frames, frame, strategy_name="t", strategy_params={"k": 1},
        ma_exit_level=ma,
        exit_policy=exit_policy or ExitPolicy(),
        account=account or AccountPolicy(cost_rate=COST),
        exposure=exposure,
    )


# ── ① 无前视：次日开盘成交 ──────────────────────────────────────────────

def test_entry_fills_at_next_open() -> bool:
    """信号在 `d1` ⇒ 成交价 = `d2` 的**开盘价**（不是 `d1` 的收盘、也不是 `d2` 的收盘）。"""
    bars = [(10, 10, 10, 10)] * 3 + [(11, 11, 11, 11)] * 3
    r = _run(bars, [("d1", SYM, 9.0)],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=2))
    got = r.trades[0].entry_price if r.trades else float("nan")
    ok = math.isclose(got, 10.0, rel_tol=1e-12) and r.trades[0].entry_day == "d2"
    print(f"{'[PASS]' if ok else '[FAIL]'} 次日开盘成交（成交日 {r.trades[0].entry_day}，"
          f"价 {got}）")
    return ok


# ── ② 跳空：止损被越过时取更差价 ────────────────────────────────────────

def test_stop_gap_uses_worse_price() -> bool:
    """**已持仓**，随后某日**跳空低开**穿过止损（开盘 90 < 止损 95）⇒ 成交价 = **90**。

    ⚠️ 若按止损价 95 成交，每笔会**凭空多赚** 5 元/股 —— 系统性高估就是从这来的。
    """
    bars = [(100, 100, 100, 100),      # d1 信号（止损 95）
            (100, 100, 100, 100),      # d2 开盘 100 入场
            (90, 91, 89, 90),          # d3 ★ 跳空低开：开盘 90 已在止损 95 之下
            (89, 89, 89, 89), (89, 89, 89, 89)]
    r = _run(bars, [("d1", SYM, 95.0)],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=4))
    t = r.trades[0]
    ok = (t.exit_reason == EXIT_STOP and math.isclose(t.exit_price, 90.0, rel_tol=1e-12))
    print(f"{'[PASS]' if ok else '[FAIL]'} 跳空越过止损 ⇒ 按开盘价成交"
          f"（{t.exit_reason} @ {t.exit_price}，应 90.0 而不是 95.0）")
    return ok


def test_entry_skipped_if_open_already_below_stop() -> bool:
    """★ 入场前价格**已跌破结构止损** ⇒ **不开仓**（形态已破坏），不是"进场即止损"。

    这条是设计决定：止损是**结构位**，开盘就跌穿它意味着**这笔设置已经失效** ——
    硬进等于明知形态坏了还接。**必须显式**（否则会静默产生一笔必亏的交易）。
    """
    bars = [(100, 100, 100, 100),      # d1 信号（止损 95）
            (90, 91, 89, 90),          # d2 ★ 开盘 90 已在止损 95 之下
            (90, 90, 90, 90), (90, 90, 90, 90)]
    r = _run(bars, [("d1", SYM, 95.0)],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=3))
    ok = len(r.trades) == 0
    print(f"{'[PASS]' if ok else '[FAIL]'} 开盘已跌破止损 ⇒ 不开仓"
          f"（成交 {len(r.trades)} 笔，应 0）")
    return ok


def test_stop_without_gap_uses_stop_price() -> bool:
    """没跳空（开盘 10.0 > 止损 9.5，盘中 low 9.0）⇒ 成交价 = **止损价 9.5**。"""
    bars = [(10, 10, 10, 10), (10.0, 10.5, 9.0, 9.2)] + [(9, 9, 9, 9)] * 3
    r = _run(bars, [("d1", SYM, 9.5)],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=3))
    t = r.trades[0]
    ok = (t.exit_reason == EXIT_STOP and math.isclose(t.exit_price, 9.5, rel_tol=1e-12))
    print(f"{'[PASS]' if ok else '[FAIL]'} 未跳空 ⇒ 按止损价成交（{t.exit_price}）")
    return ok


# ── ③ 同一根 bar 内止损与止盈都触 ⇒ 判止损 ──────────────────────────────

def test_stop_wins_when_both_touched_in_same_bar() -> bool:
    """入场 10、止损 9.5（风险 0.5）、目标 3R = 11.5。

    造一根 `low ≤ 9.5` **且** `high ≥ 11.5` 的 bar —— 日线**看不到日内先后**，
    ⇒ 必须按**保守**处理（判止损），否则等于偷偷用未来信息。
    """
    bars = [(10, 10, 10, 10), (10, 12.0, 9.0, 11.0)] + [(11, 11, 11, 11)] * 3
    r = _run(bars, [("d1", SYM, 9.5)],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=3.0, max_hold_days=3))
    t = r.trades[0]
    ok = t.exit_reason == EXIT_STOP
    print(f"{'[PASS]' if ok else '[FAIL]'} 同 bar 冲突 ⇒ 判止损（实际 {t.exit_reason}）")
    return ok


# ── ④ R 定仓 ─────────────────────────────────────────────────────────────

def test_position_size_follows_r_rule() -> bool:
    """股数 = `净值 × 1% ÷ (入场价 − 止损价)`（他的原公式，行 81–83）。

    入场 100、止损 95 ⇒ 风险/股 = 5；净值 100 万 × 1% = 1 万 ⇒ **2000 股**。
    用 `return_pct` 反推：风险 1 万 ÷ (2000×100=20 万名义) = 5% = 止损幅度 ✓
    """
    bars = [(100, 100, 100, 100)] * 2 + [(100, 100, 95.0, 95.0)] + [(95, 95, 95, 95)] * 3
    r = _run(bars, [("d1", SYM, 95.0)],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=5))
    t = r.trades[0]
    notional = t.shares * t.entry_price
    ok = math.isclose(t.shares, 2000.0, rel_tol=1e-9) and math.isclose(
        notional, 200_000.0, rel_tol=1e-9)
    print(f"{'[PASS]' if ok else '[FAIL]'} R 定仓：{t.shares:.1f} 股 / 名义 {notional:,.0f}"
          f"（应 2000 股 / 200,000）")
    return ok


def test_r_multiple_is_minus_one_on_clean_stop() -> bool:
    """干净打止损（无跳空）⇒ `r_multiple` 应 ≈ **−1**（扣成本后略差）。"""
    bars = [(100, 100, 100, 100), (100, 100, 95.0, 95.0)] + [(95, 95, 95, 95)] * 3
    r = _run(bars, [("d1", SYM, 95.0)],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=5))
    got = r.trades[0].r_multiple
    ok = -1.02 < got < -0.99
    print(f"{'[PASS]' if ok else '[FAIL]'} 干净止损的 R 倍数 ≈ −1（实际 {got:.4f}）")
    return ok


# ── ⑤ 持仓位与曝险上限 ───────────────────────────────────────────────────

def test_max_positions_blocks_extra_entries() -> bool:
    """超过 `max_positions` 的候选被**跳过并计数**（不是静默丢弃）。"""
    r = _run([(100, 100, 100, 100)] * 6,
             [("d1", s, 95.0) for s in "ABCD"], symbols=tuple("ABCD"),
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99),
             account=AccountPolicy(max_positions=2, cost_rate=COST))
    ok = r.skipped_no_slot == 2 and len({t.symbol for t in r.trades}) == 2
    print(f"{'[PASS]' if ok else '[FAIL]'} 持仓位上限：跳过 {r.skipped_no_slot} 只"
          f"（应 2），成交 {len({t.symbol for t in r.trades})} 只（应 2）")
    return ok


def test_exposure_cap_blocks_entry() -> bool:
    """止损距离 1% ⇒ 单笔名义 = 净值（100%），第二笔就该被**曝险上限**挡住。"""
    r = _run([(100, 100, 100, 100)] * 6,
             [("d1", "A", 99.0), ("d1", "B", 99.0)], symbols=("A", "B"),
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99),
             account=AccountPolicy(max_total_exposure=1.0, cost_rate=COST))
    ok = r.skipped_exposure == 1
    print(f"{'[PASS]' if ok else '[FAIL]'} 曝险上限挡住超杠杆的入场"
          f"（跳过 {r.skipped_exposure}，应 1）")
    return ok


# ── ⑥ 部分止盈 + 止损移到入场点 ──────────────────────────────────────────

def test_partial_target_then_breakeven_stop() -> bool:
    """入场 100、止损 95（风险 5）、3R = 115。

    第二天 `high = 116` ⇒ 触发部分止盈；此后止损应上移到 **100**。
    再一天 `low = 99` ⇒ 应**在 100 出场**（不是 95）。
    """
    bars = [(100, 100, 100, 100),      # d1 信号
            (100, 100, 100, 100),      # d2 入场（开盘 100）
            (100, 116, 100, 114),      # d3 触 3R ⇒ 部分止盈 + 止损移到 100
            (101, 101, 99, 100),       # d4 low 99 触 100 ⇒ 剩下的在 100 出场
            (100, 100, 100, 100)]
    r = _run(bars, [("d1", SYM, 95.0)],
             exit_policy=ExitPolicy(target_r=3.0, use_ma_exit=False, max_hold_days=9))
    t = r.trades[0]
    ok = (t.exit_reason == EXIT_STOP and math.isclose(t.exit_price, 100.0, rel_tol=1e-9)
          and t.r_multiple > 0)
    print(f"{'[PASS]' if ok else '[FAIL]'} 部分止盈后止损移到入场价"
          f"（{t.exit_reason} @ {t.exit_price}，R={t.r_multiple:.3f}）")
    return ok


# ── ⑦ 输出完整性 ─────────────────────────────────────────────────────────

def test_equity_curve_is_recorded() -> bool:
    """★ **必须同时有交易清单与净值曲线** —— 否则回撤都算不了（qsx 的输入契约）。"""
    bars = [(100, 100, 100, 100)] * 6
    r = _run(bars, [("d1", SYM, 99.0)],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=2))
    ok = (len(r.equity_days) == 6 and len(r.equity_values) == 6
          and len(r.daily_exposure) == 6)
    print(f"{'[PASS]' if ok else '[FAIL]'} 净值曲线完整（{len(r.equity_values)} 天）")
    return ok


def test_reproducible() -> bool:
    bars = [(100, 101, 99, 100), (100, 106, 99, 105), (105, 108, 104, 107),
            (107, 109, 105, 106), (106, 107, 100, 101), (101, 102, 100, 101)] * 2
    a = _run(bars, [("d1", SYM, 98.0)], exit_policy=ExitPolicy(max_hold_days=4))
    b = _run(bars, [("d1", SYM, 98.0)], exit_policy=ExitPolicy(max_hold_days=4))
    ok = a.trades == b.trades and a.equity_values == b.equity_values
    print(f"{'[PASS]' if ok else '[FAIL]'} 同输入 ⇒ 逐位相同（可复现）")
    return ok


# ── ⑧ ★ 跨引擎一致性（"两种模型不是两个真相"的唯一凭据）────────────────

def test_cross_engine_consistency_with_pnl_engine() -> bool:
    """★ 与 `pnl_engine` 在**重叠情形**下必须对得上。

    构造一个两边都能表达的场景：**只做多 / 不设止损与止盈 / 均线出场关掉 /
    固定持有 H 根**。取 `open == close`（消除开盘/收盘的口径差），
    然后：
      · 本模拟器：`(出场价 / 入场价) − 1`
      · `pnl_engine`：`exp(Σ posᵗ × log_retᵗ) − 1`（同一段持仓）**且不计成本**
    ⇒ 两者必须一致。**这就是"第二份 PnL 实现"的合法性凭据**（承铁律 R1）。

    ⚠️ 为什么必须留这条测试：一旦两条路径悄悄分叉，**训练时钻的空子在审计时还会再钻一次**，
       而两边**各自看起来都正常**。
    """
    n = 12
    path = [100.0, 103.0, 106.0, 104.0, 109.0, 112.0,
            115.0, 111.0, 118.0, 121.0, 119.0, 124.0]
    bars = [(p, p, p, p) for p in path]           # open == high == low == close

    r = _run(bars, [("d1", SYM, 50.0)],           # 止损远在下方 ⇒ 永不触发
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=999, max_hold_days=8),
             account=AccountPolicy(cost_rate=0.0))
    t = r.trades[0]
    sim_ret = t.exit_price / t.entry_price - 1.0

    # pnl_engine 侧：入场在 entry_bar，出场在 exit_bar（open==close ⇒ 口径一致）
    dates = [f"d{i + 1}" for i in range(n)]
    a = dates.index(t.entry_day)
    b = dates.index(t.exit_day)
    closes = np.array(path)
    log_ret = np.zeros(n)                       # ⚠️ 首格必须 0（不是 NaN）——
    log_ret[1:] = np.log(closes[1:] / closes[:-1])   # `0 × NaN = NaN` 会污染整个求和
    # 让 `pos * log_ret[t]` 的求和恰好等于 log(close[exit] / close[entry])：
    #   取 pos[t] = 1 于 t ∈ (a, b]，ret = log(close_t / close_{t-1})
    positions = [0.0] * n
    for k in range(a + 1, b + 1):
        positions[k] = 1.0
    pnl = pnl_engine.per_bar_pnl(positions, list(log_ret), cost_rate=0.0)
    engine_ret = math.exp(sum(pnl)) - 1.0

    ok = math.isclose(sim_ret, engine_ret, rel_tol=1e-9)
    print(f"{'[PASS]' if ok else '[FAIL]'} 跨引擎一致性：模拟器 {sim_ret:.6%} vs "
          f"pnl_engine {engine_ret:.6%}")
    return ok


def test_cross_engine_consistency_includes_cost() -> bool:
    """带上成本后两边也应一致（差异仅来自成本的复合方式，量级 < 1e-6）。"""
    n = 10
    path = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0,
            112.0, 114.0, 116.0, 118.0]
    bars = [(p, p, p, p) for p in path]
    r = _run(bars, [("d1", SYM, 50.0)],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=999, max_hold_days=7),
             account=AccountPolicy(cost_rate=COST))
    t = r.trades[0]
    sim_ret = t.return_pct

    dates = [f"d{i + 1}" for i in range(n)]
    a, b = dates.index(t.entry_day), dates.index(t.exit_day)
    closes = np.array(path)
    log_ret = np.zeros(n)
    log_ret[1:] = np.log(closes[1:] / closes[:-1])
    positions = [0.0] * n
    for k in range(a + 1, b + 1):
        positions[k] = 1.0
    engine_ret = math.exp(sum(
        pnl_engine.per_bar_pnl(positions, list(log_ret), cost_rate=COST))) - 1.0
    ok = abs(sim_ret - engine_ret) < 1e-4
    print(f"{'[PASS]' if ok else '[FAIL]'} 跨引擎一致性（含成本）：模拟器 "
          f"{sim_ret:.6%} vs pnl_engine {engine_ret:.6%}")
    return ok


# ── ⑨ 限价单（他入场②③ 用得到）────────────────────────────────────────

def test_limit_order_fills_when_price_touches() -> bool:
    """限价 99：次日 `low = 98 ≤ 99` ⇒ 成交；价 = `min(99, 开盘 100)` = **99**。"""
    bars = [(100, 100, 100, 100),      # d1 信号（限价 99）
            (100, 101, 98, 100),       # d2 low 98 碰到限价
            (100, 100, 100, 100)]
    r = _run(bars, [{"day": "d1", "symbol": SYM, "stop_price": 90.0,
                     "limit_price": 99.0, "valid_days": 2}],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=1))
    got = r.trades[0].entry_price if r.trades else float("nan")
    ok = math.isclose(got, 99.0, rel_tol=1e-12)
    print(f"{'[PASS]' if ok else '[FAIL]'} 限价单碰到即成交 @ {got}（应 99.0）")
    return ok


def test_limit_order_uses_better_open() -> bool:
    """限价 99，但次日**开盘就 96**（更优）⇒ 成交价应是 **96**（不是 99）。"""
    bars = [(100, 100, 100, 100), (96, 97, 95, 96), (96, 96, 96, 96)]
    r = _run(bars, [{"day": "d1", "symbol": SYM, "stop_price": 90.0,
                     "limit_price": 99.0, "valid_days": 2}],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=1))
    got = r.trades[0].entry_price if r.trades else float("nan")
    ok = math.isclose(got, 96.0, rel_tol=1e-12)
    print(f"{'[PASS]' if ok else '[FAIL]'} 开盘更优 ⇒ 按开盘成交 @ {got}（应 96.0）")
    return ok


def test_limit_order_expires_and_is_counted() -> bool:
    """★ 他的入场②：「**超过两天没回撤 ⇒ 放弃**」—— 没碰到就**到期作废并计数**。"""
    bars = [(100, 100, 100, 100)] * 5          # 价格一直在 100，限价 99 永不成交
    r = _run(bars, [{"day": "d1", "symbol": SYM, "stop_price": 90.0,
                     "limit_price": 99.0, "valid_days": 2}],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=2))
    ok = len(r.trades) == 0 and r.skipped_expired == 1
    print(f"{'[PASS]' if ok else '[FAIL]'} 限价单到期作废（成交 {len(r.trades)} 笔，"
          f"到期 {r.skipped_expired}，应 0 笔 / 1 到期）")
    return ok


# ── ⑩ 曝险策略（他的 §2.2「四阶段」）──────────────────────────────────

class _OneSlotExposure:
    """只允许 1 个持仓位（用来验证状态依赖的 `max_positions` 真的生效）。"""

    def settings(self, state, default):
        from trade_simulator import ExposureSettings
        return ExposureSettings(max_positions=1,
                                risk_fraction=default.risk_fraction,
                                exit_policy=default.exit_policy,
                                stage="capped")


def test_exposure_policy_controls_max_positions() -> bool:
    r = _run([(100, 100, 100, 100)] * 6,
             [("d1", s, 95.0) for s in "ABC"], symbols=tuple("ABC"),
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99),
             exposure=_OneSlotExposure())
    ok = len({t.symbol for t in r.trades}) == 1 and r.skipped_no_slot == 2
    print(f"{'[PASS]' if ok else '[FAIL]'} 曝险策略能改 `max_positions`"
          f"（成交 {len({t.symbol for t in r.trades})} 只，跳过 {r.skipped_no_slot}）")
    return ok


class _NoExitOverrideExposure:
    def settings(self, state, default):
        from trade_simulator import ExposureSettings
        # 关掉均线出场 ⇒ 若当日设置生效，持仓就不会被均线打掉
        return ExposureSettings(max_positions=default.max_positions,
                                risk_fraction=default.risk_fraction,
                                exit_policy=ExitPolicy(use_ma_exit=False,
                                                       target_r=999,
                                                       max_hold_days=99),
                                stage="hold")


def test_exposure_policy_can_change_exit_policy() -> bool:
    """★ 他阶段④的「**2–3 天部分获利**」要求出场规则**逐日可变**。"""
    bars = [(100, 100, 100, 100), (100, 101, 99, 100), (100, 101, 99, 99),
            (99, 100, 98, 99), (99, 100, 98, 99)]
    ma = None
    r0 = _run(bars, [("d1", SYM, 90.0)],
              exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=1))
    r1 = _run(bars, [("d1", SYM, 90.0)],
              exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=1),
              exposure=_NoExitOverrideExposure())
    ok = (len(r0.trades) == 1 and len(r1.trades) == 1
          and r1.trades[0].hold_days > r0.trades[0].hold_days)
    print(f"{'[PASS]' if ok else '[FAIL]'} 曝险策略能改**当日出场规则**"
          f"（持有 {r0.trades[0].hold_days} → {r1.trades[0].hold_days} 天）")
    return ok


def test_time_based_partial_takes_half_at_close() -> bool:
    """★ 时间型部分止盈（他阶段④的「**2–3 天部分获利**」）。

    入场 100 / 止损 95（风险 5）：d4 收盘 110 = **+2R** 且持有已 2 天
      ⇒ 按当日**收盘**减半，止损上移到 100
      ⇒ d5 打到 100（= 0R）⇒ 总 R = 0.5×2 + 0.5×0 = **+1.0**

    对照组（关掉时间止盈）：一路持到 d5 收盘 100 ⇒ R ≈ **0**。两者必须不同。
    """
    bars = [(100, 100, 100, 100),      # d1 信号
            (100, 100, 100, 100),      # d2 入场 @100
            (100, 104, 100, 104),      # d3 收盘 104
            (100, 110, 100, 110),      # d4 收盘 110 = +2R ⇒ 时间止盈
            (101, 101, 100, 100)]      # d5 回到 100 ⇒ 打中移到 100 的止损
    base = ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=4)
    fast = ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=4,
                      partial_after_days=2)
    slow_r = _run(bars, [("d1", SYM, 95.0)], exit_policy=base).trades[0].r_multiple
    got = _run(bars, [("d1", SYM, 95.0)], exit_policy=fast).trades[0].r_multiple
    ok = (abs(got - 1.0) < 0.02) and got > slow_r + 0.5
    print(f"{'[PASS]' if ok else '[FAIL]'} 时间型部分止盈：R {slow_r:.3f} → {got:.3f}"
          f"（应 ≈ 1.0）")
    return ok


def test_full_partial_through_target_closes_the_position() -> bool:
    """★ `partial_fraction=1.0`（到目标**全出**）⇒ 仓位必须**被平掉**。

    ⚠️ 这个 bug 是**敏感性扫描**才暴露出来的：股数减到 0 之后仓位**没被删除**，
       它带着 0 股继续占着持仓位、一直挂到 `max_hold_days` ⇒
       **后面几十个信号根本进不来**。
       症状极具欺骗性：那一行显示 26 笔 / Sharpe 0.95 / MDD −5.46%，
       **看起来像"这个参数最好"**，其实是仓位没平。

    这里：入场 100 / 止损 95（风险 5）/ 3R = 115。
    d3 的 `high = 116 ≥ 115` ⇒ 应当**当天就收尾**（`target_final`），
    而不是拖到 60 天上限。
    """
    bars = [(100, 100, 100, 100), (100, 100, 100, 100),
            (100, 116, 100, 114)] + [(114, 114, 114, 114)] * 6
    r = _run(bars, [("d1", SYM, 95.0)],
             exit_policy=ExitPolicy(target_r=3.0, use_ma_exit=False,
                                    partial_fraction=1.0, max_hold_days=60))
    t = r.trades[0] if r.trades else None
    ok = (t is not None and t.exit_reason == "target_final" and t.hold_days <= 2)
    print(f"{'[PASS]' if ok else '[FAIL]'} 全出后仓位被平掉"
          f"（{t.exit_reason if t else '无交易'}，持有 {t.hold_days if t else '-'} 天）")
    return ok


# ── ⑪ ★ 对账：R 必须能从**记录字段独立重算**（最强的检查）──────────────

def _reconcile(t, cost_rate: float) -> float:
    """★ 用**独立公式**从记录的字段重算 R（**与模拟器内部实现无关**，照定义写）。

    ```
    没做过部分止盈：
        gross = (出场价 − 入场价) × 股数
        cost  = (入场价×股数 + 出场价×股数) × 费率        ← 进、出各收一次
    做过部分止盈（两笔腿）：
        gross = (部分价 − 入场价) × 部分股数
              + (出场价 − 入场价) × (股数 − 部分股数)
        cost  = (入场价×股数 + 部分价×部分股数
                 + 出场价×(股数−部分股数)) × 费率
    R = (gross − cost) / (股数 × (入场价 − 初始止损))
    ```
    """
    e, xp, sh, stop = t.entry_price, t.exit_price, t.shares, t.initial_stop
    if t.took_partial:
        pp, ps = t.partial_price, t.partial_shares
        rest = sh - ps
        gross = (pp - e) * ps + (xp - e) * rest
        cost = (e * sh + pp * ps + xp * rest) * cost_rate
    else:
        gross = (xp - e) * sh
        cost = (e * sh + xp * sh) * cost_rate
    return (gross - cost) / (sh * (e - stop))


def test_trade_records_reconcile() -> bool:
    """★ **每一笔**的 `r_multiple` 都必须能从记录字段独立算出来（含部分止盈的）。

    ⇒ 这条不过，说明**记的账和算的账对不上** —— 那所有报告都别信。
    """
    # ⚠️ `_make` 给**每个标的相同的 bar** ⇒ 一次跑只能出一种形态。
    #    所以跑**两次**：一次只涨（出部分止盈），一次只跌（出纯止损）——
    #    **两类成交都要被对账覆盖**，只测一类等于没测另一半的记账。
    syms = ("AAA", "BBB", "CCC", "DDD")
    # 止损距取 **5%**（不是 1%）—— 否则单笔占满 100% 名义，曝险上限只放 1 笔进来。
    rally = [(100, 101, 99, 100),     # d1 信号（止损 = 95）
             (100, 108, 99, 107),     # d2 入场 @100；high 108 未到 3R(115)
             (100, 120, 100, 118),    # d3 high 120 ≥ 115 ⇒ **部分止盈**（卖一半）
             (118, 119, 94, 95),      # d4 low 94 ⇒ 打到（已上移到 100 的）止损
             (95, 95, 94, 94)]
    fall = [(100, 101, 99, 100),      # d1 信号
            (100, 100, 94, 94),       # d2 入场 @100，low 94 ⇒ 直接打止损
            (94, 94, 93, 93), (93, 93, 93, 93)]
    ep = ExitPolicy(use_ma_exit=False, target_r=3.0, partial_fraction=0.5,
                    breakeven_after_partial=True, max_hold_days=5)
    runs = [_run(rally, [("d1", s, 95.0) for s in syms], symbols=syms,
                 exit_policy=ep),
            _run(fall, [("d1", s, 95.0) for s in syms], symbols=syms,
                 exit_policy=ep)]
    trades = [t for r in runs for t in r.trades]
    partials = sum(1 for t in trades if t.took_partial)
    checked = bad = 0
    for t in trades:
        expect = _reconcile(t, COST)
        checked += 1
        if abs(expect - t.r_multiple) > 1e-9:
            bad += 1
            print(f"   ✗ {t.symbol} {t.entry_day}: 记录 {t.r_multiple:.6f} "
                  f"vs 重算 {expect:.6f}")
    # 两类**都必须出现**，否则这条测试有一半是空的
    ok = (checked > 0 and bad == 0 and partials > 0
          and partials < checked)
    print(f"{'[PASS]' if ok else '[FAIL]'} 逐笔对账：{checked} 笔"
          f"（{partials} 笔部分止盈 / {checked - partials} 笔纯止损），不符 {bad} 笔")
    return ok


def test_zero_partial_does_not_move_stop_to_breakeven() -> bool:
    """★ `partial_fraction=0`（**一股都不卖**）**不准**把止损移到保本。

    ⚠️ 漏了这道门槛等于**白送一次止损上移**：没落袋任何利润，风险却先降了。
    """
    bars = [(100, 100, 100, 100),      # d1 信号（止损 95）
            (100, 100, 100, 100),      # d2 入场 @100
            (100, 116, 100, 110),      # d3 high 116 ≥ 3R(115) —— 但只卖 0 股
            (99, 100, 98, 99),         # d4 low 98：止损若被移到 100 就会在这被打掉
            (99, 99, 99, 99), (99, 99, 99, 99)]
    r = _run(bars, [("d1", SYM, 95.0)],
             exit_policy=ExitPolicy(target_r=3.0, use_ma_exit=False,
                                    partial_fraction=0.0,
                                    breakeven_after_partial=True,
                                    max_hold_days=3))
    t = r.trades[0]
    ok = t.exit_reason == "time_cap" and abs(t.exit_price - 99.0) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} 卖 0 股不移动止损"
          f"（{t.exit_reason} @ {t.exit_price}，应 time_cap @ 99.0）")
    return ok


def test_full_partial_records_the_actual_fill_price() -> bool:
    """★ 全出那条路径记的**成交价必须是实际成交价**（`max(止盈价, 开盘)`），不是收盘价。

    入场 100 / 止损 95 ⇒ 3R = 115。d3 `open=100, high=130, close=120`
    ⇒ 实际成交在 **115**（`max(115, 100)`），而**不是**收盘 120。
    """
    bars = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 130, 100, 120)]
    r = _run(bars, [("d1", SYM, 95.0)],
             exit_policy=ExitPolicy(target_r=3.0, use_ma_exit=False,
                                    partial_fraction=1.0))
    t = r.trades[0]
    ok = abs(t.exit_price - 115.0) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} 全出记的是实际成交价"
          f"（{t.exit_price}，应 115.0 而非收盘 120.0）")
    return ok


def test_static_exposure_is_the_default() -> bool:
    r = _run([(100, 100, 100, 100)] * 4, [("d1", SYM, 95.0)],
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=1))
    ok = len(r.stages) == 4 and set(r.stages) == {"static"}
    print(f"{'[PASS]' if ok else '[FAIL]'} 默认无曝险策略 ⇒ 每天都是 `static`")
    return ok


# ── ⑪ 复审 G2/G3：对齐校验 + 末日信号计数 ─────────────────────────────

def test_misaligned_ma_levels_raises() -> bool:
    """★ `ma_exit_level` 的**行序/列序**与 `dates`/`symbols` 不一致 ⇒ **报错**。

    ⚠️ 原来直接 `.to_numpy()` ⇒ 传错顺序会**静默错位**：出场均线每天读到
       **别人的**水平线，不报错、只是全错。（复审 G2）
    """
    dates, syms, frames = _make([(100, 100, 100, 100)] * 5)
    bad = pd.DataFrame(np.nan, index=list(dates)[::-1],     # ← 行序反了
                       columns=list(syms))
    try:
        simulate(dates, syms, frames,
                 pd.DataFrame([("d1", SYM, 95.0)],
                              columns=["day", "symbol", "stop_price"]),
                 strategy_name="t", strategy_params={}, ma_exit_level=bad,
                 exit_policy=ExitPolicy(), account=AccountPolicy(cost_rate=COST))
    except ValueError as e:
        ok = "行序" in str(e)
        print(f"{'[PASS]' if ok else '[FAIL]'} 均线宽表行序错 ⇒ 报错")
        return ok
    print("[FAIL] 均线宽表行序错 ⇒ 竟然没报错（会静默错位）")
    return False


def test_last_bar_signal_is_counted_not_dropped() -> bool:
    """★ **末日的信号**：`arrive` 越过数据末尾 ⇒ 永远等不到成交。

    原来**静默丢弃、不计入任何计数**（复审 G3）⇒ 现在必须计入
    `skipped_after_end`（承 P1：不许静默丢东西）。
    """
    bars = [(100, 100, 100, 100)] * 4
    dates = [f"d{i + 1}" for i in range(len(bars))]
    r = _run(bars, [(dates[-1], SYM, 95.0)],          # ← 信号在**最后一天**
             exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=2))
    ok = len(r.trades) == 0 and r.skipped_after_end == 1
    print(f"{'[PASS]' if ok else '[FAIL]'} 末日信号被**计数**而不是静默丢弃"
          f"（成交 {len(r.trades)}，末日跳过 {r.skipped_after_end}，应 0 / 1）")
    return ok


# ── ⑫ ★ 成交**逻辑**对账（复审第 9 条）────────────────────────────────

def test_reconcile_fills_checks_the_rules_not_just_the_arithmetic() -> bool:
    """★ `reconcile_fills` 必须从 bar **独立反推**成交价 —— 验"**算得对不对**"。

    ⚠️ 与 `reconcile` 的分工（复审第 9 条的原话）：

    > `reconcile()` 验证的是「**我记的和我算的一样**」，
    > **不是**「我算的和规则一样」。

    这里造一笔**跳空越过止损**：入场 100 / 止损 95，
    出场日 `open = 90`（跳空低开）、`low = 88` ⇒
    按规则成交价应是 **`min(95, 90) = 90`**（不是 95，也不是 88）。
    """
    from trade_simulator import reconcile_fills

    bars = [(100, 100, 100, 100),      # d1 信号（止损 95）
            (100, 101, 99, 100),       # d2 入场 @100
            (90, 92, 88, 91)]          # d3 跳空低开 ⇒ 按 min(95, 90) = 90 成交
    dates, syms, frames = _make(bars)
    r = simulate(dates, syms, frames,
                 pd.DataFrame([("d1", SYM, 95.0)],
                              columns=["day", "symbol", "stop_price"]),
                 strategy_name="t", strategy_params={},
                 ma_exit_level=pd.DataFrame(np.nan, index=list(dates),
                                            columns=list(syms)),
                 exit_policy=ExitPolicy(use_ma_exit=False, target_r=99,
                                        max_hold_days=3),
                 account=AccountPolicy(cost_rate=COST))
    chk = reconcile_fills(r, dates, syms, frames)
    ok = chk["bad"] == 0 and chk["checked_entry"] >= 1 and chk["checked_stop"] >= 1
    print(f"{'[PASS]' if ok else '[FAIL]'} 成交逻辑对账：入场 {chk['checked_entry']} / "
          f"止损 {chk['checked_stop']} 笔，不符 {chk['bad']}"
          f"（成交价 {r.trades[0].exit_price if r.trades else '-'}，应 90.0）")
    return ok


def test_reconcile_fills_uses_breakeven_stop_after_partial() -> bool:
    """★ 做过部分止盈的 ⇒ 止损应已移到**保本**，对账要按保本算。

    入场 100 / 止损 95 / 3R = 115。d3 `high=120 ≥ 115` ⇒ 部分止盈 + 止损移到 100。
    d4 `open=98, low=97` ⇒ 打中**保本止损 100** ⇒ 成交价 `min(100, 98) = 98`。
    """
    from trade_simulator import reconcile_fills

    bars = [(100, 100, 100, 100), (100, 101, 99, 100), (100, 120, 100, 118),
            (98, 99, 97, 98), (98, 98, 98, 98)]
    dates, syms, frames = _make(bars)
    r = simulate(dates, syms, frames,
                 pd.DataFrame([("d1", SYM, 95.0)],
                              columns=["day", "symbol", "stop_price"]),
                 strategy_name="t", strategy_params={},
                 ma_exit_level=pd.DataFrame(np.nan, index=list(dates),
                                            columns=list(syms)),
                 exit_policy=ExitPolicy(use_ma_exit=False, target_r=3.0,
                                        partial_fraction=0.5,
                                        breakeven_after_partial=True,
                                        max_hold_days=4),
                 account=AccountPolicy(cost_rate=COST))
    chk = reconcile_fills(r, dates, syms, frames)
    ok = chk["bad"] == 0 and chk["checked_stop"] >= 1
    print(f"{'[PASS]' if ok else '[FAIL]'} 保本止损也被对账覆盖"
          f"（止损出场 {chk['checked_stop']} 笔，不符 {chk['bad']}）")
    return ok


def test_reconcile_fills_covers_close_settled_exits() -> bool:
    """★ **"收盘结算"类出场也要被对账**（治 TD-05-11）。

    第一版 `reconcile_fills` 只对**入场**与**止损** —— 而 `EXIT_*` 是**封闭枚举**。
    "覆盖 2/7 却报『成交对账通过』"正是本仓反复出事的形态
    （同源：`audit_conditions` 的 `if k in prod` 静默跳过）。

    这里造一笔**跌破均线**出场：d3 `close = 95 < ma_exit_level = 98`
    ⇒ `EXIT_MA_BREAK`，按规则成交价 = **当日收盘 95**（不是开盘 98、不是均线值 98）。
    """
    from trade_simulator import reconcile_fills

    bars = [(100, 100, 100, 100),      # d1 信号（止损 95）
            (100, 101, 99, 100),       # d2 入场 @100
            (98, 99, 97, 95),          # d3 收盘 95 跌破均线 98 ⇒ 按收盘 95 成交
            (95, 96, 94, 95)]
    dates, syms, frames = _make(bars)
    ma = pd.DataFrame(np.nan, index=list(dates), columns=list(syms))
    ma.loc[dates[2], SYM] = 98.0                    # 只有 d3 有均线值
    r = simulate(dates, syms, frames,
                 pd.DataFrame([("d1", SYM, 95.0)],
                              columns=["day", "symbol", "stop_price"]),
                 strategy_name="t", strategy_params={}, ma_exit_level=ma,
                 exit_policy=ExitPolicy(use_ma_exit=True, target_r=99,
                                        max_hold_days=9),
                 account=AccountPolicy(cost_rate=COST))
    chk = reconcile_fills(r, dates, syms, frames)
    kinds = [t.exit_reason for t in r.trades]
    ok = (chk["bad"] == 0 and chk["checked_close"] >= 1
          and kinds == ["ma_break"]
          and abs(r.trades[0].exit_price - 95.0) < 1e-9)
    print(f"{'[PASS]' if ok else '[FAIL]'} 收盘结算类出场也被对账"
          f"（出场 {kinds}，收盘对账 {chk['checked_close']} 笔，不符 {chk['bad']}）")
    return ok


def test_reconcile_fills_rejects_unknown_exit_reason() -> bool:
    """★ **穷举守卫**（治 TD-05-11）：出现没登记的出场原因 ⇒ **报错**，不许静默漏对账。

    为什么必须有这条：第一版的病根就是"审不了就静默跳过"。
    ⇒ 以后新增出场原因时，**强制**作者把它归类到
      `_CLOSE_SETTLED`（可反推）或 `_UNVERIFIABLE_FILLS`（显形）—— 漏了就跑不动。
    """
    from dataclasses import replace as _replace

    from trade_simulator import reconcile_fills

    bars = [(100, 100, 100, 100),      # d1 信号（止损 95）
            (100, 101, 99, 100),       # d2 入场 @100
            (90, 92, 88, 91)]          # d3 跳空 ⇒ 止损出场
    dates, syms, frames = _make(bars)
    r = simulate(dates, syms, frames,
                 pd.DataFrame([("d1", SYM, 95.0)],
                              columns=["day", "symbol", "stop_price"]),
                 strategy_name="t", strategy_params={},
                 ma_exit_level=pd.DataFrame(np.nan, index=list(dates),
                                            columns=list(syms)),
                 exit_policy=ExitPolicy(use_ma_exit=False, target_r=99,
                                        max_hold_days=3),
                 account=AccountPolicy(cost_rate=COST))
    bogus = _replace(r, trades=[_replace(r.trades[0], exit_reason="bogus")])
    try:
        reconcile_fills(bogus, dates, syms, frames)
    except ValueError as e:
        ok = "不认识" in str(e)
        print(f"{'[PASS]' if ok else '[FAIL]'} 未知出场原因 ⇒ 报错（不静默漏对账）")
        return ok
    print("[FAIL] 未知出场原因 ⇒ 竟然没报错（静默漏对账）")
    return False


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            if not fn():
                failed += 1
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {fn.__name__}: 抛异常")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
