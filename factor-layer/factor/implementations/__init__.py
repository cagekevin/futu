"""M3 因子实现 —— **导入即注册**（照 `backtest/strategies/` 的写法）。

⚠️ 用之前必须 `import factor.implementations`。
本模块**故意不做**自动导入 —— 若 `factor/__init__.py` 里 import 它，
会与 `factor_registry` 形成**循环 import**。
`get_factor()` 在注册表为空时会给出可操作的提示。

**加一个新因子 = 加一个文件 + 在本文件加一行 import**（承 A2）。

## 因子清单（按 `role` 分组 —— 见 `factor_spec` 的 role 契约变更）

### `role = alpha`：**可独立评估**（评估只跑这一组）

| 原料 | 因子 | 测什么 |
|---|---|---|
| `close` | `ret5` / `ret20` / `ret60` | 短 / 中周期收益（反转 → 动量）|
| `close` | `ret150` / `ret260` | 长周期收益（动量）|
| `close`+`low` | `off_low150` / `off_low260` | **离区间最低点多远**（≠ N 日收益）|
| `close` | `vol20` / `skew20` / `max20` | `log_ret` 的二阶矩 / 三阶矩 / 极值 |
| `volume` | `turn20` | 量能 —— 唯一没被 `close` 覆盖的维度 |
| `close` | `rsi14` | **RSI**（11.5 条件①⑤ 的原料）|
| `high`+`low`+`close` | `atr14` | 真实波幅（**价格单位**）|
| `high`+`low`+`close` | `atr_pct14` | 真实波幅 / 收盘（**含跳空**）|
| `high`+`low`+`close` | `adr20` | 日均波幅 / 收盘（**不含跳空**，§10.6 的过滤器）|
| `close` | **`rs_rank`** | **池内截面相对强度百分位**（§7.1 VCP 第 2 条「RS ≥ 90」）|
| `high`+`close` | **`near_52w_high`** | **距 52 周新高**（§7.1 VCP 第 3 条「不低于 15%」）|

### `role = screening`：**筛选原料**（**不参与 IC 评估**）

| 原料 | 因子 | 用途 |
|---|---|---|
| `high`+`low`+`close` | `ma_dist_ema10` / `ma_dist_ema20` / `ma_dist_ema50` | 11.5 条件②：到短中期 EMA 的**ATR 归一距离** |
| `high`+`low`+`close` | `ma_dist_sma150` / `ma_dist_sma200` | 11.5 条件②：到长期 SMA 的同款距离 |
| `high`+`low`+`close` | **`range_pct10`** | §7.1 VCP 第 4、5 条：**波幅收缩**的原料 |
| `volume` | **`vol_ratio10_50`** | §7.1 VCP 第 6 条：**最后配合成交量下跌** |

> ⚠️ **均线值不是因子**（它是价格的平滑，排序它 = 排序价格）——
> 所以登记的是**距离**，且是**除以 ATR** 的距离（条件② 原话是"误差 ≤ 1 个 ATR"）。

> ⚠️ **同原料 ≠ 同因子**：`vol20` / `skew20` / `max20` 都从 `log_ret` 来，
> 但分别测离散度 / 不对称性 / 尾部。`atr_pct14` 与 `adr20` 都测"波幅占价格比"，
> 但**一个是含跳空的 ATR、一个是不含跳空的日均幅**。
> **它们是否真的独立，要靠截面相关去验，不靠名字。**
"""
from . import (  # noqa: F401
    # ── role = alpha（可独立评估）──
    adr20,
    atr14,
    atr_pct14,
    max20,
    near_52w_high,
    off_low150,
    off_low260,
    ret5,
    ret20,
    ret60,
    ret150,
    ret260,
    rs_rank,
    rsi14,
    skew20,
    turn20,
    vol20,
    # ── role = screening（筛选原料，不参与 IC）──
    ma_dist_ema10,
    ma_dist_ema20,
    ma_dist_ema50,
    ma_dist_sma150,
    ma_dist_sma200,
    daily_range_pct,
    range_pct10,
    vol_ratio10_50,
)

__all__ = [
    # alpha
    "adr20", "atr14", "atr_pct14", "max20", "near_52w_high",
    "off_low150", "off_low260", "ret5", "ret20", "ret60", "ret150",
    "ret260", "rs_rank", "rsi14", "skew20", "turn20", "vol20",
    # screening
    "daily_range_pct", "ma_dist_ema10", "ma_dist_ema20", "ma_dist_ema50",
    "ma_dist_sma150", "ma_dist_sma200", "range_pct10", "vol_ratio10_50",
]
