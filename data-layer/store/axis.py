"""时间轴 + 库接口 —— PLAN §3.3 里的 `Axis`。

只回答"有什么"，不判断好坏；缺就是缺，不"取最新"、不降级（承 P6 / K4）。

用法：
    from store import axis
    axis.days()                       # 有哪些交易日
    axis.symbols("2026-10-06")        # 某天有哪些标的
    axis.items("2026-10-06", "SPX")   # 某天某标的有哪些数据项
    axis.get("2026-10-06", "SPX", "net_gex")
    axis.series("SPX", "net_gex")     # 跨日序列

    # 全局项（无标的）—— 标地位传 None：
    axis.put("2026-10-06", None, "calendar", {...})
    axis.get("2026-10-06", None, "calendar")
    axis.global_items("2026-10-06")
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from config import DATA_DIR
from . import read as _read
from . import write as _write
from .keys import (
    GLOBAL_ITEMS, KNOWN_ITEMS, Key, StoreError, UnregisteredItem,
)
from .read import Missing

__all__ = [
    "days", "symbols", "items", "global_items", "get", "series", "latest_day",
    "put", "put_many", "exists",
    "Axis", "Missing", "StoreError", "UnregisteredItem", "KNOWN_ITEMS",
    "GLOBAL_ITEMS", "Key",
]


# ── 模块级接口（用默认 DATA_DIR）────────────────────────────────────────

def days(root: Path | None = None) -> list[str]:
    return _read.days(root or DATA_DIR)


def symbols(day, root: Path | None = None) -> list[str]:
    return _read.symbols(root or DATA_DIR, day)


def items(day, symbol: str, root: Path | None = None) -> list[str]:
    return _read.items(root or DATA_DIR, day, symbol)


def global_items(day, root: Path | None = None) -> list[str]:
    """某天有哪些**全局数据项**（无标的，如 `calendar`）。"""
    return _read.global_items(root or DATA_DIR, day)


def get(day, symbol: str | None, item: str, *, default: Any = Missing,
        root: Path | None = None) -> Any:
    return _read.get(root or DATA_DIR, day, symbol, item, default=default)


def series(symbol: str | None, item: str, *, days: list[str] | None = None,
           root: Path | None = None) -> list[tuple[str, Any]]:
    return _read.series(root or DATA_DIR, symbol, item, days=days)


def latest_day(symbol: str | None, item: str, *, root: Path | None = None) -> str | None:
    """某项数据最近有值的交易日；从没有过 → None（增量更新用）。"""
    return _read.latest_day(root or DATA_DIR, symbol, item)


def put(day, symbol: str | None, item: str, payload: object,
        root: Path | None = None) -> Path:
    return _write.put(root or DATA_DIR, day, symbol, item, payload)


def put_many(day, records: list[tuple[str | None, str, object]],
             root: Path | None = None) -> list[Path]:
    return _write.put_many(root or DATA_DIR, day, records)


def exists(day, symbol: str | None, item: str, root: Path | None = None) -> bool:
    return _read.exists(root or DATA_DIR, day, symbol, item)


# ── 面向对象的封装（可指定 root，便于测试）─────────────────────────────

class Axis:
    """库的句柄。默认用全局 DATA_DIR；给定 root 时可指向别处（测试用）。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else DATA_DIR

    def days(self) -> list[str]:
        return _read.days(self.root)

    def symbols(self, day) -> list[str]:
        return _read.symbols(self.root, day)

    def items(self, day, symbol: str) -> list[str]:
        return _read.items(self.root, day, symbol)

    def global_items(self, day) -> list[str]:
        return _read.global_items(self.root, day)

    def get(self, day, symbol: str | None, item: str, *,
            default: Any = Missing) -> Any:
        return _read.get(self.root, day, symbol, item, default=default)

    def series(self, symbol: str | None, item: str, *,
               days: list[str] | None = None) -> list[tuple[str, Any]]:
        return _read.series(self.root, symbol, item, days=days)

    def latest_day(self, symbol: str | None, item: str) -> str | None:
        return _read.latest_day(self.root, symbol, item)

    def put(self, day, symbol: str | None, item: str, payload: object) -> Path:
        return _write.put(self.root, day, symbol, item, payload)

    def put_many(self, day,
                 records: list[tuple[str | None, str, object]]) -> list[Path]:
        return _write.put_many(self.root, day, records)

    def exists(self, day, symbol: str | None, item: str) -> bool:
        return _read.exists(self.root, day, symbol, item)
