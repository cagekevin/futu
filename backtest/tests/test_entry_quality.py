"""`entry_quality` —— 进场质量筛查的测试。

## 为什么这组测试主要是"防我自己过拟合"

这个模块**天然危险**：筛 18 个特征，总有一个 AUC 看着高。
所以测试钉住的不是"能不能算出 AUC"，而是**三条纪律**：

| # | 纪律 | 对应测试 |
|---|---|---|
| 1 | **必须报分母**（族大小）| `test_family_size_is_reported` |
| 2 | ★ **BH 必须真的把"看着显著"的拦下** | `test_bh_kills_looks_significant` |
| 3 | **样本太少 / 只有一类 ⇒ 不硬算** | `test_degenerate_inputs_are_skipped` |

⚠️ 真实的教训：本项目实测三个目标（活过 5 天 / 活过 2 天 / R>0），
**18 个特征全部 0 过 BH**，而其中三个**原始 p 值看着显著**
（`vol_ratio10_50` 0.0465 / `rs_rank` 0.0255 / `ret260` **0.0175**）——
**全被 BH 拦下**。这正是这个模块存在的意义。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import entry_quality as eq  # noqa: E402


def test_auc_is_half_for_uninformative_feature() -> bool:
    """★ **AUC = 0.5 表示没有分辨力** —— 用随机噪声验。"""
    rng = np.random.default_rng(0)
    x = rng.normal(size=400)
    y = rng.integers(0, 2, size=400)
    a = eq._auc(x, y)                              # noqa: SLF001
    ok = abs(a - 0.5) < 0.08
    print(f"{'[PASS]' if ok else '[FAIL]'} 噪声的 AUC ≈ 0.5（{a:.3f}）")
    return ok


def test_auc_is_one_for_perfect_separation() -> bool:
    """完美分离 ⇒ AUC = 1.0。"""
    x = np.array([1.0, 2.0, 3.0, 8.0, 9.0, 10.0])
    y = np.array([0, 0, 0, 1, 1, 1])
    ok = abs(eq._auc(x, y) - 1.0) < 1e-12             # noqa: SLF001
    print(f"{'[PASS]' if ok else '[FAIL]'} 完美分离 ⇒ AUC = 1.0")
    return ok


def test_auc_handles_ties() -> bool:
    """**并列值取平均秩** —— 否则 AUC 会被并列结构系统性带偏。"""
    x = np.array([1.0, 1.0, 1.0, 5.0, 5.0, 5.0])
    y = np.array([0, 0, 0, 1, 1, 1])
    ok = abs(eq._auc(x, y) - 1.0) < 1e-12             # noqa: SLF001
    print(f"{'[PASS]' if ok else '[FAIL]'} 并列值处理正确（AUC = 1.0）")
    return ok


def test_family_size_is_reported() -> bool:
    """★ **必须报分母** —— 渲染里要出现"筛了 N 个特征"。"""
    rng = np.random.default_rng(1)
    n = 60
    f = pd.DataFrame({f"f{i}": rng.normal(size=n) for i in range(7)})
    y = rng.integers(0, 2, size=n)
    s = eq.screen(f, y, n_perm=200)
    text = eq.render_screen(s)
    ok = s.n_features == 7 and "族大小" in text and "7" in text
    print(f"{'[PASS]' if ok else '[FAIL]'} 渲染里报了分母（族大小 {s.n_features}）")
    return ok


def test_bh_kills_looks_significant() -> bool:
    """★★ **BH 必须把"看着显著"的拦下** —— 这是本模块存在的理由。

    构造：**一个纯噪声特征**（真 AUC = 0.5），但样本小 ⇒ 原始 p 可能 < 0.05。
    多次重抽里，**只要出现过一次"原始 p<0.05 但 BH 不显著"**，就说明 BH 在起作用。
    """
    hit = 0
    for seed in range(30):
        rng = np.random.default_rng(seed)
        n = 30
        f = pd.DataFrame({f"noise{i}": rng.normal(size=n) for i in range(15)})
        y = rng.integers(0, 2, size=n)
        s = eq.screen(f, y, n_perm=300, seed=seed)
        raw_sig = [r for r in s.results if r.p_value < 0.05]
        if raw_sig and not any(r.significant for r in s.results):
            hit += 1
    ok = hit > 0
    print(f"{'[PASS]' if ok else '[FAIL]'} BH 拦下「原始 p<0.05 但不显著」"
          f"（30 次重抽里出现 {hit} 次）")
    return ok


def test_degenerate_inputs_are_skipped() -> bool:
    """样本太少 / 标签只有一类 ⇒ **不硬算**（不假装有结论）。"""
    rng = np.random.default_rng(2)
    # ① 只有一类标签
    s1 = eq.screen(pd.DataFrame({"a": rng.normal(size=40)}),
                   np.ones(40, dtype=int), n_perm=100)
    # ② 特征覆盖率太低
    f = pd.DataFrame({"a": [np.nan] * 38 + [1.0, 2.0]})
    s2 = eq.screen(f, rng.integers(0, 2, size=40), n_perm=100)
    ok = s1.n_features == 0 and s2.n_features == 0
    print(f"{'[PASS]' if ok else '[FAIL]'} 退化输入被跳过"
          f"（{s1.n_features} / {s2.n_features} 个特征）")
    return ok


def test_p_value_is_never_zero() -> bool:
    """★ `p` **不许是 0** —— 置换次数有限 ⇒ 最小是 `1/n_perm`（报 0 是撒谎）。"""
    rng = np.random.default_rng(3)
    n = 40
    x = np.concatenate([np.zeros(20), np.ones(20)])       # 完美分离
    s = eq.screen(pd.DataFrame({"perfect": x}),
                  np.array([0] * 20 + [1] * 20), n_perm=500)
    p = s.results[0].p_value
    ok = p > 0
    print(f"{'[PASS]' if ok else '[FAIL]'} p 值不为 0（{p:.5f}）")
    return ok


def test_verdict_says_no_signal_when_nothing_survives() -> bool:
    """★ 全没过 BH ⇒ 结论必须**明说"没有可观测的进场质量信号"**（那是结论，不是失败）。"""
    s = eq.ScreenResult(target="x", n_trades=30, n_features=5, results=[
        eq.FeatureResult(name=f"f{i}", n=30, auc=0.6, p_value=0.2,
                         adj_p=0.5, significant=False) for i in range(5)])
    v = s.verdict()
    ok = "没有一个" in v and "进场质量" in v
    print(f"{'[PASS]' if ok else '[FAIL]'} 全不过 ⇒ 明说没信号")
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
