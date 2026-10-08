"""指标正确性测试（承 R2/R3/B2/D7）：用已知答案的合成数据验证。

这是 V1 说的"带外证据"之一 —— 期望值**手推**，**不来自**被测函数。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import performance_metrics  # noqa: E402


def test_annual_return_no_double_division() -> bool:
    """承 R3：年化 = 收益率 × ppy，**不再除样本长度**。"""
    pnl = [0.001] * 10
    got = performance_metrics.annualized_return(pnl, 6240)
    ok = abs(got - 6.24) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} test_annual_return（{got} == 6.24）")
    return ok


def test_sortino_floor_bounds_sparse_pnl() -> bool:
    """承 R2：稀疏 PnL 的 Sortino **有界**（被 clamp 在 ±SORTINO_CLAMP）。

    注意：撞到上限 = "不可信信号"，不是"真实值"。这里只验**有界性**（R2 的要求）。
    """
    sparse = [0.01] + [0.0] * 99
    value = performance_metrics.sortino_ratio(sparse, 6240)
    ok = abs(value) <= performance_metrics.SORTINO_CLAMP
    print(f"{'[PASS]' if ok else '[FAIL]'} test_sortino_floor"
          f"（{value:.3f} ∈ ±{performance_metrics.SORTINO_CLAMP}）")
    return ok


def test_periods_per_year() -> bool:
    """承 B2：自动估计，不写死；H1 与 D1 不同量级。"""
    d1 = performance_metrics.periods_per_year([i * 86400 for i in range(366)])
    h1 = performance_metrics.periods_per_year([i * 3600 for i in range(6241)])
    ok = d1 != h1 and 300 < d1 < 400
    print(f"{'[PASS]' if ok else '[FAIL]'} test_periods_per_year（D1={d1}, H1={h1}）")
    return ok


def test_periods_per_year_fails_loud_on_too_few() -> bool:
    """承 P1：时间戳不足 → **报错**，不静默回退。"""
    try:
        performance_metrics.periods_per_year([1])
    except ValueError:
        print("[PASS] test_periods_per_year_fails_loud_on_too_few（报错而非静默）")
        return True
    print("[FAIL] test_periods_per_year_fails_loud_on_too_few（应报错）")
    return False


def test_periods_per_year_fails_loud_on_out_of_range() -> bool:
    """承 D7/P1：估计值越界（时间戳单位错）→ **报错**，不静默截断。"""
    try:
        # 跨度 1 秒的 2 根 bar → ppy ≈ 6.3e7，远超上限
        performance_metrics.periods_per_year([0, 1])
    except ValueError:
        print("[PASS] test_periods_per_year_fails_loud_on_out_of_range（越界报错）")
        return True
    print("[FAIL] test_periods_per_year_fails_loud_on_out_of_range（应报错）")
    return False


if __name__ == "__main__":
    results = [
        test_annual_return_no_double_division(),
        test_sortino_floor_bounds_sparse_pnl(),
        test_periods_per_year(),
        test_periods_per_year_fails_loud_on_too_few(),
        test_periods_per_year_fails_loud_on_out_of_range(),
    ]
    print("-" * 50)
    print(f"  {sum(results)}/{len(results)} 通过")
    raise SystemExit(0 if all(results) else 1)
