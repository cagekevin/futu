# M5 evaluate —— 评估与判决

> **判定因子有没有截面选股能力。** 零 IO、**只读** —— **不许修改因子值**（承 J1）。
> 这是「回答因子能不能选股」的**最终出口**，也是**标签的唯一实现点**。
>
> 另含 **`placebo/`（随机对照）** —— 回答「某个**选择规则**挑出来的股票，是否优于**同日随机**」。

---

## 1. 文件地图

| 文件 | 作用 |
|---|---|
| `forward_return.py` | ★**标签构造**（全仓唯一实现点） |
| `metrics/ic.py` | RankIC 序列 / ICIR / IC 胜率 / t 检验 |
| `metrics/quantile_returns.py` | 等频分箱 + 分组收益 + 多空收益 + 单调性 |
| `metrics/turnover.py` | 换手 |
| `judgement.py` | BH 校正 + **判决出口**（**无裸判路径**） |
| `evaluator.py` | 组装 + `EvaluationReport`（★ M5 对外唯一入口） |
| **`placebo/`** | ★ **随机对照**（R1 选择集 / R2 抽样 / R3 配对判决）—— 见下 |

### `placebo/` —— 随机对照（R1 / R2 / R3）

> **需求级契约**：`../docs/PRD/02-随机对照-PRD-2026-10-08.md`（约束编号 **C / H / N / Z**）

| 模块 | 文件 | 职责 |
|---|---|---|
| **R1 选择集** | `placebo/selection_contract.py` | 协议 + 输入容器（**物理切片**：拿不到 `day` 之后）|
| | `placebo/selection_registry.py` | 注册表 + 唯一入口 `run_selection`（切片在此做）|
| | `placebo/implementations/` | 规则实现（**导入即注册**）—— 现有 `rsi_tight_consolidation`（11.5 的 RSI 紧密盘整）|
| **R2 对照抽样** | `placebo/null_sampler.py` | 同日 / 同池 / 同数量 → 抽 R 次（向量化无放回）|
| **R3 配对判决** | `placebo/paired_statistics.py` | 逐日配对 + 三档判决（§6.4 **冻结**）|
| | `placebo/placebo_report.py` | 报告结构 + 渲染（**唯一产出物**）|
| | `placebo/baseline_runner.py` | ★ 组装 `run_placebo`（第 4 个文件，**已声明偏离**）|

**与 `metrics/` 的分工（问法不同，不重复）**：

| | 回答什么 | 输入 |
|---|---|---|
| `metrics/` | 「**因子值**越大 → 后续收益越高？」（排序能力）| 连续因子值 |
| `placebo/` | 「**筛出来的那批** → 优于同日随机？」（选择能力）| 0/1 选中集 |

**结论形态**：`metrics/` 出 IC / ICIR；`placebo/` 出**排位 + 三档判决**（通过 / 说不清 / 不通过）。

> ⚠️ **判据③看的是 `real_mean_after_cost`（真实侧绝对收益），不是配对差值** ——
> 因为**配对差值对成本免疫**（两侧同扣）：`(real−c) − (null−c) = real−null`。
> 这是落地时对 §6.4 的**契约级修正**（详见 PRD §五 R3 的落地记录）。

---

## 2. 标签（全仓唯一定义）

```python
TARGET_HORIZON = 2
ENTRY_OFFSET = 1

forward_return(panel, *, field="open") -> pd.DataFrame
```

```
forward_return[t] = log(open[t+2] / open[t+1])      末位 → NaN
```

**为什么是 `t+1 → t+2` 而不是 `t → t+1`**：`t` 日收盘看到 → 决策 → **实际成交在 `t+1` 开盘** → 收益算到 `t+2` 开盘。
用 `close[t] → close[t+1]` 是错的（收盘瞬间无法成交）。

> **与 `backtest/target_label.py` 的唯一差异**：那边边界**置 0**（喂 PnL 引擎），
> 本层边界**置 NaN**（0 会污染 IC）。**公式与成交约定一致**，由跨层契约测试断言「**非边界逐位相等**」。

---

## 3. 指标

### `metrics/ic.py`

```python
MIN_SAMPLES_FLOOR = 2

rank_ic_series(factor, target, *, min_samples) -> DataFrame     # 列：rank_ic + n_valid
ic_statistics(ic: pd.Series, *, min_days) -> dict
    # → icir / ic_win_rate / t_stat / p_value / n_days / ...
```

### `metrics/quantile_returns.py`

```python
quantile_returns(factor, target, *, bins, min_samples)
    -> (分组平均收益 DF, 每组样本数 DF, long_short Series, 跳过的日 list)

monotonicity(quantile_mean: DataFrame, direction: int) -> float
```

**分箱规则（定死）**：**等频、逐日、`bins = 5`、封顶**；多空两边各半仓；**每组样本数必须打印**。

### `metrics/turnover.py`

```python
turnover(factor, target, *, bins, min_samples)
    -> {"series", "mean", "median", "n_days", "skipped_days"}
```

---

## 4. 判决 `judgement.py`

```python
benjamini_hochberg(pvalues, *, alpha) -> list[bool]    # NaN → 报错

@dataclass(frozen=True)
class JudgeThresholds:      # 全必填
    alpha, min_days, min_abs_icir, min_abs_monotonicity,
    cost_bps_per_turnover, min_net_annual_return, annualization_days
    # property: cost_rate_per_turnover

@dataclass(frozen=True)
class JudgeEntry:
    name, p_value, icir, n_days, monotonicity, long_short_mean, turnover_mean

judge_batch(entries, *, thresholds) -> list[dict]
    # 一次 BH 校正整批；每条含 verdict / n_tests / n_tests_in_bh /
    # p_value_raw / p_value_bh_significant / ... / net_annual_return
```

**判决常量**：`VERDICT_SIGNIFICANT="significant"`、`VERDICT_NOT_SIGNIFICANT="not_significant"`、
`VERDICT_INSUFFICIENT="insufficient_data"`

> **BH 的分母是「这一批试了多少个因子」** ⇒ 入口必须是 `judge_batch(entries)`
> **收齐全批 p 值一次校正**。
> ⚠️ 若用 `[p] * n_tests` 做 BH，会**退化成不校正**（`p_(m) = p ≤ m/m × α = α` 恒成立）。

---

## 5. 对外入口 `evaluator.py`

```python
@dataclass(frozen=True)
class EvaluateConfig:      # 全必填
    bins: int              # >= 2
    min_samples: int       # >= bins
    thresholds: JudgeThresholds

evaluate(factor, panel, *, config) -> EvaluationReport
evaluate_many(factors, panel, *, config) -> list[EvaluationReport]   # ★批量 + 一次 BH

REQUIRED_METRICS = ("rank_ic_series", "icir", "ic_win_rate",
                    "t_test", "monotonicity", "long_short", "turnover")
```

### `EvaluationReport`（frozen）字段

| 字段 | 内容 |
|---|---|
| `factor_name` / `direction` | 因子名与方向 |
| `ic` | DataFrame（`rank_ic` + `n_valid`） |
| `ic_stats` | dict |
| `quantile_mean` / `quantile_counts` | 分组收益 / 每组样本数 |
| `long_short` | Series |
| `monotonicity` | float |
| `turnover` | dict |
| `judgement` | dict |
| `log` | dict |
| **`universe`** | ★**票池诊断**（`universe_diagnostics`） |

方法：`has_all_required_metrics() -> bool`、`normalized_ic() -> pd.Series`

---

## 6. 约束

| # | 约束 | 验收 |
|---|---|---|
| **J1** | **只读** —— 不许修改因子值 | `grep evaluate/ → 无对因子值的赋值`（`ast` 级检查） |
| **J2** | **七项指标缺一不可** | 报告缺任一项 → 报错（含**换手**） |
| **J3** | 判决**必须报检验次数 + BH 结果**；**无裸判出口** | 输出含 `n_tests`；`grep → 判决只经 BH` |
| **J4** | **IC 的有效样本必须显形** | 日志含每日 `n_valid`（因子值非 NaN **∩** forward return 非 NaN）；`grep → 无 fillna(0)` |
| **J5** | **方向归一**：单调性与 IC 符号**都**按 `spec.direction` 归一 | 方向**来自 spec，不由调用方临时传**；原始值保留（`rank_ic` + `rank_ic_normalized`） |

---

## 7. 陷阱

| # | 陷阱 | 后果 |
|---|---|---|
| T1 | **IC 算在没对齐的样本上** | ① `corr` 默认行为会**悄悄丢样本**，你永远不知道那天用了多少只；② 先 `fillna(0)` 再算 → IC 被垃圾值污染 |
| T2 | **分箱不均** | 269 只 / `bins=5` → 等频是 54/54/54/54/53；误用**等距**分箱则长尾分布下中间组可能塞进 80% 样本，多空收益**完全不可比** |
| T3 | **多重检验被忽略** | 试 20 个因子，最好的 `p = 0.03` 看起来显著 —— 但**期望有 1 个假阳性**（0.05 × 20） |

---

## 8. 审计时发现并修掉的真问题（值得记住的坑）

| # | 问题 | 修法 |
|---|---|---|
| A1 | `benjamini_hochberg` 的 **NaN 语义**：本层静默跳过，`backtest` 报错 | 改为**报错**；无法检验的由 `judge_batch` 先标 `insufficient_data`，**不进 BH family**（分母不虚高） |
| A2 | **`p = NaN` 被判成 `not_significant`** —— 把「**没证据**」说成「**证据说无效**」 | 新增 `_testable()`；不能检验 → `insufficient_data` |
| A3 | **整批评估因单个因子数据不足而抛异常** | `has_all_required_metrics` 改为**结构检查**（只看字段在不在）；值的非 NaN 检查归测试 |
| A4 | **`benjamini_hochberg` 按 p 值排序返回**（不是原始下标）→ 调用方顺序全错 | 已修 + 加回归测试 |
| A5 | **IC 未按 `direction` 归一** → 报告出现「单调性 +1.000 但 ICIR = −0.124」的**自相矛盾** | IC 也归一，且**原始值保留** |

---

## 9. 测试

`tests/test_evaluate.py` —— 42 项
