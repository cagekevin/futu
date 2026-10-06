"""源适配器注册表 —— 换源只动 `fetch/`（承 F3 / 铁律 5）。

新增一个源 = 在 `fetch/sources/` 加一个文件（或子包）+ 自注册（调 `register()`）。
**`fetch/` 之外的代码永远不 import 具体源**：它们通过 `fetch_api.chain(...)` 拿
归一化结果（承 F1 / X4）。
"""
from __future__ import annotations

from typing import Callable, Protocol

from .fetch_types import ChainResult, FetchError, Request


class ChainSource(Protocol):
    """期权链源适配器协议。"""

    name: str  # 实例内部标识，**不外泄**

    def fetch_chain(self, req: Request) -> ChainResult: ...


_REGISTRY: dict[str, Callable[[], ChainSource]] = {}


def register(name: str, factory: Callable[[], ChainSource]) -> None:
    """注册一个源。`name` 只在 M2 内部使用（承 F1：源名不泄到上游）。"""
    _REGISTRY[name.lower()] = factory


def keys() -> list[str]:
    return sorted(_REGISTRY)


def get_source(name: str) -> ChainSource:
    key = name.lower()
    if key not in _REGISTRY:
        raise FetchError(
            f"未知数据源：{name!r}。已注册：{keys()}（加新源只新增 fetch/ 文件，承 F3）"
        )
    return _REGISTRY[key]()


def default_source_name() -> str:
    """默认源 —— 从环境变量读，未设 → 用内置首个可用源。"""
    import os

    return os.getenv("TRADING_DESK_SOURCE", "cboe").lower()
