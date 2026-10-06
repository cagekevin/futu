"""复权口径（P1）验证：K线统一不复权 + 复权因子单独存。

依赖真实环境（REST 凭据 + OpenD）。缺失时跳过取数部分，形态用假数据验证。

跑法：.venv/bin/python tests/test_adjust_factor.py
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


# ── P1：两通道不复权价格必须一致 ────────────────────────────────────────

def test_rest_and_opend_raw_prices_match():
    """★ P1：REST（autype=0）与 OpenD（autype=None）同标的同日 close 必须一致。"""
    if not (_has_rest() and _has_opend()):
        print("  (skip: 需 REST + OpenD)")
        return
    r = fetch_api.kline("AAPL", as_of="2026-10-06", ktype="K_DAY", years=1,
                        source="futu-rest")
    o = fetch_api.kline("AAPL", as_of="2026-10-06", ktype="K_DAY", source="futu-opend")
    rb = {x["date"]: x["close"] for x in r.rows}
    ob = {x["date"]: x["close"] for x in o.rows}
    common = sorted(set(rb) & set(ob))
    assert common, "两通道无共同日期"
    d = common[-1]
    assert abs(rb[d] - ob[d]) < 1e-6, f"{d}: REST={rb[d]} OpenD={ob[d]}（P1 不一致）"


def test_rest_default_is_raw():
    """REST 默认不复权（autype=0）—— 回归：曾误用 1（前复权）。"""
    if not _has_rest():
        print("  (skip)")
        return
    r = fetch_api.kline("AAPL", as_of="2026-10-06", ktype="K_DAY", years=1,
                        source="futu-rest")
    assert r.extra["autype"] == 0, r.extra


# ── 复权因子：取数 + 形态 + 入库 ────────────────────────────────────────

def test_rehab_fetch_and_shape():
    if not _has_opend():
        print("  (skip: OpenD 未运行)")
        return
    r = fetch_api.rehab("AAPL")
    assert len(r.rows) > 0
    row = r.rows[0]
    assert "ex_div_date" in row
    # 含复权因子字段
    assert "forward_adj_factorA" in row or "backward_adj_factorA" in row
    # NaN 已转 None（JSON 可序列化）
    import json
    json.dumps(row)


def test_adjust_factor_stored_by_ex_date():
    """复权因子按**除权日**存（键的"交易日"位 = ex_div_date）。"""
    root = Path(tempfile.mkdtemp())
    axis.put("2026-08-10", "AAPL", "adjust_factor",
             {"ex_div_date": "2026-08-10", "forward_adj_factorA": 0.99913}, root=root)
    got = axis.get("2026-08-10", "AAPL", "adjust_factor", root=root)
    assert got["forward_adj_factorA"] == 0.99913
    # 出现在标的的数据项列表里
    assert "adjust_factor" in axis.items("2026-08-10", "AAPL", root=root)


def test_run_adjust_factors_end_to_end():
    if not _has_opend():
        print("  (skip)")
        return
    root = Path(tempfile.mkdtemp())
    out = pipeline.run_adjust_factors(["AAPL"], root=root)
    assert out["records"] > 0
    days = [d for d, _ in axis.series("AAPL", "adjust_factor", root=root)]
    assert days == sorted(days), "应升序"
    assert "2026-08-10" in days or len(days) > 0


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
