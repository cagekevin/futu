"""`units` / `asof` / `rules` —— 三个地基模块的测试。

## 这一组测试的意义**不在"代码能跑"**，而在：

**把"我犯过的三类错"钉成"写不出来"。**

| 模块 | 钉住哪一类错 | 如果它坏了，会重演什么 |
|---|---|---|
| `units` | 单位混用（美元 ≤ 比例）| 552 个候选只剩 5 个，而我**误读成"日线做不到"** |
| `asof` | 一天前视（开盘前用当日收盘）| 曝险档位全部错位，且**不报错** |
| `rules` | 出处漂移 / 并列规则连乘 | 把"他的原文"当成"我定的"；16 条相乘 = 4e-10 个候选 |
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import asof  # noqa: E402
import rules  # noqa: E402
import units  # noqa: E402


# ── units ────────────────────────────────────────────────────────────────

def test_adr_multiple_is_the_single_conversion() -> bool:
    """★ 手算锚定：股价 100 / 距离 5 美元 / ADR 2.6% ⇒ **1.923 倍 ADR**。

    （这正是 F1 那个 bug 的正确算法：`5 / 100 / 0.026`。
      错写法 `5 ≤ 2.0 × 0.026 = 0.052` 会把它判成"超标"。）
    """
    got = units.adr_multiple(5.0, 100.0, 0.026)
    ok = abs(got - 5.0 / 100.0 / 0.026) < 1e-12 and abs(got - 1.9231) < 1e-3
    print(f"{'[PASS]' if ok else '[FAIL]'} 美元→ADR倍数手算锚定（{got:.4f}）")
    return ok


def test_adr_multiple_rejects_bad_inputs() -> bool:
    """ADR 为 0 / close 为 0 ⇒ **NaN**（不是 inf）—— 与因子层的口径一致。"""
    ok = (np.isnan(units.adr_multiple(5.0, 100.0, 0.0))
          and np.isnan(units.adr_multiple(5.0, 0.0, 0.026))
          and np.isnan(units.adr_multiple(5.0, 100.0, np.nan)))
    print(f"{'[PASS]' if ok else '[FAIL]'} 非法输入 ⇒ NaN（不是 inf）")
    return ok


def test_stop_distance_adr_matches_hand_computed() -> bool:
    """入场 100 / 止损 95 / 股价 100 / ADR 2.6% ⇒ `5/100/0.026` = **1.923 ADR**。

    ⇒ 他的规则（「控制在 **1–1.5 倍 ADR** 之内」）会判它**超标** ✓
    """
    got = units.stop_distance_adr(100.0, 95.0, 100.0, 0.026)
    ok = abs(got - 1.9231) < 1e-3 and got > 1.5
    print(f"{'[PASS]' if ok else '[FAIL]'} 止损距离 = {got:.4f} ADR ⇒ 超他的 1.5 上限 ✓")
    return ok


def test_target_price_from_adr_not_from_r() -> bool:
    """★ 目标价按 **ADR 倍数**算，**不按 R 算** —— 因为我们的 R 和他的 R 不同刻度。"""
    got = units.target_price_from_adr(entry_price=100.0, close=100.0,
                                      adr_ratio=0.026, multiple=3.0)
    ok = abs(got - 107.8) < 1e-9
    print(f"{'[PASS]' if ok else '[FAIL]'} 3 倍 ADR 目标价 = {got:.2f}（应 107.80）")
    return ok


def test_assert_ratio_catches_dollars() -> bool:
    """★ 把**美元**当比例传进来 ⇒ **报错**（这正是 F1 的形状）。"""
    try:
        units.assert_ratio(6.185, name="adr20")       # 美元当比例
    except ValueError as e:
        ok = "不是比例" in str(e)
        print(f"{'[PASS]' if ok else '[FAIL]'} 美元当比例 ⇒ 报错")
        return ok
    print("[FAIL] 美元当比例 ⇒ 竟然通过了")
    return False


# ── asof ─────────────────────────────────────────────────────────────────

def test_atopen_cannot_see_today() -> bool:
    """★ `AtOpen(t)` 物理上取不到第 t 行 —— 这是治"一天前视"的结构保证。"""
    a = asof.AtOpen(cut=5)
    try:
        a.guard(5)
    except asof.LookaheadError:
        ok = a.latest_index() == 4 and a.rows() == slice(0, 5)
        print(f"{'[PASS]' if ok else '[FAIL]'} AtOpen(5) 最多看到第 4 行，取第 5 行被拦")
        return ok
    print("[FAIL] AtOpen(5) 竟然能看到第 5 行")
    return False


def test_atopen_day_zero_has_no_history() -> bool:
    """第 0 天开盘前**什么都看不到** ⇒ 抛错（不是静默返回空）。"""
    try:
        asof.AtOpen(cut=0).latest_index()
    except asof.LookaheadError:
        print("[PASS] 第 0 天取历史 ⇒ 抛错（不静默返回空）")
        return True
    print("[FAIL] 第 0 天取历史 ⇒ 竟然通过了")
    return False


def test_atclose_includes_today_atopen_does_not() -> bool:
    """同一个 `t`：`AtClose` 含第 t 行，`AtOpen` 不含 —— **两者必须差一行**。"""
    wide = pd.DataFrame({"x": range(10)})
    c, o = asof.AtClose(cut=5), asof.AtOpen(cut=5)
    ok = (len(c.frame(wide)) == 6 and len(o.frame(wide)) == 5
          and int(c.frame(wide)["x"].iloc[-1]) == 5
          and int(o.frame(wide)["x"].iloc[-1]) == 4)
    print(f"{'[PASS]' if ok else '[FAIL]'} AtClose 含第 5 行、AtOpen 只到第 4 行")
    return ok


# ── rules ────────────────────────────────────────────────────────────────

def test_original_rule_requires_source_location() -> bool:
    """★ 标「原文」却**不给行号** ⇒ 报错（治 `rise_12m_min` 那次出处漂移）。"""
    try:
        rules.Rule(key="k", label="x", source=rules.Source.ORIGINAL, unit="比例")
    except rules.RuleError:
        print("[PASS] 「原文」没给出处 ⇒ 报错")
        return True
    print("[FAIL] 「原文」没给出处 ⇒ 竟然通过了")
    return False


def test_chosen_rule_requires_a_value() -> bool:
    """标「我定」却**不给值** ⇒ 报错（否则报告里说不清"哪个数是我编的"）。"""
    try:
        rules.Rule(key="k", label="x", source=rules.Source.CHOSEN, unit="比例")
    except rules.RuleError:
        print("[PASS] 「我定」没给值 ⇒ 报错")
        return True
    print("[FAIL] 「我定」没给值 ⇒ 竟然通过了")
    return False


def test_unknown_unit_is_rejected() -> bool:
    """★ 单位必须显式且在允许清单里（治 R1：不许留白）。"""
    try:
        rules.Rule(key="k", label="x", source=rules.Source.INFERRED, unit="块钱")
    except rules.RuleError:
        print("[PASS] 非法单位 ⇒ 报错")
        return True
    print("[FAIL] 非法单位 ⇒ 竟然通过了")
    return False


def test_ruleset_counts_sources() -> bool:
    """`RuleSet.summary()` 要能**一眼看出有多少条是我定的**。"""
    rs = rules.RuleSet(name="d", label="演示", rules=(
        rules.Rule(key="a", label="A", source=rules.Source.ORIGINAL, unit="比例",
                   where="§1", value=1),
        rules.Rule(key="b", label="B", source=rules.Source.CHOSEN, unit="比例",
                   value=2),
    ))
    ok = "原文 1" in rs.summary() and "我定 1" in rs.summary()
    print(f"{'[PASS]' if ok else '[FAIL]'} {rs.summary()}")
    return ok


def test_ruleset_rejects_duplicate_keys() -> bool:
    """同一套里 key 不许重复（否则"逐条审"会对不上）。"""
    try:
        rules.RuleSet(name="d", label="d", rules=(
            rules.Rule(key="a", label="A", source=rules.Source.CHOSEN, unit="比例",
                       value=1),
            rules.Rule(key="a", label="A2", source=rules.Source.CHOSEN, unit="比例",
                       value=2),
        ))
    except rules.RuleError:
        print("[PASS] key 重复 ⇒ 报错")
        return True
    print("[FAIL] key 重复 ⇒ 竟然通过了")
    return False


def test_rendered_table_carries_source_and_unit() -> bool:
    """★ 自动生成的表**必须**带出处与单位 —— 手写表从此作废。"""
    rs = rules.RuleSet(name="d", label="演示", rules=(
        rules.Rule(key="rise", label="涨 50%", source=rules.Source.ORIGINAL,
                   unit="比例", where="§6.1 行 489", value=0.50),))
    t = rules.render_rule_table(rs)
    ok = "原文" in t and "§6.1 行 489" in t and "比例" in t and "0.5" in t
    print(f"{'[PASS]' if ok else '[FAIL]'} 生成的表带出处/单位/阈值")
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
