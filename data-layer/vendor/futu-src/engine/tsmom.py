"""时序动量（TSMOM）—— **纯函数、无 IO**。

    signal  = 过去 `lookback` 根的收益
    在场    = signal > 0  → 持有 `hold` 根；否则**空仓**（long/flat）

复现 Moskowitz / Ooi / Pedersen (2012)。原论文跨 58 个市场、long/short；
**这里是 long/flat** —— 散户做空成本高，这是**更保守**的版本。

## 为什么在 engine/ 而不是 research/tsmom/

`account.py`（账户）需要它来回答「**今天该几成仓**」。
**产品不能依赖一个可能被删的研究课题** —— 所以信号算法放 engine/，
`research/tsmom/tsmom_stats.py` 反过来 import 它。

⇒ **回测和账户用同一份口径**，改了这里两边一起变，**分叉不了**。

⚠️ **`monthly_returns` 里的 `ret` 是前瞻的** —— 它只能用于**回测/统计**，
不能拿去做当天的决策。**当天的决策只能看 `in_market`**（它只用 i 及之前的数据）。
"""

from __future__ import annotations

__all__ = ["LOOKBACK", "HOLD", "monthly_states", "monthly_returns"]

LOOKBACK = 252          # ≈ 12 个月
HOLD = 21               # ≈ 1 个月（再平衡周期）


def monthly_states(bars, lookback: int = LOOKBACK, hold: int = HOLD) -> list[tuple]:
    """逐月 `(日期, 在不在场)` —— **可用来做今天的决策**。

    **不看未来**：第 `i` 个的 `in_market` 只用 `close[:i+1]`。

    ⚠️ 范围到**最后一根**（不像 `monthly_returns` 要留 `hold` 根算前瞻收益）——
    账户问的是「**今天**该几成仓」，**最后一期必须在**。

    >>> monthly_states([{"date": f"{i}", "close": 100 + i} for i in range(300)])[-1]
    ('294', True)
    """
    close = [b["close"] for b in bars]
    return [(bars[i]["date"], close[i] / close[i - lookback] - 1.0 > 0)
            for i in range(lookback, len(close), hold)]


def monthly_returns(bars, lookback: int = LOOKBACK, hold: int = HOLD) -> list[tuple]:
    """逐月 `(日期, 在场, 该期收益, 是否换仓)`。

    ⚠️ **`ret` 是前瞻的**（从 `i` 起到 `i+hold`）—— 只用于回测/统计。
    ⚠️ `switched` = 这一期相对上一期**换了仓**（要付成本）。
    """
    close = [b["close"] for b in bars]
    out, prev = [], None
    for i in range(lookback, len(close) - hold, hold):
        sig = close[i] / close[i - lookback] - 1.0
        inm = sig > 0
        out.append((bars[i]["date"], inm, close[i + hold] / close[i] - 1.0,
                    prev is not None and inm != prev))
        prev = inm
    return out
