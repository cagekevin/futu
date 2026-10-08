"""复权换算 —— **唯一实现**（承 `docs/reference/futu/adjustment-factor.md` §6）。

下游（回测 / 前端）**不许自建**复权 —— 承最高目标"数据只有一份"。
本模块是**纯函数、零 IO**（承 G1）。

## 口径：mode **显式、无默认**（承 spec §4）

- `hfq`（后复权）：`× ∏_{d ≤ t}` —— **只用 ≤t 的事件** ⇒ **严格因果** ⇒ **回测用它**（承 D8）。
- `qfq`（前复权）：`× ∏_{d > t}` —— 用了 **t 之后**才发生的除权 ⇒ **含未来** ⇒ **只能展示**。

边界必须严格（spec §1.1 实测）：前复权用 `d >= t` → 差 **90%**（全错）。

## 我们只做"确定的部分"，不确定的**显形**（承 P6：不降级）

spec §5 的实测结论，决定了这里的**边界**：

| 事件 | 编码（实测） | 能否精确复权 |
|---|---|---|
| **拆股** | `backward_A = 1/split_ratio`（4:1 → **4.0 精确**） | ✅ 能 |
| **派现** | `backward_A = 1.0`、`backward_B = +派现`（**加法**） | ❌ **不能** |

派现的后复权是 `raw × 1 + B`，而 **`B` 的精确形式未定**（spec §5.1：5 个候选都对不上；
§5.2 更发现**富途自己的 `autype=2` 也有 ~0.1%/事件 偏差**）。
⇒ **不猜**：派现除权日 → 记进 `unusable_days`，让下游**知道哪天的收益不可信**。

⚠️ 这不是"半成品"，是**诚实的边界**：知道的精确修掉，不知道的标出来。
（派现日的真实收益 = 价格变动 **+ 现金分红**，我们只有前者；硬算 = 系统性低估。）

## 边界（**没做**的，写清为什么）

- **"因子不全就报错"（spec §6）**：判定"不全"需要一个**权威的事件全集**，
  而 `get_rehab` 只给"有事件的日子" —— 无法区分"没事件"与"缺数据"。
  ⇒ **不假装能判**。宁可不做，也不给一个会误报的检查。
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "HFQ", "QFQ", "MODES", "AdjustmentError", "AdjustmentResult", "adjust_series",
]

HFQ = "hfq"     # 后复权：只用 ≤t 的事件 → 严格因果 → **回测用**（承 D8）
QFQ = "qfq"     # 前复权：用了 t 之后的事件 → 含未来 → 只能展示（承 D8）
MODES = (HFQ, QFQ)


class AdjustmentError(Exception):
    """复权参数 / 数据不合法 —— 报错，不猜（承 P2/P6）。"""


@dataclass(frozen=True)
class AdjustmentResult:
    """复权结果 —— **自带边界说明**（承 P3：结果自证）。

    `values[i] is None` = 第 i 天的值**不可信**（该日是"未知形式"的事件日）；
    下游**不许**把 None 当 0、也不许 ffill —— 缺就是缺（承 K4）。
    """

    days: tuple[str, ...]
    values: tuple[float | None, ...]
    multipliers: tuple[float, ...]
    unusable_days: tuple[str, ...]
    mode: str
    n_split_events: int
    n_dividend_events: int


@dataclass(frozen=True)
class _Event:
    """一个除权事件（从 `adjust_factor` 的一行归一化而来）。"""

    day: str
    forward_a: float      # 前复权乘子（拆股 = split_ratio；派现 = 1 − 派现/除权前收盘）
    backward_a: float     # 后复权乘子（拆股 = 1/split_ratio；派现 = 1.0）
    is_dividend: bool     # 派现：后复权的**加法**项形式未定（spec §5.1）


def _events(factor_rows) -> list[_Event]:
    """`adjust_factor` 的行 → 事件（按日升序）。字段缺失/非数 → **报错**（承 P2）。"""
    out: list[_Event] = []
    for row in factor_rows or []:
        day = str(row.get("ex_div_date") or "")[:10]
        if not day:
            raise AdjustmentError(f"因子行缺 ex_div_date：{row!r}")
        forward_a = _number(row.get("forward_adj_factorA"), "forward_adj_factorA", day)
        backward_a = _number(row.get("backward_adj_factorA"), "backward_adj_factorA", day)
        backward_b = _number(row.get("backward_adj_factorB") or 0.0,
                             "backward_adj_factorB", day)
        # 派现的判据（实测）：后复权 A == 1 且 B != 0 —— 纯加法项。
        is_dividend = backward_a == 1.0 and backward_b != 0.0
        out.append(_Event(day=day, forward_a=forward_a, backward_a=backward_a,
                          is_dividend=is_dividend))
    out.sort(key=lambda e: e.day)
    return out


def _number(value, field: str, day: str) -> float:
    if value is None:
        raise AdjustmentError(f"{day} 的 {field} 缺失 —— 复权因子不全（承 P6：不静默当 1.0）")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AdjustmentError(f"{day} 的 {field} 不是数：{value!r}") from exc


def adjust_series(days, values, factor_rows, *, mode: str) -> AdjustmentResult:
    """raw 序列 + 因子行 → **复权序列**（唯一实现）。

    - `days`：bar 的交易日（`YYYY-MM-DD`，**升序**，与 `values` 等长）。
    - `values`：raw 值（价）。
    - `factor_rows`：`adjust_factor` 的行（含 `ex_div_date` 与因子字段）。
    - `mode`：**必须显式**（`"hfq"` / `"qfq"`），**无默认**（承 spec §4）。
    """
    if mode not in MODES:
        raise AdjustmentError(
            f"复权口径必须显式且合法：{mode!r}（可选 {MODES}，**无默认** —— 承 spec §4）"
        )
    days = [str(d)[:10] for d in days]
    if len(days) != len(values):
        raise AdjustmentError(f"days 与 values 长度不等：{len(days)} vs {len(values)}")
    if any(days[i] > days[i + 1] for i in range(len(days) - 1)):
        raise AdjustmentError("days 必须升序（复权是沿时间累乘的，乱序 = 静默错）")

    events = _events(factor_rows)
    multipliers: list[float] = []
    unusable: list[str] = []

    for day in days:
        multiplier = 1.0
        for event in events:
            if mode == HFQ:
                if event.day <= day:                 # **≤ t**：严格因果
                    multiplier *= event.backward_a
            else:
                if event.day > day:                  # **> t**：含未来（只能展示）
                    multiplier *= event.forward_a
        multipliers.append(multiplier)
        # 后复权下，派现日的"加法项"形式未定 → 这天的收益不可信（不猜）。
        if mode == HFQ and any(e.day == day and e.is_dividend for e in events):
            unusable.append(day)

    unusable_set = set(unusable)
    out_values = tuple(
        None if day in unusable_set else value * multiplier
        for day, value, multiplier in zip(days, values, multipliers)
    )

    return AdjustmentResult(
        days=tuple(days),
        values=out_values,
        multipliers=tuple(multipliers),
        unusable_days=tuple(unusable),
        mode=mode,
        n_split_events=sum(1 for e in events if not e.is_dividend),
        n_dividend_events=sum(1 for e in events if e.is_dividend),
    )
