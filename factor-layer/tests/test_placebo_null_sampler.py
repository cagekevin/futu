"""R2 对照抽样 —— 对照 PRD §五 R2 的约束 N1–N4。

不依赖网络、不依赖真实数据。
跑法：`.venv/bin/python tests/test_placebo_null_sampler.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from evaluate.placebo import null_sampler as ns  # noqa: E402

CFG = ns.SamplingConfig(iterations=1_000, seed=20261008)


# ── N1：同日 / 同池 / 同数量 ────────────────────────────────────────────

def test_n1_shape_and_range() -> bool:
    """形状 (R, K)，值域 [0, pool_size)。"""
    idx = ns.sample_indices(50, 7, config=CFG, day="2026-01-02")
    ok = (idx.shape == (CFG.iterations, 7)
          and idx.min() >= 0 and idx.max() < 50
          and idx.dtype.kind == "i")
    print(f"{'[PASS]' if ok else '[FAIL]'} test_n1_shape_and_range（{idx.shape}）")
    return ok


def test_n1_without_replacement() -> bool:
    """★ 无放回：**每一行内**的下标互不重复（承 N4）。

    有放回会重复抽到同一只 ⇒ 零分布被扭曲，且不报错。
    """
    idx = ns.sample_indices(30, 10, config=ns.SamplingConfig(iterations=500, seed=7),
                            day="2026-01-02")
    dup_rows = [i for i, row in enumerate(idx) if len(set(row.tolist())) != row.size]
    ok = not dup_rows
    print(f"{'[PASS]' if ok else '[FAIL]'} test_n1_without_replacement"
          f"（重复行 {len(dup_rows)} / {len(idx)}）")
    return ok


def test_n1_k_is_respected_exactly() -> bool:
    """数量锁死：每日抽出的个数**恰好等于** K（不多不少）。"""
    for k in (1, 3, 25):
        idx = ns.sample_indices(40, k, config=ns.SamplingConfig(iterations=50, seed=3),
                                day="2026-01-02")
        if idx.shape != (50, k):
            print(f"[FAIL] test_n1_k_is_respected_exactly（k={k} → {idx.shape}）")
            return False
    print("[PASS] test_n1_k_is_respected_exactly（K=1/3/25 均精确）")
    return True


# ── N2：可复现 + 日间独立 ───────────────────────────────────────────────

def test_n2_same_config_same_day_reproduces() -> bool:
    """同种子 + 同输入 → **逐位相同**（承 N2）。"""
    a = ns.sample_indices(25, 5, config=CFG, day="2026-01-02")
    b = ns.sample_indices(25, 5, config=CFG, day="2026-01-02")
    ok = np.array_equal(a, b)
    print(f"{'[PASS]' if ok else '[FAIL]'} test_n2_same_config_same_day_reproduces")
    return ok


def test_n2_different_days_are_independent() -> bool:
    """★ 不同交易日 → 抽样序列**必须不同**（否则日间人为相关，承 N2）。"""
    a = ns.sample_indices(25, 5, config=CFG, day="2026-01-02")
    b = ns.sample_indices(25, 5, config=CFG, day="2026-01-05")
    ok = not np.array_equal(a, b) and ns.derive_day_seed(CFG.seed, "2026-01-02") \
        != ns.derive_day_seed(CFG.seed, "2026-01-05")
    print(f"{'[PASS]' if ok else '[FAIL]'} test_n2_different_days_are_independent")
    return ok


def test_n2_different_seeds_differ() -> bool:
    a = ns.sample_indices(25, 5, config=ns.SamplingConfig(iterations=100, seed=1),
                          day="2026-01-02")
    b = ns.sample_indices(25, 5, config=ns.SamplingConfig(iterations=100, seed=2),
                          day="2026-01-02")
    ok = not np.array_equal(a, b)
    print(f"{'[PASS]' if ok else '[FAIL]'} test_n2_different_seeds_differ")
    return ok


def test_n2_derive_day_seed_is_stable() -> bool:
    ok = (ns.derive_day_seed(42, "2026-01-02") == ns.derive_day_seed(42, "2026-01-02")
          and isinstance(ns.derive_day_seed(42, "2026-01-02"), int))
    print(f"{'[PASS]' if ok else '[FAIL]'} test_n2_derive_day_seed_is_stable")
    return ok


# ── N3：票池由外部给（本模块不自建）─────────────────────────────────────

def test_n3_sampler_does_not_build_universe() -> bool:
    """★ `null_sampler.py` 里**不得**出现取数 / 票池构造（承 N3 + C1）。

    `ast` 级（剥掉字符串常量）—— 因为 docstring 里**会提到**这些名字。
    配阳性对照。
    """
    import ast

    banned = ("subprocess", "read_stocks", "read_panel", "provide", "request",
              "read_csv", "open", "universe")
    path = Path(__file__).resolve().parent.parent / "evaluate" / "placebo" / "null_sampler.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            continue
        if isinstance(node, ast.Name) and node.id in banned:
            hits.append(f"{node.lineno} Name({node.id})")
        elif isinstance(node, ast.Attribute) and node.attr in banned:
            hits.append(f"{node.lineno} .{node.attr}")
    ok = not hits
    print(f"{'[PASS]' if ok else '[FAIL]'} test_n3_sampler_does_not_build_universe"
          f"{'' if ok else ' ← ' + str(hits)}")
    return ok


def test_n3_positive_control() -> bool:
    """阳性对照：上面的检查逻辑**真的能抓到**取数代码。"""
    import ast

    banned = ("subprocess", "read_stocks", "read_panel", "provide", "request",
              "read_csv", "open", "universe")
    tree = ast.parse("import subprocess\nx = read_stocks('2026-01-02')\n")
    hits = [n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id in banned]
    hits += [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]
    ok = "subprocess" in hits and "read_stocks" in hits
    print(f"{'[PASS]' if ok else '[FAIL]'} test_n3_positive_control（抓到 {sorted(set(hits))}）")
    return ok


# ── N4：抽不满就报错 ────────────────────────────────────────────────────

def test_n4_pool_smaller_than_k_raises() -> bool:
    """★ 票池 3 只、K=10 → **报错**（不静默少抽、不重复抽，承 N4）。"""
    try:
        ns.sample_indices(3, 10, config=CFG, day="2026-01-02")
    except ValueError as e:
        ok = "不足" in str(e) and "N4" in str(e)
        print(f"{'[PASS]' if ok else '[FAIL]'} test_n4_pool_smaller_than_k_raises"
              f"（{str(e)[:60]}…）")
        return ok
    print("[FAIL] test_n4_pool_smaller_than_k_raises（应报错）")
    return False


def test_n4_empty_or_zero_k_raises() -> bool:
    for pool, k in ((0, 1), (10, 0)):
        try:
            ns.sample_indices(pool, k, config=CFG, day="d")
        except ValueError:
            continue
        print(f"[FAIL] test_n4_empty_or_zero_k_raises（pool={pool}, k={k} 未报错）")
        return False
    print("[PASS] test_n4_empty_or_zero_k_raises")
    return True


def test_n4_k_equals_pool_is_allowed() -> bool:
    """边界：K == 票池大小 → 合法（抽全部，只是顺序随机）——**不是**错误。"""
    idx = ns.sample_indices(6, 6, config=ns.SamplingConfig(iterations=20, seed=5),
                            day="2026-01-02")
    ok = idx.shape == (20, 6) and all(sorted(r.tolist()) == list(range(6)) for r in idx)
    print(f"{'[PASS]' if ok else '[FAIL]'} test_n4_k_equals_pool_is_allowed")
    return ok


# ── 抽样正确性：均匀性（证明"瞎选"真的是均匀的）────────────────────────

def test_sampling_is_uniform() -> bool:
    """★ 均匀性：每只标的中签频率 ≈ K / pool_size（承"等概率"）。

    这一条**证明抽样是对的** —— 否则"零分布"根本不是"瞎选"的分布。
    """
    pool_size, k, iters = 10, 3, 20_000
    idx = ns.sample_indices(pool_size, k,
                            config=ns.SamplingConfig(iterations=iters, seed=20261008),
                            day="2026-01-02")
    counts = np.bincount(idx.ravel(), minlength=pool_size)
    expected = iters * k / pool_size
    worst = float(np.max(np.abs(counts - expected) / expected))
    ok = worst < 0.05
    print(f"{'[PASS]' if ok else '[FAIL]'} test_sampling_is_uniform"
          f"（最大偏差 {worst*100:.2f}% ，期望 {expected:.0f}）")
    return ok


def test_sampling_covers_whole_pool() -> bool:
    """抽多了之后，票池里**每一只都该被抽到过**（不能只在小圈子里抽）。"""
    pool_size, k, iters = 50, 2, 5_000
    idx = ns.sample_indices(pool_size, k,
                            config=ns.SamplingConfig(iterations=iters, seed=11),
                            day="2026-01-02")
    covered = len(set(idx.ravel().tolist()))
    ok = covered == pool_size
    print(f"{'[PASS]' if ok else '[FAIL]'} test_sampling_covers_whole_pool"
          f"（覆盖 {covered}/{pool_size}）")
    return ok


# ── C3：配置必填 + 构造时校验 ───────────────────────────────────────────

def test_config_rejects_bad_values_at_construction() -> bool:
    cases = [
        (0, 1), (-1, 1),                       # iterations 非法
        (100, "42"), (100, 1.0), (100, True),  # seed 非法
    ]
    for iters, seed in cases:
        try:
            ns.SamplingConfig(iterations=iters, seed=seed)
        except (ValueError, TypeError):
            continue
        print(f"[FAIL] test_config_rejects_bad_values_at_construction"
              f"（iterations={iters}, seed={seed!r} 未报错）")
        return False
    print("[PASS] test_config_rejects_bad_values_at_construction（构造时报错）")
    return True


def test_config_is_frozen() -> bool:
    cfg = ns.SamplingConfig(iterations=10, seed=1)
    try:
        cfg.iterations = 20            # type: ignore[misc]
    except Exception:
        ok = cfg.params == {"iterations": 10, "seed": 1}
        print(f"{'[PASS]' if ok else '[FAIL]'} test_config_is_frozen（params={cfg.params}）")
        return ok
    print("[FAIL] test_config_is_frozen（竟然可改）")
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
            print(f"FAIL {fn.__name__}: 抛异常")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
