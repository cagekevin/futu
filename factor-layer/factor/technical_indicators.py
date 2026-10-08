"""技术指标的**唯一实现点**（供 `implementations/` 下多个因子共用）。

## 为什么必须有这个模块（承 SSOT）

新一批因子（RSI / ATR / ADR / 均线距离…）会**共用同一批公式**：

| 谁要 | 要什么 |
|---|---|
| `atr14` | ATR(14，价格单位) |
| `atr_pct14` | ATR(14) / close |
| `ma_dist_ema10/20/50`、`ma_dist_sma150/200` | `(close − MA) / ATR` |

若让每个因子文件各写一遍 ATR 公式 ⇒ **6 处 ATR**，改一次口径要改 6 个文件，
迟早出现"两个 ATR 不一致"（**两份真相**）。
⇒ 公式**只写在这里**，因子文件只做**薄封装**。

## warm-up 约定（承 A4）

本仓既有的判据：`FactorSpec.min_window` 行**必须全是 `NaN`**
（`factor_registry._check_values` 机械校验）。

既有三个因子（`vol20` / `turn20` / `max20`）靠"先 `diff()` 造 1 行 NaN，
再 `rolling(N)`"让**首个有效值恰好落在索引 `N`** —— 与 `min_window = N` 对齐。

⚠️ 但那是**凑出来的**，而且 `diff()` 本身就是它们的定义的一部分。

本模块的指标公式里**没有** `diff`（ADR / EMA / SMA），
若为凑 warm-up 硬加 `shift(1)`，会**悄悄改变定义**
（把"今天的 MA"变成"昨天的 MA"）。
⇒ 一律用 `mask_warmup()` **显式声明**"前 `window` 行不用"：
   **宁可明说少用几行，也不偷偷换定义。**

## 上游字段

一律用面板字段（`open/high/low/close/volume`），**不 import 数据层**（承 C1）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注
    pass

__all__ = [
    "ADR_WINDOW",
    "ATR_WINDOW",
    "HIGH_52W_WINDOW",
    "RSI_FLAT",
    "RSI_WINDOW",
    "RS_WEIGHTS",
    "VOLUME_LONG_WINDOW",
    "VOLUME_SHORT_WINDOW",
    "adr_percent",
    "cross_sectional_percentile",
    "ema",
    "ma_distance_in_atr",
    "mask_warmup",
    "n_day_return",
    "proximity_to_rolling_high",
    "range_percent",
    "rise_from_low",
    "rolling_high",
    "rolling_low",
    "sma",
    "volume_ratio",
    "weighted_relative_return",
    "wilder_atr",
    "wilder_rsi",
]

#: 默认窗口（与 11.5 / 10.6 / 10.8 的口径一致）。
RSI_WINDOW = 14
ATR_WINDOW = 14
ADR_WINDOW = 20

#: 52 周 ≈ 250 个交易日（VCP 六要点第 3 条「接近 52 周新高」）。
HIGH_52W_WINDOW = 250

#: 量能趋势的短 / 长窗口（VCP 六要点第 6 条「最后配合成交量下跌」）。
VOLUME_SHORT_WINDOW = 10
VOLUME_LONG_WINDOW = 50

#: **完全平盘**（窗口内既无涨也无跌）时的 RSI —— RSI 在此**无定义**，取中性值。
#: ⚠️ 不能取 100：那会让"连续多日一动不动的票"被判成极强，从而**混进**条件⑤（`RSI > 50`）。
RSI_FLAT = 50.0


def mask_warmup(values: pd.DataFrame, window: int) -> pd.DataFrame:
    """把**前 `window` 行**置为 `NaN` —— 显式满足 A4 的 warm-up 不变量。

    ⚠️ 这不是"多丢几行"，而是**把口径写明白**：
       `min_window` 的语义就是"这些行不可用"。指标在索引 `window−1` 才凑够
       `window` 个样本时，那一行**到底算不算可用**是个口径问题 ——
       本模块统一取**保守**的一侧（少用一行），并在 docstring 里写明。
    """
    if window < 1:
        raise ValueError(f"window 必须 ≥ 1，收到 {window}")
    out = values.copy()
    out.iloc[:window] = np.nan
    return out


def wilder_rsi(close: pd.DataFrame, window: int = RSI_WINDOW) -> pd.DataFrame:
    """RSI —— **Wilder 平滑**（`alpha = 1/window`）。

    ```
    delta    = close.diff()                       # 第 0 行 NaN
    avg_gain = ewm(alpha=1/14, min_periods=14) 对 gain 的指数均值
    RSI      = 100 − 100 / (1 + avg_gain/avg_loss)
    ```

    **首位有效值在索引 `window`**（因 `diff` 造了 1 行 NaN，`min_periods=window`
    又要 `window` 个有效观测）⇒ 前 `window` 行为 `NaN`，与 `min_window = window` 对齐。

    ⚠️ **边界：`avg_loss == 0` 有两种情形，必须分开**（否则会静默出错）：

    | 情形 | 正确取值 | 若一律取 100 会怎样 |
    |---|---|---|
    | 全程上涨（`avg_gain > 0`）| **100**（标准约定）| 对 |
    | **完全平盘**（`avg_gain == 0`）| **50**（RSI 无定义 ⇒ 取中性）| **错**：一个 14 天一动不动的票会被判成"极强" |

    ⚠️ 第二种不是纸上情形：停牌 / 极不活跃的股票会**连续多日 `high == low == close`**。
       而它**直接影响 11.5 条件⑤（`RSI > 50`）** —— 取 100 会让平盘股**混进**筛选结果，
       取 50 则正确地把它挡在门外。
    """
    delta = close.diff()                       # 第 0 行 → NaN
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False,
                        min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False,
                        min_periods=window).mean()

    # 先避开除零（0 → NaN，否则会算出 inf 一路传进 IC —— IC 不报错，只失真）
    rs = avg_gain / avg_loss.where(avg_loss > 0.0)
    out = 100.0 - 100.0 / (1.0 + rs)
    # 再补回"零损失"的两种语义：全程上涨 = 100；完全平盘 = 中性 50
    zero_loss = pd.DataFrame(
        np.where(avg_gain > 0.0, 100.0, RSI_FLAT),
        index=avg_gain.index, columns=avg_gain.columns,
    )
    return out.where(avg_loss != 0.0, zero_loss)


def wilder_atr(high: pd.DataFrame, low: pd.DataFrame,
               close: pd.DataFrame, window: int = ATR_WINDOW) -> pd.DataFrame:
    """ATR（Average True Range，Wilder 平滑）—— **价格单位**（元 / 美元）。

    ```
    TR  = max(high−low, |high−prev_close|, |low−prev_close|)
    ATR = ewm(alpha=1/window, min_periods=window) 对 TR 的指数均值
    ```

    ⚠️ **必须用 `np.maximum` 而不是 `DataFrame.max(axis=1)`**：
       pandas 的 `.max(axis=1)` **默认跳过 `NaN`** ⇒ 第 0 行（`prev_close` 为 `NaN`）
       会取到 `high−low`，**warm-up 就少一行**、首个有效值提前。
       `np.maximum` 会**传播 `NaN`** ⇒ 第 0 行 `NaN` ⇒ 前 `window` 行为 `NaN` ✅

    首位有效值在索引 `window`。
    """
    prev_close = close.shift(1)                # 第 0 行 → NaN
    true_range = np.maximum(
        np.maximum(high - low, (high - prev_close).abs()),
        (low - prev_close).abs(),
    )
    return true_range.ewm(alpha=1.0 / window, adjust=False,
                          min_periods=window).mean()


def adr_percent(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame,
                window: int = ADR_WINDOW) -> pd.DataFrame:
    """ADR%（Average Daily Range Percentage）—— **比值**（`0.025` = 2.5%）。

    ```
    ADR%_t = mean( (high − low) / close , 过去 window 天 )
    ```

    口径出处：`关于策略/9-资料-TradingTugboat.md` §10.6
    （"过去 20 天，每天 `(最高 − 最低) / 收盘`，再取平均"），
    他的**选股过滤器**用它：`< 2.5% → 直接排除`。

    ⚠️ 与 `wilder_atr` 的**区别**：ADR% 不含跳空（只用当日 `high−low`），
       ATR 含跳空（把 `prev_close` 也算进真实波幅）。
       两者**不是同一个东西**，故并存、分开报（承 §十的"两个都加"）。
    """
    daily_range = (high - low) / close
    return mask_warmup(daily_range.rolling(window).mean(), window)


def ema(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """指数移动平均（`adjust=False` 的递推式，即交易软件里的标准 EMA）。"""
    return mask_warmup(close.ewm(span=window, adjust=False).mean(), window)


def sma(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """简单移动平均。"""
    return mask_warmup(close.rolling(window).mean(), window)


def rolling_high(high: pd.DataFrame, window: int) -> pd.DataFrame:
    """过去 `window` 天的**最高价**（含当日）。首位有效值在索引 `window`。"""
    return mask_warmup(high.rolling(window).max(), window)


def rolling_low(low: pd.DataFrame, window: int) -> pd.DataFrame:
    """过去 `window` 天的**最低价**（含当日）。首位有效值在索引 `window`。"""
    return mask_warmup(low.rolling(window).min(), window)


def proximity_to_rolling_high(close: pd.DataFrame, high: pd.DataFrame,
                              window: int = HIGH_52W_WINDOW) -> pd.DataFrame:
    """`close / 过去 window 天最高价` —— **距高点有多近**（`1.0` = 正好在新高）。

    用途：Tugboat §7.1 VCP 六要点第 3 条
    「**接近 52 周新高（最好不低于 52 周新高的 15%）**」
    ⇒ 判据是 `≥ 0.85`（**越低越远**）。

    ⚠️ 分母用 **`high` 的最高价**，不是 `close` 的最高收盘 —— 他的原话是"52 周**新高**"，
       而"新高"在图表上指**最高价**。用收盘会**低估**距离（把已经触及的高点抹掉）。
    """
    return mask_warmup(close / rolling_high(high, window), window)


def range_percent(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame,
                  window: int) -> pd.DataFrame:
    """`(过去 window 天最高价 − 最低价) / close` —— **区间波幅占比**。

    用途：Tugboat §7.1 VCP 六要点第 4、5 条
    「**波幅收缩，最好三次或以上**」「**收缩到最后，股价波动 < 1%**」。

    ⚠️ **"三次收缩"不是本函数能算的** —— 它需要看**这个序列的形态**
       （连续几段越来越窄），那属**规则的派生**（R1 允许基于已声明因子做该日可见的派生）。
       本函数只提供**原料**。

    ⚠️ 与 `adr_percent` 的区别：ADR% 是**逐日**波幅的均值（不含窗口极值），
       本函数是**窗口内极差**。一个度量"每天动多少"，一个度量"整段有多宽"。
    """
    spread = rolling_high(high, window) - rolling_low(low, window)
    return mask_warmup(spread / close, window)


def volume_ratio(volume: pd.DataFrame, short_window: int = VOLUME_SHORT_WINDOW,
                 long_window: int = VOLUME_LONG_WINDOW) -> pd.DataFrame:
    """`短期均量 / 长期均量` —— **量能的趋势**（`< 1` = 在缩量）。

    用途：Tugboat §7.1 VCP 六要点第 6 条「**最后配合成交量下跌**」
    ⇒ 判据是 `< 1`（盘整末期缩量）。

    ⚠️ 它**自带抗拆股**：分子分母同为量，任何**恒定**的拆股比例都会约掉。
       但**窗口内**发生拆股（比例前后不一致）仍会失真 —— 那是数据口径的事，不是本函数的。
    """
    if short_window >= long_window:
        raise ValueError(
            f"短期窗口必须小于长期窗口：{short_window} vs {long_window}"
            f"（否则比值恒 ≥ 1，判别不了缩量）")
    short = volume.rolling(short_window).mean()
    long = volume.rolling(long_window).mean()
    return mask_warmup(short / long.where(long > 0.0), long_window)


def cross_sectional_percentile(values: pd.DataFrame) -> pd.DataFrame:
    """**当日横截面**百分位（`0`–`1`）—— `rank(axis=1, pct=True)`。

    ⚠️ 它是**横截面**运算，不是逐标的的时间序列指标：
       同一天里，某只票的排名取决于**当天票池里其他标的**。
       ⇒ 只对**"排名本身有意义"**的量用它（如相对强度），**不要**对价格水平用。
    """
    return values.rank(axis=1, pct=True)


#: IBD / MarketSmith 式相对强度的**加权口径**：
#: `3 / 6 / 9 / 12` 个月收益，权重 `40 / 20 / 20 / 20`（交易日近似取 63 / 126 / 189 / 252）。
#: 出处：Tugboat §7.1「相对强度要强（**他一般选 90 以上**）」——
#: 他用的是 MarketSmith 的 RS **百分位排名**，其加权方案即此。
RS_WEIGHTS: tuple[tuple[int, float], ...] = (
    (63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2),
)


def weighted_relative_return(close: pd.DataFrame,
                             weights: tuple[tuple[int, float], ...] = RS_WEIGHTS,
                             ) -> pd.DataFrame:
    """加权多窗口收益（**相对强度的原始值**，还没做截面排名）。

    ```
    加权 = Σ wᵢ × (close_t / close_{t−nᵢ} − 1)
    ```
    ⚠️ 各窗口的 `shift` 会造出不同的 `NaN` 前导 ⇒ 取**最长窗口**为 warm-up
       （与 `min_window = max(nᵢ)` 对齐）。
    """
    if not weights:
        raise ValueError("weights 不能为空")
    longest = max(n for n, _ in weights)
    total = None
    for n, w in weights:
        ret = close / close.shift(n) - 1.0
        total = ret * w if total is None else total + ret * w
    return mask_warmup(total, longest)


def ma_distance_in_atr(close: pd.DataFrame, ma: pd.DataFrame,
                       atr: pd.DataFrame) -> pd.DataFrame:
    """`(close − MA) / ATR` —— **以 ATR 为单位的"离均线多远"**。

    为什么要**除以 ATR**（而不是用固定百分比）：
      11.5 条件② 的原话是「误差 ≤ **1 个 ATR**」——
      **ATR 归一是让"贴近"对高波动股票自动放宽**。
      若改成一个固定百分比，等于**换了规则**（高 ATR 的票会被误排除）。

    正负号有意义：正值 = 价格在均线**上方**，负值 = 下方。
    （条件② 只看 `abs(...) ≤ 1`，但符号留给报告用 —— "受上方均线压制"是 pass 原因之一。）

    前导 `NaN` 行数 = **`max(MA 的, ATR 的)`** —— 调用方的 `min_window` 要照此声明。

    ⚠️ **`ATR == 0` → 结果为 `NaN`（无定义），不是 `0`、更不是 `inf`**：
       `ATR = 0` 意味着窗口内价格一动没动（停牌 / 极不活跃）——
       此时"离均线几个 ATR"**没有意义**。若不挡，`x/0` 会给出 `inf`，
       而 `inf` 会一路传进统计（**不会报错**），把当天那一行悄悄毁掉。
    """
    safe_atr = atr.where(atr > 0.0)
    return (close - ma) / safe_atr


def n_day_return(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """`close / close.shift(window) − 1` —— N 日收益率。

    首位有效值在索引 `window`（`shift(window)` 让前 `window` 行为 `NaN`）。
    """
    return close / close.shift(window) - 1.0


def rise_from_low(close: pd.DataFrame, low: pd.DataFrame,
                  window: int) -> pd.DataFrame:
    """`close / min(low, 过去 window 天) − 1` —— **从区间最低点涨了多少**。

    ⚠️ **这不是"N 日收益率"**（`n_day_return` 才是）。
       11.5 条件③ 的原话是「过去 150 或 260 个交易日，**从最低点**涨 > 30–50%」——
       比的是**当前价 vs 区间最低点**，不是"当前价 vs N 天前"。
       两者可以差很远（一路阴跌后反弹的票：N 日收益为负，但离最低点可能已涨不少）。
    """
    lowest = low.rolling(window).min()
    return mask_warmup(close / lowest - 1.0, window)
