"""`rs_rank_1m` —— 池内截面相对强度百分位（**纯 1 个月**）。

★ **为什么它必须存在**（治 TD-05-10）：`rs_rank` 只实现了 IBD **加权 3/6/9/12 月** 一档，
而 Tugboat **筛 VCP / 紧密盘整时明写的是 `RS(1M)`**：

> §11.4 **Deepvue 筛选（示范）**：「交易所 = NYSE/NASDAQ、**`RS(1M) > 90`**（另做 3 / 6 / 12 月）、
> `ADR(20) > 2.5%` …」

他还在 §8.1 专门讲过这个口径的**含义**：

> **1 个月 > 97** ⇒ **近期才有爆发性走势，目前可能还处于短暂盘整**（例：QUBT）。

⇒ 拿"加权（偏长期）"的尺子去量"**刚爆发 + 正盘整**"的形态，会**系统性筛掉**他想要的票。

**窗口取 21 个交易日**（≈ 1 个月）—— 与他 Deepvue 预设的 `RS(1M)` 对应。
（⚠️ "1 个月"是**日历月**还是**交易日**，原文没写；这里取 21 个交易日并**显形**。）
"""
from __future__ import annotations

from factor.factor_registry import register_factor
from factor.implementations.rs_rank import RsRankFactor

#: 1 个月 ≈ 21 个交易日。
_ONE_MONTH_DAYS = 21

#: ★ **窗口口径**（原文没给"一个月"的精确定义 ⇒ 显式写在这里，不埋在参数里）。
ONE_MONTH_WEIGHTS: tuple[tuple[int, float], ...] = ((_ONE_MONTH_DAYS, 1.0),)

register_factor(RsRankFactor(name="rs_rank_1m", weights=ONE_MONTH_WEIGHTS))
