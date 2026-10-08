"""R3 端到端 —— `run_placebo` 的组装正确性（R1 → R2 → R3）。

用**合成面板**（不碰网络、不碰真实库），跑法：
    `.venv/bin/python tests/test_placebo_runner.py`
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from evaluate.placebo import paired_statistics as ps  # noqa: E402
from evaluate.placebo.baseline_runner import run_placebo  # noqa: E402
from evaluate.placebo.null_sampler import SamplingConfig  # noqa: E402
from evaluate.placebo.placebo_report import render_report  # noqa: E402
from evaluate.placebo.selection_registry import register_selection  # noqa: E402
from panel.panel_types import CrossSectionPanel  # noqa: E402

_COUNTER = itertools.count()
T_DAYS = 80
N_SYMS = 40


def _panel(*, momentum: float = 0.0, seed: int = 7) -> CrossSectionPanel:
    """合成面板：可选**动量自相关**（`momentum>0` 时"过去涨的继续涨"）。"""
    rng = np.random.default_rng(seed)
    dates = tuple(pd.date_range("2026-01-01", periods=T_DAYS, freq="D")
                  .strftime("%Y-%m-%d"))
    symbols = tuple(f"S{i:02d}" for i in range(N_SYMS))

    noise = rng.normal(0.0, 0.01, size=(T_DAYS, N_SYMS))
    rets = np.zeros_like(noise)
    for t in range(T_DAYS):
        prev = rets[t - 1] if t else np.zeros(N_SYMS)
        rets[t] = momentum * prev + noise[t]
    prices = 100.0 * np.exp(np.cumsum(rets, axis=0))

    frame = pd.DataFrame(prices, index=list(dates), columns=list(symbols))
    fields = {name: frame for name in ("open", "high", "low", "close", "volume")}
    return CrossSectionPanel(
        dates=dates,
        symbols=symbols,
        fields=fields,
        universe_by_day={d: symbols for d in dates},
        n_adjust_events={},
        contaminated_days={},
        adjust="hfq",
        snapshot_day=dates[-1],
        snapshot_is_after_day=False,
    )


class _TopKRule:
    """规则：取某因子**当日横截面**最大的 K 只。"""

    def __init__(self, k: int):
        self.name = f"runner_topk_{next(_COUNTER)}"
        self.requires = ("f",)
        self.params = {"k": k}

    def select(self, data):
        row = data.today("f")
        return set(row.nlargest(self.params["k"]).index)


def _factors(panel: CrossSectionPanel) -> dict[str, pd.DataFrame]:
    return {"f": panel.field("open")}


# ── 能跑通 + 报告字段齐全（承 Z2 / Z4）──────────────────────────────────

def test_run_placebo_produces_complete_report() -> bool:
    panel = _panel()
    rule = _TopKRule(5)
    register_selection(rule)

    r = run_placebo(
        selection_name=rule.name, panel=panel, factors=_factors(panel),
        cost_rate=0.0003, sampling=SamplingConfig(iterations=200, seed=42),
    )

    checks = {
        "有有效天数": r.n_days_valid > 0,
        "总数正确": r.n_days == T_DAYS,
        "判决合法": r.verdict in (ps.VERDICT_PASS, ps.VERDICT_UNCLEAR, ps.VERDICT_FAIL),
        "排位在 0-100": 0.0 <= r.rank_percentile <= 100.0,
        "胜率在 0-1": 0.0 <= r.win_days_ratio <= 1.0,
        "三道判据齐全": all(np.isfinite(x) for x in (
            r.rank_percentile, r.first_half_delta, r.second_half_delta,
            r.real_mean_after_cost)),
        "两侧都报了": np.isfinite(r.real_mean) and np.isfinite(r.null_mean),
        "差值都在": np.isfinite(r.delta_mean) and np.isfinite(r.delta_median),
        "样本量显形": r.k_per_day_median > 0 and r.universe_median > 0,
        "告警非空": bool(r.survivorship_warning),
        "规则已冻结": r.rule_frozen_at == ps.JUDGE_RULE_FROZEN_AT,
        "指纹非空": bool(r.selection_params_fingerprint)
        and bool(r.sampling_params_fingerprint),
    }
    bad = [k for k, v in checks.items() if not v]
    ok = not bad
    print(f"{'[PASS]' if ok else '[FAIL]'} test_run_placebo_produces_complete_report"
          f"{'' if ok else ' ← ' + str(bad)}")
    return ok


def test_report_renders_without_error() -> bool:
    panel = _panel()
    rule = _TopKRule(5)
    register_selection(rule)
    r = run_placebo(selection_name=rule.name, panel=panel, factors=_factors(panel),
                    cost_rate=0.0003, sampling=SamplingConfig(iterations=100, seed=1))
    text = render_report(r)
    ok = ("随机对照报告" in text and "判决" in text and "排位" in text
          and "胜率" in text and "扣成本" in text)
    print(f"{'[PASS]' if ok else '[FAIL]'} test_report_renders_without_error"
          f"（{len(text.splitlines())} 行）")
    return ok


# ── 无效样本必须被剔除、且显形（承 Z4）──────────────────────────────────

def test_last_two_days_are_dropped_by_label() -> bool:
    """`forward_return` 在末尾两天是 NaN ⇒ 有效天数必然 **少于** 总天数。

    若这里相等，说明有人把 NaN 当 0 用了（那是造数据）。
    """
    panel = _panel()
    rule = _TopKRule(5)
    register_selection(rule)
    r = run_placebo(selection_name=rule.name, panel=panel, factors=_factors(panel),
                    cost_rate=0.0, sampling=SamplingConfig(iterations=50, seed=2))
    ok = r.n_days_valid < r.n_days
    print(f"{'[PASS]' if ok else '[FAIL]'} test_last_two_days_are_dropped_by_label"
          f"（有效 {r.n_days_valid} / 总 {r.n_days}）")
    return ok


# ── 无信息规则 ⇒ 排位应落在"像随机"的区间 ───────────────────────────────

def test_no_information_rule_lands_near_middle() -> bool:
    """★ 用**纯随机因子** ⇒ 排位必须落在"像随机"的区间（20%–80%）。

    ⚠️ 这里**故意不用价格**当因子：价格水平 = **累积涨幅**，本身就带微弱动量
       —— 实测用价格时排位 **79%**（贴着上界），那测的就不是"无信息"了。
    """
    panel = _panel(momentum=0.0, seed=11)
    rng = np.random.default_rng(99)
    random_factor = pd.DataFrame(
        rng.normal(size=(T_DAYS, N_SYMS)),
        index=list(panel.dates), columns=list(panel.symbols),
    )
    rule = _TopKRule(8)
    register_selection(rule)
    r = run_placebo(selection_name=rule.name, panel=panel,
                    factors={"f": random_factor},
                    cost_rate=0.0, sampling=SamplingConfig(iterations=500, seed=3))
    ok = 20.0 <= r.rank_percentile <= 80.0
    print(f"{'[PASS]' if ok else '[FAIL]'} test_no_information_rule_lands_near_middle"
          f"（排位 {r.rank_percentile:.1f}%）")
    return ok


# ── 有信息规则 ⇒ 排位应偏高（证明整条链路能"看见"优势）──────────────────

def test_momentum_data_reveals_edge() -> bool:
    """★ 在**强动量**数据上，"选过去涨幅最高"应当排位偏高。

    这条是整条链路的**灵敏度检验**：若它抓不到真实优势，那这套东西就没用。
    """
    panel = _panel(momentum=0.6, seed=13)
    rule = _TopKRule(8)
    register_selection(rule)
    r = run_placebo(selection_name=rule.name, panel=panel, factors=_factors(panel),
                    cost_rate=0.0, sampling=SamplingConfig(iterations=500, seed=4))
    ok = r.rank_percentile >= 70.0
    print(f"{'[PASS]' if ok else '[FAIL]'} test_momentum_data_reveals_edge"
          f"（排位 {r.rank_percentile:.1f}%，判决 {r.verdict}）")
    return ok


# ── 参数校验 ────────────────────────────────────────────────────────────

def test_negative_cost_rejected_by_runner() -> bool:
    panel = _panel()
    rule = _TopKRule(3)
    register_selection(rule)
    try:
        run_placebo(selection_name=rule.name, panel=panel, factors=_factors(panel),
                    cost_rate=-0.01, sampling=SamplingConfig(iterations=10, seed=1))
    except ValueError:
        print("[PASS] test_negative_cost_rejected_by_runner（承 Z3）")
        return True
    print("[FAIL] test_negative_cost_rejected_by_runner")
    return False


def test_cost_rate_is_required() -> bool:
    """★ 成本**必填无默认**（承 Z3）—— 漏传必须报 TypeError，不许悄悄用 0。"""
    panel = _panel()
    rule = _TopKRule(3)
    register_selection(rule)
    try:
        run_placebo(selection_name=rule.name, panel=panel, factors=_factors(panel),
                    sampling=SamplingConfig(iterations=10, seed=1))  # type: ignore[call-arg]
    except TypeError:
        print("[PASS] test_cost_rate_is_required（缺 cost_rate → TypeError）")
        return True
    print("[FAIL] test_cost_rate_is_required")
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
