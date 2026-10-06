"""futu_algo 数据层的薄封装 —— 补上它未定义的市场，再导出 store 相关类。

`futu_algo` 的 `MARKETS` 只定义了 **HK / US**，但自选清单里还有
**SH / SZ / CC / FX / BD**。`parse_symbol()` 遇到未知市场会直接抛 `ConfigError`，
导致 `ParquetStore` 连路径都算不出来。

所以任何要用 ParquetStore 读写本地缓存的代码，都从这里导入：

    from fetch.opend.futu_data import Coverage, ParquetStore, SeriesKey

**导入本模块即完成市场补丁**（`setdefault`，幂等，不会覆盖 futu_algo 已有的 HK/US）。
"""

from __future__ import annotations

from datetime import time as dtime
from pathlib import Path

from futu_algo.data.store import Coverage, ParquetStore, SeriesKey
from futu_algo.market import instrument as _inst

# 各市场的**命名时区** —— 别用固定偏移：美股的 time_zone 字段有 -5/-4 两种
# （夏令时切换），固定偏移会把一半的 bar 标错。
_EXTRA_MARKETS: dict[str, tuple[str, str, tuple, int]] = {
    "SH": ("CNY", "Asia/Shanghai", ((dtime(9, 30), dtime(11, 30)), (dtime(13, 0), dtime(15, 0))), 242),
    "SZ": ("CNY", "Asia/Shanghai", ((dtime(9, 30), dtime(11, 30)), (dtime(13, 0), dtime(15, 0))), 242),
    "CC": ("USD", "America/New_York", ((dtime(0, 0), dtime(23, 59)),), 365),  # 7×24
    "FX": ("USD", "America/New_York", ((dtime(0, 0), dtime(23, 59)),), 260),  # 7×24
    "BD": ("USD", "America/New_York", ((dtime(8, 0), dtime(17, 0)),), 252),
}

for _code, (_cur, _tz, _sess, _days) in _EXTRA_MARKETS.items():
    _inst.MARKETS.setdefault(
        _code,
        _inst.MarketSpec(code=_code, currency=_cur, tz=_tz, sessions=_sess, trading_days_per_year=_days),
    )

__all__ = ["Coverage", "ParquetStore", "SeriesKey", "DEFAULT_ROOT", "make_manager"]


# ---------------------------------------------------------------- 便捷入口
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data"


def make_manager(
    root: str | Path | None = None,
    *,
    adjust: str = "qfq",
    host: str = "127.0.0.1",
    port: int = 11111,
    offline: bool = False,
) -> "DataManager":
    """构造 `DataManager`（用它就够，别自己拼 ParquetStore + FutuSource + QuoteGateway）。

    - `offline=True`：只读本地缓存，完全不连 OpenD（读不到就抛 DataError）
    - 否则：本地没有的数据会**自动向 OpenD 拉**（消耗该标的的历史K线额度，同标的重复免费）

    多周期不用自己聚合：`manager.load_bars(symbol, Timeframe.parse("2H"), start, end)`
    会自动拉 60M 原生数据、再按**交易时段**聚合成 2H（不跨午休）。

    用完 `close()`，或 `with make_manager() as m:`。
    """
    from futu_algo.data.manager import DataManager
    from futu_algo.data.source import FutuSource
    from futu_algo.futu_gateway import QuoteGateway

    store = ParquetStore(root or DEFAULT_ROOT)
    if offline:
        return DataManager(store, None, adjust=adjust, offline=True)
    gateway = QuoteGateway(host=host, port=port)
    return DataManager(
        store,
        source_factory=lambda: FutuSource(gateway, owns_gateway=True),
        adjust=adjust,
    )
