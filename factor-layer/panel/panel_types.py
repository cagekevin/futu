"""M1 面板 —— 数据结构 + 契约常量（**零依赖**：不 import 同层其它模块，更不 import 外部）。

本模块是 factor-layer 内部**唯一**的截面数据结构定义（承 PRD M1 / §6.3）。

## 为什么宽表用 DataFrame（而不是 tuple-of-tuples）

`backtest/strategy_contract.py` 的判据是「不可变序列才能让事实真正只读」，它用
tuple 是对的 —— 那里传的是**一条只读序列**。

但本层的核心运算是**逐日横截面**：M4 的标准化、中性化（逐日 OLS 取残差）都是
"按行"操作。用 tuple 手写这些循环会显著提高出错概率。
按**准确性 > 复杂度**判据 → 选 DataFrame，并用**只读约定**补上不可变性。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注，运行时不 import
    import pandas as pd

__all__ = [
    "CrossSectionPanel",
    "KLINE_ITEM",
    "ADJUST_HFQ",
    "KLINE_BAR_FIELDS",
    "PANEL_LONG_COLUMNS",
    "SNAPSHOT_ITEM",
    "SNAPSHOT_UNIVERSE",
    "SNAPSHOT_FIELDS",
]

# ── 契约常量（**唯一**出处，禁裸字面量 —— 承写码4步法 State 2.3）────────────

#: K线数据项名（与数据层 `store/keys.py::KNOWN_ITEMS` 里的 `kline` 一致）。
KLINE_ITEM = "kline"

#: 本层固定的研究口径 —— 后复权（严格因果，承 PRD §6.1 / `Agent.md` §4.4）。
#: ⚠️ 数据层的 `--adjust` **不传 = 原样 raw**；这里给的是本层的**显式选择**，
#:    不是"省略就用它"的兜底 —— 想取 raw 必须显式传 `adjust=None`（承 P1）。
ADJUST_HFQ = "hfq"

#: 合法口径（与数据层 `engine/adjust.py::MODES` **同值**）。
#: ⚠️ 两份定义是并列设计的已知成本；一致性由**跨层契约测试**锁死（PRD §八 #21）。
ADJUST_MODES = ("hfq", "qfq")

#: K线 bar 的字段（与 `backtest/data_source.py::KLINE_BAR_FIELDS` **同值**）。
#: ⚠️ 两份定义是**并列设计**的已知成本（承 PRD §7.2：本层与 backtest 互不 import）；
#:    一致性由**跨层契约测试**锁死（PRD §八 #21），不靠人记。
KLINE_BAR_FIELDS = ("open", "high", "low", "close", "volume")

#: 长表列名（承 PRD §6.3）。
PANEL_LONG_COLUMNS = ("trade_date", "symbol", "value")

#: 全市场快照的数据项名（与数据层 `store/keys.py::KNOWN_ITEMS` 的 `snapshot` 一致）。
SNAPSHOT_ITEM = "snapshot"

#: 快照的**标地位**约定代码（承数据层 `store/keys.py::UNIVERSE_SYMBOL`）——
#: 它是"整市场一份"数据的占位，**本身不是标的**。
SNAPSHOT_UNIVERSE = "UNIVERSE"

#: 本层用到的快照字段（M2 的原料）。
#: ⚠️ 与数据层快照的**全部**字段不同 —— 这是本层**消费清单**，不是数据层契约。
SNAPSHOT_FIELDS = ("symbol", "industry", "price", "market_cap")


@dataclass(frozen=True, eq=False)
class CrossSectionPanel:
    """截面面板 —— 本层内部**唯一**的数据结构（承 PRD §6.3）。

    宽表：`fields[name]` 是 `DataFrame`，index = 交易日（**升序**），
    columns = 标的（**升序**）。

    ⚠️ **可变性纪律**（承 `backtest/strategy_contract.py` 的判据）：
    `frozen=True` 只挡"重新赋值"，**挡不住就地改 DataFrame** ——
    所以这里另加一条**约定**：**消费方一律只读**，禁止原地 mutate
    （M4 需要派生新值时，**改名产出新面板**，承 M4-T5）。

    `eq=False`：DataFrame 的 `__eq__` 是逐元素比较（返回 DataFrame），
    会让 dataclass 生成的 `__eq__` 崩掉 —— 故关闭，测试逐字段断言。

    **显形字段**（承 P5：由 M1 透传，不许在本层吞掉）：
      · `n_adjust_events`      —— 复权用到的除权事件数（**0 = 空操作**）
      · `contaminated_days`    —— 派现日（收益不可信）
      · `snapshot_day`         —— 判定票池用了**哪天**的快照（承 PRD M1-2）
      · `snapshot_is_after_day`—— 该快照日**晚于**所查日期（**含未来信息**）
      · `adjust`               —— 本面板的**复权口径**（`None` = 原样 raw）
        ⚠️ 它让 `FactorSpec.adjust` **可验证**：因子声明要 `hfq`，而面板若是 raw，
           算出来的因子就是错的（除权日假跳变）—— 必须**报错**而不是静默算
    """

    dates: tuple[str, ...]
    symbols: tuple[str, ...]
    fields: Mapping[str, "pd.DataFrame"]
    universe_by_day: Mapping[str, tuple[str, ...]]
    n_adjust_events: Mapping[str, int]
    contaminated_days: Mapping[str, tuple[str, ...]]
    adjust: str | None
    snapshot_day: str
    snapshot_is_after_day: bool

    def field(self, name: str) -> "pd.DataFrame":
        """取某字段的宽表；**没有该字段 → 报错**（承 P1：缺就报，不静默给空）。"""
        if name not in self.fields:
            raise KeyError(
                f"面板没有字段 {name!r}；现有：{sorted(self.fields)}"
                f"（承 P1：不静默兜底）"
            )
        return self.fields[name]
