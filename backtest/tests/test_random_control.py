"""`random_control` —— 随机对照模块的测试。

## 为什么需要它（第三轮独立复审第 4 条）

复审指出两件事，两条都指向**这个模块必须存在且必须自带分辨力**：

1. **仓库里没有随机对照的入口** —— 我是在 `/tmp` 里跑的，**跑完就没了**。
2. ★ **n=20 没有分辨力，而且两次结果方向相反**（70–85 分位 vs 30 分位）
   ⇒ **「互相印证」是我编的**；n=20 下 30/50/70 分位**互相不可区分**。

⇒ 所以这一组测试钉住的是**"结论不许超出分辨力"**这件事，不只是"代码能跑"。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import random_control as rc  # noqa: E402


def _ctl(n: int, real: float, draws: list[float]) -> rc.ControlResult:
    arr = np.asarray(draws * (n // len(draws) + 1))[:n]
    return rc.ControlResult(
        n_iter=n,
        real={"expectancy_r": real, "total_return": 0.0, "sharpe": 0.0,
              "ir_stripped": 0.0},
        rand={"expectancy_r": arr, "total_return": arr, "sharpe": arr,
              "ir_stripped": arr},
        hold_real={"平均持有": 5.0, "0 天": 0.1},
        hold_rand={"平均持有": 4.0, "0 天": 0.4})


def test_step_is_one_over_n_plus_one() -> bool:
    """★ 分位步长 = `1/(n+1)` —— n=20 时只能取 1/21 的倍数。

    （复审的原话：「最高可达分位 = 95.2%，**95% 门槛刚好够不着**」。）
    """
    c = _ctl(20, 0.0, [1.0, -1.0])
    ok = abs(c.step - 1 / 21) < 1e-12 and c.step > 0.047
    print(f"{'[PASS]' if ok else '[FAIL]'} n=20 的分位步长 = {c.step * 100:.2f}pp")
    return ok


def test_larger_n_shrinks_the_standard_error() -> bool:
    """★ **n 越大分辨力越强** —— 这是"为什么要 n=200 而不是 20"的量化依据。"""
    small = _ctl(20, 0.0, [1.0, -1.0])
    big = _ctl(200, 0.0, [1.0, -1.0])
    ok = big.se("expectancy_r") < small.se("expectancy_r") / 2
    print(f"{'[PASS]' if ok else '[FAIL]'} 标准误 n=20 → n=200："
          f"{small.se('expectancy_r') * 100:.1f}pp → {big.se('expectancy_r') * 100:.1f}pp")
    return ok


def test_middle_percentile_is_reported_as_indistinguishable() -> bool:
    """★★ **30 / 50 / 70 分位必须都报"不可区分"** —— 这正是复审的核心批评。"""
    outs = []
    for i, n in enumerate((40, 60, 80)):
        draws = [1.0, 0.0, -1.0]
        c = _ctl(n, 0.0, draws)
        outs.append(c.verdict("expectancy_r"))
    ok = all("不可区分" in o for o in outs)
    print(f"{'[PASS]' if ok else '[FAIL]'} 中间分位一律报「不可区分」（{len(outs)} 个档）")
    return ok


def test_extreme_percentile_is_reported_as_better() -> bool:
    """真实远高于所有随机抽样 ⇒ **可以**说"优于随机"。"""
    c = _ctl(50, 10.0, [1.0, -1.0, 0.5])
    ok = "优于" in c.verdict("expectancy_r")
    print(f"{'[PASS]' if ok else '[FAIL]'} 极端高分位 ⇒ 报「优于随机」")
    return ok


def test_verdict_carries_the_resolution() -> bool:
    """★ 结论里**必须带上分辨力**（分位 + 标准误）—— 不许只给一个百分数。"""
    c = _ctl(60, 0.0, [1.0, -1.0])
    v = c.verdict("expectancy_r")
    ok = "%" in v and "pp" in v
    print(f"{'[PASS]' if ok else '[FAIL]'} 结论自带分辨力（{v[:44]}…）")
    return ok


def test_random_candidates_match_day_and_count() -> bool:
    """★ 随机候选必须**同日、同数量** —— 否则比出来的是"日期/数量"的差，不是选股。"""
    import pandas as pd

    class _P:
        dates = ("d1", "d2", "d3")
        symbols = tuple(f"S{i}" for i in range(10))

        def field(self, _n):
            return pd.DataFrame(1.0, index=list(self.dates),
                                columns=list(self.symbols))

    cand = pd.DataFrame({"day": ["d1", "d1", "d2"], "symbol": ["S0", "S1", "S2"],
                         "stop_price": [1.0, 1.0, 1.0]})
    out = rc.random_candidates(cand, _P(), rng=np.random.default_rng(0))
    same_days = (out.groupby("day").size().to_dict() == {"d1": 2, "d2": 1})
    ok = same_days and len(out) == 3
    print(f"{'[PASS]' if ok else '[FAIL]'} 随机候选同日同数量（{out.groupby('day').size().to_dict()}）")
    return ok


def test_hold_distribution_splits_zero_one_two() -> bool:
    """★ 持有期分桶**必须拆开 0/1/2 天** —— `hold=0` 是"成交当日就止损"。"""
    class _T:
        def __init__(self, h):
            self.hold_days = h

    d = rc._hold_dist([_T(0), _T(1), _T(2), _T(30)])     # noqa: SLF001
    ok = (abs(d["0 天"] - 0.25) < 1e-12 and abs(d["1 天"] - 0.25) < 1e-12
          and abs(d["2 天"] - 0.25) < 1e-12 and abs(d[">20 天"] - 0.25) < 1e-12)
    print(f"{'[PASS]' if ok else '[FAIL]'} 持有期 0/1/2 天分开（{d['0 天']:.2f}/"
          f"{d['1 天']:.2f}/{d['2 天']:.2f}）")
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
