"""把 engine/indicators/tdx.py 的 list 版通达信公式适配成 futu_algo 的 `Strategy`。

适配之后就能直接复用 futu_algo 的这几样（不用自己写）：
  · `check_lookahead()`  —— **截断重算**，检测公式有没有偷看未来
  · `backtest`           —— 回测（撮合 / 滑点 / 绩效指标）
  · `screener` 的 confirm —— 服务器端选股后的本地确认

公式的形状（见 `engine/indicators/cd.py`）：

    def calc_cd(bars: list[dict]) -> dict[str, list]     # 输入 OHLCV，输出各中间量与信号

用法：

    from futu_algo.strategy.lookahead import check_lookahead
    from tdx_strategy import TdxFormula
    from indicators.cd import calc_cd

    formula = TdxFormula(calc_cd, signal="DXDX", warmup=60, name="cd")
    report = check_lookahead(formula, bars)      # bars 是 futu_algo 的 bar frame
    print(report.summary())

`check_lookahead` 的原理：因果的指标在第 t 根的值，用 `bars[0..t]` 算与用全量算**必须相同**；
只要有一个检查点对不上，就说明第 t 根用到了 t 之后的数据。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from futu_algo.strategy.base import Strategy

BarsFn = Callable[[list[dict[str, Any]]], dict[str, list]]


def to_records(bars: pd.DataFrame) -> list[dict[str, Any]]:
    """futu_algo 的 bar frame → `py/tdx.py` 公式要的 `list[dict]`。

    `date` 给成 `YYYYMMDD` 整数（和 REST 落盘一致），并额外带上原始时间戳 `time`。
    """
    open_, high = bars["open"].to_numpy(), bars["high"].to_numpy()
    low, close = bars["low"].to_numpy(), bars["close"].to_numpy()
    volume = bars["volume"].to_numpy()
    out: list[dict[str, Any]] = []
    for i, ts in enumerate(bars.index):
        out.append({
            "date": int(ts.strftime("%Y%m%d")),
            "time": ts,
            "open": float(open_[i]),
            "high": float(high[i]),
            "low": float(low[i]),
            "close": float(close[i]),
            "volume": float(volume[i]),
        })
    return out


class TdxFormula(Strategy):
    """把一个 `fn(bars) -> {名字: 序列}` 的通达信公式包装成 `Strategy`。

    - `calc`：公式函数，输入 `list[dict]`，输出 `{列名: 序列}`
    - `signal`：输出里哪一列是信号（取值必须是 1 / 0 / NaN）
    - `warmup`：前多少根不可信（`check_lookahead` 会从这之后开始检查）
    """

    name = "tdx"
    title = ""

    def __init__(
        self,
        calc: BarsFn,
        *,
        signal: str,
        warmup: int = 0,
        name: str | None = None,
        title: str = "",
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(params)
        self._calc = calc
        self._signal = signal
        self._warmup = int(warmup)
        if name:
            self.name = name
        self.title = title or self.name

    def warmup_bars(self) -> int:
        return self._warmup

    def indicators(self, bars: pd.DataFrame) -> pd.DataFrame:
        result = self._calc(to_records(bars))
        cols = {
            key: value
            for key, value in result.items()
            if isinstance(value, list) and len(value) == len(bars)
        }
        if self._signal not in cols:
            raise ValueError(
                f"公式没有输出信号列 {self._signal!r}；可用列：{sorted(result)}"
            )
        return pd.DataFrame(cols, index=bars.index)

    def signals(self, bars: pd.DataFrame, ind: pd.DataFrame) -> pd.Series:
        series = ind[self._signal].astype("float64")
        return series.where(series.notna())


def point_in_time(
    calc: BarsFn,
    bars: pd.DataFrame,
    key: str,
    window: int,
    *,
    min_bars: int = 2,
) -> pd.Series:
    """逐根回放一个 list 版公式：第 i 根的值只用 `bars[i-window+1 : i+1]` 算。

    **给会重绘的公式用**（如 `tdx.ZIG`）—— 直接整段跑等于用了未来数据，回测会虚高。
    对因果公式，本函数的结果应当与直接跑**完全一致**（可用来反向验证公式是否因果）。

    内部复用 `futu_algo.indicators.tdx.point_in_time`，这里只做 list ↔ DataFrame 的适配。
    """
    from futu_algo.indicators.tdx import point_in_time as _pit

    def fn(window_bars: pd.DataFrame) -> pd.Series:
        values = calc(to_records(window_bars)).get(key) or []
        return pd.Series(values, index=window_bars.index)

    return _pit(fn, bars, window, min_bars=min_bars)
