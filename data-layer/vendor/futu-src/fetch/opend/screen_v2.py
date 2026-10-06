"""V2 选股通道（协议 3252）—— 全项目**唯一**的服务器端筛选出口。

`universe.py`（全市场快照）和 `engine/screener/screener.py`（条件筛）都走这里。
**不再有第二条通道** —— V1（`get_stock_filter`）已弃用。

## 为什么只用 V2

| | V1 `get_stock_filter` | V2 `get_stock_screen` |
|---|---|---|
| 返回 | **只回代码** | **直接回字段值**（价 / 市值 / 涨幅 / 行业） |
| 后果 | 必须本地再拉快照补字段 → 美股 OTC 让整批失败 → 拆批重试 → 撞限频（**实测 90 秒**） | **零 enrich** → 秒级 |
| 因子 | 几十个 | **244+**（行情 / 财务 / 技术指标 / K 线形态 / 期权 / 经纪商） |

## 两个必须记住的坑

**1. 单位两头不一样**

    filter    传**百分数**：涨 5% 传 5.0
    retrieve  返回**小数**：dval = 0.24992 就是 +24.992%

拿 NVDA / AAPL / MSFT / AMZN / TSM / META / AVGO 与本地日K自算的
`close[-1]/close[-n-1]-1` 逐只比对，倍率全是 1.00。

**2. `page_from` 是「偏移量」不是「页号」**

按页号翻页会大面积重复 —— 实测 `page_from=0/1/2` 三页只出 202 条唯一记录，
`0/200/400` 才不重叠。

**依赖**：futu-api 需要 **protobuf < 5**。5.x 删了 `FieldDescriptor.label`，
SDK 内部还在用它，接口会直接报
`'google._upb._message.FieldDescriptor' object has no attribute 'label'`。

## V2 给不了什么（所以那几样必须本地算）

    顾比线（自定义公式）
    CD 底背离（V2 有 `MACD_BOTTOM_DIVERGE`，但那是富途的背离算法，口径不同）
    行业集体行为的统计（V2 给行业字段，统计得自己算）
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MARKET = "US"
DEFAULT_DAYS = (20, 50, 120, 250)
PAGE_MAX = 200  # 接口上限，每页最多 200

# 返回记录里的值字段，按出现顺序取第一个
_VALUE_KEYS = ("sval", "dval", "ival", "lval", "bval")

# 富途对「没有行业分类」的标的会返回占位符，不是真行业
_EMPTY_INDUSTRY = {"-", "—", "--"}

__all__ = [
    "DEFAULT_MARKET", "DEFAULT_DAYS", "PAGE_MAX",
    "ScreenSpec", "DEFAULT_SPEC", "filters_from_spec", "add_filters",
    "field_keys", "parse_item", "build_request", "run_screen",
]


# ---------------------------------------------------------------- 过滤条件
@dataclass(frozen=True)
class ScreenSpec:
    """全市场快照的默认过滤 —— **声明式**，要调改这一处。

    砍掉仙股 / 微盘 / 没成交量的，页数从 48 降到 15，拉取时间跟着降。
    这些票本来也不该进 RPS 榜。
    """

    min_price: float | None = 1.0  # 现价 ≥ $1，剔仙股
    min_mcap: float | None = 1e9  # 市值 ≥ 10 亿美元，剔微盘
    min_avg_turnover: float | None = 1e7  # 近 N 日平均成交额 ≥ 1000 万美元（热度）
    avg_turnover_days: int = 20
    min_listed_days: int | None = 120  # 上市满 120 天，剔新股


DEFAULT_SPEC = ScreenSpec()


def filters_from_spec(spec):
    """`ScreenSpec` → V2 filter 列表（和 `PRESETS` 里写的是同一种形状）。"""
    out = []
    for field, prop in (
        ("min_price", "PRICE"),
        ("min_mcap", "MARKET_CAP"),
        ("min_listed_days", "LISTED_DAYS"),
    ):
        value = getattr(spec, field, None)
        if value is not None:
            out.append({"type": "simple_property", "name": prop, "lower": float(value)})
    if spec.min_avg_turnover is not None:
        out.append({
            "type": "cumulative_property",
            "name": "AVG_TURNOVER",
            "days": int(spec.avg_turnover_days),
            "lower": float(spec.min_avg_turnover),
        })
    return out


def add_filters(req, filters):
    """把 filter 列表挂到请求上。字段名一律走 SDK 枚举，**别硬编码数字**。

    支持的 `type`（够用就行，要加照抄）：
        simple_property / cumulative_property / financial_property /
        indicator_pattern / indicator_positional / kline_shape / featured_property
    """
    from futu.quote.stock_screen_const import (
        CumulativeProperty,
        FeaturedProperty,
        FinancialProperty,
        Indicator,
        KlineShapeProperty,
        KlineShapeType,
        OptionHVPeriod,
        OptionProperty,
        Pattern,
        Period,
        Position,
        SimpleProperty,
        Term,
    )

    def _enum(cls, value):
        if value is None or isinstance(value, int):
            return value
        return int(getattr(cls, value)) if hasattr(cls, value) else value

    for f in filters or []:
        kind = f.get("type")
        if kind == "simple_property":
            req.add_simple_property(
                name=_enum(SimpleProperty, f["name"]),
                lower=f.get("lower"), upper=f.get("upper"),
                lower_included=f.get("lower_included", True),
                upper_included=f.get("upper_included", True),
            )
        elif kind == "cumulative_property":
            req.add_cumulative_property(
                name=_enum(CumulativeProperty, f["name"]),
                days=int(f.get("days", 1)),
                lower=f.get("lower"), upper=f.get("upper"),
                lower_included=f.get("lower_included", True),
                upper_included=f.get("upper_included", True),
            )
        elif kind == "financial_property":
            req.add_financial_property(
                name=_enum(FinancialProperty, f["name"]),
                term=_enum(Term, f.get("term")),
                lower=f.get("lower"), upper=f.get("upper"),
            )
        elif kind == "indicator_pattern":
            req.add_indicator_pattern(
                name=_enum(Pattern, f["name"]),
                period_type=_enum(Period, f.get("period", "DAY")),
                continuous_period=f.get("continuous_period"),
            )
        elif kind == "indicator_positional":
            req.add_indicator_positional(
                first_indicator_name=_enum(Indicator, f["first_indicator_name"]),
                period_type=_enum(Period, f.get("period", "DAY")),
                position=_enum(Position, f["position"]),
                second_indicator=_enum(Indicator, f.get("second_indicator")),
                second_value=f.get("second_value"),
                continuous_period=f.get("continuous_period"),
            )
        elif kind == "kline_shape":
            value_set = [_enum(KlineShapeType, v) for v in (f.get("value_set") or [])]
            req.add_kline_shape(
                name=_enum(KlineShapeProperty, f["name"]),
                period=_enum(Period, f.get("period", "DAY")),
                value_set=[int(v) for v in value_set] if value_set else None,
            )
        elif kind == "featured_property":
            req.add_featured_property(
                name=_enum(FeaturedProperty, f["name"]),
                intervals=f.get("intervals"),
                value_set=f.get("value_set"),
                period=f.get("period"),
                range_period=f.get("range_period"),
            )
        elif kind == "option":
            req.add_option(
                name=_enum(OptionProperty, f["name"]),
                intervals=f.get("intervals"),
                param=f.get("param"),
                period=_enum(OptionHVPeriod, f.get("period")),
            )
        else:
            raise ValueError(f"未知 filter type：{kind}")


# ---------------------------------------------------------------- 字段映射
def field_keys(days, with_industry=True):
    """请求与解析共用的字段表：`(type, name, days) -> 我们自己的字段名`。

    不硬编码 1101/3102 这类魔法数字 —— 一律用 SDK 的枚举取整值，
    SDK 升级换号了这里自动跟上。
    """
    from futu.quote.stock_screen_const import (
        BasicProperty,
        CumulativeProperty,
        SimpleProperty,
    )

    keys = {
        ("basic", int(BasicProperty.CODE), 0): "code",
        ("basic", int(BasicProperty.NAME), 0): "name",
        ("simple", int(SimpleProperty.PRICE), 0): "price",
        ("simple", int(SimpleProperty.MARKET_CAP), 0): "mcap",
    }
    if with_industry:
        keys[("basic", int(BasicProperty.INDUSTRY), 0)] = "industry"
    for n in days:
        keys[("cumulative", int(CumulativeProperty.PRICE_CHANGE_PCT), n)] = f"chg{int(n)}"
    return keys


def _value(rec):
    for k in _VALUE_KEYS:
        if k in rec:
            return rec[k]
    return None


def parse_item(item, keys):
    """把一条返回记录摊平成 `{字段名: 值}`。按 `(type, name, days)` 匹配，不靠位置。"""
    out = {}
    for rec in item.get("results") or []:
        prop = rec.get("property") or {}
        key = (rec.get("type"), int(prop.get("name") or 0), int(prop.get("days") or 0))
        name = keys.get(key)
        if name:
            out[name] = _value(rec)
    industry = (out.get("industry") or "").strip()
    if industry in _EMPTY_INDUSTRY:
        out["industry"] = None  # 占位符当没有，免得聚出一个叫 "-" 的行业
    return out


# ---------------------------------------------------------------- 请求
def _apply_sort(req, sort=None):
    """显式排序 —— **默认按代码升序**：分页必须稳定，否则翻页会漏掉 / 重复股票。

    `sort` = `{"property_type": "simple"|"basic"|"cumulative", "name": "MARKET_CAP",
    "direction": "DESC"}`。

    ⚠️ 条件筛要「**取前 N 名**」时排序**必须放在请求里** ——
    服务器端排序决定了截断取哪 N 只，本地再排只是重排同一批。
    """
    from futu.quote.stock_screen_const import (
        BasicProperty,
        CumulativeProperty,
        ScrSortDir,
        SimpleProperty,
    )

    if not sort:
        sort = {"property_type": "basic", "name": "CODE", "direction": "ASC"}
    ptype = sort.get("property_type", "basic")
    enum = {"basic": BasicProperty, "simple": SimpleProperty, "cumulative": CumulativeProperty}.get(ptype)
    name = sort.get("name")
    if isinstance(name, str) and enum is not None and hasattr(enum, name):
        name = int(getattr(enum, name))
    req.set_sort(
        direction=int(getattr(ScrSortDir, sort.get("direction", "ASC"))),
        property_type=ptype,
        property_params={"name": name},
    )


def build_request(
    market=DEFAULT_MARKET,
    days=DEFAULT_DAYS,
    page_from=0,
    page_count=PAGE_MAX,
    with_industry=True,
    filters=None,
    sort=None,
):
    """构造一次 V2 请求。

    `page_from` 是**偏移量**（不是页号）。`filters` 见 `add_filters`，`sort` 见 `_apply_sort`。
    """
    from futu import StockScreenRequest
    from futu.quote.stock_screen_const import (
        BasicProperty,
        CumulativeProperty,
        ScrMarket,
        SimpleField,
        SimpleProperty,
    )

    req = StockScreenRequest()
    req.page_from = int(page_from)
    req.page_count = int(page_count)
    req.add_simple_field(
        field=int(SimpleField.MARKET),
        values=[int(getattr(ScrMarket, market))],
    )
    req.add_retrieve_basic(name=int(BasicProperty.CODE))
    req.add_retrieve_basic(name=int(BasicProperty.NAME))
    if with_industry:
        req.add_retrieve_basic(name=int(BasicProperty.INDUSTRY))
    req.add_retrieve_simple(name=int(SimpleProperty.PRICE))
    req.add_retrieve_simple(name=int(SimpleProperty.MARKET_CAP))
    for n in days:
        req.add_retrieve_cumulative(
            name=int(CumulativeProperty.PRICE_CHANGE_PCT), days=int(n)
        )
    add_filters(req, filters)
    _apply_sort(req, sort)
    return req


def run_screen(
    market=DEFAULT_MARKET,
    days=DEFAULT_DAYS,
    *,
    filters=None,
    sort=None,
    with_industry=True,
    page_count=PAGE_MAX,
    max_pages=None,
    max_rows=None,
    gateway=None,
    verbose=True,
):
    """分页跑完一次筛选，返回 `(rows, total, pages)`。

    `max_rows`：凑够这么多就停（条件筛的 `limit` 用这个）；
    `max_pages`：最多翻几页（调试用）。

    走 `QuoteGateway` —— 它按 API family 限频并在被限频时自动重试。
    用 `stock_filter` 这一族（文档值 10 次/30 秒）。
    """
    from futu_algo.futu_gateway import QuoteGateway

    keys = field_keys(days, with_industry)
    own_gateway = gateway is None
    gw = gateway or QuoteGateway()
    rows: list[dict] = []
    pages = 0
    total = 0
    try:
        while True:
            offset = pages * page_count
            req = build_request(market, days, offset, page_count, with_industry, filters, sort)
            _, data = gw.call(
                "stock_filter",
                "get_stock_screen",
                lambda ctx, r=req: ctx.get_stock_screen(r),
            )
            last_page, total, items = data
            rows.extend(parse_item(it, keys) for it in (items or []))
            pages += 1
            if verbose:
                print(f"  偏移 {offset} → 累计 {len(rows)}/{total}", flush=True)
            if (
                last_page
                or not items
                or (max_pages and pages >= max_pages)
                or (max_rows and len(rows) >= max_rows)
            ):
                break
    finally:
        if own_gateway:
            gw.close()

    dedup = {r["code"]: r for r in rows if r.get("code")}
    if verbose and total and len(dedup) < total and not max_rows and not max_pages:
        print(f"  注意：市场 {total} 只，实际拿到 {len(dedup)} 只（缺 {total - len(dedup)}）")
    out = [dedup[k] for k in sorted(dedup)]
    # `max_rows` 不只是「翻到够就停」—— 一页 200 条拉回来也得截到 limit，
    # 否则调用方说 limit=8 却拿到 200 条
    if max_rows:
        out = out[: int(max_rows)]
    return out, total, pages
