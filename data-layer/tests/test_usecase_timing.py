"""S7 用例：大盘择时 —— **只靠后端**（通过 provide）把择时数据取全。

这是"不拖后腿"验收判据的第一次实跑：
> 明天来个全新用途，能不能只靠这个后端做出来、而不用回头改后端？

本测试**只用 `provide.Access`**（下游视角），不 import fetch/engine/store。
证明：择时这类用途能从"底层 + 指标"自建出来。

跑法：.venv/bin/python tests/test_usecase_timing.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from provide.api import Access  # noqa: E402  —— 下游只用这一层

# 择时用途需要的数据项（来自 §6.1 的择时相关清单）
TIMING_ITEMS = [
    "spot", "net_gex", "net_gex_0dte", "net_dex",
    "zero_gamma", "call_wall", "put_wall",
    "abs_call_wall", "abs_put_wall", "pc_oi", "pc_volume",
]


def _seed(root: Path) -> None:
    """模拟管线产出的一天（这里直接写底 + 指标，等价于 pipeline 的结果）。"""
    from store import axis

    for sym, spot, gex in (("SPX", 7773.95, 9.6e10), ("NDX", 29300.0, -2.1e10),
                           ("QQQ", 756.43, 5.7e9), ("IWM", 244.5, 3.3e9)):
        axis.put("2026-10-06", sym, "spot",
                 {"value": spot, "feed_timestamp": "2026-10-06T15:59:00",
                  "fetched_at": "2026-10-06T16:01:00"}, root=root)
        for item, val in (("net_gex", gex), ("net_gex_0dte", gex * 0.1),
                          ("net_dex", -2.2e12), ("zero_gamma", spot * 0.99),
                          ("call_wall", spot * 1.01), ("put_wall", spot * 0.97),
                          ("abs_call_wall", spot * 1.01), ("abs_put_wall", spot * 0.97),
                          ("pc_oi", 1.39), ("pc_volume", 1.18)):
            axis.put("2026-10-06", sym, item,
                     {"value": val, "source_key": f"{sym}:2026-10-06:chain",
                      "computed_at": "2026-10-06T16:00:00+00:00", "bucket": "Tout",
                      "weight_col": "open_interest", "as_of": "2026-10-06"}, root=root)


def test_timing_can_be_built_from_backend_alone():
    root = Path(tempfile.mkdtemp())
    _seed(root)
    acc = Access(root)

    # ① 横截面：所有标的的择时指标，一次 matrix 拿全
    table = acc.matrix("2026-10-06", TIMING_ITEMS)
    assert set(table) == {"SPX", "NDX", "QQQ", "IWM"}, table.keys()
    for sym, row in table.items():
        missing = [it for it in TIMING_ITEMS if it not in row]
        assert not missing, f"{sym} 缺择时数据：{missing}"

    # ② 用底层+指标**自建**一个择时读数（下游逻辑，不碰后端）
    def timing_read(sym: str) -> str:
        r = table[sym]
        below = r["spot"] < r["zero_gamma"]
        if below:
            return "负 gamma（放大波动）"
        return "正 gamma（收敛波动）"

    assert timing_read("SPX") == "正 gamma（收敛波动）"

    # ③ 行式导出（下游喂给别的东西）
    rows = acc.export_rows("2026-10-06", TIMING_ITEMS)
    assert len(rows) == 4 * len(TIMING_ITEMS)

    # ④ 时间序列（择时用到跨日）
    ts = acc.timeseries("SPX", "net_gex")
    assert len(ts) >= 1


def test_new_use_case_needs_no_backend_change():
    """★ 核心验收：全新用途也能只靠后端做出来（承"不拖后腿"判据）。

    这里"发明"一个后端从未设计过的用途：**跨标的 gamma 离散度**。
    它只用底层已提供的 net_gex / spot，**没有回头改后端**。
    """
    root = Path(tempfile.mkdtemp())
    _seed(root)
    acc = Access(root)

    gex = acc.matrix("2026-10-06", ["net_gex"])
    vals = [v["net_gex"] for v in gex.values()]
    dispersion = max(vals) - min(vals)
    assert dispersion > 0  # 能算出来 → 后端够用 ✅

    # 再发明一个：绝对值最大的标的（谁在主导）
    leader = max(gex, key=lambda s: abs(gex[s]["net_gex"]))
    assert leader == "SPX"


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
