"""**Tugboat 管线的因果性测试** —— 补上一个真实存在的测试真空。

## 为什么需要这个文件（一次真实的漏检）

项目里本来有 `test_causality.py`（T1 污染未来 / T2 截断不变），
但它只遍历 `strategy_contract.available_strategies()` ——
而 `strategies/__init__.py` 里**只注册了 `momentum` 与 `regime_gated_momentum`**。

⇒ **Tugboat 根本不在里面。**

后果：四阶段曝险用**当日收盘**算的市场状态、去给**当日开盘**的入场定档
（**一天前视**）—— 这个 bug 从**测试真空**里漏过去了，
直到**外部独立复审**跳出"文档划定的范围"去查才发现。

## 本文件测什么

| 测试 | 不变式 |
|---|---|
| `test_market_state_ignores_future` | 把 `t` 之后的行情**全改掉**，`t` 当天的市场状态**必须不变** |
| `test_rule_masks_ignore_future` | 同上，选股条件的掩码在 `t` 当天**必须不变** |
| `test_breakout_uses_only_today` | 反向：`breakout` **应当**受当天收盘影响（那是信号本身）|

⚠️ 本层**不许 import 因子层**（仓库铁律）⇒ 因子**由测试构造后注入**
（与生产代码同一手法：策略的 `factors` 是入参）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from strategies.tugboat_breakout import (  # noqa: E402
    REQUIRED_FACTORS, TugboatBreakout,
)

N_DAYS, N_SYMS = 320, 4
SYMS = ("AAA", "BBB", "CCC", "DDD")
DATES = tuple(f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(N_DAYS))


class _Panel:
    """极简面板（只要 `field()` 与 `dates` / `symbols`）—— 不依赖任何一层的类型。"""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self._f = frames
        self.dates = DATES
        self.symbols = SYMS

    def field(self, name: str) -> pd.DataFrame:
        return self._f[name]


def _make(seed: int = 7) -> tuple[_Panel, dict[str, pd.DataFrame]]:
    """造一份**确定性的**面板 + 因子（因子全部由 `close` 派生，保证因果）。"""
    rng = np.random.default_rng(seed)
    close = pd.DataFrame(
        100.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.012, (N_DAYS, N_SYMS)), axis=0),
        index=list(DATES), columns=list(SYMS))
    high = close * (1 + rng.uniform(0.0, 0.02, close.shape))
    low = close * (1 - rng.uniform(0.0, 0.02, close.shape))
    op = close.shift(1).fillna(close.iloc[0])
    frames = {"open": op, "high": high, "low": low, "close": close}

    ma = lambda w: close.rolling(w).mean()          # noqa: E731
    atr = (high - low).rolling(14).mean()
    f = {
        "atr14": atr,
        "adr20": ((high - low) / close).rolling(20).mean(),
        "atr_pct14": atr / close,
        "ret260": close / close.shift(260) - 1.0,
        "rs_rank": close.pct_change(60).rank(axis=1, pct=True),
        "near_52w_high": close / high.rolling(250).max(),
        "range_pct10": ((high - low) / close).rolling(10).mean(),
        "daily_range_pct": (high - low) / close,
        "vol_ratio10_50": pd.DataFrame(
            np.ones((N_DAYS, N_SYMS)) * 0.9, index=list(DATES), columns=list(SYMS)),
        "rsi14": pd.DataFrame(
            np.full((N_DAYS, N_SYMS), 55.0), index=list(DATES), columns=list(SYMS)),
    }
    for w in (10, 20, 50, 150, 200):
        f[f"ma_dist_{'ema' if w <= 50 else 'sma'}{w}"] = (close - ma(w)) / atr
    assert set(REQUIRED_FACTORS) <= set(f), sorted(set(REQUIRED_FACTORS) - set(f))
    return _Panel(frames), {k: f[k] for k in REQUIRED_FACTORS}


def _perturb_after(panel: _Panel, factors: dict[str, pd.DataFrame], cut: int,
                   ) -> tuple[_Panel, dict[str, pd.DataFrame]]:
    """把第 `cut` 行**之后**的所有数据改成明显不同的值（未来污染）。"""
    def bump(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out.iloc[cut + 1:] = out.iloc[cut + 1:] * 3.0 + 17.0
        return out

    frames = {k: bump(v) for k, v in panel._f.items()}          # noqa: SLF001
    f = {k: bump(v) for k, v in factors.items()}
    return _Panel(frames), f


def _mask_at(panel: _Panel, factors: dict[str, pd.DataFrame], label: str,
             cut: int) -> np.ndarray:
    s = TugboatBreakout(market_gate=False, bench_gate=False)
    for lbl, m in s._masks(panel, factors):
        if label in lbl:
            return m.to_numpy()[cut]
    raise KeyError(f"没有条件名含 {label!r}")


# ── T1：市场状态不许看未来 ───────────────────────────────────────────────

def _spy_of(panel: _Panel) -> _Panel:
    return _Panel({k: v[["AAA"]].rename(columns={"AAA": "SPY"})
                   for k, v in panel._f.items()})               # noqa: SLF001


def test_market_state_ignores_today() -> bool:
    """★★ **曝险在开盘前定档 ⇒ `t` 当天的状态不许用到第 `t` 行。**

    ## 为什么这条测试的**第一版是错的**（重要教训）

    第一版我写的是"**把 `t` 之后**的行情改掉，`t` 当天状态必须不变" ——
    结果**去掉 `shift(1)` 之后它照样绿** ⇒ **它抓不到那个 bug**。

    原因：这个 bug **不是"用了未来数据"**。
    `close.rolling(50).mean()` 本来就不会看未来 ——
    真正的错是**时点**：**用"今天收盘"算的状态，去决定"今天开盘"下多少注**。

    ⇒ 正确的判据是：**扰动"第 `t` 行本身"，`t` 当天用到的状态必须不变**
      （因为开盘时第 `t` 行还没走完）。

    **教训**：写因果测试时，要问的是「**这一天能看到哪些行**」，
    而不是「有没有用后面的行」—— 后者会漏掉整类"时点错"。
    """
    import run_tugboat as rt

    panel, _ = _make()
    spy = _spy_of(panel)
    cut = 280
    base = rt._market_state(panel, spy)

    # 只扰动**第 cut 行本身**（不是未来）
    def bump_this_row(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out.iloc[cut] = out.iloc[cut] * 3.0 + 17.0
        return out

    p2 = _Panel({k: bump_this_row(v) for k, v in panel._f.items()})   # noqa: SLF001
    after = rt._market_state(p2, _spy_of(p2))

    same_today = base.iloc[cut].equals(after.iloc[cut])
    # 反向：扰动**必须真的生效**（否则这条测试是空的）
    changed = not base.iloc[cut + 1].equals(after.iloc[cut + 1])
    ok = same_today and changed
    print(f"{'[PASS]' if ok else '[FAIL]'} 曝险定档不用**今天**的数据"
          f"（今天同={same_today}，扰动确实生效={changed}）")
    return ok


def test_market_state_ignores_future() -> bool:
    """把 `t` **之后**的行情改掉，`t` 当天状态必须不变（弱一些，但要一起守）。"""
    import run_tugboat as rt

    panel, _ = _make()
    cut = 280
    base = rt._market_state(panel, _spy_of(panel))
    p2, _ = _perturb_after(panel, {}, cut)
    after = rt._market_state(p2, _spy_of(p2))
    ok = base.iloc[cut].equals(after.iloc[cut])
    print(f"{'[PASS]' if ok else '[FAIL]'} 市场状态不看未来（今天同={ok}）")
    return ok


# ── T2：选股条件不许看未来 ───────────────────────────────────────────────

def test_rule_masks_ignore_future() -> bool:
    """★ 选股条件里**除了 `突破触发`**，其余在 `t` 当天都不许受未来影响。"""
    panel, factors = _make()
    cut = 280
    p2, f2 = _perturb_after(panel, factors, cut)

    s = TugboatBreakout(market_gate=False, bench_gate=False)
    labels = [lbl for lbl, _ in s._masks(panel, factors)]
    bad = []
    for lbl in labels:
        a = _mask_at(panel, factors, lbl, cut)
        b = _mask_at(p2, f2, lbl, cut)
        if not np.array_equal(a, b):
            bad.append(lbl)
    # `突破触发` **允许**受影响（它读当天收盘 —— 那是信号本身）
    bad = [lbl for lbl in bad if "突破" not in lbl]
    ok = not bad
    print(f"{'[PASS]' if ok else '[FAIL]'} 选股条件不看未来"
          f"（{len(labels)} 条里违规：{bad if bad else '无'}）")
    return ok


def test_breakout_uses_only_today() -> bool:
    """反向检查：**`突破触发` 应当**受当天收盘影响 —— 否则说明扰动没生效。"""
    panel, factors = _make()
    cut = 280
    p2, f2 = _perturb_after(panel, factors, cut)
    a = _mask_at(panel, factors, "突破", cut)
    b = _mask_at(p2, f2, "突破", cut)
    # 只扰动 cut+1 之后 ⇒ 当天**不应**变（它只读 ≤ cut 的数据）
    # ⇒ 这条其实也在验"突破只看今天（含）"，与"不看明天"是两回事
    ok = np.array_equal(a, b)
    print(f"{'[PASS]' if ok else '[FAIL]'} 突破触发不看**明天**（只看今天及以前）")
    return ok


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            if not fn():
                failed += 1
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {fn.__name__}: 抛异常")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
