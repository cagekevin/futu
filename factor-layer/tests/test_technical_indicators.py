"""技术指标 —— **手推校验**（对照 `factor/technical_indicators.py`）。

## 为什么单独一个测试文件

`test_factor.py` 验的是 **契约**（七要素 / warm-up / 注册表 / role），
本文件验的是 **公式算得对不对** —— 两者失效模式不同：
契约对而公式错，会**一路顺畅地**产出错误结论（IC 不报错，只失真）。

## 校验方式

凡是能用"**照定义写的笨循环**"独立实现的，就用它对照**向量化实现**
（比硬编码常数更能暴露边界问题）；只有 RSI 的平滑递推用手算值锚定。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from factor.technical_indicators import (  # noqa: E402
    ADR_WINDOW, ATR_WINDOW, RSI_FLAT, RSI_WINDOW,
    adr_percent, ema, ma_distance_in_atr, mask_warmup, n_day_return,
    rise_from_low, sma, wilder_atr, wilder_rsi,
)

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"{'[PASS]' if cond else '[FAIL]'} {label}")
    if not cond:
        FAILED.append(label)


def _df(values: list[float], name: str = "S1") -> pd.DataFrame:
    return pd.DataFrame({name: values}, dtype="float64")


def _series(values: list[float], name: str = "S1") -> list[float]:
    return list(values)


# ── RSI ────────────────────────────────────────────────────────────────

def test_rsi_is_100_when_price_only_rises() -> None:
    """单调上涨 ⇒ 无损失 ⇒ RSI = 100。"""
    close = _df([100.0 + i for i in range(40)])
    rsi = wilder_rsi(close, RSI_WINDOW)
    check(float(rsi.iloc[-1, 0]) == 100.0, f"rsi 单调上涨 = {rsi.iloc[-1, 0]:.4f}（应 100）")


def test_rsi_is_0_when_price_only_falls() -> None:
    """单调下跌 ⇒ 无收益 ⇒ RSI = 0。"""
    close = _df([100.0 - i for i in range(40)])
    rsi = wilder_rsi(close, RSI_WINDOW)
    check(float(rsi.iloc[-1, 0]) == 0.0, f"rsi 单调下跌 = {rsi.iloc[-1, 0]:.4f}（应 0）")


def test_rsi_is_flat_midpoint_when_price_never_moves() -> None:
    """★ 完全平盘 ⇒ RSI = 50（**不是 100**）。

    这条**直接保护 11.5 条件⑤（`RSI > 50`）**：
    取 100 会让停牌 / 极不活跃的票被判成"极强"从而混进筛选结果。
    """
    close = _df([50.0] * 40)
    rsi = wilder_rsi(close, RSI_WINDOW)
    got = float(rsi.iloc[-1, 0])
    check(got == RSI_FLAT, f"rsi 完全平盘 = {got:.4f}（应 {RSI_FLAT}，不是 100）")


def test_rsi_matches_hand_computed_wilder_value() -> None:
    """★ 手算锚定：`window=3`、涨跌交替的序列。

    取 `closes = [10, 11, 10, 11, 10, 11, 10, 11]` ⇒
    `gain = [_, 1, 0, 1, 0, 1, 0, 1]`、`loss = [_, 0, 1, 0, 1, 0, 1, 0]`；
    `alpha = 1/3` 的递推（`y ← (2/3)y + (1/3)x`）在索引 7 处：

        avg_gain = 463/729     avg_loss = 266/729
        RSI = 100 × 463/729 = **46300/729 ≈ 63.5117**

    （推导：`y1=1, y2=2/3, y3=7/9, y4=14/27, y5=55/81, y6=110/243, y7=463/729`；
      `z1=0, z2=1/3, z3=2/9, z4=13/27, z5=26/81, z6=133/243, z7=266/729`。）
    """
    close = _df([10.0, 11.0, 10.0, 11.0, 10.0, 11.0, 10.0, 11.0])
    rsi = wilder_rsi(close, 3)
    expected = 46300.0 / 729.0
    got = float(rsi.iloc[7, 0])
    check(abs(got - expected) < 1e-9, f"rsi 手算 = {got:.10f}（应 {expected:.10f}）")


def test_rsi_warmup_length_matches_window() -> None:
    """RSI 的前 `window` 行必须是 NaN（与 `min_window` 对齐）。"""
    close = _df([100.0 + (i % 7) for i in range(40)])
    for window in (3, 14):
        rsi = wilder_rsi(close, window)
        head_ok = bool(rsi.iloc[:window].isna().to_numpy().all())
        first_ok = bool(rsi.iloc[window].notna().to_numpy().all())
        check(head_ok and first_ok,
              f"rsi(window={window}) 前 {window} 行 NaN 且第 {window} 行有值")


def test_rsi_stays_within_0_and_100() -> None:
    rng = np.random.default_rng(7)
    close = _df(list(100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 200)))))
    rsi = wilder_rsi(close, RSI_WINDOW).iloc[RSI_WINDOW:, 0]
    check(bool(((rsi >= 0.0) & (rsi <= 100.0)).all()),
          f"rsi 值域 [{rsi.min():.2f}, {rsi.max():.2f}] ⊂ [0, 100]")


# ── ATR ────────────────────────────────────────────────────────────────

def test_atr_equals_two_on_the_hand_built_panel() -> None:
    """★ 手算锚定：`close = 1..n`、`high = close+1`、`low = close−1` ⇒ **TR ≡ 2**。

    推导：`high−low = 2`；`|high − prev_close| = 2`；`|low − prev_close| = 0`
    ⇒ `TR = max(2, 2, 0) = 2` ⇒ `ATR ≡ 2.0`
    （这也是测试夹具取 `±1` 的原因 —— 让 ATR 是个整数）。
    """
    close = _df([float(i + 1) for i in range(60)])
    high, low = close + 1.0, close - 1.0
    atr = wilder_atr(high, low, close, ATR_WINDOW)
    body = atr.iloc[ATR_WINDOW:, 0]
    check(bool(np.allclose(body, 2.0)), f"atr 在夹具上 ≡ {body.iloc[0]:.6f}（应 2.0）")


def test_atr_counts_gaps_but_adr_does_not() -> None:
    """★ 口径差异：**ATR 含跳空、ADR% 不含** —— 两者必须给出不同的数。

    构造一个**有跳空**的序列（每日 `high=low=close`，但隔日大幅跳空）：
      · `high−low = 0` ⇒ **ADR% = 0**
      · 真实波幅 = 跳空幅度 ⇒ **ATR > 0**
    若这两个数相等，说明有一方**算错了口径**（而它们本该并存、分开报）。
    """
    closes = [100.0, 110.0, 100.0, 110.0, 100.0, 110.0, 100.0, 110.0, 100.0, 110.0,
              100.0, 110.0, 100.0, 110.0, 100.0, 110.0, 100.0, 110.0, 100.0, 110.0,
              100.0, 110.0]
    close = _df(closes)
    high = low = close.copy()                     # 无日内波幅，只有跳空
    atr = wilder_atr(high, low, close, 14)
    adr = adr_percent(high, low, close, 20)
    atr_last = float(atr.iloc[-1, 0])
    adr_last = float(adr.iloc[-1, 0])
    check(atr_last > 0.0 and adr_last == 0.0,
          f"有跳空时 ATR={atr_last:.4f} > 0 而 ADR%={adr_last:.4f} = 0（口径不同）")


def test_atr_warmup_length_matches_window() -> None:
    close = _df([100.0 + i * 0.5 for i in range(40)])
    atr = wilder_atr(close + 0.3, close - 0.3, close, ATR_WINDOW)
    check(bool(atr.iloc[:ATR_WINDOW].isna().to_numpy().all())
          and bool(atr.iloc[ATR_WINDOW].notna().to_numpy().all()),
          f"atr 前 {ATR_WINDOW} 行 NaN 且第 {ATR_WINDOW} 行有值")


# ── ADR% / EMA / SMA（用"照定义写的笨循环"独立对照）────────────────────

def test_adr_matches_literal_definition() -> None:
    """照定义（`mean((high−low)/close)`）的笨循环 vs 向量化实现。"""
    rng = np.random.default_rng(11)
    n, window = 120, ADR_WINDOW
    closes = list(100.0 + rng.normal(0, 1, n).cumsum())
    highs = [c + abs(x) for c, x in zip(closes, rng.normal(0, 0.5, n))]
    lows = [c - abs(x) for c, x in zip(closes, rng.normal(0, 0.5, n))]
    close, high, low = _df(closes), _df(highs), _df(lows)

    got = adr_percent(high, low, close, window).to_numpy()[:, 0]
    ratio = np.array([(h - low_) / c for h, low_, c in zip(highs, lows, closes)])
    exp = np.array([
        float(np.mean(ratio[i - window + 1:i + 1])) if i + 1 >= window else np.nan
        for i in range(n)
    ])
    exp[:window] = np.nan                          # mask_warmup 的另一行
    worst = float(np.nanmax(np.abs(got - exp)))
    check(bool(np.allclose(got, exp, equal_nan=True)),
          f"adr20 与照定义的笨循环一致（最大差 {worst:.2e}）")


def test_ema_matches_recursive_definition() -> None:
    """`ewm(span=w, adjust=False)` 的 `alpha` 必须是 **2/(w+1)**（不是 1/w）。"""
    values = [100.0 * (1 + 0.01 * np.sin(i / 3)) for i in range(60)]
    close = _df(values)
    for window in (10, 20):
        alpha = 2.0 / (window + 1)
        expected, prev = [], None
        for x in values:
            prev = x if prev is None else alpha * x + (1 - alpha) * prev
            expected.append(prev)
        got = ema(close, window).to_numpy()[:, 0]
        exp = np.array(expected, dtype=float)
        exp[:window] = np.nan
        check(bool(np.allclose(got, exp, equal_nan=True)),
              f"ema{window} 与递推定义一致（alpha=2/({window}+1)）")


def test_sma_matches_mean_of_window() -> None:
    values = [float(i) for i in range(40)]
    close = _df(values)
    got = sma(close, 10).to_numpy()[:, 0]
    exp = np.array([float(np.mean(values[i - 9:i + 1])) if i >= 9 else np.nan
                    for i in range(40)])
    exp[:10] = np.nan
    check(bool(np.allclose(got, exp, equal_nan=True)), "sma10 与窗口均值一致")


# ── 收益 / 离底 ────────────────────────────────────────────────────────

def test_n_day_return_matches_definition() -> None:
    values = [100.0, 101.0, 102.5, 99.0, 103.0, 104.0]
    close = _df(values)
    got = n_day_return(close, 2).to_numpy()[:, 0]
    exp = np.array([np.nan, np.nan, values[2] / values[0] - 1,
                    values[3] / values[1] - 1, values[4] / values[2] - 1,
                    values[5] / values[3] - 1])
    check(bool(np.allclose(got, exp, equal_nan=True)), "n_day_return 与定义一致")


def test_rise_from_low_is_not_n_day_return() -> None:
    """★ 语义区分：**"从最低点涨" ≠ "N 日收益"**（条件③ 的口径之争）。

    构造"先阴跌、再小幅反弹"：`rise_from_low` 为正，而 `n_day_return` 仍为负。
    若两者相等，说明有一个写错了 —— 那会让条件③ **悄悄变成另一条规则**。
    """
    values = [100.0, 95.0, 90.0, 85.0, 80.0, 82.0, 84.0]      # 阴跌到 80 后反弹到 84
    close = _df(values)
    low = _df(values)
    window = 5
    off_low = float(rise_from_low(close, low, window).iloc[-1, 0])
    ret = float(n_day_return(close, window).iloc[-1, 0])
    check(off_low > 0.0 and ret < 0.0,
          f"off_low150 语义：离底 {off_low:+.4f} > 0 而 5 日收益 {ret:+.4f} < 0")


# ── 均线距离 / 边界 ────────────────────────────────────────────────────

def test_ma_distance_is_in_atr_units() -> None:
    """`(close − MA) / ATR`：ATR 翻倍 ⇒ 距离**减半**（证明分母真的是 ATR）。"""
    close = _df([100.0] * 30)
    ma = _df([98.0] * 30)
    atr = _df([2.0] * 30)
    d1 = float(ma_distance_in_atr(close, ma, atr).iloc[-1, 0])
    d2 = float(ma_distance_in_atr(close, ma, atr * 2.0).iloc[-1, 0])
    check(abs(d1 - 1.0) < 1e-12 and abs(d2 - 0.5) < 1e-12,
          f"ma_dist = {d1}（ATR=2）；ATR 翻倍后 = {d2}（应 0.5）")


def test_ma_distance_returns_nan_when_atr_is_zero() -> None:
    """★ `ATR == 0`（停牌 / 完全不动）⇒ `NaN`，**不是 `inf`**。

    `inf` 会一路传进统计而**不报错**，把当天那一行悄悄毁掉。
    """
    close = _df([100.0] * 30)
    ma = _df([98.0] * 30)
    atr = _df([0.0] * 30)
    got = ma_distance_in_atr(close, ma, atr)
    check(bool(got.isna().to_numpy().all()), "ATR=0 ⇒ 距离为 NaN（不是 inf）")


def test_mask_warmup_rejects_bad_window() -> None:
    try:
        mask_warmup(_df([1.0, 2.0]), 0)
    except ValueError:
        check(True, "mask_warmup(window=0) 报错")
        return
    check(False, "mask_warmup(window=0) 报错")


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        try:
            fn()
        except Exception:  # noqa: BLE001
            FAILED.append(fn.__name__)
            print(f"[FAIL] {fn.__name__}: 抛异常")
            traceback.print_exc()
    total = len(fns)
    print(f"\n{total - len(FAILED)}/{total} passed")
    sys.exit(1 if FAILED else 0)
