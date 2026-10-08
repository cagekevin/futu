# M2 exposure —— 风险暴露（构造 `date × symbol` 矩阵）

> **构造**暴露供 M4 **使用**。M2 **不自己取数** —— 需要 `snapshot` 也必须经 M1（承 M1-1）。

---

## 1. 为什么需要它

> 不扣掉行业 / 市值，你测到的可能是「**这个行业在涨**」而不是「**这只股票有优势**」。

---

## 2. 数据结构 `exposure_types.py`

```python
@dataclass(frozen=True, eq=False)
class ExposureSet:
    dates, symbols
    size                     # DataFrame：log 市值
    industry                 # DataFrame：行业标签
    min_industry_count       # 分组阈值
    coverage                 # dict：覆盖率报告
    size_is_approximated     # 逐日 bool：是否用了历史近似
    industry_is_approximated # 逐日 bool

    def industry_dummies(self, day) -> pd.DataFrame   # 已去一列，优先去 other
```

**常量**：`INDUSTRY_OTHER="other"`、`MIN_INDUSTRY_COUNT_FLOOR=2`、`MIN_INDUSTRY_COUNT_RECOMMENDED=3`

---

## 3. 接口

### `size_exposure.py`

| 函数 | 作用 |
|---|---|
| `implied_shares(snapshot_rows) -> dict[str, float]` | 快照 → 隐含股本（`market_cap / price`） |
| `build_size_exposure(raw_close, shares) -> pd.DataFrame` | `size = log(股本 × raw_close)` |

> ⚠️ **必须用 raw close，不能用 hfq** —— hfq 比值多一个**因股而异**的累积复权因子，
> 会把 size 的**横截面排序**拧歪。⇒ M2 会**再取一次 raw 面板**（额外取数，不是冗余）。

### `industry_exposure.py`

| 函数 | 作用 |
|---|---|
| `industry_labels(snapshot_rows) -> dict[str, str]` | 快照 → `{symbol: 行业名}` |
| `build_industry_exposure(industry_of, dates, symbols, present_by_day, *, min_industry_count) -> pd.DataFrame` | 逐日行业标签（**低频行业归 `other`**） |

### `exposure_coverage.py`

| 函数 | 作用 |
|---|---|
| `coverage_report(size, industry, *, min_industry_count) -> dict` | 覆盖率报告，含 **`absorbed`（必须为 0）** |

### `exposure_builder.py`（★唯一入口）

```python
snapshot_rows(record) -> list[dict]

read_exposures(panel, *, min_industry_count=MIN_INDUSTRY_COUNT_RECOMMENDED,
               runner=None) -> ExposureSet
```

`coverage["absorbed"] != 0` 时**报错**（不静默）。

---

## 4. 约束

| # | 约束 | 验收 |
|---|---|---|
| **U1** | `size = log(市值)`；市值缺失 → **NaN**，不填 0、不前值填充 | 缺失标的在中性化时**显形剔除并计数** |
| **U2** | 行业哑变量**必须去一列**，禁完整 one-hot | 构造行业矩阵 → **秩 = 行业数 − 1** |
| **U3** | 分组阈值**必填 + 显形**；样本量 < 阈值的行业归**显式 `other`** | 日志打印「行业数 / `other` 组标的数 / **被完全吸收的标的数（必须为 0）**」 |
| **U4** | 历史暴露的近似**必须显形** | 输出含 `size_is_approximated` / `industry_is_approximated` 标记 |
| **U5** | 覆盖率**必须报告** | 打印「size 缺失 N / industry 缺失 M / 归 other K」 |

**阈值决策（定死）：`≥ 3`**。理由：`≥2` 是数学硬底线（n=1 时残差恒为 0），
`≥5` 覆盖率代价过大（39% 进 `other`）。实测 ≥3 → 30 个行业、覆盖 81%。

---

## 5. 陷阱（都是「不报错但毁数据」型）

| # | 陷阱 | 后果 |
|---|---|---|
| **T1** | **低频行业被回归完全吸收 → 该股票因子值恒为 0** | 票池 269 只 / 68 个行业，分布极不均。**行业样本量 = 1 时，哑变量系数 = 该股因子值 → 残差恒等于 0**，股票「消失」**且不报错** |
| **T2** | **用当前市值反推历史市值 = 前视偏差** | 反推式含未来信息（当前股本结构是未来才知道的），**且不可消除** |
| **T3** | **「当前快照」被当成「历史快照」** | 行业分类若用 2026 年的去做 2022 年 = 假设行业从未变更 |

> **实测背景**：库里 `snapshot` **只有 1 天**（2026-10-06）⇒ 历史 size / industry
> 只能近似，**T2 / T3 是当前无法消除的**。这一点必须写进任何使用暴露的结论里。

---

## 6. 测试

`tests/test_exposure.py` —— 21 项（含端到端：归 `other` 19%、`absorbed = 0`）
