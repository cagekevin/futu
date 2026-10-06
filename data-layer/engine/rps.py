"""M3.2 指标 —— 相对强度（RS）三种口径 + RPS。**纯函数，零 IO**（承 G1）。

三种口径**别混**（承 vendor/rs.py 的教训）：

| 口径 | 定义 | 能不能跨股比 |
|---|---|---|
| RS 线 | 个股 ÷ 基准 | ❌ 随股价水平漂移 |
| Mansfield RS | 把 RS 线归一化到零轴 | ✅ |
| RPS | 涨幅的全样本百分位排名 | ✅ |

参数是**权威口径**：
- RS 线「新高」= 52 周新高（≈250 交易日）
- Mansfield RS = 周线 + 52 周 SMA
- RPS 门槛经典 90；本项目默认 80
"""
from __future__ import annotations

import datetime as _dt
import math

NAN = float("nan")

WEEKS_52_IN_DAILY = 250
MANSFIELD_WEEKS = 52

RPS_MIN = 80.0
RPS_MIN_CLASSIC = 90.0


def rs_line(close: list[float], bench: list[float]) -> list[float]:
    """RS 线 = 个股 ÷ 基准。两序列必须逐根对齐（对齐是调用方的事）。"""
    return [c / b if b else NAN for c, b in zip(close, bench)]


def to_weekly(dates: list[int], values: list[float]) -> tuple[list[int], list[float]]:
    """日线 → 周线：每周取最后一根。`dates` 为 YYYYMMDD 整数。"""
    out_d: list[int] = []
    out_v: list[float] = []
    last_key: tuple[int, int] | None = None
    for d, v in zip(dates, values):
        s = str(d)
        key = _dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])).isocalendar()[:2]
        if key == last_key and out_d:
            out_d[-1], out_v[-1] = d, v
        else:
            out_d.append(d)
            out_v.append(v)
            last_key = key
    return out_d, out_v


def mansfield(weekly_rs: list[float], n: int = MANSFIELD_WEEKS) -> list[float]:
    """Mansfield RS = `(RS / SMA(RS, n) − 1) × 100`。输入必须是**周线** RS 线。"""
    out = [NAN] * len(weekly_rs)
    if n <= 0:
        return out
    for i in range(n - 1, len(weekly_rs)):
        w = weekly_rs[i - n + 1 : i + 1]
        if all(math.isfinite(v) for v in w):
            out[i] = (weekly_rs[i] / (sum(w) / n) - 1) * 100
    return out


def change(close: list[float], n: int) -> float | None:
    """N 根区间涨幅。历史不足 N 根 → None（不用不足的窗口硬算）。"""
    if n <= 0 or len(close) <= n:
        return None
    a, b = close[-n - 1], close[-1]
    if not (math.isfinite(a) and math.isfinite(b)) or a == 0:
        return None
    return b / a - 1


def rps(returns: dict[str, float]) -> dict[str, float]:
    """RPS = `(1 − 名次 / 总数) × 100`。并列取平均名次。"""
    items = sorted(returns.items(), key=lambda kv: kv[1], reverse=True)
    total = len(items)
    out: dict[str, float] = {}
    i = 0
    while i < total:
        j = i
        while j + 1 < total and items[j + 1][1] == items[i][1]:
            j += 1
        rank = (i + j) / 2 + 1
        value = (1 - rank / total) * 100
        for k in range(i, j + 1):
            out[items[k][0]] = value
        i = j + 1
    return out


def off_high(series: list[float], lookback: int | None = None) -> float | None:
    """当前值距回看窗口内最高的差距（%）。0 = 正在创新高。

    ⚠️ RS 线新高的权威定义是 **52 周**（`lookback=WEEKS_52_IN_DAILY`）。
    """
    vals = [v for v in series if math.isfinite(v)]
    if lookback:
        vals = vals[-lookback:]
    if not vals or vals[-1] == 0:
        return None
    return (vals[-1] / max(vals) - 1) * 100


__all__ = [
    "rs_line", "to_weekly", "mansfield", "change", "rps", "off_high",
    "RPS_MIN", "RPS_MIN_CLASSIC", "WEEKS_52_IN_DAILY", "MANSFIELD_WEEKS",
]
