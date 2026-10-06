"""S8 验证 —— 择股数据层：RPS / 行业（纯计算）+ 富途源（失败必报）。

跑法：.venv/bin/python tests/test_s8_screen.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import rps as RS  # noqa: E402
from engine import industry as IND  # noqa: E402
from fetch import fetch_api  # noqa: E402
from fetch.fetch_types import FetchError  # noqa: E402


# ── RPS 口径 ─────────────────────────────────────────────────────────────

def test_rps_ranks_and_ties():
    r = RS.rps({"A": 0.5, "B": 0.5, "C": 0.1})
    # A、B 并列第 1（平均名次 1.5）→ 相同 RPS；C 最低
    assert r["A"] == r["B"], r
    assert r["A"] > r["C"], r
    # (1 - 1.5/3)*100 = 50；C: (1 - 3/3)*100 = 0
    assert abs(r["A"] - 50.0) < 1e-9
    assert abs(r["C"] - 0.0) < 1e-9


def test_change_uses_full_window():
    closes = [100, 101, 102, 103, 104, 110]
    ch = RS.change(closes, 5)
    assert abs(ch - (110 / 100 - 1)) < 1e-12
    # 历史不足 → None（不硬算）
    assert RS.change([100, 110], 5) is None


def test_mansfield_zero_axis():
    # 恒定 RS → Mansfield 应为 0（正好在均线上）
    flat = [1.0] * 60
    m = RS.mansfield(flat, 52)
    assert abs(m[-1]) < 1e-9, m[-1]


def test_off_high_52w():
    series = list(range(1, 300))  # 持续新高
    assert RS.off_high(series, lookback=250) == 0.0


# ── 行业状态机 ───────────────────────────────────────────────────────────

def test_industry_stats_min_count():
    rows = [{"industry": "SMALL", "chg250": 0.1} for _ in range(3)]  # 3 < 5
    rows += [{"industry": "BIG", "chg250": 0.1} for _ in range(6)]
    stats = {s.industry: s for s in IND.industry_stats(rows, "chg250", min_count=5)}
    assert "SMALL" not in stats
    assert "BIG" in stats
    assert stats["BIG"].count == 6


def test_industry_up_ratio_has_direction():
    rows = [{"industry": "X", "chg250": 0.1} for _ in range(5)]
    rows += [{"industry": "X", "chg250": -0.05} for _ in range(5)]
    s = IND.industry_stats(rows, "chg250", min_count=5)[0]
    assert abs(s.up_ratio - 0.5) < 1e-9  # 5/10 上涨
    assert s.count == 10


def test_classify_states():
    # 全周期健康 → 持续涨
    strong = {20: {"median": 0.05, "up_ratio": 0.8},
              120: {"median": 0.1, "up_ratio": 0.8},
              250: {"median": 0.2, "up_ratio": 0.8}}
    assert IND.classify(strong) == "持续涨"
    # 长期不健康 + 中期也不健康 → 持续跌
    weak = {20: {"median": -0.05, "up_ratio": 0.2},
            120: {"median": -0.1, "up_ratio": 0.3},
            250: {"median": -0.2, "up_ratio": 0.3}}
    assert IND.classify(weak) == "持续跌"
    # 全周期接近 0 → 横盘
    flat = {20: {"median": 0.001, "up_ratio": 0.5},
            120: {"median": -0.001, "up_ratio": 0.5},
            250: {"median": 0.002, "up_ratio": 0.5}}
    assert IND.classify(flat) == "横盘"
    # 缺周期 → 数据不全
    assert IND.classify({20: {"median": 0.1, "up_ratio": 0.8}}) == "数据不全"


# ── 富途源：失败必报（承 F4）─────────────────────────────────────────────

def test_futu_fails_loudly_without_opend():
    """OpenD 未启动 → 抛 FetchError（**不静默返回空**，承 F4）。"""
    try:
        r = fetch_api.kline("AAPL")
    except FetchError as e:
        assert "取数失败" in str(e) or "富途源不可用" in str(e), str(e)
    else:
        # 环境真有 OpenD 且连着 → 能取到也是正当结果；但绝不能是"静默空"。
        assert r.rows, "取数不得静默返回空（承 F4）"


def test_futu_registered():
    assert "futu-opend" in fetch_api.available_sources()


def test_futu_symbol_strips_prefix():
    from fetch.sources.futu.opend_source import _strip

    assert _strip("US.AAPL") == "AAPL"
    assert _strip("US..SPX") == "SPX"
    assert _strip("AAPL") == "AAPL"


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
