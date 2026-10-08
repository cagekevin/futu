"""取数对外入口 —— 统一取数接口。

上游**只**通过这里取数，永远不 import 具体源（承 F1 / X4：源名不漏）。
换源只动 `fetch/`：改环境变量 `TRADING_DESK_SOURCE`，或在 `fetch/sources/` 加新源。

出口结构见 `fetch_types.ChainResult`：`{ 通用字段…, extra: {…源特有…} }`（承 F2）。
"""
from __future__ import annotations

from .sources import cboe_options_source as _cboe  # noqa: F401 — 触发自注册
from .sources.futu import opend_source as _futu_opend  # noqa: F401 — 自注册（OpenD）
from .sources.futu import rest_source as _futu_rest  # noqa: F401 — 自注册（REST）
from .fetch_types import ChainResult, FetchError, Request
from . import source_registry

__all__ = [
    "chain", "spot", "kline", "snapshot", "watchlist", "trading_days", "rehab",
    "plate_list", "plate_members",
    "Request", "ChainResult", "FetchError", "available_sources",
]


def available_sources() -> list[str]:
    """可用的源名（仅供诊断；正常业务不需要知道源）。"""
    return source_registry.keys()


def chain(symbol: str, *, as_of: str | None = None,
          source: str | None = None) -> ChainResult:
    """取一条归一化的期权链。

    `symbol`：纯代码（如 `SPX`）。`as_of`：请求的数据日（用于 P4 校验）。
    `source`：**默认不传**（走配置）。传它只用于测试/诊断，业务代码不该传。
    """
    req = Request(symbol=symbol, as_of=as_of, kind="chain")
    src = source_registry.get_source(source or source_registry.default_source_name())
    return src.fetch_chain(req)


def spot(symbol: str, *, source: str | None = None) -> tuple[float, object]:
    """取一个标的的 spot（轻量，用于 VIX 等参照物）。"""
    req = Request(symbol=symbol, kind="spot")
    src = source_registry.get_source(source or source_registry.default_source_name())
    fetch_spot = getattr(src, "fetch_spot", None)
    if fetch_spot is None:
        raise FetchError(f"源 {src.name!r} 不支持 spot 取数")
    return fetch_spot(req)


def kline(symbol: str, *, as_of: str | None = None, ktype: str = "K_DAY",
          count: int = 1000, index: bool = False, years: int = 3,
          months: int | None = None, source: str | None = None):
    """取归一化的 K线（OHLCV）。

    **路由规则**（见 docs/reference/futu/README.md §2）：
    - **默认一律走 REST**（无额度）—— 包括分钟线（REST 有 1/3/5/10/15/30/60/120/180 分）。
    - **只有显式传 `source="futu"`（OpenD）才走 OpenD** —— 它吃「历史 K线额度」，
      默认**绝不隐式消耗**（承 P1：资源消耗必须显式）。
    - REST **没有 4H（`K_240M`）**：若默认走 REST 遇到不支持的周期 → **明确报错**，
      让调用方显式决定是否用 OpenD（而不是悄悄吃额度）。
    """
    req = Request(symbol=symbol, as_of=as_of, kind="kline")
    # 默认 REST；只有显式 source="futu-opend" 才走 OpenD。
    if source is None:
        from .sources.futu.rest_source import KTYPE as _REST_KTYPE
        if ktype in _REST_KTYPE:
            name = "futu-rest"
        elif ktype in ("K_240M",):
            raise FetchError(
                f"周期 {ktype}（4H）REST 不支持 —— 若确实要用 OpenD（会消耗历史K线额度），"
                f"请显式传 source='futu-opend'（承 P1：资源消耗必须显式）"
            )
        else:
            name = "futu-rest"  # 交给 REST 源处理，未知周期由它报错
    else:
        name = source
    src = source_registry.get_source(name)
    fn = getattr(src, "fetch_kline", None)
    if fn is None:
        raise FetchError(f"源 {src.name!r} 不支持 K线取数")
    # 两个源的参数集不同（REST 用 years/months，OpenD 用 count/index），按源派发。
    if name == "futu-opend":
        return fn(req, ktype=ktype, count=count, index=index)
    return fn(req, ktype=ktype, years=years, months=months)


def snapshot(*, market: str = "US", source: str | None = None):
    """取全市场快照（供 RPS / 行业用，需全样本）。"""
    req = Request(symbol="UNIVERSE", kind="snapshot")
    src = source_registry.get_source(source or "futu-opend")
    fn = getattr(src, "fetch_snapshot", None)
    if fn is None:
        raise FetchError(f"源 {src.name!r} 不支持快照取数")
    return fn(req, market=market)


def watchlist(*, source: str | None = None):
    """取自选清单。"""
    req = Request(symbol="WATCHLIST", kind="watchlist")
    src = source_registry.get_source(source or "futu-opend")
    fn = getattr(src, "fetch_watchlist", None)
    if fn is None:
        raise FetchError(f"源 {src.name!r} 不支持自选取数")
    return fn(req)


def plate_list(*, market: str = "US", plate_type: str = "CONCEPT",
               source: str | None = None):
    """取某市场的**板块名册**（`CONCEPT` 概念 / `INDUSTRY` 行业）。

    板块 = 一揽子股票；概念与行业走**同一条**通路（吸收自参照项目 plates.py）。
    """
    req = Request(symbol="PLATES", kind="plate_list")
    src = source_registry.get_source(source or "futu-opend")
    fn = getattr(src, "fetch_plate_list", None)
    if fn is None:
        raise FetchError(f"源 {src.name!r} 不支持板块名册取数")
    return fn(req, market=market, plate_type=plate_type)


def plate_members(plate_code: str, *, source: str | None = None):
    """取一个**板块的成分股**（板块纯代码，如 `LIST23925`）。"""
    req = Request(symbol=plate_code, kind="plate_members")
    src = source_registry.get_source(source or "futu-opend")
    fn = getattr(src, "fetch_plate_members", None)
    if fn is None:
        raise FetchError(f"源 {src.name!r} 不支持板块成分股取数")
    return fn(req)


def trading_days(market: str, start: str, end: str,
                 *, source: str | None = None):
    """取某市场的**开市日清单**（交易日历，全局数据）。

    默认走 REST（无额度）。返回**只含开市日**（接口原生，不补非交易日）。
    ⚠️ 单次上限约 4000 条，超长历史需分段调用。
    """
    src = source_registry.get_source(source or "futu-rest")
    fn = getattr(src, "fetch_trading_days", None)
    if fn is None:
        raise FetchError(f"源 {src.name!r} 不支持交易日历取数")
    return fn(market, start, end)


def rehab(symbol: str, *, source: str | None = None):
    """取某标的的**复权因子**（每除权日一条）。

    走 OpenD（`get_rehab`，**不吃历史 K线额度**）。
    与 K线（存不复权）配套 —— 下游要复权价时自算。
    """
    req = Request(symbol=symbol, kind="rehab")
    src = source_registry.get_source(source or "futu-opend")
    fn = getattr(src, "fetch_rehab", None)
    if fn is None:
        raise FetchError(f"源 {src.name!r} 不支持复权因子取数")
    return fn(req)
