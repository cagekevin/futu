"""M1 面板 —— 对照 PRD §五 M1 的 5 条约束（M1-1 ~ M1-5）+ 显形字段透传。

跑法：.venv/bin/python tests/test_panel.py

★ 断言的写法（承写码4步法 State 3「禁自证式断言」）：
  · 静态检查（"源码里不出现 X"）**只查代码、不查文档字符串** ——
    否则 docstring 里写"禁 ffill"反而会把检查弄红（用 `ast` 剥掉 docstring）。
  · 每条静态检查都配一条**阳性对照**（证明检查不是空的）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

FACTOR_LAYER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FACTOR_LAYER))

import pandas as pd  # noqa: E402

from panel.panel_builder import read_panel  # noqa: E402
from panel.panel_convert import long_to_panel, panel_from_provide, panel_to_long  # noqa: E402
from panel.panel_types import (  # noqa: E402
    ADJUST_MODES, KLINE_BAR_FIELDS, PANEL_LONG_COLUMNS, CrossSectionPanel,
)
from panel.stock_universe import (  # noqa: E402
    stocks_from_payload, universe_by_day, universe_diagnostics,
)

PANEL_DIR = FACTOR_LAYER / "panel"


# ── 测试夹具：假的取数器（避免依赖真库 / 真子进程）────────────────────────

def _panel_payload() -> dict:
    """两天 × 两标的，且 **MSFT 缺 09-29**（用来验"缺就是缺"）。"""
    return {
        "item": "kline",
        "days": ["2026-09-29", "2026-09-30"],
        "symbols": ["AAPL", "MSFT"],
        "adjust": "hfq",
        "contaminated_days": {"AAPL": ["2026-09-30"]},
        "n_adjust_events": {"AAPL": 62, "MSFT": 94},
        "values": {
            "AAPL": {
                "2026-09-29": {"open": 1.0, "high": 2.0, "low": 0.5,
                               "close": 1.5, "volume": 100.0},
                "2026-09-30": {"open": 1.5, "high": 3.0, "low": 1.0,
                               "close": 2.5, "volume": 200.0},
            },
            "MSFT": {
                "2026-09-30": {"open": 10.0, "high": 20.0, "low": 5.0,
                               "close": 15.0, "volume": 300.0},
            },
        },
    }


def _stocks_payload(stocks=("AAPL", "MSFT"), *, after=False) -> dict:
    return {
        "day": "2026-09-30", "snapshot_day": "2026-09-30",
        "snapshot_is_after_day": after, "stocks": list(stocks),
        "n_excluded_plate": 0, "n_excluded_reserved": 0, "n_excluded_non_stock": 0,
    }


def _runner_factory(*, days=None, stocks=None, panel=None, calls=None):
    """返回一个假 runner —— 它记录被调用的 argv（用来断言参数拼装）。

    `days=None` → 给一个**正常的交易日轴**；要测"轴为空"必须显式传 `days=[]`。
    """
    calls = calls if calls is not None else []

    def runner(*args):
        calls.append(args)
        cmd = args[0]
        if cmd == "days":
            return list(["2026-09-29", "2026-09-30"] if days is None else days)
        if cmd == "stocks":
            return dict(stocks or _stocks_payload())
        if cmd == "panel":
            return dict(panel or _panel_payload())
        raise AssertionError(f"未预期的子命令 {cmd!r}")

    return runner, calls


def _build(**kw):
    runner, calls = _runner_factory(
        days=kw.pop("days", ["2026-09-29", "2026-09-30"]), **kw)
    panel = read_panel(["AAPL", "MSFT"], runner=runner)
    return panel, calls


# ── 静态检查的工具：剥掉 docstring，只留代码（承"禁自证式断言"）──────────

def _code_only(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, doc_holders) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = node.body[1:]
    return ast.unparse(tree)


def _panel_sources() -> dict[str, str]:
    return {p.name: _code_only(p) for p in sorted(PANEL_DIR.glob("*.py"))}


# ── M1-1 取数唯一入口 ────────────────────────────────────────────────────

def test_m1_1_only_provide_reader_spawns_subprocess():
    """M1-1：本层**只有** `provide_reader.py` 会 spawn 子进程。"""
    offenders = [name for name, code in _panel_sources().items()
                 if "subprocess" in code and name != "provide_reader.py"]
    assert not offenders, f"取数入口不唯一：{offenders}"


def test_m1_1_positive_control_subprocess_exists():
    """★ 阳性对照：上一条必须**真的**能抓到 —— `provide_reader.py` 里确实有 subprocess。"""
    assert "subprocess" in _panel_sources()["provide_reader.py"], (
        "provide_reader.py 里没有 subprocess → 上一条检查形同虚设"
    )


def test_m1_1_panel_cli_args_are_explicit():
    """M1-1：参数拼装正确（参数名一变必红）—— 含显式的 `--adjust`。"""
    _, calls = _build()
    panel_calls = [c for c in calls if c[0] == "panel"]
    assert panel_calls == [
        ("panel", "--symbols", "AAPL", "MSFT", "--item", "kline",
         "--days", "2026-09-29", "2026-09-30", "--adjust", "hfq")
    ], panel_calls


def test_m1_1_adjust_none_means_raw_not_default():
    """M3-A3：`adjust=None` = **原样 raw**（不传 `--adjust`），不是"省略就用默认"。"""
    runner, calls = _runner_factory()
    read_panel(["AAPL", "MSFT"], adjust=None, runner=runner)
    panel_call = [c for c in calls if c[0] == "panel"][0]
    assert "--adjust" not in panel_call, panel_call


def test_m1_1_reader_raises_on_failure():
    """F4/P6：子进程非 0 → **报错**，不静默返回空。"""
    from panel import provide_reader as R

    original = R._run_provide_cli
    try:
        R._run_provide_cli = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            R.read_days()
        except RuntimeError:
            return
        raise AssertionError("取数失败应报错（承 F4/P6）")
    finally:
        R._run_provide_cli = original


# ── M1-2 票池由 data-layer 给，本层不做类型判定 ──────────────────────────

def test_m1_2_no_symbol_type_judgement_in_source():
    """M1-2：本层源码里**不出现**标的类型判定（板块前缀 / 快照存在性）。"""
    banned = ("PLATE_CODE_PREFIX", "snapshot_symbols", "LIST200")
    for name, code in _panel_sources().items():
        for token in banned:
            assert token not in code, f"{name} 出现标的类型判定：{token}"


def test_m1_2_universe_is_stock_set_intersection():
    """M1-2：每日票池 = **股票集合** ∩ 该日面板里有值 —— 且**按日单独切**。"""
    panel, _ = _build()
    assert panel.universe_by_day["2026-09-29"] == ("AAPL",)          # MSFT 那天缺
    assert panel.universe_by_day["2026-09-30"] == ("AAPL", "MSFT")


def test_m1_2_universe_excludes_symbols_not_in_stock_set():
    """M1-2：不在股票集合里的标的（板块/ETF）被排除 —— 本层不做判定，只用数据层的集合。"""
    fields = {"close": pd.DataFrame(
        {"AAPL": [1.0], "SPY": [2.0]}, index=["2026-09-30"])}
    got = universe_by_day(["2026-09-30"], ["AAPL", "SPY"], fields, {"AAPL"})
    assert got == {"2026-09-30": ("AAPL",)}


def test_m1_2_empty_stock_set_raises():
    """P1：股票票池为空 → **报错**（不静默给空面板），并提示可操作方案。"""
    runner, _ = _runner_factory(stocks=_stocks_payload(stocks=()))
    try:
        read_panel(["AAPL"], runner=runner)
    except ValueError as e:
        assert "stocks_day" in str(e), "报错要给可操作提示"
        return
    raise AssertionError("空票池应报错（承 P1）")


def test_stocks_payload_shape_is_checked():
    """P2：stocks 契约不符 → 报错，不猜结构。"""
    for bad in ({}, {"stocks": "AAPL"}, {"stocks": None}):
        try:
            stocks_from_payload(bad)
        except ValueError:
            continue
        raise AssertionError(f"坏结构应报错：{bad!r}")


# ── M1-3 转换无损 + 缺就是缺 ─────────────────────────────────────────────

def test_m1_3_long_wide_roundtrip_is_exact():
    """M1-3：长宽往返**逐位相等**（含 NaN 的位置与数量）。"""
    fields = panel_from_provide(_panel_payload())
    close = fields["close"]
    rows = panel_to_long(close)
    back = long_to_panel(rows, dates=list(close.index), symbols=list(close.columns))
    pd.testing.assert_frame_equal(close, back)


def test_m1_3_missing_cells_absent_from_long():
    """缺就是缺：长表里**不出现**缺失格子（不是 value=None / 0）。"""
    fields = panel_from_provide(_panel_payload())
    rows = panel_to_long(fields["close"])
    assert len(rows) == 3, rows                       # 2 + 1，不是 2×2=4
    assert not any(r["symbol"] == "MSFT" and r["trade_date"] == "2026-09-29"
                   for r in rows)
    assert all(r["value"] is not None for r in rows)


def test_m1_3_no_fill_in_source():
    """M1-3：源码里**不出现**填充（禁 ffill / bfill / fillna，承 K4/D3/P6）。"""
    for name, code in _panel_sources().items():
        for token in ("ffill", "bfill", "fillna"):
            assert token not in code, f"{name} 出现填充：{token}"


def test_m1_3_duplicate_key_raises():
    """M1-3：重复 `(trade_date, symbol)` → **报错**（不静默取一个）。"""
    rows = [{"trade_date": "2026-09-30", "symbol": "AAPL", "value": 1.0},
            {"trade_date": "2026-09-30", "symbol": "AAPL", "value": 2.0}]
    try:
        long_to_panel(rows)
    except ValueError as e:
        assert "重复" in str(e)
        return
    raise AssertionError("重复键应报错（承 P2）")


def test_m1_3_bad_payload_shape_raises():
    """P2：panel 契约不符 → 报错，不猜结构。"""
    for bad in ({}, {"values": "x"}, {"values": {"AAPL": {"2026-09-30": 1}}}):
        try:
            panel_from_provide(bad)
        except ValueError:
            continue
        raise AssertionError(f"坏结构应报错：{bad!r}")


def test_m1_3_bar_missing_field_raises():
    """P2：bar 缺字段 → 报错（不静默补 0 / NaN）。"""
    payload = _panel_payload()
    del payload["values"]["AAPL"]["2026-09-30"]["volume"]
    try:
        panel_from_provide(payload)
    except ValueError as e:
        assert "volume" in str(e)
        return
    raise AssertionError("bar 缺字段应报错（承 P2）")


def test_m1_3_none_bar_value_raises():
    """K4/P6：bar 字段是 None → 报错（不许把 None 当合法值喂进面板）。"""
    payload = _panel_payload()
    payload["values"]["AAPL"]["2026-09-30"]["close"] = None
    try:
        panel_from_provide(payload)
    except ValueError:
        return
    raise AssertionError("None 值应报错（承 K4/P6）")


# ── M1-4 取数参数必填无默认 ──────────────────────────────────────────────

def test_m1_4_empty_trading_axis_raises():
    """M1-4：交易日轴为空 → 报错（不静默给空面板）。"""
    runner, _ = _runner_factory(days=[])
    try:
        read_panel(["AAPL"], runner=runner)
    except ValueError as e:
        assert "交易日" in str(e)
        return
    raise AssertionError("空交易日轴应报错（承 P1）")


def test_m1_4_adjust_type_is_checked():
    """M1-4：`adjust` 只接受 str / None —— 别的类型报错。"""
    runner, _ = _runner_factory()
    try:
        read_panel(["AAPL"], adjust=123, runner=runner)
    except ValueError:
        return
    raise AssertionError("非法 adjust 类型应报错")


# ── M1-5 可复现（不实现缓存）────────────────────────────────────────────

def test_m1_5_two_runs_are_identical():
    """M1-5：同一份输入 → 两次运行**逐位相同**（本层不实现缓存，故无缓存差异）。"""
    first, _ = _build()
    second, _ = _build()
    assert first.dates == second.dates
    assert first.symbols == second.symbols
    assert first.universe_by_day == second.universe_by_day
    assert first.n_adjust_events == second.n_adjust_events
    assert first.contaminated_days == second.contaminated_days
    assert first.snapshot_is_after_day == second.snapshot_is_after_day
    for name in first.fields:
        pd.testing.assert_frame_equal(first.fields[name], second.fields[name])


# ── 显形字段透传（P5 / State 2.5）────────────────────────────────────────

def test_visible_fields_are_passed_through():
    """P5：四个显形字段**必须透传**，不许在 M1 吞掉。"""
    panel, _ = _build(stocks=_stocks_payload(after=True))
    assert panel.n_adjust_events == {"AAPL": 62, "MSFT": 94}
    assert panel.contaminated_days == {"AAPL": ("2026-09-30",)}
    assert panel.snapshot_day == "2026-09-30"
    assert panel.snapshot_is_after_day is True
    # 口径也透传 —— `FactorSpec.adjust` 要靠它校验（承 A3）
    assert panel.adjust == "hfq"


def test_adjust_none_is_visible_on_panel():
    """P3：`adjust=None`（原样 raw）必须在面板上**看得出来**，不许丢。"""
    runner, _ = _runner_factory(panel={**_panel_payload(), "adjust": None})
    panel = read_panel(["AAPL", "MSFT"], runner=runner)
    assert panel.adjust is None


def test_echoed_symbols_must_match_request():
    """P2：数据层回显的标的与请求不一致 → 报错（不静默用其中一边）。"""
    runner, _ = _runner_factory()          # 夹具回显 AAPL + MSFT
    try:
        read_panel(["AAPL"], runner=runner)   # 只请求一个
    except ValueError as e:
        assert "回显" in str(e)
        return
    raise AssertionError("回显不一致应报错（承 P2）")


def test_zero_adjust_events_is_visible_not_hidden():
    """P5：`n_adjust_events == 0`（复权是空操作）必须**看得见**，不许被当成"没有"。"""
    payload = _panel_payload()
    payload["n_adjust_events"] = {"AAPL": 0, "MSFT": 0}
    runner, _ = _runner_factory(panel=payload)
    panel = read_panel(["AAPL", "MSFT"], runner=runner)
    assert panel.n_adjust_events == {"AAPL": 0, "MSFT": 0}


def test_field_missing_raises():
    """P1：取不存在的字段 → 报错（不静默给空）。"""
    panel, _ = _build()
    try:
        panel.field("vwap")
    except KeyError as e:
        assert "vwap" in str(e)
        return
    raise AssertionError("缺字段应报错（承 P1）")


# ── 跨层契约测试（PRD §八 #21）──────────────────────────────────────────

def _load_by_path(name: str, path: Path):
    """按文件路径加载模块 —— 避开 `sys.path` 污染与标准库同名（如 `statistics`）。

    ⚠️ **必须先注册进 `sys.modules`**：被加载的模块里有
       `@dataclass` + `from __future__ import annotations`，
       而 `dataclasses._is_type` 会去 `sys.modules[cls.__module__]` 找模块 ——
       没注册就拿不到 `__dict__`（报 `'NoneType' object has no attribute ...`）。
    """
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_kline_bar_fields_match_backtest():
    """★ 跨层契约：本层 `KLINE_BAR_FIELDS` 与 `backtest/data_source.py` **同值**。

    两份定义是**并列设计**的已知成本（本层与 backtest 互不 import）；
    一致性靠这条测试锁死 —— 不靠人记。
    """
    sys.path.insert(0, str(FACTOR_LAYER.parent / "backtest"))
    import data_source  # noqa: PLC0415

    assert tuple(data_source.KLINE_BAR_FIELDS) == tuple(KLINE_BAR_FIELDS)


def _panel_with_universe(universe_by_day: dict, *, symbols=("A", "B", "C")):
    """造一个只关心 `universe_by_day` 的面板（诊断只看票池，不看值）。"""
    dates = list(universe_by_day)
    return CrossSectionPanel(
        dates=tuple(dates), symbols=tuple(symbols),
        fields={"close": pd.DataFrame(1.0, index=dates, columns=list(symbols))},
        universe_by_day=universe_by_day, n_adjust_events={},
        contaminated_days={}, adjust="hfq", snapshot_day="2026-10-06",
        snapshot_is_after_day=True)


def test_universe_diagnostics_flags_survivor_set():
    """★ 审计 A4：票池**只增不减**（没有任何标的退出）→ 必须显形为幸存者集合。

    实测 `data-layer` 的库就是这样：325 只标的、**0 只提前结束**。
    """
    panel = _panel_with_universe({
        "d1": ("A",), "d2": ("A", "B"), "d3": ("A", "B", "C"),
    })
    got = universe_diagnostics(panel)
    assert got["no_symbol_ever_left"] is True
    assert got["n_ended_early"] == 0
    assert got["n_symbols_ever"] == 3 and got["n_symbols_first_day"] == 1
    assert "幸存者集合" in got["caveat"]
    assert got["last_seen_position_median"] == 1.0


def test_universe_diagnostics_detects_retirement():
    """★ 阳性对照：**有标的退出** → 必须判为"不是纯幸存者集合"。"""
    panel = _panel_with_universe({
        "d1": ("A", "B", "C"), "d2": ("A", "C"), "d3": ("A",),
    })
    got = universe_diagnostics(panel)
    assert got["no_symbol_ever_left"] is False
    assert got["n_ended_early"] == 2
    assert set(got["ended_early_sample"]) == {"B", "C"}
    # 非幸存者集合时**不该**出现"IC 被高估"这个论断（那是幸存者集合才有的含义）
    assert "高估" not in got["caveat"], got["caveat"]


def test_universe_diagnostics_empty_panel():
    """空面板 → 不崩，给空诊断。"""
    panel = _panel_with_universe({})
    got = universe_diagnostics(panel)
    assert got["n_days"] == 0 and got["no_symbol_ever_left"] is None


def test_adjust_modes_match_data_layer():
    """★ 跨层契约：本层 `ADJUST_MODES` 与数据层 `engine/adjust.py::MODES` **同值**。

    ⚠️ **审计补的测试（2026-10-07）**：`panel_types.py` 的注释写着
       「一致性由**跨层契约测试**锁死」，但那条测试**当时并不存在** ——
       等于一句**虚假保证**。现补上。
    """
    module = _load_by_path(
        "data_layer_engine_adjust",
        FACTOR_LAYER.parent / "data-layer" / "engine" / "adjust.py")
    assert tuple(module.MODES) == tuple(ADJUST_MODES), (
        f"数据层 MODES={tuple(module.MODES)}，本层 ADJUST_MODES={tuple(ADJUST_MODES)}"
    )


def test_long_columns_are_the_prd_contract():
    """PRD §6.3：长表列名写死为 `(trade_date, symbol, value)`。"""
    assert tuple(PANEL_LONG_COLUMNS) == ("trade_date", "symbol", "value")


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
