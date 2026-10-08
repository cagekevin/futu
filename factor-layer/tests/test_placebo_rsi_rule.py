"""11.5「RSI 紧密盘整」规则 —— 对照 `rsi_tight_consolidation`。

## 为什么测试要**逐条件**造样本

只验"能选出一批股票"是**弱的** —— 一个把条件写反、或某条根本没生效的实现，
照样能选出东西。所以这里为**每个条件**造一只"只差这一条"的票：
它必须在其余四条上都通过、**只**挂在被测的那一条上。
⇒ 五个条件各有一条测试盯着它有没有**真的在拦人**。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from evaluate.placebo import selection_registry as reg  # noqa: E402
from evaluate.placebo.implementations.rsi_tight_consolidation import (  # noqa: E402
    ASW_DEFAULTS, SELECTION_NAME, RsiTightConsolidation,
)
from evaluate.placebo.selection_contract import SelectionInput  # noqa: E402

DATES = ["2026-01-01", "2026-01-02", "2026-01-03",
         "2026-01-04", "2026-01-05", "2026-01-06"]
DAY = "2026-01-06"
MA_FACTORS = ("ma_dist_ema10", "ma_dist_ema20", "ma_dist_ema50",
              "ma_dist_sma150", "ma_dist_sma200")

#: 每只票的"人设"：`(RSI 六天, 均线距离, 离底涨幅, ATR%)`；`None` = 用通过值。
_PASS_RSI = [59.0, 59.5, 60.0, 60.5, 61.0, 61.2]
_PROFILES: dict[str, tuple] = {
    "PASS":          (_PASS_RSI, 0.5, 0.5, 0.04),
    "NO_RSI_CALM":   ([60.0, 60.5, 61.0, 65.0, 65.2, 65.5], 0.5, 0.5, 0.04),   # ① 某日跳 4.0
    "NO_RSI_LEVEL":  ([49.0, 49.2, 49.4, 49.6, 49.8, 50.0], 0.5, 0.5, 0.04),   # ⑤ 50.0 不大于 50
    "NO_NEAR_MA":    (_PASS_RSI, 3.0, 0.5, 0.04),                              # ② 离均线 3 个 ATR
    "NO_RISE":       (_PASS_RSI, 0.5, 0.10, 0.04),                             # ③ 离底只涨 10%
    "NO_RANGE":      (_PASS_RSI, 0.5, 0.5, 0.010),                             # ④ 波幅只有 1.0%
    # ① 的**歧义样本**：+2.9/−2.9 交替 ⇒ 每日都 <3（过前半）、净变化 2.9 ≤5
    #    （`net` 放行）、逐日绝对变化之和 14.5 >5（`path` 拦住）。
    # ⚠️ 它**故意不进主夹具** —— 因为它在默认 `net` 读法下**确实该通过**，
    #    放进主夹具会让"默认只选 PASS"这类断言变成谎话。
    "OSCILLATE":     ([60.0, 62.9, 60.0, 62.9, 60.0, 62.9], 0.5, 0.5, 0.04),
}
#: 主夹具：一只全通过 + 五只"各自只差一条"。
UNIVERSE = tuple(s for s in _PROFILES if s != "OSCILLATE")


def _factors(universe: tuple[str, ...] = UNIVERSE) -> dict[str, pd.DataFrame]:
    rsi = pd.DataFrame(
        {s: _PROFILES[s][0] for s in universe}, index=DATES, dtype="float64")
    tables = {"rsi14": rsi}
    for name in MA_FACTORS:
        tables[name] = pd.DataFrame(
            {s: [_PROFILES[s][1]] for s in universe}, index=[DAY], dtype="float64")
    for name in ("off_low150", "off_low260", "atr_pct14", "adr20"):
        col = 2 if name.startswith("off_low") else 3      # adr20 与 atr_pct14 同值即可
        tables[name] = pd.DataFrame(
            {s: [_PROFILES[s][col]] for s in universe}, index=[DAY], dtype="float64")
    return tables


def _data(rule: RsiTightConsolidation,
          universe: tuple[str, ...] = UNIVERSE) -> SelectionInput:
    tables = _factors(universe)
    return SelectionInput(day=DAY, universe=universe,
                          factors={k: v for k, v in tables.items()
                                   if k in rule.requires})


def _selected(rule: RsiTightConsolidation,
              universe: tuple[str, ...] = UNIVERSE) -> set[str]:
    return rule.select(_data(rule, universe))


# ── 逐条件：每个条件都必须**真的在拦人** ────────────────────────────────

def test_default_picks_only_the_fully_passing_name() -> bool:
    got = _selected(RsiTightConsolidation())
    ok = got == {"PASS"}
    print(f"{'[PASS]' if ok else '[FAIL]'} 默认参数只选出 PASS（实际 {sorted(got)}）")
    return ok


def test_each_condition_actually_gates() -> bool:
    """★ 五条条件各有一条票"只差它" —— 逐条断言它确实被拦下。

    若某条条件写错 / 没生效，对应那只票就会被**误选**，这里立刻红。
    """
    got = _selected(RsiTightConsolidation())
    failures = [name for name in ("NO_RSI_CALM", "NO_RSI_LEVEL", "NO_NEAR_MA",
                                  "NO_RISE", "NO_RANGE")
                if name in got]
    ok = not failures
    print(f"{'[PASS]' if ok else '[FAIL]'} 五个条件各自都在拦人"
          f"{'' if ok else ' ← 漏掉了 ' + str(failures)}")
    return ok


def test_funnel_counts_are_cumulative_and_allow_diagnosis() -> bool:
    """漏斗计数必须**单调不增**，且能指到"卡在哪一条"。"""
    counts = RsiTightConsolidation().diagnose(_data(RsiTightConsolidation()))
    seq = [counts["universe"]] + [counts[k] for k in counts if k.startswith("after_")]
    ok = (seq == sorted(seq, reverse=True)
          and counts["after_rsi_calm"] == len(UNIVERSE) - 1      # 只有 NO_RSI_CALM 挂在①
          and counts.get("after_rsi_above", 0) <= len(UNIVERSE))
    print(f"{'[PASS]' if ok else '[FAIL]'} 漏斗计数单调不增：{seq}")
    return ok


# ── ★ "累计变化"的两种读法必须真的不同 ─────────────────────────────────

def test_aggregate_mode_net_vs_path_differ_on_oscillation() -> bool:
    """★ `net` 放行"来回震荡"，`path` 拦住它 —— 两种读法**结论不同**。

    `OSCILLATE` 的 RSI 是 +2.9/−2.9 交替：每日都 <3（过①的前半），
    净变化 2.9 ≤5（**net 放行**），但逐日绝对变化之和 14.5 >5（**path 拦住**）。
    ⇒ 这就是"原文有歧义、必须摊在参数上"的实证。
    """
    # ⚠️ 两个都要**显式**给模式 —— 默认值是 `path`，不能靠"默认 = net"来对比。
    net_rule = RsiTightConsolidation(rsi_aggregate_mode="net", name="probe_net")
    path_rule = RsiTightConsolidation(rsi_aggregate_mode="path", name="probe_path")
    probe = ("PASS", "OSCILLATE")
    net_got = _selected(net_rule, probe)
    path_got = _selected(path_rule, probe)
    ok = "OSCILLATE" in net_got and "OSCILLATE" not in path_got
    print(f"{'[PASS]' if ok else '[FAIL]'} net 放行震荡 / path 拦住"
          f"（net={sorted(net_got)}，path={sorted(path_got)}）")
    return ok


# ── 参数化与显形 ────────────────────────────────────────────────────────

def test_requires_is_derived_from_params() -> bool:
    """★ `requires` **由参数算出** —— 跑 adr 版就不该白要 `atr_pct14`。

    否则依赖清单会说谎（承 H1/H4）。
    """
    atr_rule = RsiTightConsolidation()
    adr_rule = RsiTightConsolidation(range_basis="adr", name="rsi_tc_adr")
    ok = ("atr_pct14" in atr_rule.requires and "adr20" not in atr_rule.requires
          and "adr20" in adr_rule.requires
          and "atr_pct14" not in adr_rule.requires
          and "off_low260" in atr_rule.requires)
    print(f"{'[PASS]' if ok else '[FAIL]'} requires 随参数变化"
          f"（atr 版要 {atr_rule.requires[:2]}…）")
    return ok


def test_window_parameter_changes_requires() -> bool:
    r150 = RsiTightConsolidation(rise_window=150, name="rsi_tc_w150")
    ok = "off_low150" in r150.requires and "off_low260" not in r150.requires
    print(f"{'[PASS]' if ok else '[FAIL]'} rise_window=150 ⇒ 只要 off_low150")
    return ok


def test_unknown_parameter_is_rejected() -> bool:
    """未知参数 → **报错**（不静默忽略，否则报告里的指纹会撒谎）。"""
    try:
        RsiTightConsolidation(totally_unknown=1)
    except ValueError as e:
        ok = "未知参数" in str(e)
        print(f"{'[PASS]' if ok else '[FAIL]'} 未知参数报错")
        return ok
    print("[FAIL] 未知参数报错（竟然接受了）")
    return False


def test_bad_enum_values_are_rejected() -> bool:
    for patch in ({"rsi_aggregate_mode": "median"}, {"range_basis": "close"},
                  {"rsi_change_days": 0}):
        try:
            RsiTightConsolidation(**patch)
        except ValueError:
            continue
        print(f"[FAIL] 非法取值应报错：{patch}")
        return False
    print("[PASS] 非法枚举 / 非法天数 → 报错")
    return True


def test_defaults_match_the_documented_asw_version() -> bool:
    """★ 默认值**就是**预注册的那一套 —— 改动它 = 开新实验。

    这条把"预注册参数"钉在测试里：谁悄悄改了默认值，这里立刻红。

    ⚠️ `rsi_aggregate_mode` 定案是 **`path`**（不是 `net`）—— 理由见规则模块 docstring
    的「①的读法」：只有 `path` 真的测"RSI 没动"，且它是 `net` 的子集 ⇒ 更保守。
    """
    ok = (ASW_DEFAULTS["rsi_change_days"] == 4
          and ASW_DEFAULTS["rsi_daily_max"] == 3.0
          and ASW_DEFAULTS["rsi_aggregate_max"] == 5.0
          and ASW_DEFAULTS["rsi_aggregate_mode"] == "path"
          and ASW_DEFAULTS["ma_atr_max"] == 1.0
          and ASW_DEFAULTS["rise_window"] == 260
          and ASW_DEFAULTS["rise_min"] == 0.30
          and ASW_DEFAULTS["range_basis"] == "atr"
          and ASW_DEFAULTS["range_min"] == 0.025
          and ASW_DEFAULTS["rsi_min"] == 50.0)
    print(f"{'[PASS]' if ok else '[FAIL]'} 默认参数 = 预注册那套（含 ①=path）")
    return ok


def test_params_and_name_are_carried_for_reproducibility() -> bool:
    rule = RsiTightConsolidation()
    ok = (rule.name == SELECTION_NAME
          and rule.params == dict(ASW_DEFAULTS)
          and len(reg.params_fingerprint(rule.params)) == 12)
    print(f"{'[PASS]' if ok else '[FAIL]'} name / params / 指纹齐备")
    return ok


# ── 边界：历史不够 → 空集（不报错、不补造）─────────────────────────────

def test_insufficient_history_yields_empty_set() -> bool:
    """RSI 历史不足 `days+1` 行 ⇒ **空集**（不是报错、更不是编值）。"""
    rule = RsiTightConsolidation()
    short = _factors()
    short["rsi14"] = short["rsi14"].iloc[:2]          # 只有 2 天
    data = SelectionInput(day=DAY, universe=UNIVERSE,
                          factors={k: v for k, v in short.items()
                                   if k in rule.requires})
    got = rule.select(data)
    ok = got == set()
    print(f"{'[PASS]' if ok else '[FAIL]'} RSI 历史不足 ⇒ 空集（{sorted(got)}）")
    return ok


def test_missing_condition_data_does_not_pass() -> bool:
    """★ 某条件查不到值（NaN）⇒ **不通过** —— 不填 True、不填 0（承 P6）。"""
    rule = RsiTightConsolidation()
    tables = _factors()
    tables["off_low260"] = tables["off_low260"].copy()
    tables["off_low260"].loc[DAY, "PASS"] = float("nan")     # PASS 的涨幅缺值
    data = SelectionInput(day=DAY, universe=UNIVERSE,
                          factors={k: v for k, v in tables.items()
                                   if k in rule.requires})
    ok = rule.select(data) == set()
    print(f"{'[PASS]' if ok else '[FAIL]'} 缺值 ⇒ 不算通过（不补造）")
    return ok


# ── ★ 导入即注册（漏了这行，代码看起来完全正常但注册表是空的）──────────

def test_importing_implementations_registers_the_rule() -> bool:
    """★ `import evaluate.placebo.implementations` 必须**已经注册好**默认规则。

    ⚠️ 这条是**真跑一次**才暴露出来的：规则模块当时忘了写 `register_selection(...)`，
       而 `__init__.py` 明明 import 了它 ⇒ 注册表是空的，
       `get_selection()` 报"注册表为空"，**代码却看起来完全正常**。
       原来那条"端到端"测试没抓到 —— 因为**它自己手动注册了**。
    """
    import evaluate.placebo.implementations  # noqa: F401  —— 只做导入，不手动注册
    from evaluate.placebo.selection_registry import available_selections, get_selection

    names = available_selections()
    ok = SELECTION_NAME in names and get_selection(SELECTION_NAME).name == SELECTION_NAME
    print(f"{'[PASS]' if ok else '[FAIL]'} 导入 implementations ⇒ 默认规则已注册"
          f"（{names}）")
    return ok


# ── 走一遍 `run_selection`（注册表 + 物理切片）──────────────────────────

def test_through_run_selection_registry_and_slicing() -> bool:
    """端到端走一次 R1 的**唯一入口** —— 验证注册 + 物理切片 + 产物校验。"""
    name = "rsi_tc_registry_probe"
    reg.register_selection(RsiTightConsolidation(name=name))
    tables = _factors()
    # 故意塞进"未来"的垃圾：切片后不该影响今天的选择（承 H3）
    future = pd.DataFrame({s: [999.0] for s in UNIVERSE},
                          index=["2026-12-31"], dtype="float64")
    for k in MA_FACTORS:
        tables[k] = pd.concat([tables[k], future])

    result = reg.run_selection(name, DAY, tables, UNIVERSE)
    ok = result.picked == frozenset({"PASS"})
    print(f"{'[PASS]' if ok else '[FAIL]'} 经 run_selection 端到端："
          f"选中 {sorted(result.picked)}，指纹 {result.params_fingerprint}")
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
