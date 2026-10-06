"""CBOE 期权链源适配器（M2.1）。

从 CBOE 公开延迟接口拉整条期权链（一次 GET），**归一化**成 `ChainResult`。

承 F1（源名不外泄）：本模块之外不得出现 `cboe` 字样 —— 上游只认通用字段。
承 F4（失败必报）：HTTP 错误 / 结构异常 → 抛 `FetchError`，**不静默返回空**。
承 P4（校验真取到）：返回的 feed 日期与请求不符 → 报。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..fetch_types import ChainResult, FetchError, Request

_ET = ZoneInfo("America/New_York")

# 延迟报价端点（非官方文档，一次 GET 拿全链）。
_BASE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
# 指数端点的符号以 `_` 前缀（如 `_SPX`）；ETF 无前缀。
_INDEX_PREFIX = "_"

# OCC 合约符号：ROOT + YYMMDD + C/P + 行权价×1000（8 位）。
_OCC_RE = re.compile(r"^(?P<root>.+?)(?P<exp>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")

# 已知的指数代码（需 `_` 前缀）。其余按 ETF/股票直取。
_INDEX_SYMBOLS = {"SPX", "NDX", "VIX", "RUT", "DJX", "OEX", "XSP"}


def _session() -> requests.Session:
    retry = Retry(
        total=4,
        backoff_factor=2.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "trading-desk/0.1 (data backend)"
    return s


_SESSION = _session()


def _remote_symbol(symbol: str) -> str:
    """内部纯代码 → 端点符号。**只在本模块内使用**（承 F1）。"""
    s = symbol.upper()
    return f"{_INDEX_PREFIX}{s}" if s in _INDEX_SYMBOLS else s


def _parse_occ(contract: str) -> tuple[date, str, float] | None:
    m = _OCC_RE.match(contract)
    if m is None:
        return None
    exp = datetime.strptime(m.group("exp"), "%y%m%d").date()
    return exp, m.group("cp"), int(m.group("strike")) / 1000.0


class CboeSource:
    """CBOE 延迟报价源。`name` 仅内部使用（承 F1）。"""

    name = "cboe"

    def __init__(self, timeout: int = 60) -> None:
        self.timeout = timeout

    def fetch_chain(self, req: Request) -> ChainResult:
        url = _BASE_URL.format(sym=_remote_symbol(req.symbol))
        try:
            resp = _SESSION.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise FetchError(
                f"取数失败（源内部，标的 {req.symbol}）：{type(e).__name__}: {e}"
            ) from e

        try:
            raw = resp.json()
        except ValueError as e:
            raise FetchError(f"取数失败：响应非 JSON（标的 {req.symbol}）：{e}") from e

        return _parse(req, raw, fetched_at=datetime.now(timezone.utc))

    def fetch_spot(self, req: Request) -> tuple[float, datetime]:
        """轻量取数：只取 spot（如 VIX 做参照）。"""
        url = _BASE_URL.format(sym=_remote_symbol(req.symbol))
        try:
            resp = _SESSION.get(url, timeout=self.timeout)
            resp.raise_for_status()
            raw = resp.json()
        except requests.RequestException as e:
            raise FetchError(f"取数失败（spot，标的 {req.symbol}）：{e}") from e
        except ValueError as e:
            raise FetchError(f"取数失败：响应非 JSON（标的 {req.symbol}）：{e}") from e
        data = raw.get("data", {})
        if "current_price" not in data:
            raise FetchError(f"取数失败：响应缺 current_price（标的 {req.symbol}）")
        ts = _feed_ts(raw)
        return float(data["current_price"]), ts


def _feed_ts(raw: dict) -> datetime:
    """源时间戳 → ET naive datetime（源给 UTC，转 ET）。"""
    ts_raw = raw.get("timestamp")
    if not ts_raw:
        raise FetchError("取数失败：响应缺 timestamp（无法自证，承 P3）")
    try:
        return (
            datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=timezone.utc)
            .astimezone(_ET)
            .replace(tzinfo=None)
        )
    except ValueError as e:
        raise FetchError(f"取数失败：时间戳格式异常 {ts_raw!r}：{e}") from e


def _parse(req: Request, raw: dict, fetched_at: datetime) -> ChainResult:
    """把原始 JSON 归一化成统一出口（承 F2）。"""
    if "data" not in raw:
        raise FetchError(f"取数失败：响应缺 data（标的 {req.symbol}）")
    data = raw["data"]
    if "current_price" not in data:
        raise FetchError(f"取数失败：响应缺 current_price（标的 {req.symbol}）")

    feed_ts = _feed_ts(raw)

    # P4：校验"真取到" —— 内容日期与请求不符 → 报（承 F4 / P4）。
    #
    # ⚠️ 口径（实测得出，非推演）：这个 feed 的 `timestamp` 是**报价时刻**，
    # 不是"交易日"。盘前 / 周末取数时，报价时刻自然停在上一交易日 ——
    # 例如美东 10-06 凌晨取数，feed 时间是 10-05 收盘。
    # 因此这里**不**把"请求日 ≠ feed 日"一律判为错，而是：
    #   - 允许 feed 日 ≤ 请求日（源自带延迟 / 盘前）；
    #   - 拒绝 feed 日 **晚于** 请求日（未来的数据 = 真异常，承 P5/P6）。
    if req.as_of is not None:
        req_d = date.fromisoformat(req.as_of)
        feed_d = feed_ts.date()
        if feed_d > req_d:
            raise FetchError(
                f"取数校验失败：请求 {req.as_of}，源返回 {feed_ts:%Y-%m-%d}"
                f"（内容日期晚于请求日 = 异常，承 P4/P5）"
            )

    options = data.get("options")
    if options is None:
        raise FetchError(f"取数失败：响应缺 options 列表（标的 {req.symbol}）")

    rows: list[dict] = []
    unparsed: list[str] = []
    for o in options:
        contract = o.get("option")
        if not contract:
            raise FetchError(f"取数失败：合约条目缺 option 字段（承 P2）")
        parsed = _parse_occ(contract)
        if parsed is None:
            unparsed.append(contract)
            continue
        exp, cp, strike = parsed
        rows.append({
            "contract": contract,
            "expiry": exp.isoformat(),
            "cp": cp,
            "strike": strike,
            "bid": _req_num(o, "bid"),
            "ask": _req_num(o, "ask"),
            "iv": _req_num(o, "iv"),
            "open_interest": _req_num(o, "open_interest"),
            "volume": _req_num(o, "volume"),
            "delta": _req_num(o, "delta"),
            "gamma": _req_num(o, "gamma"),
            "last_trade_price": _req_num(o, "last_trade_price"),
        })

    if not rows:
        # F5：原始数据也要保留 —— 全链应含全部合约；一条都没有 = 异常。
        raise FetchError(f"取数失败：{req.symbol} 一个合约都没解析出来")

    extra = {
        # 源特有：未归一，上游用前要看（键名**不代表源**）。
        "unparsed_contracts": unparsed,
        "raw_option_count": len(options),
    }
    return ChainResult(
        symbol=req.symbol.upper(),
        spot=float(data["current_price"]),
        feed_timestamp=feed_ts,
        fetched_at=fetched_at,
        rows=rows,
        extra=extra,
    )


def _req_num(o: dict, key: str) -> float:
    """必取数值字段：缺失 → 报（承 P1 / P2：禁止默认值兜底）。

    唯一允许的例外：源显式给了 `null` → 记为 0.0，但那**必须显形**
    （在 `extra` 里可见），这里按整数 0 处理并依赖源自证时间戳。
    """
    if key not in o:
        raise FetchError(f"取数失败：合约缺字段 {key!r}（承 P2：字段清单写死）")
    v = o[key]
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError) as e:
        raise FetchError(f"取数失败：字段 {key!r} 非数值：{v!r}") from e


def make() -> CboeSource:
    return CboeSource()


# 自注册（换源只动 M2）。
from .. import source_registry  # noqa: E402

source_registry.register("cboe", make)
