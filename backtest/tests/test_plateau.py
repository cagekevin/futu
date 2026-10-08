"""`plateau` —— 邻域稳定性（**小样本下唯一能做的过拟合检验**）的测试。

## 为什么是"形状"而不是"绝对值"

`IS/OOS` 需要样本；本项目的实测是 **29 笔**，切完每边 ~14 笔 ⇒ **做不了**。
而「**尖峰 vs 平台**」是**结构性**的：

> **尖峰一定是噪声** —— 只有那一个值好、邻居都差，没有机制能解释它。

⇒ 这一组测试钉住三件事：

| # | 钉什么 |
|---|---|
| 1 | **尖峰必须被判成"不许信"** |
| 2 | **平台必须被判成"形状支持"**（但**不等于有优势**）|
| 3 | ★ **全部挤在 2×SE 内 ⇒ "参数没影响"** —— 那就不该挑 |
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import plateau as pl  # noqa: E402


def _pt(v, m, se=0.05, n=20) -> pl.PlateauPoint:
    return pl.PlateauPoint(value=v, n_trades=n, metric=m, se=se)


def _res(pts) -> pl.PlateauResult:
    r = pl.PlateauResult(param="x", metric="expectancy_r")
    r.points = list(pts)
    return r


def test_spike_is_rejected() -> bool:
    """★ **尖峰**（中间好、两边差，差远超 2×SE）⇒ 判「尖峰」⇒ 不许信。"""
    r = _res([_pt(1, -0.5), _pt(2, +1.0), _pt(3, -0.5)])
    ok = r.is_spike and "尖峰" in r.verdict()
    print(f"{'[PASS]' if ok else '[FAIL]'} 尖峰被判成噪声（{r.verdict()[:26]}…）")
    return ok


def test_plateau_is_accepted() -> bool:
    """★ **平台**（一片都好）⇒ 判「平台」⇒ 形状支持（**仍不等于有优势**）。"""
    r = _res([_pt(1, +0.80), _pt(2, +1.00), _pt(3, +0.90)])
    ok = (not r.is_spike) and "平台" in r.verdict() and "不等于有优势" in r.verdict()
    print(f"{'[PASS]' if ok else '[FAIL]'} 平台被接受但**不背书**")
    return ok


def test_all_within_noise_means_no_effect() -> bool:
    """★ **全部点挤在 2×SE 内** ⇒ 「参数没有影响」—— 那就不该挑。

    这正是本项目四条轴里三条的实测结果。
    """
    r = _res([_pt(1, -0.10, se=0.50), _pt(2, -0.12, se=0.50),
              _pt(3, -0.08, se=0.50)])
    ok = r.all_within_noise and "参数没有影响" in r.verdict()
    print(f"{'[PASS]' if ok else '[FAIL]'} 全在噪声内 ⇒ 判「参数没影响」")
    return ok


def test_neighbours_are_by_value_not_by_order() -> bool:
    """★ 邻居按**值的大小**取 —— 网格顺序不可靠（调用方可能乱序传）。"""
    r = _res([_pt(3, -0.5), _pt(1, -0.5), _pt(2, +1.0)])      # 乱序
    nb = r.neighbours(r.best)
    ok = sorted(float(q.value) for q in nb) == [1.0, 3.0]
    print(f"{'[PASS]' if ok else '[FAIL]'} 邻居按值取（{[q.value for q in nb]}）")
    return ok


def test_no_neighbour_means_spike() -> bool:
    """只有一个点 ⇒ **更不该信**（连邻居都没有）。"""
    r = _res([_pt(1, +1.0)])
    ok = r.is_spike
    print(f"{'[PASS]' if ok else '[FAIL]'} 单点 ⇒ 判尖峰")
    return ok


def test_cluster_se_uses_days_not_trades() -> bool:
    """★ **簇级 SE**：同一交易日多笔**不独立** ⇒ 按笔算会**低估** SE。

    ⚠️ 构造的关键：**簇均值必须不同** —— 若每天的均值都一样，
       簇级 SE **数学上就是 0**（那正是"聚簇吸收了簇内相关"的表现），
       这条测试就退化成"0 > 正数"了。（第一版我就这么写错了。）

    构造：3 天，每天 10 笔**完全相同**（簇内相关 = 1）
      d1 全 +1.0｜d2 全 −1.0｜d3 全 +0.5
    ⇒ 簇级 SE ≈ `std([1,−1,0.5]) / √3` ≈ 0.38
    ⇒ 按笔 SE ≈ `std(30 笔) / √30` ≈ 0.14
    """
    class _T:
        def __init__(self, day, r):
            self.entry_day, self.r_multiple = day, r

    trades = ([_T("d1", 1.0)] * 10 + [_T("d2", -1.0)] * 10
              + [_T("d3", 0.5)] * 10)
    se = pl.cluster_se(trades)
    per_trade = float(np.std([t.r_multiple for t in trades], ddof=1)
                      / np.sqrt(len(trades)))
    ok = np.isfinite(se) and se > per_trade
    print(f"{'[PASS]' if ok else '[FAIL]'} 簇级 SE({se:.3f}) > 按笔 SE({per_trade:.3f})")
    return ok


def test_cluster_se_nan_on_too_few_clusters() -> bool:
    """簇数 < 3 ⇒ **NaN**（不假装算得出来）。"""
    class _T:
        def __init__(self, day, r):
            self.entry_day, self.r_multiple = day, r

    ok = np.isnan(pl.cluster_se([_T("d1", 1.0), _T("d2", -1.0)]))
    print(f"{'[PASS]' if ok else '[FAIL]'} 簇数不足 ⇒ NaN")
    return ok


def test_render_warns_when_trades_per_point_too_few() -> bool:
    """★ 每点笔数不足门槛 ⇒ 渲染里必须**明确警告**（并点出 IS/OOS 做不了）。"""
    r = _res([_pt(1, -0.1, n=2), _pt(2, -0.2, n=2)])
    text = pl.render_plateau(r)
    ok = "每点笔数不足" in text and "IS/OOS" in text
    print(f"{'[PASS]' if ok else '[FAIL]'} 笔数不足时有警告")
    return ok


def test_min_trades_per_point_threshold_is_declared() -> bool:
    """门槛是**显式常量**（不是散在比较里）—— 否则各处会漂。"""
    ok = pl.MIN_TRADES_PER_POINT == 10
    print(f"{'[PASS]' if ok else '[FAIL]'} 门槛显式声明（{pl.MIN_TRADES_PER_POINT}）")
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
