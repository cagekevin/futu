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
from trade_simulator import SimulationResult, Trade  # noqa: E402


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
        equity_values=tuple(eq), skipped_no_slot=0, skipped_exposure=0)


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
