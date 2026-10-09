"""`rs_rank_6m` —— 池内截面相对强度百分位（**纯 6 个月**）。

★ **为什么它必须存在**（治 TD-05-10）：Tugboat 在 §8.1 把两个回看期**并列**给出，
并按"股票类型"分工：

> **6 个月 > 97** ⇒ 之前有过爆发性走势，**盘整时间相对较长**。
> ⇒ **大市值股票（如 Tesla）更适合用长期一点的相对强度。**

⇒ 它和 `rs_rank_1m` 是**两种可以交易的设置**，不是"对/错"两种读法。

**窗口取 126 个交易日**（≈ 6 个月）—— 与 `RS_WEIGHTS` 里 6 个月那一档**同一个窗口**，
差别只在**它是唯一一档**（纯 6 月）而不是加权。
"""
from __future__ import annotations

from factor.factor_registry import register_factor
from factor.implementations.rs_rank import RsRankFactor

#: 6 个月 ≈ 126 个交易日（与 `RS_WEIGHTS` 里 6 个月那一档同窗口）。
_SIX_MONTH_DAYS = 126

#: ★ **窗口口径**显式写在这里（承 P1：不埋在参数里）。
SIX_MONTH_WEIGHTS: tuple[tuple[int, float], ...] = ((_SIX_MONTH_DAYS, 1.0),)

register_factor(RsRankFactor(name="rs_rank_6m", weights=SIX_MONTH_WEIGHTS))
