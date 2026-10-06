"""复权（局部修正）—— 把跨除权日的价格比修正为**经济正确**的总收益比。

承"数据层四问 · 复权" + 回测设计 D5（唯一判据 = 正确性）：

**问题**：股票在除权除息日，raw 价会"凭空"跳一档 ——
拆股（4:1 → 价 ÷4）、派现（价 − 每股派现）。拿 raw 价算收益，
除权日会出现**假暴跌**（拆股 −75%、派现 −0.2~0.5%），标签全是噪声。

**为什么是"局部修正"，不是"整条复权序列"**：
- 回测要的是**比率**（`log(open[t+2]/open[t+1])`），不是绝对价位；
- 只有**跨除权日**的窗口需要修正，修正量就是**窗口内事件**的量；
- 不必把整条序列链式复权 —— 也就**不碰** hfq 那个没定出来的常数项 D
  （D 只在"想把因子链成一条序列"时才冒出来；不链，就没有 D）。

**经济模型（不是约定，是算术）**：持有 1 股，跨过除权日 d 后
- 拆股（`split_ratio` = 旧/新）：1 股 → `1/split_ratio` 股；
- 现金分红（`per_cash_div` + `special_dividend`，每股）：额外拿到这些现金。
⇒ 总收益比 =（期末市值 + 期间现金）/ 期初市值。

**口径天然因果**：只用窗口内**已发生**的事件，**不存在 qfq/hfq 那种
"锚定最新 vs 锚定最初"的选择**，故本模块**无 `mode` 参数**
（D8 的"前复权含未来"问题在这里根本不存在）。

**只处理"干净"的公司行动**：拆股 + 现金分红。其余（送股 / 转增 / 配股 /
增发 / 分拆 / 合股）需要份额或价格建模、**未验证 → 显形报错**，不静默处理
（承 P2 / P6）。

本模块**纯计算**（零 IO，承 G1 / G2）。

用法：
    from engine.price_adjustment import corrected_return_ratio
    # AAPL 2020-08-31 拆股日：close 499.23 → 129.04
    r = corrected_return_ratio(499.23, 129.04, [{"ex_div_date": "2020-08-31",
                                                 "split_ratio": 0.25}])
    # r ≈ 1.0339（真实当日涨幅）；raw 比 129.04/499.23 ≈ 0.2585 是假暴跌
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

__all__ = ["AdjustmentError", "restore_price", "corrected_return_ratio"]


class AdjustmentError(ValueError):
    """复权输入不合法（含未支持的公司行动）—— 显形报错（承 P2 / P6，不猜）。"""


# 需要"份额 / 价格建模"才能修正的公司行动 —— 未验证，**拒绝**而非静默忽略。
# （`split_ratio` 与 `per_cash_div` / `special_dividend` 是本模块**支持**的字段。）
_UNSUPPORTED_FIELDS: tuple[str, ...] = (
    "join_base", "join_ert",                                  # 合股
    "bonus_base", "bonus_ert", "per_share_div_ratio",         # 送股
    "transfer_base", "transfer_ert", "per_share_trans_ratio", # 转增
    "allot_base", "allot_ert", "allotment_ratio", "allotment_price",  # 配股
    "add_base", "add_ert",                                    # 增发
    "stk_spo_ratio", "stk_spo_price",                         # 定向增发
    "spin_off_base", "spin_off_ert", "spin_off_ratio",        # 分拆
)


def _guard(event: Mapping[str, Any]) -> None:
    """拒绝本模块**没有验证过**的公司行动（承 P2 / P6：不猜、不静默）。"""
    bad = [k for k in _UNSUPPORTED_FIELDS
           if event.get(k) not in (None, 0, 0.0)]
    if bad:
        raise AdjustmentError(
            f"除权日 {event.get('ex_div_date')} 含未支持的公司行动 {bad} —— "
            f"本模块只做「拆股 + 现金分红」，其余需份额/价格建模、尚未验证，"
            f"拒绝静默处理（承 P2/P6）"
        )


def _split_factor(event: Mapping[str, Any]) -> float:
    """`split_ratio`（旧/新）→ 份额倍数 `f`（新/旧）；无拆股 → 1.0。"""
    sr = event.get("split_ratio")
    if sr is None:
        return 1.0
    sr = float(sr)
    if sr <= 0:
        raise AdjustmentError(
            f"除权日 {event.get('ex_div_date')} 的 split_ratio={sr} 非法（须 > 0）"
        )
    return 1.0 / sr


def _cash_dividend(event: Mapping[str, Any]) -> float:
    """总现金分红 = `per_cash_div` + `special_dividend`（实测：MSFT 2004-11-15
    `backward_B = 3.08 = 0.08 + 3.00`，特别股息是在常规股息**之外**）。"""
    return (float(event.get("per_cash_div") or 0.0)
            + float(event.get("special_dividend") or 0.0))


def restore_price(price: float, event: Mapping[str, Any]) -> float:
    """把除权日**当天及之后**的价 → 还原到**除权日之前**的可比基准（经济正确）。

        restore = price / split_ratio + (per_cash_div + special_dividend)

    - 拆股：`price / split_ratio`（4:1，`split_ratio=0.25` → ×4）；
    - 派现：`+ 总现金分红`（把分红加回 = 总收益）。

    ⇒ 对跨该事件的两个价：`corrected_return_ratio(P_前, P_后, [event])`
      等价于 `restore_price(P_后, event) / P_前`。
    """
    p = float(price)
    if p <= 0:
        raise AdjustmentError(f"价格非法：{price!r}（须 > 0）")
    _guard(event)
    return p * _split_factor(event) + _cash_dividend(event)


def corrected_return_ratio(
    prev_price: float,
    curr_price: float,
    events: Sequence[Mapping[str, Any]],
) -> float:
    """跨除权事件的**正确价格比**（= 总收益比）。

    - `prev_price` / `curr_price`：期初 / 期末的 raw 价（同一序列，如相邻 bar 的 open）。
    - `events`：落在 `(t_prev, t_curr]` 的事件，**按时间升序**。

    **份额/现金模型**（多事件复合的确切形式，非"链式乘因子"）：

        终值 V = P_后 · ∏_i f_i  +  Σ_i  div_i · ∏_{j<i} f_j
        结果   = V / P_前

    其中 `f_i = 1/split_ratio_i`（份额倍数），`div_i` = 第 i 个事件的总现金分红。

    **为什么 `div_i` 乘的是"它**之前**的拆股"**：分红按"当时持有的股数"发 ——
    若先拆股（1→f 股），再派现，则拿到 `f · div`；反之先派现再拆股，现金不缩水。
    ⇒ 这一项是**算术**，不是约定；单事件时退化为 `restore_price(P_后)/P_前`。
    """
    prev = float(prev_price)
    if prev <= 0:
        raise AdjustmentError(f"期初价非法：{prev_price!r}（须 > 0）")
    evs = list(events)
    for e in evs:
        _guard(e)

    prod_all = 1.0
    for e in evs:
        prod_all *= _split_factor(e)
    value = float(curr_price) * prod_all

    prefix = 1.0                      # ∏_{j<i} f_j
    for e in evs:
        value += _cash_dividend(e) * prefix
        prefix *= _split_factor(e)
    return value / prev
