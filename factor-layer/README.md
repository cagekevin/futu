# factor-layer —— 截面因子层

> **读这一页就知道因子层能做什么。** 细节按 §6 索引往下点，**不必读源码**。
> 本文件是**能力速查卡**；需求级契约在 `docs/PRD/`。

---

## 1. 一句话定位

> **把「某只股票在某一天、在一群同类股票里排第几」这件事，从原始因子一路做到有效性判决。**

| 维度 | 内容 |
|---|---|
| **给谁用** | ① 你自己（美股选股研究）；② 下游 `backtest/`（**可选**，经候选池文件） |
| **核心价值** | 补上现有系统的**横截面**那一半 |
| **不做什么** | 不取数、不做时序回测与 PnL、不做组合权重优化、不下单 |

**为什么不能没有它**：`backtest/` 是**纯时序验证**（WF / purge / placebo / 污染未来），
它能回答「有没有偷看未来」，但**回答不了「这个因子有没有排序能力」**。

---

## 2. 模块地图（依赖方向单向，不可逆）

```
M1 panel      →  外部契约（provide，跨进程 JSON）★ 本层唯一取数入口
M2 exposure   →  M1（经 M1 的 provide_reader）
M3 factor     →  M1
M4 preprocess →  M1、M2、M3
M5 evaluate   →  M1、M4
```

| # | 模块 | 职责 | README |
|---|---|---|---|
| **M1** | **panel** | 截面数据结构（宽表 ⇄ 长表）、取数、票池切片 | [`panel/README.md`](panel/README.md) |
| **M2** | **exposure** | 构造 `date × symbol` 风险暴露（size + industry）| [`exposure/README.md`](exposure/README.md) |
| **M3** | **factor** | 因子注册表 + 因子实现（每个因子一个文件）| [`factor/README.md`](factor/README.md) |
| **M4** | **preprocess** | 去极值 → 补缺 → 标准化 → 中性化（**顺序写死**）| [`preprocess/README.md`](preprocess/README.md) |
| **M5** | **evaluate** | RankIC / ICIR / 分组单调性 / 换手 + BH 判决 | [`evaluate/README.md`](evaluate/README.md) |

**与 `backtest/` 的边界（写死）**：两层都消费 `provide`，但**形状相反**
（本层要「多标的 × 单日」，`backtest` 要「单标的 × 时间序列」）。
**两层互不 import**，只经「候选池文件」交接（承 `Agent.md` §1）。

---

## 3. 跑一次完整流程（三层调用）

```bash
cd factor-layer
.venv/bin/python run_full_window.py          # 全窗口（M1→M5 端到端）
.venv/bin/python run_full_window.py 90       # 只跑最近 90 天（对比用）
.venv/bin/python diagnose_ret60.py           # 一次性诊断脚本
```

`run_full_window.py` 会并排对比「N 天 vs 全窗口」，对每个因子输出
ICIR / p 值 / 单调性 / 毛净年化收益 / 判决 + **票池诊断**。

编程方式（推荐，可脚本化）：

```python
from panel.panel_builder import read_panel                 # M1
from exposure.exposure_builder import read_exposures       # M2
from factor.factor_registry import run_factor, available_factors   # M3
from preprocess.preprocess_pipeline import read_preprocessed_factor, PreprocessConfig  # M4
from evaluate.evaluator import evaluate_many, EvaluateConfig       # M5

panel = read_panel(symbols, days=[...], adjust="hfq")
exposures = read_exposures(panel)
values = run_factor("ret20", panel)
pre = read_preprocessed_factor(values, exposures, config)
reports = evaluate_many([pre], panel, config=eval_config)
```

---

## 4. 已实现的因子（**20 个**）

| 组 | 因子 | 测什么 |
|---|---|---|
| **`alpha`（15）** | `ret5` / `ret20` / `ret60` / `ret150` / `ret260` | 窗口梯度收益（反转 → 动量，`direction` 在 20/60 之间翻转）|
| | `off_low150` / `off_low260` | **距区间最低点**的涨幅（⚠️ ≠ N 日收益）|
| | `vol20` / `skew20` / `max20` | `log_ret` 的**离散度 / 不对称性 / 极值** |
| | `turn20` | **量能**（唯一用 `volume` 的）|
| | `rsi14` / `atr14` / `atr_pct14` / `adr20` | 技术指标（11.5 的原料）|
| **`screening`（5）** | `ma_dist_ema10/20/50`、`ma_dist_sma150/200` | 到均线的 **ATR 归一距离**（11.5 条件②）|

**加一个新因子 = 2 处改动**（框架其余零改动）：
1. 新建 `factor/implementations/<name>.py`（定义类 + `register_factor(...)`）
2. 在 `factor/implementations/__init__.py` 加一行 `from . import <name>`

`run_full_window.py` 用 `alpha_factors()` **自动发现**，新因子自动纳入。

> **`role` 是 2026-10-08 追加的第七要素**（`FactorSpec` 六要素 → 七要素）：
> - **`alpha`** = 可独立评估的因子（上表 15 个）
> - **`screening`** = 筛选原料（均线距离…）
>
> **为什么必须区分**：均线是**价格的平滑**，排序它 = 排序价格 ⇒
> 拿去算 IC 会产出一批「显著有效」的**假阳性**（价格本身有趋势）。
> ⇒ **评估只跑 `alpha_factors()`**。详见 `factor/factor_spec.py` 的 role 说明
> 与 `docs/PRD/01-…md` 的「契约变更」记录。

**指标的唯实现点**：`factor/technical_indicators.py`
（RSI / ATR / ADR% / EMA / SMA / 均线距离 / 离底涨幅 —— 公式只写那里，因子只做薄封装）。

---

## 5. 关键约束（写代码前必读）

| # | 约束 | 含义 |
|---|---|---|
| **Q1** | 单一取数入口 | 只走 `provide`；**禁止 import data-layer 的 `store/` `fetch/` `engine/`** |
| **Q2** | 票池按日切片 | 任何截面计算**必须显式传当日标的集合**；禁全局票池 |
| **Q3** | 标签口径唯一 | `log(open[t+2] / open[t+1])`（末位 NaN）—— 与 `backtest/target_label.py` 靠**跨层契约测试**锁死 |
| **Q4** | 口径消耗必填 | 预处理 / 评估参数**必填无默认**；`neutralize_method` **禁 `None`** |
| **Q5** | 判决必过多重检验 | 判决**只经 BH**，且**报检验次数**（无裸判出口） |

**分层原则**：`data-layer` 承担全部**数据语义**（什么标的、什么口径）；
本层**只做研究逻辑**。接口不可用时**必须报错**，不许 fallback 到自己实现。

---

## 6. 文档索引

| 想知道 | 去哪 |
|---|---|
| **各模块能做什么、接口签名** | **`panel/` `exposure/` `factor/` `preprocess/` `evaluate/` 下的 `README.md`** |
| 需求级契约（Q / M / U / A / T / J 约束编号） | `docs/PRD/01-截面因子-PRD-2026-10-06.md` |
| **策略研究 / 验证目标（非代码）** | **`../关于策略/0-索引.md`**（见 §7） |
| 全仓分层与铁律 | `../Agent.md` |

---

## 7. `关于策略/` —— 策略知识库（**不是代码**）

> ⚠️ **它已经不在本层了。**
> **2026-10-09 移到仓库根** → [`../关于策略/`](../关于策略/0-索引.md)。
>
> **为什么**：它的验证**横跨 `factor-layer` 与 `backtest`**
> （`backtest/strategies/tugboat_rules.py`、仓库根 `run_tugboat.py`），
> 而且 90% 的内容（人 / 认识 / 设计 / 议程 / 外部来源整理）**与截面因子无关**。
> **它不属于任何一层 —— 所以挂在任何一层下都是错的。**

**一句话定位**：**"我在做什么、我研究过什么、验证出来什么"**，与因子层代码是两个世界。

| 分区 | 记什么 |
|---|---|
| `我/` | 人 / 认识（误区 + 工具箱）/ 设计（在场机制）/ 议程 |
| `视频10期/` | 10 期视频整理了什么、**哪站得住**（判据来源：先跑赢猴子） |
| `Minervini与Donchian/` | 实查的规则原文 + 可验证性 + 模拟数字 |
| **`Tugboat/`** | **字幕整理（`9`）+ 验证的结论与事实（`10` `11` `19`）** —— ⭐ **先读 `Tugboat/19-最终结论-2026-10-09.md`**；**未决问题在 `daily/架构日志/债务.md`** |
| `工具/` `素材/` | 脚本；原始字幕（**两个都不读**） |

**索引**：[`../关于策略/0-索引.md`](../关于策略/0-索引.md)
（**要验证什么 / 能验证什么**：`../关于策略/Tugboat/10-…` 与 `11-…`）

---

## 8. 当前状态（2026-10-08）

| 项 | 状态 |
|---|---|
| M1–M5 五个模块 | **全部落地**，全层测试 **136/136 通过** |
| 端到端链路 | 已跑通（`run_full_window.py`） |
| **实测判决结果** | 3 个经典量价因子在**全窗口**上表现见 `关于策略/Tugboat/11-验证-数据体检报告-2026-10-08.md` 与 `run_full_window.py` 输出 |
| **已知未解决** | **幸存者偏差**（无法修复，只能显形：`panel/stock_universe.py::universe_diagnostics`） |
| 依赖 | `pandas`、`numpy`、**`scipy`**（t 检验需 t 分布 CDF） |
