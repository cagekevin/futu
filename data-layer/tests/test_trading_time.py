"""时区统一验证 —— 所有时间戳 → Unix 秒 + 美东判定（承"四问·时区"）。

跑法：.venv/bin/python tests/test_trading_time.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ET  # noqa: E402
from trading_time import TimeError, et_datetime, to_unix_seconds, trading_day  # noqa: E402


def test_seconds_pass_through():
    assert to_unix_seconds(1759780740, unit="s") == 1759780740


def test_milliseconds_collapse_to_seconds():
    """REST 通道的 time_key 是毫秒 → 必须显式 unit='ms' 收敛成秒。"""
    sec = int(datetime(2026, 10, 6, 15, 59, 0, tzinfo=ET).timestamp())
    assert to_unix_seconds(sec * 1000, unit="ms") == sec
    assert to_unix_seconds(str(sec * 1000), unit="ms") == sec   # 数字字符串同理


def test_numeric_requires_explicit_unit():
    """数字时间戳**不给单位 → 报错**（不猜，承 D7/P1）。"""
    for v in (1759780740, 1759780740000, "1759780740000"):
        try:
            to_unix_seconds(v)
        except TimeError:
            continue
        raise AssertionError(f"{v!r} 不给 unit 应报错（承 D7）")


def test_out_of_range_raises():
    """量级校验：单位错导致越界 → **报错**，不静默（承 D7/P6）。"""
    bad = [
        (1759780, "s"),               # 秒÷1000 → 1970（低于 1980 下界）
        (1759780740000000, "s"),      # 微秒 → 远未来（高于 2100 上界）
        (1759780740, "ms"),           # 把"秒"误标成"毫秒" → ÷1000 → 1970
    ]
    for v, u in bad:
        try:
            to_unix_seconds(v, unit=u)
        except TimeError:
            continue
        raise AssertionError(f"{v!r} unit={u!r} 应越界报错（承 D7）")


def test_bad_unit_rejected():
    try:
        to_unix_seconds(1759780740, unit="us")
    except TimeError:
        return
    raise AssertionError("非法 unit 应报错")


def test_opend_naive_string_is_et():
    """OpenD 通道的 time_key 是美东本地 naive 字符串 → 当美东解析。"""
    got = to_unix_seconds("2026-10-06 15:59:00")
    assert got == int(datetime(2026, 10, 6, 15, 59, 0, tzinfo=ET).timestamp())
    # 2026-10 处于夏令时（EDT = UTC-4）—— int 回喂必须显式 unit（承 D7）
    assert et_datetime(got, unit="s").utcoffset() == timedelta(hours=-4)


def test_winter_is_est():
    """2026-01 处于冬令时（EST = UTC-5）—— 证明确实按 ET 而非固定偏移。"""
    got = to_unix_seconds("2026-01-15 10:00:00")
    assert et_datetime(got, unit="s").utcoffset() == timedelta(hours=-5)


def test_aware_datetime_converts_across_day():
    """UTC 2026-10-07 02:00 = 美东 2026-10-06 22:00 → 交易日应是 10-06（承 L1）。"""
    utc = datetime(2026, 10, 7, 2, 0, tzinfo=timezone.utc)
    assert trading_day(utc) == "2026-10-06"


def test_date_only_string():
    assert trading_day("2026-10-06") == "2026-10-06"


def test_iso_with_t_and_z():
    assert trading_day("2026-10-07T02:00:00Z") == "2026-10-06"


def test_rejects_bad_input():
    for bad in (None, True, "", "  ", "not-a-time", [1], {"t": 1}):
        try:
            to_unix_seconds(bad)
        except TimeError:
            continue
        raise AssertionError(f"{bad!r} 应报 TimeError（承 P1，不兜底）")


def test_round_trip():
    for s in ("2026-10-06 09:30:00", "2026-10-06 16:00:00", "2026-01-15 10:00:00"):
        assert et_datetime(to_unix_seconds(s), unit="s").strftime("%Y-%m-%d %H:%M:%S") == s


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
