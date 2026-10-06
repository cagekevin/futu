"""富途 OpenD 源适配器（M2.1）—— K线 / 全市场快照 / 自选 / 市场快照。

承 F1（源名不外泄）：本模块之外不得出现本模块的源名。
承 F4（失败必报）：OpenD 未启动 / 返回码非成功 → 抛 `FetchError`，**不静默返回空**。
换源只动 M2（承 F3）。

出口统一为 `Rows`：{ rows: [行 dict], extra: {…} }。**不落盘、不计算**（承 M2 约束）。

额度与通道（实测 + 端方 README 确认，见 docs/plan §8.2）：
- **历史 K线**：吃「历史K线 300 标的 / 7 天滚动释放」额度 —— 用于**少量**标的（个股深挖）。
- **全市场快照**（`get_stock_screen` V2）：**不吃历史额度**（服务器端回字段值），
  这是拿全市场 chg / 行业 / 市值的唯一通道 —— RPS 的原料。
- **市场快照**（`get_market_snapshot`）：**不吃历史额度**，按代码取少量标的的价/市值。

⚠️ 实测签名（futu-api 10.11，与旧文档不同，踩过）：
- `request_history_kline(...)` 返回 **三元组** `(ret, data, page_req_key)`；
- `get_market_snapshot(list)` 返回 **二元组** `(ret, data)`；
- `get_stock_screen(req)` 返回 `(ret, (last_page, total, items))`。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ...fetch_types import FetchError, Request
from trading_time import to_unix_seconds, trading_day

_HOST = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
_PORT = int(os.getenv("FUTU_OPEND_PORT", "11111"))

# K线通用字段（归一化后，承 F2）。含 time_key —— 分钟线去重/排序靠它。
KLINE_FIELDS = ("time_key", "date", "open", "high", "low", "close", "volume")
# 全市场快照通用字段。
SNAPSHOT_FIELDS = ("symbol", "name", "industry", "price", "market_cap",
                   "chg20", "chg50", "chg120", "chg250")

# OpenD 原生 ktype 全集（实测 futu.KLType + 逐个 ret=0 确认）：
#   1/3/5/10/15/30/60/120/180/240 分钟 + 日/周/月/季/年。
# ⚠️ 与 REST 的**编号**不同名（REST 是另一套数字，见 rest_source.py）—— 两码事。
NATIVE_KTYPES = (
    "K_1M", "K_3M", "K_5M", "K_10M", "K_15M", "K_30M", "K_60M",
    "K_120M", "K_180M", "K_240M",
    "K_DAY", "K_WEEK", "K_MON", "K_QUARTER", "K_YEAR",
)
# 哪些是「日内」（去重/排序键用 time_key）。
_INTRADAY_KTYPES = frozenset(
    {"K_1M", "K_3M", "K_5M", "K_10M", "K_15M", "K_30M", "K_60M",
     "K_120M", "K_180M", "K_240M"}
)

# V2 服务器端筛：默认涨幅周期（RPS 按这四档排名）。
DEFAULT_DAYS = (20, 50, 120, 250)
# V2 每页上限（接口硬限）。
PAGE_MAX = 200


@dataclass
class Rows:
    """通用行式出口（K线 / 快照 / 自选都用它）。"""

    symbol: str
    fetched_at: datetime
    rows: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def _ret_ok() -> int:
    from futu import RET_OK

    return RET_OK


def _context():
    """打开 OpenD 连接；未安装 / 未启动 → 报（承 F4）。"""
    try:
        from futu import OpenQuoteContext  # 懒导入
    except ImportError as e:
        raise FetchError(
            "富途源不可用：未安装 futu-api（pip install futu-api）。"
            "换源只动 fetch/（承 F3）"
        ) from e
    try:
        return OpenQuoteContext(host=_HOST, port=_PORT)
    except Exception as e:  # noqa: BLE001
        raise FetchError(
            f"富途源不可用：无法连接 OpenD {_HOST}:{_PORT}（{e}）。"
            f"请启动 OpenD 或换源（承 F4）"
        ) from e


def _code(symbol: str, *, index: bool = False) -> str:
    """内部纯代码 → 富途代码。**只在本模块内使用**（承 F1）。"""
    s = symbol.upper()
    return f"US..{s}" if index else f"US.{s}"


def _default_start(as_of: str | None, lookback_days: int = 400) -> str:
    """K线起点：`as_of` 往前推 `lookback_days` 个自然日（覆盖约 1 年交易日）。

    ⚠️ 必须给起点 —— 富途默认从**最早**返回，不给 start 拿不到最新数据（实测）。
    """
    import datetime as _dt

    end = _dt.date.fromisoformat(as_of) if as_of else _dt.date.today()
    return (end - _dt.timedelta(days=lookback_days)).isoformat()


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _is_nan(v) -> bool:
    """NaN / NaT / None → True（用于把 pandas 的 NaN 转成 JSON 的 null）。"""
    if v is None:
        return True
    try:
        return v != v  # NaN 唯一性质
    except Exception:  # noqa: BLE001
        return False


def _num_or_str(v):
    """数值字段尽量转 float；否则原样（字符串/日期）。"""
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return v


def _strip(code: str) -> str:
    """富途代码 → 纯代码（去市场前缀）。承 X4：键里无源名/前缀。

    `US.AAPL` → `AAPL`；`US..SPX`（指数双点）→ `SPX`。
    """
    if "." in code:
        return code.split(".", 1)[1].lstrip(".")
    return code


class FutuSource:
    """富途 OpenD 源。`name` 仅内部使用（承 F1）。"""

    name = "futu"

    def __init__(self, timeout: int = 60) -> None:
        self.timeout = timeout

    # ── K线（吃历史额度）────────────────────────────────────────────────
    def fetch_kline(self, req: Request, *, ktype: str = "K_DAY",
                    count: int = 1000, index: bool = False,
                    start: str | None = None, all_pages: bool = True) -> Rows:
        """取历史 K线（从 `start` 往后到最新）。

        ⚠️⚠️ **吃「历史 K线额度」**（实测：拉一只 → `used` +1）。本系统**默认不用它**
        —— `fetch_api.py::kline` 默认走 REST；只有显式 `source="futu-opend"` 才到这里。
        **同一标的不同周期只占 1 个额度**（30 天窗口内）。

        ⚠️ **实测行为（与直觉相反，已踩）**：`request_history_kline` **默认从最早
        开始返回**，且传 `end` 不改变起点 —— 想拿**最新**数据必须传 `start`。
        不传 `start` 只会拿到上市最初的 `max_count` 根（实测 AAPL 拿到 2025-10，
        而不是今天）。

        - `start`：起点（YYYY-MM-DD）。None → 用 `as_of` 往前推 `lookback_days`。
        - `count`：单页上限；`all_pages=True` 时翻页拿全区间（`page_req_key`）。
        ⚠️ 返回值是**三元组** `(ret, data, page_req_key)`（旧文档写二元组）。
        """
        ctx = _context()
        try:
            # 起点：没给就按 as_of 往前推（默认一年），保证能覆盖到最新。
            start = start or _default_start(req.as_of, lookback_days=400)
            intraday = ktype in _INTRADAY_KTYPES
            # 去重键：分钟线用 time_key（同一天多根），日线及以上用 date。
            bars: dict[Any, dict] = {}
            page_key = None
            pages = 0
            while True:
                try:
                    ret, data, page_key = ctx.request_history_kline(
                        _code(req.symbol, index=index), start=start, ktype=ktype,
                        autype=None,  # 不复权：期权行权价是绝对价（实测口径）
                        max_count=int(count), page_req_key=page_key,
                    )
                except (ValueError, TypeError) as e:
                    raise FetchError(
                        f"取数失败（K线 {req.symbol}）：OpenD 未就绪或返回值异常（{e}）"
                    ) from e
                if ret != _ret_ok():
                    raise FetchError(
                        f"取数失败（K线 {req.symbol}）：返回码非成功 — {data}"
                    )
                if data is None or getattr(data, "empty", True):
                    break
                for _, r in data.iterrows():
                    row = _kline_row(r)
                    bars[row["time_key"] if intraday else row["date"]] = row
                pages += 1
                if not all_pages or not page_key:
                    break
                if pages > 500:  # 防翻页死循环
                    break
            rows = [bars[k] for k in sorted(bars)]
            if not rows:
                raise FetchError(f"取数失败（K线 {req.symbol}）：空结果")
            # P4：请求日之前的都要（K线自带 date，天然自证，不猜）
            if req.as_of is not None:
                rows = [r for r in rows if r["date"] <= req.as_of]
                if not rows:
                    raise FetchError(
                        f"取数校验失败（K线 {req.symbol}）：无 {req.as_of} 之前的数据"
                    )
            rows.sort(key=lambda r: r["time_key"] if intraday else r["date"])
            return Rows(symbol=req.symbol.upper(), fetched_at=datetime.now(),
                        rows=rows,
                        extra={"ktype": ktype, "autype": None, "pages": pages,
                               "intraday": intraday, "start": start})
        finally:
            _close(ctx)

    # ── 全市场快照（V2，不吃历史额度）──────────────────────────────────
    def fetch_snapshot(self, req: Request, *, market: str = "US",
                       days: tuple[int, ...] = DEFAULT_DAYS,
                       max_pages: int | None = None) -> Rows:
        """全市场快照 —— 取每只的 价 / 市值 / 行业 / N 日涨幅。

        走 V2 服务器端筛（`get_stock_screen`，协议 3252）：**服务器端回字段值，
        不吃历史额度** —— 这是全市场 RPS 的原料（承 README 第 599 行）。
        ⚠️ `page_from` 是**偏移量**，不是页号（按页号翻页会大面积重复，已踩）。
        """
        ctx = _context()
        try:
            keys = _screen_keys(days)
            rows: list[dict] = []
            offset = 0
            total = 0
            page_idx = 0
            while True:
                req_obj = _build_screen_request(market, days, offset, PAGE_MAX)
                try:
                    ret, data = ctx.get_stock_screen(req_obj)
                except Exception as e:  # noqa: BLE001 — 含 protobuf 版本坑，必须显形
                    raise FetchError(
                        f"取数失败（全市场快照）：{type(e).__name__}: {e}。"
                        f"若为 FieldDescriptor.label 报错 → protobuf 需 < 5（见 requirements.txt）"
                    ) from e
                if ret != _ret_ok():
                    raise FetchError(f"取数失败（全市场快照）：返回码非成功 — {data}")
                last_page, total, items = data
                rows.extend(_parse_screen_item(it, keys) for it in (items or []))
                page_idx += 1
                if last_page or not items or (max_pages and page_idx >= max_pages):
                    break
                offset += PAGE_MAX  # 偏移量推进
            # 去重（按代码），排序稳定
            dedup = {r["symbol"]: r for r in rows if r.get("symbol")}
            out = [dedup[k] for k in sorted(dedup)]
            if not out:
                raise FetchError("取数失败（全市场快照）：空结果（不静默返回空，承 F4）")
            return Rows(symbol="UNIVERSE", fetched_at=datetime.now(), rows=out,
                        extra={"market": market, "days": list(days),
                               "total_reported": total, "pages": page_idx})
        finally:
            _close(ctx)

    # ── 市场快照（少量代码，不吃历史额度）──────────────────────────────
    def fetch_market_snapshot(self, req: Request, *,
                              codes: list[str] | None = None) -> Rows:
        """按代码取少量标的的市场快照（价/市值/涨幅等）。返回**二元组**。"""
        ctx = _context()
        try:
            code_list = codes or [_code(req.symbol)]
            try:
                ret, data = ctx.get_market_snapshot(code_list)
            except (ValueError, TypeError) as e:
                raise FetchError(
                    f"取数失败（市场快照）：OpenD 未就绪或返回值异常（{e}）"
                ) from e
            if ret != _ret_ok():
                raise FetchError(f"取数失败（市场快照）：返回码非成功 — {data}")
            if data is None or data.empty:
                raise FetchError("取数失败（市场快照）：空结果")
            rows = [{
                "symbol": _strip(str(r["code"])),
                "name": r.get("name"),
                "price": _f(r.get("last_price")),
                "market_cap": _f(r.get("total_market_val")),
                "change_rate": _f(r.get("change_rate")),
            } for _, r in data.iterrows()]
            return Rows(symbol=req.symbol.upper(), fetched_at=datetime.now(),
                        rows=rows, extra={"kind": "market_snapshot"})
        finally:
            _close(ctx)

    # ── 复权因子（每除权日一条；不吃历史额度）────────────────────────────
    def fetch_rehab(self, req: Request) -> Rows:
        """取某标的的**复权因子**（每个除权除息日一行）。

        `get_rehab(code)` —— 富途复权因子接口（**不吃历史 K线额度**；限频 60/30s）。
        每行含各类公司行动 + 复权因子：
          `forward_adj_factorA/B`（前复权）、`backward_adj_factorA/B`（后复权）、
          `split_ratio` / `per_cash_div` / `bonus_*` 等。

        ⇒ 与 K线（存不复权）配套：下游要前/后复权价时**自算**（承"底层 + 可自算"）。
        """
        ctx = _context()
        try:
            try:
                ret, data = ctx.get_rehab(_code(req.symbol))
            except (ValueError, TypeError) as e:
                raise FetchError(
                    f"取数失败（复权因子 {req.symbol}）：OpenD 未就绪或返回值异常（{e}）"
                ) from e
            if ret != _ret_ok():
                raise FetchError(f"取数失败（复权因子 {req.symbol}）：返回码非成功 — {data}")
            if data is None or getattr(data, "empty", True):
                # 无复权因子是**合法**的（无分红的标的）—— 返回空，不报错。
                return Rows(symbol=req.symbol.upper(), fetched_at=datetime.now(),
                            rows=[], extra={"kind": "rehab", "count": 0})
            rows = []
            for _, r in data.iterrows():
                row = {"ex_div_date": str(r.get("ex_div_date"))[:10]}
                # 原样保留全部字段（承 P2：不丢），NaN → None（JSON 无 NaN）
                for col in data.columns:
                    if col == "ex_div_date":
                        continue
                    v = r.get(col)
                    row[col] = None if _is_nan(v) else _num_or_str(v)
                rows.append(row)
            rows.sort(key=lambda x: x["ex_div_date"])
            return Rows(symbol=req.symbol.upper(), fetched_at=datetime.now(),
                        rows=rows, extra={"kind": "rehab", "count": len(rows)})
        finally:
            _close(ctx)

    # ── 自选清单 ─────────────────────────────────────────────────────────
    def fetch_watchlist(self, req: Request) -> Rows:
        ctx = _context()
        try:
            try:
                ret, groups = ctx.get_user_security_group()
            except (ValueError, TypeError) as e:
                raise FetchError(f"取数失败（自选清单）：OpenD 未就绪（{e}）") from e
            if ret != _ret_ok():
                raise FetchError(f"取数失败（自选清单）：{groups}")
            rows = []
            for g in groups.to_dict("records"):
                gname = g["group_name"]
                r2, lst = ctx.get_user_security(group_name=gname)
                if r2 != _ret_ok():
                    raise FetchError(f"取数失败（自选组 {gname}）：{lst}")
                for s in lst.to_dict("records"):
                    code = s.get("code")
                    if not code:
                        continue
                    rows.append({"symbol": _strip(code), "group": gname,
                                 "name": s.get("name")})
            return Rows(symbol="WATCHLIST", fetched_at=datetime.now(),
                        rows=rows, extra={"groups": len(groups)})
        finally:
            _close(ctx)


# ── V2 服务器端筛的辅助（不硬编码魔法数字，用 SDK 枚举）─────────────────

# 返回记录里的值字段，按顺序取第一个（实测）。
_VALUE_KEYS = ("sval", "dval", "ival", "lval", "bval")
# 端方对「无行业」返回占位符，不是真行业。
_EMPTY_INDUSTRY = {"-", "—", "--"}


def _screen_keys(days: tuple[int, ...]) -> dict:
    """`(type, name, days) -> 我们的字段名`。用 SDK 枚举取整，SDK 升级自动跟上。"""
    from futu.quote.stock_screen_const import (
        BasicProperty, CumulativeProperty, SimpleProperty,
    )

    keys = {
        ("basic", int(BasicProperty.CODE), 0): "symbol",
        ("basic", int(BasicProperty.NAME), 0): "name",
        ("basic", int(BasicProperty.INDUSTRY), 0): "industry",
        ("simple", int(SimpleProperty.PRICE), 0): "price",
        ("simple", int(SimpleProperty.MARKET_CAP), 0): "market_cap",
    }
    for n in days:
        keys[("cumulative", int(CumulativeProperty.PRICE_CHANGE_PCT), int(n))] = f"chg{int(n)}"
    return keys


def _build_screen_request(market: str, days: tuple[int, ...],
                          page_from: int, page_count: int):
    """构造一次 V2 请求（按代码升序，保证分页稳定）。"""
    from futu import StockScreenRequest
    from futu.quote.stock_screen_const import (
        BasicProperty, CumulativeProperty, ScrMarket, ScrSortDir,
        SimpleField, SimpleProperty,
    )

    req = StockScreenRequest()
    req.page_from = int(page_from)
    req.page_count = int(page_count)
    req.add_simple_field(field=int(SimpleField.MARKET),
                         values=[int(getattr(ScrMarket, market))])
    req.add_retrieve_basic(name=int(BasicProperty.CODE))
    req.add_retrieve_basic(name=int(BasicProperty.NAME))
    req.add_retrieve_basic(name=int(BasicProperty.INDUSTRY))
    req.add_retrieve_simple(name=int(SimpleProperty.PRICE))
    req.add_retrieve_simple(name=int(SimpleProperty.MARKET_CAP))
    for n in days:
        req.add_retrieve_cumulative(
            name=int(CumulativeProperty.PRICE_CHANGE_PCT), days=int(n))
    # 默认按代码升序 —— 分页必须稳定，否则翻页会漏/重（已踩）。
    req.set_sort(direction=int(ScrSortDir.ASC), property_type="basic",
                 property_params={"name": int(BasicProperty.CODE)})
    return req


def _value(rec: dict):
    for k in _VALUE_KEYS:
        if k in rec:
            return rec[k]
    return None


def _parse_screen_item(item: dict, keys: dict) -> dict:
    """一条 V2 返回记录 → `{字段名: 值}`。按 `(type, name, days)` 匹配，不靠位置。"""
    out: dict[str, Any] = {}
    for rec in item.get("results") or []:
        prop = rec.get("property") or {}
        key = (rec.get("type"), int(prop.get("name") or 0), int(prop.get("days") or 0))
        name = keys.get(key)
        if name:
            out[name] = _value(rec)
    if "symbol" in out:
        out["symbol"] = _strip(str(out["symbol"]))
    ind = (out.get("industry") or "").strip()
    if ind in _EMPTY_INDUSTRY:
        out["industry"] = None  # 占位符当没有，免得聚出一个叫 "-" 的行业
    for k in ("price", "market_cap", "chg20", "chg50", "chg120", "chg250"):
        if k in out:
            out[k] = _f(out[k])
    return out


def _kline_row(r) -> dict:
    """一行 K线 → 归一化 dict。

    `time_key`：**统一为 Unix 秒**（承"时区统一"）。OpenD 原生给的是
    **美东本地 naive 字符串**（`"2026-10-06 15:59:00"`）—— 经
    `trading_time.to_unix_seconds`（naive 当美东）收敛成秒，与 REST 通道一致。
    `date` = 该时刻的**美东交易日**（`trading_day`）。

    ⚠️ 分钟线同一天有多根，只按 date 去重会把一天压成一根。
    """
    tk = r["time_key"]
    return {
        "time_key": to_unix_seconds(tk),
        "date": trading_day(tk),
        "open": _f(r["open"]), "high": _f(r["high"]),
        "low": _f(r["low"]), "close": _f(r["close"]),
        "volume": _f(r["volume"]),
    }


def _close(ctx) -> None:
    try:
        ctx.close()
    except Exception:  # noqa: BLE001
        pass


def make() -> FutuSource:
    return FutuSource()


from ... import source_registry  # noqa: E402

source_registry.register("futu-opend", make)
