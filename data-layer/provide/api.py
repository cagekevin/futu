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
from engine.adjust import MODES as ADJUST_MODES
from engine.adjust import adjust_series
from engine.time_alignment import DEFAULT_MIN_BARS, align_by_intersection
from store import axis as _store
from store.keys import PLATE_CODE_PREFIX, UNIVERSE_SYMBOL
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

    def stocks(self, day) -> dict:
        """某天的**股票**票池（承 B）—— ★ **过滤归本层，不归下游**。

        「什么是标的」是**数据语义**，因此判定规则只在本模块实现**一处**；
        下游（研究 / 回测 / 因子）**不得**自己判断代码前缀或标的类型 ——
        否则就是「两处都能改 = 两份真相 = 迟早对不上」。

        规则（写死，承 P2：不猜）：
          ① 起点 = `symbols(day)` —— 该天库里有记录的**所有键**（含板块与保留代码）；
          ② 排除**板块代码**（`PLATE_CODE_PREFIX` 前缀）—— 板块是一揽子股票，不是标的；
          ③ 排除**保留约定代码**（`UNIVERSE_SYMBOL`，即 `UNIVERSE`）——
             它是"整市场一份"数据的占位，**本身不是标的**（承 keys.py）；
          ④ 排除**非股票**（不在最近一次 `snapshot` 的 symbol 集合里）——
             全市场快照（`snapshot`）只覆盖股票；实测自选池里的 ETF / 指数全部落在此列。

        ⚠️ ④ 用**最近一次快照**判定：快照目前只有少数几天，历史日只能用最近的快照
        近似（ETF 名单变化慢，可接受）。返回里的 `snapshot_day` **显形**用了哪天的
        快照（承 P5：近似必须显形，不许 paper over）。

        返回（JSON 可序列化，承 D）：
            {"day", "snapshot_day", "snapshot_is_after_day", "stocks",
             "n_excluded_plate", "n_excluded_reserved", "n_excluded_non_stock"}

        ⚠️ `snapshot_is_after_day`：所查日期**早于**判定用的快照日 ⇒ 该判定**含未来
        信息**（拿后来的股票名录去筛更早的日期）。这是**显形**，不是掩饰 ——
        下游若要严格无前视，应自己检查此标记（承 P5：近似必须显形）。
        """
        keys = self.symbols(day)
        plates = [s for s in keys if s.startswith(PLATE_CODE_PREFIX)]
        reserved = [s for s in keys if s == UNIVERSE_SYMBOL]
        rest = [s for s in keys
                if not s.startswith(PLATE_CODE_PREFIX) and s != UNIVERSE_SYMBOL]

        snapshot_day = self._axis.latest_day(UNIVERSE_SYMBOL, "snapshot")
        if snapshot_day is None:
            raise ValueError(
                "库里没有任何 snapshot —— 无法判定「哪些是股票」。"
                "先跑 pipeline 的 snapshot 步骤（承 P1：缺就报，不猜）"
            )
        universe = self._snapshot_symbols(snapshot_day)
        stocks = [s for s in rest if s in universe]
        return {
            "day": day,
            "snapshot_day": snapshot_day,
            "snapshot_is_after_day": snapshot_day > day,
            "stocks": stocks,
            "n_excluded_plate": len(plates),
            "n_excluded_reserved": len(reserved),
            "n_excluded_non_stock": len(rest) - len(stocks),
        }

    def _snapshot_symbols(self, day) -> set[str]:
        """某天快照里的股票代码集合（承 P2：结构不符 → 报错，不猜）。"""
        payload = self.value(day, UNIVERSE_SYMBOL, "snapshot")
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError(
                f"{day} 的 snapshot 结构异常（无 rows 列表）—— 承 P2：不猜结构"
            )
        return {str(r["symbol"]) for r in rows
                if isinstance(r, dict) and r.get("symbol")}

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
              days: list[str] | None = None, *,
              adjust: str | None = None) -> dict:
        """多标的 × 多日 × 单指标 → **按日分组**的面板（承 B）。

        ★ 与 `align_panel` 的分工（两者都做「多标的 × 多日」，但**语义相反**）：
          - `align_panel`：**按 bar 时间戳对齐**、默认**交集** → **时序**语义，
            适合「几个品种画在同一张图上」；
          - `panel`（本方法）：**按交易日分组、组内可缺、不做任何对齐** → **截面**语义，
            适合「某一天横跨所有标的比较」。

        `adjust`：复权口径 —— `None`（**原样 raw**）/ `"hfq"` / `"qfq"`，
        **必须显式**（承 spec §4：不设默认口径）。换算走 `engine.adjust`（**唯一实现**）。

        返回（JSON 可序列化，承 D）：
            {"item", "days", "symbols", "adjust", "contaminated_days",
             "n_adjust_events", "values"}
            `values[symbol][day]` = 值：
              · 逐 bar 数据项（`kline`）→ **展开成 bar**（本方法只接受**每日一根**；
                多根 → 报错并指向 `align_panel`，承 P2：不猜）；
              · 其它项 → 标量。

        ⚠️ `n_adjust_events[symbol]` = 本次复权**用到几个除权事件**（承 P5：显形）。
        **为 0 = 复权是空操作**（该标的库里没有 `adjust_factor` 记录）。
        ⚠️ **不能因此报错** —— `adjust_factor` 只记"有事件的日子"，无法区分
        "真没除权"与"我们没拉"（见 `engine/adjust.py` §边界）⇒ 只能**显形**，
        下游看到 0 应自行确认数据是否已备齐（`pipeline.py --adjust-factors`）。
        """
        values: dict[str, dict[str, Any]] = {}
        contaminated: dict[str, list[str]] = {}
        n_events: dict[str, int] = {}
        seen: set[str] = set()

        for s in symbols:
            rows = self._axis.series(s, item, days=days)
            if adjust is not None:
                rows, bad, count = _adjust_payload_rows(
                    rows, self._axis.series(s, "adjust_factor"),
                    mode=adjust, symbol=s, item=item)
                if bad:
                    contaminated[s] = bad
                n_events[s] = count
            per_day: dict[str, Any] = {}
            for d, rec in rows:
                per_day[d] = _panel_point(_unwrap(rec), item, s, d)
                seen.add(d)
            values[s] = per_day

        return {
            "item": item,
            "days": sorted(seen),
            "symbols": list(symbols),
            "adjust": adjust,
            "contaminated_days": contaminated,
            "n_adjust_events": n_events,
            "values": values,
        }

    def align_panel(self, symbols: list[str], *, item: str = "kline",
                    days: list[str] | None = None,
                    min_bars: int = DEFAULT_MIN_BARS,
                    adjust: str | None = None) -> dict:
        """★ B：多品种**按时间戳对齐** → 面板（对外聚合查询）。

        `adjust`：复权口径 —— `None`（**原样 raw**）/ `"hfq"` / `"qfq"`。
        **必须显式**（承 spec §4：不设默认口径；`None` 是"不复权"，不是"默认选一个"）。
        复权换算走 `engine.adjust`（**唯一实现**，承"数据只有一份"）——
        下游**不自建**复权。

        承"四问·对齐"：默认**交集**（只留所有品种都有真实报价的 bar，
        消除休市 ffill 造出的假 bar）；交集 < `min_bars` 才降级并集 + **仅 ffill**（禁 bfill）。

        只支持**逐 bar** 的数据项（`kline`）—— 其它项是"每天一条"、无 bar 级时间戳，
        按 bar 对齐无意义 → **明确报错**（承 P2：不猜结构）。
        返回 JSON 可序列化，含自证字段（策略 / 丢弃 / 填充数）。
        """
        series: dict[str, list[tuple[int, Any]]] = {}
        contaminated: dict[str, list[str]] = {}
        n_events: dict[str, int] = {}
        for s in symbols:
            bars: list[tuple[int, Any]] = []
            for _day, payload in self._axis.series(s, item, days=days):
                bars.extend(_bars_with_timestamp(payload, s, item))
            if adjust is not None:
                # ⚠️ 因子取**全量**（`days=None`）—— hfq 要 ≤t 的**全部**事件，
                #    按窗口过滤会漏掉窗口之前的分红/拆股 → 复权从根上错。
                bars, bad, count = _adjust_bars(
                    bars, self._axis.series(s, "adjust_factor"), mode=adjust, symbol=s)
                if bad:
                    contaminated[s] = bad
                n_events[s] = count
            series[s] = bars
        panel = align_by_intersection(series, min_bars=min_bars)
        return {
            "index": list(panel.index),
            "values": {s: list(col) for s, col in panel.values.items()},
            "strategy": panel.strategy,
            "adjust": adjust,
            # 收益被"未知形式"（派现的加法项）污染的**交易日** —— 下游**显形**处理，
            # 不许当 0、不许 ffill（承 K4/P6）。
            "contaminated_days": contaminated,
            # 本次复权用到几个除权事件 —— **0 = 空操作**（库里没该标的的因子），
            # 承 P5：显形，不静默（不能报错，因为无法区分"没除权"与"没拉"）。
            "n_adjust_events": n_events,
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


def _day_multipliers(days: list[str], factor_rows,
                     *, mode: str) -> tuple[dict[str, float], list[str], int]:
    """逐日复权乘子 + "不可信日" + **本次用到的除权事件数**。

    换算的**唯一实现**是 `engine.adjust.adjust_series` —— 这里只用 1.0 当"哑值"，
    为的是拿**逐日乘子**与"不可信日"，换算逻辑不在此重复。

    ★ 第三个返回值（事件数）是**显形**用的（承 P5）：
    该标的在库里**没有 `adjust_factor` 记录**时，事件数为 **0** ⇒ 复权是**空操作**。
    ⚠️ 但**不能因此报错**：`adjust_factor` 只记"有事件的日子"，
    无法区分"真没除权"与"我们没拉"（见 `engine/adjust.py` §边界）——
    故只能**显形**，让下游自己判断。
    """
    if mode not in ADJUST_MODES:
        raise ValueError(f"复权口径必须显式且合法：{mode!r}（可选 {ADJUST_MODES}）")
    ordered = sorted(set(days))
    # `axis.series` 给的是 `[(日, 记录)]` —— 只取**记录**（事件日以记录内的 `ex_div_date`
    # 为准，不依赖外层键日，避免两处日期口径打架）。
    rows = [rec for _day, rec in factor_rows]
    result = adjust_series(ordered, [1.0] * len(ordered), rows, mode=mode)
    return (dict(zip(result.days, result.multipliers)),
            sorted(result.unusable_days),
            result.n_split_events + result.n_dividend_events)


def _scale_bar(bar: Any, multiplier: float) -> dict:
    """按乘子缩放 bar 的 OHLC。

    ⚠️ **只调 OHLC，不碰 volume**：拆股会等比放大成交量，而"该不该调、怎么调"
    spec 没定 ⇒ **不猜**（承 P2）。将来要用成交量，先把这个口径定死。
    """
    out = dict(bar)
    for field in ("open", "high", "low", "close"):
        value = bar.get(field)
        if value is not None:
            out[field] = value * multiplier
    return out


def _adjust_bars(bars: list[tuple[int, Any]], factor_rows,
                 *, mode: str, symbol: str
                 ) -> tuple[list[tuple[int, Any]], list[str], int]:
    """逐 bar 复权 —— 用**该 bar 所属交易日**的乘子（同一天所有 bar 共用一个乘子）。"""
    days = sorted({_bar_day(b, symbol) for _ts, b in bars})
    multiplier_of, unusable, n_events = _day_multipliers(days, factor_rows, mode=mode)
    out = [(ts, _scale_bar(b, multiplier_of[_bar_day(b, symbol)])) for ts, b in bars]
    return out, unusable, n_events


def _adjust_payload_rows(rows: list[tuple[str, Any]], factor_rows,
                         *, mode: str, symbol: str, item: str
                         ) -> tuple[list[tuple[str, Any]], list[str], int]:
    """按日的 payload 逐日复权（供 `panel`）—— 换算仍只走 `_day_multipliers` 一处。"""
    multiplier_of, unusable, n_events = _day_multipliers(
        [d for d, _payload in rows], factor_rows, mode=mode)
    out: list[tuple[str, Any]] = []
    for day, payload in rows:
        if not isinstance(payload, dict) or "bars" not in payload:
            raise ValueError(
                f"{symbol}.{item} 在 {day} 不是逐 bar 数据（无 bars 字段）—— "
                f"复权只支持 K线（承 P2：不猜结构）"
            )
        new_payload = dict(payload)
        new_payload["bars"] = [_scale_bar(b, multiplier_of[_bar_day(b, symbol)])
                               for b in payload["bars"]]
        out.append((day, new_payload))
    return out, unusable, n_events


def _panel_point(value: Any, item: str, symbol: str, day: str) -> Any:
    """面板里的单点值：逐 bar 项**展开成 bar**；其余原样返回。

    本方法是**日频截面**接口 —— 每日多于一根 bar 时**报错**并指向 `align_panel`
    （承 P2：不猜；不悄悄取第一根）。
    """
    if isinstance(value, dict) and "bars" in value:
        bars = value["bars"]
        if len(bars) != 1:
            raise ValueError(
                f"{symbol}.{item} 在 {day} 有 {len(bars)} 根 bar —— `panel` 是"
                f"**日频截面**接口，只接受每日一根；分钟线请用 `align_panel`（承 P2）"
            )
        return bars[0]
    return value


def _bar_day(bar: Any, symbol: str) -> str:
    """bar 的**交易日**（`date` 字段）。缺/非法 → 报错（承 P2：不猜结构）。"""
    day = str((bar or {}).get("date") or "")[:10]
    if len(day) != 10:
        raise ValueError(
            f"{symbol} 的 bar 缺合法 date（拿到 {day!r}），无法按交易日复权（承 P2）"
        )
    return day


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
