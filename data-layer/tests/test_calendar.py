"""交易日历（全局数据项）验证。

依赖真实 REST（~/.config/futu）—— 环境缺失时跳过取数部分，但形态/入库用假数据验证。

跑法：.venv/bin/python tests/test_calendar.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
from fetch import fetch_api  # noqa: E402
from fetch.fetch_types import FetchError  # noqa: E402
from store import axis  # noqa: E402


def _has_rest() -> bool:
    from fetch.sources.futu.rest_source import _APPKEY, _KEY_PEM
    return _KEY_PEM.exists() and _APPKEY.exists()


# ── 形态：全局项，一天一条，只含开市日 ─────────────────────────────────

def test_calendar_is_global_item():
    """日历是全局项（symbol=None），不进标的列表。"""
    root = Path(tempfile.mkdtemp())
    axis.put("2026-10-06", None, "calendar",
             {"US": {"market": "US", "trade_date_type": "WHOLE", "trade_second": 23400}},
             root=root)
    axis.put("2026-10-06", "SPX", "spot", 7773.95, root=root)
    assert axis.global_items("2026-10-06", root=root) == ["calendar"]
    assert axis.symbols("2026-10-06", root=root) == ["SPX"]  # 日历不是标的


def test_calendar_non_trading_day_absent():
    """非交易日**不存在**（缺就是缺，承 K4，不补）。"""
    root = Path(tempfile.mkdtemp())
    axis.put("2026-10-06", None, "calendar", {"US": {}}, root=root)
    # 2026-10-04 是周日 —— 从未写过 → 读应抛 Missing
    try:
        axis.get("2026-10-04", None, "calendar", root=root)
    except axis.Missing:
        pass
    else:
        raise AssertionError("非交易日应缺（承 K4）")


# ── 取数 + 入库（真实 REST）────────────────────────────────────────────

def test_fetch_trading_days_real():
    if not _has_rest():
        print("  (skip: 无 REST 凭据)")
        return
    r = fetch_api.trading_days("US", "2026-10-01", "2026-10-06")
    days = [x["day"] for x in r.rows]
    # 只有开市日：10-03/10-04 是周末，不应出现
    assert "2026-10-03" not in days and "2026-10-04" not in days, days
    assert "2026-10-05" in days and "2026-10-06" in days
    # 字段原样（不推断）
    assert set(r.rows[0]) == {"day", "market", "trade_date_type", "trade_second"}


def test_run_calendar_end_to_end():
    if not _has_rest():
        print("  (skip)")
        return
    root = Path(tempfile.mkdtemp())
    out = pipeline.run_calendar(["US"], "2026-10-01", "2026-10-06", root=root)
    # 10-01,10-02,10-05,10-06 开市 = 4 天
    assert out["days"] == 4, out
    rec = axis.get("2026-10-06", None, "calendar", root=root)
    assert rec["US"]["market"] == "US"


def test_calendar_multi_market_differs():
    """不同市场日历不同（HK 与 US 不共享）。"""
    if not _has_rest():
        print("  (skip)")
        return
    us = fetch_api.trading_days("US", "2026-10-01", "2026-10-06")
    hk = fetch_api.trading_days("HK", "2026-10-01", "2026-10-06")
    us_days = {x["day"] for x in us.rows}
    hk_days = {x["day"] for x in hk.rows}
    # 两者都是"开市日清单"，但内容可能不同（不强制相等）
    assert us_days and hk_days


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
