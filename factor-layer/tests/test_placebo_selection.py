"""R1 选择集 —— 对照 PRD §五 R1 的约束 H1–H4。

不依赖网络、不依赖真实数据：全部用**合成面板**（与 `test_evaluate.py` 同风格）。
跑法：`.venv/bin/python tests/test_placebo_selection.py`
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from evaluate.placebo import selection_registry as reg  # noqa: E402
from evaluate.placebo.selection_contract import SelectionInput  # noqa: E402

_COUNTER = itertools.count()


def _name(prefix: str) -> str:
    """注册表是**进程级全局**的 ⇒ 每个用例用唯一名，避免互相污染。"""
    return f"{prefix}_{next(_COUNTER)}"


def _panel(dates: list[str], symbols: tuple[str, ...], rows: list[list]) -> pd.DataFrame:
    return pd.DataFrame(rows, index=dates, columns=list(symbols))


DATES = ["2026-01-01", "2026-01-02", "2026-01-03"]
SYMS = ("A", "B", "C", "D")


def _factor_table() -> pd.DataFrame:
    return _panel(DATES, SYMS, [
        [1.0, 2.0, 3.0, 4.0],
        [4.0, 3.0, 2.0, 1.0],
        [9.0, 9.0, 9.0, 9.0],      # ← 未来（day 之后），绝不该被看到
    ])


class _ThresholdRule:
    """最小可用规则：取某因子**当日**超过阈值的标的。"""

    def __init__(self, name: str, *, requires: tuple[str, ...] = ("f",),
                 threshold: float = 2.0):
        self.name = name
        self.requires = requires
        self.params = {"threshold": threshold}

    def select(self, data: SelectionInput) -> set[str]:
        row = data.today("f")
        return set(row[row > self.params["threshold"]].index)


class _SpyRule:
    """间谍规则：记录它**实际收到**的因子表索引（用来直接取证切片）。"""

    def __init__(self, name: str):
        self.name = name
        self.requires = ("f",)
        self.params = {}
        self.seen_index: tuple[str, ...] | None = None

    def select(self, data: SelectionInput) -> set[str]:
        self.seen_index = tuple(data.factor("f").index)
        return set()


# ── H1：接口唯一 ────────────────────────────────────────────────────────

def test_h1_missing_name_rejected() -> bool:
    class NoName:
        requires = ()
        params = {}

        def select(self, data):
            return set()

    try:
        reg.register_selection(NoName())
    except ValueError:
        print("[PASS] test_h1_missing_name_rejected（空名报错）")
        return True
    print("[FAIL] test_h1_missing_name_rejected")
    return False


def test_h1_missing_requires_rejected() -> bool:
    class NoRequires:
        name = _name("h1_requires")
        params = {}

        def select(self, data):
            return set()

    try:
        reg.register_selection(NoRequires())
    except TypeError:
        print("[PASS] test_h1_missing_requires_rejected（缺 requires 报错）")
        return True
    print("[FAIL] test_h1_missing_requires_rejected")
    return False


def test_h1_select_must_be_callable() -> bool:
    class NoSelect:
        name = _name("h1_select")
        requires = ()
        params = {}
        select = "不是方法"

    try:
        reg.register_selection(NoSelect())
    except TypeError:
        print("[PASS] test_h1_select_must_be_callable（select 不可调用 → 报错）")
        return True
    print("[FAIL] test_h1_select_must_be_callable")
    return False


def test_h1_params_required_for_reproducibility() -> bool:
    """H1/H3：没有 `params` 就无法回答「当时用的是哪个阈值」⇒ 必须报错。"""
    class NoParams:
        name = _name("h1_params")
        requires = ()

        def select(self, data):
            return set()

    try:
        reg.register_selection(NoParams())
    except TypeError:
        print("[PASS] test_h1_params_required_for_reproducibility")
        return True
    print("[FAIL] test_h1_params_required_for_reproducibility")
    return False


# ── H2：注册表制 ────────────────────────────────────────────────────────

def test_h2_duplicate_name_rejected() -> bool:
    """重名 → 报错，不静默覆盖（承 P2）。"""
    nm = _name("h2_dup")
    reg.register_selection(_ThresholdRule(nm))
    try:
        reg.register_selection(_ThresholdRule(nm))
    except ValueError:
        print("[PASS] test_h2_duplicate_name_rejected（重名报错）")
        return True
    print("[FAIL] test_h2_duplicate_name_rejected")
    return False


def test_h2_available_is_sorted_and_contains_registered() -> bool:
    nm = _name("h2_avail")
    reg.register_selection(_ThresholdRule(nm))
    got = reg.available_selections()
    ok = nm in got and got == sorted(got)
    print(f"{'[PASS]' if ok else '[FAIL]'} test_h2_available_is_sorted_and_contains_registered")
    return ok


def test_h2_unknown_name_raises() -> bool:
    """未知名 → 报错并列出可用名（承 P1：不静默兜底）。"""
    try:
        reg.get_selection("__不存在的规则__")
    except KeyError as e:
        ok = "可用" in str(e)
        print(f"{'[PASS]' if ok else '[FAIL]'} test_h2_unknown_name_raises（含可用列表）")
        return ok
    print("[FAIL] test_h2_unknown_name_raises")
    return False


# ── H3：物理切片 + 依赖显式 ─────────────────────────────────────────────

def test_h3_physical_slice_does_not_contain_future() -> bool:
    """★ 直接取证：规则**实际收到**的因子表里没有 `day` 之后的行。"""
    spy = _SpyRule(_name("h3_spy"))
    reg.register_selection(spy)
    reg.run_selection(spy.name, "2026-01-02", {"f": _factor_table()}, SYMS)

    expected = ("2026-01-01", "2026-01-02")
    ok = spy.seen_index == expected
    print(f"{'[PASS]' if ok else '[FAIL]'} test_h3_physical_slice_does_not_contain_future"
          f"（收到 {spy.seen_index}，期望 {expected}）")
    return ok


def test_h3_future_pollution_does_not_change_result() -> bool:
    """★ 黑盒版：把 `day` 之后换成垃圾 → 选中集逐位不变（承 backtest V9）。"""
    nm = _name("h3_pollution")
    reg.register_selection(_ThresholdRule(nm))
    universe = SYMS
    day = "2026-01-02"

    clean = reg.run_selection(nm, day, {"f": _factor_table()}, universe)

    polluted_table = _factor_table()
    polluted_table.loc["2026-01-03"] = [-999.0, 999.0, 888.0, -888.0]
    polluted = reg.run_selection(nm, day, {"f": polluted_table}, universe)

    ok = clean.picked == polluted.picked
    print(f"{'[PASS]' if ok else '[FAIL]'} test_h3_future_pollution_does_not_change_result"
          f"（{sorted(clean.picked)} vs {sorted(polluted.picked)}）")
    return ok


def test_h3_undeclared_factor_rejected() -> bool:
    """没在 `requires` 里声明的因子 → 报错（承 H4：依赖清单不许是谎言）。"""
    nm = _name("h3_undeclared")
    reg.register_selection(_ThresholdRule(nm))

    class _Sneaky(_ThresholdRule):
        def select(self, data):
            data.factor("偷偷用的因子")      # 未声明
            return set()

    sneaky = _Sneaky(_name("h3_sneaky"))
    reg.register_selection(sneaky)
    # 把"偷偷用的因子"塞进 factors —— 但规则没声明，仍须报错
    try:
        reg.run_selection(sneaky.name, "2026-01-02",
                          {"f": _factor_table(), "偷偷用的因子": _factor_table()}, SYMS)
    except KeyError as e:
        ok = "没声明" in str(e)
        print(f"{'[PASS]' if ok else '[FAIL]'} test_h3_undeclared_factor_rejected")
        return ok
    print("[FAIL] test_h3_undeclared_factor_rejected（未声明却读到了）")
    return False


# ── 依赖齐备 / 产物校验 ─────────────────────────────────────────────────

def test_missing_required_factor_rejected() -> bool:
    """`requires` 有、送进来的没有 → 报错，**不静默用 0**（承 H1）。"""
    nm = _name("missing_req")
    reg.register_selection(_ThresholdRule(nm, requires=("f", "g")))
    try:
        reg.run_selection(nm, "2026-01-02", {"f": _factor_table()}, SYMS)
    except KeyError as e:
        ok = "需要因子" in str(e)
        print(f"{'[PASS]' if ok else '[FAIL]'} test_missing_required_factor_rejected")
        return ok
    print("[FAIL] test_missing_required_factor_rejected")
    return False


def test_product_must_be_set() -> bool:
    nm = _name("prod_type")

    class _BadProduct(_ThresholdRule):
        def select(self, data):
            return ["A", "B"]        # list 而非 set

    reg.register_selection(_BadProduct(nm))
    try:
        reg.run_selection(nm, "2026-01-02", {"f": _factor_table()}, SYMS)
    except TypeError as e:
        ok = "必须返回 set" in str(e)
        print(f"{'[PASS]' if ok else '[FAIL]'} test_product_must_be_set")
        return ok
    print("[FAIL] test_product_must_be_set")
    return False


def test_product_outside_universe_rejected() -> bool:
    """选中票池外的标的 → 报错（承 C2：选择只在当日票池内）。"""
    nm = _name("prod_outside")

    class _Outsider(_ThresholdRule):
        def select(self, data):
            return {"A", "ZZZ"}      # ZZZ 不在票池

    reg.register_selection(_Outsider(nm))
    try:
        reg.run_selection(nm, "2026-01-02", {"f": _factor_table()}, SYMS)
    except ValueError as e:
        ok = "票池外" in str(e)
        print(f"{'[PASS]' if ok else '[FAIL]'} test_product_outside_universe_rejected")
        return ok
    print("[FAIL] test_product_outside_universe_rejected")
    return False


# ── H4：派生边界（代码级，剥 docstring）──────────────────────────────────

def test_h4_no_raw_indicator_computation_in_framework() -> bool:
    """H4：`placebo/` 的**框架文件**里不许重算原始指标（RSI/EMA/ATR…）。

    ⚠️ **为什么是 `ast` 级而不是 grep**（承 PRD 01 §8.1 的实测教训）：
       朴素 grep 会**误报** —— 它命中的是 docstring 里**写的禁令**
       （本文件第一次验证时就是这样：`"承 H4：RSI 本体归 M3"` 被当成指标计算）。
       故剥掉所有字符串常量，只扫真正的**代码节点**。

    ⚠️ **范围只含框架文件**：`implementations/` 下是**规则实现**，
       它按设计要消费因子值（可以出现 `threshold` 之类的比较），
       但仍不得重算原始指标 —— 这条留给"该规则的测试"守。
    """
    import ast

    banned = ("rsi", "ema", "sma", "atr", "adr", "rolling", "ewm", "pct_change")
    offenders: list[str] = []
    base = Path(__file__).resolve().parent.parent / "evaluate" / "placebo"

    for path in sorted(base.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                continue                      # ★ 剥掉字符串（docstring / 注释文本）
            if isinstance(node, ast.Name) and node.id.lower() in banned:
                offenders.append(f"{path.name}:{node.lineno} Name({node.id})")
            elif isinstance(node, ast.Attribute) and node.attr.lower() in banned:
                offenders.append(f"{path.name}:{node.lineno} .{node.attr}")

    ok = not offenders
    detail = "" if ok else f"← {offenders}"
    print(f"{'[PASS]' if ok else '[FAIL]'} "
          f"test_h4_no_raw_indicator_computation_in_framework {detail}")
    return ok


def test_h4_positive_control() -> bool:
    """★ 阳性对照：上一条必须**真的能抓到** —— 不能是空转的检查。

    构造一段含 `rsi` 的代码 → 断言同一个检查逻辑会报出来（承 PRD 01 §8.1）。
    """
    import ast

    banned = ("rsi", "ema", "sma", "atr", "adr", "rolling", "ewm", "pct_change")
    snippet = "x = rsi(close, 14)\n"
    tree = ast.parse(snippet)
    hits = [
        n.id for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id.lower() in banned
    ]
    ok = hits == ["rsi"]
    print(f"{'[PASS]' if ok else '[FAIL]'} test_h4_positive_control（抓到 {hits}）")
    return ok


# ── 可复现：参数指纹 ────────────────────────────────────────────────────

def test_params_fingerprint_stable_and_sensitive() -> bool:
    """同内容 → 同指纹；改任一阈值 → 指纹变（承 H3 / P3）。"""
    a = reg.params_fingerprint({"threshold": 2.0, "bins": 5})
    b = reg.params_fingerprint({"bins": 5, "threshold": 2.0})    # 键序不同
    c = reg.params_fingerprint({"threshold": 2.1, "bins": 5})    # 值不同
    ok = (a == b) and (a != c) and len(a) == 12
    print(f"{'[PASS]' if ok else '[FAIL]'} test_params_fingerprint_stable_and_sensitive")
    return ok


def test_result_carries_fingerprint_and_universe_size() -> bool:
    """产物带指纹与票池规模 —— R3 的 Z4（样本量显形）要用。"""
    nm = _name("result_fields")
    reg.register_selection(_ThresholdRule(nm, threshold=2.0))
    r = reg.run_selection(nm, "2026-01-02", {"f": _factor_table()}, SYMS)
    ok = (r.name == nm and r.day == "2026-01-02" and r.n_universe == len(SYMS)
          and len(r.params_fingerprint) == 12 and r.picked == {"A", "B"})
    print(f"{'[PASS]' if ok else '[FAIL]'} test_result_carries_fingerprint_and_universe_size"
          f"（picked={sorted(r.picked)}）")
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
            print(f"FAIL {fn.__name__}: 抛异常")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
