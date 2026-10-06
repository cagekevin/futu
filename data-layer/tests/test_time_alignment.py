"""多品种时间对齐验证 —— 交集优先、降级仅 ffill、禁 bfill（承"四问·停牌/对齐"）。

跑法：.venv/bin/python tests/test_time_alignment.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.time_alignment import (  # noqa: E402
    AlignmentError, align_by_intersection,
)


def test_intersection_drops_unshared_timestamps():
    """默认交集：只保留所有品种都有真实报价的时刻。"""
    panel = align_by_intersection({
        "A": [(1, 10.0), (2, 11.0), (3, 12.0), (4, 13.0)],
        "B": [(1, 20.0), (3, 21.0)],                 # B 在 2、4 无报价（休市）
    }, min_bars=2)
    assert panel.strategy == "intersection"
    assert panel.index == (1, 3)
    assert panel.values["A"] == (10.0, 12.0)
    assert panel.values["B"] == (20.0, 21.0)         # 全是真实值，无捏造
    assert panel.n_union == 4 and panel.n_dropped == 2
    assert panel.n_filled == 0


def test_no_fake_flat_bars():
    """交集里不存在 close==open 的假 bar（对齐只挑真实时刻，不 ffill 造数）。"""
    a = [(1, 10.0), (2, 11.0), (3, 12.0)]
    b = [(1, 20.0), (3, 21.0)]
    panel = align_by_intersection({"A": a, "B": b}, min_bars=2)
    # B 在交集里每个值都来自它自己的真实报价（20、21），没有 ffill 出来的重复值
    assert panel.values["B"] == (20.0, 21.0)


def test_degrade_to_union_ffill_only():
    """交集太小 → 降级并集，但只用 ffill，且**不 bfill**（前导缺保持 None）。"""
    panel = align_by_intersection({
        "A": [(1, 10.0), (2, 11.0), (3, 12.0)],
        "B": [(2, 20.0), (3, 21.0)],                 # 1 处无报价
    }, min_bars=5)                                   # 交集只有 2 < 5 → 降级
    assert panel.strategy == "union_ffill"
    assert panel.index == (1, 2, 3)
    # B 在 t=1 没有报价 → 保持 None（绝不拿 t=2 的 20 去 bfill 未来函数）
    assert panel.values["B"] == (None, 20.0, 21.0)
    assert panel.n_filled == 0


def test_ffill_fills_gaps_after_start():
    panel = align_by_intersection({
        "A": [(1, 10.0), (2, 11.0), (3, 12.0)],
        "C": [(1, 30.0), (3, 33.0)],                 # 2 处无报价
    }, min_bars=5)
    assert panel.strategy == "union_ffill"
    assert panel.values["C"] == (30.0, 30.0, 33.0)   # 2 处用前值 ffill
    assert panel.n_filled == 1


def test_rejects_duplicate_timestamp():
    try:
        align_by_intersection({"A": [(1, 10.0), (1, 11.0)]})
    except AlignmentError:
        return
    raise AssertionError("重复时间戳应报错（承 P2，不猜）")


def test_rejects_empty_inputs():
    for bad in ({}, {"A": []}):
        try:
            align_by_intersection(bad)
        except AlignmentError:
            continue
        raise AssertionError(f"{bad!r} 应报错")


def test_aligns_full_ohlc_bars():
    """对齐对象是**整根 bar**（不是单列）—— 交集只留真实 bar，绝不捏造。"""
    spy = [(1, {"close": 100.0, "volume": 10.0}),
           (2, {"close": 101.0, "volume": 11.0}),
           (3, {"close": 102.0, "volume": 12.0})]
    fx = [(1, {"close": 1.10, "volume": 5.0}),
          (3, {"close": 1.11, "volume": 6.0})]      # 2 处休市
    panel = align_by_intersection({"SPY": spy, "FX": fx}, min_bars=2)
    assert panel.strategy == "intersection"
    assert panel.index == (1, 3)
    assert panel.values["SPY"] == (spy[0][1], spy[2][1])   # 保留整根真实 bar
    assert panel.values["FX"] == (fx[0][1], fx[1][1])      # t=2 未被捏造


def test_degrade_ffill_repeats_whole_bar_no_bfill():
    """降级时 ffill 会重复**整根 bar**（spec 说的"假 bar"）—— 但只在前导缺之后，
    且**绝不 bfill**（前导缺保持 None）。"""
    a = [(1, {"c": 1.0}), (2, {"c": 2.0}), (3, {"c": 3.0})]
    b = [(2, {"c": 20.0}), (3, {"c": 21.0})]
    panel = align_by_intersection({"A": a, "B": b}, min_bars=5)
    assert panel.strategy == "union_ffill"
    assert panel.values["B"][0] is None                    # 前导缺 → None（禁 bfill）
    assert panel.values["B"][1] == {"c": 20.0}
    assert panel.values["B"][2] == {"c": 21.0}


def test_panel_shape():
    panel = align_by_intersection({"A": [(1, 1.0)], "B": [(1, 2.0)]}, min_bars=1)
    assert panel.n_symbols == 2 and panel.n_bars == 1
    assert panel.column("A") == (1.0,)


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
