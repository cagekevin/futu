"""交易级组合模拟器 —— 事件驱动 / 组合级 / 路径依赖。

需求级规格：`docs/design/03-策略规格-Tugboat突破交易2.0-2026-10-08.md`

---

## 为什么本层需要**第二种模型**（承规格 §3.0c 的代码级审计）

现有 `pnl_engine.py` 的模型是「**每根 bar 一个目标仓位**」：
`position = tanh(factor) × POSITION_CAP`，然后逐 bar 盯市。它是**向量化、单标的**的。

而 Tugboat 的系统是「**横截面选股 + 共享资金 + 按 R 定仓 + 路径依赖出场**」。
两者**不是同一个模型** —— 现有引擎装不下：

| 他的系统需要 | `pnl_engine` 实际是 |
|---|---|
| 270 只横截面里每天选 | `run_backtest` 逐只独立跑 |
| **N 个并发持仓、资金共享** | 无组合概念 |
| **按 R 定仓**（股数 = 1% ÷ 止损距离）| 仓位与止损无关 |
| **止损 / 止盈 / 跳空** | 没有成交事件 |

## ★ 与 `pnl_engine` 的关系（回应铁律 R1「PnL 只能有一份」）

R1 的原话是「**PnL 一旦有第二份实现 = 两个真相**」。本文件**是第二种模型**，
所以必须回答"凭什么不是第二份真相"：

1. **应用域不重叠**：`pnl_engine` 算「目标仓位 × 收益」；本文件算「成交价 × 股数」。
   **同一策略**只会走其中一条，不会两条都走。
2. **口径对齐**：成本率取 `backtest_config.COST_RATE`（**唯一来源**，本文件不定义数字）。
3. **★ 跨引擎一致性测试**：在**两者都能表达**的情形（只做多 / 不设止损 / 固定仓位 /
   持有 H 根）下，**两边结果必须一致** —— 见 `tests/test_trade_simulator.py`。
   ⇒ 这不是"两个真相"，是**两份互相校验**。

## 成交约定（**照抄外部主流框架的通行做法**，见规格 §3.0c）

| 情形 | 约定 | 为什么 |
|---|---|---|
| 入场 | **次日开盘价** | 信号在 `t` 收盘确认 ⇒ 最早 `t+1` 开盘能成交（无前视）|
| **同一根 bar 内止损与止盈都触** | **判止损** | 日线看不到日内先后 ⇒ 取**对他不利**的一侧 |
| **跳空越过止损** | 成交价 = **`min(止损价, 开盘价)`** | 跳空低开时你只能在开盘价卖（更差）—— 按止损价成交会**高估** |
| 跳空越过止盈 | 成交价 = **`max(止盈价, 开盘价)`** | 对称：跳空高开时更好 |
| 均线 / 超时出场 | **当日收盘价** | 收盘时才确认 |
| 成本 | 入场、出场各收一次（按成交名义额）| 与 `backtest_config.COST_RATE` 同口径 |

## 日内顺序（为什么是这个顺序）

```
每根 bar：
  ① 先按**开盘价**成交上一日排队的入场单   ← 开盘在前
  ② 再检查已持仓的出场（止损 → 止盈 → 均线 → 超时）← 盘中/收盘在后
  ③ 最后用**今日收盘**算明日候选
```
⇒ 同一天"旧仓出场"腾出的持仓位，**不能**给当天开盘的入场用（开盘在前）—— 这是**保守**的一侧。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import numpy as np
import pandas as pd

import backtest_config

__all__ = [
    "RECENT_TRADES_WINDOW",
    "SKIP_REASONS",
    "AccountPolicy",
    "AccountState",
    "EntryRequest",
    "ExitPolicy",
    "ExposurePolicy",
    "ExposureSettings",
    "SimulationResult",
    "StaticExposure",
    "Trade",
    "TradeStrategy",
    "reconcile",
    "reconcile_fills",
    "simulate",
]

#: 出场原因（**封闭枚举** —— 报告按它分组，写错会静默少一类）。
EXIT_STOP = "stop"
#: ★ 他 §6.1 提前离场**第 1 条**：「单日大动能下跌（突兀走势）」（行 570）。
#: 成交价 = **出场日收盘**（与 `_CLOSE_SETTLED` 同款 ⇒ 可反推）。
EXIT_EARLY_DROP = "early_drop"
#: ★ 他 §6.1 提前离场**第 4 条**：「**大盘或所在行业发生集体性显著回撤**」（行 573）。
#: 成交价 = **出场日收盘**（与 `_CLOSE_SETTLED` 同款 ⇒ 可反推）。
EXIT_MARKET = "market"
#: ★ 他 §2.2 阶段③：「**开新仓同时关旧仓** / 部分获利，止损移到盈亏平衡」（行 129）。
#: 成交价 = **出场日收盘**（与 `_CLOSE_SETTLED` 同款 ⇒ 可反推）。
EXIT_ROTATE = "rotate"
EXIT_TARGET_PARTIAL = "target_partial"
EXIT_TARGET_FINAL = "target_final"
EXIT_MA_BREAK = "ma_break"
EXIT_NO_PROGRESS = "no_progress"
EXIT_TIME_CAP = "time_cap"
EXIT_END_OF_DATA = "end_of_data"

#: ★ **跳过原因（封闭枚举 + 中文标签）** —— `SimulationResult.skipped` 的合法键集。
#:
#: ## 为什么是一张表（治 TD-05-17）
#:
#: 原来这里是**四个独立的计数字段**（`skipped_no_slot` / `skipped_exposure` /
#: `skipped_expired` / `skipped_after_end`），而它们的**口径、单位、展示顺序**
#: 在生产者（本文件）与消费者（`trade_metrics.render_report`）**各写一份** ⇒
#: 并排读的时候「重复或漏计」看不出来：
#:
#: · **重复**：一张限价单先被"仓位满"挡住（计入 `no_slot`），挂到有效期结束时
#:   又计一次"限价到期" ⇒ **同一张单被数两次**，四个数相加 ≠ 被跳过的信号数；
#: · **漏计**：`i >= expire` 且**已持有该标的**的那条路径**直接 `continue`**，
#:   哪个计数都不进；开盘已跌破止损、价格缺失两条路径同样静默丢弃。
#:
#: ⇒ 现在：**唯一记账点** `_skip()` ＋ 一张**穷举**的原因表。
#:   记账时机 = **这张单离开 `live` 且没成交的那一刻**（不是"被挡的那一天"）：
#:   被挡住但后来又成交的单**不算跳过**（原来那种"记了又成交"就是重复的来源）。
#:
#: ## 谁读这张表
#:
#: 生产者给全（键 + 中文标签 + 顺序），消费者**只转发**、按它渲染 —— 承 Step 4 铁律②：
#: 消费端不许自己拼文案、不许自己决定顺序。
SKIP_NO_SLOT = "no_slot"
SKIP_EXPOSURE = "exposure"
SKIP_EXPIRED = "expired"
SKIP_HELD = "held"
SKIP_INVALIDATED = "invalidated"
SKIP_NO_PRICE = "no_price"
SKIP_BAD_CANDIDATE = "bad_candidate"
SKIP_AFTER_END = "after_end"

SKIP_REASONS: Mapping[str, str] = {
    SKIP_NO_SLOT: "仓位已满",
    SKIP_EXPOSURE: "总曝险上限",
    SKIP_EXPIRED: "限价到期未成交",
    SKIP_HELD: "已有同标的持仓",
    SKIP_INVALIDATED: "开盘已跌破止损（设置失效）",
    SKIP_NO_PRICE: "价格缺失或非正",
    SKIP_BAD_CANDIDATE: "候选无效（不在面板内／止损非有限）",
    SKIP_AFTER_END: "末日信号（等不到成交）",
}


@dataclass(frozen=True)
class ExitPolicy:
    """出场规则（**他的原规则**，规格 §1.3）。

    ⚠️ 这些值**照原文取**，不是"我拍的"（改动等于改规则，必须显形）：

    | 字段 | 他的原文 |
    |---|---|
    | `target_r` | 「**至少 3–4 个 R** 才考虑部分获利」（行 549）|
    | `partial_fraction` | 「**分段获利**」—— 部分减码 |
    | `breakeven_after_partial` | 「部分获利后**立即把止损上移到入场点**」（行 554）|
    | `ma_exit` | 「最后一段：**收盘价跌破 10 日或 20 日均线时卖**」（行 555–556）|
    | `no_progress_days` | 「**5 天以上**没有发生预想的爆发性走势」（行 562）|
    | `max_hold_days` | ⚠️ **原文没给** —— 我加的防挂死上限（**必须显形**）|
    """

    target_r: float = 3.0
    partial_fraction: float = 0.5
    breakeven_after_partial: bool = True
    use_ma_exit: bool = True
    #: **是否按止损出场**。`False` = 只把止损价拿去**定仓位**（股数照算），
    #: 但**不真的止损** —— 用于做「**有框架 vs 无框架**」的对照臂：
    #: 同一批入场、同样的股数，唯一差别就是"有没有那套出场规则"。
    use_stop: bool = True
    no_progress_days: int = 5
    no_progress_min_r: float = 1.0
    max_hold_days: int = 60
    #: **时间型部分止盈**：持有满 N 天仍未触发 3R ⇒ 也先减半（按当日收盘）。
    #: 出处：他 §2.2 阶段④「**压低曝险 + 节奏变快**（很窄的止损、**2–3 天部分获利**）」。
    #: `None` = 关闭（默认；只有阶段④会打开）。
    partial_after_days: int | None = None
    #: ★ **单日大动能下跌 ⇒ 离场**（他 §6.1 提前离场**第 1 条**：「**单日大动能下跌**（突兀走势）」，
    #: 行 570；§6.1 的失败案例里 **PLUG** 就是这个形态：「突破后立刻大动能拉回原点」）。
    #:
    #: 判据 = **当日涨跌幅 ≤ −k × ADR** —— 用 **ADR 归一**而不是绝对百分比，
    #: 理由与止损同一条：**高波动的票不该被一个绝对百分比误伤**
    #: （原文在止损那里也是这个尺度：「尽量不大于一个 ADR」）。
    #:
    #: ⚠️ `None` = **关闭**（默认）—— 这是刻意的：`ExitPolicy` 被大量单测直接构造，
    #:    默认打开会**静默改变它们的夹具语义**。`run_tugboat` 会显式打开
    #:    （值来自 `DEFAULTS["early_drop_adr"]`，**我定的数**，进报告指纹）。
    early_drop_adr: float | None = None
    #: ★ **大盘集体性显著回撤 ⇒ 离场**（他 §6.1 提前离场**第 4 条**，行 573：
    #: 「**大盘或所在行业发生集体性显著回撤**」；他的例子：买了核能股 **BWXT**，
    #: 第三天**整个核能行业集体回调** ⇒ 他先离场）。
    #:
    #: 判据 = **§7.1 方法①②同时不成立** —— `spy_above_20ma <= 0`（标普跌破 20 日线）
    #: **且** `net4 < 0`（跌超 4% 的家数多于升超的）。
    #: 「**集体性**」= 广度差（`net4`）；「**显著**」= 指数跌破 20MA ⇒ 两个词各对应一个条件，
    #: **没有我拍的数**。它与入场门槛（`market_gate`）的判据**正好互补**。
    #:
    #: ⚠️ **「所在行业」那一半测不了**（票池没有行业分类）⇒ 显形，只做了「大盘」那一半。
    #: ⚠️ 默认 `False`（理由同 `early_drop_adr`：别静默改单测夹具）。
    market_exit: bool = False


@dataclass(frozen=True)
class AccountPolicy:
    """资金与风险规则（**他的 R 规则**，规格 §1.2）。

    | 字段 | 他的原文 |
    |---|---|
    | `risk_per_trade` | 「R = 每笔交易的风险 = 本金的 **1%**」（行 81）|
    | `max_positions` | 「**5 笔持仓 = 5R** 潜在损失」（行 102）|
    | `max_total_exposure` | ⚠️ **原文没给** —— 我加的防杠杆上限（**必须显形**）|
    | `cash_rate` | ⚠️ 现金不计息（保守；原文未提）|
    """

    initial_equity: float = 1_000_000.0
    risk_per_trade: float = 0.01
    max_positions: int = 5
    max_total_exposure: float = 1.0
    cost_rate: float = backtest_config.COST_RATE
    #: ★ **市价单在「信号日收盘」成交**（而不是次日开盘）。
    #:
    #: 默认 `False` = 次日开盘 —— **保守且无前视争议**。
    #: 打开它是为了测一件要紧的事：**我们比他晚了整整一天**。
    #: 他做的是**日内**（"开盘第一根 K 线最高点入场"），而突破策略对**那一天**极敏感。
    #: ⇒ 用"当日收盘成交"当他的**上界近似**（比次日开盘更接近他的成交时点）。
    trade_on_close: bool = False
    #: ★ **仓位满了就换仓**：他 §2.2 阶段③「还想加仓 → **开新仓同时关旧仓**」（行 129）。
    #:
    #: ⚠️ **原文没说关哪一笔** ⇒ 这里取「**R 最低的那笔**」（关强的没道理）。
    #:    这个选择**是我定的**，必须显形。
    #: ⚠️ 默认 `False`（理由同 `ExitPolicy.early_drop_adr`：别静默改单测夹具）；
    #:    `TugboatBreakout` 会用 `DEFAULTS` 的值补上。
    rotate_on_full: bool = False


@dataclass(frozen=True)
class EntryRequest:
    """一张待成交的入场单（由策略在信号日收盘生成）。

    两种成交方式（承规格 §11.2 的 B1）：

    | `limit_price` | 成交 |
    |---|---|
    | `None` | **市价**：信号日**次日开盘**成交（他的入场①「突破时买入」的日线版）|
    | 给了值 | **限价**：有效期 `valid_days` 天内，某日 `low ≤ limit` 即成交，价 = `min(limit, open)` |

    限价用途：他的入场②「突破后**回踩**到区间上方买」（**超两天没回撤 ⇒ 放弃**）
    与入场③「偷步买」。
    """

    day: str
    symbol: str
    stop_price: float
    limit_price: float | None = None
    valid_days: int = 1


@dataclass(frozen=True)
class AccountState:
    """策略在**当日决策时**能看到的账户状态（**只含已发生的事**，无未来信息）。

    ⚠️ 这是**反馈环**的入口（结果 → 状态 → 曝险 → 结果）—— 规格 §11.1 的 A6'：
       规则必须**写死在契约里**，且报告要**画出档位时间序列**让人看得见。
    """

    day_index: int
    #: **当前日期字符串**。★ 曝险策略必须用它去查市场状态，
    #: **不要**用 `day_index` 去 `market_state.index[i]` ——
    #: 那隐含了"市场状态的行序与模拟的日序一致"这个**没有任何东西保证**的前提，
    #: 一旦不一致，档位会**安静地错位**（不报错，只是全错）。
    day: str
    equity: float
    initial_equity: float
    open_positions: int
    n_closed: int
    total_r: float
    #: 最近 `recent_r` 笔的 R 倍数与出场原因（按时间序，**只含已平仓**）。
    recent_r: tuple[float, ...]
    recent_reasons: tuple[str, ...]

    @property
    def equity_ratio(self) -> float:
        """净值 / 初始净值（> 1 = 在赚）。"""
        return self.equity / self.initial_equity if self.initial_equity else 1.0


@dataclass(frozen=True)
class ExposureSettings:
    """曝险策略对**当日**给出的设置（`stage` 供报告画时间序列）。"""

    max_positions: int
    risk_fraction: float
    exit_policy: ExitPolicy
    stage: str


class ExposurePolicy(Protocol):
    """**状态依赖**的曝险策略（他的 §2.2「曝险四阶段」）。

    规格 §11.1 的定论：四阶段调的是 **`max_positions`（暴露多少笔）**，
    **不是 R 本身** —— 他自己说「R 的绝对值不随本金变动」「禁止在疯狂牛市高位加大 R」。
    """

    def settings(self, state: AccountState, default: ExposureSettings,
                 ) -> ExposureSettings:
        ...


@dataclass(frozen=True)
class StaticExposure:
    """默认：不随状态变（等价于没有四阶段）。"""

    def settings(self, state: AccountState,
                 default: ExposureSettings) -> ExposureSettings:
        return default


#: `AccountState.recent_r` / `recent_reasons` 保留最近多少笔。
RECENT_TRADES_WINDOW = 20


class TradeStrategy(Protocol):
    """交易策略契约：**只声明"选什么、止损在哪、什么算趋势破坏"**。

    ⚠️ **指标计算全归策略**（承 M3 的 role 纪律）：模拟器**不算任何指标** ——
    它只做机制（成交、记账、持仓位）。这样"指标的唯一实现点"仍然在因子层。
    """

    name: str
    params: Mapping[str, Any]
    exit_policy: ExitPolicy

    def candidates(self, panel) -> pd.DataFrame:
        """返回候选入场表，列 = `day, symbol, stop_price`（**只用 ≤ day 的信息**）。"""
        ...

    def ma_exit_level(self, panel) -> pd.DataFrame:
        """宽表（index=日期，columns=标的）：**收盘跌破它 = 趋势破坏**（他的 10/20 日线）。

        ⚠️ 由**策略**产出而不是模拟器算 —— 见类 docstring 的指标归属。
        """
        ...


@dataclass
class _Position:
    symbol: str
    entry_day: str
    entry_bar: int              # ★ 成交那根 bar 的下标（持仓天数从**成交日**算）
    entry_price: float
    initial_stop: float
    stop: float
    shares_initial: float
    shares_left: float
    risk_amount: float          # = shares_initial × (entry − initial_stop)
    took_partial: bool = False
    realized: float = 0.0       # 已实现盈亏（含成本前的毛利，成本单列）
    cost_paid: float = 0.0
    peak_r: float = 0.0         # 持有期内达到过的最大浮盈（R 计）—— 供"无进展"判定
    partial_price: float = float("nan")   # 部分止盈那笔的成交价
    partial_shares: float = 0.0            # 部分止盈卖掉的股数
    #: 最近一次**实际成交**的价格 —— 全出那条路径要用它记 `exit_price`。
    #: ⚠️ 用它而不是 `close`：部分止盈的成交价是 `max(止盈价, 开盘)`，
    #:    与当日收盘**不是一回事**（记错会让导出的逐笔清单**看起来对不上账**）。
    last_fill: float = float("nan")

    @property
    def risk_per_share(self) -> float:
        return self.entry_price - self.initial_stop


@dataclass(frozen=True)
class Trade:
    """一笔**完整交易**（含部分止盈的加权 R 倍数）。"""

    symbol: str
    entry_day: str
    entry_price: float
    initial_stop: float
    exit_day: str
    exit_price: float
    shares: float
    r_multiple: float           # 毛利 / 初始风险（**扣成本前**；成本在报告里单列）
    return_pct: float
    exit_reason: str
    hold_days: int
    #: 是否**做过部分止盈** —— 供对账。
    took_partial: bool = False
    #: 部分止盈那笔的**成交价与股数**（`took_partial=False` 时无意义）。
    #: 有了它，**带部分止盈的交易也能被逐笔重算**（否则对账只能覆盖一半的成交）。
    partial_price: float = float("nan")
    partial_shares: float = 0.0


@dataclass(frozen=True)
class SimulationResult:
    """模拟结果 —— **既有交易清单、也有净值曲线**。

    ⚠️ 规格 §3.0c 的依据：qsx 明确写「**只给交易清单的话，回撤都算不了**」
    （"trade endpoints cannot identify the intervening account drawdown"）
    ⇒ **两者都必须有**。
    """

    strategy: str
    params_fingerprint: str
    trades: tuple[Trade, ...]
    equity_days: tuple[str, ...]
    equity_values: tuple[float, ...]
    #: ★ **被跳过的信号** —— `原因 → 笔数`，**按信号（订单）计、互斥**。
    #:
    #: 键的合法集合 = `SKIP_REASONS`（**穷举**：未知原因会报错，见 `_skip()`）；
    #: 每张单**恰好计入一个**类别 ⇒ 各项相加 = 被跳过的信号总数（治 TD-05-17）。
    #: 中文标签也在 `SKIP_REASONS` 里（生产者给全 ⇒ 消费者只转发）。
    skipped: Mapping[str, int] = field(default_factory=dict)
    daily_exposure: tuple[float, ...] = field(default=())
    #: 每天适用的曝险档位名（供报告画时间序列 —— 承规格 §11.1 的 A6'）。
    stages: tuple[str, ...] = ()

    @property
    def n_trades(self) -> int:
        return len(self.trades)


def _fingerprint(mapping: Mapping[str, Any]) -> str:
    import hashlib
    import json

    payload = json.dumps({k: v for k, v in sorted(mapping.items())},
                         default=str, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def reconcile(result: SimulationResult, cost_rate: float) -> dict[str, Any]:
    """★ **自检**：用**独立公式**把每一笔的 R 重算一遍，跟记录比。

    ## 为什么把自检**放在模拟器旁边**

    因为记账错**不会报错** —— 它只会让所有报告数字安静地错掉。
    放在这里，调用方（如 `run_tugboat.py`）可以**每次跑都核一遍**，
    而不是"等想起来再写个脚本查"。

    独立公式（照定义写，**不复用模拟器内部的任何一步**）：

    ```
    没部分止盈： gross = (出场−入场)×股数
                 cost  = (入场×股数 + 出场×股数) × 费率
    有部分止盈： gross = (部分价−入场)×部分股数 + (出场−入场)×(股数−部分股数)
                 cost  = (入场×股数 + 部分价×部分股数 + 出场×(股数−部分股数)) × 费率
    R = (gross − cost) / (股数 × (入场 − 初始止损))
    ```

    返回 `{"n", "n_partial", "bad", "max_abs_diff", "examples"}`；
    `bad == 0` 才算过。
    """
    bad = 0
    worst = 0.0
    examples: list[str] = []
    n_partial = 0
    skipped = 0
    for t in result.trades:
        e, xp, sh, stop = (t.entry_price, t.exit_price, t.shares, t.initial_stop)
        if sh <= 0 or not np.isfinite(e) or not np.isfinite(xp):
            # ⚠️ **跳过要计数** —— 一个专门用来"不静默"的自检，
            #    自己不能有一条静默路径。（复审 10.2）
            skipped += 1
            continue
        if t.took_partial and np.isfinite(t.partial_price):
            n_partial += 1
            pp, ps = t.partial_price, t.partial_shares
            rest = sh - ps
            gross = (pp - e) * ps + (xp - e) * rest
            cost = (e * sh + pp * ps + xp * rest) * cost_rate
        else:
            gross = (xp - e) * sh
            cost = (e * sh + xp * sh) * cost_rate
        expect = (gross - cost) / (sh * (e - stop))
        diff = abs(expect - t.r_multiple)
        worst = max(worst, diff)
        if diff > 1e-9:
            bad += 1
            if len(examples) < 5:
                examples.append(f"{t.symbol} {t.entry_day}: 记录 {t.r_multiple:.6f}"
                                f" vs 重算 {expect:.6f}")
    return {"n": len(result.trades), "n_partial": n_partial, "bad": bad,
            "max_abs_diff": worst, "examples": examples, "skipped": skipped}


#: `reconcile_fills` 能**独立反推**成交价的出场原因（"收盘结算"类：③均线 / ④超时 / 末尾强平）。
#: 它们的成交价规则就是 `close[出场日]` —— 简单、无需复制模拟器的路径逻辑。
_CLOSE_SETTLED = (EXIT_MA_BREAK, EXIT_NO_PROGRESS, EXIT_TIME_CAP, EXIT_END_OF_DATA,
                  EXIT_EARLY_DROP, EXIT_MARKET, EXIT_ROTATE)

#: **不能**独立反推的出场原因 ⇒ **显形**，不假装覆盖（承 R5：审不了要报错／报出，不许静默跳过）。
_UNVERIFIABLE_FILLS: dict[str, str] = {
    EXIT_TARGET_FINAL: (
        "部分止盈把股数卖到 0 的收尾价 = 最后一次实际成交价；"
        "而「部分止盈是哪一天成交的」不在 `Trade` 记录里（只有价与股数）"
        "⇒ 无法从 bar 独立反推（反推需要复制模拟器的路径分支 = 第二份实现）"),
}


def reconcile_fills(result: SimulationResult, dates, symbols, bars: Mapping[str, Any],
                    *, trade_on_close: bool = False) -> dict[str, Any]:
    """★ **成交逻辑**对账 —— 从 bar **独立反推**每一笔的成交价，跟记录比。

    ## 为什么需要它（第三轮独立复审第 9 条）

    复审指出 `reconcile()` 的覆盖面被高估：

    > `reconcile()` 验证的是「**我记的和我算的一样**」，
    > **不是**「我算的和规则一样」。

    它**不覆盖**（而这些正是最可能出错的地方）：

    | 不覆盖的 | 错的话会怎样 |
    |---|---|
    | 跳空越过止损 ⇒ `min(止损, 开盘)` | 按止损价成交会**系统性高估** |
    | 同一根 bar 止损优先 | 取错一侧就**系统性高估** |
    | 限价 vs 开盘取优 | 取错就高估 |
    | `trade_on_close` 当天不判出场 | 我**刚修过**的一个真 bug |

    ## 覆盖面：**穷举 + 未覆盖显形**（2026-10-09 改，治 TD-05-11）

    第一版只对**入场价**与**止损价**两处 —— 而 `EXIT_*` 是**封闭枚举（7 个）**。
    「覆盖 2/7 而说『成交对账通过』」正是本仓反复出事的形态（**审不了就静默跳过**）。

    ⇒ 现在：

    | 类 | 出场原因 | 怎么对 |
    |---|---|---|
    | **可反推** | 入场 / `EXIT_STOP` | 见下两行 |
    | **可反推** | `EXIT_MA_BREAK` / `EXIT_NO_PROGRESS` / `EXIT_TIME_CAP` / `EXIT_END_OF_DATA` | 成交价 = **出场日收盘** |
    | **不可反推（显形）** | `EXIT_TARGET_FINAL` | 见 `_UNVERIFIABLE_FILLS` 的理由 —— **报出条数，不假装覆盖** |
    | **穷举守卫** | 任何**不在上表**的出场原因 | **`raise`** —— 新增出场原因时不会静默漏对账 |

    ## 本函数独立重算什么

    对**每一笔**，从 `bars` 里取那几天的行情，**按规则重推**成交价：

    ```
    入场价    = 入场日的 open（`trade_on_close` 时 = close）
    止损出场   = 出场日 low ≤ 该笔止损 ⇒ 成交价 = min(该笔止损, 出场日 open)
    收盘结算类 = 出场日 close
    ```

    止损位怎么推：**没做过部分止盈** ⇒ `initial_stop`；
    **做过** ⇒ `max(initial_stop, entry_price)`（部分止盈后移到保本）。

    ⇒ 前两条正是"跳空"与"保本止损"的落点，也是最容易写错的地方。
    """
    import numpy as _np

    o = _np.asarray(bars["open"].loc[list(dates)].to_numpy(), dtype=float)
    c = _np.asarray(bars["close"].loc[list(dates)].to_numpy(), dtype=float)
    lo = _np.asarray(bars["low"].loc[list(dates)].to_numpy(), dtype=float)
    di = {d: i for i, d in enumerate(dates)}
    si = {s: j for j, s in enumerate(symbols)}

    # ★ **穷举守卫**：出场原因是封闭枚举；出现没进任何一张表的，**报错**而不是静默漏对账。
    _known = {EXIT_STOP, *_CLOSE_SETTLED, *_UNVERIFIABLE_FILLS}
    _unknown = sorted({t.exit_reason for t in result.trades} - _known)
    if _unknown:
        raise ValueError(
            f"`reconcile_fills` 不认识这些出场原因：{_unknown}\n"
            f"⇒ 新增出场原因必须**同时**进 `_CLOSE_SETTLED`（可反推）或 "
            f"`_UNVERIFIABLE_FILLS`（显形）—— 承 R5：审不了要报错，不许静默跳过")

    bad_entry = bad_stop = checked_entry = checked_stop = 0
    checked_close = bad_close = 0
    unverifiable: dict[str, int] = {}
    examples: list[str] = []
    for t in result.trades:
        i, j = di.get(t.entry_day), si.get(t.symbol)
        if i is None or j is None:
            continue
        # ── 入场价：市价单按 open（`trade_on_close` 时按 close）──
        want = c[i, j] if trade_on_close else o[i, j]
        if _np.isfinite(want):
            checked_entry += 1
            if abs(float(want) - t.entry_price) > 1e-9:
                bad_entry += 1
                if len(examples) < 5:
                    examples.append(
                        f"{t.symbol} {t.entry_day} 入场：记录 {t.entry_price:.6f}"
                        f" vs 按规则 {float(want):.6f}")
        k = di.get(t.exit_day)
        # ── 止损出场的成交价 ──
        if t.exit_reason == EXIT_STOP:
            if k is None:
                continue
            stop = t.initial_stop
            if t.took_partial:
                stop = max(stop, t.entry_price)      # 部分止盈后移到保本
            if not (_np.isfinite(lo[k, j]) and _np.isfinite(o[k, j])):
                continue
            if lo[k, j] <= stop:                      # 真的打到了
                expect = min(stop, float(o[k, j]))    # ★ 跳空 ⇒ 取更差的
                checked_stop += 1
                if abs(expect - t.exit_price) > 1e-9:
                    bad_stop += 1
                    if len(examples) < 5:
                        examples.append(
                            f"{t.symbol} {t.exit_day} 止损：记录 {t.exit_price:.6f}"
                            f" vs 按规则 {expect:.6f}")
        # ── "收盘结算"类（③均线 / ④超时 / 末尾强平）：成交价 = **出场日收盘** ──
        elif t.exit_reason in _CLOSE_SETTLED:
            if k is None or not _np.isfinite(c[k, j]):
                continue
            expect = float(c[k, j])
            checked_close += 1
            if abs(expect - t.exit_price) > 1e-9:
                bad_close += 1
                if len(examples) < 5:
                    examples.append(
                        f"{t.symbol} {t.exit_day} {t.exit_reason}：记录 "
                        f"{t.exit_price:.6f} vs 按规则（收盘）{expect:.6f}")
        # ── 不可独立反推 ⇒ **显形**（不假装覆盖）──
        else:
            unverifiable[t.exit_reason] = unverifiable.get(t.exit_reason, 0) + 1
    return {"checked_entry": checked_entry, "bad_entry": bad_entry,
            "checked_stop": checked_stop, "bad_stop": bad_stop,
            "checked_close": checked_close, "bad_close": bad_close,
            "unverifiable": unverifiable,
            "bad": bad_entry + bad_stop + bad_close, "examples": examples}


@dataclass
class _Order:
    """一张活的入场单（市价只能当日成交；限价可挂 `valid_days` 天）。

    ★ **不再需要 `counted` 标记**（治 TD-05-17）：
    记账时机改成「**离开 `live` 且没成交的那一刻**」—— 一张单只会在那一天
    离开一次，所以"恰好计入一个类别"是**结构上成立**的，不靠一个布尔标记来兜。
    （原来记在"被挡住的那一天"，于是同一张单会被记 N 次、且"记了又成交"。）
    """

    symbol_idx: int
    arrive: int
    expire: int
    stop: float
    limit: float | None


def simulate(dates: tuple[str, ...], symbols: tuple[str, ...],
             bars: Mapping[str, "pd.DataFrame"], candidates: pd.DataFrame, *,
             strategy_name: str, strategy_params: Mapping[str, Any],
             ma_exit_level: pd.DataFrame, exit_policy: ExitPolicy,
             account: AccountPolicy,
             adr_ratio: "pd.DataFrame | None" = None,
             market_state: "pd.DataFrame | None" = None,
             exposure: ExposurePolicy | None = None) -> SimulationResult:
    """跑一次组合模拟。返回交易清单 + 净值曲线 + 跳过计数。

    ★ **入参刻意只用原生 `DataFrame` / `tuple`，不依赖任何一层的类型** ——
      `factor-layer` 的 `CrossSectionPanel` 与 `backtest` 的类型都不引用
      （承仓库铁律：**两层互不 import**）。调用方负责把面板转成这四张宽表。

    `bars`：键含 `open` / `high` / `low` / `close`，**每张宽表** index=日期、columns=标的。
    `candidates`：列 `day, symbol, stop_price`；**同一个 day 内**按 `symbol` 排序后处理
      ⇒ **可复现**（不依赖 dict 顺序）。
    """
    if account.cost_rate < 0:
        raise ValueError(f"cost_rate 不能为负：{account.cost_rate}")
    if account.max_positions < 1:
        raise ValueError(f"max_positions 必须 ≥ 1：{account.max_positions}")
    if account.max_total_exposure <= 0:
        raise ValueError(f"max_total_exposure 必须 > 0：{account.max_total_exposure}")
    missing = [k for k in ("open", "high", "low", "close") if k not in bars]
    if missing:
        raise KeyError(f"bars 缺字段 {missing}（承 P1：不静默兜底）")

    dates = tuple(dates)
    symbols = tuple(symbols)
    opens = np.asarray(bars["open"], dtype=float)
    highs = np.asarray(bars["high"], dtype=float)
    lows = np.asarray(bars["low"], dtype=float)
    closes = np.asarray(bars["close"], dtype=float)
    sym_idx = {s: j for j, s in enumerate(symbols)}
    day_idx = {d: i for i, d in enumerate(dates)}
    # ★ **对齐校验**（复审 G2）：`ma_exit_level` 必须与 `dates`/`symbols` **同序**。
    #   原来直接 `.to_numpy()` ⇒ 调用方传错行序/列序会**静默错位**
    #   （出场均线每天读到**别人的**水平线，不报错、只是全错）。
    #   当前调用方恰好对齐，所以没暴露 —— 但那是**运气**，不是保证。
    if tuple(ma_exit_level.index) != tuple(dates):
        raise ValueError(
            "ma_exit_level 的**行序**与 dates 不一致 ⇒ 出场均线会静默错位。"
            f"（前 3 行：{list(ma_exit_level.index[:3])} vs {list(dates[:3])}）"
            "承 P1：不静默兜底")
    if tuple(ma_exit_level.columns) != tuple(symbols):
        raise ValueError(
            "ma_exit_level 的**列序**与 symbols 不一致 ⇒ 出场均线会静默错位。"
            f"（前 3 列：{list(ma_exit_level.columns[:3])} vs {list(symbols[:3])}）"
            "承 P1：不静默兜底")
    ma_levels = ma_exit_level.to_numpy(dtype=float)
    # ★ **ADR 比例**（供「单日大动能下跌」用）—— 与 `ma_exit_level` 同款：
    #   由调用方算好传进来（模拟器不 import 因子层），且**必须对齐校验**
    #   （错位会静默用别人的 ADR 判离场）。
    if exit_policy.early_drop_adr is not None and adr_ratio is None:
        raise ValueError(
            "exit_policy.early_drop_adr 打开了，但没传 `adr_ratio` "
            "—— 承 P1：不静默兜底（不许'没 ADR 就当不会大动能下跌'）")
    if adr_ratio is not None:
        if tuple(adr_ratio.index) != tuple(dates):
            raise ValueError(
                "adr_ratio 的**行序**与 dates 不一致 ⇒ 大动能下跌判据会静默错位。"
                f"（前 3 行：{list(adr_ratio.index[:3])} vs {list(dates[:3])}）")
        if tuple(adr_ratio.columns) != tuple(symbols):
            raise ValueError(
                "adr_ratio 的**列序**与 symbols 不一致 ⇒ 大动能下跌判据会静默错位。"
                f"（前 3 列：{list(adr_ratio.columns[:3])} vs {list(symbols[:3])}）")
        adr_arr = adr_ratio.to_numpy(dtype=float)
    else:
        adr_arr = None
    # ★ **大盘集体性显著回撤**（§6.1 提前离场**第 4 条**，行 573）—— 与 `adr_ratio` 同款注入。
    #   判据与入场门槛**互补**：`market_ok = (spy_above_20ma > 0) & (net4 >= 0)`
    #   ⇒ 离场条件 = **两者同时不成立**（「集体性」= 广度差、「显著」= 跌破 20MA）。
    if exit_policy.market_exit and market_state is None:
        raise ValueError(
            "exit_policy.market_exit 打开了，但没传 `market_state` "
            "—— 承 P1：不静默兜底")
    if market_state is not None:
        miss = [c for c in ("net4", "spy_above_20ma")
                if c not in market_state.columns]
        if miss:
            raise ValueError(f"market_state 缺列 {miss}（承 P1：不静默兜底）")
        market_ok = ((market_state["spy_above_20ma"] > 0)
                     & (market_state["net4"] >= 0)
                     ).reindex(dates).fillna(False).to_numpy(dtype=bool)
    else:
        market_ok = None

    # 候选 → 入场单（**按 `(arrive, symbol_idx)` 排序** ⇒ 可复现，不依赖行序）
    has_limit = "limit_price" in candidates.columns
    has_valid = "valid_days" in candidates.columns
    orders: list[_Order] = []

    #: ★ **被跳过的信号**（`原因 → 笔数`）—— **唯一记账点**在 `_skip()`（治 TD-05-17）。
    skipped: dict[str, int] = {k: 0 for k in SKIP_REASONS}

    def _skip(reason: str) -> None:
        """★ **唯一的跳过记账点** —— 一个信号**恰好**计入一个类别。

        治 TD-05-17 的两种形态：
        · **重复**：原来记在「被挡住的那一天」⇒ 同一张限价单先被「仓位已满」记一次、
          挂到有效期结束时又被「限价到期」记一次；而且**记过之后又成交**的单
          也留在"被跳过"里。现在只在**离开 `live` 且没成交**的那一刻记一次。
        · **漏计**：`continue` 掉的路径（已持有该标的／开盘已跌破止损／价格缺失／
          候选越界）原来哪个计数都不进 ⇒ 现在**每条离场路径都必须**在这里分类。

        未知 `reason` ⇒ **报错**（穷举守卫，承 P1：不静默兜底）——
        与 `reconcile_fills` 里「未知出场原因 ⇒ raise」同款。
        """
        if reason not in SKIP_REASONS:
            raise ValueError(
                f"未知跳过原因 {reason!r}（合法：{sorted(SKIP_REASONS)}）"
                "—— 穷举守卫：新增离场路径必须同时在这里分类")
        skipped[reason] += 1

    for row in candidates.itertuples(index=False):
        day, symbol = getattr(row, "day"), getattr(row, "symbol")
        stop = getattr(row, "stop_price")
        i, j = day_idx.get(day), sym_idx.get(symbol)
        if i is None or j is None or not np.isfinite(stop):
            # ⚠️ 候选越界 / 止损非有限 ⇒ 原来**静默 `continue`**（哪个计数都不进）。
            _skip(SKIP_BAD_CANDIDATE)
            continue
        limit = getattr(row, "limit_price", None) if has_limit else None
        if limit is not None and not np.isfinite(limit):
            limit = None
        valid = int(getattr(row, "valid_days", 1)) if has_valid else 1
        if limit is None:
            valid = 1                       # 市价单只有一次机会
        if valid < 1:
            raise ValueError(f"valid_days 必须 ≥ 1，收到 {valid}（{symbol} @ {day}）")
        # 市价单：默认**次日开盘**；`trade_on_close` 时**当日收盘**
        arrive = i if (limit is None and account.trade_on_close) else i + 1
        # ★ **末日的信号永远等不到成交**（复审 G3）：`arrive` 已经越过数据末尾，
        #   主循环不会处理它 —— 原来**静默丢弃、且不计入任何 skipped 计数**。
        if arrive >= len(dates):
            _skip(SKIP_AFTER_END)
            continue
        orders.append(_Order(symbol_idx=j, arrive=arrive, expire=arrive + valid - 1,
                             stop=float(stop),
                             limit=None if limit is None else float(limit)))
    orders.sort(key=lambda o: (o.arrive, o.symbol_idx))
    pending: dict[int, list[_Order]] = {}
    for o in orders:
        pending.setdefault(o.arrive, []).append(o)

    exposure = exposure or StaticExposure()

    cash = account.initial_equity
    positions: dict[int, _Position] = {}
    trades: list[Trade] = []
    equity_days: list[str] = []
    equity_values: list[float] = []
    exposures: list[float] = []
    live: list[_Order] = []
    stage_by_day: list[str] = []
    default_settings = ExposureSettings(
        max_positions=account.max_positions,
        risk_fraction=account.risk_per_trade,
        exit_policy=exit_policy,
        stage="static")

    def cost_of(notional: float) -> float:
        return abs(notional) * account.cost_rate

    for i, day in enumerate(dates):
        # ★ 两个权益口径，**不能混用**：
        #   `equity_open` —— 用**开盘价**估已有持仓 ⇒ 开盘那一刻真的知道的值。
        #       **定仓与曝险检查必须用它**（否则是轻微前视：开盘时不知道今天收盘）。
        #   `equity_close` —— 用**收盘价** ⇒ 只用于**当日收盘记账**。
        equity_open = cash + sum(
            p.shares_left * (opens[i, j] if np.isfinite(opens[i, j])
                             else p.entry_price)
            for j, p in positions.items())
        equity_now = cash + sum(
            p.shares_left * (closes[i, j] if np.isfinite(closes[i, j])
                             else p.entry_price)
            for j, p in positions.items())

        # ── ⓪ 先按**当日可见**的账户状态定今天的曝险设置（无未来信息）──
        recent = trades[-RECENT_TRADES_WINDOW:]
        settings = exposure.settings(
            AccountState(
                day_index=i, day=day, equity=equity_now,
                initial_equity=account.initial_equity,
                open_positions=len(positions), n_closed=len(trades),
                total_r=float(sum(t.r_multiple for t in trades)),
                recent_r=tuple(t.r_multiple for t in recent),
                recent_reasons=tuple(t.exit_reason for t in recent)),
            default_settings)
        ep = settings.exit_policy
        stage_by_day.append(settings.stage)

        # ── ① 开盘：处理**活着的入场单**（含前几日挂着的限价单）──────────
        live.extend(pending.get(i, ()))
        keep: list[_Order] = []
        # ⚠️ **按"到达日"排，不是按 symbol 排**（复审 10.7）：
        #    按 symbol 排会让**新信号挤掉旧挂单**（旧单还没到期就先被处理、占了仓位）。
        #    按 `(arrive, symbol_idx)` 才是"先到先得"。
        for o in sorted(live, key=lambda x: (x.arrive, x.symbol_idx)):
            j = o.symbol_idx
            op, lo = opens[i, j], lows[i, j]
            # ⚠️ **按构造不可达**（`keep` 只收 `i < o.expire` 的单 ⇒ 进入本循环时
            #    必有 `o.expire >= i`）。保留它只为"任何一条离场路径都必须分类"。
            if o.expire < i:
                _skip(SKIP_EXPIRED)
                continue
            if j in positions:                              # 已持有 ⇒ 挂到到期
                if i < o.expire:
                    keep.append(o)              # 还在等仓位腾出来 ⇒ 此刻**不记账**
                else:
                    _skip(SKIP_HELD)            # 挂到最后一刻仍未成交 ⇒ 记一次
                continue
            if o.limit is None:                             # 市价单：只有一次机会
                # 默认按**开盘**；`trade_on_close` 时按**当日收盘**
                ref = closes[i, j] if account.trade_on_close else op
                if not np.isfinite(ref) or ref <= 0:
                    _skip(SKIP_NO_PRICE)        # 原来**静默 `continue`**
                    continue
                px = float(ref)
            else:                                           # 限价单：碰到才算
                if not np.isfinite(lo) or not np.isfinite(op) or lo > o.limit:
                    if i < o.expire:
                        keep.append(o)
                    else:
                        _skip(SKIP_EXPIRED)
                    continue
                px = min(o.limit, float(op))                # 开盘更优 ⇒ 按开盘
            if px <= o.stop:
                # 成交价已在止损下方 ⇒ 不进场（不是"进场即止损"）。
                # ⚠️ 原来**静默 `continue`** —— 它同样是"一个没变成交易的信号"。
                _skip(SKIP_INVALIDATED)
                continue
            # ①d ★ **仓位满了就换仓**（他 §2.2 阶段③「还想加仓 → **开新仓同时关旧仓**」，行 129）
            #   ⚠️ **原文没说关哪一笔** ⇒ 取「**R 最低**的那笔」（关强的没道理）。
            #      **这个选择是我定的**，必须显形。
            if len(positions) >= settings.max_positions and account.rotate_on_full:
                worst_j, worst_r = None, None
                for jj, pp in positions.items():
                    px_j = closes[i, jj]
                    if not np.isfinite(px_j) or pp.risk_per_share <= 0:
                        continue
                    r_j = (px_j - pp.entry_price) / pp.risk_per_share
                    if worst_r is None or r_j < worst_r:
                        worst_j, worst_r = jj, r_j
                if worst_j is not None:
                    pp = positions[worst_j]
                    px_j = float(closes[i, worst_j])
                    pp.realized += (px_j - pp.entry_price) * pp.shares_left
                    notional = px_j * pp.shares_left
                    c = cost_of(notional)
                    pp.cost_paid += c
                    cash += notional - c
                    held_days = i - int(day_idx[pp.entry_day])
                    trades.append(Trade(
                        symbol=pp.symbol, entry_day=pp.entry_day,
                        entry_price=pp.entry_price, initial_stop=pp.initial_stop,
                        exit_day=day, exit_price=px_j, shares=pp.shares_initial,
                        took_partial=pp.took_partial,
                        partial_price=pp.partial_price,
                        partial_shares=pp.partial_shares,
                        r_multiple=(pp.realized - pp.cost_paid) / pp.risk_amount,
                        return_pct=(pp.realized - pp.cost_paid)
                        / (pp.shares_initial * pp.entry_price),
                        exit_reason=EXIT_ROTATE, hold_days=held_days))
                    del positions[worst_j]
            if len(positions) >= settings.max_positions:
                if i < o.expire:
                    keep.append(o)              # 挂着等仓位 ⇒ 此刻**不记账**
                else:
                    _skip(SKIP_NO_SLOT)         # 到最后一刻仍没位置 ⇒ 记一次
                continue
            # ★ **R 的绝对值不随本金变动**（原文 §2.1 行 102：「100 万 → 105 万，R 仍是 1 万」；
            #   同节行 103：「**调整频率：每季度 / 每年一次**」）。
            #   ⇒ 分母用**初始本金**（`account.initial_equity`），**不是**当日开盘权益。
            #   ⚠️ 原来用 `equity_open` ⇒ **R 随权益浮动**：赚了就放大单笔风险、
            #      亏了就缩小 —— 那不是他的规则（他的 R 一个季度才动一次）。
            #   ⚠️ 别把它和「**用利润下注**」混为一谈：后者发生在**总曝险**那一层
            #      （`held + notional > equity_open × max_total_exposure`，见下方）——
            #      单笔 R 固定 + 总曝险随利润 ⇒ 两条**同时**成立才是原文的样子。
            shares = (account.initial_equity * settings.risk_fraction) / (px - o.stop)
            notional = shares * px
            held = sum(p.shares_left * opens[i, jj]
                       for jj, p in positions.items()
                       if np.isfinite(opens[i, jj]))
            if (held + notional) > equity_open * account.max_total_exposure:
                if i < o.expire:
                    keep.append(o)              # ★ 同上：只在"最后一刻"记一次
                else:
                    _skip(SKIP_EXPOSURE)
                continue
            c = cost_of(notional)
            cash -= notional + c
            positions[j] = _Position(
                symbol=symbols[j], entry_day=day, entry_bar=i,
                entry_price=float(px), initial_stop=float(o.stop),
                stop=float(o.stop), shares_initial=shares, shares_left=shares,
                risk_amount=shares * (px - o.stop), cost_paid=c)
        live = keep

        # ── ② 止损 → 止盈 → 均线 → 超时（**顺序即优先级**）──────────────
        for j in sorted(positions):
            p = positions[j]
            hi, lo, op, cl = highs[i, j], lows[i, j], opens[i, j], closes[i, j]
            if not np.isfinite(cl):
                continue
            hold = i - p.entry_bar

            # ★ **收盘成交 ⇒ 成交那根 bar 不能再判出场。**
            #   那根 bar 的 high/low **已经走完**（成交价就是它的收盘），
            #   拿它的 low 去判止损 = **用已经过去的信息** ⇒ 当天必被打掉。
            #   （这个 bug 是 `--free-params` 抓到的：`trade_on_close=True` 时
            #    跑出 **−97.66%**、363 笔 —— 荒谬到一眼可见，但它确实是记账错。）
            if hold == 0 and account.trade_on_close:
                continue

            # 记录"达到过的最大 R"（供"5 天无进展"判定）
            if np.isfinite(hi):
                p.peak_r = max(p.peak_r, (hi - p.entry_price) / p.risk_per_share)

            # ① 止损：★ 同一根 bar 止损与止盈都触时**判止损**（取对他不利的一侧）
            #    `ep.use_stop=False` ⇒ 跳过（对照臂：只留仓位口径，不要出场规则）
            if ep.use_stop and np.isfinite(lo) and lo <= p.stop:
                px = min(p.stop, op) if np.isfinite(op) else p.stop
                p.realized += (px - p.entry_price) * p.shares_left
                notional = px * p.shares_left
                c = cost_of(notional)
                p.cost_paid += c
                cash += notional - c
                trades.append(Trade(
                    symbol=p.symbol, entry_day=p.entry_day,
                    entry_price=p.entry_price, initial_stop=p.initial_stop,
                    exit_day=day, exit_price=float(px), shares=p.shares_initial,
                    took_partial=p.took_partial,
                    partial_price=p.partial_price,
                    partial_shares=p.partial_shares,
                    r_multiple=(p.realized - p.cost_paid) / p.risk_amount,
                    return_pct=(p.realized - p.cost_paid)
                    / (p.shares_initial * p.entry_price),
                    exit_reason=EXIT_STOP, hold_days=hold))
                del positions[j]
                continue

            # ①b ★ **单日大动能下跌 ⇒ 离场**（他 §6.1 提前离场**第 1 条**，行 570）
            #   「**单日大动能下跌（突兀走势）**」—— 他举的失败案例 **PLUG** 就是这个形态
            #   （「突破后立刻大动能拉回原点」）。
            #   判据 = 当日涨跌幅 ≤ −`early_drop_adr` × ADR（**ADR 归一**，与止损同尺度）。
            #   ⚠️ 放在止损**之后**：若当日已打穿止损，由①负责
            #      （同 bar 冲突一律取**对他不利**的一侧）。
            if (p.shares_left > 1e-12 and ep.early_drop_adr is not None
                    and adr_arr is not None and i > 0):
                pc, aj = closes[i - 1, j], adr_arr[i, j]
                if (np.isfinite(pc) and pc > 0 and np.isfinite(cl)
                        and np.isfinite(aj)
                        and (cl / pc - 1.0) <= -float(ep.early_drop_adr) * aj):
                    p.realized += (cl - p.entry_price) * p.shares_left
                    notional = cl * p.shares_left
                    c = cost_of(notional)
                    p.cost_paid += c
                    cash += notional - c
                    trades.append(Trade(
                        symbol=p.symbol, entry_day=p.entry_day,
                        entry_price=p.entry_price, initial_stop=p.initial_stop,
                        exit_day=day, exit_price=float(cl), shares=p.shares_initial,
                        took_partial=p.took_partial,
                        partial_price=p.partial_price,
                        partial_shares=p.partial_shares,
                        r_multiple=(p.realized - p.cost_paid) / p.risk_amount,
                        return_pct=(p.realized - p.cost_paid)
                        / (p.shares_initial * p.entry_price),
                        exit_reason=EXIT_EARLY_DROP, hold_days=hold))
                    del positions[j]
                    continue

            # ①c ★ **大盘集体性显著回撤 ⇒ 离场**（他 §6.1 提前离场**第 4 条**，行 573）
            #   「**大盘或所在行业发生集体性显著回撤**」—— 他的例子：买了核能股 **BWXT**，
            #   第三天**整个核能行业集体回调** ⇒ 他先离场。
            #   ⚠️ **「所在行业」那一半测不了**（票池没有行业分类）⇒ 只做了「大盘」那一半。
            #   ⚠️ 放在止损与单日大跌**之后**：系统性风险是"主动离场"，不是"被打中"。
            if (p.shares_left > 1e-12 and ep.market_exit and market_ok is not None
                    and not market_ok[i]):
                p.realized += (cl - p.entry_price) * p.shares_left
                notional = cl * p.shares_left
                c = cost_of(notional)
                p.cost_paid += c
                cash += notional - c
                trades.append(Trade(
                    symbol=p.symbol, entry_day=p.entry_day,
                    entry_price=p.entry_price, initial_stop=p.initial_stop,
                    exit_day=day, exit_price=float(cl), shares=p.shares_initial,
                    took_partial=p.took_partial,
                    partial_price=p.partial_price,
                    partial_shares=p.partial_shares,
                    r_multiple=(p.realized - p.cost_paid) / p.risk_amount,
                    return_pct=(p.realized - p.cost_paid)
                    / (p.shares_initial * p.entry_price),
                    exit_reason=EXIT_MARKET, hold_days=hold))
                del positions[j]
                continue

            # ② 止盈（部分）：跳空高开则用**更有利**的开盘价成交
            #    `ep` = 当日适用的出场规则（曝险策略可逐日改它 —— 他阶段④的"2–3 天部分获利"）
            target = p.entry_price + ep.target_r * p.risk_per_share
            if (not p.took_partial and np.isfinite(hi) and hi >= target):
                px = max(target, op) if np.isfinite(op) else target
                sold = p.shares_left * ep.partial_fraction
                # ★ 只有**真的卖了**才算"部分止盈"、才准把止损移到保本。
                #   否则 `partial_fraction=0` 会变成**白送一次止损上移**。
                if sold > 1e-12:
                    p.realized += (px - p.entry_price) * sold
                    notional = px * sold
                    c = cost_of(notional)
                    p.cost_paid += c
                    cash += notional - c
                    p.shares_left -= sold
                    p.took_partial = True
                    p.last_fill = float(px)
                    p.partial_price, p.partial_shares = float(px), sold
                    if ep.breakeven_after_partial:
                        p.stop = max(p.stop, p.entry_price)  # 止损上移到入场点

            # ②b **时间型**部分止盈（他阶段④的「2–3 天部分获利」）—— 按当日**收盘**成交
            if (not p.took_partial and ep.partial_after_days is not None
                    and hold >= ep.partial_after_days and np.isfinite(cl)
                    and p.shares_left > 1e-12):
                sold = p.shares_left * ep.partial_fraction
                if sold > 1e-12:
                    p.realized += (cl - p.entry_price) * sold
                    notional = cl * sold
                    c = cost_of(notional)
                    p.cost_paid += c
                    cash += notional - c
                    p.shares_left -= sold
                    p.took_partial = True
                    p.last_fill = float(cl)
                    p.partial_price, p.partial_shares = float(cl), sold
                    if ep.breakeven_after_partial:
                        p.stop = max(p.stop, p.entry_price)

            # ②c ★ 部分止盈把**股数减到 0** ⇒ 这笔已经**平完了**，必须收尾。
            #     ⚠️ 漏了这一步，仓位会**带着 0 股继续占着持仓位**，
            #        一直挂到 `max_hold_days` ⇒ 后面的信号**根本进不来**。
            #     （这个 bug 被敏感性扫描抓到：`partial_fraction=1.0` 那行
            #      只有 26 笔、Sharpe 0.95 —— 看起来像"参数好"，其实是**没平仓**。）
            if p.shares_left <= 1e-12:
                # ★ 成交价取**最后一次实际成交价**（`last_fill`），不是当日收盘 ——
                #   部分止盈的成交价是 `max(止盈价, 开盘)`，与收盘**不是一回事**。
                #   （记错不会影响 PnL，但会让导出的逐笔清单**对不上账**。）
                fill = p.last_fill if np.isfinite(p.last_fill) else float(cl)
                trades.append(Trade(
                    symbol=p.symbol, entry_day=p.entry_day,
                    entry_price=p.entry_price, initial_stop=p.initial_stop,
                    exit_day=day, exit_price=float(fill),
                    shares=p.shares_initial,
                    took_partial=p.took_partial,
                    partial_price=p.partial_price,
                    partial_shares=p.partial_shares,
                    r_multiple=(p.realized - p.cost_paid) / p.risk_amount,
                    return_pct=(p.realized - p.cost_paid)
                    / (p.shares_initial * p.entry_price),
                    exit_reason=EXIT_TARGET_FINAL, hold_days=hold))
                del positions[j]
                continue

            # ③ 均线破坏（他"最后一段"的规则）
            if (ep.use_ma_exit and p.shares_left > 1e-12
                    and np.isfinite(ma_levels[i, j]) and cl < ma_levels[i, j]):
                p.realized += (cl - p.entry_price) * p.shares_left
                notional = cl * p.shares_left
                c = cost_of(notional)
                p.cost_paid += c
                cash += notional - c
                trades.append(Trade(
                    symbol=p.symbol, entry_day=p.entry_day,
                    entry_price=p.entry_price, initial_stop=p.initial_stop,
                    exit_day=day, exit_price=float(cl), shares=p.shares_initial,
                    took_partial=p.took_partial,
                    partial_price=p.partial_price,
                    partial_shares=p.partial_shares,
                    r_multiple=(p.realized - p.cost_paid) / p.risk_amount,
                    return_pct=(p.realized - p.cost_paid)
                    / (p.shares_initial * p.entry_price),
                    exit_reason=EXIT_MA_BREAK, hold_days=hold))
                del positions[j]
                continue

            # ④ "5 天以上没有预想的爆发" + 防挂死的硬上限
            if p.shares_left > 1e-12:
                stalled = (hold >= ep.no_progress_days
                           and p.peak_r < ep.no_progress_min_r)
                overdue = hold >= ep.max_hold_days
                if stalled or overdue:
                    p.realized += (cl - p.entry_price) * p.shares_left
                    notional = cl * p.shares_left
                    c = cost_of(notional)
                    p.cost_paid += c
                    cash += notional - c
                    trades.append(Trade(
                        symbol=p.symbol, entry_day=p.entry_day,
                        entry_price=p.entry_price, initial_stop=p.initial_stop,
                        exit_day=day, exit_price=float(cl), shares=p.shares_initial,
                    took_partial=p.took_partial,
                    partial_price=p.partial_price,
                    partial_shares=p.partial_shares,
                        r_multiple=(p.realized - p.cost_paid) / p.risk_amount,
                        return_pct=(p.realized - p.cost_paid)
                        / (p.shares_initial * p.entry_price),
                        exit_reason=EXIT_NO_PROGRESS if stalled else EXIT_TIME_CAP,
                        hold_days=hold))
                    del positions[j]

        # ── ③ 收盘：记账 + 用今日收盘生成**明日**候选（由调用方预先算好）──
        # ⚠️ **收盘口径也要兜底**（与上面 `equity_open` 对称）。
        #    原来这里直接 `closes[i, j]` —— 任一持仓当日无收盘（停牌/缺数据）
        #    ⇒ **整条净值变 NaN** ⇒ `total_return` / `Sharpe` 全变 NaN，
        #    而且**不报错**。同一个函数里"一边兜底、一边不兜"是隐患。
        equity_now = cash + sum(
            p.shares_left * (closes[i, j] if np.isfinite(closes[i, j])
                             else p.entry_price)
            for j, p in positions.items())
        equity_days.append(day)
        equity_values.append(float(equity_now))
        exposures.append(
            float(sum(p.shares_left * closes[i, j] for j, p in positions.items())
                  / equity_now) if equity_now > 0 else 0.0)

    # 数据末尾仍持仓 → 按最后收盘价平掉（**显形**，不当作正常出场）
    last = len(dates) - 1
    cleanup_cost = 0.0
    for j in sorted(positions):
        p = positions[j]
        cl = closes[last, j]
        if not np.isfinite(cl):
            continue
        p.realized += (cl - p.entry_price) * p.shares_left
        notional = cl * p.shares_left
        p.cost_paid += cost_of(notional)
        # ★ 这笔平仓成本**必须进最终权益** —— 否则最后一天的净值会**虚高**
        #   （`equity_values[-1]` 是在这一步**之前**记的）。
        cleanup_cost += cost_of(notional)
        trades.append(Trade(
            symbol=p.symbol, entry_day=p.entry_day,
            entry_price=p.entry_price, initial_stop=p.initial_stop,
            exit_day=dates[last], exit_price=float(cl),
            shares=p.shares_initial,
                    took_partial=p.took_partial,
                    partial_price=p.partial_price,
                    partial_shares=p.partial_shares,
            r_multiple=(p.realized - p.cost_paid) / p.risk_amount,
            return_pct=(p.realized - p.cost_paid)
            / (p.shares_initial * p.entry_price),
            exit_reason=EXIT_END_OF_DATA,
            hold_days=last - p.entry_bar))

    # ★ 末尾平仓的成本要进**最终权益** —— 否则最后一天的净值虚高
    if equity_values:
        equity_values[-1] = float(equity_values[-1] - cleanup_cost)

    return SimulationResult(
        strategy=strategy_name,
        params_fingerprint=_fingerprint(dict(strategy_params)),
        trades=tuple(trades),
        equity_days=tuple(equity_days),
        equity_values=tuple(equity_values),
        skipped=dict(skipped),
        daily_exposure=tuple(exposures),
        stages=tuple(stage_by_day),
    )
