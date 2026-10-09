"""`tugboat_breakout` —— Tugboat §6.1「突破交易 2.0」的可回测实现。

需求级规格：`docs/design/03-策略规格-Tugboat突破交易2.0-2026-10-08.md`
（**逐条对照**见规格 §1.9 的缺口审计 —— 本文件按那份审计实现，不再漏条）

---

## ★ 分层：因子由**调用方传入**，本文件不 import 因子层

本文件在 `backtest/`，而因子在 `factor-layer/` —— **两层互不 import 是仓库铁律**。
⇒ `candidates(panel, factors)` 的 `factors` 由调用方算好传进来
（与 `factor-layer` 的 placebo `run_placebo(..., factors=...)` **同一个设计**）。

## ★ 选股条件一览（每条都标出处；**带 ⚠️ 的是我定的近似**）

### 他的四条件（§6.1 行 487–490）

| # | 条件 | 因子 | 阈值 |
|---|---|---|---|
| T3 | 股价在 **200 日线之上** | `ma_dist_sma200 > 0` | — |
| T4 | 盘整**靠近支撑位** | `min(|ma_dist_*|) ≤ 1 ATR` | ⚠️ 用 §11.5 的"贴近均线"口径 |
| T5 | 过去 **12 个月涨过 50%** | `ret260 ≥ 0.50` | **原文** |
| T6 | 最近**没有高动能下跌** | 近 3 日跌幅 < 2×ADR | ⚠️ 数值我定 |
| T1 | **Tight Range ≥5 天** | `range_pct10 ≤ 6%`（**不含今天**）| ⚠️ 数值我定 |
| T2 | 均线**平行或收拢** | `abs(SMA10−SMA20)/close ≤ 2%` **且收拢** | ⚠️ 数值我定 |
| T7 | **不太 Overextended** | `close/SMA50 − 1 ≤ 15%` | ⚠️ 数值我定 |
| T8 | 200MA 不向下（§11.5 的 pass 条件）| `SMA200 ≥ 20 日前` | ⚠️ 量化口径取自外部资源 |

### ⚠️ VCP 六要点（§7.1 行 701–708）—— **只在选 VCP 形态时用**（不是叠加）

> ### ★★ 这是第一版最严重的错误：**把两套并列的标准当成一套串联**
>
> | 出处 | 是什么 |
> |---|---|
> | **§6.1「Tight Range + 四条件」** | 他**通用**的突破入场条件（T1–T8）|
> | **§7.1「VCP 六要点」** | 那是 **VCP 那一个形态**的标准（A1–A6）|
>
> 他自己写得很清楚：**VCP 只是三种形态里的「中间盘整」那一种**
> （§6.1 行 494–500：高位盘整 = High Tight Flag / **中间盘整 = VCP 的 Cheat** / 低位盘整）。
>
> ⇒ **VCP 六要点不是"四条件之上再加六条"**，而是**并列的另一套（用于其中一个形态）**。
> （§7.1 的标题也是「**为什么学会 VCP 却无法持续盈利**」——
>  那是在讲 VCP 这类形态的适用市况，**不是**在给突破交易加过滤条件。）
>
> **我把两套连乘，后果是算术级的**：16 条各自单独通过率相乘 = **4.0e-10**
> ⇒ 满窗口 299,970 格 × 4e-10 = **0.0001 个候选**
> （**每条都不荒唐**，最稀的 A5 是 1.43% —— 但连乘必然归零）。
>
> ⇒ **修法**：`vcp_filter` 参数（**默认 `False`**）。
> `False` = §6.1 字面的通用条件；`True` = 只挑 VCP 那一个形态。
> **两个版本都跑**（声明为独立实验，一起过 BH）。

`vcp_filter = True` 时才启用下面这组（**VCP 形态专有**）：



| # | 条件 | 因子 | 阈值 |
|---|---|---|---|
| A1 | **150 日线之上** | `ma_dist_sma150 > 0` | — |
| A2 | **相对强度要强** | `rs_rank ≥ 0.90` | 他「一般选 **90** 以上」|
| A3 | **接近 52 周新高** | `near_52w_high ≥ 0.85` | 他「不低于 **15%**」|
| A4 | **波幅收缩 ≥3 次** | `range_pct10` 三点递减 | 他「最好**三次**或以上」|
| A5 | 收缩到最后**波动 < 1%** | **`daily_range_pct ≤ 1%`**（当日振幅）| 他「< 1%」|
| A6 | 最后配合**成交量下跌** | `vol_ratio10_50 < 1` | 他「成交量**下跌**」|

> ### ★ A5 的读法（**改过一次，如实记录**）
>
> 原文只说「股价波动 < 1%」，**没说**是"当日"还是"多日"。
>
> | 读法 | 是否成立 |
> |---|---|
> | **多日极差 ≤ 1%**（我第一版）| ⛔ **语义上就错**：10 天极差 ≤1% = 两周总共动了不到 1% —— 那不是「紧密盘整」，是**停牌** |
> | **当日振幅 ≤ 1%**（现在）| ✅ 与 §10.6 的 ADR% 同族（单日 vs 20 日均值），符合"收缩到最后"的图景 |
>
> **漏斗实测佐证**（改前）：14 条全过后剩 9 格，**A5 一过就归零**（9 → 0）。
> ⚠️ **我是在看到"归零"之后改的** —— 但**判断依据是语义**（见上表第一行：那个读法本身不成立），
> **不是"为了凑样本量放宽阈值"**。这条区别请你自行判断是否接受。

## ★ 入场与止损

**入场（他的三种，本文件实现①；②③见 `ENTER_MODES`）**：

| 方式 | 做法 |
|---|---|
| **① 突破时买入**（默认）| 今日**收盘 > 过去 `breakout_lookback` 日最高** ⇒ 次日开盘入场 |
| ② 突破后回踩买 | 突破后**当天或第二天**回踩到区间**上方**再进；超两天 ⇒ **放弃** |
| ③ 偷步买 | 还在区间内就买（**风险最高**，仅作对照）|

**止损 —— 照原文的四个候选（B3）**：取「**区间底部**」（`min(low)` over 紧区间）
（他的另外三个候选是"突破那根 K 线的底部 / 当日低点 / 前一日低点"，
 都比区间底部**高** ⇒ 我们取**更低**的那个 = 更保守）。

**止损宽度（B4）**：`signal_close − stop ≤ max_stop_adr × ADR20`，超了就**放弃这一笔**。

## ⚠️ 测不了的三条（**显形，不许 paper over**）

| 他的条件 | 为什么测不了 |
|---|---|
| **催化剂 / 叙事**（他称"**最核心**的筛选条件"）| 要人判断 |
| **日内入场**（1/5/30 分钟、开盘第一根 K 线）| 库里**没有分钟数据** |
| **SA 的情绪维度**（NAAIM / AAII / COT）| 无数据 |
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import numpy as np
import pandas as pd

import units
from strategies.tugboat_rules import (
    ALL_KEYS, BASE, RULESETS, RSI_TIGHT, VCP, check_coverage, rule_params,
)
from trade_simulator import (
    EXIT_STOP, AccountState, ExitPolicy, ExposureSettings,
)

#: 形态（他 §6.1 的三种，按「离 52 周高点多近」分）。
FORM_HIGH = "high_tight_flag"
FORM_MID = "mid_cheat"
FORM_LOW = "low_cheat"

#: 入场方式的三种（他 §6.1 行 522–528）。默认 `"breakout"` = 他的方式①。
ENTER_BREAKOUT = "breakout"
ENTER_PULLBACK = "pullback"
ENTER_ANTICIPATE = "anticipate"
ENTER_MODES = (ENTER_BREAKOUT, ENTER_PULLBACK, ENTER_ANTICIPATE)

#: ★ **阈值只有一个来源** —— 全部从 `tugboat_rules.Rule.value` **派生**
#:   （值**带出处**写在那边；这里只留"不是阈值"的参数）。
#:
#:   为什么要派生：原来 `DEFAULTS` 与 `Rule.value` **各写一份**
#:   （`rs_min` 与 `Rule(rs_rank, value=…)` 是同一个数）⇒ 改一处漏一处；
#:   而 `rule_values()` 早就是为这件事写的、却**没人调用**（治 TD-05-29 的顺带发现）。
_THRESHOLDS: Mapping[str, Any] = rule_params(BASE, VCP, RSI_TIGHT)

#: 全部阈值（**默认值就是预注册那一套**；改它 = 开新实验）。
#:
#: ⚠️ **「哪几条是我定的」不在这里手写** —— 出处是 `rules.py::Rule.source`（**数据**），
#:    报告里由 `RuleSet.summary()` **自动打印**（`run_tugboat.py` 的「规则集」那一行）。
#:    （这里**曾经**手写「6 个阈值我定的」，而实际只有 1 个 —— 治 TD-05-21。）
DEFAULTS: Mapping[str, Any] = {
    # ── 他的四条件（§6.1）──
    "tight_days": 5,                 # 他「一般 5 天或以上」
    # ── ★ 阈值**全部派生**（唯一真源 = `tugboat_rules.Rule.value`，带出处）──
    #   这里原来与 `Rule.value` **各写一份**（`rs_min` vs `Rule(rs_rank, value=…)`）
    #   ⇒ 改一处漏一处（治 TD-05-29 的顺带发现）。
    #   ⚠️ 每个阈值的**出处与理由**在 `Rule.note`，**别在这里重写**（那是第二份真相）。
    #   ⚠️ 曾经有两个**幽灵开关**（`require_converging` / `require_volume_decline`）：
    #      定义了但全仓无人引用，行为被硬编码 ⇒ 已删（同 TD-05-29）。
    **_THRESHOLDS,
    # ⚠️「最近几天没有高动能下跌」的**天数**不是阈值 ⇒ 留在参数里
    "no_dump_days": 3,
    # ── VCP 六要点（§7.1）—— 阈值已派生；这里只剩"段落长度"不是阈值 ──
    "contraction_step": 5,           # ⚠️「一段」多长——原文没给
    # ★ **VCP 六要点是否启用** —— 默认 `False`：
    #   `False` = §6.1 的**通用**突破条件（T1–T8）
    #   `True`  = 只挑 **VCP 那一个形态**（§6.1 三种形态里的"中间盘整"）
    #   见模块 docstring：**两套是并列的，连乘会归零**（实测 4.0e-10）。
    "vcp_filter": False,
    # ── 形态分界（B1：按「离 52 周高点多近」分 高／中／低）──
    #   ⚠️ 这两个数原来**硬编码在 `candidates()` 里** ⇒ 不可变更、不可审计、
    #      **报告指纹不含它**（治 TD-05-20）。现在进 `DEFAULTS`（唯一参数真源）。
    "form_high_near": 0.95,          # ≥ 此值 ⇒ 高位紧旗（⚠️ 数值我定）
    "form_low_near": 0.90,           # < 此值 ⇒ 低位（⚠️ 数值我定）
    # ── 突破触发 ──
    "breakout_lookback": 5,          # 他「区间突破」——回看长度我定
    "entry_mode": ENTER_BREAKOUT,    # 他的三种之①（②③ 见 `ENTER_MODES`）
    "pullback_days": 2,              # 他入场②：「**当天或第二天**，超过两天没回撤 ⇒ 放弃」
    "anticipate_days": 5,            # ⚠️ 入场③ 的挂单有效期 —— **原文没给**，我取紧区间长度
    # 止损位取他**四个候选**里的哪一个（见 `_stop_series` 的说明）
    "stop_basis": "breakout_low",
    # ── 止损宽度（§10.6②）──
    # ★★ **这里撤回一个结论。**
    #
    #   我第一版写的是 `max_stop_adr: None`（不设上限），理由写的是：
    #   「他的 ≤1 ADR 是**日内**口径，日线 low 天然宽几倍 ⇒ **日线做不到**」。
    #
    #   **那是错的，而且根因是我自己的一个 bug**：
    #   ```python
    #   width_ok = (close - stop) <= max_stop_adr * adr   # ✗ 美元 <= 比例
    #   ```
    #   左边是**美元**、右边是**比例**（`adr20` 中位 0.026）⇒ 阈值变成 **0.052 美元**
    #   ⇒ 552 个候选只剩 **5** 个。**我把自己的量纲错读成了"日线做不到"。**
    #
    #   正确的写法（`/close` 一次）下：
    #     · `≤1.0×ADR` → 335 / 552 通过
    #     · `≤1.5×ADR` → 490 / 552 通过
    #   而**真实止损距离的中位是 0.88 倍 ADR** —— **他的规则日线完全做得到。**
    #
    #   ⇒ 现在默认 **1.5**（他原文「控制在 **1–1.5 倍 ADR** 之内」的上界），
    #     且**单位换算只走 `units.stop_distance_adr()`**（结构上不可能再写错）。
    # ⚠️ §11.5 条件① 的**弯路留痕**（值在 `rsi_daily_change_max` / `rsi_cum_change_max`）：
    #   原文是**两个条件**（「最近 3–4 天 RSI **每日**变化 < 3，**且累计** ≤ 5」）；
    #   第一版只写了一个 `rsi.diff(3).abs() < 3`（= 3 日**累计** < 3）——
    #   既漏了"每日"那一半，又把"累计 ≤ 5"记成了"累计 < 3"（**比原文更严**，治 TD-05-09）。
    # ── ★ §7.1「判断大市动能」→ **入场门槛**（治 TD-05-30 的核心那一条）──
    #   原文：「如果大市具备动能 → VCP 等突破交易的成功率往往比较高」；
    #        「如果你总是突破失败，**可能并非 VCP 六要点有哪一点没满足**，
    #          而是你在**不适合的大市状况下**做 VCP 突破」。
    #   ⚠️ 这**不是**四阶段曝险 —— §2.2 那套是**情绪**维度（已由 `TugboatExposure` 表达），
    #      本开关管的是「**做不做**」。
    #   ⚠️ `True` 时**必须**注入市况（`attach_market_state`），否则 `candidates()` 报错
    #      （承 P1：不静默兜底 —— 不许"没市况就当市况很好"）。
    "market_gate": True,
    # ── ★ §6.1「止损**结合 SA**」的放宽档（治 TD-05-39）──
    #   原文（行 550）：「**市场开始变得波动、动能开始下降 → 不要设得太窄**」
    #   （理由同 §8.4：波动大的环境里，太窄的止损会被**正常噪音**扫出去）。
    #   ⇒ **动能差**的日子（`market_gate` 的判据不成立时），止损上限从
    #      `stop_width_adr`（1.5，§10.6② 的上界）**放宽**到本值。
    #   ⚠️ **2.5 这个数是我定的** —— 原文只说了"不要设得太窄"，没给数。
    "stop_width_adr_weak": 2.5,
    # ── ★ §6.1 提前离场**第 1 条**：「单日大动能下跌（突兀走势）」（治 TD-05-32）──
    #   判据 = 当日涨跌幅 ≤ −本值 × ADR（ADR 归一，与止损同尺度）。
    #   ⚠️ **2.0 这个数是我定的** —— 原文只说「大动能」「突兀」，没给数。
    #   ⚠️ `ExitPolicy` 的默认是 `None`（关闭），由 `run_tugboat` 用本值显式打开
    #      （见 `trade_simulator.ExitPolicy.early_drop_adr` 的理由：别静默改单测夹具）。
    "early_drop_adr": 2.0,
    # ── ★ §6.1 提前离场**第 4 条**：「大盘或所在行业发生集体性显著回撤」（治 TD-05-32）──
    #   判据 = §7.1 方法①②**同时不成立**（`spy_above_20ma <= 0` 且 `net4 < 0`）
    #   ⇒ **没有我拍的数**（「集体性」= 广度、「显著」= 跌破 20MA，两个词各对一个条件）。
    #   ⚠️ 「所在行业」那一半**测不了**（票池无行业分类）⇒ 显形，只做「大盘」。
    "market_exit": True,
    # ── ★ §2.2 阶段①④ 的**收紧档**（治 TD-05-39 的剩余两条）──
    #   原文：「阶段① 疑似见底：**止损设窄**」；「阶段④ 过度延伸：**很窄的止损**」。
    #   判据 = 四阶段里 ①④ 的**同一份阈值**（`BREADTH_WASHOUT` / `BREADTH_EUPHORIA` /
    #   `INDEX_STRETCH`，与 `TugboatExposure` **共用**）⇒ **判据不是我拍的**。
    #   ⚠️ **1.0 这个数是我定的** —— 原文只说"窄 / 很窄"，没给数。
    #      （旁证：§6.1 行 549 他自己说止损「**尽量不大于一个 ADR**」⇒ 1.0 有依据。）
    "stop_width_adr_tight": 1.0,
    # ── ★ 用**哪一套规则集**（并列，不是开关）──
    #   `"base"`      = §6.1 通用条件 + §10.6① / §8.1 选股过滤器
    #   `"vcp"`       = §7.1 VCP 六要点（**替代** base，不是叠加）
    #   `"rsi_tight"` = §11.5 RSI 紧密盘整五条
    "ruleset": "base",
}

#: 条件② 用到的五条均线距离（§11.5 原文口径）。
_MA_DIST = ("ma_dist_ema10", "ma_dist_ema20", "ma_dist_ema50",
            "ma_dist_sma150", "ma_dist_sma200")

#: 本策略**需要**的因子（调用方按它取数）。
REQUIRED_FACTORS: tuple[str, ...] = (
    "atr14", "adr20", "ret260", "rs_rank", "near_52w_high",
    "range_pct10", "daily_range_pct", "vol_ratio10_50",
    "rsi14", "atr_pct14",                       # §11.5 那一套要用
    *_MA_DIST,
)


class TugboatBreakout:
    """§6.1 突破交易 2.0（**不含**催化剂与日内入场 —— 那两条做不到）。"""

    name = "tugboat_breakout"
    required_factors = REQUIRED_FACTORS

    def __init__(self, *, exit_policy: ExitPolicy | None = None,
                 **overrides: Any) -> None:
        unknown = set(overrides) - set(DEFAULTS)
        if unknown:
            raise ValueError(
                f"未知参数 {sorted(unknown)}（可用：{sorted(DEFAULTS)}）"
                f"—— 不静默忽略，否则报告里的指纹会撒谎")
        if overrides.get("entry_mode", DEFAULTS["entry_mode"]) not in ENTER_MODES:
            raise ValueError(f"entry_mode 必须是 {ENTER_MODES}")
        self.params: dict[str, Any] = {**DEFAULTS, **overrides}
        # 兼容旧的 `vcp_filter` 布尔开关 ⇒ 映射到**并列的规则集**
        # （布尔开关表达不了"二选一"，见 `tugboat_rules` 的教训 2）
        if "vcp_filter" in overrides and "ruleset" not in overrides:
            self.params["ruleset"] = "vcp" if overrides["vcp_filter"] else "base"
        if self.params["ruleset"] not in RULESETS:
            raise ValueError(
                f"ruleset 必须是 {sorted(RULESETS)}，收到 {self.params['ruleset']!r}")
        # ★ **§6.1 提前离场第 1 条**（治 TD-05-32）：本策略**默认打开**「单日大动能下跌」，
        #   值取 `DEFAULTS["early_drop_adr"]`（**我定的数**，进报告指纹）。
        #   调用方显式传了 `exit_policy` 就**尊重它**（只在它没设这一项时补上）
        #   —— 这样 `--sensitivity` 等诊断臂的出场规则不会被静默改掉。
        _ep = exit_policy or ExitPolicy()
        if _ep.early_drop_adr is None:
            _ep = replace(_ep, early_drop_adr=float(self.params["early_drop_adr"]))
        if not _ep.market_exit:
            # ★ §6.1 提前离场第 4 条：**大盘集体性显著回撤 ⇒ 离场**（治 TD-05-32）。
            #   同样"调用方给了就尊重它"（`--sensitivity` 等诊断臂不会被静默改掉）。
            _ep = replace(_ep, market_exit=bool(self.params["market_exit"]))
        self.exit_policy = _ep
        #: 按日的**大市动能**（列含 `net4` / `spy_above_20ma`）—— **由调用方注入**
        #: （`attach_market_state`），保证本文件不依赖因子层（与 `TugboatExposure` 同款）。
        self.market_state: pd.DataFrame | None = None

    @property
    def ruleset(self):
        """当前用的那一套规则（**并列三选一**，不是 base + 开关）。"""
        return RULESETS[self.params["ruleset"]]

    def attach_market_state(self, state: pd.DataFrame) -> None:
        """注入按日的**大市动能**（§7.1 的两个方法）—— `market_gate` 的门槛用它。"""
        missing = [c for c in ("net4", "spy_above_20ma", "vol_ratio",
                               "breadth", "index_dist_200ma")
                   if c not in state.columns]
        if missing:
            raise ValueError(
                f"market_state 缺列 {missing}（承 P1：不静默兜底）"
                " —— §7.1 的两个方法：`net4`（升/跌超 4% 家数占比之差）、"
                "`spy_above_20ma`（标普是否在 20 日线之上）；"
                "§6.1 的止损维度：`vol_ratio`（票池振幅中位数的扩张比）；"
                "§2.2 阶段①④：`breadth`（50MA 之上比例）、`index_dist_200ma`")
        self.market_state = state

    def _market_ok(self, panel) -> pd.DataFrame:
        """★ §7.1 的**两个方法** → 按日的放行布尔，摊成与掩码同形状的宽表。

        | # | 原文方法（§7.1 行 755–758）| 实现 | 判据 |
        |---|---|---|---|
        | ① | Stockbee Market Monitor：**当日升超 4% / 跌超 4% 的个股数** | 票池里 `ret > +4%` 与 `ret < −4%` 的**占比之差** `net4` | **`net4 >= 0`** —— 「升超的家数**不少于**跌超的家数」。这是 Market Monitor 的**直接读法**（原文反复说「绿（升超多）和红（跌超多）**相互交错** ⇒ 非常不利」），**不是我拍的阈值** |
        | ② | **标普 500 能否维持在 20 日线之上**并平稳向上 | `spy_above_20ma` | `> 0` |

        ⚠️ 原文的 Market Monitor 用的是**绝对家数**（「跌超 4% 的股票 **> 300 个**」），
        而我们的票池只有 **270 只** ⇒ 改用**占比**（已在 `run_tugboat._market_state` 显形）。
        """
        if self.market_state is None:
            raise ValueError(
                "market_gate=True 但没注入市况 —— 先 `attach_market_state(...)`，"
                "或显式 `market_gate=False`（承 P1：不静默兜底）")
        st = self.market_state
        ok = ((st["spy_above_20ma"] > 0) & (st["net4"] >= 0)
              ).reindex(panel.dates).fillna(False).to_numpy(dtype=bool)
        return pd.DataFrame(np.repeat(ok[:, None], len(panel.symbols), axis=1),
                            index=panel.dates, columns=panel.symbols)

    def _stop_limit(self, panel) -> pd.DataFrame:
        """★ **止损宽度的上限**（按日）—— §6.1「止损**结合 SA**」（治 TD-05-39）。

        原文（行 550）：「**市场开始变得波动**、动能开始下降 → **不要设得太窄**」。

        ## ⚠️ 为什么判据是「**波动扩张**」而不是「动能差」（一次真实的错）
        我第一版拿 `market_gate` 的判据（§7.1 的动能）来放宽止损 —— **结果一点没变**：
        §7.1 的**门槛已经把"动能差的日子"整段排除了**，那些日子进不到这里
        ⇒ 放宽档**永远不可达**（两道判据用同一个市况 ⇒ 互斥）。
        而原文两处说的**不是同一个东西**：§7.1 是**动能**（做不做），
        §6.1 是「市场开始变得**波动**」—— **波动维度**。
        ⇒ 改用 `vol_ratio`（票池日振幅中位数的 20 日均 ÷ 20 天前）**> 1** = 波动在扩张。

        `market_gate=False` 时一律用 `stop_width_adr`（**不用市况就不该依赖市况**）。
        """
        base = float(self.params["stop_width_adr"])
        if not bool(self.params["market_gate"]):
            return pd.DataFrame(base, index=panel.dates, columns=panel.symbols)
        weak = float(self.params["stop_width_adr_weak"])
        tight = float(self.params["stop_width_adr_tight"])
        st = self.market_state
        if st is None:
            raise ValueError(
                "market_gate=True 但没注入市况 —— 先 `attach_market_state(...)`，"
                "或显式 `market_gate=False`（承 P1：不静默兜底）")
        idx = st.reindex(panel.dates)
        # ★ **波动在扩张** ⇒ 放宽（`> 1` 是"扩张"的直接读法，不是我拍的阈值）
        expanding = (idx["vol_ratio"] > 1.0).fillna(False).to_numpy(dtype=bool)
        per_day = np.where(expanding, weak, base)
        # ★ **§2.2 阶段①④ ⇒ 收紧**（治 TD-05-39 的剩余两条）——
        #   原文：「阶段① 疑似见底：小注尝试、**止损设窄**、不成功就快速认错」；
        #        「阶段④ 过度延伸：压低曝险 + 节奏变快（**很窄的止损**、2–3 天部分获利）」。
        #   ⚠️ **档位本身在模拟器侧**（依赖 `AccountState`），策略层拿不到 ——
        #      但四阶段的**判据是市场状态**，那两个列 `market_state` 里本来就有
        #      ⇒ 这里用**同一份阈值常量**（`BREADTH_WASHOUT` / `BREADTH_EUPHORIA` /
        #      `INDEX_STRETCH`，与 `TugboatExposure` **共用**，不是各写一份）。
        #   优先级：**收紧覆盖放宽**（阶段①④ 是主动的短期状态；波动扩张是环境背景）。
        washout = (idx["breadth"] < BREADTH_WASHOUT).fillna(False).to_numpy(dtype=bool)
        euphoria = ((idx["breadth"] > BREADTH_EUPHORIA)
                    | (idx["index_dist_200ma"] > INDEX_STRETCH)
                    ).fillna(False).to_numpy(dtype=bool)
        per_day = np.where(washout | euphoria, tight, per_day)
        return pd.DataFrame(np.repeat(per_day[:, None], len(panel.symbols), axis=1),
                            index=panel.dates, columns=panel.symbols)

    # ── 内部：从 `factors` 里取一张宽表（**缺就报错**，不静默兜底）──────

    @staticmethod
    def _get(factors: Mapping[str, pd.DataFrame], name: str) -> pd.DataFrame:
        if name not in factors:
            raise KeyError(
                f"缺少因子 {name!r}（本策略需要 {REQUIRED_FACTORS}）；"
                f"现有：{sorted(factors)} —— 承 P1：不静默兜底")
        return factors[name]

    # ── 每日选股（**全部向量化 + 只用 ≤ 当日的信息**）────────────────────

    def _range_edges(self, panel):
        """紧区间的**上下沿**（都取**今天之前**的 `tight_days` 天）—— 两处共用，防分叉。

        · 上沿 = 「区间突破」的基准线（也作入场②的**回踩挂单价**）
        · 下沿 = **止损位**的候选之一（也作入场③的**偷步挂单价**）
        """
        n = int(self.params["tight_days"])
        high, low = panel.field("high"), panel.field("low")
        return (high.shift(1).rolling(n).max(), low.shift(1).rolling(n).min())

    def _stop_series(self, panel) -> pd.DataFrame:
        """止损位 —— 他给了**四个候选**（§6.1 行 526），这里四个都能跑。

        | `stop_basis` | 含义 |
        |---|---|
        | **`breakout_low`（默认）** | 「**确认突破那根 K 线的底部**」= 信号日的 `low` |
        | `prior_low` | 「**前一日低点**」|
        | `range_bottom` | 「**区间底部**」= 紧区间内的最低 `low` |
        | `range_mid` | ⚠️ 原文没有，加它是当**上界**（更紧的止损 = 更高赔率）|

        ## ★ 为什么默认取 `breakout_low`（我先选错了，如实记录）

        第一版我选了**区间底部**，理由是"更保守"。**那是错的**：

        > 紧密盘整（T1 要求 5 日区间 ≤6%）的票 **ADR 只有 2–3%**，
        > 而区间底部离突破点 ≈ 区间高度（**≈6%**）⇒ 止损 ≈ **3–4 个 ADR**，
        > **远超他明说的「≤1 个 ADR」**。

        ⇒ **「≤1 个 ADR」这句话本身就在指认止损位**。
        实测佐证：用区间底部时，"止损宽度 ≤2×ADR"这一条把 299 个候选砍到 **1** 个。
        """
        basis = self.params["stop_basis"]
        low = panel.field("low")
        if basis == "breakout_low":
            return low
        if basis == "prior_low":
            return low.shift(1)
        prior_hi, prior_lo = self._range_edges(panel)
        if basis == "range_bottom":
            return prior_lo
        if basis == "range_mid":
            return (prior_hi + prior_lo) / 2.0
        raise ValueError(
            f"未知 stop_basis={basis!r}（可用：breakout_low / prior_low / "
            f"range_bottom / range_mid）—— 承 P1：不静默兜底")

    def _impl_masks(self, panel, factors: Mapping[str, pd.DataFrame],
                    ) -> dict[str, pd.DataFrame]:
        """算出**所有可能用到的**条件的掩码（**含今天**的版本，按 `key` 索引）。

        ## ★ 两个刻意的设计

        ### ① 「看今天 / 看昨天」**不在这里决定**

        这里一律算**含今天**的版本；"看昨天"由 `Rule.as_of` 在 `_masks` 里
        **统一** `shift(1)`。⇒ "这条看哪天"只有**一处**决定（登记表里），
        不散在各处 `shift(1)` 里（散着写就是我出 F2 那类错的方式）。

        ### ② 单位换算**只走 `units.py`**

        `stop_width` 那条必须用 `units.stop_distance_adr()` ——
        我写过一个 `(close - stop) <= max_stop_adr * adr`（**美元 ≤ 比例**），
        把 552 个候选砍到 5 个，还**误读成"日线做不到"**。
        ⇒ 现在唯一的换算式在 `units.py`，这里**只准调它**。
        """
        p = self.params
        atr = self._get(factors, "atr14")
        adr = self._get(factors, "adr20")
        ret260 = self._get(factors, "ret260")
        rs = self._get(factors, "rs_rank")
        near_high = self._get(factors, "near_52w_high")
        rng = self._get(factors, "range_pct10")
        daily_rng = self._get(factors, "daily_range_pct")
        volr = self._get(factors, "vol_ratio10_50")
        rsi = self._get(factors, "rsi14")
        atr_pct = self._get(factors, "atr_pct14")
        ma = {n: self._get(factors, n) for n in _MA_DIST}

        close, high_f, low_f = (panel.field("close"), panel.field("high"),
                                panel.field("low"))
        stop = self._stop_series(panel)

        # ── 区间几何（**含今天**；"看昨天"交给 `Rule.as_of`）──
        n = int(p["tight_days"])
        hi_n, lo_n = high_f.rolling(n).max(), low_f.rolling(n).min()

        # ── 均线 ──
        s10, s20 = close.rolling(10).mean(), close.rolling(20).mean()
        gap = (s10 - s20).abs()
        # ★ 从 `ma_dist_sma200` 反推 **200MA 本身**（T8 曾经比的是"距离"，是错的）
        sma200 = close - ma["ma_dist_sma200"] * atr

        # ── §11.4 口径的"过度延伸"：距 50MA 的 **ATR 倍数** ──
        #    （`ma_dist_ema50` 本身就是 ATR 归一距离 ⇒ 直接比，不用换算）
        #    ⚠️ 我第一版写的是"距 50MA ≤15% 价格" —— **那是另一个东西**
        overext = ma["ma_dist_ema50"]

        # ── 距最近一条均线的 ATR 距离（T4 / §11.5 条件② 共用）──
        near = ma[_MA_DIST[0]].abs()
        for name in _MA_DIST[1:]:
            near = np.minimum(near, ma[name].abs())

        # ── §11.5 条件①（**照原文拆成两条**，治 TD-05-09）──
        #   「最近 3–4 天 RSI **每日**变化 < 3，**且累计** ≤ 5」
        rsi_chg_daily = rsi.diff(1).abs().rolling(3).max()   # 最近 3 天的**最大单日**变化
        rsi_chg_cum = rsi.diff(3).abs()                      # 3 日**累计**变化

        # ── VCP「收缩 ≥3 次」：在同一条因子上取 k 个递减检查点 ──
        step = int(p["contraction_step"])
        k = int(p["contractions_min"])
        shrinking = pd.DataFrame(True, index=rng.index, columns=rng.columns)
        for t in range(k - 1):
            shrinking &= rng.shift(t * step) < rng.shift((t + 1) * step)

        lb = int(p["breakout_lookback"])

        # ★ **「贴近均线」的判据只有一份**（治 TD-05-19）：
        #   BASE 的 T4（`near_support`）与 RSI_TIGHT 的 ②（`near_ma`）**是同一个表达式**
        #   （`near` = 到最近一条均线的 ATR 距离）—— 原来两处各写一遍 ⇒ 改一处漏一处。
        #   ⇒ 在这里算一次，两个 key 引用**同一份**。
        near_ok = near <= float(p["near_ma_max_atr"])

        return {
            # ── §6.1 通用条件 ──
            "tight_range": (hi_n - lo_n) / close <= float(p["tight_range_max"]),
            # ★ **原文是「或」，不是「且」**（§6.1 行 493：「均线平行**或**开始收拢」）——
            #   原来写成 `&`（必须同时"够平"且"在收拢"）⇒ 比原文更严。
            #   漏斗实测：它是**最大的一刀**（砍掉 36,354 格）；改回「或」⇒
            #   候选 33→63、笔数 29→48、期望 R −0.124→**+0.049**（治 TD-05-29）。
            "ma_converge": (gap / close <= float(p["ma_converge_max"]))
                           | (gap <= gap.shift(n)),
            "above_200ma": ma["ma_dist_sma200"] > 0,
            "near_support": near_ok,
            "rise_12m": ret260 >= float(p["rise_12m_min"]),
            "no_dump": (close / close.shift(1) - 1.0).rolling(
                int(p["no_dump_days"])).min() > -float(p["no_dump_adr"]) * adr,
            "not_overextended": overext <= float(p["overextend_max"]),
            "ma200_rising": sma200 >= sma200.shift(20),
            # ── 他的两个**选股过滤器**（第一版完全没实现）──
            "adr_floor": adr >= float(p["adr_floor"]),
            "rs_rank": rs >= float(p["rs_min"]),
            # ── §10 指标层：两条**筛选器**（治 TD-05-38 的前两条）──
            #   ① §10.8②「RSI > 50 = 突破交易的核心确认讯号」—— `rsi_above_50` 的实现在
            #      下面 §11.5 那一段（**同一份**，`RSI_TIGHT` 也用它）；这里只是让 `BASE` 也能取到它。
            #   ② §10.6 进阶「ADR% 从 5% 收缩到 2% = 突破前兆」—— 比较窗口 `adr_contract_days`
            #      是**我定的**（原文只说了 ADR 自己的窗口设 3 天更敏感）。
            #   ⚠️ §10.2「多头排列」试过并**撤回**（与 T2 的"均线收拢"语义互斥 ⇒ 候选归零），
            #      证据留在 `tugboat_rules.BASE` 的注释里。
            "adr_contracting": adr < adr.shift(int(p["adr_contract_days"])),
            # ── 突破触发 ──
            "breakout": close > high_f.shift(1).rolling(lb).max(),
            # ── 止损宽度：★ **单位换算只走 `units.py`** ──
            #    信号日用 `close` 近似入场价（成交在次日开盘）
            # ★ 上限**按日**取（§6.1「止损结合 SA」）：动能差 ⇒ 放宽到 `stop_width_adr_weak`
            "stop_width": units.stop_distance_adr(
                close, stop, close, adr).le(self._stop_limit(panel)),
            # ── §7.1 VCP 六要点 ──
            "above_150ma": ma["ma_dist_sma150"] > 0,
            "near_52w_high": near_high >= float(p["near_high_min"]),
            "contractions": shrinking,
            "final_range": daily_rng <= float(p["final_range_max"]),
            "volume_decline": volr < float(p["volume_decline_max"]),
            # ── §11.5 RSI 紧密盘整 ──
            "rsi_change_daily": rsi_chg_daily < float(p["rsi_daily_change_max"]),
            "rsi_change_cum": rsi_chg_cum <= float(p["rsi_cum_change_max"]),
            "atr_pct_floor": atr_pct > float(p["atr_pct_min"]),
            "rsi_above_50": rsi > float(p["rsi_min"]),
            "near_ma": near_ok,          # ★ 与 `near_support` **同一份**（治 TD-05-19）
        }

    def _masks(self, panel, factors: Mapping[str, pd.DataFrame],
               ruleset=None, include_optional: bool = False,
               ) -> list[tuple[str, pd.DataFrame]]:
        """按 `RuleSet` 取条件掩码 —— **`candidates` 与 `diagnose` 共用**（防分叉）。

        ★ **覆盖检查在这里**（承 R5：审计必须穷举）：
          规则里声明了但没实现 ⇒ 报错；实现了但**任何规则集里都没登记** ⇒ 报错。
          （上一版的对账工具用 `if k in prod` 静默跳过，**恰好漏掉唯一有 bug 的那条**。）

        ★ `include_optional` —— 原文写「**一般 / 最好**」的那些条件（`Rule.optional`）：
          · `False`（默认，`candidates` 用）⇒ **跳过**：它们是**偏好**，不参与排除
            （原文：「他**一般**选 90 以上」「**最好**不低于 15%」「**最好**三次或以上」）；
          · `True`（`diagnose` 用）⇒ 全印出来 —— **他看的是六样，一样不少**。
        """
        rs = ruleset if ruleset is not None else self.ruleset
        impl = self._impl_masks(panel, factors)
        check_coverage(rs, set(impl), all_declared=ALL_KEYS)
        out = []
        for r in rs.rules:
            if r.optional and not include_optional:
                continue
            m = impl[r.key]
            if r.as_of == "yesterday":
                m = m.shift(1)          # ★ "看哪天"由登记表决定，只有这一处
            out.append((r.label, m.fillna(False)))
        return out

    def candidates(self, panel, factors: Mapping[str, pd.DataFrame],
                   ) -> pd.DataFrame:
        """返回 `day, symbol, stop_price, form, limit_price, valid_days`。

        `day` = **信号日**；成交方式由 `entry_mode` 决定（他的三种入场）：

        | `entry_mode` | 怎么成交 | 出处 |
        |---|---|---|
        | `breakout`（默认）| **市价**，次日开盘 | 他入场①「日线波幅扩张突破时入场」|
        | `pullback` | **限价挂在上沿**，有效期 `pullback_days`（**2**）| 他入场②：「突破后回踩买…**超过两天没回撤 ⇒ 放弃**」|
        | `anticipate` | **限价挂在下沿**（还在区间内就买），有效期 `anticipate_days` | 他入场③「偷步买」（**他明说这方式风险最高**）|

        ⚠️ **三种是"备选"，不是"叠加"** —— 同时开三种会把**同一笔设置数三次**。
        """
        p = self.params
        masks = self._masks(panel, factors)
        cond = masks[0][1]
        for _name, m in masks[1:]:
            cond = cond & m
        # ★ **§7.1 的入场门槛**（治 TD-05-30 的核心那一条）：市况不对 ⇒ **不做突破**。
        #   这是「**做不做**」，与 `TugboatExposure` 的「做几笔」（§2.2 四阶段，情绪维度）**两件事**。
        if bool(p["market_gate"]):
            cond = cond & self._market_ok(panel)

        close = panel.field("close")
        stop = self._stop_series(panel)
        near_high = self._get(factors, "near_52w_high")
        prior_hi, prior_lo = self._range_edges(panel)

        # ── 形态分类（B1）：按"离 52 周高点多近" ──
        # ★ 分界值来自 `DEFAULTS`（治 TD-05-20）—— 原来 0.95 / 0.90 硬编码在这里，
        #   既不可变更、不可审计，也**不进报告指纹**（改了没人知道）。
        form = pd.DataFrame(FORM_MID, index=close.index, columns=close.columns)
        form = form.where(near_high < float(p["form_high_near"]), FORM_HIGH)
        form = form.where(near_high >= float(p["form_low_near"]), FORM_LOW)

        stack = cond.stack()
        hit = stack[stack.fillna(False)]
        cols = ["day", "symbol", "stop_price", "form",
                "limit_price", "valid_days"]
        if hit.empty:
            return pd.DataFrame(columns=cols)

        mode = p["entry_mode"]
        if mode == ENTER_PULLBACK:
            limit_src = prior_hi
            valid = int(p["pullback_days"])
        elif mode == ENTER_ANTICIPATE:
            limit_src = prior_lo
            valid = int(p["anticipate_days"])
        else:
            limit_src, valid = None, 1        # 市价（`valid_days` 被模拟器忽略）

        return pd.DataFrame({
            "day": [d for d, _ in hit.index],
            "symbol": [s for _, s in hit.index],
            "stop_price": [float(stop.loc[d, s]) for d, s in hit.index],
            "form": [form.loc[d, s] for d, s in hit.index],
            "limit_price": [None if limit_src is None
                            else float(limit_src.loc[d, s]) for d, s in hit.index],
            "valid_days": [valid] * len(hit),
        })

    def diagnose(self, panel, factors: Mapping[str, pd.DataFrame]) -> dict[str, int]:
        """**漏斗计数** —— 回答"为什么一只都选不出来"。

        逐个条件**累计**统计剩余格数；掉得最多的那一步就是卡点。
        ⚠️ 只用于诊断，**不进判决**。
        """
        masks = self._masks(panel, factors)          # 筛选链（**不含** optional）
        keep = masks[0][1].fillna(False)
        out = {"eligible": int(keep.to_numpy().sum())}
        for name, m in masks[1:]:
            keep = keep & m.fillna(False)
            out[f"after:{name}"] = int(keep.to_numpy().sum())
        # ★ **观察项**（原文写「一般 / 最好」的偏好）：单独报**单独通过率**，
        #   **不并入累计** —— 否则漏斗会显示一个"其实不参与排除"的条件在砍人，
        #   读者会以为它是门槛（那正是 `optional` 要修掉的东西）。
        soft_labels = {r.label for r in self.ruleset.rules if r.optional}
        for name, m in self._masks(panel, factors, include_optional=True):
            if name in soft_labels:
                out[f"soft:{name}"] = int(m.fillna(False).to_numpy().sum())
        return out

    def ma_exit_level(self, panel, factors: Mapping[str, pd.DataFrame],
                      ) -> pd.DataFrame:
        """「收盘跌破 **10 日或 20 日均线**」（他 §6.1 的"最后一段"规则）。

        ⚠️ **均线口径的读法**：他说"10 日或 20 日均线"**没指明** SMA 还是 EMA。
           本文件取 **EMA** —— 依据是 §11.5 里他把短中期一律写成 `EMA`
           （"贴近 10EMA / 20EMA / 50EMA"），而长期才写 `SMA`。
           **这是我的读法，必须显形。**

        实现：不必反推均线值 —— `close < MA10` ⟺ `ma_dist_ema10 < 0`
        ⇒ 等价水位 = `close − min(dist10, dist20) × ATR14`。
        """
        d10 = self._get(factors, "ma_dist_ema10")
        d20 = self._get(factors, "ma_dist_ema20")
        atr = self._get(factors, "atr14")
        return panel.field("close") - np.minimum(d10, d20) * atr


# ══════════════════════════════════════════════════════════════════════════
# 曝险四阶段（他的 §2.2）—— 他说这一块「占 7–8 成重要性」
# ══════════════════════════════════════════════════════════════════════════

#: 阶段的阈值与动作（**冻结** —— 承规格 §11.1 的 A6'：状态依赖策略会产生反馈环，
#: 规则必须写死在契约里，且报告要画出档位时间序列让人看得见）。
#:
#: ⚠️ **阈值全是我定的**（原文只给方向："情绪低落"、"极度乐观"、"到上限"），
#: 且**状态只能用代理**（见模块 docstring 的"做不到"清单）。
BREADTH_WASHOUT = 0.20     # 「疑似见底」：§7.3 条件④ 原话「50 日线以上比例 **< 20%**」
BREADTH_EUPHORIA = 0.80    # 「过度延伸」：绝大多数票都在 50 日线上
INDEX_STRETCH = 0.25       # 「过度延伸」：指数高于 200 日线 **25%** 以上
STALL_WINDOW = 10          # 看最近多少笔成交
STALL_STOP_RATIO = 0.6     # 止损占比 ≥ 此值 ⇒ **降档**（他："止损密集被打中 ⇒ 果断降曝险"）
HOT_WIN_RATIO = 0.5        # 近期胜率 ≥ 此值 ⇒ 视为"交易开始顺手"（阶段②→③）

STAGE_WASHOUT = "①疑似见底"
STAGE_RECOVER = "②动能恢复"
STAGE_FULL = "③到上限"
STAGE_EUPHORIA = "④过度延伸"
STAGE_DOWNSHIFT = "+降档"

#: 一个"快节奏"的出场规则 —— 他阶段④：「压低曝险 + 节奏变快（很窄的止损、**2–3 天部分获利**）」。
_FAST_EXIT_PARTIAL_DAYS = 2


class TugboatExposure:
    """他的 §2.2「曝险四阶段」。

    ## ★ 关键设计（规格 §11.1 的定论，**不是实现细节**）

    | 项 | 定论 |
    |---|---|
    | 调什么 | **`max_positions`（暴露多少笔），不调 R** —— 他自己说「R 的绝对值不随本金变动」 |
    | 状态从哪来 | **由调用方按日传入**（`market_state`），因为模拟器**不许 import 因子层** |
    | 降档依据 | **自己的近期成交结果**（止损密度）—— 他原话就是"止损密集被打中" |
    | 反馈环 | 规则**写死在这里**；每天的档位**记进结果**，报告会画时间序列 |

    ⚠️ **做不到的**：情绪维度（NAAIM/AAII/COT）无数据 ⇒ ①④ 只能靠**宽度 + 指数偏离**代理。
    """

    def __init__(self, *, base_max_positions: int = 5,
                 breadth_washout: float = BREADTH_WASHOUT,
                 breadth_euphoria: float = BREADTH_EUPHORIA,
                 index_stretch: float = INDEX_STRETCH,
                 stall_window: int = STALL_WINDOW,
                 stall_stop_ratio: float = STALL_STOP_RATIO,
                 hot_win_ratio: float = HOT_WIN_RATIO,
                 use_slots: bool = True, use_exit: bool = True) -> None:
        # ★ **两个组成部分可以单独关掉** —— 这是为了把"四阶段"**拆成四臂**测
        #   （第三轮独立复审第 5 条）。
        #
        #   复审把两个作用**拆开**跑过，结论是：
        #     只调 `max_positions` +2.29%｜只改阶段④出场规则 **−4.69%**｜
        #     两个一起 +4.59%｜都不做 −0.86%
        #   ⇒ **两个组成部分单独都小或负，合起来才 +4.59%** ⇒
        #     ★ **这是交互项主导，不是"四阶段有正贡献"。**
        #
        #   而我当时把它列进「**机制级结论（幅度大、方向一致）**」——
        #   **恰好搞反了**：交互项主导是**不稳定**的标志，不是稳健的标志
        #   （`A×B` 交互产生的效应，**换个窗口几乎必然翻转**）。
        #
        #   ⇒ 所以这两个开关的作用不是"多一个参数"，是**让拆解成为可能**。
        self.use_slots = bool(use_slots)
        self.use_exit = bool(use_exit)
        self.base_max_positions = int(base_max_positions)
        self.breadth_washout = float(breadth_washout)
        self.breadth_euphoria = float(breadth_euphoria)
        self.index_stretch = float(index_stretch)
        self.stall_window = int(stall_window)
        self.stall_stop_ratio = float(stall_stop_ratio)
        self.hot_win_ratio = float(hot_win_ratio)
        #: 每日市场状态（索引 = 日期，列含 `breadth` / `index_dist_200ma`）。
        #: **由调用方注入**（`attach_market_state`）—— 保证本文件不依赖因子层。
        self.market_state: pd.DataFrame | None = None

    def attach_market_state(self, state: pd.DataFrame) -> None:
        """注入按日的市场状态（列：`breadth`、`index_dist_200ma`）。"""
        missing = [c for c in ("breadth", "index_dist_200ma")
                   if c not in state.columns]
        if missing:
            raise ValueError(
                f"market_state 缺列 {missing}（承 P1：不静默兜底）")
        self.market_state = state

    # ── 主逻辑 ──────────────────────────────────────────────────────────

    def settings(self, state: AccountState, default: ExposureSettings,
                 ) -> ExposureSettings:
        day = self._day_of(state)
        breadth, stretch = self._state_of(day)

        # ① 档位（顺序即优先级 —— 写死的，不是"看情况"）
        if breadth < self.breadth_washout:
            stage, cap = STAGE_WASHOUT, 1
        elif breadth > self.breadth_euphoria or stretch > self.index_stretch:
            stage, cap = STAGE_EUPHORIA, 2
        elif self._is_hot(state):
            stage, cap = STAGE_FULL, self.base_max_positions
        else:
            stage, cap = STAGE_RECOVER, max(2, self.base_max_positions - 2)

        # ② **降档**：近期"止损密集被打中" ⇒ 果断降曝险（他原话）
        if self._is_stalled(state):
            cap = max(1, cap - 1)
            stage += STAGE_DOWNSHIFT

        # ★ 拆解开关：关掉哪一半，就回退到"不做四阶段"那一半
        if not self.use_slots:
            cap = default.max_positions

        # ③ 出场规则随档位变（只有④"节奏变快"）
        ep = default.exit_policy
        if self.use_exit and stage.startswith(STAGE_EUPHORIA):
            ep = replace(ep, partial_after_days=_FAST_EXIT_PARTIAL_DAYS)

        return ExposureSettings(max_positions=cap,
                                risk_fraction=default.risk_fraction,
                                exit_policy=ep, stage=stage)

    # ── 判定用的三个小函数（都可单独测）────────────────────────────────

    def _day_of(self, state: AccountState) -> str | None:
        """★ 查市场状态一律用 **`state.day`（日期字符串）**。

        ⚠️ 曾经写成 `self.market_state.index[state.day_index]` ——
           那隐含"**市场状态的行序 = 模拟的日序**"这个**没有任何东西保证**的前提。
           一旦调用方传进来的 `market_state` 顺序不同（比如按日期倒序、
           或者缺了几天），档位会**安静地错位**：不报错、不抛异常，
           只是**每天读到别人的宽度** ⇒ 四阶段全错。
        """
        return state.day if state.day else None

    def _state_of(self, day: str | None) -> tuple[float, float]:
        if day is None or self.market_state is None or day not in self.market_state.index:
            # 没有状态 ⇒ 取**中性**（不假装是见底也不假装是亢奋）
            return 0.5, 0.0
        row = self.market_state.loc[day]
        breadth = float(row["breadth"])
        stretch = float(row["index_dist_200ma"])
        if not np.isfinite(breadth):
            breadth = 0.5
        if not np.isfinite(stretch):
            stretch = 0.0
        return breadth, stretch

    def _is_hot(self, state: AccountState) -> bool:
        """近期交易是否"顺手"（他阶段②→③ 的依据：「你的交易开始顺手」）。"""
        recent = state.recent_r[-self.stall_window:]
        if not recent:
            return False
        return float(np.mean([1.0 if r > 0 else 0.0 for r in recent])) >= self.hot_win_ratio

    def _is_stalled(self, state: AccountState) -> bool:
        """他 §2.2 的降曝险触发：**突破失败 / 止损密集被打中**。

        ⚠️ 只看**已平仓**的成交（无未来信息）。样本不足时**不降档**（不猜）。
        """
        reasons = state.recent_reasons[-self.stall_window:]
        if len(reasons) < self.stall_window:
            return False
        n_stop = sum(1 for r in reasons if r == EXIT_STOP)
        return (n_stop / len(reasons)) >= self.stall_stop_ratio


#: 注册（照 `strategies/__init__.py` 的写法；见该文件的说明）。
__all__ = [
    "DEFAULTS", "ENTER_MODES", "REQUIRED_FACTORS",
    "TugboatBreakout", "TugboatExposure",
]
