"""`TugboatExposure`（他的 §2.2「曝险四阶段」）—— 测试。

## 为什么要单独测它

他说这一块「**占 7–8 成重要性**」，而它又是**唯一有反馈环**的部件
（结果 → 状态 → 曝险 → 结果）。规格 §11.1 的 A6' 因此要求：
**规则写死、档位可见**。

这里逐条锁死四个档位的**边界行为**与那条**降档**规则 ——
它们一旦被事后调参，这个测试立刻红。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from strategies.tugboat_breakout import (  # noqa: E402
    STAGE_DOWNSHIFT, STAGE_EUPHORIA, STAGE_FULL, STAGE_RECOVER, STAGE_WASHOUT,
    TugboatExposure,
)
from trade_simulator import (  # noqa: E402
    AccountState, ExitPolicy, ExposureSettings,
)

DEFAULT = ExposureSettings(max_positions=5, risk_fraction=0.01,
                           exit_policy=ExitPolicy(), stage="static")


def _state(day_index=0, *, day="d1", recent_r=(), recent_reasons=()) -> AccountState:
    # ⚠️ 必须给 `day` —— 曝险策略**按日期字符串**查市场状态（不再按行序）。
    return AccountState(day_index=day_index, day=day, equity=1_000_000.0,
                        initial_equity=1_000_000.0, open_positions=0,
                        n_closed=len(recent_r), total_r=float(sum(recent_r)),
                        recent_r=tuple(recent_r),
                        recent_reasons=tuple(recent_reasons))


def _market(breadths, stretches=None) -> pd.DataFrame:
    n = len(breadths)
    return pd.DataFrame({
        "breadth": breadths,
        "index_dist_200ma": stretches or [0.0] * n,
    }, index=[f"d{i + 1}" for i in range(n)])


def _exposure(breadth, stretch=0.0, **kw) -> TugboatExposure:
    e = TugboatExposure(**kw)
    e.attach_market_state(_market([breadth], [stretch]))
    return e


# ── 四个档位 ─────────────────────────────────────────────────────────────

def test_washout_uses_single_slot() -> bool:
    """① 疑似见底（宽度 < 20%）⇒ **小注尝试**：只开 1 个持仓位。"""
    got = _exposure(0.10).settings(_state(), DEFAULT)
    ok = got.stage == STAGE_WASHOUT and got.max_positions == 1
    print(f"{'[PASS]' if ok else '[FAIL]'} ①疑似见底 ⇒ max_positions=1（{got.stage} / "
          f"{got.max_positions}）")
    return ok


def test_euphoria_cuts_slots_and_speeds_up() -> bool:
    """④ 过度延伸（宽度 > 80%）⇒ **压低曝险 + 节奏变快**（2 天部分获利）。"""
    got = _exposure(0.90).settings(_state(), DEFAULT)
    ok = (got.stage == STAGE_EUPHORIA and got.max_positions == 2
          and got.exit_policy.partial_after_days == 2)
    print(f"{'[PASS]' if ok else '[FAIL]'} ④过度延伸 ⇒ 减仓 + {got.exit_policy.partial_after_days} "
          f"天部分获利（{got.stage} / {got.max_positions}）")
    return ok


def test_index_stretch_also_triggers_euphoria() -> bool:
    """④ 的另一条触发：**指数高于 200 日线 25% 以上**（不只看宽度）。"""
    got = _exposure(0.50, stretch=0.30).settings(_state(), DEFAULT)
    ok = got.stage == STAGE_EUPHORIA and got.max_positions == 2
    print(f"{'[PASS]' if ok else '[FAIL]'} 指数过度延伸也触发④（{got.stage}）")
    return ok


def test_neutral_is_recovering() -> bool:
    """② 中性市况 ⇒ 中间档（不是满仓）—— 「大众还在怀疑」时不该满上。"""
    got = _exposure(0.50).settings(_state(), DEFAULT)
    ok = got.stage == STAGE_RECOVER and got.max_positions == 3
    print(f"{'[PASS]' if ok else '[FAIL]'} ②动能恢复 ⇒ 中间档（{got.stage} / "
          f"{got.max_positions}）")
    return ok


def test_hot_streak_promotes_to_full() -> bool:
    """③ 「你的交易开始顺手」⇒ 满档。依据是**近期成交**（不是手感）。"""
    hot = _state(recent_r=(2.0, -1.0, 3.0, 1.5, -1.0, 2.0),
                 recent_reasons=("target_final", "stop", "target_final",
                                 "ma_break", "stop", "target_final"))
    got = _exposure(0.50).settings(hot, DEFAULT)
    ok = got.stage == STAGE_FULL and got.max_positions == DEFAULT.max_positions
    print(f"{'[PASS]' if ok else '[FAIL]'} ③近期顺手 ⇒ 满档（{got.stage} / "
          f"{got.max_positions}）")
    return ok


# ── 降档（他："止损密集被打中 ⇒ 果断降曝险，不犹豫"）─────────────────────

def test_stall_downshifts_one_notch() -> bool:
    """近期止损占比 ≥ 60% ⇒ **降一档**，且档位名带标记（报告要看得见）。"""
    stalled = _state(recent_r=(-1.0,) * 10,
                     recent_reasons=("stop",) * 8 + ("ma_break",) * 2)
    got = _exposure(0.50).settings(stalled, DEFAULT)
    ok = (STAGE_DOWNSHIFT in got.stage and got.max_positions == 2)
    print(f"{'[PASS]' if ok else '[FAIL]'} 止损密集 ⇒ 降一档（{got.stage} / "
          f"{got.max_positions}，应含 {STAGE_DOWNSHIFT} 且 =2）")
    return ok


def test_downshift_never_goes_below_one() -> bool:
    """降档**不越界**：从①（已经是 1）再降仍是 1，不会变成 0 或负数。"""
    stalled = _state(recent_r=(-1.0,) * 10,
                     recent_reasons=("stop",) * 10)
    got = _exposure(0.05).settings(stalled, DEFAULT)
    ok = got.max_positions >= 1
    print(f"{'[PASS]' if ok else '[FAIL]'} 降档不越界（{got.stage} / "
          f"{got.max_positions}，应 ≥1）")
    return ok


def test_no_downshift_when_sample_is_thin() -> bool:
    """样本不足时**不降档** —— 连续亏 2 笔不等于"策略集体失效"（不猜）。"""
    thin = _state(recent_r=(-1.0, -1.0), recent_reasons=("stop", "stop"))
    got = _exposure(0.50).settings(thin, DEFAULT)
    ok = STAGE_DOWNSHIFT not in got.stage
    print(f"{'[PASS]' if ok else '[FAIL]'} 样本不足不降档（{got.stage}）")
    return ok


# ── 边界与契约 ───────────────────────────────────────────────────────────

def test_market_state_is_looked_up_by_date_not_by_row_order() -> bool:
    """★ **把市场状态倒序传进去，档位仍必须按日期取对。**

    ⚠️ 曾经写成 `market_state.index[state.day_index]` —— 那隐含
       "市场状态的行序 = 模拟的日序"这个**没有任何东西保证**的前提。
       一旦调用方传进来的顺序不同，档位会**安静地错位**（不报错，只是全错）。

    这里：`d1` 是极度亢奋（宽度 0.95）、`d2` 是疑似见底（宽度 0.05）。
    **倒序**放进 DataFrame，再分别在 `d1` / `d2` 上问 —— 答案必须仍然对得上日期。
    """
    m = pd.DataFrame({"breadth": [0.05, 0.95], "index_dist_200ma": [0.0, 0.0]},
                     index=["d2", "d1"])            # ★ 故意倒序
    e = TugboatExposure()
    e.attach_market_state(m)
    a = e.settings(_state(day="d1"), DEFAULT)
    b = e.settings(_state(day="d2"), DEFAULT)
    ok = a.stage == STAGE_EUPHORIA and b.stage == STAGE_WASHOUT
    print(f"{'[PASS]' if ok else '[FAIL]'} 市场状态**按日期**取（倒序传入也不串）"
          f"（d1→{a.stage}，d2→{b.stage}）")
    return ok


def test_missing_market_state_falls_back_to_neutral() -> bool:
    """★ 没注入市场状态 ⇒ 取**中性**（不假装见底、也不假装亢奋）。"""
    got = TugboatExposure().settings(_state(), DEFAULT)
    ok = got.stage == STAGE_RECOVER
    print(f"{'[PASS]' if ok else '[FAIL]'} 无市场状态 ⇒ 中性档（{got.stage}）")
    return ok


def test_attach_market_state_requires_columns() -> bool:
    """缺列 ⇒ **报错**（承 P1：不静默兜底）。"""
    try:
        TugboatExposure().attach_market_state(pd.DataFrame({"breadth": [0.5]}))
    except ValueError as e:
        ok = "index_dist_200ma" in str(e)
        print(f"{'[PASS]' if ok else '[FAIL]'} 市场状态缺列 ⇒ 报错")
        return ok
    print("[FAIL] 市场状态缺列 ⇒ 报错（竟然接受了）")
    return False


def test_risk_fraction_is_never_changed() -> bool:
    """★ **四阶段只调持仓数，不调 R** —— 他自己说「R 的绝对值不随本金变动」。"""
    outs = [_exposure(b).settings(_state(), DEFAULT).risk_fraction
            for b in (0.05, 0.50, 0.90)]
    ok = all(abs(x - DEFAULT.risk_fraction) < 1e-12 for x in outs)
    print(f"{'[PASS]' if ok else '[FAIL]'} 四阶段不动 risk_fraction（{outs}）")
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
