"""S4 验证 —— 对照 PRD M3 的可验证标准（G1–G6）以及希腊值正确性。

跑法：.venv/bin/python tests/test_engine.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import greeks  # noqa: E402
from engine import gex as E  # noqa: E402


# ── 希腊值正确性（put-call parity + 已知极限）────────────────────────────

def test_bs_put_call_parity():
    s, k, t, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.2
    c = greeks.call_price(s, k, t, r, sigma)
    p = greeks.put_price(s, k, t, r, sigma)
    # C - P = S - K e^{-rT}
    lhs = c - p
    rhs = s - k * np.exp(-r * t)
    assert abs(lhs - rhs) < 1e-8, (lhs, rhs)


def test_bs_gamma_positive_and_peaks_atm():
    t, r, sigma = 0.1, 0.05, 0.2
    g_atm = greeks.gamma(100.0, 100.0, t, r, sigma)
    g_otm = greeks.gamma(100.0, 150.0, t, r, sigma)
    assert g_atm > g_otm > 0


def test_iv_roundtrip():
    s, k, t, r, sigma = 100.0, 105.0, 0.25, 0.04, 0.3
    price = greeks.call_price(s, k, t, r, sigma)
    iv = greeks.implied_vol(price, s, k, t, r, True)
    assert abs(float(iv) - sigma) < 1e-4, float(iv)


# ── G1 / G2：纯计算，零 IO ───────────────────────────────────────────────

def test_g1_g2_no_io_in_engine():
    root = Path(__file__).resolve().parent.parent / "engine"
    pat = re.compile(r"\b(requests|open|read_parquet|read_csv|to_parquet|to_csv|"
                     r"urlopen|httpx|urllib)\b")
    bad = []
    for p in root.rglob("*.py"):
        for m in pat.finditer(p.read_text(encoding="utf-8")):
            # 允许注释里出现这些词（如本测试文件自身说明），仅检查代码行
            bad.append((p.name, m.group(0)))
    assert not bad, f"engine/ 出现 IO 符号：{bad}"


def test_g5_deterministic_recompute():
    """同一输入 + 同一参数 → 结果逐位一致（承 G5）。"""
    chain = _fake_chain()
    kw = dict(as_of="2026-10-06", now_et=datetime(2026, 10, 6, 10, 0),
              r=0.04, source_key="k", bucket="Tout")
    m1 = E.compute(chain, 100.0, **kw)
    m2 = E.compute(chain, 100.0, **kw)
    for k in ("net_gex", "net_dex", "zero_gamma", "call_wall", "put_wall"):
        assert m1[k] == m2[k], (k, m1[k], m2[k])


def test_g4_output_self_provenance():
    chain = _fake_chain()
    m = E.compute(chain, 100.0, as_of="2026-10-06",
                  now_et=datetime(2026, 10, 6, 10, 0), r=0.04,
                  source_key="SPX:2026-10-06:chain")
    assert m["source_key"] == "SPX:2026-10-06:chain"
    assert "computed_at" in m and m["computed_at"]
    assert m["as_of"] == "2026-10-06"


def test_g3_no_implicit_now():
    """时刻显式传入 —— 两次用不同 now_et → DTE 结果不同（证明用的是传入值）。"""
    chain = _fake_chain()
    m1 = E.compute(chain, 100.0, as_of="2026-10-06",
                   now_et=datetime(2026, 10, 6, 10, 0), r=0.04, source_key="k")
    m2 = E.compute(chain, 100.0, as_of="2026-10-06",
                   now_et=datetime(2026, 10, 6, 15, 0), r=0.04, source_key="k")
    # 时间推进 → t 变小 → gamma 变化 → net_gex 必变
    assert m1["net_gex"] != m2["net_gex"]


# ── 0DTE 桶不选中已过期合约（回归）──────────────────────────────────────

def test_0dte_excludes_expired_contract():
    day = datetime(2026, 10, 6, 3, 0)  # 未开盘
    chain = _fake_chain(extra_expiry="2026-10-05")  # 昨日已过期
    m = E.compute(chain, 100.0, as_of="2026-10-06", now_et=day, r=0.04,
                  source_key="k", bucket="0DTE")
    assert m["net_gex_0dte"] != 0.0, "0DTE 不应因昨日残留到期而算成 0"
    # 昨日（10-05）已过期，应被排除；最近未过期到期是 10-07（DTE=1）
    assert m["dte_lo"] >= 0, f"不应选中已过期合约（dte_lo={m['dte_lo']}）"


# ── 失败显形（承 P6）────────────────────────────────────────────────────

def test_empty_chain_raises():
    try:
        E.compute([], 100.0, as_of="2026-10-06",
                  now_et=datetime(2026, 10, 6), r=0.04, source_key="k")
    except ValueError:
        pass
    else:
        raise AssertionError("空链应报错，不降级（承 P6）")


def _fake_chain(extra_expiry: str | None = None) -> list[dict]:
    """构造一条可解析的模拟期权链（ATM 附近若干 strike）。

    ⚠️ call / put 的 OI 刻意不等：call 2000 / put 1000 —— 对称的链
    净 GEX 恒为 0，测不出任何变化（踩过）。
    """
    rows = []
    for strike in range(90, 111, 5):
        for cp in ("C", "P"):
            rows.append({
                "contract": f"SYN{strike}{cp}", "expiry": "2026-10-07",
                "cp": cp, "strike": float(strike), "bid": 1.0, "ask": 1.2,
                "iv": 0.25,
                "open_interest": 2000.0 if cp == "C" else 1000.0,
                "volume": 100.0, "delta": 0.0, "gamma": 0.0,
                "last_trade_price": 1.1,
            })
    if extra_expiry:
        for cp in ("C", "P"):
            rows.append({
                "contract": f"SYNOLD{cp}", "expiry": extra_expiry, "cp": cp,
                "strike": 100.0, "bid": 0.0, "ask": 0.0, "iv": 0.25,
                "open_interest": 50000.0, "volume": 0.0, "delta": 0.0,
                "gamma": 0.0, "last_trade_price": 0.0,
            })
    return rows


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
