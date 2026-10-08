"""复权唯一实现测试（先红后绿）。

判据全部来自 `docs/reference/futu/adjustment-factor.md` 的**实测确认**条目：
- 拆股 `backward_A = 1/split_ratio`（4:1 → 4.0）**精确** → 复权后**拆股日无假跳变**；
- 派现 `backward_A = 1.0 / B = +派现`（加法）→ 后复权**不可精确** → 该日**不可信**；
- 前复权用 `d > t`（用 `d >= t` 会差 90%）；
- `mode` **必须显式**（无默认）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import adjust as A  # noqa: E402

# 4:1 拆股（2020-08-31）：raw 400 → 100（看着像 -75%，其实是拆股）
SPLIT_ROW = {"ex_div_date": "2020-08-31", "forward_adj_factorA": 0.25,
             "forward_adj_factorB": 0.0, "backward_adj_factorA": 4.0,
             "backward_adj_factorB": 0.0, "split_ratio": 0.25, "per_cash_div": None}
# 派现 0.27（2026-05-11）：backward_A == 1、backward_B == 0.27（加法项）
DIV_ROW = {"ex_div_date": "2026-05-11", "forward_adj_factorA": 0.99907,
           "forward_adj_factorB": 0.0, "backward_adj_factorA": 1.0,
           "backward_adj_factorB": 0.27, "split_ratio": None, "per_cash_div": 0.27}


def test_hfq_removes_split_jump() -> bool:
    """拆股日**不再有假跳变**：400→100 复权后应回到 400（收益 0%）。"""
    days = ["2020-08-28", "2020-08-31", "2020-09-01"]
    r = A.adjust_series(days, [400.0, 100.0, 101.0], [SPLIT_ROW], mode=A.HFQ)
    ok = r.values == (400.0, 400.0, 404.0) and not r.unusable_days
    raw_ret = 100.0 / 400.0 - 1.0
    adj_ret = r.values[1] / r.values[0] - 1.0
    print(f"{'[PASS]' if ok else '[FAIL]'} test_hfq_removes_split_jump"
          f"（raw 收益 {raw_ret:+.1%} → 复权后 {adj_ret:+.1%}）")
    return ok


def test_qfq_uses_strictly_after_boundary() -> bool:
    """前复权乘子只用 **d > t**：拆股**当天**不再乘（乘了就错 90%）。"""
    days = ["2020-08-28", "2020-08-31", "2020-09-01"]
    r = A.adjust_series(days, [400.0, 100.0, 101.0], [SPLIT_ROW], mode=A.QFQ)
    ok = r.multipliers == (0.25, 1.0, 1.0) and r.values == (100.0, 100.0, 101.0)
    print(f"{'[PASS]' if ok else '[FAIL]'} test_qfq_uses_strictly_after_boundary"
          f"（乘子 {r.multipliers}）")
    return ok


def test_dividend_day_is_marked_unusable() -> bool:
    """派现日：后复权**加法项形式未定** → 该日**标为不可信**，不猜。"""
    days = ["2026-05-08", "2026-05-11", "2026-05-12"]
    r = A.adjust_series(days, [290.0, 289.73, 290.0], [DIV_ROW], mode=A.HFQ)
    ok = (r.unusable_days == ("2026-05-11",) and r.values[1] is None
          and r.values[0] == 290.0 and r.n_dividend_events == 1)
    print(f"{'[PASS]' if ok else '[FAIL]'} test_dividend_day_is_marked_unusable"
          f"（不可信日 {r.unusable_days}）")
    return ok


def test_mode_must_be_explicit_and_valid() -> bool:
    """承 spec §4：`mode` 无默认；非法口径 → 报错（不静默选一个）。"""
    try:
        A.adjust_series(["2026-05-11"], [1.0], [], mode="auto")
    except A.AdjustmentError:
        print("[PASS] test_mode_must_be_explicit_and_valid（非法口径报错）")
        return True
    print("[FAIL] test_mode_must_be_explicit_and_valid（应报错）")
    return False


def test_missing_factor_field_fails_loud() -> bool:
    """承 P6：因子字段缺失 → **报错**，不静默当 1.0。"""
    bad = [{"ex_div_date": "2020-08-31", "backward_adj_factorA": None}]
    try:
        A.adjust_series(["2020-08-31"], [1.0], bad, mode=A.HFQ)
    except A.AdjustmentError:
        print("[PASS] test_missing_factor_field_fails_loud（缺失即报错）")
        return True
    print("[FAIL] test_missing_factor_field_fails_loud（应报错）")
    return False


def test_unsorted_days_fails_loud() -> bool:
    """乱序日 → 报错（复权是沿时间累乘，乱序 = 静默错）。"""
    try:
        A.adjust_series(["2026-05-12", "2026-05-11"], [1.0, 1.0], [], mode=A.HFQ)
    except A.AdjustmentError:
        print("[PASS] test_unsorted_days_fails_loud（乱序即报错）")
        return True
    print("[FAIL] test_unsorted_days_fails_loud（应报错）")
    return False


if __name__ == "__main__":
    results = [
        test_hfq_removes_split_jump(),
        test_qfq_uses_strictly_after_boundary(),
        test_dividend_day_is_marked_unusable(),
        test_mode_must_be_explicit_and_valid(),
        test_missing_factor_field_fails_loud(),
        test_unsorted_days_fails_loud(),
    ]
    print("-" * 50)
    print(f"  {sum(results)}/{len(results)} 通过")
    raise SystemExit(0 if all(results) else 1)
