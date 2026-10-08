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


# ── ★ stocks：票池（过滤归数据层，不归下游 —— 用户反馈 2026-10-06）──────────

def _seed_universe(root: Path) -> None:
    """快照（= 股票字典）+ 若干天的键（含板块 / 股票 / 非股票）。"""
    axis.put("2026-10-06", "UNIVERSE", "snapshot", {
        "market": "US",
        "rows": [{"symbol": "AAPL", "industry": "消费电子"},
                 {"symbol": "MSFT", "industry": "软件"}],
    }, root=root)
    axis.put_many("2026-10-06", [
        ("AAPL", "kline", {"bars": [{"time_key": 1, "close": 1.0}]}),
        ("MSFT", "kline", {"bars": [{"time_key": 1, "close": 2.0}]}),
        ("SPY", "kline", {"bars": [{"time_key": 1, "close": 3.0}]}),   # ETF：不在快照里
        ("LIST23925", "plate_members", {"value": ["AAPL"]}),            # 板块：不是标的
    ], root=root)
    axis.put_many("2026-09-01", [
        ("AAPL", "kline", {"bars": [{"time_key": 1, "close": 1.0}]}),
    ], root=root)


def test_stocks_excludes_plates_and_non_stocks():
    """票池 = 该日的键 − 板块代码 − 保留代码（UNIVERSE）− 非股票（不在快照里的 ETF）。"""
    root = Path(tempfile.mkdtemp())
    _seed_universe(root)
    acc = Access(root)
    got = acc.stocks("2026-10-06")
    assert got["stocks"] == ["AAPL", "MSFT"], got
    assert got["n_excluded_plate"] == 1        # LIST23925
    assert got["n_excluded_reserved"] == 1     # UNIVERSE（快照自己的占位键）
    assert got["n_excluded_non_stock"] == 1    # SPY


def test_stocks_is_json_serializable():
    """跨进程契约：结果可序列化（承 D）。"""
    root = Path(tempfile.mkdtemp())
    _seed_universe(root)
    json.dumps(Access(root).stocks("2026-10-06"))


def test_stocks_shows_snapshot_day_and_lookahead_flag():
    """显形：用了哪天的快照；所查日期早于快照日 ⇒ 标记含未来信息（承 P5）。"""
    root = Path(tempfile.mkdtemp())
    _seed_universe(root)
    acc = Access(root)
    same_day = acc.stocks("2026-10-06")
    assert same_day["snapshot_day"] == "2026-10-06"
    assert same_day["snapshot_is_after_day"] is False
    earlier = acc.stocks("2026-09-01")
    assert earlier["snapshot_day"] == "2026-10-06"
    assert earlier["snapshot_is_after_day"] is True, "早于快照日必须显形标记"


def test_stocks_errors_without_snapshot():
    """没有快照 → 报错（承 P1：缺就报，不猜）。"""
    root = Path(tempfile.mkdtemp())
    axis.put_many("2026-10-06", [
        ("AAPL", "kline", {"bars": [{"time_key": 1, "close": 1.0}]}),
    ], root=root)
    try:
        Access(root).stocks("2026-10-06")
    except ValueError as e:
        assert "snapshot" in str(e)
        return
    raise AssertionError("无快照应报错（承 P1）")


# ── ★ panel：截面面板（按日分组、组内可缺、不做对齐）──────────────────────

def _seed_panel_ragged(root: Path) -> None:
    """两天，标的集合不同（AAPL 两天都有，MSFT 只有第二天）。"""
    axis.put_many("2026-10-05", [
        ("AAPL", "kline", {"ktype": "K_DAY", "bars": [
            {"time_key": 100, "date": "2026-10-05", "close": 10.0}]}),
    ], root=root)
    axis.put_many("2026-10-06", [
        ("AAPL", "kline", {"ktype": "K_DAY", "bars": [
            {"time_key": 200, "date": "2026-10-06", "close": 20.0}]}),
        ("MSFT", "kline", {"ktype": "K_DAY", "bars": [
            {"time_key": 200, "date": "2026-10-06", "close": 30.0}]}),
    ], root=root)


def test_panel_groups_by_day_no_alignment():
    """截面语义：按日分组、组内可缺 —— **不补**（与 align_panel 的 ffill 相反）。"""
    root = Path(tempfile.mkdtemp())
    _seed_panel_ragged(root)
    p = Access(root).panel(["AAPL", "MSFT"], "kline")
    assert p["days"] == ["2026-10-05", "2026-10-06"]
    assert p["values"]["AAPL"]["2026-10-05"]["close"] == 10.0
    assert p["values"]["AAPL"]["2026-10-06"]["close"] == 20.0
    assert p["values"]["MSFT"]["2026-10-06"]["close"] == 30.0
    assert "2026-10-05" not in p["values"]["MSFT"], "缺就是缺，不许补（承 K4）"
    json.dumps(p)  # 跨进程契约：可序列化（承 D）


def test_panel_expands_bar_not_payload():
    """逐 bar 项展开成 bar —— 下游不必知道内部包装结构（承 P2）。"""
    root = Path(tempfile.mkdtemp())
    _seed_panel_ragged(root)
    p = Access(root).panel(["AAPL"], "kline")
    point = p["values"]["AAPL"]["2026-10-06"]
    assert "bars" not in point and point["close"] == 20.0


def test_panel_rejects_multi_bar_day():
    """一天多于一根 bar → 报错（本接口是日频截面；承 P2：不猜，不取第一根）。"""
    root = Path(tempfile.mkdtemp())
    axis.put_many("2026-10-06", [
        ("AAPL", "kline", {"bars": [{"time_key": 1, "date": "2026-10-06", "close": 1.0},
                                    {"time_key": 2, "date": "2026-10-06", "close": 2.0}]}),
    ], root=root)
    try:
        Access(root).panel(["AAPL"], "kline")
    except ValueError as e:
        assert "align_panel" in str(e), "报错要指向正确接口"
        return
    raise AssertionError("一天多根 bar 应报错（承 P2）")


def test_panel_non_bar_item_returns_scalar():
    """非逐 bar 项（net_gex）→ 标量，不展开。"""
    root = Path(tempfile.mkdtemp())
    _seed(root)
    p = Access(root).panel(["SPX"], "net_gex")
    assert p["values"]["SPX"]["2026-10-06"] == 9.6e10


def test_panel_hfq_removes_split_jump():
    """复权：拆股 4:1 → hfq 后**拆股日无假跳变**，且 **volume 不动**（承 P2）。"""
    root = Path(tempfile.mkdtemp())
    axis.put_many("2020-08-28", [
        ("AAPL", "kline", {"bars": [{"time_key": 1, "date": "2020-08-28",
                                     "open": 400.0, "high": 410.0, "low": 390.0,
                                     "close": 400.0, "volume": 1000.0}]}),
    ], root=root)
    axis.put_many("2020-08-31", [
        ("AAPL", "kline", {"bars": [{"time_key": 2, "date": "2020-08-31",
                                     "open": 100.0, "high": 105.0, "low": 99.0,
                                     "close": 100.0, "volume": 4000.0}]}),
    ], root=root)
    axis.put("2020-08-31", "AAPL", "adjust_factor",
             {"ex_div_date": "2020-08-31", "forward_adj_factorA": 0.25,
              "forward_adj_factorB": 0.0, "backward_adj_factorA": 4.0,
              "backward_adj_factorB": 0.0, "split_ratio": 0.25,
              "per_cash_div": None}, root=root)

    acc = Access(root)
    raw = acc.panel(["AAPL"], "kline")
    assert raw["values"]["AAPL"]["2020-08-28"]["close"] == 400.0
    assert raw["values"]["AAPL"]["2020-08-31"]["close"] == 100.0   # 看着像 -75%

    hfq = acc.panel(["AAPL"], "kline", adjust="hfq")
    assert hfq["values"]["AAPL"]["2020-08-28"]["close"] == 400.0
    assert hfq["values"]["AAPL"]["2020-08-31"]["close"] == 400.0   # 假跳变消失
    assert hfq["values"]["AAPL"]["2020-08-31"]["volume"] == 4000.0  # volume 不动
    assert hfq["adjust"] == "hfq"


def test_panel_shows_zero_adjust_events_when_no_factors():
    """★ 显形：库里没有该标的的复权因子 → 事件数 **0**（复权是空操作）。

    这是**静默失败**的防呆：返回里 `adjust="hfq"` 但一个事件都没用到 ——
    下游必须能看出"这次复权没生效"（承 P5）。
    ⚠️ 不能报错：`adjust_factor` 只记"有事件的日子"，无法区分"真没除权"与"我们没拉"。
    """
    root = Path(tempfile.mkdtemp())
    _seed_panel_ragged(root)          # 没有 seed 任何 adjust_factor
    acc = Access(root)
    p = acc.panel(["AAPL"], "kline", adjust="hfq")
    assert p["adjust"] == "hfq"
    assert p["n_adjust_events"] == {"AAPL": 0}, "没因子时必须显形为 0"


def test_panel_counts_adjust_events_when_present():
    """有复权因子 → 事件数 > 0（与上一条构成对照）。"""
    root = Path(tempfile.mkdtemp())
    axis.put_many("2020-08-28", [
        ("AAPL", "kline", {"bars": [{"time_key": 1, "date": "2020-08-28", "close": 400.0}]}),
    ], root=root)
    axis.put("2020-08-31", "AAPL", "adjust_factor",
             {"ex_div_date": "2020-08-31", "forward_adj_factorA": 0.25,
              "forward_adj_factorB": 0.0, "backward_adj_factorA": 4.0,
              "backward_adj_factorB": 0.0, "split_ratio": 0.25,
              "per_cash_div": None}, root=root)
    p = Access(root).panel(["AAPL"], "kline", adjust="hfq")
    assert p["n_adjust_events"] == {"AAPL": 1}


def test_align_panel_also_shows_adjust_events():
    """`align_panel` 同样显形（两个复权入口行为一致）。"""
    root = Path(tempfile.mkdtemp())
    _seed_panel_ragged(root)          # bar 带 date（复权需要交易日）
    panel = Access(root).align_panel(["AAPL"], min_bars=1, adjust="hfq")
    assert panel["n_adjust_events"] == {"AAPL": 0}


def test_panel_adjust_must_be_explicit_and_legal():
    """复权口径必须显式且合法（承 spec §4：无默认）。"""
    root = Path(tempfile.mkdtemp())
    _seed_panel_ragged(root)
    acc = Access(root)
    assert acc.panel(["AAPL"], "kline")["adjust"] is None      # 不传 = 原样 raw
    try:
        acc.panel(["AAPL"], "kline", adjust="whatever")
    except ValueError:
        return
    raise AssertionError("非法复权口径应报错")


def test_cli_exposes_stocks_and_panel():
    """跨进程契约：两个新子命令可用（承 D）。"""
    layer = Path(__file__).resolve().parent.parent
    for argv in (["stocks", "--day", "2026-09-30"],
                 ["panel", "--symbols", "AAPL", "--item", "kline"]):
        out = subprocess.run([sys.executable, "-m", "provide.cli", *argv],
                             cwd=layer, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        json.loads(out.stdout)   # 输出永远是合法 JSON


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
