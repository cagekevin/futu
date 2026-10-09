"""**成本压力曲线**（承 V4）—— 测试。

## 这个文件钉什么

| 测试 | 不变式 |
|---|---|
| `test_ladder_is_the_v4_one` | 档位必须是 V4 原文的 **1/2/3/5×**（只报一个点 = 没落地 V4）|
| `test_curve_moves_with_cost` | **成本真的进了结果**：成本越高，总收益**单调不增**（把成本改坏 ⇒ 必须红）|
| `test_judge_row_is_the_configured_multiple` | 判据档取 `COST_STRESS_JUDGE_MULTIPLE`；**缺档 ⇒ 报错**（不静默当"没通过"）|

⚠️ 夹具**复用** `test_trade_simulator._make()`（同一份"把逐 bar 摊成宽表"的代码），
不在这里再造第二份。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
#: `run_tugboat` 在**仓库根**（它是编排层，不在 `backtest/` 里）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import backtest_config  # noqa: E402
import run_tugboat as rt  # noqa: E402
from tests.test_trade_simulator import _make  # noqa: E402
from trade_simulator import AccountPolicy, ExitPolicy, simulate  # noqa: E402

COST = backtest_config.COST_RATE
SYMS = ("AAA", "BBB")

#: 8 根 bar：两批信号（d1 / d5），每批两个标的 ⇒ **4 笔完整往返**。
#: 止损 98、成交 100 ⇒ 每笔名义额 ≈ 0.5×权益 ⇒ 成本差异看得见。
BARS = [
    (100, 100, 100, 100),      # d1 信号
    (100, 100, 100, 100),      # d2 成交（100）
    (100, 100, 97, 98),        # d3 止损 98 触发
    (100, 100, 100, 100),      # d4
    (100, 100, 100, 100),      # d5 信号
    (100, 100, 100, 100),      # d6 成交（100）
    (100, 100, 97, 98),        # d7 止损 98 触发
    (100, 100, 100, 100),      # d8
]

CANDS = [("d1", "AAA", 98.0), ("d1", "BBB", 98.0),
         ("d5", "AAA", 98.0), ("d5", "BBB", 98.0)]


def _runner():
    """造一个 `run(cost_rate) -> SimulationResult`（真 `simulate`，只换成本率）。"""
    dates, syms, frames = _make(BARS, SYMS)
    cand = pd.DataFrame(CANDS, columns=["day", "symbol", "stop_price"])
    ma = pd.DataFrame(np.nan, index=list(dates), columns=list(syms))

    def run(cost_rate: float):
        return simulate(
            dates, syms, frames, cand,
            strategy_name="t", strategy_params={}, ma_exit_level=ma,
            exit_policy=ExitPolicy(use_ma_exit=False, target_r=99, max_hold_days=99),
            account=AccountPolicy(cost_rate=cost_rate, max_positions=2,
                                  max_total_exposure=2.0))
    return run


def _rows() -> list[dict]:
    return rt.cost_stress(_runner(), base_cost_rate=COST, benchmark=None)


def test_ladder_is_the_v4_one() -> bool:
    """★ 档位必须是 V4 原文的 **1/2/3/5×**（`docs/design/01` §4.2 V4）。

    ⚠️ 这里**故意把 spec 原文写死** —— 测试是 spec 的可执行形式：
    配置改了而 spec 没改 ⇒ 必须红。
    """
    rows = _rows()
    mults = tuple(r["multiple"] for r in rows)
    ok = (mults == (1.0, 2.0, 3.0, 5.0)
          and backtest_config.COST_STRESS_JUDGE_MULTIPLE == 2.0)
    print(f"{'[PASS]' if ok else '[FAIL]'} 成本压力档位 = V4 原文 1/2/3/5×"
          f"（实测 {mults}，判据档 {backtest_config.COST_STRESS_JUDGE_MULTIPLE:g}×）")
    return ok


def test_curve_moves_with_cost() -> bool:
    """★ **成本真的进了结果**：成本越高，总收益**单调不增**，且 5× 明显低于 1×。

    判据：把 `trade_simulator` 的成本项改坏（`cost_of` 恒 0）⇒ 曲线变平 ⇒ 本条必红。
    """
    rows = _rows()
    totals = [r["total_return"] for r in rows]
    mono = all(b <= a + 1e-12 for a, b in zip(totals, totals[1:]))
    moved = totals[-1] < totals[0] - 1e-9
    ok = mono and moved
    print(f"{'[PASS]' if ok else '[FAIL]'} 成本压力曲线随成本单调下降"
          f"（{['%.4f' % t for t in totals]}，单调={mono}，确实动了={moved}）")
    return ok


def test_judge_row_is_the_configured_multiple() -> bool:
    """★ 判据档取 `COST_STRESS_JUDGE_MULTIPLE`；**缺档 ⇒ 报错**（承 P1，不静默）。"""
    rows = _rows()
    row = rt.cost_stress_judge(rows)
    got = row["multiple"] == float(backtest_config.COST_STRESS_JUDGE_MULTIPLE)
    try:
        rt.cost_stress_judge([r for r in rows if r["multiple"] != 2.0])
    except KeyError:
        raised = True
    else:
        raised = False
    ok = got and raised
    print(f"{'[PASS]' if ok else '[FAIL]'} 判据档 = 配置值（取到 {row['multiple']:g}×）"
          f"，且**缺档会报错**（{raised}）")
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
