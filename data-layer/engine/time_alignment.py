"""多品种时间对齐 —— **交集优先**，降级**仅 ffill**（禁止 bfill）。

承"数据层四问 · 停牌 / 多品种对齐"（唯一判据 = 正确性）：

**问题**：不同品种的休市 / 停牌时刻不同。若按**并集 + ffill** 拼时间轴，
外汇休市而美股开盘时，ffill 会给外汇造出一根
**OHLC 四值全相等**（波动率 0）的**假 K 线** ——
相关性 / 波动率特征全被污染。

**规矩**（写死）：
1. **默认交集**：只保留**所有品种都有真实报价**的时间戳。
2. **降级**：交集太小（`< min_bars`）才退回并集，但**只用 ffill（因果填充）**，
   **明确禁止 bfill**（用未来价填过去 = 未来函数）。
3. 结果**自证**用了哪种策略、丢了多少时间戳、填了多少格（承 P3）。

本模块是**纯计算**（零 IO，承 G1 / G2）：输入各品种的 `(时间戳, 记录)` 序列
（时间戳 = `trading_time.to_unix_seconds` 得到的 **Unix 秒**；记录 = **整根 bar**
（OHLCV dict）或任意标量），输出对齐面板。

用法：
    from engine.time_alignment import align_by_intersection
    panel = align_by_intersection({"SPY": spy_bars, "EURUSD": fx_bars})
    panel.index            # 公共时间戳（升序）
    panel.values["SPY"]    # 与 index 等长的 bar 记录序列（或标量序列）
    panel.strategy         # "intersection" | "union_ffill"
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

__all__ = ["AlignmentError", "AlignedPanel", "align_by_intersection"]

# 交集小于此 bar 数才降级为并集（并集里 ffill，禁 bfill）。
DEFAULT_MIN_BARS = 30


class AlignmentError(ValueError):
    """对齐输入不合法 —— 显形报错（承 P2 / P6，不猜）。"""


@dataclass(frozen=True)
class AlignedPanel:
    """对齐后的面板（自证：用了哪种策略、丢/填了多少）。"""

    index: tuple[int, ...]                       # 公共时间戳（升序，Unix 秒）
    values: dict[str, tuple[Any | None, ...]]    # 每品种一列，与 index 等长（记录或标量）
    strategy: str                                # "intersection" | "union_ffill"
    n_union: int                                 # 并集大小
    n_dropped: int                               # 交集相对并集丢掉的格数
    n_filled: int                                # 降级时 ffill 填充的格数

    def column(self, symbol: str) -> tuple[Any | None, ...]:
        return self.values[symbol]

    @property
    def n_symbols(self) -> int:
        return len(self.values)

    @property
    def n_bars(self) -> int:
        return len(self.index)


def align_by_intersection(
    series: dict[str, Sequence[tuple[int, Any]]],
    *,
    min_bars: int = DEFAULT_MIN_BARS,
) -> AlignedPanel:
    """多品种序列 → 对齐面板。

    - `series`：`{品种: [(Unix 秒, 记录), …]}`。记录 = **整根 bar**（OHLCV dict）
      或任意标量。同一品种**重复时间戳 → 报错**（承 P2）。
    - `min_bars`：交集小于它才降级为并集 + ffill（禁 bfill）。

    默认走**交集**（消除休市 ffill 造出的假 bar）。
    """
    if not series:
        raise AlignmentError("对齐至少需要一个品种（收到空 dict）")
    if min_bars < 1:
        raise AlignmentError(f"min_bars 必须 >= 1（收到 {min_bars}）")

    per: dict[str, dict[int, Any]] = {}
    for sym, seq in series.items():
        d: dict[int, Any] = {}
        for ts, v in seq:
            t = int(ts)
            if t in d:
                raise AlignmentError(
                    f"品种 {sym!r} 在时间戳 {t} 有重复报价 —— 不猜、先显形（承 P2）"
                )
            d[t] = v
        if not d:
            raise AlignmentError(f"品种 {sym!r} 无任何报价，无法对齐")
        per[sym] = d

    union = sorted(set().union(*(set(d) for d in per.values())))
    inter = sorted(set.intersection(*(set(d) for d in per.values())))

    # ── 主路径：交集（只留所有品种都有真实报价的时刻）──────────────────
    if len(inter) >= min_bars:
        values = {s: tuple(per[s][t] for t in inter) for s in per}
        return AlignedPanel(
            index=tuple(inter), values=values, strategy="intersection",
            n_union=len(union), n_dropped=len(union) - len(inter), n_filled=0,
        )

    # ── 降级路径：并集 + **仅 ffill**（禁 bfill）────────────────────────
    filled = 0
    values = {}
    for s, d in per.items():
        col: list[Any | None] = []
        last: Any | None = None
        for t in union:
            if t in d:
                last = d[t]
                col.append(last)
            elif last is None:
                col.append(None)      # 该品种尚未有报价 → 保持缺（绝不 bfill）
            else:
                col.append(last)      # 因果填充
                filled += 1
        values[s] = tuple(col)
    return AlignedPanel(
        index=tuple(union), values=values, strategy="union_ffill",
        n_union=len(union), n_dropped=0, n_filled=filled,
    )
