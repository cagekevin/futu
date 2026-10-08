# engine —— 计算（纯函数，零 IO）

> **铁律：`engine/` 零 IO** —— 不联网、不读文件、不写文件（承 G1/G2）。
> 输入是数据，输出是数据；谁把结果存起来是 `store/` + `pipeline.py` 的事。

---

## 1. 模块地图

| 文件 | 干什么 | 被谁用 |
|---|---|---|
| **`adjust.py`** | ★**复权唯一实现**（hfq / qfq） | `provide`（`--adjust`）、`pipeline` |
| **`time_alignment.py`** | 多品种逐 bar 对齐（交集优先） | `provide.align_panel` |
| `gex.py` | 期权 GEX / zero gamma / walls / PCR | `pipeline`（期权类数据项） |
| `greeks.py` | Black-Scholes 希腊值（纯函数） | `gex.py` |
| `rps.py` | 相对强度：RS 线 / Mansfield / RPS | `pipeline`（行业 / 板块状态） |
| `industry.py` | 行业聚合统计 + 状态分类 | `pipeline` |

---

## 2. `adjust.py` —— 复权（唯一实现）

```python
HFQ = "hfq"          # 后复权（锚定第一根，严格因果）← 回测必须用
QFQ = "qfq"          # 前复权（锚定最新根，含未来除权信息）
MODES = (HFQ, QFQ)

class AdjustmentError(Exception)

@dataclass(frozen=True)
class AdjustmentResult:
    days, values, multipliers, unusable_days, mode,
    n_split_events, n_dividend_events

adjust_series(days, values, factor_rows, *, mode: str) -> AdjustmentResult
```

- `mode` **必须显式传，无默认**（承 P1：口径消耗必须显形）
- `days` 必须升序

### 口径分工（写死）

| 口径 | 因果性 | 用在哪 |
|---|---|---|
| **hfq 后复权** | **严格因果**（`factor[t]` 只依赖 `≤ t` 的事件） | **回测 / 因子计算** |
| qfq 前复权 | **含未来**（`factor[t]` 依赖 `t` 之后的除权） | 仅**界面展示** |

> **前复权是最隐蔽的未来函数之一**：它看起来"方便"（最新价 = 真实市价），
> 但让策略**提前知道未来的分红送转**。

### 刻意未做的边界

| 项 | 状态 |
|---|---|
| **派现日的后复权加法项** | **形式未定** → 该日进 `unusable_days`（值为 `None`），下游必须**显形处理**，不许当 0、不许 ffill |
| "因子不全就报错" | **故意不实现** —— 无法区分"真没除权"与"我们没拉" |

---

## 3. `time_alignment.py` —— 多品种对齐

```python
DEFAULT_MIN_BARS = 30
class AlignmentError(ValueError)

@dataclass(frozen=True)
class AlignedPanel:
    index, values, strategy, n_union, n_dropped, n_filled
    column(symbol) -> ...
    n_symbols / n_bars   # property

align_by_intersection(series, *, min_bars=DEFAULT_MIN_BARS) -> AlignedPanel
```

- **默认交集** —— 只保留所有品种**都有真实报价**的 bar，彻底消除休市 ffill 造出的假 K 线
- 交集太小（`< min_bars`）才降级：**并集 + 仅 ffill**（**禁 bfill**，即禁未来函数）
- 对齐对象是**整根 bar**（OHLCV），不是单列

> **定位澄清**：它**不是"停牌"的解法** —— 停牌的职责只有一句「不造假」（存储不补）。
> 对齐是**查询 / 组织**能力，所以它**不存储、不强制、按需调用**。

---

## 4. 其余模块

### `gex.py`（期权指标）

| 函数 | 作用 |
|---|---|
| `compute(chain, spot, *, as_of, now_et, r, source_key, bucket="Tout", weight_col="open_interest")` | ★**主入口**，一次算出全部期权指标 |
| `zero_gamma(w, spot, r, weight_col="open_interest")` | zero gamma 水平 |
| `gex_by_strike(w, spot, r, ref_spot=None)` | 逐行权价 GEX |
| `walls(w, spot, r, ref_spot=None)` | `call_wall` / `put_wall` |
| `absolute_walls(...)` | `abs_call_wall` / `abs_put_wall` |
| `put_call_ratios(w)` | `pc_oi` / `pc_volume` |

`compute` 返回 20 余个字段（含 `bucket` / `weight_col` / `dte_lo` / `dte_hi` / `source_key` / `computed_at` / `as_of` 自证字段）。

### `greeks.py`（Black-Scholes，接受标量或 ndarray）

`call_price` / `put_price` / `call_delta` / `put_delta` / `gamma` / `vega` /
`call_theta` / `put_theta` / `vanna` / `charm` / `charm_per_day`（签名均 `(s, k, t, r, sigma)`）、
`implied_vol(price, s, k, t, r, is_call, tol=1e-6, max_iter=60)`

### `rps.py`（相对强度）

常量：`WEEKS_52_IN_DAILY=250`、`MANSFIELD_WEEKS=52`、`RPS_MIN=80.0`、`RPS_MIN_CLASSIC=90.0`

`rs_line(close, bench)` / `to_weekly(dates, values)` / `mansfield(weekly_rs, n)` /
`change(close, n)` / `rps(returns)` / `off_high(series, lookback=None)`

### `industry.py`（行业聚合）

`STATE_THRESHOLD=0.6`、`STATE_FLAT=0.05`、`STATE_ORDER`（10 态）、`STATE_NOTE`

`IndustryStat`（`industry, count, median, mean, std, up_ratio, rps` + `direction` / `agreement`）、
`classify(by_days, *, thr, flat)`、`industry_stats(rows, key="chg250", min_count=5)`

> **与 `factor-layer/exposure/` 不重叠**：本模块按**行业**聚合（一行业一条），
> 那里按**股票**铺开（`date × symbol`）—— 聚合方向**垂直**。

---

## 5. 测试

- `tests/test_engine.py` —— M3 标准 G1–G6 + 希腊值正确性
- `tests/test_adjust.py` —— 复权唯一实现（先红后绿）
- `tests/test_adjust_factor.py` —— 复权口径：K 线统一不复权 + 因子单独存
- `tests/test_time_alignment.py` —— 对齐（交集优先、仅 ffill、禁 bfill）
- `tests/verify_adjustment_formula.py` —— 对拍：我方复权公式 vs 富途自身复权 K 线
