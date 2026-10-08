"""S8+ 验证 —— 富途两通道（REST / OpenD）取数能力。

依赖真实环境：REST 需凭据（~/.config/futu）；OpenD 需本机进程。
环境缺失时**跳过**（不算失败），但**能力存在**要能被静态验证。

跑法：.venv/bin/python tests/test_futu_sources.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetch import fetch_api as api  # noqa: E402
from fetch.fetch_types import FetchError  # noqa: E402


def _has_opend() -> bool:
    try:
        from futu import OpenQuoteContext, RET_OK
        ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
        try:
            r, _ = ctx.get_global_state()
            return r == RET_OK
        finally:
            ctx.close()
    except Exception:  # noqa: BLE001
        return False


def _has_rest() -> bool:
    from fetch.sources.futu.rest_source import _APPKEY, _KEY_PEM
    return _KEY_PEM.exists() and _APPKEY.exists()


# ── 能力存在性（静态，不依赖环境）──────────────────────────────────────

def test_sources_registered():
    srcs = api.available_sources()
    assert {"cboe", "futu-opend", "futu-rest"} <= set(srcs), srcs


def test_ktype_tables_present():
    from fetch.sources.futu.opend_source import NATIVE_KTYPES
    from fetch.sources.futu.rest_source import KTYPE
    # OpenD 原生含 4H（实测确认）
    assert "K_240M" in NATIVE_KTYPES
    assert "K_120M" in NATIVE_KTYPES
    # REST 编号表存在
    assert KTYPE["K_DAY"] == 2 and KTYPE["K_120M"] == 14


def test_kline_default_never_uses_opend():
    """★ 默认走 REST —— 绝不隐式消耗 OpenD 历史额度（承 P1）。"""
    # 4H 默认不静默走 OpenD，而是明确报错（让人显式决定）
    try:
        api.kline("AAPL", ktype="K_240M")   # 不传 source
    except FetchError as e:
        assert "REST 不支持" in str(e) and "source='futu-opend'" in str(e), str(e)
    else:
        raise AssertionError("4H 默认应报错，不得隐式走 OpenD 吃额度")


def test_kline_4h_explicit_opend_works():
    """显式 source='futu-opend' 才走 OpenD（吃额度，用户自担）。"""
    if not _has_opend():
        print("  (skip: OpenD 未运行)")
        return
    r = api.kline("AAPL", as_of="2026-10-06", ktype="K_240M", source="futu-opend")
    assert r.extra["ktype"] == "K_240M"


# ── REST：日线（无额度）────────────────────────────────────────────────

def test_rest_daily_kline():
    if not _has_rest():
        print("  (skip: 无 REST 凭据)")
        return
    r = api.kline("AAPL", as_of="2026-10-06", ktype="K_DAY", years=1,
                  source="futu-rest")
    assert len(r.rows) > 100
    # 必须是**最新**数据（不是上市最初那批）—— 这是 REST end+num 翻页的意义
    assert r.rows[-1]["date"] >= "2026-09-01", r.rows[-1]["date"]
    # 归一化字段齐
    assert set(("time_key", "date", "open", "high", "low", "close", "volume")) <= set(r.rows[-1])


def test_rest_2h_kline():
    if not _has_rest():
        print("  (skip)")
        return
    r = api.kline("AAPL", as_of="2026-10-06", ktype="K_120M", source="futu-rest")
    assert len(r.rows) > 10
    # 分钟线去重键用 time_key（同一天多根）
    days = {b["date"] for b in r.rows}
    assert len(r.rows) > len(days), "分钟线一天应有多根"


# ── OpenD：4H（原生）───────────────────────────────────────────────────

def test_opend_4h_kline():
    if not _has_opend():
        print("  (skip: OpenD 未运行)")
        return
    r = api.kline("AAPL", as_of="2026-10-06", ktype="K_240M", source="futu-opend")
    assert len(r.rows) > 10
    # 4H：去重键用 time_key
    assert r.extra["ktype"] == "K_240M"
    assert r.extra["intraday"] is True


def test_opend_snapshot_is_market_wide():
    if not _has_opend():
        print("  (skip)")
        return
    from fetch.sources.futu.opend_source import FutuSource
    from fetch.fetch_types import Request
    r = FutuSource().fetch_snapshot(Request(symbol="UNIVERSE"), max_pages=1)
    assert len(r.rows) > 50
    sample = r.rows[0]
    # 快照字段：chg/行业/市值（RPS 的原料）
    assert set(("symbol", "price", "market_cap")) <= set(sample)
    assert any(k.startswith("chg") for k in sample)


def test_opend_fails_loudly_when_down_or_ok():
    """OpenD 不可用 → FetchError（不静默空）；可用 → 有数据。"""
    try:
        r = api.kline("AAPL", ktype="K_DAY", source="futu-opend")
    except FetchError as e:
        assert "取数失败" in str(e) or "富途" in str(e)
    else:
        assert r.rows


def test_opend_rate_limit_buckets_declared():
    """★ 承 P2：OpenD 的限频桶必须**显式声明**（`opend:rehab` 曾漏）。

    根因（2026-10-07 实测）：`fetch_rehab` 的 docstring 写了「限频 60/30s」，
    但**没接限流器** —— 批量跑 328 只时 268 只被打回，OpenD 回：
    「获取复权因子频率太高，请求失败，**每30秒最多60次**」。
    """
    from fetch.rate_limit import declared_buckets  # noqa: PLC0415

    buckets = declared_buckets()
    for name in ("opend:watchlist", "opend:plate", "opend:screen", "opend:rehab"):
        assert name in buckets, f"限频桶未声明：{name}"


def test_rehab_actually_waits_on_its_bucket():
    """★ 阳性对照：光"声明了"不够 —— 源码里**必须真的 `wait`**。

    本次的 bug 恰恰是"声明（docstring）了但没执行"，
    所以只测"桶已声明"会漏掉它，必须查 `fetch_rehab` 的源码。
    """
    import inspect  # noqa: PLC0415

    from fetch.sources.futu import opend_source  # noqa: PLC0415

    src = inspect.getsource(opend_source.FutuSource.fetch_rehab)
    assert '_wait_limit("opend:rehab")' in src, (
        "fetch_rehab 必须在调 ctx.get_rehab 前 wait 自己的桶（承 P2：声明与执行要一致）"
    )


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
