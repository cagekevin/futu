"""标签 `target_ret`（承 D2）。

归属：**留在回测层**（2026-10-06 更正）。
根因：它不是纯"市场事实"，而是 **价格事实 + 执行约定** ——
`log(open[t+2] / open[t+1])` 里含"**t+1 开盘成交**"这个**你的交易假设**。
把假设藏进"数据层" = 让假设隐形（将来换执行约定时不知道去哪改）；
且它是**含未来**的量（t 日的值要 t+2 才知道），而数据层的键是"观测日 t"、
**没有"可观测时刻"这个概念** —— 存进去就埋一个 lookahead 坑。
数据层只需给 `open`（它已经给了）。

口径（写死，不许改）：
    target_ret[t] = log(open[t+2] / open[t+1])
    最后两个位置置 0（边界）。

为什么是 t+1 → t+2 而不是 t → t+1：
    你看到 t 收盘 → 决策 → 实际成交在 t+1 开盘 → 收益算到 t+2 开盘。
    用 close[t] → close[t+1] 是错的（收盘瞬间无法成交），
    这半个 bar 的差异在高频策略上就是生与死。
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import backtest_config


def target_ret(opens: Sequence[float]) -> list[float]:
    """open 序列 → target_ret 序列（与输入等长；尾部 h 个置 0）。

    保留 `target_ret` 这个名字，是为了与设计文档（01/02 的 D2）**用同一套词汇**
    —— 改名会让"标签口径"在文档与代码之间对不上，反而更难懂。
    """
    horizon = backtest_config.TARGET_HORIZON
    n = len(opens)
    out = [0.0] * n
    for t in range(n - horizon):
        denom = opens[t + 1]
        numer = opens[t + horizon]
        if denom <= 0 or numer <= 0:
            raise ValueError(
                f"open 非法：open[{t + 1}]={denom} / open[{t + horizon}]={numer}"
                f"（承 P5：异常值显形，别让它变成 math domain error）"
            )
        out[t] = math.log(numer / denom)
    return out
