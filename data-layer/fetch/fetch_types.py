"""M2.3 参数（Request）+ 统一出口结构（承 F1 / F2）。

这里定义"取数的统一请求"与"归一化后的统一出口"，**不含任何源名**。
源适配器只负责：把各源原始格式翻译成这里的结构。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ── 通用字段清单（承 F2 / §6）────────────────────────────────────────────
# 通用 = 各源都应能提供的字段，命名统一。源特有什么 → 放进 `extra`。
CHAIN_FIELDS = (
    "contract",         # 合约唯一标识（源自有，但语义通用）
    "expiry",           # 到期日（date）
    "cp",               # "C" / "P"
    "strike",           # 行权价
    "bid",
    "ask",
    "iv",               # 隐含波动率（**分数**，0.20 = 20%）
    "open_interest",
    "volume",
    "delta",            # 源提供的 delta（未归一化的希腊值 → extra 更合适，这里保留通用位）
    "gamma",
    "last_trade_price",
)


class FetchError(Exception):
    """取数失败 —— 必显形，不静默返回空（承 F4 / P4 / P6）。"""


@dataclass(frozen=True)
class Request:
    """统一请求参数（承 M2.3）：标的 / 日期 / 类型。

    ⚠️ 参数里**没有"用途"语义**，也**不含源名**（承 X4）。
    `as_of` 是"要取哪天的数据"，用于 P4 校验"内容日期与请求不符 → 报"。
    """

    symbol: str
    as_of: str | None = None       # YYYY-MM-DD；None = 取最新
    kind: str = "chain"            # 数据项类型：chain / kline / snapshot …


@dataclass
class ChainResult:
    """归一化后的期权链出口（承 F2）。

    - `rows`：每行一个合约，键为 `CHAIN_FIELDS`（通用命名字段）
    - `extra`：源特有字段（**键名不代表源**；上游知"extra 未归一，用前要看"）
    - `symbol` / `spot` / `feed_timestamp` / `fetched_at`：自证字段（承 P3）
    """

    symbol: str
    spot: float
    feed_timestamp: datetime          # 数据本身的时间（源给）
    fetched_at: datetime              # 取数时刻
    rows: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:  # pragma: no cover
        return len(self.rows)
