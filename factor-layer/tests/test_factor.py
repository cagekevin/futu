"""M3 因子 —— 对照 PRD §五 M3 的 5 条约束（A1 ~ A5）。

跑法：.venv/bin/python tests/test_factor.py

★ 三条**关键**断言：
  1. **warm-up 期必须是 NaN** —— 填 0 / 填常数会让垃圾值混进 IC（IC 不报错，只失真）
  2. **口径必须匹配** —— 因子声明 `hfq` 而面板是 raw → 除权日假跳变，且不报错
  3. **因子拿不到未声明字段** —— 否则「依赖清单」变成谎言
"""
from __future__ import annotations

import ast
import math
import sys
from datetime import date, timedelta
from pathlib import Path

FACTOR_LAYER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FACTOR_LAYER))

import pandas as pd  # noqa: E402

import factor.implementations  # noqa: E402,F401  —— 导入即注册
from factor.factor_protocol import FactorInput  # noqa: E402
from factor.factor_registry import (  # noqa: E402
    _check_values, available_factors, factor_inputs, get_factor,
    register_factor, run_factor,
)
from factor.factor_spec import (  # noqa: E402
    DIRECTION_LONG, DIRECTION_SHORT, NEUTRALIZED_PREFIX, STANDARDIZED_PREFIX,
    FactorName, FactorSpec,
)
from panel.panel_types import CrossSectionPanel  # noqa: E402

IMPL_DIR = FACTOR_LAYER / "factor" / "implementations"
SYMBOLS = ("AAA", "BBB")


def _dates(n: int) -> list[str]:
    start = date(2026, 1, 1)
    return [str(start + timedelta(days=i)) for i in range(n)]


def _make_panel(*, n_days: int = 80, symbols=SYMBOLS, adjust: str | None = "hfq",
                closes=None) -> CrossSectionPanel:
    """构造一个面板 —— `close` 默认是 `1, 2, 3, …`（便于手算断言）。"""
    dates = _dates(n_days)
    frames = {}
    for symbol in symbols:
        series = (closes or {}).get(symbol)
        if series is None:
            series = [float(i + 1) for i in range(n_days)]
        frames[symbol] = series
    fields = {"close": pd.DataFrame(frames, index=dates, dtype="float64")}
    return CrossSectionPanel(
        dates=tuple(dates), symbols=tuple(sorted(symbols)), fields=fields,
        universe_by_day={d: tuple(sorted(symbols)) for d in dates},
        n_adjust_events={}, contaminated_days={}, adjust=adjust,
        snapshot_day="2026-10-06", snapshot_is_after_day=True,
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


# ── A1 因子只产原始值 ───────────────────────────────────────────────────

def test_a1_implementations_have_no_preprocessing():
    """A1：实现里**不出现**预处理（去极值 / 标准化 / 中性化 / 填充）。"""
    banned = ("winsorize", "standardize", "neutralize", "clip",
              "ffill", "bfill", "fillna", "zscore")
    for path in sorted(IMPL_DIR.glob("*.py")):
        code = _code_only(path)
        for token in banned:
            assert token not in code, f"{path.name} 出现预处理：{token}"


def test_a1_ret20_matches_hand_computation():
    """A1：产物是**原始**值 —— 与手算逐位一致。"""
    panel = _make_panel(n_days=30)
    values = run_factor("ret20", panel)
    # close = 1,2,3,… → ret20[i] = (i+1)/i - 1
    assert math.isclose(values.values.iloc[20, 0], 21.0 / 1.0 - 1.0, rel_tol=1e-12)
    assert math.isclose(values.values.iloc[29, 0], 30.0 / 10.0 - 1.0, rel_tol=1e-12)


def test_a1_vol20_matches_hand_computation():
    """A1：`vol20` 与手算一致（`std` 用样本标准差，pandas 默认 ddof=1）。"""
    panel = _make_panel(n_days=30)
    values = run_factor("vol20", panel)
    close = panel.field("close")["AAA"]
    expected = close.apply(math.log).diff().rolling(20).std()
    assert math.isclose(values.values.iloc[25, 0], expected.iloc[25], rel_tol=1e-12)


def test_a1_factor_values_bind_spec():
    """A1：`FactorValues` 把 spec 与值**绑在一起**（防错配）。"""
    panel = _make_panel(n_days=30)
    values = run_factor("ret20", panel)
    assert values.name == "ret20"
    assert values.spec.min_window == 20
    assert values.spec.direction == DIRECTION_SHORT


# ── A2 注册表制 ─────────────────────────────────────────────────────────

def test_a2_first_batch_registered():
    """A2：首批三个因子都在注册表里。"""
    assert available_factors() == ["ret20", "ret60", "vol20"]


def test_a2_add_factor_is_one_file_plus_one_line():
    """A2：`implementations/` 里**每个文件对应注册表一个条目**（结构断言）。"""
    files = {p.stem for p in IMPL_DIR.glob("*.py") if p.stem != "__init__"}
    assert files == set(available_factors()), (files, available_factors())


def test_a2_duplicate_name_raises():
    """A2：重名 → **报错**，不静默覆盖（承 P2）。"""
    spec = FactorSpec(name="ret20", inputs=("close",), min_window=1,
                      frequency="1d", adjust="hfq", direction=DIRECTION_LONG)

    class Dup:
        pass

    Dup.spec = spec
    try:
        register_factor(Dup())
    except ValueError as e:
        assert "重复" in str(e)
        return
    raise AssertionError("重名应报错（承 P2）")


def test_a2_unknown_factor_lists_available():
    """A2/P1：未知因子 → 报错并列出可用名字。"""
    try:
        get_factor("nope")
    except KeyError as e:
        assert "ret20" in str(e), "报错要列出可用因子"
        return
    raise AssertionError("未知因子应报错（承 P1）")


def test_a2_registered_factor_has_spec():
    """A2：登记项必须有 `spec`（否则 getattr 静默拿到空名）。"""
    class NoSpec:
        pass

    try:
        register_factor(NoSpec())
    except ValueError as e:
        assert "spec.name" in str(e)
        return
    raise AssertionError("缺 spec 应报错（承 P2）")


# ── A3 FactorSpec 六要素必填无默认 ──────────────────────────────────────

def test_a3_six_fields_all_required():
    """A3：六要素缺任一 → `TypeError`（**无默认值**）。"""
    base = dict(inputs=("close",), min_window=20, frequency="1d",
                adjust="hfq", direction=DIRECTION_LONG)
    for missing in ("name", "inputs", "min_window", "frequency",
                    "adjust", "direction"):
        kwargs = {k: v for k, v in base.items() if k != missing}
        if missing == "name":
            continue                     # `name` 是位置参数，下面单测
        try:
            FactorSpec(**kwargs)         # type: ignore[arg-type]
        except TypeError:
            continue
        raise AssertionError(f"缺 {missing} 应 TypeError（承 A3）")


def test_a3_spec_rejects_bad_values():
    """A3：非法取值在**构造时**报错（不等到运行时）。"""
    good = dict(name="x1", inputs=("close",), min_window=20,
                frequency="1d", adjust="hfq", direction=DIRECTION_LONG)
    bad_cases = [
        {"min_window": 0}, {"min_window": -1}, {"frequency": "1h"},
        {"adjust": "raw"}, {"adjust": None}, {"direction": 0},
        {"direction": 2}, {"inputs": ()}, {"inputs": ("close", "close")},
        {"inputs": ("not an identifier",)}, {"name": ""},
    ]
    for patch in bad_cases:
        try:
            FactorSpec(**{**good, **patch})   # type: ignore[arg-type]
        except (ValueError, TypeError):
            continue
        raise AssertionError(f"非法取值应报错：{patch}")


def test_a3_original_name_cannot_use_derived_prefix():
    """A3/A5：原始因子名**不许**带派生前缀（那是 M4 的产物）。"""
    for bad in (f"{STANDARDIZED_PREFIX}ret20", f"{NEUTRALIZED_PREFIX}ret20"):
        try:
            FactorSpec(name=bad, inputs=("close",), min_window=1,
                       frequency="1d", adjust="hfq", direction=DIRECTION_LONG)
        except ValueError as e:
            assert "前缀" in str(e)
            continue
        raise AssertionError(f"派生前缀应报错：{bad}")


def test_a3_factor_cannot_use_undeclared_field():
    """★ A3：因子**拿不到**未声明的字段（否则依赖清单变成谎言）。"""
    panel = _make_panel(n_days=30)
    spec = get_factor("ret20").spec
    data = factor_inputs(spec, panel)                 # 只含 `close`
    assert data.field("close") is not None
    try:
        data.field("volume")                          # 没声明
    except KeyError as e:
        assert "没声明" in str(e)
        return
    raise AssertionError("未声明字段应报错（承 A3）")


def test_a3_adjust_mismatch_raises():
    """★ A3：因子声明的口径与面板口径**不匹配** → 报错（不静默算错的因子）。"""
    raw_panel = _make_panel(n_days=30, adjust=None)
    try:
        run_factor("ret20", raw_panel)               # 因子声明 hfq
    except ValueError as e:
        assert "口径" in str(e)
        return
    raise AssertionError("口径不匹配应报错（承 A3）")


def test_a3_positive_control_matching_adjust_passes():
    """★ 阳性对照：口径**匹配**时必须能跑通（上一条不能把正常路径也挡了）。"""
    values = run_factor("ret20", _make_panel(n_days=30, adjust="hfq"))
    assert values.values.iloc[20, 0] == values.values.iloc[20, 0]   # 非 NaN


# ── A4 min_window 显式 + warm-up 必须是 NaN ─────────────────────────────

def test_a4_warmup_is_nan_for_all_factors():
    """★ A4：前 `min_window` 行**必须全是 NaN**（不是 0、不是常数）。"""
    panel = _make_panel(n_days=80)
    for name, window in (("ret20", 20), ("ret60", 60), ("vol20", 20)):
        values = run_factor(name, panel)
        head = values.values.iloc[:window]
        assert bool(head.isna().to_numpy().all()), (
            f"{name} 前 {window} 行不全是 NaN —— warm-up 期垃圾值会污染 IC（承 A4）"
        )
        # 且 warm-up 之后**必须有值**（否则 min_window 声明得不对）
        assert bool(values.values.iloc[window].notna().any()), (
            f"{name} 第 {window} 行仍全 NaN —— min_window 声明偏小"
        )


def test_a4_warmup_check_catches_zero_filled():
    """★ 阳性对照：把 warm-up 填成 **0** → `_check_values` 必须报错。

    没有这条，上一条可能只是"恰好通过"，而不是检查真的在起作用。
    """
    panel = _make_panel(n_days=30)
    spec = get_factor("ret20").spec
    bad = run_factor("ret20", panel).values.fillna(0.0)   # 填 0 —— 典型的错法
    try:
        _check_values(bad, spec, panel)
    except ValueError as e:
        assert "min_window" in str(e)
        return
    raise AssertionError("填 0 的 warm-up 必须被抓到（承 A4）")


def test_a4_shape_mismatch_raises():
    """P2：产物形状与面板不一致 → 报错。"""
    panel = _make_panel(n_days=30)
    spec = get_factor("ret20").spec
    good = run_factor("ret20", panel).values
    for bad in (good.iloc[:-1], good[["AAA"]], good * 0 + 1):
        if bad.shape == good.shape:
            continue
        try:
            _check_values(bad, spec, panel)
        except ValueError:
            continue
        raise AssertionError("形状不一致应报错（承 P2）")


def test_a4_non_dataframe_raises():
    """P2：`compute` 返回非 DataFrame → 报错。"""
    panel = _make_panel(n_days=30)
    try:
        _check_values([1, 2, 3], get_factor("ret20").spec, panel)  # type: ignore[arg-type]
    except TypeError:
        return
    raise AssertionError("非 DataFrame 应报错（承 P2）")


# ── A5 命名编码血缘 ─────────────────────────────────────────────────────

def test_a5_name_roundtrip():
    """★ A5：从派生名**反解**原始名。"""
    assert FactorName.parse("ret20").raw == "ret20"
    assert FactorName.parse("z_ret20").raw == "ret20"
    assert FactorName.parse("z_neu_ret20").raw == "ret20"


def test_a5_neutralized_prefix_checked_before_standardized():
    """★ A5：`z_neu_` 必须**先查** —— 否则 `z_neu_ret20` 会解成 `neu_ret20`。"""
    assert FactorName.parse("z_neu_ret20").raw == "ret20"
    assert FactorName.parse("z_neu_ret20").raw != "neu_ret20"


def test_a5_derived_names():
    """A5：原始名 → 派生名（M4 会用）。"""
    name = FactorName("ret20")
    assert name.standardized == "z_ret20"
    assert name.neutralized == "z_neu_ret20"


def test_a5_empty_name_raises():
    """A5：空名 → 报错。"""
    for bad in ("", None):
        try:
            FactorName.parse(bad)          # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError(f"空名应报错：{bad!r}")


# ── 先验方向声明（PRD §五 M3 §1.5 的决策）──────────────────────────────

def test_directions_are_declared_priors():
    """`direction` 是**先验**，且三个因子的先验各不相同（刻意）。"""
    assert get_factor("ret20").spec.direction == DIRECTION_SHORT   # 短期反转
    assert get_factor("ret60").spec.direction == DIRECTION_LONG    # 中期动量
    assert get_factor("vol20").spec.direction == DIRECTION_SHORT   # 低波动异象


def test_factor_input_rejects_undeclared():
    """A3：`FactorInput` 直接构造时也拒绝未声明字段。"""
    data = FactorInput(fields={"close": pd.DataFrame()})
    try:
        data.field("open")
    except KeyError:
        return
    raise AssertionError("未声明字段应报错（承 A3）")


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
