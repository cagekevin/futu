#!/usr/bin/env python3
"""对拍验证：我们的复权公式 vs **富途自己的复权 K线**。

**不落库、不改行为** —— 只回答一个问题：官方只给了单条 `复权价 = 原价 × A + B`，
**多个除权日怎么叠**（后复权对派现是加法、对拆股是乘法）？

做法：同一标的、同一区间，取
    autype=0（raw） / autype=2（后复权 hfq） / autype=1（前复权 qfq），
用库里存的 `adjust_factor` 套**若干候选公式**，看哪个逐根对上富途。

⚠️ 比较用**相对首根归一化**的比值（消掉"后复权锚点=上市首日"带来的常数差；
我们的因子集从 1987 起，未必覆盖上市首日）。

跑法：.venv/bin/python tests/verify_adjustment_formula.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import DATA_DIR  # noqa: E402
from fetch.fetch_types import Request  # noqa: E402
from fetch.sources.futu.rest_source import FutuRestSource  # noqa: E402

SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"
END = "2026-10-06"
YEARS = 8


def _klines(autype: int) -> dict[str, float]:
    res = FutuRestSource().fetch_kline(
        Request(symbol=SYMBOL), ktype="K_DAY", years=YEARS, autype=autype)
    return {r["date"]: float(r["close"]) for r in res.rows}


def _factors() -> list[dict]:
    """库里有的用库里的；没有 → 现拉（OpenD `get_rehab`，不吃历史额度）。"""
    rows = []
    for d in sorted(DATA_DIR.iterdir()):
        p = d / f"{SYMBOL}.adjust_factor.json"
        if p.exists():
            rows.append(json.loads(p.read_text()))
    if not rows:
        from fetch import fetch_api
        print(f"[factors] 库里没有 {SYMBOL} 的因子 → 现拉（OpenD get_rehab）")
        rows = list(fetch_api.rehab(SYMBOL).rows)
    rows.sort(key=lambda r: r["ex_div_date"])
    return rows


def _prod(ev, key: str) -> float:
    p = 1.0
    for e in ev:
        p *= float(e[key])
    return p


def _sum(ev, key: str) -> float:
    return sum(float(e[key]) for e in ev)


def _prod_inv(ev, key: str) -> float:
    p = 1.0
    for e in ev:
        p *= 1.0 / float(e[key])
    return p


def _compose(p: float, ev, ak: str, bk: str) -> float:
    for e in ev:
        p = p * float(e[ak]) + float(e[bk])
    return p


def main() -> int:
    raw, hfq, qfq = _klines(0), _klines(2), _klines(1)
    fac = _factors()
    dates = sorted(set(raw) & set(hfq) & set(qfq))
    t0 = dates[0]
    print(f"[fetch] raw={len(raw)} hfq={len(hfq)} qfq={len(qfq)} 因子={len(fac)} 条")
    print(f"[range] {t0} .. {dates[-1]}\n")

    def ev_at(t: str, boundary: str) -> list[dict]:
        if boundary == "<=":
            return [e for e in fac if e["ex_div_date"] <= t]
        return [e for e in fac if e["ex_div_date"] < t]

    def ev_after(t: str, boundary: str) -> list[dict]:
        if boundary == ">":
            return [e for e in fac if e["ex_div_date"] > t]
        return [e for e in fac if e["ex_div_date"] >= t]

    print("== 后复权（hfq）：d <= t（或 < t）的事件 ==")
    for boundary in ("<=", "<"):
        cands = {
            "mulA":       lambda p, ev: p * _prod(ev, "backward_adj_factorA"),
            "mulA+sumB":  lambda p, ev: p * _prod(ev, "backward_adj_factorA") + _sum(ev, "backward_adj_factorB"),
            "compose":    lambda p, ev: _compose(p, ev, "backward_adj_factorA", "backward_adj_factorB"),
            "trf(1/A_f)": lambda p, ev: p * _prod_inv(ev, "forward_adj_factorA"),
            "trf(1/A_b)": lambda p, ev: p * _prod_inv(ev, "backward_adj_factorA"),
        }
        for name, fn in cands.items():
            # 我们可能缺早期事件（常数偏移）→ 看"富途 − 我们"是否恒定
            diffs = [hfq[t] - fn(raw[t], ev_at(t, boundary)) for t in dates]
            dmin, dmax = min(diffs), max(diffs)
            spread = (dmax - dmin) / (sum(diffs) / len(diffs)) if diffs else 0.0
            print(f"  {boundary:2s} {name:10s} 差值范围=[{dmin:9.4f}, {dmax:9.4f}] 相对跨度={spread:.4%}")

    print("\n== 诊断：三条序列的原始值 ==")
    probe = [dates[0], dates[len(dates) // 4], dates[len(dates) // 2],
             dates[3 * len(dates) // 4], dates[-1]]
    for t in probe:
        ev = ev_at(t, "<=")
        print(f"  {t}  n_ev={len(ev):2d}  raw={raw[t]:9.4f}  qfq={qfq[t]:9.4f}"
              f"  hfq={hfq[t]:10.4f}  hfq/raw={hfq[t]/raw[t]:8.4f}"
              f"  qfq/raw={qfq[t]/raw[t]:7.4f}  ∏A_b={_prod(ev, 'backward_adj_factorA'):.4f}")
    print(f"  [参考] 因子表首/末除权日: {fac[0]['ex_div_date']} .. {fac[-1]['ex_div_date']}")
    print("  [参考] 最后一个除权日之后的事件数 = 0；首个 = 全部")

    print("\n== D 分解：D_obs = hfq − raw×∏A_b(≤t) ==")
    for t in probe:
        ev = ev_at(t, "<=")
        A = _prod(ev, "backward_adj_factorA")
        D = hfq[t] - raw[t] * A
        c1 = _sum(ev, "backward_adj_factorB")                       # 原始和
        c2 = sum(float(e["backward_adj_factorB"]) * _prod(
            [x for x in ev if x["ex_div_date"] > e["ex_div_date"]], "backward_adj_factorA")
            for e in ev)                                            # 按"之后拆股"缩放
        c3 = c1 * A                                                 # 按"全部拆股"缩放
        print(f"  {t}  D_obs={D:10.4f}  ΣB={c1:8.4f}  ΣB×later={c2:10.4f}  ΣB×A(t)={c3:10.4f}")

    print("\n== 反推富途的「每事件因子」 f(d) = F(后)/F(前)，F = hfq/raw ==")
    for e in fac:
        d = e["ex_div_date"]
        if not (dates[0] <= d <= dates[-1]):
            continue
        idx = next((i for i, t in enumerate(dates) if t >= d), None)
        if idx is None or idx == 0:
            continue
        ta, tb = dates[idx], dates[idx - 1]
        f = (hfq[ta] / raw[ta]) / (hfq[tb] / raw[tb])
        print(f"  {d}  f={f:.6f}  1/A_f={1/float(e['forward_adj_factorA']):.6f}"
              f"  A_b={e['backward_adj_factorA']}  B_b={e['backward_adj_factorB']}"
              f"  div={e['per_cash_div']}  raw_前={raw[tb]:.2f}")

    print("\n== 前复权（qfq）：d > t（或 >= t）的事件 ==")
    for boundary in (">", ">="):
        cands = {
            "mulA":    lambda p, ev: p * _prod(ev, "forward_adj_factorA"),
            "compose": lambda p, ev: _compose(p, ev, "forward_adj_factorA", "forward_adj_factorB"),
        }
        for name, fn in cands.items():
            errs = []
            base_o = fn(raw[t0], ev_after(t0, boundary))
            for t in dates:
                o = fn(raw[t], ev_after(t, boundary))
                errs.append(abs(o / base_o - qfq[t] / qfq[t0]) / (qfq[t] / qfq[t0]))
            print(f"  {boundary:2s} {name:10s} 归一化误差={max(errs):.6%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
