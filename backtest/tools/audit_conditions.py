#!/usr/bin/env python3
"""★ 条件对账审计 —— **照定义重写**那 14 条选股条件，逐格与生产实现对比。

## 为什么需要它（它已经赚回成本了）

2026-10-08 第一次跑它，**当场抓到 T8 是个真 bug**：

```python
# 生产（错）：比的是「离 200MA 的**距离**（ATR 归一）不下降」
t8 = ma_dist_sma200 >= ma_dist_sma200.shift(20)
# 正确：比的是「**200MA 本身**不下降」
sma200 = close - ma_dist_sma200 * atr14 ;  t8 = sma200 >= sma200.shift(20)
```

200MA 在**跌**而股价在**涨**时，那个距离**照样上升** ⇒ 条件被错误放行
（实测 **56%** 的格子上两种写法结论不同）。
修完之后回测结果**从 +19.00% 掉到 +4.59%** —— 那个 bug 一直在"帮忙"。

## 方法（不是"再看一眼"）

| | |
|---|---|
| **右侧** | `TugboatBreakout._masks()` —— 向量化的生产实现 |
| **左侧** | **照 docstring 的定义、用显式下标重写**（不用 `rolling` / `shift` 那套）|
| **做法** | 随机抽 N 个 `(日, 标的)` 格，逐格比 **全部** 条件的布尔值 |

⇒ **不同实现、同一定义** —— 能抓到窗口偏移、`<` vs `<=`、`shift(1)` 漏加/多加、
以及"用了不该用的当天数据"这类错。

## ⚠️ 写这个脚本时，我自己也踩了两个坑（都是"假报不符"）

1. **窗口内 NaN 没挡** —— pandas 的 `rolling(w).mean()` 要求 w 个**非空**值，
   而用 `nanmean` 会在缺失数据上**算出值** ⇒ 新股（SOLS / KRMN / CRWV）会假报不符。
   ⇒ 一律先检查"所需窗口内全非空"。
2. **ATR 取错下标** —— 条件②用的是"**截至昨天**"的均线距离，
   而我在重写里取了 `t` 的 ATR（应为 `t−1`）⇒ 假报 14 处不符。

**⇒ 审计脚本自己也要能被审。** 报"不符"时先怀疑脚本，再去改生产代码。

## 用法

```bash
cd <repo>
factor-layer/.venv/bin/python backtest/tools/audit_conditions.py
```
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "factor-layer"))
sys.path.insert(0, str(ROOT / "backtest"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import factor.implementations  # noqa: E402
from factor.factor_registry import run_factor  # noqa: E402
from panel.panel_builder import read_panel  # noqa: E402
from panel.provide_reader import read_days, read_stocks  # noqa: E402
from strategies.tugboat_breakout import REQUIRED_FACTORS, TugboatBreakout  # noqa: E402

#: 抽样格数（越大越慢；3,855 格约 27 秒）。
N_SAMPLE = 4000

#: 每个格要"全非空"的回看长度（= 最长窗口 260 + 余量）。
LOOKBACK = 262


def naive(s, t: int, j: int, P, F, C, H, L) -> dict[str, bool]:
    """**照定义、用显式下标**重算 —— 刻意不借用 `_masks` 的任何写法。"""
    n = int(P["tight_days"])
    out: dict[str, bool] = {}

    # ⚠️ 回看窗口内**不许有 NaN**（理由见模块 docstring 的坑 ①）
    if t - LOOKBACK < 0:
        return {}
    if not (np.isfinite(C[t - LOOKBACK:t + 1, j]).all()
            and np.isfinite(H[t - LOOKBACK:t + 1, j]).all()
            and np.isfinite(L[t - LOOKBACK:t + 1, j]).all()):
        return {}

    y = t - 1                                    # 全部"盘整态"条件看**昨天**

    # T1 紧区间（**今天之前**的 n 天）
    hi = np.max(H[t - n:t, j])
    lo = np.min(L[t - n:t, j])
    out["T1 紧区间(不含今天)"] = bool((hi - lo) / C[t - 1, j] <= P["tight_range_max"])

    def sma_at(idx: int, w: int) -> float:
        return float(np.mean(C[idx - w + 1:idx + 1, j]))

    def ema_at(idx: int, w: int) -> float:
        return float(pd.Series(C[:idx + 1, j]).ewm(span=w, adjust=False)
                     .mean().iloc[-1])

    d_now = sma_at(y, 10) - sma_at(y, 20)
    d_before = sma_at(y - n, 10) - sma_at(y - n, 20)
    out["T2 均线平行且收拢"] = bool(
        abs(d_now) / C[y, j] <= P["ma_converge_max"] and abs(d_now) <= abs(d_before))

    s200, s150, s50 = sma_at(y, 200), sma_at(y, 150), sma_at(y, 50)
    out["T3 >200MA"] = bool(C[y, j] > s200)
    # ★ T8：**200MA 本身**不向下（不是"距离不下降"—— 那曾是生产实现的 bug）
    out["T8 200MA不向下"] = bool(s200 >= sma_at(y - 20, 200))

    # T4 贴近任一条均线（ATR 取 **y** 的 —— 见 docstring 的坑 ②）
    tr = np.full(y + 1, np.nan)
    for k in range(1, y + 1):
        tr[k] = max(H[k, j] - L[k, j], abs(H[k, j] - C[k - 1, j]),
                    abs(L[k, j] - C[k - 1, j]))
    atr = float(pd.Series(tr[:y + 1]).ewm(alpha=1 / 14, adjust=False,
                                          min_periods=14).mean().iloc[-1])
    if not np.isfinite(atr) or atr <= 0:
        return {}
    dists = [abs(C[y, j] - ema_at(y, w)) / atr for w in (10, 20, 50)]
    dists += [abs(C[y, j] - sma_at(y, w)) / atr for w in (150, 200)]
    out["T4 贴近某条均线"] = bool(min(dists) <= P["near_ma_max_atr"])

    out["T5 12月涨>50%"] = bool(C[y, j] / C[y - 260, j] - 1.0 >= P["rise_12m_min"])

    k = int(P["no_dump_days"])
    worst = min(C[i, j] / C[i - 1, j] - 1.0 for i in range(y - k + 1, y + 1))
    adr = float(np.mean((H[y - 19:y + 1, j] - L[y - 19:y + 1, j])
                        / C[y - 19:y + 1, j]))
    out["T6 近3日无大跌"] = bool(worst > -P["no_dump_adr"] * adr)
    out["T7 不过度延伸"] = bool(C[y, j] / s50 - 1.0 <= P["overextend_max"])

    def fv(name: str, idx: int) -> float:
        a = F[name]                              # ⚠️ 是 **DataFrame**（`_masks` 要用 .shift）
        return float(a.iloc[idx, j]) if 0 <= idx < len(a) else np.nan

    out["A1 >150MA"] = bool(fv("ma_dist_sma150", y) > 0)
    out["A2 RS>=90"] = bool(fv("rs_rank", y) >= P["rs_min"])
    out["A3 近52周高点"] = bool(fv("near_52w_high", y) >= P["near_high_min"])
    step = int(P["contraction_step"])
    rngs = [fv("range_pct10", y - i * step) for i in range(int(P["contractions_min"]))]
    out["A4 波幅收缩>=3次"] = bool(all(
        np.isfinite(rngs[i]) and np.isfinite(rngs[i + 1]) and rngs[i] < rngs[i + 1]
        for i in range(len(rngs) - 1)))
    out["A5 最后段<1%"] = bool(fv("daily_range_pct", y) <= P["final_range_max"])
    out["A6 缩量"] = bool(fv("vol_ratio10_50", y) < 1.0)

    lb = int(P["breakout_lookback"])
    out["突破触发"] = bool(C[t, j] > np.max(H[t - lb:t, j]))
    return out


def main() -> int:
    stocks_day = "2026-10-07"
    days = read_days()
    panel = read_panel(read_stocks(stocks_day)["stocks"], days=days[-600:],
                       stocks_day=stocks_day)
    s = TugboatBreakout()
    P = s.params
    F = {n: run_factor(n, panel).values for n in REQUIRED_FACTORS}
    C = panel.field("close").to_numpy()
    H = panel.field("high").to_numpy()
    L = panel.field("low").to_numpy()
    D, SYMS = list(panel.dates), list(panel.symbols)

    prod = {name: m.fillna(False).to_numpy() for name, m in s._masks(panel, F)}
    print(f"面板 {len(D)} 天 × {len(SYMS)} 只｜生产实现 {len(prod)} 条条件"
          f"（`vcp_filter=False` 时 VCP 那 6 条本来就不启用）\n")

    rng = np.random.default_rng(20261008)
    ts = rng.integers(320, len(D), size=N_SAMPLE)
    js = rng.integers(0, len(SYMS), size=N_SAMPLE)

    mismatch: dict[str, list] = {}
    checked = 0
    for t, j in zip(ts, js):
        t, j = int(t), int(j)
        got = naive(s, t, j, P, F, C, H, L)
        if not got:
            continue
        checked += 1
        for k, v in got.items():
            if k in prod and bool(prod[k][t, j]) != bool(v):
                mismatch.setdefault(k, []).append(
                    (D[t], SYMS[j], bool(prod[k][t, j]), bool(v)))

    print(f"对账格数：{checked}\n")
    if not mismatch:
        print("✅ 条件**逐格完全一致**（生产实现 vs 照定义重写）")
        return 0
    for k, bad in mismatch.items():
        print(f"❌ {k}: {len(bad)} 处不符")
        for day, sym, a, b in bad[:5]:
            print(f"     {day} {sym}: 生产={a} 重写={b}")
    print("\n⚠️ **先怀疑重写脚本**（NaN 窗口 / 下标），再改生产代码 ——")
    print("   本脚本自己就假报过两次，理由见模块 docstring。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
