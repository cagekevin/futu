# M4 preprocess —— 预处理（四步，顺序写死）

> **把原始因子变成可横截面比较的标准因子。** 只改值，不改口径；**原始因子必须保留**。

---

## 1. 顺序（写死，调换即报错）

```
原始因子 → ① 去极值 → ② 补缺 → ③ 标准化 → ④ 中性化 → 标准因子
```

**为什么是这个顺序**：先去极值（否则均值/标准差被极端值带偏）→ 再补缺（去极值后中位数更稳）
→ 再标准化（回归对量纲敏感）→ **最后中性化**（扣掉行业和市值后，不该再做任何会重新引入暴露的处理）。

---

## 2. 配置 `preprocess_pipeline.py`

```python
WINSORIZE_METHODS   = ("mad", "none")
IMPUTE_METHODS      = ("cross_section_median", "none")
STANDARDIZE_METHODS = ("zscore", "none")
NEUTRALIZE_METHODS  = ("size_industry", "size", "industry", "none")

@dataclass(frozen=True)
class PreprocessConfig:            # 全必填无默认，构造时校验
    winsorize_method: str
    winsorize_n: float             # > 0
    impute_method: str
    impute_threshold: float        # (0, 1]
    standardize_method: str
    neutralize_method: str         # 禁 None；不做写 "none"
```

### 首批只实现 4 个方法（定死）

| 步骤 | 方法 | 参数 |
|---|---|---|
| 去极值 | `mad` | `n = 5.0`（逐日 `median ± n × MAD` 后 clip） |
| 补缺 | `cross_section_median` | 逐日填当日横截面中位数 |
| 标准化 | `zscore` | 逐日 `(x − mean) / std` |
| 中性化 | `size_industry` | 逐日 OLS：`factor ~ 1 + log_mktcap + industry_dummies`，**取残差** |

---

## 3. 唯一入口

```python
derived_name(source_name: str, config: PreprocessConfig) -> str
    # → "z_<name>" 或 "z_neu_<name>"

read_preprocessed_factor(values: FactorValues, exposures: ExposureSet,
                         config: PreprocessConfig) -> PreprocessedFactor
```

```python
@dataclass(frozen=True, eq=False)
class PreprocessedFactor:
    name      # 派生名
    source    # 原始 FactorSpec（血缘）
    values    # DataFrame
    log       # dict：每步改动量
    # property: direction
```

---

## 4. 四个算法模块（均返回 `(新宽表, 改动量dict)`，**不原地改**）

| 文件 | 函数 | 说明 |
|---|---|---|
| `factor_winsorize.py` | `winsorize(values, *, method, n)` | 逐日 `median ± n×MAD` clip |
| `factor_impute.py` | `impute(values, *, method, threshold)` | 逐日横截面中位数补缺（**本层唯一允许填充处**） |
| `factor_standardize.py` | `standardize(values, *, method)` | 逐日 zscore（`std=0` → 整日 NaN） |
| `factor_neutralize.py` | `neutralize(values, exposures, *, method)` | 逐日 OLS 取残差 |

---

## 5. 约束

| # | 约束 | 验收 |
|---|---|---|
| **T1** | **顺序固定**，调换即报错 | 构造使两种顺序结果不同的数据 → 断言结果是**前者**；另加源码级 `ast` 断言 |
| **T2** | **每步改动量显形**（clip 几个 / 填几个 / 剔除几个） | 运行日志**必含三组计数**；某日填充率 > 阈值 → 告警 |
| **T3** | **中性化验收**：与 `log(市值)` 的截面相关 **< 0.02** | 逐日算，取绝对值最大者（实测真实数据 **1.19e-15**） |
| **T4** | **配置 frozen + 构造时校验** | `PreprocessConfig(winsorize_n=-1)` → **构造时**抛错（不是运行时） |
| **T5** | **派生必须改名，禁原地覆盖** | 预处理后原始因子仍可读；`grep → 无对原始因子名的原地赋值` |

> **T3 的性质澄清**：OLS 残差与回归元**正交**是数学性质 ⇒ `corr(残差, size) ≈ 0`
> **在正确实现下必然成立**。所以它不是「可能失败的验收」，而是**实现正确性的探针**
> （漏截距 / 没真回归 / 哑变量没去列 都会打破它）。配**阳性对照**（只减均值 → corr > 0.9）。

---

## 6. 陷阱

| # | 陷阱 | 后果 |
|---|---|---|
| T1 | **顺序被调换（静默）** | 先标准化再去极值 / 先中性化再标准化 → 结果完全不同**且不报错**，所有历史结论**作废（不可比）** |
| T2 | **中性化回归写错，但 IC 反而更好看** | 漏截距 / 行业做完整 one-hot（与截距共线 → 矩阵奇异）/ 市值不取 log / 缺失暴露的标的**静默丢弃**（丢的往往是小盘股）。这些错法大多让残差更「干净」，**IC 看起来更好** |
| T3 | **补缺静默改变样本** | 填中位数会「造出」原本不存在的数据点。**若某日填了 40%，那一日的 IC 基本无意义** |

---

## 7. 日志语义（写码时的修正，值得记）

- **空日**（全 NaN，如 warm-up）与 **退化日**（有值但 `MAD`/`std` == 0）**必须分开报**
  —— 混成一个数会让 warm-up 看起来像数据有问题，而**真正的退化反被淹没**
- 字段：`*_empty_days` vs `*_degenerate_days`；空日**不计入**「填充超阈值」

---

## 8. 测试

`tests/test_preprocess.py` —— 21 项（含源码级 `ast` 检查四步调用顺序）
