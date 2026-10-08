"""M1.1 键（Key）—— 定义 (交易日, 标的, 数据项) 的表示与规范化。

承 §6.1 / §6.2 / K1 / K3：
- 交易日：`YYYY-MM-DD` 字符串
- 标的：纯代码（`AAPL` / `SPX`），无市场前缀、无源名；**也允许 None（全局项）**
- 数据项：受控清单里的名字（未注册 → 报错）

★ 无标的的"全局数据项"（2026-10-06 定，承架构确认"存在无标的数据项"）：
    有些数据**不属于任何标的**，是**一天一份的全局事实** —— 如「交易日历」。
    其键的标的位为 `None`；物理上是 `_<数据项>.json`（`_` 不是合法标的，天然不冲突）。
    哪些数据项是全局的，见 `GLOBAL_ITEMS`（**显式清单**，承 P2：不猜）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

# "整市场一份"的数据（如全市场快照）标地位用的**约定代码** —— 它本身不是标的。
UNIVERSE_SYMBOL = "UNIVERSE"

# 板块代码前缀（板块编码规则，如 `LIST23925`）。
# 板块是"一揽子股票"，**本身不是标的** —— 它的代码只出现在 `plate_members` /
# `plate_state` 的标地位。**票池必须排除它**（否则会把"板块"当成"股票"参与比较）。
# 这是**编码规则**，不是猜测；`plate_list`（全局项）里的代码实测全部以此开头。
PLATE_CODE_PREFIX = "LIST"

# ── 数据项受控清单（承 K3 / §6.1）────────────────────────────────────────
#
# 规矩：`<主体>_<属性>`，全小写蛇形；不含源名、不含用途名。
# 未注册的名字 → 报错（不"容忍未知结构"，承 P2）。
KNOWN_ITEMS: frozenset[str] = frozenset({
    # 底层（带标的）
    "chain",            # 期权链（每行一个合约）
    "kline",            # K线（OHLCV）
    "snapshot",         # 全市场快照（本项带标的=UNIVERSE，见 UNIVERSE_SYMBOL）
    "watchlist",        # 自选清单
    # 板块（概念 / 行业）—— 一揽子股票
    "plate_members",    # 一个板块的成分股（标的位 = 板块代码，如 LIST23925）
    "plate_state",      # 一个板块的集体行为 + 状态（标的位 = 板块代码）
    # 市场结构（指标）
    "spot",
    "net_gex",
    "net_gex_0dte",
    "net_dex",
    "zero_gamma",
    "call_wall",
    "put_wall",
    "abs_call_wall",
    "abs_put_wall",
    "pc_oi",
    "pc_volume",
    # 市场状态
    "vix",
    "breadth",
    # 公司行动（复权用）
    "adjust_factor",    # 复权因子（每除权日一条）
    # 其他
    "rates",
    # ── 全局项（无标的，标地位为 None）──────────────────────────────
    "calendar",         # 交易日历（某市场哪天开市）★见 GLOBAL_ITEMS
    "plate_list",       # 板块名册（代码/名字/类型）★见 GLOBAL_ITEMS
    "industry_state",   # 行业集体行为 + 状态 ★见 GLOBAL_ITEMS
                        # （**无标的**：行业名含中文/空格，不是合法代码）
})

# 全局数据项：键的标的位为 None（承架构确认）。**显式清单**（承 P2）。
GLOBAL_ITEMS: frozenset[str] = frozenset({
    "calendar",
    "plate_list",
    "industry_state",
})

# 全局项在物理布局里的文件名前缀（标地位为空 → `_<item>.json`）。
# `_` 不是合法标的（`_SYMBOL_RE` 要求首字符为字母数字），故永不冲突。
GLOBAL_PREFIX = "_"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 标的规定化：大写字母数字，允许 `-` / `.`（如 BTC-USD、BRK.B）。不含源名前缀。
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]*$")
# 数据项命名规范：小写蛇形。
_ITEM_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class StoreError(Exception):
    """库层的错误基类 —— 缺/错/不一致一律显形（承 P6）。"""


class UnregisteredItem(StoreError):
    """数据项不在受控清单里（承 K3）。"""


def normalize_day(day: date | datetime | str) -> str:
    """交易日 → `YYYY-MM-DD`。

    只接受 `date` / `datetime` / 规范的 `YYYY-MM-DD` 字符串。
    `20261006` 这种非规范写法**直接报错**（承 M1-A：键不规范 → 取不到）。
    """
    if isinstance(day, datetime):
        return day.date().isoformat()
    if isinstance(day, date):
        return day.isoformat()
    if isinstance(day, str):
        if not _DATE_RE.match(day):
            raise StoreError(
                f"交易日表示不规范：{day!r}（要求 YYYY-MM-DD，如 2026-10-06）"
            )
        # 校验真实存在（拒绝 2026-13-40）
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError as e:
            raise StoreError(f"交易日不是有效日期：{day!r}（{e}）") from e
        return day
    raise StoreError(f"不支持的交易日类型：{type(day)!r}")


def normalize_symbol(symbol: str | None) -> str | None:
    """标的 → 纯代码（大写，无源名/前缀）；`None` = 全局项（无标的）。

    - `None` → `None`（**只对全局项合法**，由 `normalize_item` / `Key.make` 保证配对）
    - `US.AAPL` 这类带市场前缀的写法**直接报错**（承 X4：键里无源名/前缀）。
    """
    if symbol is None:
        return None
    if not isinstance(symbol, str) or not symbol:
        raise StoreError(f"标的不合法：{symbol!r}")
    s = symbol.strip().upper()
    if "." in s.split("-")[0] and s.split(".")[0] in {"US", "HK", "SH", "SZ", "JP"}:
        raise StoreError(
            f"标的带市场前缀：{symbol!r}（要求纯代码，如 AAPL 而非 US.AAPL）"
        )
    if not _SYMBOL_RE.match(s):
        raise StoreError(f"标的不规范：{symbol!r}（要求纯代码，大写字母数字）")
    return s


def normalize_item(item: str) -> str:
    """数据项 → 受控清单里的名字；未注册 → 报错（承 K3）。"""
    if not isinstance(item, str) or not item:
        raise StoreError(f"数据项不合法：{item!r}")
    name = item.strip().lower()
    if not _ITEM_RE.match(name):
        raise StoreError(
            f"数据项命名违规：{item!r}（要求小写蛇形，如 net_gex）"
        )
    if name not in KNOWN_ITEMS:
        raise UnregisteredItem(
            f"数据项未注册：{name!r}。若要新增，请先加入 store/keys.py 的 "
            f"KNOWN_ITEMS（承 K3：数据项是受控清单）"
        )
    return name


def is_global_item(item: str) -> bool:
    """该数据项是否**无标的**（全局项）。→ `GLOBAL_ITEMS`。"""
    return normalize_item(item) in GLOBAL_ITEMS


@dataclass(frozen=True, slots=True)
class Key:
    """(交易日, 标的, 数据项) 三元组 —— 库里的唯一定位符。

    `symbol` 为 `None` = 全局项（无标的，如交易日历）。
    **配对约束**（承 P2：不猜）：
      - 全局项（`GLOBAL_ITEMS`）→ 标的**必须**为 None；
      - 非全局项 → 标的**必须**非 None。
    任一违反 → 报错（不"容忍"错配）。
    """

    day: str
    symbol: str | None
    item: str

    @classmethod
    def make(cls, day, symbol: str | None, item: str) -> "Key":
        item_n = normalize_item(item)
        sym_n = normalize_symbol(symbol)
        glob = item_n in GLOBAL_ITEMS
        if glob and sym_n is not None:
            raise StoreError(
                f"全局数据项 {item_n!r} 不应带标的（收到 {sym_n!r}）—— "
                f"标地位应为 None（承 P2）"
            )
        if not glob and sym_n is None:
            raise StoreError(
                f"数据项 {item_n!r} 不是全局项，必须带标的（收到 None）—— "
                f"若确为全局项，请加入 GLOBAL_ITEMS（承 P2）"
            )
        return cls(day=normalize_day(day), symbol=sym_n, item=item_n)

    @property
    def is_global(self) -> bool:
        return self.symbol is None

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        return f"({self.day}, {self.symbol or '∅'}, {self.item})"
