"""`trade_metrics` —— 测试。

盯三件最容易写错、且**会直接误导结论**的事：

1. **稳健性**（`robustness`）：去掉最好的 N 笔后还剩多少。
   ⚠️ 它是**诊断**，不是判决 —— 单独看会把他"牺牲胜率换赔率"的**正常形态**读成缺陷。
2. **蒙特卡洛必须是 bootstrap**：第一版写"重排成交顺序" ⇒
   **总和与顺序无关** ⇒ "最终总 R"按构造是常数（三个分位数完全相同）——
   看起来像"极其稳定"，其实是**方法没在测东西**。
3. **基准剥离**：`beta` 与**剥离后的 IR** 必须都有（否则把行情当本事）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import trade_metrics as tm  # noqa: E402
from trade_simulator import SKIP_REASONS, SimulationResult, Trade  # noqa: E402


def _result(rs, *, reasons=None, equity=None) -> SimulationResult:
    reasons = reasons or ["stop"] * len(rs)
    trades = tuple(
        Trade(symbol=f"S{i}", entry_day=f"d{i}", entry_price=100.0,
              initial_stop=95.0, exit_day=f"d{i + 1}", exit_price=100.0,
              shares=2000.0, r_multiple=float(r), return_pct=float(r) * 0.05,
              exit_reason=reasons[i], hold_days=3)
        for i, r in enumerate(rs))
    eq = equity or [1_000_000.0 * (1.0 + 0.001 * i) for i in range(len(rs) + 1)]
    return SimulationResult(
        strategy="t", params_fingerprint="x", trades=trades,
        equity_days=tuple(f"d{i}" for i in range(len(eq))),
        equity_values=tuple(eq), skipped={k: 0 for k in SKIP_REASONS})


# ── 稳健性 ───────────────────────────────────────────────────────────────

def test_robustness_breakeven_drop() -> bool:
    """8 笔小亏 + 2 笔大赢（总和 +3）⇒ **去掉 1 笔（最大那笔）就转负**。"""
    rb = tm.robustness(_result([-1.0] * 8 + [5.0, 6.0]))
    ok = rb["breakeven_drop"] == 1 and abs(rb["total_r_drop1"] - (-3.0)) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} 去掉 1 笔即转负（剩 {rb['total_r_drop1']} R）")
    return ok


def test_robustness_reports_drops() -> bool:
    """`drop_top` 里每个 N 都要有结果（含 N=0 = 原值）。

    `[1, 2, 3, −1]`：总和 5；去掉最好 1 笔（3）⇒ 2；去掉最好 3 笔（3+2+1=6）⇒ **−1**。
    """
    rb = tm.robustness(_result([1.0, 2.0, 3.0, -1.0]))
    ok = (abs(rb["total_r_drop0"] - 5.0) < 1e-9
          and abs(rb["total_r_drop1"] - 2.0) < 1e-9
          and abs(rb["total_r_drop3"] - (-1.0)) < 1e-9)
    print(f"{'[PASS]' if ok else '[FAIL]'} 去掉 0/1/3 笔："
          f"{rb['total_r_drop0']}/{rb['total_r_drop1']}/{rb['total_r_drop3']}")
    return ok


def test_robustness_flat_result_needs_dropping_all() -> bool:
    """全为正（各 1R，共 3 笔）⇒ **只有全去掉**才不再为正 ⇒ `breakeven_drop == 3`。"""
    rb = tm.robustness(_result([1.0, 1.0, 1.0]))
    ok = rb["breakeven_drop"] == 3
    print(f"{'[PASS]' if ok else '[FAIL]'} 全为正 ⇒ 要全去掉才不为正"
          f"（{rb['breakeven_drop']}，应 3）")
    return ok


def test_robustness_key_survives_k_larger_than_n() -> bool:
    """★ 当 `k > 笔数` 时，键名仍用**请求的 k** —— 否则调用方按 k 取值会 `KeyError`。

    （这个 bug 被 `test_render_report_runs` 抓到过：报告去取 `avg_r_drop5`
      而函数只产出了 `avg_r_drop4`。）
    """
    rb = tm.robustness(_result([1.0, -1.0]))
    ok = "avg_r_drop5" in rb and "total_r_drop10" in rb
    print(f"{'[PASS]' if ok else '[FAIL]'} k 大于笔数时键名仍按 k"
          f"（有 avg_r_drop5: {'avg_r_drop5' in rb}）")
    return ok


# ── ★ 蒙特卡洛必须是 bootstrap（不是重排）──────────────────────────────

def test_monte_carlo_is_bootstrap_not_shuffle() -> bool:
    """★ 分位数**必须不同** —— 若三个相同，说明写成了"重排"（方法没在测东西）。

    ⚠️ 这条正是被实测抓出来的：第一版 `p05 = p50 = p95 = 20.0`，
       因为**总和与顺序无关** ⇒ 按构造是常数。
    """
    rs = [-1.0] * 10 + [4.0] * 6
    mc = tm.monte_carlo(_result(rs), iterations=500, seed=1)
    spread = mc["p95"] - mc["p05"]
    ok = spread > 1.0 and mc["p05"] != mc["p50"]
    print(f"{'[PASS]' if ok else '[FAIL]'} 蒙特卡洛是重采样（"
          f"p05 {mc['p05']:.1f} / p50 {mc['p50']:.1f} / p95 {mc['p95']:.1f}）")
    return ok


def test_monte_carlo_reports_profit_ratio() -> bool:
    """`p_profit` = 重采样后仍赚钱的比例 —— 这条最该看。"""
    mc = tm.monte_carlo(_result([2.0] * 20), iterations=200, seed=1)
    ok = abs(mc["p_profit"] - 1.0) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} 全赢的成交 ⇒ 重采样后必赚"
          f"（{mc['p_profit'] * 100:.0f}%）")
    return ok


# ── 汇总与基准剥离 ───────────────────────────────────────────────────────

def test_summarize_includes_robustness() -> bool:
    m = tm.summarize(_result([1.0, -1.0, 2.0]))
    ok = "robustness" in m and "breakeven_drop" in m["robustness"]
    print(f"{'[PASS]' if ok else '[FAIL]'} summarize 带上了稳健性")
    return ok


def test_beta_stripping_is_reported() -> bool:
    """★ 有基准时必须同时给 `beta` 与**剥离后的 IR**（否则把行情当本事）。"""
    n = 60
    rets = np.array([0.01 if i % 2 else 0.0 for i in range(n)])
    eq = 1_000_000.0 * np.cumprod(np.concatenate([[1.0], 1.0 + rets]))
    m = tm.summarize(_result([1.0] * n, equity=list(eq)), benchmark=list(rets))
    ok = np.isfinite(m["beta"]) and np.isfinite(m["ir_stripped"])
    print(f"{'[PASS]' if ok else '[FAIL]'} 报告 beta={m['beta']:.3f} 与 "
          f"剥离后 IR={m['ir_stripped']:.3f}")
    return ok


def test_render_report_runs() -> bool:
    m = tm.summarize(_result([1.0, -1.0, 2.0, -1.0]))
    text = tm.render_report(m, tm.monte_carlo(_result([1.0, -1.0, 2.0, -1.0]),
                                              iterations=100, seed=1),
                            title="测试")
    ok = "R 倍数分布" in text and "稳健性" in text and "蒙特卡洛" in text
    print(f"{'[PASS]' if ok else '[FAIL]'} 报告渲染成功（{len(text)} 字符）")
    return ok


# ── ★★ 公式审计：用**手算已知答案**的序列，逐条验 `trade_metrics` ─────────
#
# 为什么单独做这一组：**报告里每一个数字都出自这里**。
# 公式错一个，前面所有结论都要重算 —— 而且**错得很安静**（不会报错，只会给错数）。

def test_max_drawdown_matches_hand_computed() -> bool:
    """净值 `100 → 120 → 90 → 130` ⇒ 最大回撤 = `90/120 − 1` = **−25%**。"""
    m = tm.summarize(_result([1.0], equity=[100.0, 120.0, 90.0, 130.0]))
    ok = abs(m["max_drawdown"] - (-0.25)) < 1e-12
    print(f"{'[PASS]' if ok else '[FAIL]'} 最大回撤手算对得上（{m['max_drawdown']:.6f}）")
    return ok


def test_cagr_matches_compound_definition() -> bool:
    """`100 → 121` 跨 **504 天**（= 2 个 252 天年）⇒ CAGR = `1.21^0.5 − 1` = **10%**。"""
    eq = list(np.linspace(100.0, 121.0, 505))
    m = tm.summarize(_result(list(np.zeros(504)), equity=eq))
    ok = abs(m["cagr"] - 0.10) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} CAGR 按复利定义（{m['cagr']:.8f}，应 0.1）")
    return ok


def test_sharpe_matches_definition() -> bool:
    """逐日收益 `+1% / −1%` 交替 ⇒ 均值 0 ⇒ **Sharpe = 0**（不是 n/a）。"""
    rets = [0.01 if i % 2 else -0.01 for i in range(200)]
    eq = list(1_000_000.0 * np.cumprod(np.concatenate([[1.0], 1.0 + np.array(rets)])))
    m = tm.summarize(_result(list(np.zeros(200)), equity=eq))
    ok = np.isfinite(m["sharpe"]) and abs(m["sharpe"]) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} 零均值收益 ⇒ Sharpe ≈ 0（{m['sharpe']:.3e}）")
    return ok


def test_beta_and_corr_are_exact_for_proportional_series() -> bool:
    """`策略收益 = 2 × 基准收益` ⇒ **beta = 2、corr = 1**（精确）。"""
    b = np.array([0.01, -0.02, 0.03, -0.01, 0.02, -0.015, 0.005, -0.025])
    r = 2.0 * b
    eq = list(1_000_000.0 * np.cumprod(np.concatenate([[1.0], 1.0 + r])))
    m = tm.summarize(_result(list(np.zeros(len(r))), equity=eq), benchmark=list(b))
    ok = abs(m["beta"] - 2.0) < 1e-9 and abs(m["corr"] - 1.0) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} beta / corr 精确（beta={m['beta']:.6f}, "
          f"corr={m['corr']:.6f}）")
    return ok


def test_ir_stripped_removes_benchmark_exposure() -> bool:
    """`策略 = beta × 基准 + alpha` ⇒ **剥离 beta 后应精确还原出 alpha 的 Sharpe**。

    这是"剥离 beta"这件事**唯一有意义的定义**：剥离后剩下的就是 alpha。

    ⚠️ **必须把 alpha 构造成与基准正交**（下面那两行）。
       第一版我用的是**独立随机**的 alpha —— 但有限样本下 `cov(alpha, b) ≠ 0`，
       样本 beta 自然会偏一点点，于是测试**假报失败**。
       正交化之后样本 beta **精确**等于真值，这条测试才在检验"剥离"而不是在检验抽样噪声。
    """
    rng = np.random.default_rng(7)
    b = rng.normal(0.0, 0.01, 500)
    b = b - b.mean()
    raw = rng.normal(0.0005, 0.005, 500)
    # ★ 只去掉**沿 b 的分量**（正交化），**保留 alpha 自身的均值** ——
    #   否则 alpha 均值被归零、剥离后的 Sharpe 恒为 0，这条测试就退化成"0 == 0"了。
    alpha = raw - (raw @ b) / (b @ b) * b
    beta_true = 1.4
    r = beta_true * b + alpha
    eq = list(1_000_000.0 * np.cumprod(np.concatenate([[1.0], 1.0 + r])))
    m = tm.summarize(_result(list(np.zeros(500)), equity=eq), benchmark=list(b))
    expect = float(alpha.mean() / alpha.std(ddof=1) * np.sqrt(252))
    ok = abs(m["beta"] - beta_true) < 1e-9 and abs(m["ir_stripped"] - expect) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} 剥离 beta 后还原 alpha 的 Sharpe"
          f"（beta={m['beta']:.6f}，IR剥离={m['ir_stripped']:.6f} vs 手算 {expect:.6f}）")
    return ok


def test_payoff_ratio_matches_definition() -> bool:
    """`+3, +1, −1, −1` ⇒ 平均赚 2 / 平均亏 1 ⇒ **盈亏比 = 2**。"""
    m = tm.summarize(_result([3.0, 1.0, -1.0, -1.0]))
    ok = abs(m["payoff_ratio"] - 2.0) < 1e-12 and abs(m["win_rate"] - 0.5) < 1e-12
    print(f"{'[PASS]' if ok else '[FAIL]'} 盈亏比 / 胜率手算对得上"
          f"（{m['payoff_ratio']:.4f} / {m['win_rate']:.2f}）")
    return ok


def test_equity_starts_at_initial_capital() -> bool:
    """★ `total_return` / `cagr` 的**基数是 `equity[0]`** ⇒ 它**必须等于初始资金**。

    ⚠️ 这是一条**隐性假设**（`summarize` 不知道初始资金是多少，只能拿首格当基数）。
       若哪天首格不是初始资金（例如第一天就开了仓），这两个数会**安静地偏掉**。
    """
    eq = [1_000_000.0] + [1_010_000.0] * 10
    m = tm.summarize(_result(list(np.zeros(10)), equity=eq))
    ok = abs(m["total_return"] - 0.01) < 1e-12
    print(f"{'[PASS]' if ok else '[FAIL]'} 基数是首格净值（总收益 {m['total_return']:.6f}）")
    return ok


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
