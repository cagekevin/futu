"""S6 验证 —— 对照 PRD M4 的可验证标准（P1–P4）。

跑法：.venv/bin/python tests/test_provide.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from provide.api import Access, CONTRACT_VERSION  # noqa: E402
from store import axis  # noqa: E402


def _seed(root: Path) -> None:
    axis.put_many("2026-10-06", [
        ("SPX", "net_gex", {"value": 9.6e10, "source_key": "SPX:2026-10-06:chain",
                            "computed_at": "2026-10-06T07:00:00+00:00",
                            "bucket": "Tout", "weight_col": "open_interest",
                            "as_of": "2026-10-06"}),
        ("SPX", "zero_gamma", {"value": 7700.0, "source_key": "SPX:2026-10-06:chain",
                               "computed_at": "2026-10-06T07:00:00+00:00",
                               "bucket": "Tout", "weight_col": "open_interest",
                               "as_of": "2026-10-06"}),
        ("QQQ", "net_gex", {"value": 5.7e9, "source_key": "QQQ:2026-10-06:chain",
                            "computed_at": "2026-10-06T07:00:00+00:00",
                            "bucket": "Tout", "weight_col": "open_interest",
                            "as_of": "2026-10-06"}),
    ], root=root)
    axis.put_many("2026-10-05", [
        ("SPX", "net_gex", {"value": 9.0e10, "source_key": "SPX:2026-10-05:chain",
                            "computed_at": "2026-10-05T07:00:00+00:00",
                            "bucket": "Tout", "weight_col": "open_interest",
                            "as_of": "2026-10-05"}),
    ], root=root)


def test_p1_no_use_case_semantics():
    """P1：接口参数 / 返回无"用途"字样。"""
    root = Path(tempfile.mkdtemp())
    _seed(root)
    acc = Access(root)
    row = acc.record("2026-10-06", "SPX", "net_gex")
    text = json.dumps(row, ensure_ascii=False).lower()
    for word in ("timing", "择时", "purpose", "用途", "strategy", "策略"):
        assert word not in text, f"接口泄漏用途语义：{word}"


def test_p2_no_storage_details():
    """P2：下游不需知道路径/格式/源名。"""
    root = Path(tempfile.mkdtemp())
    _seed(root)
    acc = Access(root)
    row = acc.record("2026-10-06", "SPX", "net_gex")
    text = json.dumps(row, ensure_ascii=False).lower()
    for bad in (".json", "data/", "cboe", "futu", "parquet"):
        assert bad not in text, f"泄漏存储细节：{bad}"


def test_p3_not_a_thin_shell():
    """P3：M4 必须提供 M1 没有的能力（批量/聚合/格式/跨进程）。"""
    root = Path(tempfile.mkdtemp())
    _seed(root)
    acc = Access(root)
    # B：批量 + 矩阵
    assert hasattr(acc, "batch") and hasattr(acc, "matrix")
    m = acc.matrix("2026-10-06", ["net_gex", "zero_gamma"])
    assert m["SPX"]["net_gex"] == 9.6e10
    assert m["SPX"]["zero_gamma"] == 7700.0
    assert "QQQ" in m
    # C：行式导出（JSON 可序列化）
    rows = acc.export_rows("2026-10-06", ["net_gex"])
    json.dumps(rows)  # 不抛 = 可序列化
    assert len(rows) == 2
    # D：跨进程
    conf = Path(__file__).resolve().parent.parent / "provide" / "cli.py"
    assert conf.exists()
    out = subprocess.run(
        [sys.executable, "-m", "provide.cli", "days"],
        cwd=conf.parent.parent, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "2026-10-06" in out.stdout


def test_p4_stable_contract():
    """P4：对外契约稳定 —— 版本常量存在，返回键稳定。"""
    root = Path(tempfile.mkdtemp())
    _seed(root)
    acc = Access(root)
    row = acc.record("2026-10-06", "SPX", "net_gex")
    assert set(row) >= {"day", "symbol", "item", "value"}
    assert CONTRACT_VERSION


def test_batch_and_missing_not_backfilled():
    root = Path(tempfile.mkdtemp())
    _seed(root)
    acc = Access(root)
    got = acc.batch([
        ("2026-10-06", "SPX", "net_gex"),
        ("2026-10-06", "SPX", "call_wall"),   # 缺 → 不进结果
    ])
    assert len(got) == 1
    assert got[0].item == "net_gex"


def test_timeseries_no_backfill():
    root = Path(tempfile.mkdtemp())
    _seed(root)
    acc = Access(root)
    ts = acc.timeseries("SPX", "net_gex")
    # 只有 10-05 和 10-06（10-06 之后无，不补）
    assert [r.day for r in ts] == ["2026-10-05", "2026-10-06"]


def _seed_kline(root: Path) -> None:
    axis.put_many("2026-10-06", [
        ("SPY", "kline", {"ktype": "K_DAY", "bars": [
            {"time_key": 100, "close": 1.0},
            {"time_key": 200, "close": 2.0},
            {"time_key": 300, "close": 3.0},
        ]}),
        ("QQQ", "kline", {"ktype": "K_DAY", "bars": [
            {"time_key": 100, "close": 10.0},
            {"time_key": 300, "close": 30.0},
        ]}),
    ], root=root)


def test_align_panel_intersection_no_fake_bars():
    """M4 对外提供多品种对齐：默认**交集**，只留真实 bar，不造假（承 D1/D3）。"""
    root = Path(tempfile.mkdtemp())
    _seed_kline(root)
    acc = Access(root)
    panel = acc.align_panel(["SPY", "QQQ"], min_bars=2)
    assert panel["strategy"] == "intersection"
    assert panel["index"] == [100, 300]
    assert [b["close"] for b in panel["values"]["SPY"]] == [1.0, 3.0]
    assert [b["close"] for b in panel["values"]["QQQ"]] == [10.0, 30.0]
    assert panel["n_dropped"] == 1
    json.dumps(panel)  # 跨进程契约：可序列化（承 D）


def test_align_panel_degrades_ffill_no_bfill():
    """交集 < min_bars → 降级并集 + 仅 ffill；前导缺保持 None（**禁 bfill**）。"""
    root = Path(tempfile.mkdtemp())
    axis.put_many("2026-10-06", [
        ("SPY", "kline", {"bars": [{"time_key": 100, "close": 1.0},
                                   {"time_key": 200, "close": 2.0},
                                   {"time_key": 300, "close": 3.0}]}),
        ("QQQ", "kline", {"bars": [{"time_key": 200, "close": 20.0},
                                   {"time_key": 300, "close": 30.0}]}),
    ], root=root)
    acc = Access(root)
    panel = acc.align_panel(["SPY", "QQQ"], min_bars=5)   # 交集=2 < 5 → 降级
    assert panel["strategy"] == "union_ffill"
    assert panel["index"] == [100, 200, 300]
    assert panel["values"]["QQQ"][0] is None              # 前导缺 → None，不 bfill
    assert panel["values"]["QQQ"][1]["close"] == 20.0


def test_align_panel_rejects_non_bar_item():
    """非逐 bar 数据项（如 net_gex）→ 明确报错（承 P2，不猜结构）。"""
    root = Path(tempfile.mkdtemp())
    _seed(root)
    acc = Access(root)
    try:
        acc.align_panel(["SPX"], item="net_gex")
    except ValueError:
        return
    raise AssertionError("非逐 bar 数据项应对齐报错（承 P2）")


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
