"""`rsi_tight_consolidation` —— 用 RSI 筛「紧密盘整」（Tugboat §11.5）。

**核心思路**（原文）：
> 用 RSI 的"指数化"特性，去找"**最近几天 RSI 几乎没动**"的股票 ——
> RSI 原地踏步 = 股价原地踏步 = **紧密盘整（Tight Flag / VCP 的雏形）**。

## 五个条件（StockCharts ASW 版，即本类的**默认参数**）

| # | 条件 | 用哪个因子 |
|---|---|---|
| ① | 最近 **3–4 天** RSI 每日变化 **< 3**，且**累计变化 ≤ 5** | `rsi14`（差分在 R1 里做，承 H4）|
| ② | 股价**贴近** 10EMA / 20EMA / 50EMA / 150SMA / 200SMA **中任一条**（误差 ≤ **1 个 ATR**）| `ma_dist_ema10/20/50`、`ma_dist_sma150/200` |
| ③ | 过去 **150 或 260** 个交易日，**从最低点**涨 > **30–50%** | `off_low150` / `off_low260` |
| ④ | **ATR / 收盘价 > 2.5%** | `atr_pct14`（或 `adr20`，见下）|
| ⑤ | **RSI > 50**（或 > 45）| `rsi14` |

## ⚠️ 一、本规则**没有**实现他的「过滤层」—— 但**实测影响很小**（不是"做了没说"）

原文的 `+ 过滤层`：**个股（非 ETF）/ 限 NYSE·NASDAQ / 美国 / 市值 > $1B /
50 日均成交额 > $1000 万 / 剔除生物科技·制药·必需消费**。

| 过滤项 | 能不能做 | 为什么 |
|---|---|---|
| 个股（非 ETF）/ 美国 | ✅ | `stocks()` 已用快照剔除非股票（数据层做，本层不重复判断）|
| **市值 > $1B** | ❌ | 快照**只有 1–2 天**，历史市值没有数据 |
| **50 日均成交额 > $1000 万** | ❌ | 需要"美元成交额"，而本层 `close` 是 **hfq**、`volume` 是**不复权**——两者相乘不是真实美元额 |
| **限 NYSE / NASDAQ** | ❌ | 数据里**没有交易所字段** |
| **剔除生物科技 / 制药 / 必需消费** | ❌ | 只有快照的 `industry`，同样只有 1–2 天 |

### ★ 但实测表明：这个过滤层在**本票池上几乎是空操作**（2026-10-08 实测）

| 过滤项 | 实测剔除量 | 依据 |
|---|---|---|
| **市值 > $1B** | **13 / 270 = 4.8%** | 票池市值 **中位 $13.5B**、p25 $4.9B（快照 `market_cap`）|
| **50 日均成交额 > $10M** | **17 / 270 = 6.3%** | 中位 **$317.7M/日**；`<$20M` 也只有 21 只 |
| **剔除生物科技 / 制药 / 必需消费** | **≈ 0** | 票池行业前几名是：半导体 21 / 软件基础设施 19 / 资本市场 16 / 航空航天 14 / 电子元件 12 —— **本来就没有生物科技** |
| **限 NYSE / NASDAQ** | 无法测 | 但票池来自自选名单，本就是主流交易所 |

⇒ **三项合起来约 5–7%（并集约 20 只），不是量级差异。**
   ⇒ 结论**可以按原样读**，只需带上这一条 —— **不必**因为过滤层缺失就否掉整次实验。

### ★ 为什么"配对设计"还额外帮了忙

R2 的陪考**从同一个池子里抽** ⇒ 上面那 20 只细票在**两侧以相近的频率出现**，
它们的噪声在**配对差值**里大部分**抵消**掉。

⚠️ **但抵消是不完全的**：规则是**有条件的**挑选（高 ATR / 大涨幅这类条件，
可能**更偏向**那些细票），而陪考是**均匀**抽 ⇒ 存在残余不对称。
**所以这条仍然要带在结论里读**（承 Z4 的显形纪律），只是**不必**当成致命缺陷。

> ⚠️ 一并说明：那 17 只低流动性票里最差的是 `EXE`（约 **$0.1M/日**，
> 股价 $0.044、ATR 仅 $0.001）—— 它的收益基本是**噪声**。
> 要不要单独实现流动性过滤，是**下一步**的事（需要补一个美元成交额因子）。

## ⚠️ 二、原文给的是**区间**，取哪一端会改变结论 ⇒ 全部做成参数

11.5 写的是「最近 **3–4** 天」「涨 > **30–50%**」「**150 或 260**」「RSI > **50**（或 **45**）」——
**他没定死**。由我们替他挑一个 = **悄悄改规则**，所以：

- 所有可动的地方都是**显式参数**，且 `params` 会进报告的指纹（可复现）；
- **默认值 = ASW 版**（原文第一版）；
- 跑变体时必须**显式给 `name`**（注册表要不同的键），并在报告里**当作独立实验**报
  —— 若同一批跑多个变体，**多个 p 值必须一起过 BH**（承 C5）。

**两处原文本身就有歧义**，参数把它**摊在明面上**而不是藏在注释里：

| 歧义 | 两种读法 | **定案** |
|---|---|---|
| ①的"**累计变化**" | `net` = 净变化（今天 − N 天前）／ `path` = 逐日绝对变化之和 | **`path`**（见下）|
| ④的分母 | `atr` = ATR/收盘（**含跳空**）／ `adr` = ADR%（**不含跳空**）| `atr`（§11.5 原文），`adr` 作变体分开报 |

### ★ ①的读法：定案取 `path`（三条理由，都可验证）

1. **规则的前提只在 `path` 下成立**。原文的推理是「RSI 原地踏步 **⇒** 股价原地踏步
   ⇒ 紧密盘整」。而一个 RSI 每天**来回摆 ±2.9** 的票，用 `net` 看是"净变化 2.9"，**放行** ——
   可它的 RSI 一天动近 3 点，**股价并不平静**，与规则自己的前提**矛盾**
   ⇒ `net` 会放进**假阳性**（形态根本不是紧密盘整的票）。
2. **`path` 是 `net` 的严格子集**（累加绝对值 ≥ 净变化）⇒ **更严 ⇒ 结论更难成立 ⇒ 更保守**。
3. **参数仍留着**（`rsi_aggregate_mode="net"` 可跑变体）——
   若将来要报这一支，它算**独立实验**，p 值要**一起过 BH**。

> ⚠️ **`path` 会不会太严？** 实测（400 天面板，**可用日 139 天**）：
>
> | | `path`（**定案**）| `net`（变体）|
> |---|---|---|
> | 条件① 后剩 | **8.7** 只 | 20.9 只 |
> | 有命中的交易日 | **104 / 139 = 75%** | 129 / 139 = 93% |
> | 命中日均 | **2.4** 只 | 5.0 只 |
>
> ⇒ ① 确实是**最紧的一关**（269.6 → 8.7，砍掉 **96.8%**），
> 但**规则没有失效**：四分之三的可用交易日仍有选中，每天 2–9 只 ——
> 这与 Tugboat 说的"筛完进一个小 chart list"一致。
>
> ⚠️ **代价必须显形**：每天只选 2–3 只 ⇒ **配对比较的噪声很大**
> ⇒ 报告里必须读 `k_per_day_median`（见 §「样本量」）。

## ⚠️ 三、一条**做不到**的 pass 条件（同源问题）

原文的看图评级里，**pass** 的原因之一是「**200MA 仍向下**」（落后股）。
但本层只有 `ma_dist_sma200`（**距离**，没有**斜率**）⇒
**本规则答不了"200MA 是否向下"**，故**不得声称**过滤了这条。
（要补，得再加一个均线斜率因子 —— 尚未实现。）
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from ..selection_contract import SelectionInput
from ..selection_registry import register_selection

__all__ = ["ASW_DEFAULTS", "SELECTION_NAME", "RsiTightConsolidation"]

#: 注册表里的默认名字（跑变体时请用别的名字，见模块 docstring）。
SELECTION_NAME = "rsi_tight_consolidation"

#: **StockCharts ASW 版的默认参数** —— 11.5 原文第一版。
#: ⚠️ 这是**预注册**用的一套：改它 = 开新实验。
ASW_DEFAULTS: Mapping[str, Any] = {
    # ① RSI 平滑
    "rsi_change_days": 4,            # "最近 3–4 天"
    "rsi_daily_max": 3.0,            # "每日变化 < 3"
    "rsi_aggregate_max": 5.0,        # "累计变化 ≤ 5"
    # "net" | "path" —— 原文歧义，**已定案取 `path`**（理由见模块 docstring 的
    # 「①的读法」一节：只有它真的测"没动"，且它是 `net` 的子集 ⇒ 更保守）。
    "rsi_aggregate_mode": "path",
    # ② 贴近均线
    "ma_atr_max": 1.0,               # "误差 ≤ 1 个 ATR"
    # ③ 离底涨幅
    "rise_window": 260,              # "150 或 260"
    "rise_min": 0.30,                # "涨 > 30–50%"
    # ④ 波幅
    "range_basis": "atr",            # "atr" | "adr"
    "range_min": 0.025,              # "> 2.5%"
    # ⑤ RSI 门槛
    "rsi_min": 50.0,                 # "RSI > 50"
}

#: 五条均线距离因子（条件② 的候选）。
_MA_FACTORS: tuple[str, ...] = (
    "ma_dist_ema10", "ma_dist_ema20", "ma_dist_ema50",
    "ma_dist_sma150", "ma_dist_sma200",
)

_MODES = ("net", "path")
_BASES = ("atr", "adr")


class RsiTightConsolidation:
    """11.5「RSI 紧密盘整」筛选规则（5 个技术条件，**不含**过滤层）。"""

    def __init__(self, *, name: str = SELECTION_NAME,
                 **overrides: Any) -> None:
        cfg = {**ASW_DEFAULTS, **overrides}
        unknown = set(overrides) - set(ASW_DEFAULTS)
        if unknown:
            raise ValueError(
                f"未知参数 {sorted(unknown)}（可用：{sorted(ASW_DEFAULTS)}）"
                f"—— 不静默忽略，否则报告里的指纹会撒谎"
            )
        if cfg["rsi_aggregate_mode"] not in _MODES:
            raise ValueError(f"rsi_aggregate_mode 必须是 {_MODES}，收到 {cfg['rsi_aggregate_mode']!r}")
        if cfg["range_basis"] not in _BASES:
            raise ValueError(f"range_basis 必须是 {_BASES}，收到 {cfg['range_basis']!r}")
        if int(cfg["rsi_change_days"]) < 1:
            raise ValueError(f"rsi_change_days 必须 ≥ 1，收到 {cfg['rsi_change_days']}")

        self.name = name
        self.params = dict(cfg)
        # `requires` **由参数算出**（不是写死的全集）——
        # 这样"跑 adr 版"时就不会白要 `atr_pct14`（也让依赖清单说实话，承 H1/H4）。
        self.requires = tuple(sorted({
            "rsi14",
            f"off_low{int(cfg['rise_window'])}",
            "atr_pct14" if cfg["range_basis"] == "atr" else "adr20",
            *_MA_FACTORS,
        }))

    # ── 五条条件的布尔掩码（`select` 与 `diagnose` **共用**，防两处逻辑分叉）──

    def _funnel(self, data: SelectionInput) -> list[tuple[str, pd.Series]]:
        """返回 `[(条件名, 布尔 Series)]`，索引 = `data.universe`。"""
        p = self.params
        universe = pd.Index(data.universe)

        def to_universe(mask: pd.Series) -> pd.Series:
            # 缺值 ⇒ **不算通过**（不填 True、不填 0 —— 承 P6：不补造）
            return mask.reindex(universe).fillna(False).astype(bool)

        # ① RSI 几乎没动 —— 差分在**此层**做（承 H4：差分是允许的派生）
        days = int(p["rsi_change_days"])
        rsi = data.factor("rsi14")
        if len(rsi) < days + 1:
            empty = pd.Series(False, index=universe)
            return [("rsi_calm", empty)]
        recent = rsi.iloc[-(days + 1):]
        deltas = recent.diff().iloc[1:]
        daily_ok = (deltas.abs() < float(p["rsi_daily_max"])).all(axis=0)
        if p["rsi_aggregate_mode"] == "net":
            aggregate = (recent.iloc[-1] - recent.iloc[0]).abs()
        else:
            aggregate = deltas.abs().sum(axis=0)
        agg_ok = aggregate <= float(p["rsi_aggregate_max"])
        calm = to_universe(daily_ok & agg_ok)

        # ② 贴近**任一**条均线（`min` 会自动跳过 NaN ⇒ "任一条可用即可"是对的）
        dists = pd.concat([data.today(n).abs() for n in _MA_FACTORS], axis=1)
        near = to_universe(dists.min(axis=1) <= float(p["ma_atr_max"]))

        # ③ 离 150 / 260 日最低点的涨幅
        risen = to_universe(data.today(f"off_low{int(p['rise_window'])}")
                            > float(p["rise_min"]))

        # ④ 波幅够大
        basis_factor = "atr_pct14" if p["range_basis"] == "atr" else "adr20"
        wide = to_universe(data.today(basis_factor) > float(p["range_min"]))

        # ⑤ RSI 站上门槛
        above = to_universe(data.today("rsi14") > float(p["rsi_min"]))

        return [("rsi_calm", calm), ("near_ma", near), ("risen_from_low", risen),
                ("wide_range", wide), ("rsi_above", above)]

    def select(self, data: SelectionInput) -> set[str]:
        """返回该日**五条全满足**的标的（可能为空集 —— 那就是没有）。"""
        universe = pd.Index(data.universe)
        keep = np.ones(len(universe), dtype=bool)
        for _name, mask in self._funnel(data):
            keep &= mask.to_numpy()
        return set(universe[keep])

    def diagnose(self, data: SelectionInput) -> dict[str, int]:
        """**漏斗计数** —— 回答"为什么今天一只都没选出来"。

        逐个条件**累计**统计剩余只数：第一个掉得最多的地方，就是卡住的地方。
        ⚠️ 只用于诊断，**不进判决**。
        """
        result: dict[str, int] = {"universe": len(data.universe)}
        keep = np.ones(len(data.universe), dtype=bool)
        for name, mask in self._funnel(data):
            keep &= mask.to_numpy()
            result[f"after_{name}"] = int(keep.sum())
        return result


# **导入即注册**（照 `factor/implementations/*.py` 的写法）。
# ⚠️ 少了这一行，`implementations/__init__.py` 会"导入了但没注册"——
#    注册表空 → `get_selection()` 报"注册表为空"，而**代码看起来完全正常**。
register_selection(RsiTightConsolidation())
