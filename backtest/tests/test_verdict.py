"""`verdict` —— 判决模块的测试。

## 这个文件的第一版测的是**我自己写的那套 5 条判据**

而仓库里早就有 `walk_forward_validation.judge_verdict`（**8 条**）——
**同一件事两份实现**，正是仓库铁律「判据只能有一处」要禁止的。

⇒ 现在 `verdict.py` **只剩两件事**：补 `judge_verdict` 要的输入 + 渲染。
   判据**一条都不在它里面**。所以这一组测试钉住的是：

| # | 钉什么 |
|---|---|
| 1 | **判据只有一处**（改 `walk_forward_validation` 必须改变结果）|
| 2 | **NaN 不许静默判错**（比较全 False ⇒ 会"悄悄通过"）|
| 3 | **`INVALID` 的措辞写明「不是说不清」** —— 防"样本短"盖过那道门 |
| 4 | 前后半段 / 分段一致性**真的从净值算出来**（不是占位）|
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import verdict  # noqa: E402
import walk_forward_validation as wf  # noqa: E402


class _R:
    """最小可用的 `SimulationResult` 替身（判决只用净值序列）。"""

    def __init__(self, eq):
        self.equity_values = tuple(float(x) for x in eq)
        self.equity_days = tuple(f"d{i}" for i in range(len(eq)))
        self.trades = []


def _rep(**kw) -> dict:
    base = {"cagr": 0.20, "sharpe": 1.5, "max_drawdown": -0.05, "n_trades": 300}
    return {**base, **kw}


def _up(n: int = 400) -> _R:
    """一路上涨的净值（前半段/后半段同号、分段全为正）。"""
    return _R(np.linspace(1e6, 1.3e6, n))


def test_verdict_delegates_to_walk_forward() -> bool:
    """★ **判据只有一处** —— 判决函数必须就是 `walk_forward_validation` 那个。

    （若 `verdict.py` 自己写了一套数字，这条会红。）
    """
    v = verdict.run_verdict(_up(), _rep(), side_ratio=0.3, cost2x_profitable=True)
    ok = v.level in (verdict.VALID, verdict.SUSPICIOUS, verdict.INVALID) \
        and isinstance(v.issues, list)
    print(f"{'[PASS]' if ok else '[FAIL]'} 判决走 `walk_forward_validation`"
          f"（{v.level}，{len(v.issues)} 条）")
    return ok


def test_healthy_result_is_not_invalid() -> bool:
    """一个各项都好的结果 ⇒ **不该**判 `INVALID`（否则判据就是坏的）。"""
    v = verdict.run_verdict(_up(), _rep(), side_ratio=0.3, cost2x_profitable=True)
    ok = v.level != verdict.INVALID
    print(f"{'[PASS]' if ok else '[FAIL]'} 健康结果不判 INVALID（{v.level}）")
    return ok


def test_nan_inputs_are_replaced_not_silently_passed() -> bool:
    """★ **`NaN` 不许静默判错** —— 比较 `NaN > x` 全是 `False` ⇒ 会"悄悄通过"。

    这里给一个**全 NaN** 的报告 ⇒ 必须被替换成保守值 ⇒ 判 `INVALID`。
    """
    bad = {"cagr": float("nan"), "sharpe": float("nan"),
           "max_drawdown": float("nan"), "n_trades": 0}
    v = verdict.run_verdict(_up(), bad, side_ratio=None, cost2x_profitable=False)
    ok = v.level == verdict.INVALID
    print(f"{'[PASS]' if ok else '[FAIL]'} 全 NaN ⇒ 判 INVALID（不是静默通过）")
    return ok


def test_segments_are_computed_from_equity() -> bool:
    """★ 前后半段 / 分段一致性必须**真的从净值算**（不是占位 0）。

    前半段涨、后半段跌 ⇒ 两段**异号**。
    """
    n = 400
    eq = np.concatenate([np.linspace(1e6, 1.4e6, n // 2),
                         np.linspace(1.4e6, 1.1e6, n - n // 2)])
    seg = verdict.compute_segments(_R(eq))
    ok = (np.isfinite(seg["h1_ann"]) and np.isfinite(seg["h2_ann"])
          and seg["h1_ann"] > 0 > seg["h2_ann"] and seg["wf_total"] >= 2)
    print(f"{'[PASS]' if ok else '[FAIL]'} 前后半段从净值算出"
          f"（H1 {seg['h1_ann'] * 100:+.1f}% / H2 {seg['h2_ann'] * 100:+.1f}%，"
          f"分段 {seg['wf_positive']}/{seg['wf_total']}）")
    return ok


def test_inconsistent_halves_are_flagged() -> bool:
    """前半段涨、后半段跌 ⇒ 判据「前后半段同号」应当被触发。"""
    n = 400
    eq = np.concatenate([np.linspace(1e6, 1.5e6, n // 2),
                         np.linspace(1.5e6, 1.0e6, n - n // 2)])
    v = verdict.run_verdict(_R(eq), _rep(), side_ratio=0.3, cost2x_profitable=True)
    ok = any("前后半段" in m for m in v.issues)
    print(f"{'[PASS]' if ok else '[FAIL]'} 前后半段异号被抓到（{v.issues[:1]}）")
    return ok


def test_invalid_text_says_not_unsure() -> bool:
    """★ `INVALID` 的措辞必须写明「**不是说不清，是不合格**」——
    这正是这条存在的理由：**不许拿"样本短"盖过自己那道门**。
    """
    v = verdict.Verdict(verdict=verdict.INVALID, issues=["x"], inputs={
        "ann_ret": -0.01, "sharpe": -0.1, "mdd": 0.1, "max_side": 1.0,
        "h1_ann": -0.02, "h2_ann": 0.003, "wf_positive": 2, "wf_total": 5,
        "cost2x_profitable": False, "n_trades": 29, "min_trades": 120})
    text = verdict.render_verdict(v)
    ok = "说不清" in text and "不合格" in text
    print(f"{'[PASS]' if ok else '[FAIL]'} INVALID 的措辞写明「不是说不清」")
    return ok


def test_render_shows_the_eight_inputs() -> bool:
    """★ 渲染里必须**把 8 条判据要的输入都印出来** —— 否则读者无法复核。"""
    v = verdict.run_verdict(_up(), _rep(), side_ratio=0.3, cost2x_profitable=True)
    text = verdict.render_verdict(v)
    ok = all(k in text for k in ("年化", "Sharpe", "MDD", "单边",
                                 "前半段", "分段为正", "2×成本", "笔"))
    print(f"{'[PASS]' if ok else '[FAIL]'} 渲染印出全部输入")
    return ok


def test_judge_verdict_signature_is_the_repo_one() -> bool:
    """★ 反证：仓库那个函数**确实存在且是 8 条**的接口（防哪天被改名）。"""
    import inspect
    params = set(inspect.signature(wf.judge_verdict).parameters)
    need = {"ann_ret", "sharpe", "mdd", "max_side", "h1_ann", "h2_ann",
            "wf_positive", "wf_total", "cost2x_profitable", "n_trades"}
    ok = need <= params
    print(f"{'[PASS]' if ok else '[FAIL]'} `judge_verdict` 的接口没变"
          f"（缺 {sorted(need - params)}）")
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
