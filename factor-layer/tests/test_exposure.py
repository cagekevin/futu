"""M2 暴露 —— 对照 PRD §五 M2 的 5 条约束（U1 ~ U5）。

跑法：.venv/bin/python tests/test_exposure.py

★ 两条**关键**断言（M2 最容易静默出错的地方）：
  1. **市值反推必须用 raw close**（不传 `--adjust`）—— 用 hfq 会把横截面排序拧歪
  2. **`absorbed` 必须为 0** —— 只有 1 只的行业哑变量会**完全吸收**该标的（残差恒为 0）
"""
from __future__ import annotations

import ast
import math
import sys
from pathlib import Path

FACTOR_LAYER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FACTOR_LAYER))

import pandas as pd  # noqa: E402

from exposure.exposure_builder import read_exposures  # noqa: E402
from exposure.exposure_coverage import coverage_report  # noqa: E402
from exposure.exposure_types import (  # noqa: E402
    INDUSTRY_OTHER, MIN_INDUSTRY_COUNT_FLOOR, MIN_INDUSTRY_COUNT_RECOMMENDED,
)
from exposure.industry_exposure import (  # noqa: E402
    build_industry_exposure, industry_labels,
)
from exposure.size_exposure import build_size_exposure, implied_shares  # noqa: E402
from panel.panel_types import CrossSectionPanel  # noqa: E402

EXPOSURE_DIR = FACTOR_LAYER / "exposure"

DAY = "2026-09-30"
SNAPSHOT_DAY = "2026-10-06"


# ── 夹具 ────────────────────────────────────────────────────────────────

#: 7 只：半导体 ×3、软件 ×3、稀有 ×1（阈值 3 下"稀有"必须归 other）。
SNAPSHOT_ROWS = [
    {"symbol": "AAA", "industry": "半导体", "price": 10.0, "market_cap": 1000.0},
    {"symbol": "BBB", "industry": "半导体", "price": 10.0, "market_cap": 1000.0},
    {"symbol": "CCC", "industry": "半导体", "price": 10.0, "market_cap": 1000.0},
    {"symbol": "DDD", "industry": "软件", "price": 10.0, "market_cap": 1000.0},
    {"symbol": "EEE", "industry": "软件", "price": 10.0, "market_cap": 1000.0},
    {"symbol": "FFF", "industry": "软件", "price": 10.0, "market_cap": 1000.0},
    {"symbol": "GGG", "industry": "稀有", "price": 10.0, "market_cap": 1000.0},
]
SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG")


def _make_panel(*, dates=(DAY,), symbols=SYMBOLS, closes=None,
                present=None, snapshot_day=SNAPSHOT_DAY) -> CrossSectionPanel:
    """直接构造 `CrossSectionPanel` —— 让 M2 的测试只测 M2，不牵扯取数。"""
    closes = closes or {s: 10.0 for s in symbols}
    fields = {"close": pd.DataFrame(
        {s: [float(closes.get(s, 10.0)) for _d in dates] for s in symbols},
        index=list(dates))}
    return CrossSectionPanel(
        dates=tuple(dates), symbols=tuple(symbols), fields=fields,
        universe_by_day=present or {d: tuple(symbols) for d in dates},
        n_adjust_events={}, contaminated_days={},
        adjust="hfq",
        snapshot_day=snapshot_day, snapshot_is_after_day=True,
    )


def _m2_runner(*, dates=(DAY,), symbols=SYMBOLS, closes=None,
               rows=None, calls=None):
    """假 runner：处理 `panel`（raw）与 `get`（snapshot）两类调用。"""
    calls = calls if calls is not None else []
    closes = closes or {s: 10.0 for s in symbols}
    rows = SNAPSHOT_ROWS if rows is None else rows

    def runner(*args):
        calls.append(args)
        if args[0] == "panel":
            def price_of(symbol):
                return float(closes.get(symbol, 10.0))
            return {
                "item": "kline", "days": list(dates), "symbols": list(symbols),
                "adjust": None, "contaminated_days": {}, "n_adjust_events": {},
                "values": {s: {d: {"open": price_of(s), "high": price_of(s),
                                   "low": price_of(s), "close": price_of(s),
                                   "volume": 1.0}
                               for d in dates} for s in symbols},
            }
        if args[0] == "get":
            return {"day": args[2], "symbol": "UNIVERSE", "item": "snapshot",
                    "value": {"market": "US", "rows": rows}}
        raise AssertionError(f"未预期的子命令 {args[0]!r}")

    return runner, calls


def _read(**kw):
    panel = kw.pop("panel", None) or _make_panel(**{
        k: v for k, v in kw.items() if k in ("dates", "symbols", "closes",
                                             "present", "snapshot_day")})
    runner, calls = _m2_runner(**{k: v for k, v in kw.items()
                                  if k in ("dates", "symbols", "closes", "rows")})
    return read_exposures(panel, runner=runner), calls


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


# ── U1 size = log(市值)；缺失 → NaN，不填 0、不前值填充 ─────────────────

def test_u1_size_is_log_of_shares_times_raw_close():
    """U1：`size = log(股本 × raw_close)`，股本 = `market_cap / price`。"""
    shares = implied_shares(SNAPSHOT_ROWS)
    assert shares["AAA"] == 100.0                       # 1000 / 10
    raw = pd.DataFrame({"AAA": [10.0]}, index=[DAY])
    size = build_size_exposure(raw, shares)
    assert math.isclose(size.at[DAY, "AAA"], math.log(100.0 * 10.0), rel_tol=1e-12)


def test_u1_missing_shares_gives_nan_column():
    """U1：快照里没有该标的（缺股本）→ **整列 NaN**（不填 0、不用别的值替代）。"""
    raw = pd.DataFrame({"AAA": [10.0], "ZZZ": [7.0]}, index=[DAY])
    size = build_size_exposure(raw, implied_shares(SNAPSHOT_ROWS))
    assert not math.isnan(size.at[DAY, "AAA"])
    assert math.isnan(size.at[DAY, "ZZZ"]), "缺股本必须 NaN，不许兜底"


def test_u1_nonpositive_close_gives_nan():
    """U1：`raw_close <= 0` → NaN（log 无定义；**不取绝对值、不加 epsilon**）。"""
    raw = pd.DataFrame({"AAA": [0.0, -1.0, 10.0]}, index=["d1", "d2", "d3"])
    size = build_size_exposure(raw, implied_shares(SNAPSHOT_ROWS))
    assert math.isnan(size.at["d1", "AAA"]) and math.isnan(size.at["d2", "AAA"])
    assert not math.isnan(size.at["d3", "AAA"])


def test_u1_no_fill_in_source():
    """U1：源码里**不出现**填充（禁 ffill / bfill / fillna）。"""
    for path in sorted(EXPOSURE_DIR.glob("*.py")):
        code = _code_only(path)
        for token in ("ffill", "bfill", "fillna"):
            assert token not in code, f"{path.name} 出现填充：{token}"


def test_u1_implied_shares_skips_bad_rows():
    """U1：缺 price / market_cap，或非正 → **不产出**（缺就是缺）。"""
    rows = [
        {"symbol": "AAA", "price": 10.0, "market_cap": 1000.0},
        {"symbol": "BBB", "price": None, "market_cap": 1000.0},
        {"symbol": "CCC", "price": 0.0, "market_cap": 1000.0},
        {"symbol": "DDD", "price": 10.0, "market_cap": None},
    ]
    assert set(implied_shares(rows)) == {"AAA"}


# ── U2 行业哑变量必须去一列 ─────────────────────────────────────────────

def test_u2_dummies_drop_exactly_one_column():
    """U2：哑变量**必须去一列** —— 否则与截距完全共线（矩阵奇异）。"""
    exposures, _ = _read()
    dummies = exposures.industry_dummies(DAY)
    groups = {"半导体", "软件", "other"}                # 3 组
    assert dummies.shape[1] == len(groups) - 1, dummies.columns.tolist()
    assert dummies.shape[0] == len(SYMBOLS)


def test_u2_dummies_prefer_dropping_other():
    """U2：优先**去掉 `other`** —— 让截距吸收最不稳定的杂项组。"""
    exposures, _ = _read()
    dummies = exposures.industry_dummies(DAY)
    assert INDUSTRY_OTHER not in dummies.columns
    assert set(dummies.columns) == {"半导体", "软件"}
    # 哑变量矩阵的秩 = 组数 − 1（去一列的直接后果）
    assert dummies.to_numpy().sum() == 6                # 3 + 3，`other` 那只全 0


def test_u2_missing_industry_not_in_dummies():
    """U2/K4：缺行业标签的标的**不在哑变量里**（缺就是缺，不猜一个组）。"""
    exposures, _ = _read(rows=SNAPSHOT_ROWS + [
        {"symbol": "HHH", "industry": None, "price": 10.0, "market_cap": 1000.0}])
    dummies = exposures.industry_dummies(DAY)
    assert "HHH" not in dummies.index


def test_u2_unknown_day_raises():
    """P1：取不存在的交易日的哑变量 → 报错。"""
    exposures, _ = _read()
    try:
        exposures.industry_dummies("1999-01-01")
    except KeyError:
        return
    raise AssertionError("未知交易日应报错（承 P1）")


# ── U3 行业分组阈值必填 + 显形（含 absorbed 必须为 0）────────────────────

def test_u3_threshold_below_floor_raises():
    """U3：阈值低于硬底线（2）→ **报错**（n=1 时哑变量会完全吸收该标的）。"""
    assert MIN_INDUSTRY_COUNT_FLOOR == 2
    try:
        build_industry_exposure({}, [DAY], [], {}, min_industry_count=1)
    except ValueError as e:
        assert "硬底线" in str(e)
        return
    raise AssertionError("阈值 < 2 应报错（承 U3）")


def test_u3_low_count_industry_goes_to_other():
    """U3：**逐日**样本量 < 阈值的行业 → 归 `other`。"""
    industry_of = {s: r["industry"] for s, r in
                   zip(SYMBOLS, SNAPSHOT_ROWS)}
    frame = build_industry_exposure(
        industry_of, [DAY], SYMBOLS, {DAY: SYMBOLS}, min_industry_count=3)
    assert frame.at[DAY, "AAA"] == "半导体"
    assert frame.at[DAY, "GGG"] == INDUSTRY_OTHER, "只有 1 只的行业必须归 other"


def test_u3_absorbed_is_zero_on_healthy_input():
    """U3：正常输入下 `absorbed` **必须为 0**。"""
    exposures, _ = _read()
    assert exposures.coverage["absorbed"] == 0
    assert exposures.coverage["min_kept_group_size"] >= 2


def test_u3_absorbed_guard_actually_catches_bad_input():
    """★ 阳性对照：`coverage_report` 必须能**抓到**"某保留组只有 1 只"。

    逐日阈值下 `absorbed` 结构上恒为 0 —— 但它是防"实现退化成全局阈值"的**守卫**。
    没有这条对照，守卫可能是空的。
    """
    industry = pd.DataFrame({"AAA": ["半导体"], "BBB": ["软件"]}, index=[DAY])
    size = pd.DataFrame(0.0, index=[DAY], columns=["AAA", "BBB"])
    cov = coverage_report(size, industry, min_industry_count=3)
    # 组名排序 → 去掉「半导体」→ 保留「软件」只有 1 只 → 会被完全吸收
    assert cov["absorbed"] == 1, cov


# ── U4 历史暴露的近似必须显形 ───────────────────────────────────────────

def test_u4_approximation_flags_are_per_day():
    """U4：近似标记是**逐日**的 —— 快照日当天是观测值（False），其余天是反推（True）。"""
    exposures, _ = _read(dates=("2026-09-30", SNAPSHOT_DAY))
    assert exposures.size_is_approximated["2026-09-30"] is True
    assert exposures.size_is_approximated[SNAPSHOT_DAY] is False, "快照日是观测值"
    assert exposures.industry_is_approximated["2026-09-30"] is True
    assert exposures.industry_is_approximated[SNAPSHOT_DAY] is False


def test_u4_snapshot_day_is_recorded():
    """U4：用了哪天的快照必须**记录下来**（不许下游猜）。"""
    exposures, _ = _read()
    assert exposures.coverage["snapshot_day"] == SNAPSHOT_DAY


# ── U5 覆盖率必须报告 ───────────────────────────────────────────────────

def test_u5_coverage_reports_all_counts():
    """U5：覆盖率必须报「size 缺失 / industry 缺失 / 归 other」三组计数。"""
    with_extra = SYMBOLS + ("HHH",)          # HHH 在面板里，但快照里缺 price/行业
    exposures, _ = _read(
        symbols=with_extra,
        rows=SNAPSHOT_ROWS + [{"symbol": "HHH", "industry": None,
                               "price": None, "market_cap": None}],
    )
    cov = exposures.coverage
    for key in ("size_missing", "industry_missing", "industry_other",
                "absorbed", "n_days", "n_symbols"):
        assert key in cov, f"覆盖率缺 {key}（承 U5）"
    assert cov["size_missing"] > 0        # HHH 缺 price → 整列 NaN
    assert cov["industry_missing"] > 0    # HHH 缺行业
    assert cov["industry_other"] == 1     # GGG 归 other


# ── ★ 关键：市值反推必须用 raw close ────────────────────────────────────

def test_market_cap_reversal_uses_raw_close():
    """★ size 反推**必须**用 raw close（`panel` 调用**不传** `--adjust`）。

    用 hfq 会让比值多一个**因股而异**的累积复权因子 → size 的横截面排序被拧歪。
    """
    _, calls = _read()
    panel_calls = [c for c in calls if c[0] == "panel"]
    assert len(panel_calls) == 1, panel_calls
    assert "--adjust" not in panel_calls[0], (
        "市值反推必须取 raw（不传 --adjust）—— 传 hfq 会把排序拧歪"
    )


def test_snapshot_is_read_through_panel_reader():
    """M1-1：M2 **不许**自己 spawn —— 源码里不出现 subprocess。"""
    for path in sorted(EXPOSURE_DIR.glob("*.py")):
        assert "subprocess" not in _code_only(path), (
            f"{path.name} 自己 spawn 了子进程 —— 取数必须经 provide_reader（承 M1-1）"
        )


def test_snapshot_contract_shape_is_checked():
    """P2：snapshot 契约不符 → 报错，不猜结构。"""
    from exposure.exposure_builder import snapshot_rows

    for bad in ({}, {"value": {}}, {"value": {"rows": "x"}}):
        try:
            snapshot_rows(bad)
        except ValueError:
            continue
        raise AssertionError(f"坏结构应报错：{bad!r}")


def test_industry_labels_skips_missing():
    """K4：缺 / 空行业的标的**不产出**（缺就是缺）。"""
    rows = [
        {"symbol": "AAA", "industry": "半导体"},
        {"symbol": "BBB", "industry": None},
        {"symbol": "CCC", "industry": "   "},
    ]
    assert industry_labels(rows) == {"AAA": "半导体"}


def test_threshold_default_is_the_recommended_one():
    """PRD U3 定死：推荐阈值 3。"""
    assert MIN_INDUSTRY_COUNT_RECOMMENDED == 3


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
