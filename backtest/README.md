# backtest —— 回测与验证层

> **读这一页就知道回测层能做什么。** 设计原理在 `docs/design/`，本文只做**能力速查**。

---

## 1. 一句话定位

> **把「你的策略」放到「历史事实」上，用唯一成本假设折算 PnL，再用 8 条硬规则判决
> —— 只出结论（报告），不落盘数据。**

**回测层的全部职责只有四件**：**PnL / 指标 / 评分 / 判决**。
**不生产数据、不落盘数据** —— 只输出**报告**（报告是结论，不是市场数据）。

---

## 2. 主链路

```
run_backtest.py
   → data_source        取数（跨进程 provide.cli，唯一入口）
   → strategy_contract  喂策略（注册表制）
   → pnl_engine         因子 → 仓位 → PnL（全系统唯一公式）
   → target_label       标签
   → independent_audit  审计（组装 + 判决）
   → walk_forward_validation  分段一致性 + 8 条硬判定
```

`backtest_config.py` 被**全部模块**引用 —— **唯一配置源**。

---

## 3. 各模块能力

### `backtest_config.py`（fan-in = 6）—— 唯一配置源

| 常量 | 值 | 含义 |
|---|---|---|
| `COST_RATE` | `0.0003` | 单边成本（手续费 0.02% + 滑点 0.01%） |
| `MIN_TRADE_EXPOSURE` | `0.05` | `\|pos\|` 低于此值 → 空仓 |
| `POSITION_CAP` | `1.0` | 仓位上限 |
| `TARGET_HORIZON` | `2` | 标签视界 |
| `EVALUATION_WARMUP` | `60` | 前 N 根 bar **不参与任何统计**（唯一评估起点） |
| `WF_FOLDS` / `WF_GAP` | `5` / `20` | 分段数 / purge gap（**须 ≥ `TARGET_HORIZON`**，导入即断言） |
| `MIN_ANN_RET` | `0.02` | 年化 < 2% → **INVALID** |
| `MIN_SHARPE` | `0.5` | Sharpe < 0.5 → **SUSPICIOUS** |
| `MDD_SUSPICIOUS` / `MDD_INVALID` | `0.10` / `0.20` | 回撤阈值 |
| `MAX_SIDE_RATIO` | `0.85` | 单边 > 85% → **SUSPICIOUS**（疑似 beta） |
| `MIN_TRADES_PER_100_BARS` | `0.01` | 每 100 bar 至少 1 笔 |
| `MIN_BARS` | `300` | 对齐交集下限 |

### `data_source.py`（fan-in = 2）—— 唯一数据入口

```python
CONTRACT_VERSION = "1.0"

trading_days() -> list[str]
align_panel(symbols, *, item="kline", days=None,
            min_bars=backtest_config.MIN_BARS) -> dict
    # → {index, values, strategy, n_symbols, n_bars, n_union, n_dropped, n_filled}
extract_field(panel, field) -> dict[str, list[float]]
    # 降级即拒用：非交集 / 缺 bar → 报错
align_daily_items(...)      # ⚠️ 未接通，直接 raise NotImplementedError
```

**为什么用跨进程 CLI**：回测只该依赖**契约**，不该依赖数据层的**模块命名空间**
（数据层已占用 `config` / `engine` / `store` / `provide` / `fetch` / `pipeline` / `vendor` 等顶层名，且仍在演进）。

### `target_label.py`（fan-in = 2）

```python
target_ret(opens) -> list[float]      # log(open[t+2]/open[t+1])，尾部 2 个置 0
```

> **归属**：标签**留在回测层** —— 它含「`t+1` 开盘成交」这个**执行约定**，
> 是**你的假设**，不是纯市场事实。数据层只提供 `open`。

### `pnl_engine.py`（fan-in = 3）—— 全系统唯一 PnL 公式

```python
positions_from_factors(factors) -> list[float]     # tanh 封顶 ±1；|pos| < 阈值归零
per_bar_pnl(positions, target_returns, cost_rate=COST_RATE) -> list[float]
    # 逐 bar：pos*ret − |Δpos|*cost   （换手用上一根仓位算）
turnover_per_bar(positions) -> list[float]
```

**四行公式，每行都有坑**：`tanh` 封顶（不封 → 可能开 100 倍仓）、
`roll(1)` 用**上一根**算换手（用当前 → **未来函数**）、首根视为空仓、**换手成本必须算**。

### `performance_metrics.py`（fan-in = 2）

```python
periods_per_year(times) -> int          # 自动估计，禁写死；越界/过短报错
annualized_return(pnl, ppy) -> float    # = 均值 × ppy（不再除样本长度）
sharpe_ratio(pnl, ppy) -> float
sortino_ratio(pnl, ppy) -> float        # 下行 std 带 20% 地板，clamp 在 ±20
max_drawdown(pnl) -> float
long_short_ratio(positions) -> tuple[float, float]
count_trades(positions) -> int
```

**年化因子必须自动估计**：写死 `6240`（外汇 H1）→ 换日线数据 **Sharpe 被放大 26 倍**。

### `walk_forward_validation.py`（fan-in = 1）

```python
VERDICT_ORDER = {"VALID": 0, "SUSPICIOUS": 1, "INVALID": 2}

segment_consistency_folds(n_bars, *, n_folds=WF_FOLDS, gap=WF_GAP,
                          warmup=EVALUATION_WARMUP) -> list[dict]
    # 把 [warmup, n_bars) 均分成 n 段，铺满全区间；每折 {fold, train, val, gap}

judge_verdict(*, ann_ret, sharpe, mdd, max_side, h1_ann, h2_ann,
              wf_positive, wf_total, cost2x_profitable,
              n_trades, min_trades) -> tuple[str, list[str]]
    # ★ 8 条硬判定全在这里
```

> **名字要诚实**：固定策略**没有训练**，所以这不是真正的 Walk-Forward。
> `train` 段**从不被使用**，`gap` 也因此是空操作。它真实的作用是把样本外切成连续 N 段、
> 数「几段为正」—— 即**分段一致性检验**。

### `independent_audit.py`（fan-in = 1）—— 目标独立

```python
audit_strategy(positions, target_returns, times, *, label="strategy") -> dict
format_audit_report(report: dict) -> str
```

**返回 20 个字段**：`label`、`n_bars`、`n_bars_total`、`warmup`、`ppy`、
`ann_ret`、`sharpe`、`sortino`、`mdd`、`long_ratio`、`short_ratio`、`max_side`、
`n_trades`、`h1_ann`、`h2_ann`、`wf_positive`、`wf_total`、`cost2x_ann`、
`verdict`、`issues`

**V1 的原则**：判决**只用 PnL + 硬规则**，**不掺搜索期的启发式加分**。
审计**不 import** 任何评分 / 搜索模块。

### `strategy_contract.py`（fan-in = 4）—— 与「你的策略」的唯一接口

```python
@dataclass(frozen=True)
class MarketFacts:      # 唯一输入
    times, open, high, low, close, volume    # tuple
    extra: dict[str, tuple[float, ...]]      # ⚠️ 未接通
    def __len__() -> int
    def require_extra(name) -> tuple[float, ...]   # 缺即报错

class Strategy(Protocol):
    name: str
    def compute_factors(self, facts: MarketFacts) -> list[float]

register_strategy(strategy) -> Strategy      # 重名/空名报错
get_strategy(name) -> Strategy
available_strategies() -> list[str]
```

**最小策略示例**：

```python
class YourStrategy:
    name = "your_strategy"
    def compute_factors(self, facts):
        return [0.0] * len(facts)      # 与 facts.times 等长；只用 ≤ t 的事实
register_strategy(YourStrategy())
```

### `strategies/` —— 已注册策略（导入即注册）

| 策略 | 名字 | 逻辑 |
|---|---|---|
| `MomentumStrategy` | `momentum` | 过去 `lookback`(=5) 根收益 × `scale`(=20)，只用 ≤ t 收盘价；warm-up 输出 0 |
| `RegimeGatedMomentumStrategy` | `regime_gated_momentum` | 先用 20 根因果已实现波动率判「环境」；波动率过低 → 因子 0（空仓），否则输出正常动量 |

**加策略 = 加一个文件 + 在 `strategies/__init__.py` 加一行 import。**

### `run_backtest.py` —— 端到端 CLI

```bash
cd backtest
python3 run_backtest.py --list-strategies
python3 run_backtest.py --symbols SPY QQQ --strategy momentum
python3 run_backtest.py --synthetic --symbols SPY --strategy regime_gated_momentum
```

| 参数 | 说明 |
|---|---|
| `--symbols` | 标的（无 `--list-strategies` 时必填） |
| `--item` | 默认 `kline` |
| `--strategy` | 策略注册名，默认 `momentum` |
| `--list-strategies` | 列出已注册策略后退出 |
| `--synthetic` | 用合成随机游走数据（**仅验证代码，不可判断策略**） |
| `--synthetic-bars` / `--seed` | 默认 600 / 42 |

返回码：`0` 成功 / `1` 无可审计标的 / `2` 取数失败或被拒。

---

## 4. ★ `panel_statistics.py` —— **簇级区间 / 多重检验工具（已被 Tugboat 线接线）**

> **这是本层能力最丰富、却尚未接线的模块。** 纯标准库、纯函数、零 IO。
> 当前主链路**只做点估计 + 8 条硬判定**，**未使用任何置信区间或显著性校正**。

| 函数 | 用途 |
|---|---|
| `clustered_interval(clusters, *, statistic, iterations, seed, alpha)` | ★**默认口径**：簇级 bootstrap 区间（重采样**簇**，不重采样单条） |
| `effective_sample_size(clusters) -> (观测数, 簇数)` | 报 `n` 时**同时给簇数** |
| `clustered_cmh(strata) -> ClusteredCmhResult` | 分层比较（**只出 p 值，不判决**） |
| `wilson_interval(successes, n, *, n_clusters, scope, z_value)` | **仅限簇内单序列**；面板数据用它**判错** |
| `benjamini_hochberg(p_values, *, alpha) -> MultipleTestResult` | **报 p 值的唯一判决出口** |
| `dedupe_overlapping_events(event_positions, *, min_gap)` | 事件去重（重叠触发只算 1 次） |
| `format_rate_with_clusters(successes, clusters, *, title, alpha) -> str` | **报比例的唯一出口**（必带观测数与簇数） |

结果类：`ClusteredInterval`（含 `independence_gap`）、`ClusteredCmhResult`、
`MultipleTestResult`（含 `n_discoveries`、`is_significant(index)`）

**为什么不能用 Wilson 算面板比例**（同一批信号，两种算法，结论相反）：

| 口径 | n | Wilson 95% 区间 | 判决 |
|---|---|---|---|
| 天真：n = 品种数 | 200 | `[53.1%, 66.5%]` | 显著优于 50% |
| 真实：只来自 5 个交易日 | 5 | `[23.1%, 88.2%]` | 跨过 50%，**什么都不显著** |

> **区间宽度靠 `1/√n` 缩小，但只有独立观测才能贡献这个缩小**
> —— 把同一个信息重复 200 遍，不会让你多知道 200 倍。

**报比例的硬格式**：

```
胜率 60.0% [簇级 95%: 23.1%, 88.2%]   n=200 观测 / 5 簇
```

---

## 5. 已知未接通的缝

| # | 位置 | 状态 |
|---|---|---|
| 1 | **`panel_statistics.py`** | ✅ **已接线**（`run_tugboat.py` 用 `effective_sample_size` / `benjamini_hochberg`）；**placebo 仍未接** |
| 2 | `MarketFacts.extra` | 已声明契约，**未接通** |
| 3 | `data_source.align_daily_items` | **直接 `raise NotImplementedError`** |
| 4 | **无 placebo 对照**（V10） | `docs/design/01` §12 列为**第一优先级扩展**，未实现 |

---

## 6. 约束索引（编号见 `docs/design/01`）

| 前缀 | 管什么 | 代表条目 |
|---|---|---|
| **B** | 基础层（地基） | B1 唯一配置源、**B2 年化因子自动估计**、B5 gap 按 horizon 标定 |
| **D** | 数据准备 | D1 对齐用交集、D2 标签口径、**D8 回测用 hfq** |
| **R** | 回测层 | R1 PnL 唯一公式、**R2 Sortino 地板**、R3 年化不除样本长度 |
| **V** | 验证层 | **V1 独立审计**、V2 WF 带 gap、V3 8 条判定、V9 污染未来、**V10 placebo**、V12 簇级区间、V18 禁裸 p 值 |
| **E** | 工程层 | E3 因果性测试、E4 长名 |

**若只做三条，就做这三个**（`docs/design/01` §6）：
**B2 年化因子自动估计** / **R2 Sortino 分母地板** / **V1 独立审计**。
第二批补充后 ★★★ 扩到六条，新增：**D8 回测用 hfq** / **V7 物理防泄漏** / **V10 placebo 对照**。

---

## 7. 测试

| 文件 | 类型 | 测什么 |
|---|---|---|
| `tests/test_causality.py` | `unittest.TestCase` | ★**对所有已注册策略**逐点验证：`T2 污染未来`（t 之后换垃圾 → 前缀逐位不变）、`T1 截断不变性`（抓全序列归一化）、`R7 warm-up 输出中性 0` |
| `tests/test_performance_metrics.py` | 纯函数 + `__main__` | 手推已知答案：年化不重复除（R3）、Sortino 有界（R2）、ppy 自动估计（B2）、参数越界报错 |
| `tests/test_statistics.py` | `unittest.TestCase`（6 个测试类） | Wilson 作用域守卫、簇级区间、比例格式、CMH、多重检验、事件去重 |

> ⚠️ `test_performance_metrics.py` **不是 `TestCase`** → `unittest discover` **扫不到**，
> 需直接跑：`python3 tests/test_performance_metrics.py`。

---

## 8. 文档索引

| 想知道 | 去哪 |
|---|---|
| **为什么这么设计**（B/D/R/V/E 全编号） | `docs/design/01-回测与验证系统-design-2026-10-06.md` |
| 可执行步骤（**注意：初版手册，实现已分叉，以代码为准**） | `docs/design/02-回测与验证系统-实施手册-2026-10-06.md` |
| 全仓分层与铁律 | `../Agent.md` |
| 数据能测什么 | `../关于策略/Tugboat/11-验证-数据体检报告-2026-10-08.md` |
