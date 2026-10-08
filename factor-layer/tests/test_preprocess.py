"""M4 预处理 —— 对照 PRD §五 M4 的 5 条约束（T1 ~ T5）。

跑法：.venv/bin/python tests/test_preprocess.py

★ 三条**关键**断言：
  1. **顺序固定**（T1）—— 行为级：`winsorize→zscore` 的结果 ≠ `zscore→winsorize`
  2. **残差与 size 正交**（T3）—— OLS 残差与回归元正交，这是**实现正确性的探针**
  3. **原始值不被就地修改**（T5）
"""
from __future__ import annotations

import ast
import math
import sys
from pathlib import Path

FACTOR_LAYER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FACTOR_LAYER))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from exposure.exposure_types import ExposureSet  # noqa: E402
from factor.factor_protocol import FactorValues  # noqa: E402
from factor.factor_spec import (  # noqa: E402
    DIRECTION_SHORT, ROLE_ALPHA, FactorSpec,
)
from preprocess.factor_impute import impute  # noqa: E402
from preprocess.factor_neutralize import neutralize  # noqa: E402
from preprocess.factor_standardize import standardize  # noqa: E402
from preprocess.factor_winsorize import winsorize  # noqa: E402
from preprocess.preprocess_pipeline import (  # noqa: E402
    PreprocessConfig, derived_name, read_preprocessed_factor,
)

PRE_DIR = FACTOR_LAYER / "preprocess"
DAY = "2026-09-30"

#: 12 只 / 3 个行业（各 4 只）—— 够 `industry_dummies` 去一列后仍有 2 列。
SYMBOLS = tuple(f"S{i:02d}" for i in range(12))
INDUSTRY = {s: f"行业{i % 3}" for i, s in enumerate(SYMBOLS)}
SIZE = {s: 20.0 + 0.5 * i for i, s in enumerate(SYMBOLS)}
#: factor 与 size **强相关**（否则"中性化"测不出任何东西）。
FACTOR = {s: 2.5 * SIZE[s] + (0.1 if i % 2 else -0.1)
          for i, s in enumerate(SYMBOLS)}


def _config(**patch) -> PreprocessConfig:
    base = dict(winsorize_method="none", winsorize_n=5.0,
                impute_method="none", impute_threshold=0.5,
                standardize_method="none", neutralize_method="none")
    return PreprocessConfig(**{**base, **patch})


def _values(data=None, *, symbols=SYMBOLS, dates=(DAY,)) -> FactorValues:
    rows = data if data is not None else FACTOR
    spec = FactorSpec(name="ret20", inputs=("close",), min_window=20,
                      frequency="1d", adjust="hfq", direction=DIRECTION_SHORT,
                      role=ROLE_ALPHA)
    return FactorValues(spec=spec, values=pd.DataFrame(
        {s: [float(rows[s]) for _d in dates] for s in symbols},
        index=list(dates)))


def _exposures(*, symbols=SYMBOLS, dates=(DAY,), size=None, industry=None
               ) -> ExposureSet:
    size = SIZE if size is None else size
    industry = INDUSTRY if industry is None else industry
    return ExposureSet(
        dates=tuple(dates), symbols=tuple(symbols),
        size=pd.DataFrame({s: [float(size[s]) for _d in dates] for s in symbols},
                          index=list(dates)),
        industry=pd.DataFrame({s: [industry[s] for _d in dates] for s in symbols},
                              index=list(dates)),
        min_industry_count=3, coverage={},
        size_is_approximated={d: True for d in dates},
        industry_is_approximated={d: True for d in dates},
    )


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


# ── T1 顺序固定 ─────────────────────────────────────────────────────────

def test_t1_order_is_fixed_behaviorally():
    """★ T1：**行为级**验证 —— 调换顺序结果必然不同。

    构造一份"极端值"数据：先去极值（clip 掉 1000）再标准化，与
    先标准化（1000 把 std 拉爆）再去极值，结果完全不同。
    """
    symbols = tuple(f"X{i}" for i in range(11))
    raw = {s: (1000.0 if i == 10 else float(i + 1)) for i, s in enumerate(symbols)}
    panel = _values(raw, symbols=symbols)
    exposures = _exposures(
        symbols=symbols,
        size={s: 10.0 + i for i, s in enumerate(symbols)},
        industry={s: f"g{i % 3}" for i, s in enumerate(symbols)})

    in_order = read_preprocessed_factor(
        panel, exposures,
        _config(winsorize_method="mad", standardize_method="zscore"))
    swapped = winsorize(
        standardize(panel.values, method="zscore")[0], method="mad", n=5.0)[0]

    assert not np.allclose(in_order.values.to_numpy(), swapped.to_numpy(),
                           equal_nan=True), "顺序调换后结果竟然一样 —— 顺序没被固定？"
    assert np.allclose(in_order.values.to_numpy(),
                       winsorize(panel.values, method="mad", n=5.0)[0]
                       .pipe(lambda f: standardize(f, method="zscore")[0])
                       .to_numpy(), equal_nan=True), "结果不是「先去极值再标准化」"


def test_t1_pipeline_calls_steps_in_fixed_order():
    """★ T1：编排里四步的**调用顺序**固定（写死，不是配置项）。"""
    tree = ast.parse((PRE_DIR / "preprocess_pipeline.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "read_preprocessed_factor")
    calls = [(n.lineno, n.func.id) for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    steps = [name for _ln, name in sorted(calls)
             if name in ("winsorize", "impute", "standardize", "neutralize")]
    assert steps == ["winsorize", "impute", "standardize", "neutralize"], steps


# ── T2 每步改动量显形 ───────────────────────────────────────────────────

def test_t2_log_reports_every_step():
    """T2：日志必须含「clip 几个 / 填几个 / 剔除几个」+ 相关验收值。"""
    values = _values()
    values.values.loc[DAY, "S00"] = np.nan                 # 造一个缺失
    result = read_preprocessed_factor(
        values, _exposures(),
        _config(winsorize_method="mad", impute_method="cross_section_median",
                standardize_method="zscore", neutralize_method="size_industry"))
    for key in ("winsorize_clipped", "impute_filled", "neutralize_dropped",
                "neutralize_max_abs_corr_size", "source_name"):
        assert key in result.log, f"日志缺 {key}（承 T2）"
    assert result.log["impute_filled"] == 1, "填了几个必须数出来"


def test_t2_empty_days_are_not_counted_as_degenerate():
    """★ T2：**空日**（全 NaN，如 warm-up）与**退化日**（有值但尺度为 0）必须分开。

    混在一起报一个数，会让"warm-up 期"看起来像"数据有问题"——
    而真正的退化（std/MAD == 0）反而被淹没。
    """
    frame = pd.DataFrame([[np.nan, np.nan], [5.0, 5.0]], index=["empty", "degen"],
                         columns=["A", "B"])
    _z, log = standardize(frame, method="zscore")
    assert log["standardize_empty_days"] == 1, log
    assert log["standardize_degenerate_days"] == 1, log

    _c, log = winsorize(frame, method="mad", n=5.0)
    assert log["winsorize_empty_days"] == 1, log
    assert log["winsorize_degenerate_days"] == 1, log

    _f, log = impute(frame, method="cross_section_median", threshold=0.5)
    assert log["impute_empty_days"] == 1, log


def test_t2_impute_reports_days_over_threshold():
    """T2：某日填充比例超阈值 → 必须**显形**（否则下游以为数据是全的）。"""
    symbols = tuple(f"Y{i}" for i in range(4))
    # 4 只里缺 2 只 → 填充比例 50% ≥ 阈值 40% → 必须被记进日志
    frame = pd.DataFrame([[1.0, 2.0, np.nan, np.nan]], index=[DAY],
                         columns=list(symbols))
    _filled, log = impute(frame, method="cross_section_median", threshold=0.4)
    assert log["impute_days_over_threshold"] == [DAY], log


# ── T3 中性化验收（残差与回归元正交）────────────────────────────────────

def test_t3_residual_is_orthogonal_to_size():
    """★ T3：中性化后残差与 `log 市值` 的截面相关 **< 0.02**。

    OLS 残差与所有回归元正交 —— 这是**数学性质**，所以这条不是"可能失败"，
    而是**实现正确性的探针**（漏截距 / 没真回归 / 哑变量没去列 都会打破它）。
    """
    result = read_preprocessed_factor(
        _values(), _exposures(),
        _config(standardize_method="zscore", neutralize_method="size_industry"))
    assert result.log["neutralize_max_abs_corr_size"] < 0.02, result.log

    # 直接用数据再验一次（不只看日志里的自报值）
    residual = result.values.loc[DAY]
    size = _exposures().size.loc[DAY]
    valid = residual.notna() & size.notna()
    corr = float(np.corrcoef(residual[valid], size[valid])[0, 1])
    assert abs(corr) < 0.02, f"残差与 size 相关 {corr}"


def test_t3_positive_control_naive_demean_is_correlated():
    """★ 阳性对照：**不做回归**（只减均值）时，残差与 size **强相关**。

    没有这条，上一条可能只是"恰好通过"，而不是回归真的在起作用。
    """
    residual = _values().values.loc[DAY] - _values().values.loc[DAY].mean()
    size = _exposures().size.loc[DAY]
    corr = float(np.corrcoef(residual, size)[0, 1])
    assert abs(corr) > 0.9, f"只减均值的残差本该与 size 强相关，实测 {corr}"


def test_t3_industry_neutralization_actually_removes_industry_effect():
    """T3：行业中性化必须**真的**扣掉行业效应。"""
    # 每个行业的因子整体抬高，行业内的差异才是"个股 alpha"
    tilted = {s: FACTOR[s] + 10.0 * (int(s[1:]) % 3) for s in SYMBOLS}
    result = read_preprocessed_factor(
        _values(tilted), _exposures(),
        _config(standardize_method="zscore", neutralize_method="industry"))
    day = result.values.loc[DAY]
    for group in set(INDUSTRY.values()):
        members = [s for s in SYMBOLS if INDUSTRY[s] == group]
        assert abs(float(day[members].mean())) < 1e-9, (
            f"行业 {group} 的中性化后均值不为 0 —— 行业效应没被扣掉"
        )


# ── T4 配置 frozen + 构造时校验 ─────────────────────────────────────────

def test_t4_config_is_frozen():
    """T4：配置是 `frozen` —— 改它必须报错（不许中途改口径）。"""
    config = _config()
    try:
        config.winsorize_n = 9.0        # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("frozen 配置不该能被改（承 T4）")


def test_t4_config_rejects_bad_values_at_construction():
    """T4：非法参数在**构造时**报错（不等到运行时）。"""
    bad = [
        {"winsorize_method": "magic"}, {"impute_method": None},
        {"standardize_method": "minmax"}, {"neutralize_method": None},
        {"neutralize_method": "both"}, {"winsorize_n": 0},
        {"winsorize_n": -1}, {"impute_threshold": 0}, {"impute_threshold": 1.5},
    ]
    for patch in bad:
        try:
            _config(**patch)
        except ValueError:
            continue
        raise AssertionError(f"非法配置应报错：{patch}")


def test_t4_neutralize_method_forbids_none():
    """Q4：`neutralize_method` **禁止 `None`** —— 要"不做"必须显式写 `"none"`。"""
    try:
        _config(neutralize_method=None)
    except ValueError:
        return
    raise AssertionError("None 应被拒绝（承 Q4：口径消耗必须显形）")


# ── T5 派生因子必须改名，禁止原地覆盖 ───────────────────────────────────

def test_t5_derived_name():
    """T5/A5：只标准化 → `z_`；做了中性化 → `z_neu_`。"""
    assert derived_name("ret20", _config(standardize_method="zscore")) == "z_ret20"
    assert derived_name(
        "ret20", _config(standardize_method="zscore",
                         neutralize_method="size_industry")) == "z_neu_ret20"


def test_t5_original_values_are_not_modified():
    """★ T5：**原始值不许被就地修改** —— 每一步都返回新表。"""
    values = _values()
    before = values.values.copy(deep=True)
    read_preprocessed_factor(
        values, _exposures(),
        _config(winsorize_method="mad", impute_method="cross_section_median",
                standardize_method="zscore", neutralize_method="size_industry"))
    pd.testing.assert_frame_equal(values.values, before)


def test_t5_pipeline_result_keeps_source_lineage():
    """T5：产物保留**原始 spec**（血缘）—— 改名后仍能追回来源。"""
    result = read_preprocessed_factor(
        _values(), _exposures(),
        _config(standardize_method="zscore", neutralize_method="size_industry"))
    assert result.name == "z_neu_ret20"
    assert result.source.name == "ret20"
    assert result.source.min_window == 20


# ── 各步算法本身 ────────────────────────────────────────────────────────

def test_winsorize_clips_extremes():
    """去极值：极端值被 clip 到 `median ± n × MAD`，且计数正确。"""
    symbols = tuple(f"W{i}" for i in range(11))
    frame = pd.DataFrame([[float(i + 1) if i < 10 else 1000.0 for i in range(11)]],
                         index=[DAY], columns=list(symbols))
    clipped, log = winsorize(frame, method="mad", n=5.0)
    assert log["winsorize_clipped"] == 1
    assert clipped.loc[DAY, "W10"] < 1000.0


def test_winsorize_degenerate_day_not_flattened():
    """⚠️ `MAD == 0`（当日值几乎全相同）→ **不 clip**，并显形。

    否则会把整日压成一条直线 —— 那是"猜"出来的数据。
    """
    symbols = tuple(f"W{i}" for i in range(11))
    row = [5.0] * 10 + [1000.0]
    frame = pd.DataFrame([row], index=[DAY], columns=list(symbols))
    clipped, log = winsorize(frame, method="mad", n=5.0)
    assert log["winsorize_degenerate_days"] == 1
    assert clipped.loc[DAY, "W10"] == 1000.0, "退化日不该被压平"


def test_standardize_degenerate_day_is_nan():
    """标准化：`std == 0` 的交易日 → 整行 `NaN`（**不返回 0** —— 那是编信息）。"""
    frame = pd.DataFrame([[3.0, 3.0, 3.0]], index=[DAY], columns=["A", "B", "C"])
    z, log = standardize(frame, method="zscore")
    assert log["standardize_degenerate_days"] == 1
    assert bool(z.loc[DAY].isna().all()), "退化日必须是 NaN，不是 0"


def test_neutralize_drops_missing_exposure_and_counts():
    """U1/U2/K4：size 或行业缺失的标的 → **当日剔除并计数**。"""
    industry = dict(INDUSTRY)
    industry["S00"] = None                      # 缺行业
    size = dict(SIZE)
    size["S01"] = np.nan                        # 缺 size
    frame = _values().values
    residual, log = neutralize(frame, _exposures(size=size, industry=industry),
                               method="size_industry")
    assert log["neutralize_dropped"] == 2, log
    assert math.isnan(residual.loc[DAY, "S00"]) and math.isnan(residual.loc[DAY, "S01"])


def test_neutralize_deficient_day_is_nan_not_garbage():
    """自由度不足的交易日 → 整行 `NaN`，并**显形**（不硬回归出垃圾）。"""
    symbols = ("A", "B")
    frame = pd.DataFrame([[1.0, 2.0]], index=[DAY], columns=list(symbols))
    exposures = _exposures(symbols=symbols, size={"A": 1.0, "B": 2.0},
                           industry={"A": "x", "B": "y"})
    residual, log = neutralize(frame, exposures, method="size_industry")
    assert log["neutralize_deficient_days"] == [DAY]
    assert bool(residual.loc[DAY].isna().all())


# ── 静态检查：只有 impute 允许填充 ──────────────────────────────────────

def test_only_impute_module_may_fill():
    """静态检查：除 `factor_impute.py` 外，其它模块**不出现**填充。

    `factor_impute.py` 是**显式配置的补缺步骤**（且填了多少会报出来），
    所以它合法；其余地方出现填充就是"静默兜底"（承 K4/P6）。
    """
    for path in sorted(PRE_DIR.glob("*.py")):
        if path.name == "factor_impute.py":
            continue
        code = _code_only(path)
        for token in ("ffill", "bfill", "fillna"):
            assert token not in code, f"{path.name} 出现填充：{token}"


def test_only_impute_module_positive_control():
    """★ 阳性对照：上一条必须**真的**在起作用 —— `factor_impute.py` 里确实有填充机制。"""
    code = _code_only(PRE_DIR / "factor_impute.py")
    assert "mask(" in code, "impute 模块里没有填充机制 → 上一条检查形同虚设"


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
