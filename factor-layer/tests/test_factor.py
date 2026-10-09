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
    DIRECTION_LONG, DIRECTION_SHORT, NEUTRALIZED_PREFIX, ROLE_ALPHA,
    STANDARDIZED_PREFIX, FactorName, FactorSpec,
)
from panel.panel_types import CrossSectionPanel  # noqa: E402

IMPL_DIR = FACTOR_LAYER / "factor" / "implementations"
SYMBOLS = ("AAA", "BBB")


def _dates(n: int) -> list[str]:
    start = date(2026, 1, 1)
    return [str(start + timedelta(days=i)) for i in range(n)]


def _make_panel(*, n_days: int = 80, symbols=SYMBOLS, adjust: str | None = "hfq",
                closes=None) -> CrossSectionPanel:
    """构造一个面板 —— `close` 默认是 `1, 2, 3, …`（便于手算断言）。

    ⚠️ **夹具必须覆盖所有因子声明过的字段**，否则「遍历注册表」的测试会 `KeyError`
       —— 那会让"加因子零改动"被**夹具**卡住，而不是被代码卡住。当前需要：
       `close`（多数）/ `volume`（`turn20`）/ `high` + `low`（`atr*` / `adr20` / `ma_dist_*`）。

    `high = close + 1`、`low = close − 1` 的取法**不是随手挑的**：
      `close` 每日 +1 ⇒ 真实波幅 `TR = max(2, |high−prev_close|, |low−prev_close|) ≡ 2`
      ⇒ **`ATR(14) ≡ 2.0`**，手算断言最省事。
    """
    dates = _dates(n_days)
    frames = {}
    for symbol in symbols:
        series = (closes or {}).get(symbol)
        if series is None:
            series = [float(i + 1) for i in range(n_days)]
        frames[symbol] = series
    close = pd.DataFrame(frames, index=dates, dtype="float64")
    fields = {
        "close": close,
        # 高低价：让 TR 恒为 2（⇒ ATR ≡ 2.0），且 high−low = 2
        "high": close + 1.0,
        "low": close - 1.0,
        # 成交量：默认**恒定**（`turn20` = volume / 基线 = 1.0，便于断言）
        "volume": pd.DataFrame({s: [1.0] * n_days for s in symbols},
                               index=dates, dtype="float64"),
    }
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
    """A2：注册表的**全量清单**（20 个）。

    ⚠️ 这个列表**故意写死** —— 加因子必须同时改这里，逼作者确认"确实是有意加的"，
       而不是被某个 import 顺带注册进来（承 A2：登记是显式动作）。

    构成（2026-10-08）：
      - 收益族：`ret5` / `ret20` / `ret60` / `ret150` / `ret260`
      - 离底族：`off_low150` / `off_low260`
      - 矩 / 极值 / 量能：`vol20` / `skew20` / `max20` / `turn20`
      - 技术指标（11.5 的原料）：`rsi14` / `atr14` / `atr_pct14` / `adr20`
      - 均线距离（`screening`，条件②）：`ma_dist_ema10/20/50`、`ma_dist_sma150/200`
    """
    assert available_factors() == [
        "adr20", "atr14", "atr_pct14", "daily_range_pct",
        "ma_dist_ema10", "ma_dist_ema20", "ma_dist_ema50",
        "ma_dist_sma150", "ma_dist_sma200",
        "max20", "near_52w_high", "off_low150", "off_low260",
        "range_pct10", "ret150", "ret20", "ret260", "ret5", "ret60",
        "rs_rank", "rs_rank_1m", "rs_rank_6m",
        "rsi14", "skew20", "turn20", "vol20", "vol_ratio10_50",
    ]


def test_a2_add_factor_is_one_file_plus_one_line():
    """A2：`implementations/` 里**每个文件对应注册表一个条目**（结构断言）。"""
    files = {p.stem for p in IMPL_DIR.glob("*.py") if p.stem != "__init__"}
    assert files == set(available_factors()), (files, available_factors())


def test_a2_duplicate_name_raises():
    """A2：重名 → **报错**，不静默覆盖（承 P2）。"""
    spec = FactorSpec(name="ret20", inputs=("close",), min_window=1,
                      frequency="1d", adjust="hfq", direction=DIRECTION_LONG,
                      role=ROLE_ALPHA)

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


# ── A3 FactorSpec 七要素必填无默认 ──────────────────────────────────────

def test_a3_seven_fields_all_required():
    """A3：**七要素**缺任一 → `TypeError`（**无默认值**）。

    `role` 是 2026-10-08 追加的第七要素（见 `factor_spec` 的契约变更说明）：
    默认成 `alpha` 会让**筛选原料**（均线 / RSI…）悄悄混进因子评估。
    """
    base = dict(inputs=("close",), min_window=20, frequency="1d",
                adjust="hfq", direction=DIRECTION_LONG, role=ROLE_ALPHA)
    for missing in ("name", "inputs", "min_window", "frequency",
                    "adjust", "direction", "role"):
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
                frequency="1d", adjust="hfq", direction=DIRECTION_LONG,
                role=ROLE_ALPHA)
    bad_cases = [
        {"min_window": 0}, {"min_window": -1}, {"frequency": "1h"},
        {"adjust": "raw"}, {"adjust": None}, {"direction": 0},
        {"direction": 2}, {"inputs": ()}, {"inputs": ("close", "close")},
        {"inputs": ("not an identifier",)}, {"name": ""},
        {"role": "unknown"}, {"role": None}, {"role": "raw"},
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
                       frequency="1d", adjust="hfq", direction=DIRECTION_LONG,
                       role=ROLE_ALPHA)
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

def test_a4_warmup_is_nan_for_every_registered_factor():
    """★ A4：**每一个已注册因子**的前 `min_window` 行都必须全是 NaN。

    ⚠️ 遍历注册表（不是写死三个）—— 新加的因子**自动**受这条约束，
       不需要有人记得来补测试（承 A2：加因子零改动）。
    """
    # ⚠️ 面板长度**从注册表算出来**，不写死 —— 否则加一个长窗口因子
    #    （如 `off_low260`）就会让本测试 `iloc[260]` 越界。
    longest = max(get_factor(n).spec.min_window for n in available_factors())
    panel = _make_panel(n_days=longest + 5)
    for name in available_factors():
        spec = get_factor(name).spec
        values = run_factor(name, panel)
        window = spec.min_window
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


# ── role（2026-10-08 契约变更：七要素）──────────────────────────────────

def test_role_split_isolates_moving_average_distances():
    """★ role 的分界线：**均线距离 = `screening`，其余 = `alpha`**。

    ⚠️ 本用例**替换**了上一版「首批应当没有 screening 因子」的断言 ——
       那条的**前提**（因子只有收益 / 波动类）在 2026-10-08 加入技术指标后**不再成立**。
       留着它只会让"加了一层正确的东西"看起来像失败。
       ⇒ 改为断言**分界本身**，并且**不依赖测试执行顺序**（只看 `ma_dist_*` 那一族）。
    """
    from factor.factor_registry import alpha_factors

    screening = set(available_factors()) - set(alpha_factors())
    # ① 均线距离一族**必须**在 screening 里（排序它 = 排序价格 ⇒ 不得当 alpha 评）
    ma_like = sorted(n for n in screening if n.startswith("ma_dist_"))
    assert ma_like == [
        "ma_dist_ema10", "ma_dist_ema20", "ma_dist_ema50",
        "ma_dist_sma150", "ma_dist_sma200",
    ], ma_like
    # ② 应有的 screening 因子**必须都在**（写死 ⇒ 加 screening 因子必须来改这里）
    #
    # ⚠️ 这里**故意用子集而不是全等**：注册表是**全局**的，而本条之前
    #    `test_role_screening_is_excluded_from_alpha_factors` 会往里面塞一个
    #    测试用的 screening 因子。用全等会让本测试**依赖执行顺序** ——
    #    那种绿是假的绿。（反过来说：**跨测试污染注册表**是这个套件的已知代价。）
    expected_screening = {
        "daily_range_pct",
        "ma_dist_ema10", "ma_dist_ema20", "ma_dist_ema50",
        "ma_dist_sma150", "ma_dist_sma200",
        "range_pct10", "vol_ratio10_50",
    }
    assert expected_screening <= screening, sorted(expected_screening - screening)
    # ③ 反向：alpha 里**不许**出现均线距离
    assert all(not n.startswith("ma_dist_") for n in alpha_factors())


def test_role_screening_is_excluded_from_alpha_factors():
    """★ `screening` 类**不得**混进 `alpha_factors()`（评估只跑 alpha）。

    否则均线这类"价格的平滑"会被当成 alpha 评估 ⇒ 产出一批假阳性
    （价格本身有趋势）—— 这正是 role 要防的。
    """
    from factor.factor_registry import alpha_factors
    from factor.factor_spec import ROLE_SCREENING

    name = "ztest_screening_only"
    assert name not in available_factors()

    class ScreeningOnly:
        spec = FactorSpec(name=name, inputs=("close",), min_window=5,
                          frequency="1d", adjust="hfq",
                          direction=DIRECTION_LONG, role=ROLE_SCREENING)

        def compute(self, data):
            return data.field("close")

    register_factor(ScreeningOnly())
    assert name in available_factors(), "全量清单里应当有它"
    assert name not in alpha_factors(), "screening 不该出现在 alpha 清单里"


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
