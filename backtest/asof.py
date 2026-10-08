"""**两个决策点** —— 治「'此刻知道什么'没有结构性保证」这一类错。

## 为什么需要这个文件（一次真实的教训）

一天里有**两个决策时刻**，能看到的数是**不同的**：

| 时刻 | 能看到 | 用来干什么 |
|---|---|---|
| **收盘后** | **含第 t 行** | 生成信号（突破要收盘才确认）|
| **开盘前** | **只到第 t−1 行** | **定仓、定曝险档位** |

我犯的错：**四阶段曝险在开盘前用了"当日收盘"算的票池宽度** ⇒ **一天前视**。
它单独看"像是对的"（`_market_state` 确实只用了历史滚动窗口），
**只有把时间轴画出来**才看得出：那个宽度是**今天收盘**才算完的。

## 本文件的作用

把两个时刻做成**两个对象**，`AtOpen` **物理上取不到**第 t 行：

```python
AtOpen(t).frame(close)      # 只到 t−1 ⇒ 想前视也拿不到数据
AtClose(t).frame(close)     # 含 t
```

⇒ 前视**不是靠"注意"，是靠"拿不到"**。

## 装配规则（契约，写在这里就不用记在脑子里）

| 用途 | 用哪个 |
|---|---|
| 选股条件 | `AtClose(t)`（信号在收盘后生成）|
| **曝险档位** | ★ `AtOpen(t)` |
| **定仓（权益）** | ★ `AtOpen(t)` |
| 成交价 | `AtOpen(t)` 的**开盘价**（市价单）|
| 出场判定 | 当日 `high/low/close` —— **出场本来就要用当日**，合法 |
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

__all__ = ["AtClose", "AtOpen", "LookaheadError"]


class LookaheadError(RuntimeError):
    """试图取"当时还不知道"的数据。**宁可炸，也不静默给错数。**"""


@dataclass(frozen=True)
class AtClose:
    """`t` 日**收盘后**能看到的切片 —— **含第 `t` 行**。

    用于**生成信号**（突破必须等收盘确认）。
    """

    cut: int          # 第 t 行的下标（含）

    def rows(self) -> slice:
        return slice(0, self.cut + 1)

    def frame(self, wide: pd.DataFrame) -> pd.DataFrame:
        """切到"含第 t 行"。"""
        return wide.iloc[self.rows()]

    def value(self, wide: pd.DataFrame, column: Any) -> pd.Series:
        """取第 t 行那一列。"""
        return wide.iloc[self.cut][column]


@dataclass(frozen=True)
class AtOpen:
    """`t` 日**开盘前**能看到的切片 —— **只到第 `t−1` 行**。

    ★ 用于**定仓与曝险档位**。它**取不到**第 t 行 ⇒ 前视不可能。
    """

    cut: int          # 第 t 行的下标（**不含**）

    def rows(self) -> slice:
        return slice(0, self.cut)

    def frame(self, wide: pd.DataFrame) -> pd.DataFrame:
        """切到"只到第 t−1 行"。"""
        return wide.iloc[self.rows()]

    def latest_index(self) -> int:
        """最后一行的下标（= `t−1`）。`t=0` 时无历史 ⇒ 抛错。"""
        if self.cut == 0:
            raise LookaheadError(
                "AtOpen(0)：第 0 天**没有历史**（开盘前什么都看不到）"
                "—— 调用方应当跳过第一天，而不是让这里返回空值")
        return self.cut - 1

    def guard(self, row: int) -> None:
        """自检：`row` 是否在"开盘前能看到"的范围里。"""
        if row >= self.cut:
            raise LookaheadError(
                f"第 {row} 行属于「第 {self.cut} 天当天」—— "
                f"`AtOpen` 只能看到第 0…{self.cut - 1} 行。"
                f"承 R2：曝险/定仓不许用当日数据（这一处曾出过一天前视）")
