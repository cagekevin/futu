# M3 factor —— 因子定义与注册表

> **只产「原始值」**（承 A1）。标准化 / 中性化 / 填缺**一律不在这里做**（那是 M4）。
> 零 IO：只吃 `FactorInput`（面板切片），只吐 `pd.DataFrame`。

---

## 1. 数据结构

### `FactorSpec`（`factor_spec.py`，frozen，六要素**全必填、构造时校验**）

| 字段 | 含义 |
|---|---|
| `name` | 因子名（**原始名，禁派生前缀**） |
| `inputs` | 依赖的面板字段，如 `("close",)` |
| `min_window` | warm-up 窗口（`ret20` → 20） |
| `frequency` | 目前仅 `"1d"` |
| `adjust` | **必须显式**（`hfq` / `qfq`），禁 `None` |
| **`direction`** | **`+1` = 值越大越看多；`-1` = 越小越看多** |

**`FactorName`**（frozen）：`parse(cls, name) -> FactorName`（先查 `z_neu_`）、
`.standardized`（→ `z_<raw>`）、`.neutralized`（→ `z_neu_<raw>`）

常量：`DIRECTION_LONG=1`、`DIRECTION_SHORT=-1`、`STANDARDIZED_PREFIX="z_"`、`NEUTRALIZED_PREFIX="z_neu_"`

> **`direction` 是先验**（会被 IC 推翻），故每个因子的 docstring **必须写明先验来源**。

### `factor_protocol.py`

| 类型 | 说明 |
|---|---|
| `FactorInput`（frozen） | 字段 `fields`；`.field(name) -> DataFrame` —— **只能用声明过的字段**，否则报错 |
| `FactorValues`（frozen） | 字段 `spec`、`values`；属性 `.name`、`.direction` |
| `Factor`（Protocol） | 约定：`spec: FactorSpec` + `compute(self, data: FactorInput) -> pd.DataFrame` |

---

## 2. 注册表 `factor_registry.py`

```python
register_factor(factor: Factor) -> Factor      # 重名 / 空名 → 报错
get_factor(name: str) -> Factor
available_factors() -> list[str]               # 全量（含 screening）
alpha_factors() -> list[str]                   # ★ 评估请用这个（见 §3 的 role）
factor_inputs(spec: FactorSpec, panel: CrossSectionPanel) -> FactorInput
run_factor(name: str, panel: CrossSectionPanel) -> FactorValues   # ★唯一入口
```

`run_factor` 会校验：**口径是否匹配** / 形状是否正确 / **warm-up 期是否全 NaN**。

---

## 3. 已实现的因子 `implementations/`（**20 个**）

`__init__.py` 里逐行 import（**导入即注册**）—— **加新因子就在这里加一行**。

### `role = alpha`（17 个）—— **可独立评估**，评估只跑这一组

| 文件 | 测什么 | `inputs` | `min_window` | `direction` |
|---|---|---|---|---|
| `ret5` / `ret20` | 5 / 20 日收益 | `close` | 5 / 20 | **−1**（短期反转）|
| `ret60` / `ret150` / `ret260` | 60 / 150 / 260 日收益 | `close` | 60 / 150 / 260 | **+1**（中期动量）|
| `off_low150` / `off_low260` | **距区间最低点的涨幅**（≠ N 日收益）| `close`,`low` | 150 / 260 | **+1** |
| `vol20` | `log_ret` 的 20 日**离散度** | `close` | 20 | **−1**（低波动异象）|
| `skew20` | `log_ret` 的 20 日**不对称性** | `close` | 20 | **−1** |
| `max20` | `log_ret` 的 20 日**极值** | `close` | 20 | **−1**（彩票偏好）|
| `turn20` | 20 日**量能**（唯一用 `volume` 的）| `volume` | 20 | **−1** |
| `rsi14` | **RSI(14)**（Wilder 平滑）| `close` | 14 | **−1**（见其 docstring 的张力说明）|
| `atr14` | 真实波幅（**价格单位**）| `high`,`low`,`close` | 14 | **−1** |
| `atr_pct14` | 真实波幅 / 收盘（**含跳空**）| `high`,`low`,`close` | 14 | **−1** |
| `adr20` | 日均波幅 / 收盘（**不含跳空**，§10.6 的过滤器）| `high`,`low`,`close` | 20 | **−1** |
| `rs_rank` | **池内截面相对强度百分位**（IBD 加权 3/6/9/12 月）| `close` | 252 | **+1** |
| `near_52w_high` | `close / 过去 250 天最高价`（**距 52 周新高**）| `high`,`close` | 250 | **+1** |

### `role = screening`（7 个）—— **筛选原料**，**不参与 IC 评估**

| 文件 | 是什么 | `inputs` | `min_window` | 用途 |
|---|---|---|---|---|
| `ma_dist_ema10` / `ma_dist_ema20` / `ma_dist_ema50` | 到短中期 EMA 的 **ATR 归一距离** | `high`,`low`,`close` | 14 / 20 / 50 | 11.5 条件② |
| `ma_dist_sma150` / `ma_dist_sma200` | 到长期 SMA 的同款距离 | `high`,`low`,`close` | 150 / 200 | 11.5 条件② |
| `range_pct10` | **10 日区间波幅**（`(最高−最低)/close`）| `high`,`low`,`close` | 10 | §7.1 VCP 第 4、5 条：**波幅收缩**的原料 |
| `vol_ratio10_50` | **量能趋势**（`mean(vol,10)/mean(vol,50)`）| `volume` | 50 | §7.1 VCP 第 6 条：**最后配合成交量下跌** |

> ⚠️ **为什么登记「距离」而不是「均线值」**：均线是**价格的平滑**，
> 直接排序 `ema20` 等于排序价格。而条件② 要的是 `|距离| ≤ 1`，
> 且**必须除以 ATR**（原话是"误差 ≤ 1 **个 ATR**"）—— 换成固定百分比就是**换了规则**。

> **同原料 ≠ 同因子**：`vol20` / `skew20` / `max20` 都从 `log_ret` 来，但分别测离散度 / 不对称性 / 尾部；
> `atr_pct14` 与 `adr20` 都测"波幅占价格比"，但**一个含跳空、一个不含**。
> **它们是否真独立，靠截面相关去验，不靠名字。**

### `technical_indicators.py` —— 指标的**唯一实现点**

`RSI` / `ATR` / `ADR%` / `EMA` / `SMA` / `均线距离` / `离底涨幅` 的公式**只写在那里**，
因子文件只做**薄封装**。理由：`atr14`、`atr_pct14`、`ma_dist_*` 都要 ATR ——
各写一遍就是 **6 处 ATR**，改一次口径要改 6 个文件（迟早"两份真相"）。

⚠️ **两个静默出错的边界**（都在那里显式处理，且有测试锁死）：
- **RSI 遇上"完全平盘"** ⇒ 取 **50**（不是 100）——
  否则停牌 / 极不活跃的票会被判成"极强"，**混进条件⑤（`RSI > 50`）**
- **ATR == 0** ⇒ 均线距离取 **`NaN`**（不是 `inf`）—— 否则 `inf` 会一路传进统计**而不报错**

### `role`：`alpha` vs `screening`（2026-10-08 追加的第七要素）

| role | 是什么 | 拿去算 IC |
|---|---|---|
| **`alpha`** | 可独立评估的因子（上表 15 个）| ✅ 应该评 |
| **`screening`** | 筛选原料（均线距离…）| ❌ **排序它 = 排序价格** ⇒ 假阳性 |

⚠️ **评估 / 报告请用 `alpha_factors()`**，不要用 `available_factors()`（后者是全量）。

> **`min_window` 的口径**：`vol20` 是 **20** 不是 21 —— `rolling(20).std()` 在索引 20 才拿到 20 个有效收益率，
> 故前 20 行为 NaN。

---

## 4. ★ 加一个新因子（只需 2 处改动）

```python
# 1) 新建 factor/implementations/<name>.py
from ..factor_spec import FactorSpec, DIRECTION_LONG
from ..factor_protocol import FactorInput, FactorValues
from ..factor_registry import register_factor

class MyFactor:
    spec = FactorSpec(
        name="my_factor",
        inputs=("close",),
        min_window=10,
        frequency="1d",
        adjust="hfq",          # 必须与 read_panel 的口径一致
        direction=DIRECTION_LONG,
    )
    def compute(self, data: FactorInput) -> pd.DataFrame:
        close = data.field("close")
        return close / close.shift(10) - 1.0

register_factor(MyFactor())

# 2) 在 factor/implementations/__init__.py 加一行
from . import my_factor
```

**无需改动**：`factor_registry`、`panel`、`exposure`、`preprocess`、`evaluate` 任何代码。
`run_full_window.py` 用 `available_factors()` **自动发现**新因子。

`tests/test_factor.py::test_a2_add_factor_is_one_file_plus_one_line` 断言「文件集 == 注册表条目集」。

---

## 5. 约束

| # | 约束 | 验收 |
|---|---|---|
| **A1** | **因子只产原始值** —— 禁截尾 / 标准化 / 中性化 / 填缺 | `grep implementations/ → 无 winsorize/standardize/neutralize/fillna` |
| **A2** | **注册表制** | 加因子 = **1 文件 + 1 行**；重名 → 报错（不静默覆盖） |
| **A3** | **六要素必填无默认** | 缺任一 → 报错；`adjust` 禁 `None`；`direction ∈ {+1, −1}` |
| **A4** | **warm-up 期输出必须是 NaN** | `ret20` 前 19 个位置**全为 NaN**（不是 0、不是常数、不是前值填充） |
| **A5** | **命名编码血缘** | `ret20` → `z_ret20` → `z_neu_ret20`；`parse("z_neu_ret20").raw == "ret20"` |

---

## 6. 陷阱

| # | 陷阱 | 后果 |
|---|---|---|
| T1 | **因子自己做了预处理**（违反 A1） | M4 再标准化一次 = 双重处理；且**原始值丢失**，无法复算 |
| T2 | **`min_window` 不声明或声明错** | warm-up 期垃圾值进 IC，**IC 失真** |
| T3 | **复权口径不声明** | 因子用 raw 价、回测用 hfq → 除权日**假跳变**（`run_factor` 会因此报错） |
| T4 | **用未声明的字段** | `FactorInput.field()` 报错（否则依赖清单是谎言） |

---

## 7. 测试

`tests/test_factor.py` —— 25 项

**★ 免费验收手段**：`snapshot` 里有 `chg20 / chg50 / chg120 / chg250`，
用 K 线自算的 `ret20` 应与同日 `chg20` **高度一致** → 零成本的实现正确性交叉验证。
