"""S5 验证 —— 对照 PRD M5 的可验证标准（L1–L4）+ X1/X2/X4。

用**假源**（monkeypatch）跑管线，不依赖网络。
跑法：.venv/bin/python tests/test_pipeline.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
from fetch import fetch_api  # noqa: E402
from fetch import source_registry as registry  # noqa: E402
from fetch.fetch_types import ChainResult  # noqa: E402
from store import axis  # noqa: E402


def _fake_chain_result(symbol: str, as_of: str) -> ChainResult:
    rows = []
    for strike in range(90, 111, 5):
        for cp in ("C", "P"):
            rows.append({
                "contract": f"{symbol}{strike}{cp}", "expiry": "2026-10-07",
                "cp": cp, "strike": float(strike), "bid": 1.0, "ask": 1.2,
                "iv": 0.25,
                "open_interest": 2000.0 if cp == "C" else 1000.0,
                "volume": 100.0, "delta": 0.0, "gamma": 0.0,
                "last_trade_price": 1.1,
            })
    return ChainResult(symbol=symbol, spot=100.0,
                       feed_timestamp=datetime(2026, 10, 6, 3, 0),
                       fetched_at=datetime(2026, 10, 6, 3, 1),
                       rows=rows, extra={})


class _FakeSource:
    name = "fake"

    def fetch_chain(self, req):
        return _fake_chain_result(req.symbol, req.as_of)


class _FailSource:
    name = "fail"

    def fetch_chain(self, req):
        raise RuntimeError("simulated network failure")


def _install(src):
    registry.register("test-src", lambda: src)
    return "test-src"


# ── L1：交易日 = 美东日期 ────────────────────────────────────────────────

def test_l1_trading_day_is_et():
    """模拟非美东时区 → 交易日记为美东日。"""
    # 香港时间 2026-10-07 02:00 = 美东 2026-10-06 14:00
    hk = datetime(2026, 10, 7, 2, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    assert pipeline.trading_day(hk) == "2026-10-06"


# ── L2：任一步失败 → 立即停 ──────────────────────────────────────────────

def test_l2_fetch_failure_stops_pipeline():
    src = _install(_FailSource())
    root = Path(tempfile.mkdtemp())
    try:
        pipeline.run_day("2026-10-06", ["SPX"], root=root, r=0.04, source_name=src)
    except pipeline.PipelineError as e:
        assert "取数失败" in str(e)
    else:
        raise AssertionError("取数失败应停管线（承 L2）")
    # 库里不应有当天数据（承 L4）
    assert axis.days(root=root) == []


# ── L3 / L4：只编排 + 一天一个原子单位 ───────────────────────────────────

def test_l3_l4_run_and_atomic_store():
    src = _install(_FakeSource())
    root = Path(tempfile.mkdtemp())
    out = pipeline.run_day("2026-10-06", ["SPX", "QQQ"], root=root, r=0.04,
                           source_name=src)
    assert out["day"] == "2026-10-06"
    assert out["records"] > 0
    # 底层 + 指标都进了库
    items = axis.items("2026-10-06", "SPX", root=root)
    assert "chain" in items and "net_gex" in items
    assert set(axis.symbols("2026-10-06", root=root)) == {"SPX", "QQQ"}


def test_x1_indicators_store_only_how_not_values():
    """X1：指标只存"怎么算"（source_key/参数），不冗余存底层值。"""
    src = _install(_FakeSource())
    root = Path(tempfile.mkdtemp())
    pipeline.run_day("2026-10-06", ["SPX"], root=root, r=0.04, source_name=src)
    ng = axis.get("2026-10-06", "SPX", "net_gex", root=root)
    assert "source_key" in ng and "computed_at" in ng
    # 指标里不应内嵌整条链（那是底层，只有一份）
    assert "rows" not in ng
    assert "chain" not in ng


def test_x4_keys_have_no_source_name():
    src = _install(_FakeSource())
    root = Path(tempfile.mkdtemp())
    pipeline.run_day("2026-10-06", ["SPX"], root=root, r=0.04, source_name=src)
    # 文件名不含源名
    for p in (root / "2026-10-06").iterdir():
        assert "fake" not in p.name.lower()
        assert "cboe" not in p.name.lower()


def test_time_fields_are_unix_seconds():
    """时区统一：落库的时间戳一律 **Unix 秒（int）**，不是 ISO 字符串（承"四问·时区"）。"""
    src = _install(_FakeSource())
    root = Path(tempfile.mkdtemp())
    pipeline.run_day("2026-10-06", ["SPX"], root=root, r=0.04, source_name=src)

    spot = axis.get("2026-10-06", "SPX", "spot", root=root)
    assert isinstance(spot["feed_timestamp"], int)
    assert isinstance(spot["fetched_at"], int)

    chain = axis.get("2026-10-06", "SPX", "chain", root=root)
    assert isinstance(chain["feed_timestamp"], int)
    assert isinstance(chain["fetched_at"], int)

    ng = axis.get("2026-10-06", "SPX", "net_gex", root=root)
    assert isinstance(ng["computed_at"], int)
    assert ng["as_of"] == "2026-10-06"          # as_of 是交易日（日期，非时刻）


if __name__ == "__main__":
    import traceback

    # run_day 需要一个 source_name 参数 → 这里给 pipeline 打个小补丁
    # （生产代码走 registry 默认源，测试里显式指定）
    import inspect

    if "source_name" not in inspect.signature(pipeline.run_day).parameters:
        raise SystemExit("run_day 需支持 source_name 参数（供测试注入假源）")

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
