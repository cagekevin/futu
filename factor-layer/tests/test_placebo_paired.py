"""R3 配对判决 —— 对照 PRD §五 R3 的约束 Z1–Z5 + §6.4 的三档判决。

不依赖网络、不依赖真实数据。
跑法：`.venv/bin/python tests/test_placebo_paired.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from evaluate.placebo import paired_statistics as ps  # noqa: E402

RNG = np.random.default_rng(20261008)


def _fake(T: int = 200, R: int = 500, *, edge: float = 0.0,
          seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """造一对 (real, null)：`edge > 0` 表示真实确实更好。"""
    rng = np.random.default_rng(seed)
    common = rng.normal(0.0, 0.01, size=(T, 1))        # 共同的市场冲击
    null = common + rng.normal(0.0, 0.005, size=(T, R))
    real = common[:, 0] + edge + rng.normal(0.0, 0.005, size=T)
    return real, null


# ── Z1：同日配对 ────────────────────────────────────────────────────────

def test_z1_paired_delta_is_daywise() -> bool:
    """`delta[t]` 只用**同一天**的 real 与 null —— 不跨日池化。"""
    real = np.array([0.02, 0.00])
    null = np.array([[0.01, 0.01], [0.00, 0.00]])
    d = ps.paired_deltas(real, null)
    ok = np.allclose(d, [0.01, 0.00])
    print(f"{'[PASS]' if ok else '[FAIL]'} test_z1_paired_delta_is_daywise（{d}）")
    return ok


def test_z1_shape_mismatch_raises() -> bool:
    try:
        ps.paired_deltas(np.array([0.01, 0.02]), np.array([[0.0, 0.0]]))
    except ValueError:
        print("[PASS] test_z1_shape_mismatch_raises（天数不符报错）")
        return True
    print("[FAIL] test_z1_shape_mismatch_raises")
    return False


def test_z1_rejects_1d_null_matrix() -> bool:
    """`null_matrix` 必须是 `(T, R)` —— 一维（忘记配对）→ 报错。"""
    try:
        ps.paired_deltas(np.array([0.01]), np.array([0.0, 0.0]))
    except ValueError:
        print("[PASS] test_z1_rejects_1d_null_matrix")
        return True
    print("[FAIL] test_z1_rejects_1d_null_matrix")
    return False


# ── 排位：语义正确 ──────────────────────────────────────────────────────

def test_rank_is_about_50_when_no_edge() -> bool:
    """★ 毫无优势 ⇒ 排位应落在 50% 附近（否则门槛 95% 就没意义了）。"""
    real, null = _fake(edge=0.0, seed=3)
    r = ps.rank_percentile(real, null)
    ok = 35.0 <= r <= 65.0
    print(f"{'[PASS]' if ok else '[FAIL]'} test_rank_is_about_50_when_no_edge（{r:.1f}%）")
    return ok


def test_rank_high_when_real_dominates() -> bool:
    real, null = _fake(edge=0.02, seed=4)
    r = ps.rank_percentile(real, null)
    ok = r >= 99.0
    print(f"{'[PASS]' if ok else '[FAIL]'} test_rank_high_when_real_dominates（{r:.1f}%）")
    return ok


def test_rank_low_when_real_is_worse() -> bool:
    real, null = _fake(edge=-0.02, seed=5)
    r = ps.rank_percentile(real, null)
    ok = r <= 1.0
    print(f"{'[PASS]' if ok else '[FAIL]'} test_rank_low_when_real_is_worse（{r:.1f}%）")
    return ok


def test_rank_rejects_empty() -> bool:
    try:
        ps.rank_percentile(np.array([]), np.zeros((0, 10)))
    except ValueError:
        print("[PASS] test_rank_rejects_empty（无有效日 → 报错）")
        return True
    print("[FAIL] test_rank_rejects_empty")
    return False


# ── ★ 成本对配对差值无效（数学性质，必须锁死）──────────────────────────

def test_cost_does_not_change_paired_delta() -> bool:
    """★ `(real − c) − (null − c) = real − null` ⇒ **成本不改变配对差值**。

    这条是 §6.4 判据③改成看「真实侧绝对收益」的**唯一理由**；
    若不测，将来有人改成"扣成本后 delta 变了"就会悄悄错。
    """
    real, null = _fake(seed=6)
    d = ps.paired_deltas(real, null)

    # 两侧各扣同一成本 → 差值不变
    c = 0.0006
    d_after = ps.paired_deltas(real - c, null - c)
    ok = np.allclose(d, d_after)
    print(f"{'[PASS]' if ok else '[FAIL]'} test_cost_does_not_change_paired_delta"
          f"（最大差 {np.max(np.abs(d - d_after)):.2e}）")
    return ok


def test_cost_reduces_absolute_means_but_not_delta() -> bool:
    """扣成本**降低绝对值**（两侧都降），但**配对差值不动**。"""
    real, null = _fake(seed=7)
    s0 = ps.summarize(real, null, k_per_day=[5] * real.size,
                      pool_per_day=[50] * real.size, cost_rate=0.0)
    s1 = ps.summarize(real, null, k_per_day=[5] * real.size,
                      pool_per_day=[50] * real.size, cost_rate=0.0003)
    ok = (np.isclose(s0.delta_mean, s1.delta_mean)
          and np.isclose(s1.real_mean_after_cost, s0.real_mean - 0.0006)
          and s1.real_mean_after_cost < s0.real_mean)
    print(f"{'[PASS]' if ok else '[FAIL]'} test_cost_reduces_absolute_means_but_not_delta"
          f"（delta {s0.delta_mean:+.5f} → {s1.delta_mean:+.5f}；"
          f"real 扣成本 {s1.real_mean_after_cost:+.5f}）")
    return ok


def test_z3_negative_cost_rejected() -> bool:
    real, null = _fake(seed=8)
    try:
        ps.summarize(real, null, k_per_day=[5] * real.size,
                     pool_per_day=[50] * real.size, cost_rate=-0.1)
    except ValueError:
        print("[PASS] test_z3_negative_cost_rejected")
        return True
    print("[FAIL] test_z3_negative_cost_rejected")
    return False


# ── Z2：三道判据都在 + 分档 ─────────────────────────────────────────────

def test_z2_summary_has_all_three_criteria_fields() -> bool:
    real, null = _fake(seed=9)
    s = ps.summarize(real, null, k_per_day=[5] * real.size,
                     pool_per_day=[50] * real.size, cost_rate=0.0003)
    ok = all(np.isfinite(x) for x in (
        s.rank_pct, s.first_half_delta, s.second_half_delta,
        s.real_mean_after_cost, s.delta_mean, s.delta_median,
        s.real_mean, s.null_mean, s.win_days_ratio, s.k_median, s.pool_median,
    ))
    print(f"{'[PASS]' if ok else '[FAIL]'} test_z2_summary_has_all_three_criteria_fields")
    return ok


def test_z2_halves_split_is_not_shared() -> bool:
    """前/后半段必须**各自独立**算（不是拿全样本冒充）。"""
    T = 100
    real = np.concatenate([np.full(T // 2, 0.01), np.full(T // 2, -0.01)])
    null = np.zeros((T, 20))
    s = ps.summarize(real, null, k_per_day=[5] * T, pool_per_day=[50] * T,
                     cost_rate=0.0)
    ok = np.isclose(s.first_half_delta, 0.01) and np.isclose(s.second_half_delta, -0.01)
    print(f"{'[PASS]' if ok else '[FAIL]'} test_z2_halves_split_is_not_shared"
          f"（前 {s.first_half_delta:+.4f} 后 {s.second_half_delta:+.4f}）")
    return ok


def test_summarize_rejects_empty() -> bool:
    try:
        ps.summarize(np.array([]), np.zeros((0, 10)), k_per_day=[], pool_per_day=[],
                     cost_rate=0.0)
    except ValueError:
        print("[PASS] test_summarize_rejects_empty（承 Z4：无样本不出报告）")
        return True
    print("[FAIL] test_summarize_rejects_empty")
    return False


# ── §6.4：三档判决 ──────────────────────────────────────────────────────

def test_judge_pass_when_all_three_hold() -> bool:
    v, issues = ps.judge(rank_pct=97.0, first_half_delta=0.001,
                         second_half_delta=0.002, real_mean_after_cost=0.003)
    ok = v == ps.VERDICT_PASS and not issues
    print(f"{'[PASS]' if ok else '[FAIL]'} test_judge_pass_when_all_three_hold（{v}）")
    return ok


def test_judge_fail_when_nothing_holds() -> bool:
    v, issues = ps.judge(rank_pct=50.0, first_half_delta=-0.001,
                         second_half_delta=-0.002, real_mean_after_cost=-0.003)
    ok = v == ps.VERDICT_FAIL and len(issues) >= 3
    print(f"{'[PASS]' if ok else '[FAIL]'} test_judge_fail_when_nothing_holds"
          f"（{v}，{len(issues)} 条）")
    return ok


def test_judge_unclear_when_partially_holds() -> bool:
    """过了排位，但扣成本后就没了 ⇒ 说不清。"""
    v, issues = ps.judge(rank_pct=99.0, first_half_delta=0.001,
                         second_half_delta=0.002, real_mean_after_cost=-0.0001)
    ok = v == ps.VERDICT_UNCLEAR and len(issues) == 1
    print(f"{'[PASS]' if ok else '[FAIL]'} test_judge_unclear_when_partially_holds（{v}）")
    return ok


def test_judge_fail_on_opposite_halves_even_with_high_rank() -> bool:
    """★ 前后半段**异号** → 直接不通过（哪怕排位 99%）——承 `10-测试` §五。"""
    v, issues = ps.judge(rank_pct=99.0, first_half_delta=+0.005,
                         second_half_delta=-0.005, real_mean_after_cost=0.01)
    ok = v == ps.VERDICT_FAIL and any("异号" in i for i in issues)
    print(f"{'[PASS]' if ok else '[FAIL]'} test_judge_fail_on_opposite_halves_even_with_high_rank"
          f"（{v}）")
    return ok


def test_rank_threshold_is_frozen_at_95() -> bool:
    """门槛**冻结在契约里**（承 C4）—— 改动它 = 开新实验。"""
    ok = ps.RANK_THRESHOLD == 95.0 and ps.JUDGE_RULE_FROZEN_AT == "2026-10-08"
    print(f"{'[PASS]' if ok else '[FAIL]'} test_rank_threshold_is_frozen_at_95"
          f"（{ps.RANK_THRESHOLD}，冻结于 {ps.JUDGE_RULE_FROZEN_AT}）")
    return ok


def test_verdict_constants_defined_in_one_place() -> bool:
    """三个档位的常量只在 `paired_statistics.py` 定义（承 C4：一处）。"""
    import ast

    base = Path(__file__).resolve().parent.parent / "evaluate" / "placebo"
    offenders = []
    for path in sorted(base.glob("*.py")):
        if path.name == "paired_statistics.py":
            continue
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                continue
            if isinstance(node, ast.Name) and node.id.startswith("VERDICT_"):
                # 只允许**引用**（NAME），不允许**赋值**（Assign target）
                pass
        # 更直接：禁止在本文件里出现这些**字面量**
        for literal in ("通过", "说不清", "不通过"):
            if f'= "{literal}"' in src or f"= '{literal}'" in src:
                offenders.append(f"{path.name}:{literal}")
    ok = not offenders
    print(f"{'[PASS]' if ok else '[FAIL]'} test_verdict_constants_defined_in_one_place"
          f"{'' if ok else ' ← ' + str(offenders)}")
    return ok


# ── Z4：样本量显形 / 告警非空 ───────────────────────────────────────────

def test_report_rejects_empty_warning() -> bool:
    """告警为空 → 报错（承 Z4 / G-1：幸存者偏差**必须显形**）。"""
    from evaluate.placebo.placebo_report import PlaceboReport

    try:
        PlaceboReport(
            selection_name="x", selection_params_fingerprint="a",
            sampling_params_fingerprint="b",
            n_days=10, n_days_valid=10, k_per_day_median=5.0, universe_median=50.0,
            real_mean=0.001, null_mean=0.0, cost_rate=0.0003,
            real_mean_after_cost=0.0004, null_mean_after_cost=-0.0006,
            delta_mean=0.001, delta_median=0.001, rank_percentile=90.0,
            win_days_ratio=0.6, first_half_delta=0.001, second_half_delta=0.001,
            verdict=ps.VERDICT_UNCLEAR, rule_frozen_at=ps.JUDGE_RULE_FROZEN_AT,
            issues=(), survivorship_warning="",
        )
    except ValueError:
        print("[PASS] test_report_rejects_empty_warning")
        return True
    print("[FAIL] test_report_rejects_empty_warning")
    return False


def test_report_rejects_bad_verdict() -> bool:
    from evaluate.placebo.placebo_report import PlaceboReport

    try:
        PlaceboReport(
            selection_name="x", selection_params_fingerprint="a",
            sampling_params_fingerprint="b",
            n_days=10, n_days_valid=10, k_per_day_median=5.0, universe_median=50.0,
            real_mean=0.001, null_mean=0.0, cost_rate=0.0003,
            real_mean_after_cost=0.0004, null_mean_after_cost=-0.0006,
            delta_mean=0.001, delta_median=0.001, rank_percentile=90.0,
            win_days_ratio=0.6, first_half_delta=0.001, second_half_delta=0.001,
            verdict="significant",            # ← 禁止的二元结论
            rule_frozen_at=ps.JUDGE_RULE_FROZEN_AT,
            issues=(), survivorship_warning="w",
        )
    except ValueError:
        print("[PASS] test_report_rejects_bad_verdict（拒绝 significant 这类二元结论）")
        return True
    print("[FAIL] test_report_rejects_bad_verdict")
    return False


# ── Z5：复用唯一标签 ────────────────────────────────────────────────────

def test_z5_no_second_label_definition() -> bool:
    """`placebo/` 里不得出现第二处标签公式（承 Z5）。`ast` 级 + 剥字符串。"""
    import ast

    base = Path(__file__).resolve().parent.parent / "evaluate" / "placebo"
    offenders = []
    for path in sorted(base.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        has_call = False
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "forward_return":
                    has_call = True
        # 允许**调用** forward_return；禁止自己写 np.log(...) 这类
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Attribute) and node.attr == "log":
                offenders.append(f"{path.name}:{node.lineno} .log")
        _ = has_call
    ok = not offenders
    print(f"{'[PASS]' if ok else '[FAIL]'} test_z5_no_second_label_definition"
          f"{'' if ok else ' ← ' + str(offenders)}")
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
            print(f"FAIL {fn.__name__}: 抛异常")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
