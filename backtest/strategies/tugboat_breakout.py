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

from typing import Any, Mapping

import numpy as np
import pandas as pd

from trade_simulator import ExitPolicy

#: 形态（他 §6.1 的三种，按「离 52 周高点多近」分）。
FORM_HIGH = "high_tight_flag"
FORM_MID = "mid_cheat"
FORM_LOW = "low_cheat"

#: 入场方式的三种（他 §6.1 行 522–528）。默认 `"breakout"` = 他的方式①。
ENTER_BREAKOUT = "breakout"
ENTER_PULLBACK = "pullback"
ENTER_ANTICIPATE = "anticipate"
ENTER_MODES = (ENTER_BREAKOUT, ENTER_PULLBACK, ENTER_ANTICIPATE)

#: 全部阈值（**默认值就是预注册那一套**；改它 = 开新实验）。
#: 带 ⚠️ 的是**原文没给数、由我定**的 —— 报告里必须显形。
DEFAULTS: Mapping[str, Any] = {
    # ── 他的四条件（§6.1）──
    "tight_days": 5,                 # 他「一般 5 天或以上」
    "tight_range_max": 0.06,         # ⚠️「够紧」——数值我定
    "ma_converge_max": 0.02,         # ⚠️「平行或收拢」——数值我定
    "require_converging": True,      # 他「或**开始收拢**」（不只是平行）
    "near_ma_max_atr": 1.0,          # 条件②（§11.5「误差 ≤ 1 个 ATR」）
    "rise_12m_min": 0.50,            # 他「过去 12 个月至少涨过 50%」
    "no_dump_days": 3,               # ⚠️「最近几天没有高动能下跌」——天数我定
    "no_dump_adr": 2.0,              # ⚠️「高动能」的量级我定
    "overextend_max": 0.15,          # ⚠️「不要太 Overextended」——数值我定
    # ── VCP 六要点（§7.1）──
    "above_150ma": True,             # A1
    "rs_min": 0.90,                  # A2（他「90 以上」）
    "near_high_min": 0.85,           # A3（他「不低于 15%」）
    "contractions_min": 3,           # A4（他「最好三次或以上」）
    "contraction_step": 5,           # ⚠️「一段」多长——原文没给
    "final_range_max": 0.01,         # A5（他「< 1%」）
    "require_volume_decline": True,  # A6
    # ★ **VCP 六要点是否启用** —— 默认 `False`：
    #   `False` = §6.1 的**通用**突破条件（T1–T8）
    #   `True`  = 只挑 **VCP 那一个形态**（§6.1 三种形态里的"中间盘整"）
    #   见模块 docstring：**两套是并列的，连乘会归零**（实测 4.0e-10）。
    "vcp_filter": False,
    # ── 突破触发 ──
    "breakout_lookback": 5,          # 他「区间突破」——回看长度我定
    "entry_mode": ENTER_BREAKOUT,    # 他的三种之①
    # ── 止损宽度（B4）──
    # ⚠️ **默认 `None` = 不设宽度上限** —— 这是一个**做不到**的显形，不是省略：
    #
    #   他的原话是「止损**尽量不大于 1 个 ADR**」。但那条规则是**针对日内止损**的
    #   （§6.1 入场：日内突破时买、**止损放"第一根阳线低点"**）。
    #   我们的库里**只有日线** ⇒ 任何日线代理（突破那根 K 线的 low / 区间底部）
    #   都比他的日内止损宽**几倍**：实测 299 个候选里只有 **3 个**落在 2×ADR 内。
    #
    #   ⇒ 硬套那个阈值 = **把"日内能做到的事"当成"日线必须做到的事"**，
    #     结果是把整个系统筛空 —— 那是**假失败**，不是他的规则严格。
    #
    #   ⇒ 处理：**默认不设上限**，但在报告里**报出止损宽度分布**（以 ADR 为单位），
    #     让读者自己看"我们离他的 ≤1 ADR 有多远"。
    #     想按某个上限跑，传 `max_stop_adr=2.0` 即可（声明为变体）。
    "max_stop_adr": None,
}

#: 条件② 用到的五条均线距离（§11.5 原文口径）。
_MA_DIST = ("ma_dist_ema10", "ma_dist_ema20", "ma_dist_ema50",
            "ma_dist_sma150", "ma_dist_sma200")

#: 本策略**需要**的因子（调用方按它取数）。
REQUIRED_FACTORS: tuple[str, ...] = (
    "atr14", "adr20", "ret260", "rs_rank", "near_52w_high",
    "range_pct10", "daily_range_pct", "vol_ratio10_50", *_MA_DIST,
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
        self.exit_policy = exit_policy or ExitPolicy()

    # ── 内部：从 `factors` 里取一张宽表（**缺就报错**，不静默兜底）──────

    @staticmethod
    def _get(factors: Mapping[str, pd.DataFrame], name: str) -> pd.DataFrame:
        if name not in factors:
            raise KeyError(
                f"缺少因子 {name!r}（本策略需要 {REQUIRED_FACTORS}）；"
                f"现有：{sorted(factors)} —— 承 P1：不静默兜底")
        return factors[name]

    # ── 每日选股（**全部向量化 + 只用 ≤ 当日的信息**）────────────────────

    @staticmethod
    def _stop_series(panel) -> pd.DataFrame:
        """止损位 —— **他四个候选里的第一个**：「**确认突破那根 K 线的底部**」。

        ## ★ 为什么不是"区间底部"（我先选错了，如实记录）

        他给的四个候选（§6.1 行 526）：
        「确认突破那根 K 线的底部 / 当日低点 / 前一日低点 / **区间底部**」，
        并另说「**止损尽量不大于 1 个 ADR**」。

        我第一版选了**区间底部**，理由是"更保守"。**那是错的**：

        > 紧密盘整（T1 要求 5 日区间 ≤6%）的票 **ADR 只有 2–3%**，
        > 而区间底部离突破点 ≈ 区间高度（**≈6%**）⇒ 止损 ≈ **3–4 个 ADR**，
        > **远超他明说的「≤1 个 ADR」**。

        ⇒ **「≤1 个 ADR」这句话本身就在指认止损位**：只有**突破那根 K 线的底部**
        （≈ 当日振幅 ≈ 1 ADR）才符合。
        **把止损放宽不是"保守"，是偏离他的规则** —— 而偏离之后，
        整个"窄止损换高赔率"的机制就没了（那是他系统的核心，§2.1）。

        （实测佐证：用区间底部时，止损宽度这一条把 299 个候选砍到 **1** 个。）
        """
        return panel.field("low")

    def _masks(self, panel, factors: Mapping[str, pd.DataFrame],
               ) -> list[tuple[str, pd.DataFrame]]:
        """逐条条件的布尔掩码（**`candidates` 与 `diagnose` 共用** —— 防两处逻辑分叉）。"""
        p = self.params
        atr = self._get(factors, "atr14")
        adr = self._get(factors, "adr20")
        ret260 = self._get(factors, "ret260")
        rs = self._get(factors, "rs_rank")
        near_high = self._get(factors, "near_52w_high")
        rng = self._get(factors, "range_pct10")
        daily_rng = self._get(factors, "daily_range_pct")
        volr = self._get(factors, "vol_ratio10_50")
        ma = {n: self._get(factors, n) for n in _MA_DIST}

        # `close` / `high` / `low` 用原始字段（**策略只用面板字段做"区间"这类几何量**）
        close, high_f, low_f = (panel.field("close"), panel.field("high"),
                                panel.field("low"))

        # ── T1：紧区间（★ **不含今天** —— 否则"突破"被算进"紧"里，自相矛盾）──
        n = int(p["tight_days"])
        prior_hi = high_f.shift(1).rolling(n).max()
        prior_lo = low_f.shift(1).rolling(n).min()
        prior_close = close.shift(1)
        tight = (prior_hi - prior_lo) / prior_close <= float(p["tight_range_max"])
        stop = self._stop_series(panel)

        # ── T2：均线平行**且收拢** ──
        s10, s20 = close.rolling(10).mean(), close.rolling(20).mean()
        flat = (s10 - s20).abs() / close <= float(p["ma_converge_max"])
        converging = (s10 - s20).abs() <= (s10 - s20).abs().shift(n)
        t2 = flat & converging if p["require_converging"] else flat

        # ── T3 / T8：200 日线之上，且 200MA 不向下 ──
        t3 = ma["ma_dist_sma200"] > 0
        t8 = ma["ma_dist_sma200"] >= ma["ma_dist_sma200"].shift(20)

        # ── T4：贴近**任一**条均线（≤1 个 ATR）──
        near = ma[_MA_DIST[0]].abs()
        for name in _MA_DIST[1:]:
            near = np.minimum(near, ma[name].abs())
        t4 = near <= float(p["near_ma_max_atr"])

        # ── T5：12 个月涨过 50% ──
        t5 = ret260 >= float(p["rise_12m_min"])

        # ── T6：近 N 日没有"高动能下跌" ──
        daily_ret = close / close.shift(1) - 1.0
        worst = daily_ret.rolling(int(p["no_dump_days"])).min()
        t6 = worst > -float(p["no_dump_adr"]) * adr

        # ── T7：不要太 Overextended ──
        t7 = (close / close.rolling(50).mean() - 1.0) <= float(p["overextend_max"])

        # ── A1：150 日线之上 ──
        a1 = ma["ma_dist_sma150"] > 0 if p["above_150ma"] else True

        # ── A2 / A3 ──
        a2 = rs >= float(p["rs_min"])
        a3 = near_high >= float(p["near_high_min"])

        # ── A4：波幅**收缩 ≥3 次**（在同一条因子上取三个递减检查点）──
        step = int(p["contraction_step"])
        k = int(p["contractions_min"])
        shrinking = pd.DataFrame(True, index=rng.index, columns=rng.columns)
        for i in range(k - 1):
            shrinking &= rng.shift(i * step) < rng.shift((i + 1) * step)

        # ── A5：收缩到最后"股价波动 < 1%" = **当日振幅** ≤ 1%（见模块 docstring 的读法说明）──
        #     A6：配合成交量下跌 ──
        a5 = daily_rng <= float(p["final_range_max"])
        a6 = (volr < 1.0) if p["require_volume_decline"] else True

        # ── 突破触发（他的方式①）──
        lookback = int(p["breakout_lookback"])
        breakout = close > high_f.shift(1).rolling(lookback).max()

        # ── 止损宽度（B4）── 默认不设上限（见 `DEFAULTS` 的说明：日线做不到他的日内止损）
        #    信号日用 `close` 近似入场价
        if p["max_stop_adr"] is None:
            width_ok = pd.DataFrame(True, index=close.index, columns=close.columns)
            width_label = "止损宽度(未设上限)"
        else:
            width_ok = (close - stop) <= float(p["max_stop_adr"]) * adr
            width_label = f"止损宽度<={p['max_stop_adr']}ADR"

        # 常量 `True` 一律摊成同形状的宽表（否则 `&` 会广播出意外形状）
        def _wide(x):
            return x if isinstance(x, pd.DataFrame) else pd.DataFrame(
                bool(x), index=close.index, columns=close.columns)

        # ★★ **形态类条件一律看"截至昨天"**（承他的原话：区间扩张**之前**出现紧密盘整）
        #
        # 为什么必须这样（**这是逻辑错，不是保守**）：
        #   **信号日就是突破日**，而突破日必然"动了" ——
        #   若把「收缩到最后，波动 < 1%」「均线收拢」这类**盘整态**条件放在**信号日**上算，
        #   就等于**要求突破日不动** ⇒ **自相矛盾**。
        #
        # 实测佐证（改前）：14 条全过后剩 9 格，A5 一过就归零（9 → 0）。
        #   —— 那 9 格正是"昨天还紧、今天突破了"的票，而它们的**今天**当然不紧。
        #
        # ⚠️ `tight`（T1）**不在此列**：它已经用 `shift(1)` 取"今天之前的 n 天"，
        #    再 shift 一次会变成"前天之前"，**定义就错了**。
        setup = [
            ("T2 均线平行且收拢", t2),
            ("T3 >200MA", t3),
            ("T4 贴近某条均线", t4),
            ("T5 12月涨>50%", t5),
            ("T6 近3日无大跌", t6),
            ("T7 不过度延伸", t7),
            ("T8 200MA不向下", t8),
        ]

        # ★ VCP 六要点 —— **只在挑 VCP 形态时启用**（见模块 docstring：
        #   它是 §6.1 三种形态之一的标准，**不是**四条件之上的追加过滤）
        if p["vcp_filter"]:
            setup += [
                ("A1 >150MA", _wide(a1)),
                ("A2 RS>=90", a2),
                ("A3 近52周高点", a3),
                ("A4 波幅收缩>=3次", shrinking),
                ("A5 最后段<1%", a5),
                ("A6 缩量", _wide(a6)),
            ]

        return [
            ("T1 紧区间(不含今天)", tight),
            *[(name, m.shift(1).fillna(False)) for name, m in setup],
            ("突破触发", breakout),                 # ← 只有它看**今天**
            (width_label, width_ok),
        ]

    def candidates(self, panel, factors: Mapping[str, pd.DataFrame],
                   ) -> pd.DataFrame:
        """返回 `day, symbol, stop_price, form`（`day` = **信号日**，次日开盘成交）。"""
        masks = self._masks(panel, factors)
        cond = masks[0][1]
        for _name, m in masks[1:]:
            cond = cond & m

        close = panel.field("close")
        stop = self._stop_series(panel)
        near_high = self._get(factors, "near_52w_high")

        # ── 形态分类（B1）：按"离 52 周高点多近" ──
        form = pd.DataFrame(FORM_MID, index=close.index, columns=close.columns)
        form = form.where(near_high < 0.95, FORM_HIGH)
        form = form.where(near_high >= 0.90, FORM_LOW)

        stack = cond.stack()
        hit = stack[stack.fillna(False)]
        if hit.empty:
            return pd.DataFrame(columns=["day", "symbol", "stop_price", "form"])
        return pd.DataFrame({
            "day": [d for d, _ in hit.index],
            "symbol": [s for _, s in hit.index],
            "stop_price": [float(stop.loc[d, s]) for d, s in hit.index],
            "form": [form.loc[d, s] for d, s in hit.index],
        })

    def diagnose(self, panel, factors: Mapping[str, pd.DataFrame]) -> dict[str, int]:
        """**漏斗计数** —— 回答"为什么一只都选不出来"。

        逐个条件**累计**统计剩余格数；掉得最多的那一步就是卡点。
        ⚠️ 只用于诊断，**不进判决**。
        """
        masks = self._masks(panel, factors)
        keep = masks[0][1].fillna(False)
        out = {"eligible": int(keep.to_numpy().sum())}
        for name, m in masks[1:]:
            keep = keep & m.fillna(False)
            out[f"after:{name}"] = int(keep.to_numpy().sum())
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


#: 注册（照 `strategies/__init__.py` 的写法；见该文件的说明）。
__all__ = ["DEFAULTS", "ENTER_MODES", "REQUIRED_FACTORS", "TugboatBreakout"]
