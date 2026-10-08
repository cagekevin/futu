"""M1 面板 —— 长表 ⇄ 宽表转换（承 PRD M1-3）。

判据（写死）：
  · **往返逐位相等**（含 NaN 的位置与数量）；
  · **缺就是缺** —— 缺失保持 `NaN`，**禁 `ffill` / `bfill` / `fillna`**（承 K4/D3/P6）；
  · 重复 `(trade_date, symbol)` → **报错**（不静默取一个 —— 那是在猜）。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from panel.panel_types import KLINE_BAR_FIELDS

__all__ = ["panel_from_provide", "panel_to_long", "long_to_panel"]


def _missing_frame(index: Sequence[str], columns: Sequence[str]) -> "pd.DataFrame":
    """全 `NaN` 的空宽表（**缺就是缺**的载体 —— 不是 0）。"""
    return pd.DataFrame(float("nan"), index=list(index), columns=list(columns),
                        dtype="float64")


def panel_from_provide(payload: Mapping[str, Any]) -> dict[str, "pd.DataFrame"]:
    """`provide.cli panel` 的返回 → `{字段: 宽表}`。

    宽表：index = 交易日（**升序**），columns = 标的（**升序**）。
    ⚠️ **缺就是缺**：没有 bar 的 `(日, 标的)` 保持 **NaN** —— 不填 0、不 ffill。
    ⚠️ 结构不符 → **报错**（承 P2：不猜结构）。
    """
    if not isinstance(payload, Mapping) or "values" not in payload:
        raise ValueError(
            f"panel 契约不符（无 values）：{type(payload).__name__}（承 P2：不猜结构）"
        )
    values = payload["values"]
    if not isinstance(values, Mapping):
        raise ValueError("panel 契约不符（values 不是映射）（承 P2）")

    index = sorted(str(d) for d in (payload.get("days") or ()))
    columns = sorted(str(s) for s in (payload.get("symbols") or ()))
    known_days = set(index)
    # 承 P2：`values` 有内容却报不出轴 —— 结构对不上，**报错**而不是给一张空表
    # （空表会被下游当成"这批标的没数据"，把结构错误伪装成数据缺失）。
    if values and (not index or not columns):
        raise ValueError(
            f"panel 契约不符：values 有 {len(values)} 个标的，但 "
            f"days/symbols 为空（承 P2：不猜结构）"
        )

    # 先收成 `{字段: {(日, 标的): 值}}`，最后一次性建表 ——
    # 逐格 `.at[...] = ...` 在 32 万格 × 5 字段上是灾难级的慢。
    cells: dict[str, dict[tuple[str, str], float]] = {n: {} for n in KLINE_BAR_FIELDS}
    for symbol in columns:
        per_day = values.get(symbol) or {}
        if not isinstance(per_day, Mapping):
            raise ValueError(f"{symbol} 的值不是映射（承 P2）")
        for day, bar in per_day.items():
            day = str(day)
            if day not in known_days:
                raise ValueError(
                    f"{symbol} 的 {day} 不在契约声明的 days 里（承 P2：不猜结构）"
                )
            if not isinstance(bar, Mapping):
                raise ValueError(f"{symbol}.{day} 的 bar 不是映射（承 P2）")
            for name in KLINE_BAR_FIELDS:
                if name not in bar:
                    raise ValueError(
                        f"{symbol}.{day} 的 bar 缺字段 {name!r}"
                        f"（要求 {KLINE_BAR_FIELDS}，承 P2：不猜结构）"
                    )
                raw = bar[name]
                if raw is None:
                    raise ValueError(
                        f"{symbol}.{day}.{name} 是 None —— 缺就是缺，但**不许**"
                        f"把 None 当合法 bar 值喂进面板（承 K4/P6）"
                    )
                cells[name][(day, symbol)] = float(raw)

    frames: dict[str, "pd.DataFrame"] = {}
    for name, field_cells in cells.items():
        if not field_cells:
            frames[name] = _missing_frame(index, columns)
            continue
        rows = [{"trade_date": day, "symbol": symbol, "value": value}
                for (day, symbol), value in field_cells.items()]
        frame = (
            pd.DataFrame(rows)
            .pivot(index="trade_date", columns="symbol", values="value")
            .reindex(index=index, columns=columns)
        )
        # 轴名归一：`pivot` 会把轴名设成 `trade_date` / `symbol`，而空表路径没有轴名 ——
        # 不归一，长宽往返就**不是逐位相等**（承 M1-3）。宽表契约只约定
        # "index = 交易日、columns = 标的"这个**位置含义**，不约定轴名。
        frame.index.name = None
        frame.columns.name = None
        frames[name] = frame
    return frames


def panel_to_long(values: "pd.DataFrame") -> list[dict]:
    """宽表 → 长表 `(trade_date, symbol, value)`。

    ⚠️ **缺的格子不出现**（承 K4：缺就是缺）——
    长表里没有 = **没有值**，而不是 `value=None` / `value=0`。
    """
    rows: list[dict] = []
    for day in values.index:
        for symbol in values.columns:
            value = values.at[day, symbol]
            if pd.isna(value):
                continue
            rows.append({"trade_date": str(day), "symbol": str(symbol),
                         "value": float(value)})
    return rows


def long_to_panel(rows: Iterable[Mapping[str, Any]], *,
                  dates: Sequence[str] | None = None,
                  symbols: Sequence[str] | None = None) -> "pd.DataFrame":
    """长表 → 宽表。

    ⚠️ `dates` / `symbols` **不给** → 轴从长表**推导**；此时
    「整行 / 整列全缺失」的轴会**消失**（长表里没有它的任何一条记录）。
    要保证往返**逐位相等**，必须显式给轴 —— 这是"缺就是缺"的必然结果，
    不是 bug（承 K4）。

    重复 `(trade_date, symbol)` → **报错**（不静默取一个 —— 两份值 = 两份真相）。
    """
    seen: dict[tuple[str, str], float] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"长表行不是映射：{row!r}（承 P2）")
        try:
            day = str(row["trade_date"])
            symbol = str(row["symbol"])
            value = float(row["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"长表行缺列或值非数：{row!r}（要求 trade_date/symbol/value）（承 P2）"
            ) from exc
        key = (day, symbol)
        if key in seen:
            raise ValueError(
                f"长表有重复键 {key} —— 不静默取一个（承 P2：两份值 = 两份真相）"
            )
        seen[key] = value

    index = sorted(dates) if dates is not None else sorted({d for d, _s in seen})
    columns = sorted(symbols) if symbols is not None else sorted({s for _d, s in seen})
    frame = _missing_frame(index, columns)
    known = set(index), set(columns)
    for (day, symbol), value in seen.items():
        if day not in known[0] or symbol not in known[1]:
            raise ValueError(
                f"长表有 {day}/{symbol}，但不在给定的轴里（承 P2：不猜结构）"
            )
        frame.at[day, symbol] = value
    return frame
