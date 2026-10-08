"""M5 评估 —— 对照 PRD §五 M5 的 5 条约束（J1 ~ J5）。

跑法：.venv/bin/python tests/test_evaluate.py

★ 四条**关键**断言：
  1. **有效样本显形**（J4）—— `pandas.corr` 会悄悄丢配对不全的样本，必须报 `n_valid`
  2. **方向来自因子对象**（J5）—— 不由调用方传，否则同一因子有两份真相
  3. **判决必过 BH**（J3）—— 输出必含 `n_tests` 与校正后 p 值
  4. **跨层口径一致**（PRD §八 #21）—— 与 `backtest/target_label.py` 非边界逐位相等
"""
from __future__ import annotations

import ast
import math
import sys
from datetime import date, timedelta
from pathlib import Path

FACTOR_LAYER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FACTOR_LAYER))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from evaluate.evaluator import EvaluateConfig, evaluate, evaluate_many  # noqa: E402
from evaluate.forward_return import ENTRY_OFFSET, TARGET_HORIZON, forward_return  # noqa: E402
from evaluate.judgement import (  # noqa: E402
    VERDICT_INSUFFICIENT, VERDICT_NOT_SIGNIFICANT, VERDICT_SIGNIFICANT,
    JudgeEntry, JudgeThresholds, benjamini_hochberg, judge_batch,
)
from evaluate.metrics.ic import ic_statistics, rank_ic_series  # noqa: E402
from evaluate.metrics.quantile_returns import monotonicity, quantile_returns  # noqa: E402
from evaluate.metrics.turnover import turnover  # noqa: E402
from panel.panel_types import CrossSectionPanel  # noqa: E402

EVAL_DIR = FACTOR_LAYER / "evaluate"
SYMBOLS = tuple(f"S{i:02d}" for i in range(20))
N_DAYS = 80


def _dates(n: int) -> list[str]:
    start = date(2026, 1, 1)
    return [str(start + timedelta(days=i)) for i in range(n)]


def _make_panel(*, n_days: int = N_DAYS, symbols=SYMBOLS,
                opens=None) -> CrossSectionPanel:
    """默认：每只标的**不同的**恒定增长率（`1.005 + 0.002 × k`）。

    ⚠️ 若所有标的使用**同一条** open 序列，同日横截面上因子值会**全同** ——
       `qcut` 退化、Spearman 无定义，测试会"跑通"但**什么都没测到**。
       （这是本文件写码时真踩过的坑：11 个测试同时红，根因是夹具退化。）
    """
    dates = _dates(n_days)
    if opens is None:
        opens = {s: [100.0 * ((1.005 + 0.002 * k) ** i) for i in range(n_days)]
                 for k, s in enumerate(symbols)}
    fields = {"open": pd.DataFrame(opens, index=dates, dtype="float64")}
    return CrossSectionPanel(
        dates=tuple(dates), symbols=tuple(sorted(symbols)), fields=fields,
        universe_by_day={d: tuple(sorted(symbols)) for d in dates},
        n_adjust_events={}, contaminated_days={}, adjust="hfq",
        snapshot_day="2026-10-06", snapshot_is_after_day=True,
    )


class _FakeFactor:
    """满足 M5 输入协议的最小对象：`values` + `direction` + `name`。"""

    def __init__(self, values: pd.DataFrame, direction: int = 1,
                 name: str = "ret20") -> None:
        self.values = values
        self.direction = direction
        self.name = name


def _config(**patch) -> EvaluateConfig:
    base = dict(bins=5, min_samples=10, thresholds=_thresholds())
    return EvaluateConfig(**{**base, **patch})


def _perfect_factor(panel: CrossSectionPanel) -> _FakeFactor:
    """因子值 = 标签 → 每日 RankIC 恒为 1（完美预测）。

    ⚠️ 它**不能**用来测 ICIR —— 完美因子的 IC 序列是常数 ⇒ `std = 0` ⇒ ICIR 无定义。
       需要"有波动的 IC 序列"时用 `_noisy_factor`。
    """
    target = forward_return(panel)
    return _FakeFactor(target.copy(), direction=1)


def _noisy_factor(panel: CrossSectionPanel, *, seed: int = 7) -> _FakeFactor:
    """有噪声的因子（种子固定 → 可复现）—— IC 有波动，ICIR / t 检验才有定义。"""
    rng = np.random.default_rng(seed)
    values = pd.DataFrame(
        rng.normal(size=(len(panel.dates), len(panel.symbols))),
        index=list(panel.dates), columns=list(panel.symbols))
    return _FakeFactor(values, direction=1)


def _predictive_factor(panel: CrossSectionPanel, *, seed: int = 7,
                       noise: float = 0.5) -> pd.DataFrame:
    """**有预测力但有波动**的因子值 —— 既能得到正 ICIR，又不会 `std = 0`。

    ⚠️ 纯完美因子（`factor = target`）的 IC 序列是常数 ⇒ ICIR 无定义；
       纯噪声因子（`_noisy_factor`）的 ICIR 符号随机。要测"方向归一"必须用这个。
    """
    rng = np.random.default_rng(seed)
    noise_frame = pd.DataFrame(
        rng.normal(size=(len(panel.dates), len(panel.symbols))),
        index=list(panel.dates), columns=list(panel.symbols))
    target = forward_return(panel)
    scale = float(np.nanmax(np.abs(target.to_numpy()))) or 1.0
    return target / scale + noise * noise_frame


def _code_only(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, holders) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = node.body[1:]
    return ast.unparse(tree)


def _eval_sources() -> dict[str, str]:
    files = list(EVAL_DIR.glob("*.py")) + list((EVAL_DIR / "metrics").glob("*.py"))
    return {p.name: _code_only(p) for p in sorted(files)}


# ── J1 只读 ─────────────────────────────────────────────────────────────

def _mutates_values(code: str) -> bool:
    """检测「往 `values` 里写」—— **只认下标/属性赋值**（`values.loc[x] = …`）。

    ⚠️ 不认 `values = …`（那只是本地变量重绑定，比如 `values = list(pvalues)`）——
       否则会把一堆无关的局部变量误报成"改了因子值"。
    """
    for node in ast.walk(ast.parse(code)):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AugAssign) else [])
        for target in targets:
            if not isinstance(target, (ast.Subscript, ast.Attribute)):
                continue
            base = target
            while isinstance(base, (ast.Subscript, ast.Attribute)):
                base = base.value
            if isinstance(base, ast.Name) and base.id == "values":
                return True
    return False


def test_j1_no_assignment_to_values_in_source():
    """★ J1：评估模块**不出现**对因子值的赋值（只读）。"""
    offenders = [name for name, code in _eval_sources().items()
                 if _mutates_values(code)]
    assert not offenders, f"评估模块修改了因子值：{offenders}（承 J1）"


def test_j1_positive_control_detector_works():
    """★ 阳性对照：上一条的检测器必须**真的**能抓到赋值，且不误报重绑定。"""
    assert _mutates_values("values.loc['d'] = 1.0")
    assert _mutates_values("values[0] = 1.0")
    assert _mutates_values("values.iloc[0, 0] = 1.0")
    assert not _mutates_values("values = list(pvalues)")
    assert not _mutates_values("other.loc['d'] = 1.0")


def test_j1_evaluate_does_not_modify_input():
    """★ J1：`evaluate` 跑完后**输入逐位不变**。"""
    panel = _make_panel()
    factor = _perfect_factor(panel)
    before = factor.values.copy(deep=True)
    evaluate(factor, panel, config=_config())
    pd.testing.assert_frame_equal(factor.values, before)


# ── J2 七项指标缺一不可 ─────────────────────────────────────────────────

def test_j2_seven_metrics_all_present():
    """★ J2：七项指标全部在报告里（含**换手**），且都是**有定义的数**。

    ⚠️ 这里用**有噪声**的因子（不是完美因子）—— 完美因子的 IC 序列是常数，
       `std = 0` ⇒ ICIR / t 检验无定义，会让这条测试变成"验了个 NaN"。
    """
    panel = _make_panel()
    report = evaluate(_noisy_factor(panel), panel, config=_config())
    assert report.has_all_required_metrics(), "报告缺指标（承 J2）"
    for key in ("icir", "ic_win_rate", "t_stat", "p_value"):
        value = report.ic_stats[key]
        assert value == value, f"{key} 是 NaN —— 指标没真的算出来"
    assert report.monotonicity == report.monotonicity
    assert len(report.long_short) > 0
    assert report.turnover["mean"] == report.turnover["mean"]


def test_j2_quantile_group_sizes_are_reported():
    """★ J2/§6.4：**每组样本数必须报出**（证明等频生效）。"""
    panel = _make_panel()
    report = evaluate(_perfect_factor(panel), panel, config=_config(bins=5))
    counts = report.quantile_counts
    assert counts.shape[1] == 5
    assert int(counts.to_numpy().min()) >= 3, counts.describe()


def test_j2_equal_frequency_not_equal_width():
    """★ §6.4：分箱必须**等频** —— 长尾分布下等距会让中间组塞进大部分样本。"""
    symbols = tuple(f"L{i}" for i in range(50))
    panel = _make_panel(symbols=symbols)
    target = forward_return(panel)
    factor = target.copy()
    factor.iloc[:, 0] = factor.iloc[:, 0] * 1000.0     # 造一个极端值
    _mean, counts, _ls, _skip = quantile_returns(
        factor, target, bins=5, min_samples=10)
    sizes = counts.to_numpy()
    assert sizes.size > 0, "分箱全部被跳过 —— 夹具退化"
    # 等频：50 只 / 5 组 = 每组 10 只。等距分箱下极端值会把中间组撑爆。
    assert sizes.min() == 10 and sizes.max() == 10, (
        f"组大小不均 → 不是等频：min={sizes.min()} max={sizes.max()}（承 §6.4）"
    )


# ── J3 判决必过 BH + 报检验次数 ─────────────────────────────────────────

def _thresholds(**patch) -> JudgeThresholds:
    """默认：**成本 0**（让只测统计性质的用例不受经济门槛影响）+ 经济门槛 0。"""
    base = dict(alpha=0.05, min_days=10, min_abs_icir=0.1,
                min_abs_monotonicity=0.5,
                cost_bps_per_turnover=0.0, min_net_annual_return=0.0,
                annualization_days=250)
    return JudgeThresholds(**{**base, **patch})


def _entry(**patch) -> JudgeEntry:
    """默认：**统计上明显显著 + 经济上明显赚钱**（只测某一维时再单独覆盖）。"""
    base = dict(name="f", p_value=0.001, icir=0.5, n_days=100,
                monotonicity=0.9,
                long_short_mean=0.001,      # 日均 10bp → 年化 25%
                turnover_mean=0.0)          # 不换手 → 无成本
    return JudgeEntry(**{**base, **patch})


def test_j3_judgement_reports_n_tests_and_bh():
    """★ J3：判决输出**必含** `n_tests` 与校正结果 —— 否则"显著"无法复核。"""
    entries = [_entry(name=f"f{i}") for i in range(20)]
    out = judge_batch(entries, thresholds=_thresholds())
    assert len(out) == 20
    assert all(item["n_tests"] == 20 for item in out)
    assert all("p_value_raw" in item and "p_value_bh_significant" in item
               for item in out)


def test_j3_bh_correction_actually_changes_verdict():
    """★ J3/Q5：BH 的分母是**整批** —— 同一个 p，批次不同结论不同。

    ⚠️ 这条同时是「**不能用 `[p] * m` 冒充 BH**」的回归测试：
       把同一个 p 重复 m 次时 `p_(m) = p ≤ α` 恒成立 ⇒ BH 退化成不校正，
       而真正的 BH（一批里混着好几个接近 α 的 p）会把弱因子挡掉。
    """
    thresholds = _thresholds()
    alone = judge_batch([_entry(name="x", p_value=0.03)], thresholds=thresholds)
    assert alone[0]["p_value_bh_significant"] is True      # m=1：不校正

    # 一批 4 个：0.01 / 0.03 / 0.04 / 0.9 → 只有排名 1 的过（0.01 ≤ 1/4×0.05）
    batch = [_entry(name="a", p_value=0.01), _entry(name="b", p_value=0.03),
             _entry(name="c", p_value=0.04), _entry(name="d", p_value=0.9)]
    out = {item["name"]: item for item in judge_batch(batch, thresholds=thresholds)}
    assert out["a"]["p_value_bh_significant"] is True
    assert out["b"]["p_value_bh_significant"] is False, (
        "同一批里 p=0.03 排在 0.01 之后，BH 后不该显著（承 Q5）"
    )


def test_j3_insufficient_data_is_its_own_verdict():
    """★ J3：有效天数不足 → `insufficient_data`（**不硬判**，也不假装"不显著"）。"""
    out = judge_batch([_entry(name="x", p_value=1e-9, icir=5.0, n_days=3,
                              monotonicity=1.0)],
                      thresholds=_thresholds(min_days=60))
    assert out[0]["verdict"] == VERDICT_INSUFFICIENT


# ── ★ 经济幅度门槛（2026-10-08 审计补）──────────────────────────────────

def test_economic_threshold_blocks_statistically_significant_but_unprofitable():
    """★★ 审计核心用例：**统计显著但扣成本后亏钱 → 必须判 not_significant**。

    实测背景（全窗口 1108 个有效日）：`z_neu_vol20` 的 p = 0.0056（显著）、
    单调性 +0.600，但多空日均仅 **+0.00003**、日均换手 **0.144**
    ⇒ 保本成本只有 **2.1 bp**，低于真实成本即净亏损。
    初版判决**不看这两个数** ⇒ 会把它判成「显著」。
    """
    thresholds = _thresholds(cost_bps_per_turnover=5.0,
                             min_net_annual_return=0.0)
    # 统计面：**前三门全过**；经济面：日均 0.3bp、换手 0.144 ⇒ 成本 0.072bp/日 ⇒ 净负
    entry = _entry(name="vol20_like", p_value=0.005, icir=0.20,
                   monotonicity=0.6, long_short_mean=0.00003,
                   turnover_mean=0.144)
    out = judge_batch([entry], thresholds=thresholds)[0]
    # ★ 先证明"前三门确实都过了"—— 否则这条测试可能是被别的门槛挡下的（假通过）
    assert out["p_value_bh_significant"] is True, "统计上确实显著"
    assert abs(out["icir"]) >= out["min_abs_icir"], "ICIR 门槛已过"
    assert abs(out["monotonicity"]) >= out["min_abs_monotonicity"], "单调性门槛已过"
    # ★ 唯一的差异在经济面
    assert out["net_annual_return"] < 0, out
    assert out["verdict"] == VERDICT_NOT_SIGNIFICANT, (
        "扣成本后年化为负，不该判显著（承 PRD §6.4 的意图）"
    )


def test_economic_threshold_lets_profitable_factor_pass():
    """★ 阳性对照：**统计面完全相同**，只有经济面变好 → 必须判 significant。

    没有这条，上一条可能只是"门槛把什么都挡掉了"。
    """
    thresholds = _thresholds(cost_bps_per_turnover=5.0,
                             min_net_annual_return=0.0)
    entry = _entry(name="good", p_value=0.005, icir=0.20, monotonicity=0.6,
                   long_short_mean=0.001, turnover_mean=0.1)   # 25%/年 − 成本
    out = judge_batch([entry], thresholds=thresholds)[0]
    assert out["net_annual_return"] > 0, out
    assert out["verdict"] == VERDICT_SIGNIFICANT


def test_economic_threshold_is_the_only_difference_between_the_two():
    """★ 隔离性：两条用例**除经济面外逐字段相同** —— 判决差异只可能来自它。"""
    thresholds = _thresholds(cost_bps_per_turnover=5.0, min_net_annual_return=0.0)
    common = dict(p_value=0.005, icir=0.20, monotonicity=0.6, n_days=100)
    bad = judge_batch([_entry(name="x", long_short_mean=0.00003,
                              turnover_mean=0.144, **common)],
                      thresholds=thresholds)[0]
    good = judge_batch([_entry(name="x", long_short_mean=0.001,
                               turnover_mean=0.144, **common)],
                       thresholds=thresholds)[0]
    for key in ("p_value_raw", "p_value_bh_significant", "icir",
                "monotonicity", "n_days", "turnover_mean"):
        assert bad[key] == good[key], f"{key} 不该有差异 —— 隔离性被破坏"
    assert bad["verdict"] != good["verdict"], "只有经济面不同，判决必须不同"


def test_economic_threshold_reports_gross_cost_and_net():
    """★ 三个数都要报出来（承 J3 的同一判据：门槛的实测值必须可复核）。"""
    thresholds = _thresholds(cost_bps_per_turnover=10.0, annualization_days=250)
    out = judge_batch([_entry(long_short_mean=0.001, turnover_mean=0.2)],
                      thresholds=thresholds)[0]
    assert math.isclose(out["gross_annual_return"], 0.001 * 250, rel_tol=1e-12)
    assert math.isclose(out["cost_annual_return"], 0.2 * 0.0010 * 250, rel_tol=1e-12)
    assert math.isclose(out["net_annual_return"],
                        out["gross_annual_return"] - out["cost_annual_return"],
                        rel_tol=1e-12)
    assert out["cost_bps_per_turnover"] == 10.0


def test_economic_threshold_is_required_and_validated():
    """★ Q4：成本与年化天数**必填无默认**；非法值构造时报错。"""
    for patch in ({"cost_bps_per_turnover": -1.0}, {"annualization_days": 0}):
        try:
            _thresholds(**patch)
        except ValueError:
            continue
        raise AssertionError(f"非法阈值应报错：{patch}")
    for missing in ("cost_bps_per_turnover", "min_net_annual_return",
                    "annualization_days"):
        kwargs = dict(alpha=0.05, min_days=10, min_abs_icir=0.1,
                      min_abs_monotonicity=0.5, cost_bps_per_turnover=1.0,
                      min_net_annual_return=0.0, annualization_days=250)
        kwargs.pop(missing)
        try:
            JudgeThresholds(**kwargs)      # type: ignore[arg-type]
        except TypeError:
            continue
        raise AssertionError(f"缺 {missing} 应 TypeError（承 Q4：必填无默认）")


def test_j3_no_bare_judgement_path():
    """★ J3：**判决语义**只在 `judgement.py` 定义 —— 别处不许自造"显著"。

    判据：`VERDICT_*` 这些常量只许出现在 `judgement.py`。
    """
    offenders = [name for name, code in _eval_sources().items()
                 if "VERDICT_" in code and name != "judgement.py"]
    assert not offenders, f"出现了第二处判决出口：{offenders}（承 J3：无裸判路径）"


def test_j3_positive_control_verdict_exists():
    """★ 阳性对照：上一条必须真的能抓到 —— `judgement.py` 里确实有 `VERDICT_`。"""
    assert "VERDICT_" in _eval_sources()["judgement.py"]


def test_j3_single_factor_evaluate_uses_m1_batch():
    """`evaluate` 单因子 = `evaluate_many` 的长度 1 批次（`n_tests = 1`）。"""
    panel = _make_panel()
    report = evaluate(_perfect_factor(panel), panel, config=_config())
    assert report.judgement["n_tests"] == 1


def test_bh_known_case_max_k_rule():
    """BH 的「取**最大** k」规则：中间有个大 p 值不该阻断前面的。"""
    assert benjamini_hochberg([0.001, 0.9, 0.02], alpha=0.05) == [True, False, True]


def test_bh_known_cases():
    """BH 已知输入 → 已知输出（手算可复核）。"""
    assert benjamini_hochberg([0.001, 0.01, 0.02, 0.5], alpha=0.05) == \
        [True, True, True, False]
    assert benjamini_hochberg([0.5, 0.6], alpha=0.05) == [False, False]


def test_bh_nan_raises_not_silently_skipped():
    """★ BH：`NaN` 的 p 值 → **报错**（承 P5：缺就是缺，不许静默丢弃）。

    ⚠️ 审计修正（2026-10-07）：初版是**静默跳过**（当作"不拒绝"）——
       那与 `backtest/statistics.py` 的同名函数**语义分歧**（那边 raise），
       且把"检验做不了"混成了"检验没通过"。
    """
    try:
        benjamini_hochberg([float("nan"), 0.001], alpha=0.05)
    except ValueError as e:
        assert "NaN" in str(e)
        return
    raise AssertionError("NaN 应报错（承 P5）")


def test_bh_empty_raises_not_silent():
    """BH：空输入 → **报错**（没有检验就没有校正）—— 与 backtest 语义一致。"""
    try:
        benjamini_hochberg([], alpha=0.05)
    except ValueError:
        return
    raise AssertionError("空输入应报错（承 P1）")


def test_bh_alpha_must_be_explicit_and_legal():
    """Q4：`alpha` 必须显式且合法。"""
    for bad in (0, 1, -0.1, 1.5):
        try:
            benjamini_hochberg([0.01], alpha=bad)
        except ValueError:
            continue
        raise AssertionError(f"非法 alpha 应报错：{bad}")


def test_cross_layer_benjamini_hochberg_matches_backtest():
    """★ 跨层契约：本层 `benjamini_hochberg` 与 `backtest/statistics.py` 的同名函数
    **数值一致 + NaN 语义一致**。

    ⚠️ **审计补的测试（2026-10-07）**：`judgement.py` 的注释写着
       「一致性由**跨层契约测试**锁死」，但那条测试**当时并不存在**。
       补测时**真的发现分歧**：本层对 NaN 静默跳过、那边 raise —— 已修。
    """
    import importlib.util  # noqa: PLC0415

    path = FACTOR_LAYER.parent / "backtest" / "statistics.py"
    spec = importlib.util.spec_from_file_location("backtest_statistics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cases = [[0.001, 0.01, 0.02, 0.5], [0.01, 0.03, 0.04, 0.9],
             [0.02] * 10, [0.0001, 0.4, 0.5], [0.001, 0.9, 0.02]]
    for case in cases:
        ours = benjamini_hochberg(case, alpha=0.05)
        theirs = list(module.benjamini_hochberg(case, alpha=0.05).significant)
        assert theirs == ours, f"{case}：本层 {ours} vs backtest {theirs}"

    # NaN 语义也必须一致（两处都报错，不许一边静默）
    for label, fn in (("factor-layer", benjamini_hochberg),
                      ("backtest", module.benjamini_hochberg)):
        try:
            fn([float("nan"), 0.001], alpha=0.05)
        except ValueError:
            continue
        raise AssertionError(f"{label} 对 NaN 静默了 —— 两处语义必须一致（承 P5）")


# ── J4 有效样本显形 ─────────────────────────────────────────────────────

def test_j4_valid_samples_are_reported():
    """★ J4：每日有效样本数必须报出 —— 否则"那天用了 3 只还是 300 只"不可知。

    ⚠️ 非边界日必须**满样本**（噪声因子不含 NaN，标签也只有末尾 2 天缺）。
    """
    panel = _make_panel()
    report = evaluate(_noisy_factor(panel), panel, config=_config())
    assert "n_valid" in report.ic
    body = report.ic.iloc[:-TARGET_HORIZON]
    assert (body["n_valid"] == len(panel.symbols)).all(), report.log
    assert report.log["n_valid_median"] == len(panel.symbols)


def test_j4_label_boundary_shows_up_as_zero_valid():
    """★ J4：标签边界（最后 `TARGET_HORIZON` 天）→ 那几天 `n_valid = 0`，必须看得见。"""
    panel = _make_panel()
    report = evaluate(_perfect_factor(panel), panel, config=_config())
    tail = report.ic.tail(TARGET_HORIZON)
    assert (tail["n_valid"] == 0).all(), tail
    assert report.log["n_days_no_ic"] == TARGET_HORIZON


def test_j4_nan_pairs_are_excluded_not_filled():
    """★ J4：配对的 `NaN` **不计入有效样本**（不是 fillna(0) 后照算）。"""
    symbols = tuple(f"S{i}" for i in range(10))
    panel = _make_panel(symbols=symbols)
    target = forward_return(panel)
    factor = target.copy()
    factor.iloc[5, :5] = np.nan                    # 第 5 天只有 5 只有因子值
    ic = rank_ic_series(factor, target, min_samples=2)
    assert int(ic.loc[panel.dates[5], "n_valid"]) == 5, ic.iloc[5]
    assert int(ic.loc[panel.dates[6], "n_valid"]) == 10


def test_j4_no_fillna_in_factor_and_label_path():
    """★ J4：**碰因子值/标签的那几个模块**里不出现 `fillna`（0 会污染 IC）。

    范围说明：`quantile_returns.py` / `turnover.py` 里的填充是**分组计数**与
    **权重对齐**（没持有的标的权重本来就是 0），与"填因子值"是两回事 ——
    所以只扫 `forward_return.py` / `metrics/ic.py` / `evaluator.py` 这三个
    直接经手因子值与标签的模块。
    """
    scanned = ("forward_return.py", "ic.py", "evaluator.py")
    sources = _eval_sources()
    for name in scanned:
        assert name in sources, f"没扫到 {name} —— 检查范围写错了"
        assert "fillna" not in sources[name], f"{name} 出现 fillna（承 J4）"


def test_j4_positive_control_scan_is_not_empty():
    """★ 阳性对照：上一条扫的三个模块**真的存在且有内容**（不是空扫）。"""
    sources = _eval_sources()
    for name in ("forward_return.py", "ic.py", "evaluator.py"):
        assert len(sources[name]) > 200, f"{name} 内容过少 → 检查形同虚设"


def test_j4_low_sample_day_gives_nan_ic():
    """★ J4：有效样本 < `min_samples` → 该日 IC = `NaN`（**不硬算**）。"""
    symbols = tuple(f"S{i}" for i in range(10))
    panel = _make_panel(symbols=symbols)
    target = forward_return(panel)
    factor = target.copy()
    factor.iloc[5, :] = np.nan
    factor.iloc[5, :3] = 1.0                        # 只剩 3 只
    ic = rank_ic_series(factor, target, min_samples=5)
    assert math.isnan(ic.loc[panel.dates[5], "rank_ic"])
    assert int(ic.loc[panel.dates[5], "n_valid"]) == 3


# ── J5 方向归一 ─────────────────────────────────────────────────────────

def test_j5_direction_comes_from_factor_object():
    """★ J5：`evaluate` **不接受** `direction` 参数 —— 方向只能来自因子对象。"""
    import inspect

    params = inspect.signature(evaluate).parameters
    assert "direction" not in params, "方向不许从参数传（承 J5）"


def test_j5_missing_direction_raises():
    """★ J5：因子对象没有 `direction` → 报错（不猜一个默认方向）。"""
    panel = _make_panel()
    values = forward_return(panel).copy()

    class NoDirection:
        name = "x"

    NoDirection.values = values
    try:
        evaluate(NoDirection(), panel, config=_config())
    except TypeError as e:
        assert "direction" in str(e)
        return
    raise AssertionError("缺 direction 应报错（承 J5）")


def test_j5_direction_flips_monotonicity_sign():
    """★ J5：方向翻转 → 单调性符号翻转（同一个因子值，只是声明方向不同）。"""
    panel = _make_panel()
    target = forward_return(panel)
    factor = target.copy()
    qmean, _counts, _ls, _skip = quantile_returns(
        factor, target, bins=5, min_samples=10)
    assert math.isclose(monotonicity(qmean, 1), 1.0, rel_tol=1e-9)
    assert math.isclose(monotonicity(qmean, -1), -1.0, rel_tol=1e-9)


def test_j5_ic_is_also_normalized_by_direction():
    """★ J5：**IC 也按 `direction` 归一** —— 与单调性同符号。

    不归一时，报告里会出现「单调性 +1.0 但 ICIR 为负」这种**自相矛盾**的画面
    （同一件事的两种表述），评审的人会以为算错了。
    """
    panel = _make_panel()
    values = _predictive_factor(panel)
    forward = evaluate(_FakeFactor(values, direction=1), panel, config=_config())
    reverse = evaluate(_FakeFactor(-values, direction=-1), panel, config=_config())

    assert forward.ic_stats["icir"] > 0, forward.ic_stats
    assert reverse.ic_stats["icir"] > 0, (
        f"反向因子声明 -1 之后 ICIR 该是正的，实际 {reverse.ic_stats['icir']}"
    )
    assert math.isclose(forward.ic_stats["icir"], reverse.ic_stats["icir"],
                        rel_tol=1e-9), "归一后两个方向该给出同一个 ICIR"
    assert reverse.ic_stats["ic_win_rate"] > 0.5
    assert reverse.monotonicity > 0, "单调性与 IC 必须同符号"
    # 原始值**保留**（不许悄悄改掉）
    assert (reverse.ic["rank_ic"] < 0).mean() > 0.5, reverse.ic.head()


def test_j5_long_short_also_reports_normalized_value():
    """★ J5：多空收益**同时给原始值与归一值** —— 报告里不许混用两套符号约定。"""
    panel = _make_panel()
    values = _predictive_factor(panel)
    reverse = evaluate(_FakeFactor(-values, direction=-1), panel, config=_config())
    log = reverse.log
    assert "long_short_mean_raw" in log and "long_short_mean_normalized" in log
    assert math.isclose(log["long_short_mean_normalized"],
                        log["long_short_mean_raw"] * reverse.direction,
                        rel_tol=1e-12)
    assert log["long_short_mean_normalized"] > 0, log


def test_report_carries_universe_diagnostics():
    """★ 审计 A4：报告必须带**票池诊断** —— 否则幸存者偏差会被埋起来。

    看报告的人必须能一眼看到「这个票池里没有任何标的退出」，
    否则会把「IC 不显著」当成市场结论，而实际是数据局限。
    """
    panel = _make_panel()
    report = evaluate(_noisy_factor(panel), panel, config=_config())
    assert report.universe, "报告缺票池诊断（承审计 A4）"
    assert "no_symbol_ever_left" in report.universe
    assert "caveat" in report.universe
    assert report.universe["n_days"] == len(panel.dates)


def test_j5_bad_direction_raises():
    """J5：非法方向 → 报错。"""
    panel = _make_panel()
    qmean, _c, _l, _s = quantile_returns(
        forward_return(panel), forward_return(panel), bins=5, min_samples=10)
    try:
        monotonicity(qmean, 0)
    except ValueError:
        return
    raise AssertionError("非法 direction 应报错")


# ── 标签口径 ────────────────────────────────────────────────────────────

def test_forward_return_formula():
    """标签 = `log(open[t+2] / open[t+1])`（从**实际** open 序列手算比对）。"""
    panel = _make_panel(n_days=10, symbols=("S00",))
    opens = panel.field("open")["S00"]
    col = forward_return(panel)["S00"]
    for t in range(len(opens) - TARGET_HORIZON):
        expected = math.log(float(opens.iloc[t + TARGET_HORIZON])
                            / float(opens.iloc[t + ENTRY_OFFSET]))
        assert math.isclose(col.iloc[t], expected, rel_tol=1e-12), t


def test_forward_return_boundary_is_nan_not_zero():
    """★ 边界 → **NaN**（不是 0）—— 0 会被当成"收益为 0"混进 IC（承 J4）。"""
    panel = _make_panel(n_days=10, symbols=("S00",))
    col = forward_return(panel)["S00"]
    assert col.iloc[-TARGET_HORIZON:].isna().all(), col.tail().tolist()
    assert not (col.iloc[-TARGET_HORIZON:] == 0).any()


def test_forward_return_nonpositive_open_is_nan():
    """`open <= 0` → NaN（log 无定义；不取绝对值、不加 epsilon）。"""
    panel = _make_panel(n_days=10, symbols=("S00",),
                        opens={"S00": [0.0] * 5 + [100.0] * 5})
    col = forward_return(panel)["S00"]
    assert col.isna().any()


# ── 跨层契约（PRD §八 #21）──────────────────────────────────────────────

def test_cross_layer_target_contract():
    """★ 跨层契约：与 `backtest/target_label.py` 的**公式与成交约定**逐位一致。

    ⚠️ **边界处理刻意不同**（那边置 0 喂 PnL 引擎，本层置 NaN 喂 IC）——
       所以只断言**非边界**部分。这处差异已写进 `forward_return` 的 docstring。
    """
    sys.path.insert(0, str(FACTOR_LAYER.parent / "backtest"))
    import target_label  # noqa: PLC0415

    n = 30
    opens = [100.0 * (1.01 ** i) for i in range(n)]
    theirs = target_label.target_ret(opens)
    panel = _make_panel(n_days=n, symbols=("S00",), opens={"S00": opens})
    ours = forward_return(panel)["S00"]

    for t in range(n - TARGET_HORIZON):
        assert math.isclose(float(ours.iloc[t]), float(theirs[t]), rel_tol=1e-12), t
    # 边界：他们置 0、我们置 NaN
    assert all(theirs[t] == 0 for t in range(n - TARGET_HORIZON, n))
    assert ours.iloc[-TARGET_HORIZON:].isna().all()


# ── IC / 换手 的正确性 ──────────────────────────────────────────────────

def test_rank_ic_is_one_for_perfect_factor():
    """因子值 = 标签 → 每日 RankIC 恒为 1。"""
    panel = _make_panel()
    target = forward_return(panel)
    ic = rank_ic_series(target.copy(), target, min_samples=10).dropna()
    assert len(ic) > 0
    assert np.allclose(ic["rank_ic"].to_numpy(), 1.0)


def test_rank_ic_is_negative_for_reversed_factor():
    """因子值 = −标签 → 每日 RankIC 恒为 −1。"""
    panel = _make_panel()
    target = forward_return(panel)
    ic = rank_ic_series(-target, target, min_samples=10).dropna()
    assert np.allclose(ic["rank_ic"].to_numpy(), -1.0)


def test_ic_statistics_basic():
    """ICIR / 胜率 / t 检验的数值关系。"""
    ic = pd.Series([0.1] * 30 + [-0.05] * 10)
    stats = ic_statistics(ic, min_days=10)
    assert math.isclose(stats["ic_win_rate"], 0.75, rel_tol=1e-9)
    assert stats["icir"] > 0
    assert stats["p_value"] < 0.05


def test_ic_statistics_insufficient_days():
    """有效天数不足 → 统计量全 NaN（**不硬算**）。"""
    stats = ic_statistics(pd.Series([0.1, 0.2]), min_days=60)
    assert stats["n_days"] == 2
    assert math.isnan(stats["icir"]) and math.isnan(stats["p_value"])


def test_turnover_is_zero_when_ranks_never_change():
    """换手：分位成员完全不变 → 换手为 0。"""
    symbols = tuple(f"S{i}" for i in range(20))
    panel = _make_panel(symbols=symbols)
    # 因子值恒定（不随时间变）→ 每日分位成员相同 → 换手 0
    factor = pd.DataFrame({s: [float(i)] * N_DAYS
                           for i, s in enumerate(symbols)}, index=_dates(N_DAYS))
    target = forward_return(panel)
    out = turnover(factor, target, bins=5, min_samples=10)
    assert math.isclose(out["mean"], 0.0, abs_tol=1e-12), out["mean"]


def test_turnover_skipped_days_are_reported():
    """换手：跳过的日必须显形（跳过会让换手看起来更低）。"""
    panel = _make_panel()
    target = forward_return(panel)
    factor = target.copy()
    factor.iloc[5, :] = np.nan                      # 该日样本不足
    out = turnover(factor, target, bins=5, min_samples=10)
    assert panel.dates[5] in out["skipped_days"]


# ── 配置校验 ────────────────────────────────────────────────────────────

def test_config_rejects_bad_values():
    """Q4/T4：非法配置在**构造时**报错。"""
    for patch in ({"bins": 1}, {"bins": 0}, {"min_samples": 2}):
        try:
            _config(**patch)
        except ValueError:
            continue
        raise AssertionError(f"非法配置应报错：{patch}")


def test_thresholds_reject_bad_values():
    """Q4：判决阈值非法 → 构造时报错。"""
    for patch in ({"alpha": 0}, {"alpha": 1}, {"min_days": 1},
                  {"min_abs_icir": -1}):
        try:
            _thresholds(**patch)
        except ValueError:
            continue
        raise AssertionError(f"非法阈值应报错：{patch}")


def test_judge_batch_empty_input():
    """J3：空批次 → 空输出（既不报错，也不造出假判决）。"""
    assert judge_batch([], thresholds=_thresholds()) == []


def test_judge_batch_nan_p_is_insufficient_not_not_significant():
    """★ J3：`p = NaN`（检验做不了）→ `insufficient_data`，**不是** `not_significant`。

    ⚠️ 审计修正（2026-10-07）：初版把它判成 `not_significant` ——
       等于把「**没证据**」说成「**证据说无效**」。两者对研究决策的含义完全不同：
       前者该补数据，后者该放弃这个因子。
    """
    entries = [_entry(name="flat_ic", p_value=float("nan"), icir=float("nan"),
                      n_days=100, monotonicity=float("nan"))]
    out = judge_batch(entries, thresholds=_thresholds())
    assert out[0]["verdict"] == VERDICT_INSUFFICIENT
    assert out[0]["p_value_bh_significant"] is False


def test_evaluate_many_survives_one_unusable_factor():
    """★ J2/J3：**一个因子数据不足，不该让整批评估抛异常**。

    ⚠️ 审计修正（2026-10-07）：初版 `has_all_required_metrics` 把
       「实现漏算」和「数据不足」混成一个检查 ⇒ 短窗口下 `ret20`
       一个有效日都没有 → **整批 evaluate_many 抛 ValueError**。
       正确行为：该因子给 `insufficient_data`，其余照常。
    """
    panel = _make_panel(n_days=20)
    rng = np.random.default_rng(3)
    good = _FakeFactor(
        pd.DataFrame(rng.normal(size=(20, len(SYMBOLS))),
                     index=list(panel.dates), columns=list(SYMBOLS)),
        direction=1, name="good")
    empty = _FakeFactor(
        pd.DataFrame(np.nan, index=list(panel.dates), columns=list(SYMBOLS)),
        direction=1, name="all_nan")
    reports = evaluate_many([good, empty], panel, config=_config())
    assert len(reports) == 2
    by_name = {r.factor_name: r for r in reports}
    assert by_name["all_nan"].judgement["verdict"] == VERDICT_INSUFFICIENT


def test_has_all_required_metrics_is_structural_only():
    """★ J2：结构检查**只看字段在不在** —— 数据不足（全 NaN）不算"缺指标"。"""
    panel = _make_panel(n_days=20)
    report = evaluate(
        _FakeFactor(pd.DataFrame(np.nan, index=list(panel.dates),
                                 columns=list(SYMBOLS)), direction=1),
        panel, config=_config())
    assert report.has_all_required_metrics(), "字段齐全就不该报'缺指标'"
    assert math.isnan(report.monotonicity), "值可以是 NaN（数据不足）"


def test_judge_batch_untestable_entries_stay_out_of_bh_family():
    """★ Q5：无法检验的条目**不进 BH family**（否则分母虚高，把别人拖下水）。"""
    entries = [
        _entry(name="no_p", p_value=float("nan")),
        _entry(name="ok", p_value=0.01),
    ]
    out = {item["name"]: item for item in judge_batch(entries, thresholds=_thresholds())}
    assert out["no_p"]["verdict"] == VERDICT_INSUFFICIENT
    assert out["no_p"]["n_tests_in_bh"] == 1, "family 里只该有能检验的那一个"
    assert out["ok"]["p_value_bh_significant"] is True
    assert out["ok"]["n_tests"] == 2, "n_tests 报的是**试过的总数**"


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
