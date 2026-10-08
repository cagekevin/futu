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
EXIT_TARGET_PARTIAL = "target_partial"
EXIT_TARGET_FINAL = "target_final"
EXIT_MA_BREAK = "ma_break"
EXIT_NO_PROGRESS = "no_progress"
EXIT_TIME_CAP = "time_cap"
EXIT_END_OF_DATA = "end_of_data"


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
    skipped_no_slot: int
    skipped_exposure: int
    daily_exposure: tuple[float, ...] = field(default=())
    #: 限价单到期没成交的笔数（他的入场②「**超过两天没回撤 ⇒ 放弃**」）。
    skipped_expired: int = 0
    #: ★ **末日的信号**：`arrive` 越过数据末尾 ⇒ 永远等不到成交。
    #: 原来**静默丢弃、不计入任何计数**（复审 G3）⇒ 现在显式报出来。
    skipped_after_end: int = 0
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

    ## 本函数独立重算什么

    对**每一笔**，从 `bars` 里取那几天的行情，**按规则重推**成交价：

    ```
    入场价  = 入场日的 open（`trade_on_close` 时 = close）
    止损出场 = 出场日 low ≤ 该笔止损 ⇒ 成交价 = min(该笔止损, 出场日 open)
    ```

    止损位怎么推：**没做过部分止盈** ⇒ `initial_stop`；
    **做过** ⇒ `max(initial_stop, entry_price)`（部分止盈后移到保本）。

    ⇒ 这两条正是"跳空"与"保本止损"的落点，也是最容易写错的地方。
    """
    import numpy as _np

    o = _np.asarray(bars["open"].loc[list(dates)].to_numpy(), dtype=float)
    c = _np.asarray(bars["close"].loc[list(dates)].to_numpy(), dtype=float)
    lo = _np.asarray(bars["low"].loc[list(dates)].to_numpy(), dtype=float)
    di = {d: i for i, d in enumerate(dates)}
    si = {s: j for j, s in enumerate(symbols)}

    bad_entry = bad_stop = checked_entry = checked_stop = 0
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
        # ── 止损出场的成交价 ──
        if t.exit_reason != EXIT_STOP:
            continue
        k = di.get(t.exit_day)
        if k is None:
            continue
        stop = t.initial_stop
        if t.took_partial:
            stop = max(stop, t.entry_price)          # 部分止盈后移到保本
        if not (_np.isfinite(lo[k, j]) and _np.isfinite(o[k, j])):
            continue
        if lo[k, j] <= stop:                          # 真的打到了
            expect = min(stop, float(o[k, j]))        # ★ 跳空 ⇒ 取更差的
            checked_stop += 1
            if abs(expect - t.exit_price) > 1e-9:
                bad_stop += 1
                if len(examples) < 5:
                    examples.append(
                        f"{t.symbol} {t.exit_day} 止损：记录 {t.exit_price:.6f}"
                        f" vs 按规则 {expect:.6f}")
    return {"checked_entry": checked_entry, "bad_entry": bad_entry,
            "checked_stop": checked_stop, "bad_stop": bad_stop,
            "bad": bad_entry + bad_stop, "examples": examples}


@dataclass
class _Order:
    """一张活的入场单（市价只能当日成交；限价可挂 `valid_days` 天）。

    `counted`：**这张单是否已经被计入某个 skipped 计数**。
    ⚠️ 为什么需要它（复审 10.3）：`skipped_no_slot` / `skipped_exposure` 原来写在
       **日循环里**，而限价单被挡住后会**挂到有效期结束** ⇒ 同一张单会被**记 N 次**，
       而 `skipped_expired` 是**按单**记的 ⇒ 报告里并排的三个数**口径不同**。
       ⇒ 现在一律**按单**记一次。
    """

    symbol_idx: int
    arrive: int
    expire: int
    stop: float
    limit: float | None
    counted: bool = False


def simulate(dates: tuple[str, ...], symbols: tuple[str, ...],
             bars: Mapping[str, "pd.DataFrame"], candidates: pd.DataFrame, *,
             strategy_name: str, strategy_params: Mapping[str, Any],
             ma_exit_level: pd.DataFrame, exit_policy: ExitPolicy,
             account: AccountPolicy,
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

    # 候选 → 入场单（**按 `(arrive, symbol_idx)` 排序** ⇒ 可复现，不依赖行序）
    has_limit = "limit_price" in candidates.columns
    has_valid = "valid_days" in candidates.columns
    orders: list[_Order] = []
    #: ★ 末日的信号（`arrive` 越过数据末尾）—— 原来**静默丢弃**（复审 G3）。
    #: ⚠️ 必须在**订单循环之前**初始化（循环里就要用它计数）。
    skipped_after_end = 0
    for row in candidates.itertuples(index=False):
        day, symbol = getattr(row, "day"), getattr(row, "symbol")
        stop = getattr(row, "stop_price")
        i, j = day_idx.get(day), sym_idx.get(symbol)
        if i is None or j is None or not np.isfinite(stop):
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
        #   ⇒ 显式计数（承 P1：不许静默丢东西）。
        if arrive >= len(dates):
            skipped_after_end += 1
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
    skipped_no_slot = skipped_exposure = skipped_expired = 0
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
            if o.expire < i:                                # 超过有效期 ⇒ 放弃
                if o.limit is not None:
                    skipped_expired += 1
                continue
            if j in positions:                              # 已持有 ⇒ 挂到到期
                if i < o.expire:
                    keep.append(o)
                continue
            if o.limit is None:                             # 市价单：只有一次机会
                # 默认按**开盘**；`trade_on_close` 时按**当日收盘**
                ref = closes[i, j] if account.trade_on_close else op
                if not np.isfinite(ref) or ref <= 0:
                    continue
                px = float(ref)
            else:                                           # 限价单：碰到才算
                if not np.isfinite(lo) or not np.isfinite(op) or lo > o.limit:
                    if i < o.expire:
                        keep.append(o)
                    else:
                        skipped_expired += 1
                    continue
                px = min(o.limit, float(op))                # 开盘更优 ⇒ 按开盘
            if px <= o.stop:
                continue                # 成交价已在止损下方 ⇒ 不进场（不是"进场即止损"）
            if len(positions) >= settings.max_positions:
                if not o.counted:            # ★ 按**单**记一次，不按"单-天"
                    skipped_no_slot += 1
                    o.counted = True
                if i < o.expire:
                    keep.append(o)
                continue
            shares = (equity_open * settings.risk_fraction) / (px - o.stop)
            notional = shares * px
            held = sum(p.shares_left * opens[i, jj]
                       for jj, p in positions.items()
                       if np.isfinite(opens[i, jj]))
            if (held + notional) > equity_open * account.max_total_exposure:
                if not o.counted:            # ★ 同上
                    skipped_exposure += 1
                    o.counted = True
                if i < o.expire:
                    keep.append(o)
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

            # ② 止盈（部分）：跳空高开则按**更有利**的开盘价成交
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
        skipped_no_slot=skipped_no_slot,
        skipped_exposure=skipped_exposure,
        daily_exposure=tuple(exposures),
        skipped_expired=skipped_expired,
        skipped_after_end=skipped_after_end,
        stages=tuple(stage_by_day),
    )
