"""**单位换算的唯实现点** —— 治「裸数字没有单位」这一类错。

## 为什么需要这个文件（一次真实的教训）

我写过这一行：

```python
width_ok = (close - stop) <= max_stop_adr * adr        # ✗ 美元 <= 比例
```

`(close − stop)` 是**美元**，`adr` 是**比例**（`0.026` = 2.6%）。
⇒ 阈值变成 `2.0 × 0.026 = 0.052` **美元** ⇒ 552 个候选只剩 **5** 个。

**它不报错、算得出数** —— 而我把它**误读成"日线做不到他的 ≤1 ADR"**，
还写进了规格。**这是本项目最贵的一个错。**

## 本文件的作用

Python 没有单位系统，所以**不能靠类型**挡住它。
能做的只有一件事：**把所有跨单位的比较，收敛到一个具名函数里**。

⇒ 从此策略代码里**只准**写 `adr_multiple(...)`，不准写 `x / y / z` 这种裸式。
⇒ 再写错，就要在**同一个文件里**写错两次。

## 命名约定（配合使用）

因子/字段的名字**必须自带单位**：

| 名字 | 单位 |
|---|---|
| `close` / `high` / `low` / `stop` / `atr14` | **美元** |
| `adr_pct20` / `atr_pct14` / `range_pct10` | **比例**（`0.026` = 2.6%）|
| `ma_dist_*` | **ATR 倍数**（无量纲）|
| `vol_ratio10_50` | **倍数**（无量纲）|
| `rs_rank` / `near_52w_high` | **百分位 / 比例** |

⇒ 看到 `adr_pct20` 就知道它是比例，**不会**写出"美元 ≤ 比例"。
"""
from __future__ import annotations

import numpy as np

__all__ = [
    "adr_multiple",
    "assert_ratio",
    "pct_of_price",
    "stop_distance_adr",
    "target_price_from_adr",
]

#: 判定"比例"与"美元"的容差 —— 用于 `assert_ratio` 的自检。
_RATIO_ABS_MAX = 5.0


def pct_of_price(distance_dollars: float, close: float) -> float:
    """价格距离（**美元**）→ **占股价的比例**（`0.03` = 3%）。"""
    if close <= 0:
        return float("nan")
    return distance_dollars / close


def adr_multiple(distance_dollars: float, close: float,
                 adr_ratio: float) -> float:
    """价格距离（**美元**）→ **ADR 倍数**（无量纲）。

    ★ **这是"美元 → ADR 倍数"的唯一换算式。**
      他的原文口径（§10.6②）：「止损距离控制在 **1–1.5 倍 ADR** 之内」。
      `1.0` 表示"正好一个日均波幅"。
    """
    if close <= 0 or not np.isfinite(adr_ratio) or adr_ratio <= 0:
        return float("nan")
    return distance_dollars / close / adr_ratio


def stop_distance_adr(entry_price: float, stop_price: float, close: float,
                      adr_ratio: float) -> float:
    """**入场价到止损价**的距离，以 ADR 倍数计。

    单独一个函数（而不是让调用方自己拼）—— 因为**这是他的核心约束**，
    出现过一次量纲错，值得一个专属名字。
    """
    return adr_multiple(entry_price - stop_price, close, adr_ratio)


def target_price_from_adr(entry_price: float, close: float, adr_ratio: float,
                          multiple: float) -> float:
    """按 **ADR 倍数**反推目标价（他的 §10.6③「至少 2–3 倍 ADR」）。

    ⚠️ 用它而**不用 `entry + R × risk`** —— 因为 R 依赖止损距离，
       而我们的止损距离**不是他的**（他是"日内第一根阳线低点 + ≤1 ADR 校验"）。
       ⇒ 用 R 表达止盈，等于在**错的刻度**上设目标。
    """
    return entry_price + multiple * adr_ratio * close


def assert_ratio(value: float, *, name: str) -> float:
    """自检：`value` **看起来是比例**（不是美元）。给"接进来的外部数据"用。

    ⚠️ 这是**粗检**（比例不会 >5），但足以挡住"把美元当比例"这类事故。
    """
    if np.isfinite(value) and abs(value) > _RATIO_ABS_MAX:
        raise ValueError(
            f"{name}={value} 看起来**不是比例**（|值| > {_RATIO_ABS_MAX}）"
            f"—— 承 R1：单位混用是本项目最贵的一类错，宁可报错也不静默算下去")
    return value
