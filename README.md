# trading-desk

> **全仓库入口。** 接手本项目先读本文 + [`Agent.md`](Agent.md)，再按 §2 点进对应模块的 `README.md`
> —— **每个模块的 README 都是「读一遍就知道它能做什么」的能力清单，不必读源码**。

---

## 1. 这是什么

**本仓库 = 数据层 + 截面因子层 + 回测层 三层并列**（外有一个空的前端占位）。

```
trading-desk/
├── data-layer/     ← 数据后端（独立一层）：自己取数、计算、组织，对外提供数据
├── factor-layer/   ← 截面因子层（独立一层）：因子 → 预处理 → 截面有效性判决
├── backtest/       ← 回测/验证层（独立一层）：只消费 data-layer，不碰其内部
├── frontend/       ← 前端（当前为空，见 §5）
├── docs/adr/       ← 判据本体（ADR）
├── scripts/        ← 治理工具链
├── daily/          ← 日志（架构日志 / 债务账本 / 日程）
└── Agent.md        ← 全仓入口（分层关系 + 能力在哪查 + 铁律）
```

**接缝 = `data-layer/provide/`**：三个下游**只经它**取数，**互不 import**。

---

## 2. 各模块 README 索引（从这里开始读）

### 顶层

| 想知道 | 去哪 |
|---|---|
| 分层关系 / 能力在哪查 / 铁律 | [`Agent.md`](Agent.md) |
| **★ 要做一次验证/回测（操作手册）** | [**`docs/流程-如何做一次验证-2026-10-08.md`**](docs/流程-如何做一次验证-2026-10-08.md) |
| **★ 跑某个组合级交易系统**（横截面选股 + 按 R 定仓 + 路径依赖出场）| [**`run_tugboat.py`**](run_tugboat.py)（仓库根，**跨层组装点**）|
| **★ 看"一次完整验证"长什么样 + 它的结论** | [**`关于策略/15-最终结论-突破交易2.0-2026-10-09.md`**](factor-layer/关于策略/15-最终结论-突破交易2.0-2026-10-09.md) |
| **★ 看"我改的东西哪些对、哪些错"（含 7 类根因）** | [**`关于策略/18-改动裁定-哪些对哪些错-2026-10-09.md`**](factor-layer/关于策略/18-改动裁定-哪些对哪些错-2026-10-09.md) |
| **★ 看"审计该怎么独立划范围"（三轮外部审计）** | [**`关于策略/16`**](factor-layer/关于策略/16-独立复审与修正-2026-10-09.md) ｜ [**`17`**](factor-layer/关于策略/17-第三轮独立复审与修正-2026-10-09.md) |
| 判据本体（ADR） | [`docs/adr/`](docs/adr/) |

### `data-layer/`

| 模块 | README |
|---|---|
| **数据层总览** | [`data-layer/README.md`](data-layer/README.md) |
| `store/` 库（键 / 原子写 / 读取） | [`data-layer/store/README.md`](data-layer/store/README.md) |
| `fetch/` 取数（三源 / 限流 / 换源） | [`data-layer/fetch/README.md`](data-layer/fetch/README.md) |
| `engine/` 计算（**复权唯一实现** / 对齐 / GEX / RPS） | [`data-layer/engine/README.md`](data-layer/engine/README.md) |
| **`provide/` 对外接缝（★下游必读）** | [`data-layer/provide/README.md`](data-layer/provide/README.md) |
| 富途平台怎么用（实测事实） | [`data-layer/docs/reference/futu/`](data-layer/docs/reference/futu/README.md) |

### `factor-layer/`

| 模块 | README |
|---|---|
| **因子层总览** | [`factor-layer/README.md`](factor-layer/README.md) |
| M1 `panel/` 截面面板 + 取数 + 票池 | [`factor-layer/panel/README.md`](factor-layer/panel/README.md) |
| M2 `exposure/` 风险暴露 | [`factor-layer/exposure/README.md`](factor-layer/exposure/README.md) |
| M3 `factor/` 因子注册表 + 实现 | [`factor-layer/factor/README.md`](factor-layer/factor/README.md) |
| M4 `preprocess/` 四步流水线 | [`factor-layer/preprocess/README.md`](factor-layer/preprocess/README.md) |
| M5 `evaluate/` 评估 + BH 判决 | [`factor-layer/evaluate/README.md`](factor-layer/evaluate/README.md) |

### `backtest/`

| 模块 | README |
|---|---|
| **回测层总览**（含 `statistics.py` 孤儿模块说明） | [`backtest/README.md`](backtest/README.md) |

---

## 3. 当前目标

> **先验证「机器层」是否真有 edge；不做 UI。**

| 目标 | 状态 | 去哪看 |
|---|---|---|
| **要验证什么**（4 个目标 + 3 道判据） | 已定义 | [`factor-layer/关于策略/10-测试-机器层验证目标.md`](factor-layer/关于策略/10-测试-机器层验证目标.md) |
| **能验证什么**（库藏清点 / 可用窗口 / 幸存者偏差） | **已完成（2026-10-08）** | [`factor-layer/关于策略/11-验证-数据体检报告-2026-10-08.md`](factor-layer/关于策略/11-验证-数据体检报告-2026-10-08.md) |

**数据结论速览**：宽面板 **2022-05-03 → 2026-10-05，约 1,110 天 × ~290 只（4.4 年）**；
**已满足「≥3 年」门槛**。唯一真缺口：**板块历史成分**（仅 1 天）与 **快照历史**（仅 1 天）。

**两个必须写进每份结论的偏差**：

1. **幸存者偏差**（票池只增不减，是「今天的名单回填历史」）⇒ **「不显著」可信，「显著」不可信**。
2. **`snapshot_is_after_day`** ⇒ 用晚于该日的快照判定票池，含未来信息。

---

## 4. 铁律快查（详见 [`Agent.md`](Agent.md) §4）

| 类别 | 核心 |
|---|---|
| **反掩盖 P1–P6** | 禁默认值兜底 / 禁容忍未知结构 / 数据自证 / 校验"真取到" / 异常值显形 / **错误就是错误，不许降级** |
| **数据 SSOT** | 数据只有一份；写原子；键里无源名 |
| **分层原则** | `data-layer` 承担**数据语义**（什么标的、什么口径）；下游**只做各自逻辑**，**接口不可用必须报错，不许 fallback** |
| **复权** | K 线统一存 **raw**；换算归数据层（唯一实现）；**下游只声明口径**；回测用 **hfq**，qfq 含未来 |
| **命名** | **用长名，一看就懂；禁短名/缩写**（`fetch_api.py` 而非 `api.py`） |

---

## 5. 关于 frontend

**当前不做 UI。** `frontend/` 为空目录。

本阶段的全部需求是**验证有效性**（跑数字、出判决），不是展示。
**请勿在未被要求时开始做前端** —— 它只消费 `data-layer/provide` 的契约，
等真有展示需求时再动。

---

## 6. 环境与快速上手

```bash
# 数据层：更新数据（一条命令，详见 data-layer/README.md §7）
cd data-layer
.venv/bin/python pipeline.py --daily --skip-adjust   # 日常（约 15 秒）
.venv/bin/python pipeline.py --daily                 # 每周一次（约 3 分钟，含复权因子）
.venv/bin/python -m provide.cli stocks --day 2026-09-30
for t in tests/test_*.py; do .venv/bin/python "$t"; done

# 因子层（全窗口端到端）
cd factor-layer
.venv/bin/python run_full_window.py

# 回测层
cd backtest
python3 run_backtest.py --list-strategies
```

- Python **3.12**；虚拟环境在各层自己的 `.venv`（`uv` 管理，无 `pip`）
- 凭据在仓库外：`~/.config/futu/private_key.pem` + `appkey`
- OpenD 需本机运行（`127.0.0.1:11111`）

---

## 7. 文档的分工（防两份真相）

| 问 | 落点 |
|---|---|
| 它凭什么成立？被谁推翻过？ | `docs/adr/`（走 `scripts/adr.py`，**禁手改索引**） |
| 物理红线 / 铁律 | `Agent.md` §4 |
| 需求级约束（K/F/G/L/P/Q/M/U/A/T/J） | 各层 `docs/PRD/` |
| 链路怎么走 / 施工记录 / 债明细 | `daily/架构日志/` + 各层 `docs/plan/` |
| **模块能做什么、接口签名** | **各模块的 `README.md`**（本文 §2 索引） |
