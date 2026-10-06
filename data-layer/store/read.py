"""M1.4 读取（Read）—— 时间轴接口：days/symbols/items/get/series。

承 K4：读不产生写、读不降级；缺失就是缺失，**绝不**返回邻近日期。

`symbol=None` = 读**全局数据项**（无标的，如交易日历）—— 见 `keys.GLOBAL_ITEMS`。
"""
from __future__ import annotations

from typing import Any

from pathlib import Path

from .keys import Key, normalize_day, normalize_symbol, normalize_item
from .storage import (
    path_for, read_json, list_days, list_symbols, list_items, list_globals,
)


class Missing(KeyError):
    """键不存在 —— 显式缺失（承 K4：缺就是缺，不做'取最新'）。"""


def exists(root: Path, day, symbol: str | None, item: str) -> bool:
    return path_for(root, Key.make(day, symbol, item)).exists()


def has(root: Path, day, symbol: str | None, item: str) -> bool:
    return exists(root, day, symbol, item)


def get(root: Path, day, symbol: str | None, item: str, *,
        default: Any = Missing) -> Any:
    """取某天某标的（或全局）的某项数据。

    - 缺失 + `default` 未给 → 抛 `Missing`（**不返回别的日期**，承 K4）
    - 缺失 + 显式给了 `default` → 返回 default（调用方自担"缺"的语义）
    - `symbol=None` → 全局项（如 `calendar`）

    ⚠️ 这里**没有**"取最新"逻辑：缺 10-06 就是缺，绝不回退到 10-05。
    """
    key = Key.make(day, symbol, item)
    path = path_for(root, key)
    payload = read_json(path)
    if payload is None:
        if default is Missing:
            raise Missing(f"库中无此数据：{key}（不降级、不取最新）")
        return default
    return payload


def series(
    root: Path,
    symbol: str | None,
    item: str,
    *,
    days: list[str] | None = None,
) -> list[tuple[str, Any]]:
    """某项数据的连续序列（跨日，供时间序列）。

    返回 [(交易日, 值), …]，**只含实际存在的日子**；缺失的日子不补、不回填
    （承 K4：缺就是缺）。若给了 `days`，只在这批日期里筛，同样不补。
    `symbol=None` → 全局项序列。
    """
    sym = normalize_symbol(symbol)
    it = normalize_item(item)
    ds = list_days(root) if days is None else [normalize_day(d) for d in days]
    out: list[tuple[str, Any]] = []
    for d in sorted(ds):
        path = path_for(root, Key.make(d, sym, it))
        payload = read_json(path)
        if payload is not None:
            out.append((d, payload))
    return out


def latest_day(root: Path, symbol: str | None, item: str) -> str | None:
    """某项数据**最近有值**的交易日（升序里最后一个）；从没有过 → `None`。

    承"缓存 + 增量"：增量更新靠它定"从哪天起补"，**不重拉全历史**。
    注意：这是"有就返回"，与 `get` 的"缺就是缺"不冲突 —— 它回答的是"更新到哪了"，
    不是"某天有没有"。返回 `None` = 该项在库里**一天都没有**。
    """
    sym = normalize_symbol(symbol)
    it = normalize_item(item)
    for d in reversed(list_days(root)):
        if path_for(root, Key.make(d, sym, it)).exists():
            return d
    return None


def days(root: Path) -> list[str]:
    """有哪些交易日（升序）。"""
    return list_days(root)


def symbols(root: Path, day) -> list[str]:
    """某天有哪些标的（升序）。**不含全局项。**"""
    return list_symbols(root, normalize_day(day))


def items(root: Path, day, symbol: str) -> list[str]:
    """某天某标的有哪些数据项（升序）。"""
    return list_items(root, normalize_day(day), normalize_symbol(symbol))


def global_items(root: Path, day) -> list[str]:
    """某天有哪些**全局数据项**（升序）。"""
    return list_globals(root, normalize_day(day))
