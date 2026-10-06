"""M4 提供（provide）—— 对外数据查询接口。

**M4 相对 M1 的实质职责**（承 PRD M4 State 2）——只有这些才配独立存在：
    B. 批量 / 聚合查询   （M1 没有）
    C. 格式转换          （M1 没有：把内部记录翻成下游友好的行式结构）
    D. 跨进程 / 网络访问 （M1 没有：JSON 可序列化契约）

承 P1：接口参数 / 返回**无"用途"语义**（无"择时专用"字段）。
承 P2：下游**不知道**文件路径 / 格式 / 源名。
承 P3：若只转发 m1.get → 判薄壳 → 并回 M1。（本模块做了 B/C/D，非薄壳。）
承 P4：对外契约稳定 —— 内部存储格式变化不改对外签名。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import DATA_DIR
from engine.price_adjustment import corrected_return_ratio as _corrected_return_ratio
from engine.time_alignment import DEFAULT_MIN_BARS, align_by_intersection
from store import axis as _store
from store.read import Missing

__all__ = ["Access", "Record", "Missing"]

# 对外契约版本 —— 内部存储格式变化**不改**它（承 P4）。
CONTRACT_VERSION = "1.0"


@dataclass(frozen=True)
class Record:
    """对外的一条记录（P2：下游只看这个，不知路径/格式/源）。"""

    day: str
    symbol: str
    item: str
    value: Any

    def as_dict(self) -> dict:
        return {"day": self.day, "symbol": self.symbol,
                "item": self.item, "value": self.value}


class Access:
    """对外访问句柄。

    ⚠️ 这不是 M1 的薄壳：这里提供 M1 没有的**批量 / 矩阵 / 行式导出**，
    且把"内部记录结构（含 source_key 等自证字段）"翻成**下游友好的行式**。
    """

    def __init__(self, root: Path | None = None) -> None:
        self._axis = _store.Axis(root if root is not None else DATA_DIR)

    # ── 元信息（暴露"有什么"，不暴露"怎么存"）──────────────────────────

    def days(self) -> list[str]:
        return self._axis.days()

    def symbols(self, day) -> list[str]:
        return self._axis.symbols(day)

    def items(self, day, symbol: str) -> list[str]:
        return self._axis.items(day, symbol)

    def global_items(self, day) -> list[str]:
        """某天有哪些**全局数据项**（无标的，如 `calendar`）。"""
        return self._axis.global_items(day)

    def latest_day(self) -> str | None:
        ds = self.days()
        return ds[-1] if ds else None

    # ── 单点取值（B 之外的直接读，M1 层语义）────────────────────────────

    def value(self, day, symbol: str | None, item: str, *,
              default: Any = Missing) -> Any:
        """取一条记录的**值**（解包内部结构，给下游干净的值）。

        `symbol=None` → 全局项（如 `calendar`）。
        """
        rec = self._axis.get(day, symbol, item, default=default)
        if rec is Missing:
            return Missing
        return _unwrap(rec)

    def record(self, day, symbol: str | None, item: str) -> dict:
        """取一条完整记录（含自证字段），行式 dict（承 C）。"""
        rec = self._axis.get(day, symbol, item)
        return _to_row(day, symbol, item, rec)

    # ── ★ B：批量 / 聚合查询（M1 没有 —— 这是 M4 的实质职责）──────────

    def batch(self, requests: list[tuple[str | None, str, str]]) -> list[Record]:
        """批量取多条 (day, symbol, item) —— 一次调用拿回一批。

        M1 只有单点 `get`；下游要取 20 个标的 × 5 个指标时，逐个调用既慢
        又易错。这里提供批量入口（承 B）。
        """
        out: list[Record] = []
        for day, symbol, item in requests:
            try:
                v = self.value(day, symbol, item)
            except Missing:
                continue  # 缺就是缺，不补（承 K4）
            out.append(Record(day, symbol, item, v))
        return out

    def matrix(self, day, items: list[str],
               symbols: list[str] | None = None) -> dict[str, dict[str, Any]]:
        """某天 × 多标的 × 多指标 → 二维表 {symbol: {item: value}}。

        这是**下游最常用的形态**（横截面），M1 完全没有（承 B）。
        缺失项**不补**（记录里没有该键，即"缺"），承 K4。
        """
        syms = symbols if symbols is not None else self.symbols(day)
        out: dict[str, dict[str, Any]] = {}
        for s in syms:
            row: dict[str, Any] = {}
            for it in items:
                try:
                    row[it] = self.value(day, s, it)
                except Missing:
                    continue
            if row:
                out[s] = row
        return out

    def timeseries(self, symbol: str, item: str,
                   days: list[str] | None = None) -> list[Record]:
        """某标的某指标的跨日序列（行式）。缺失日**不补**（承 K4）。"""
        ser = self._axis.series(symbol, item, days=days)
        return [Record(d, symbol, item, _unwrap(rec)) for d, rec in ser]

    def panel(self, symbols: list[str], item: str,
              days: list[str] | None = None) -> dict[str, list[Record]]:
        """多标的 × 多日 × 单指标 —— 面板数据（承 B）。"""
        return {s: self.timeseries(s, item, days=days) for s in symbols}

    def align_panel(self, symbols: list[str], *, item: str = "kline",
                    days: list[str] | None = None,
                    min_bars: int = DEFAULT_MIN_BARS) -> dict:
        """★ B：多品种**按时间戳对齐** → 面板（对外聚合查询）。

        承"四问·对齐"：默认**交集**（只留所有品种都有真实报价的 bar，
        消除休市 ffill 造出的假 bar）；交集 < `min_bars` 才降级并集 + **仅 ffill**（禁 bfill）。

        只支持**逐 bar** 的数据项（`kline`）—— 其它项是"每天一条"、无 bar 级时间戳，
        按 bar 对齐无意义 → **明确报错**（承 P2：不猜结构）。
        返回 JSON 可序列化，含自证字段（策略 / 丢弃 / 填充数）。
        """
        series: dict[str, list[tuple[int, Any]]] = {}
        for s in symbols:
            bars: list[tuple[int, Any]] = []
            for _day, payload in self._axis.series(s, item, days=days):
                bars.extend(_bars_with_timestamp(payload, s, item))
            series[s] = bars
        panel = align_by_intersection(series, min_bars=min_bars)
        return {
            "index": list(panel.index),
            "values": {s: list(col) for s, col in panel.values.items()},
            "strategy": panel.strategy,
            "n_symbols": panel.n_symbols,
            "n_bars": panel.n_bars,
            "n_union": panel.n_union,
            "n_dropped": panel.n_dropped,
            "n_filled": panel.n_filled,
        }

    # ── ★ C：行式导出（下游友好的格式，M1 没有）────────────────────────

    def export_rows(self, day, items: list[str],
                    symbols: list[str] | None = None) -> list[dict]:
        """导出为**一行式**记录（每行 = 一条），JSON 可序列化（承 C/D）。

        下游拿到的是一组扁平 dict，不含内部结构，不知道存储实现（承 P2）。
        """
        m = self.matrix(day, items, symbols)
        rows = []
        for sym, row in m.items():
            for it, v in row.items():
                rows.append({"day": day, "symbol": sym, "item": it, "value": v})
        return rows


def _bars_with_timestamp(payload: Any, symbol: str, item: str) -> list[tuple[int, Any]]:
    """落库记录 → `[(Unix 秒, bar), …]`。只认**逐 bar** 形态（`{"bars": […]}`，即 K线）。

    其它形态（每天一条的指标 / `spot` / `chain`）**没有 bar 级时间戳** →
    明确报错（承 P2：不猜结构），不悄悄按别的字段对齐。
    """
    if not isinstance(payload, dict) or "bars" not in payload:
        raise ValueError(
            f"{symbol}.{item} 不是逐 bar 数据（无 bars 字段）—— "
            f"按 bar 对齐只支持 K线（承 P2）"
        )
    out: list[tuple[int, Any]] = []
    for b in payload["bars"]:
        if not isinstance(b, dict) or "time_key" not in b:
            raise ValueError(
                f"{symbol}.{item} 的 bar 缺 time_key，无法按时间戳对齐（承 P2）"
            )
        out.append((int(b["time_key"]), b))
    return out


def _unwrap(rec: Any) -> Any:
    """把内部记录解包成下游需要的"值"。

    内部记录（指标 / spot）统一形如 `{"value":…, …}` → 下游默认只要 `value`
    （承 P2：下游不必看到内部包装结构）。底层大表（chain）不含 `value` 键，
    原样返回。
    """
    if isinstance(rec, dict) and "value" in rec:
        return rec["value"]
    return rec


def _to_row(day: str, symbol: str | None, item: str, rec: Any) -> dict:
    """→ 行式 dict（合法 JSON）。`symbol=None` = 全局项。"""
    if isinstance(rec, dict) and "value" in rec:
        row = {"day": day, "symbol": symbol, "item": item, "value": rec["value"]}
        # 自证字段一并透出（承 P3），但**不**含存储路径等内部细节（承 P2）
        for k in ("source_key", "computed_at", "bucket", "weight_col", "as_of"):
            if k in rec:
                row[k] = rec[k]
        return row
    return {"day": day, "symbol": symbol, "item": item, "value": rec}
