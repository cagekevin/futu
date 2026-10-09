"""**同一判据只有一份** —— Tugboat 策略层的 SSOT 测试（治 TD-05-19 / TD-05-20）。

## 为什么单立一个文件

`test_tugboat_causality.py` 管的是「**看不看未来**」；
本文件管的是「**同一件事被实现成几份**」—— 两个**不同**的不变式，别混在一起。

| 测试 | 不变式 |
|---|---|
| `test_near_mask_is_single_source` | `near_support`（BASE T4）与 `near_ma`（RSI_TIGHT ②）**必须是同一份判据** |
| `test_form_boundaries_are_params` | 形态分界（0.95 / 0.90）**必须是 `DEFAULTS` 的参数**，且**真的驱动**分类 |

⚠️ 夹具**复用** `test_tugboat_causality._make()`（同一份确定性面板），
不在这里再造第二份"造面板"的代码。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from strategies.tugboat_breakout import (  # noqa: E402
    DEFAULTS, FORM_HIGH, FORM_LOW, TugboatBreakout,
)
from strategies.tugboat_rules import (  # noqa: E402
    BASE, RSI_TIGHT, VCP, rule_params,
)
from tests.test_tugboat_causality import _make  # noqa: E402


def test_near_mask_is_single_source() -> bool:
    """★ `near_support` 与 `near_ma` 必须**逐格相同**（治 TD-05-19）。

    它们本来就是**同一个表达式**（`near <= near_ma_max_atr`）在 `_impl_masks` 里
    **各写一遍** ⇒ 改一处漏一处。现在算一次、两个 key 引用**同一份**。

    判据：把其中一份改坏（只改一份）⇒ 本条必须红。
    """
    panel, factors = _make()
    impl = TugboatBreakout(market_gate=False, bench_gate=False)._impl_masks(panel, factors)
    a = impl["near_support"].to_numpy()
    b = impl["near_ma"].to_numpy()
    # ⚠️ `equal_nan=True`：两边的 warm-up 期都是 NaN，而 `NaN != NaN`
    same = np.array_equal(a, b, equal_nan=True)
    print(f"{'[PASS]' if same else '[FAIL]'} 「贴近均线」判据只有一份"
          f"（BASE 的 T4 与 RSI_TIGHT 的 ② 逐格相同={same}）")
    return same


def test_form_boundaries_are_params() -> bool:
    """★ 形态分界必须是 `DEFAULTS` 的参数，且**真的驱动**分类（治 TD-05-20）。

    原来 `0.95` / `0.90` **硬编码在 `candidates()` 里** ⇒ 不可变更、不可审计、
    **报告指纹不含它**。这里用**同一份面板 + 同一批因子**，只改这两个参数 ⇒
    `form` 列必须跟着变（否则说明它还在硬编码）。

    判据：把参数改回字面量（`form.where(near_high < 0.95, …)`）⇒ 本条必须红。
    """
    panel, factors = _make()
    # ⚠️ `market_gate=False`：本文件测的是**形态分界**，市况门槛是另一件事
    #    （它要求注入市况；夹具里没有 ⇒ 显式关掉，而不是让它去 raise）。
    base = TugboatBreakout(ruleset="rsi_tight",
                           market_gate=False, bench_gate=False).candidates(panel, factors)
    assert not base.empty, "夹具失效：这份面板在 rsi_tight 下没有候选（先修夹具）"
    day, sym = base.iloc[0]["day"], base.iloc[0]["symbol"]

    # 让那个候选的 `near_52w_high` 变成**有限值** ——
    # 否则 NaN 恒落到 LOW（NaN 比较恒 False），参数改了也**测不出差别**。
    nh = factors["near_52w_high"].copy()
    nh.loc[day, sym] = 0.96
    factors["near_52w_high"] = nh

    def forms(**over: float) -> list[str]:
        s = TugboatBreakout(ruleset="rsi_tight", market_gate=False,
                            bench_gate=False, **over)
        return sorted(s.candidates(panel, factors)["form"])

    default_forms = forms()                                    # 0.96 ≥ 0.95 ⇒ 高位
    tightened = forms(form_high_near=0.99, form_low_near=0.98)  # ⇒ 落到低位
    ok = default_forms == [FORM_HIGH] and tightened == [FORM_LOW]
    print(f"{'[PASS]' if ok else '[FAIL]'} 形态分界是参数且真的驱动分类"
          f"（默认 {default_forms} ⇒ 应 [{FORM_HIGH}]；"
          f"改 0.99/0.98 ⇒ {tightened} ⇒ 应 [{FORM_LOW}]）")
    return ok


def test_ma_converge_is_or_not_and() -> bool:
    """★ T2 必须是原文的「**或**」—— 不是「且」（治 TD-05-29）。

    原文 §6.1 行 493：「均线**平行 或** 开始收拢」。
    曾经实现成 `&`（必须同时"够平"**且**"在收拢"）⇒ 比原文更严，
    是 29 笔的主要成因（漏斗里砍掉 36,354 格，最大的一刀）。

    判据：把实现改回 `&` ⇒ 本条**必红**。
    """
    panel, factors = _make()
    s = TugboatBreakout(market_gate=False, bench_gate=False)
    mask = s._impl_masks(panel, factors)["ma_converge"].fillna(False).to_numpy(dtype=bool)

    close = panel.field("close")
    gap = (close.rolling(10).mean() - close.rolling(20).mean()).abs()
    n = int(s.params["tight_days"])
    a = (gap / close <= float(s.params["ma_converge_max"])).to_numpy(dtype=bool)
    b = (gap <= gap.shift(n)).to_numpy(dtype=bool)
    same_as_or = np.array_equal(mask, a | b)
    # 反向：必须**严格多于**「且」的通过数（否则"退化成且"也能过）
    more_than_and = int((a | b).sum()) > int((a & b).sum())
    ok = same_as_or and more_than_and
    print(f"{'[PASS]' if ok else '[FAIL]'} T2 是「或」不是「且」"
          f"（与 A|B 逐格相同={same_as_or}；|B|={int((a | b).sum())} > "
          f"|A&B|={int((a & b).sum())}={more_than_and}）")
    return ok


def test_thresholds_come_from_the_rule_registry() -> bool:
    """★ **阈值只有一处来源**（`Rule.value`，带出处）—— `DEFAULTS` 必须与它逐项一致。

    原来 `DEFAULTS` 与 `Rule.value` **各写一份**（`rs_min` vs `Rule(rs_rank, …)`）
    ⇒ 改一处漏一处（治 TD-05-29 的顺带发现）。

    判据：把 `DEFAULTS` 里的任一派生阈值改回字面量 ⇒ 本条**必红**。
    """
    derived = rule_params(BASE, VCP, RSI_TIGHT)
    bad = {k: (DEFAULTS.get(k), v) for k, v in derived.items() if DEFAULTS.get(k) != v}
    # ★ `rs_min` 的**值**是 2026-10-09 的**用户决定**（原文 0.90 → **0.85**，
    #   理由：参考系是池内而非全市场）⇒ 钉在测试里，防被悄悄改回。
    rs_ok = DEFAULTS["rs_min"] == 0.85
    ghosts = [k for k in ("require_converging", "require_volume_decline") if k in DEFAULTS]
    ok = not bad and rs_ok and not ghosts
    print(f"{'[PASS]' if ok else '[FAIL]'} 阈值只有一处来源（不一致={bad or '无'}；"
          f"rs_min=0.85={rs_ok}；幽灵开关={ghosts or '无'}）")
    return ok


def test_base_takes_the_indicator_filters() -> bool:
    """★ §10 指标层的两条筛选器必须**真的进 `BASE`**（不只是写在 `tugboat_rules` 里）。

    出处：§10.8②「**RSI > 50 = 突破交易的核心确认讯号**」（行 1491，**讲的正是突破交易**，
    原来却只挂在 `RSI_TIGHT` 上）；§10.6 进阶「**ADR% 从 5% 收缩到 2% = 突破前兆**」（行 1461）。

    判据：从 `BASE.rules` 里删掉任一条 ⇒ 本条**必红**（承 TD-05-38）。
    """
    want = {"rsi_above_50", "adr_contracting"}
    declared = {r.key for r in BASE.rules}
    panel, factors = _make()

    def mask(key: str, **kw):
        return (TugboatBreakout(market_gate=False, **kw)._impl_masks(panel, factors)[key]
                .fillna(False).to_numpy(dtype=bool))

    missing_decl = sorted(want - declared)
    missing_impl = sorted(want - set(TugboatBreakout(market_gate=False, bench_gate=False)._impl_masks(panel, factors)))
    # ★ **行为面**：参数必须**真的驱动掩码**。
    #   只断"键存在"是弱的 —— 把条件里的参数换成写死的常量，键照样在、条件却形同虚设。
    #   （不依赖夹具的代表性：只要求"两档参数 ⇒ 两种掩码"，不要求某一档必为全真/全假。）
    rsi_driven = not np.array_equal(mask("rsi_above_50", rsi_min=1e9),
                                    mask("rsi_above_50", rsi_min=0.0))
    adr_driven = not np.array_equal(mask("adr_contracting", adr_contract_days=1000),
                                    mask("adr_contracting", adr_contract_days=1))
    ok = (not missing_decl and not missing_impl and rsi_driven and adr_driven)
    print(f"{'[PASS]' if ok else '[FAIL]'} §10 指标层两条筛选器进了 BASE"
          f"（未登记={missing_decl or '无'}；未实现={missing_impl or '无'}；"
          f"参数驱动掩码 rsi={rsi_driven} adr={adr_driven}）")
    return ok


def test_market_gate_blocks_when_momentum_is_bad() -> bool:
    """★ §7.1 的两个方法必须**真的当门槛**（市况差 ⇒ 不做突破）—— 治 TD-05-30。

    原文：「如果大市具备动能 → VCP 等突破交易的成功率往往比较高」；
    「如果你总是突破失败，**可能并非 VCP 六要点有哪一点没满足**，而是你在不适合的大市状况下做突破」。

    判据：把 `_market_ok` 的判据改成恒真 ⇒ 本条**必红**（"动能差"那一档会开始出候选）。
    """
    panel, factors = _make()
    dates = list(panel.field("close").index)
    # ⚠️ 用 `rsi_tight` 而**不是** `base`：`base` 在本夹具下本来就没有候选
    #    ⇒ `0 == 0` 会把这条测试变成**恒真**（证伪不了）。这条测试要的是"有候选的规则集"。
    # ⚠️ 显式关掉 `bench_gate`：本条测的是**大市动能门槛**，不是「跑赢基准」那条门。
    kw = {"ruleset": "rsi_tight", "bench_gate": False}
    # ① `market_gate=True` 却没注入市况 ⇒ **报错**（承 P1：不许"没市况就当市况很好"）
    raised = False
    try:
        TugboatBreakout(**kw).candidates(panel, factors)   # 默认 market_gate=True
    except ValueError as e:
        raised = "market_gate" in str(e)
    # ② 动能差 ⇒ 一条都不给；动能好 ⇒ 与"关掉门槛"**逐格相同**
    s = TugboatBreakout(**kw)
    s.attach_market_state(pd.DataFrame(
        {"net4": -1.0, "spy_above_20ma": 0.0, "vol_ratio": 1.0,
         "breadth": 0.5, "index_dist_200ma": 0.0, "spy_ret260": 0.0}, index=dates))
    n_bad = len(s.candidates(panel, factors))
    s.attach_market_state(pd.DataFrame(
        {"net4": +1.0, "spy_above_20ma": 1.0, "vol_ratio": 1.0,
         "breadth": 0.5, "index_dist_200ma": 0.0, "spy_ret260": 0.0}, index=dates))
    n_good = len(s.candidates(panel, factors))
    n_off = len(TugboatBreakout(market_gate=False, **kw).candidates(panel, factors))
    ok = raised and n_bad == 0 and n_good > 0 and n_off == n_good
    print(f"{'[PASS]' if ok else '[FAIL]'} 市况门槛真的在拦人"
          f"（未注入报错={raised}；动能差候选={n_bad}（应 0）；"
          f"动能好={n_good}（应 > 0），关掉门槛={n_off}（应相等））")
    return ok


def test_stop_limit_widens_when_volatility_expands() -> bool:
    """★ §6.1「止损**结合 SA**」：**波动扩张** ⇒ 止损上限**放宽**（治 TD-05-39）。

    原文（行 550）：「**市场开始变得波动**、动能开始下降 → **不要设得太窄**」。

    ⚠️ 判据是**波动**（`vol_ratio > 1`）而**不是** §7.1 的动能 ——
    用动能会让放宽档**永远不可达**（门槛已把动能差的日子整段排除），
    这条测试就是那次错误的守卫。

    判据：把 `_stop_limit` 的 `np.where(...)` 改成恒 `base` ⇒ 本条**必红**。
    """
    panel, factors = _make()
    dates = list(panel.field("close").index)
    base = float(DEFAULTS["stop_width_adr"])
    weak = float(DEFAULTS["stop_width_adr_weak"])
    s = TugboatBreakout(ruleset="rsi_tight")
    s.attach_market_state(pd.DataFrame(
        {"net4": 0.0, "spy_above_20ma": 1.0, "vol_ratio": 2.0,
         "breadth": 0.5, "index_dist_200ma": 0.0, "spy_ret260": 0.0}, index=dates))
    got_weak = float(s._stop_limit(panel).to_numpy()[0, 0])
    s.attach_market_state(pd.DataFrame(
        {"net4": 0.0, "spy_above_20ma": 1.0, "vol_ratio": 0.5,
         "breadth": 0.5, "index_dist_200ma": 0.0, "spy_ret260": 0.0}, index=dates))
    got_ok = float(s._stop_limit(panel).to_numpy()[0, 0])
    got_off = float(TugboatBreakout(ruleset="rsi_tight", market_gate=False, bench_gate=False)
                    ._stop_limit(panel).to_numpy()[0, 0])
    ok = (got_weak == weak and got_ok == base and got_off == base
          and weak > base)
    print(f"{'[PASS]' if ok else '[FAIL]'} 止损上限随市况放宽"
          f"（波动扩张={got_weak}（应 {weak}）；波动收敛={got_ok}（应 {base}）；"
          f"关掉门槛={got_off}（应 {base}））")
    return ok


def test_vcp_soft_conditions_are_optional() -> bool:
    """★ VCP 里原文写「**一般 / 最好**」的三条必须标 `optional`（**偏好，不参与排除**）。

    原文 §7.1 的用词是**分档**的：②「他**一般**选 90 以上」（行 704）、
    ③「**最好**不低于 52 周新高的 15%」（行 705）、④「波幅收缩，**最好**三次或以上」（行 706）
    —— 三个"一般/最好"= 偏好；而 ①「在 150 日线之上」、⑤「波动 < 1%」、⑥「缩量」
    **没有修饰词** = 门槛。把它们一律做成硬 AND 是**比原文更严**。

    判据：把任一条的 `optional=True` 去掉 ⇒ 本条**必红**。
    """
    soft = {r.key for r in VCP.rules if r.optional}
    hard = {r.key for r in VCP.rules if not r.optional}
    ok_soft = soft == {"rs_rank", "near_52w_high", "contractions"}
    ok_hard = hard == {"above_150ma", "final_range", "volume_decline", "breakout"}
    # ★ 行为面：默认掩码里**不含**这三条；`include_optional=True` 时**都在**
    panel, factors = _make()
    s = TugboatBreakout(ruleset="vcp", market_gate=False, bench_gate=False)
    labels = {r.key: r.label for r in VCP.rules}
    soft_labels = {labels[k] for k in soft}
    default_labels = {n for n, _ in s._masks(panel, factors)}
    all_labels = {n for n, _ in s._masks(panel, factors, include_optional=True)}
    ok_beh = (not (soft_labels & default_labels)) and soft_labels <= all_labels
    ok = ok_soft and ok_hard and ok_beh
    print(f"{'[PASS]' if ok else '[FAIL]'} VCP 的「一般/最好」三条是 optional"
          f"（软={sorted(soft)}；硬={sorted(hard)}；默认掩码不含软={ok_beh}）")
    return ok


def test_stop_limit_tightens_in_washout_and_euphoria() -> bool:
    """★ §2.2 阶段①④ ⇒ 止损**收紧**（治 TD-05-39 的剩余两条）。

    原文：「阶段① 疑似见底：小注尝试、**止损设窄**、不成功就快速认错」；
    「阶段④ 过度延伸：压低曝险 + 节奏变快（**很窄的止损**、2–3 天部分获利）」。

    判据 = 四阶段的**同一份阈值**（`BREADTH_WASHOUT` / `BREADTH_EUPHORIA` / `INDEX_STRETCH`，
    与 `TugboatExposure` **共用**）⇒ **判据不是我拍的**（只有收紧后的**值**是我定的）。

    判据：把 `washout | euphoria` 那一行删掉 ⇒ 本条**必红**。
    """
    panel, factors = _make()
    dates = list(panel.field("close").index)
    base = float(DEFAULTS["stop_width_adr"])
    tight = float(DEFAULTS["stop_width_adr_tight"])
    s = TugboatBreakout(ruleset="rsi_tight")

    def lim(**over) -> float:
        row = {"net4": 0.0, "spy_above_20ma": 1.0, "vol_ratio": 1.0,
               "breadth": 0.5, "index_dist_200ma": 0.0, "spy_ret260": 0.0}
        row.update(over)
        s.attach_market_state(pd.DataFrame(row, index=dates))
        return float(s._stop_limit(panel).to_numpy()[0, 0])

    got_washout = lim(breadth=0.05)             # ① 疑似见底
    got_euphoria = lim(breadth=0.95)            # ④ 过度延伸（宽度）
    got_stretch = lim(index_dist_200ma=0.40)    # ④ 过度延伸（指数偏离）
    got_normal = lim()                          # 正常
    ok = (got_washout == tight and got_euphoria == tight
          and got_stretch == tight and got_normal == base and tight < base)
    print(f"{'[PASS]' if ok else '[FAIL]'} 阶段①④ ⇒ 止损收紧"
          f"（见底={got_washout}／亢奋={got_euphoria}／偏离={got_stretch}"
          f"（都应 {tight}）；正常={got_normal}（应 {base}））")
    return ok


def test_bench_gate_filters_by_relative_strength() -> bool:
    """★ **跑赢基准才做**（§8.1「RS > 90」的**时序**版本）—— 治 TD-05-30。

    判据 = `ret260(个股) − ret260(SPY) > 0`。出处是**外部 A/B 验证**：
    `vcp-signals` v3 加「Stage-2 趋势模板 + RS vs SPY」后，
    60d 超额 **−2.4pp → +0.6pp**（翻符号）、亏 ≥30% 的交易 **28 → 0**；
    其 `TrendConfig.rs_min_avg = 0.0`（require average RS > 0）⇒ **0 这个数有出处**。

    判据：把 `> 0.0` 改成恒真 ⇒ 本条**必红**。
    """
    panel, factors = _make()
    dates = list(panel.field("close").index)
    syms = list(panel.symbols)
    # ⚠️ **不能直接用 `_make()` 的 factors**：那个合成面板只有几十天，
    #    而 `ret260` 要 260 天 ⇒ 全是 NaN ⇒ 判据恒 False，测不出东西。
    #    ⇒ 手工造一张 `ret260` 宽表（个股 12 个月收益恒 +50%）。
    ret260 = pd.DataFrame(0.50, index=dates, columns=syms)

    def gate(spy_ret: float) -> bool:
        s = TugboatBreakout(ruleset="rsi_tight")
        s.attach_market_state(pd.DataFrame(
            {"net4": 1.0, "spy_above_20ma": 1.0, "vol_ratio": 1.0,
             "breadth": 0.5, "index_dist_200ma": 0.0,
             "spy_ret260": spy_ret}, index=dates))
        return bool(s._rs_vs_spy(panel, {"ret260": ret260}).to_numpy().all())

    # 个股 +50%：基准 +10% ⇒ 跑赢（应放行）；基准 +90% ⇒ 跑输（应拦下）
    ok_pass = gate(0.10) is True
    ok_block = gate(0.90) is False
    ok = ok_pass and ok_block
    print(f"{'[PASS]' if ok else '[FAIL]'} 跑赢基准才做（RS vs SPY）"
          f"（个股 +50% vs 基准 +10% ⇒ 放行={ok_pass}；"
          f"vs 基准 +90% ⇒ 拦下={ok_block}）")
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
