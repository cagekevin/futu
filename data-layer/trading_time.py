"""时区统一 —— 所有时间戳统一成 **Unix 秒**，交易日一律按**美东（ET）**判定。

承"数据层四问 · 时区"（唯一判据 = 正确性）：

> **统一成什么**：`int` 类型的 **Unix 秒**（UTC 纪元秒）—— 这是本系统**唯一**的时间表示。
> **交易日怎么定**：任何时刻 → 用 **America/New_York** 的日历日判定（承 L1：不是本机日期）。

为什么必须统一（否则是 P1 级错）—— 实测**两通道 `time_key` 写法不同**：
- **REST**（端方 `kline.mjs`）：`time_key` 是**毫秒纪元数**（端方 `depth.mjs:56` 用 `÷86400_000` 把差值换算成天 → 单位是毫秒）；
- **OpenD**（`futu` SDK）：`time_key` 是**美东本地 naive 字符串**（`"2026-10-06 15:59:00"`）。
两套混进同一张表 → 排序 / 去重 / 多品种对齐**全错**。本模块把它们收敛到同一种表示。

★ **单位必须显式，禁止靠数值大小猜**（承 D7 / P1）：
    过去按 `abs(n) >= 1e11` 猜"毫秒"，结果 `秒÷1000` 这种值（如 `1759780`）不越阈值，
    被当成"秒" → 日期变 **1970**，**静默全错**。现改为：**数字时间戳必须显式给 `unit`**
    （`"s"` / `"ms"`），自描述输入（`datetime` / ISO 字符串）不用给；一律做**量级校验**。

**单位口径**：本层统一到**秒** ⇒ **不含逐笔（tick）**（tick 需亚秒精度，另论）。

用法：
    from trading_time import to_unix_seconds, trading_day, et_datetime
    to_unix_seconds("2026-10-06 15:59:00")        # ISO/字符串：自带单位
    to_unix_seconds(1759780740000, unit="ms")     # 数字：必须显式单位
    trading_day(1759780740, unit="s")             # → "2026-10-06"（美东日）
"""
from __future__ import annotations

import re
from datetime import date, datetime

from config import ET

__all__ = ["TimeError", "to_unix_seconds", "et_datetime", "trading_day"]

# 允许的单位（数字时间戳必须显式声明，不猜 —— 承 D7）。
_VALID_UNITS = ("s", "ms")
# 量级校验窗口 [1980-01-01, 2100-01-01)：越界 → 报错（承 P6，不静默修）。
# 常见越界 = 单位错：秒÷1000 → 1970；微秒/纳秒 → 远未来。
_MIN_UNIX_SECONDS = 315_532_800     # 1980-01-01T00:00:00Z
_MAX_UNIX_SECONDS = 4_102_444_800   # 2100-01-01T00:00:00Z

_NUMERIC_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")


class TimeError(ValueError):
    """时间戳无法按规矩归一化 —— 显形报错（承 P1 / P6，不猜）。"""


def _numeric_to_seconds(n: float, unit: str) -> int:
    """数字 → Unix 秒。单位**显式**：`"ms"` ÷1000，`"s"` 原样（不猜）。"""
    if unit not in _VALID_UNITS:
        raise TimeError(f"unit 必须是 's' 或 'ms'（收到 {unit!r}）")
    if unit == "ms":
        return int(round(n / 1000.0))
    return int(n)


def _datetime_to_seconds(dt: datetime) -> int:
    """`datetime` → Unix 秒。**naive 当美东**（承 L1）。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return int(dt.timestamp())


def _parse_iso(s: str) -> datetime:
    """ISO 字符串 → `datetime`（naive 保留 naive，交由上层当美东）。"""
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    try:
        return datetime.combine(date.fromisoformat(s), datetime.min.time())
    except ValueError as e:
        raise TimeError(
            f"时间戳字符串无法解析：{s!r}（要求 ISO，如 '2026-10-06' 或 "
            f"'2026-10-06 15:59:00'）"
        ) from e


def _check_range(sec: int, raw) -> int:
    """量级校验：落在 [1980, 2100) 之外 → 报错（承 P6，不静默修）。"""
    if not (_MIN_UNIX_SECONDS <= sec < _MAX_UNIX_SECONDS):
        raise TimeError(
            f"时间戳越界：{raw!r} → {sec} 秒（不在 1980..2100 内）。"
            f"常见原因：单位错（秒 / 毫秒 / 微秒）—— 承 D7，不静默修"
        )
    return sec


def to_unix_seconds(value, *, unit: str | None = None) -> int:
    """任意时间戳 → **Unix 秒**（`int`）。

    - **数字**（`int`/`float`/纯数字字符串）：**必须显式给 `unit`**（`"s"` / `"ms"`）。
      **不猜**（承 D7 / P1）：靠数值大小猜单位会把 `秒÷1000` 静默当成秒 → 1970。
    - **自描述**（`datetime` / ISO 字符串）：自带单位，`unit` 必须为 `None`。
    - **一律做量级校验**：结果落在 `[1980, 2100)` 之外 → `TimeError`（承 P6）。
    """
    if isinstance(value, bool):  # bool 是 int 子类，必须先挡（否则 True→1）
        raise TimeError(f"时间戳不接受布尔值：{value!r}")
    if isinstance(value, (int, float)):
        if unit is None:
            raise TimeError(
                "数字时间戳必须显式给 unit='s' 或 'ms'（不猜，承 D7/P1）"
            )
        return _check_range(_numeric_to_seconds(float(value), unit), value)
    if isinstance(value, datetime):
        if unit is not None:
            raise TimeError("datetime 自带单位，不要再传 unit")
        return _check_range(_datetime_to_seconds(value), value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise TimeError("时间戳为空字符串")
        if _NUMERIC_RE.match(s):
            if unit is None:
                raise TimeError(
                    "数字时间戳必须显式给 unit='s' 或 'ms'（不猜，承 D7/P1）"
                )
            return _check_range(_numeric_to_seconds(float(s), unit), value)
        if unit is not None:
            raise TimeError("ISO 字符串自带单位，不要再传 unit")
        return _check_range(_datetime_to_seconds(_parse_iso(s)), value)
    raise TimeError(
        f"不支持的时间戳类型：{type(value)!r}（值 {value!r}）—— "
        f"接受 int/float/str/datetime（承 P1）"
    )


def et_datetime(value, *, unit: str | None = None) -> datetime:
    """任意时间戳 → **美东** aware `datetime`（承 L1）。"""
    return datetime.fromtimestamp(to_unix_seconds(value, unit=unit), tz=ET)


def trading_day(value, *, unit: str | None = None) -> str:
    """任意时间戳 → **美东交易日** `YYYY-MM-DD`（承 L1：不是本机日期）。"""
    return et_datetime(value, unit=unit).strftime("%Y-%m-%d")
