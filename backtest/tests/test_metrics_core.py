"""`metrics_core` —— 指标原语的测试。

## 为什么需要它（第三轮独立复审第 8 条）

`trade_metrics.py` 与 `performance_metrics.py` 是**同名不同口径的两份实现**：

| 指标 | `performance_metrics` | `trade_metrics` |
|---|---|---|
| 标准差 | **ddof 0** | **ddof 1** |
| 年化 | 算术 `mean × ppy` | 几何 CAGR |
| `max_drawdown` | **正数**（累计 PnL）| **负数**（净值比例）|

⇒ 而仓库铁律是「**PnL 只能有一份实现**」。
（讽刺的是 §3.3 说"抓到过 ddof 陷阱"，**同一个陷阱在两份 metrics 之间又存在一次**。）

⇒ 这一组测试钉住**三个约定的唯一来源**。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import metrics_core as mc  # noqa: E402
import performance_metrics as pm  # noqa: E402
import trade_metrics as tm  # noqa: E402


def test_ddof_is_defined_once() -> bool:
    """★ **`ddof` 只有一处**（`STD_DDOF`），且两份 metrics 都用它。

    手算：`[1, 2, 3, 4]` ⇒ 样本标准差（ddof=1）= `√(5/3)` ≈ 1.29099；
    总体标准差（ddof=0）= `√(5/4)` ≈ 1.11803。
    """
    a = [1.0, 2.0, 3.0, 4.0]
    expect = float(np.std(a, ddof=1))
    ok = (mc.STD_DDOF == 1 and abs(mc.std(a) - expect) < 1e-12
          and abs(pm._std(a) - expect) < 1e-12)          # noqa: SLF001
    print(f"{'[PASS]' if ok else '[FAIL]'} ddof 唯一来源 = {mc.STD_DDOF}"
          f"（{mc.std(a):.6f}，应 {expect:.6f}）")
    return ok


def test_drawdown_signs_are_named_apart() -> bool:
    """★ 回撤**符号跟着输入走**，且**名字带出来**：

    · `max_drawdown_from_equity` ⇒ **负数**（`−0.25` = 回撤 25%）
    · `max_drawdown_from_pnl`    ⇒ **正数**（单位同 PnL）
    """
    eq = [100.0, 120.0, 90.0, 130.0]
    pnl = list(np.diff(eq))
    a = mc.max_drawdown_from_equity(eq)
    b = mc.max_drawdown_from_pnl(pnl)
    ok = abs(a - (-0.25)) < 1e-12 and b > 0
    print(f"{'[PASS]' if ok else '[FAIL]'} 回撤符号：净值 {a:.4f}（应 −0.25）｜"
          f"PnL {b:.2f}（正）")
    return ok


def test_annualization_has_two_names() -> bool:
    """★ 年化**分两个名字**：几何（净值）vs 算术（逐笔）—— 不许混用。"""
    eq = [100.0, 121.0] + [121.0] * 503          # 跨 504 个区间 = 2 年
    geo = mc.cagr_from_equity(eq)
    ari = mc.ann_return_from_pnl([0.21], 2)
    ok = abs(geo - 0.10) < 1e-9 and abs(ari - 0.42) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} 年化两个名字：几何 {geo:.6f}（应 0.1）｜"
          f"算术 {ari:.6f}（应 0.42）")
    return ok


def test_cagr_uses_periods_not_points() -> bool:
    """★ CAGR 的区间数是**净值点数 − 1**（差 1 就是定义错，不是近似）。"""
    eq = list(np.linspace(100.0, 121.0, 505))     # 505 点 = 504 区间 = 2 年
    ok = abs(mc.cagr_from_equity(eq) - 0.10) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} CAGR 用区间数（{mc.cagr_from_equity(eq):.8f}）")
    return ok


def test_sharpe_zero_mean_is_zero() -> bool:
    """零均值收益 ⇒ Sharpe = 0（不是 NaN，也不是 inf）。"""
    r = [0.01 if i % 2 else -0.01 for i in range(200)]
    v = mc.sharpe_from_returns(r)
    ok = np.isfinite(v) and abs(v) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} 零均值 ⇒ Sharpe ≈ 0（{v:.3e}）")
    return ok


def test_sharpe_nan_on_degenerate_input() -> bool:
    """常数收益（std=0）⇒ **NaN**（不是 inf）—— 与因子层的口径一致。"""
    ok = (np.isnan(mc.sharpe_from_returns([0.01] * 50))
          and np.isnan(mc.sharpe_from_returns([0.01])))
    print(f"{'[PASS]' if ok else '[FAIL]'} std=0 / 样本不足 ⇒ NaN（不是 inf）")
    return ok


def test_performance_metrics_accepts_numpy_input() -> bool:
    """★ `performance_metrics` 原来**只吃 list**：传 numpy 数组会抛
    `ValueError: The truth value of an array ... is ambiguous`（`if xs`）。

    ⇒ 这是"让两份共用原语"时才暴露的 latent bug。
    """
    try:
        pm.sharpe_ratio(np.array([0.01, -0.02, 0.03]), 252)
    except ValueError as e:
        print(f"[FAIL] numpy 输入仍会崩：{e}")
        return False
    print("[PASS] `performance_metrics` 接受 numpy 数组")
    return True


def test_two_modules_agree_on_std() -> bool:
    """★ 两份 metrics 的**样本标准差必须一致**（这正是"同名不同口径"的落点）。"""
    a = [0.01, -0.02, 0.03, -0.01, 0.02]
    ok = abs(mc.std(a) - pm._std(a)) < 1e-15           # noqa: SLF001
    print(f"{'[PASS]' if ok else '[FAIL]'} 两份 metrics 的 std 一致"
          f"（{mc.std(a):.12f}）")
    return ok


def test_trade_metrics_delegates_drawdown() -> bool:
    """★ `trade_metrics._max_drawdown` 与 `metrics_core` **同一个值**。"""
    eq = [100.0, 120.0, 90.0, 130.0]
    ok = abs(tm._max_drawdown(eq) - mc.max_drawdown_from_equity(eq)) < 1e-15  # noqa: SLF001
    print(f"{'[PASS]' if ok else '[FAIL]'} trade_metrics 的回撤委派给 metrics_core")
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
