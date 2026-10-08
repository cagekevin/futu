# data-layer —— 数据后端

> **读这一页就知道数据层能做什么。** 需要细节时按 §6 的索引往下点，**不必读源码**。
> 本文件是**能力速查卡**，不是设计文档 —— 设计在 `docs/PRD/` 与 `docs/plan/`。

---

## 1. 一句话定位

> **自己取数、计算、组织，对外提供数据。不做 UI、不做交易、不做筛选。**

数据层承担**全部「数据语义」**（哪些代码是股票、复权口径、交易日历），
下游各层**只消费、不重建**（承 `Agent.md` §1 分层原则）。

---

## 2. 目录地图（放新文件前先想清楚属于哪层）

```
data-layer/
├── store/       库     —— 按 (交易日, 标的, 数据项) 组织；零依赖           → store/README.md
├── fetch/       取数   —— 拉原始数据、归一化；换源只动这里                 → fetch/README.md
├── engine/      计算   —— 纯计算，不碰 IO                                → engine/README.md
├── provide/     提供   —— 对外唯一接缝（批量/矩阵/导出/跨进程）            → provide/README.md
├── pipeline.py  管线   —— 定交易日 → 取数 → 计算 → 入库
├── universe.py  票池   —— 唯一声明「每天拉哪些标的 × 哪些项」（SSOT）
├── trading_time.py 时区 —— 时间戳统一（Unix 秒 + 美东判定）
├── data/        落盘   —— 一天一个目录（可用 TRADING_DESK_DATA_DIR 覆盖）
├── tests/       测试
├── vendor/      参照   —— 老项目源码，只读，吸收完即删
└── docs/        文档   —— PRD / plan / reference
```

**判据**：在 `fetch/` 里算指标 → 错；在 `engine/` 里读文件/联网 → 错。

---

## 3. 下游怎么用（唯一接缝 = `provide/`）

三种方式，**推荐跨进程 JSON**：

| 方式 | 入口 | 适合 |
|---|---|---|
| **跨进程 JSON**（推荐） | `python -m provide.cli <cmd>` | 任何语言的前端 |
| 同进程 Python | `from provide.api import Access` | 仅 Python，且**须在 `data-layer/` 内运行** |
| HTTP | 自己加薄壳转发上面两者 | Web 前端 |

> `data-layer` 带连字符，**不是合法 Python 包名**，不能 `import data-layer`。

常用命令（全部走 `provide.cli`）：

```bash
cd data-layer
.venv/bin/python -m provide.cli days
.venv/bin/python -m provide.cli stocks --day 2026-09-30              # 该日的股票票池
.venv/bin/python -m provide.cli matrix --day 2026-10-06 --items net_gex zero_gamma
.venv/bin/python -m provide.cli align  --symbols SPY QQQ --item kline --adjust hfq   # 时序对齐（交集）
.venv/bin/python -m provide.cli panel  --symbols AAPL MSFT --item kline --adjust hfq # 截面面板
```

**详细方法表与参数见 [`provide/README.md`](provide/README.md)**。

---

## 4. 数据现状（2026-10-08 实测）

| 数据项 | 覆盖天数 | 时间范围 | 备注 |
|---|---|---|---|
| **kline**（日线） | **1,480** | 2020-11-11 → 2026-10-05 | 323 只；**宽面板起点 2022-05-03** |
| **adjust_factor** | 4,066 | 1987-06-16 → 2026-10-02 | 复权因子，覆盖完整 |
| plate_state / plate_members | **1** | 2026-10-06 | 板块历史**未积累** |
| snapshot | **1** | 2026-10-06 | 快照历史**未积累** |
| chain / net_gex / 期权类 | **1** | 2026-10-06 | 仅 4 个标的 |

**完整数据体检报告**（含票池阶梯、幸存者偏差量化、可用窗口）见
[`factor-layer/关于策略/11-验证-数据体检报告-2026-10-08.md`](../factor-layer/关于策略/11-验证-数据体检报告-2026-10-08.md)。

---

## 5. 铁律（违反 = bug）

| # | 规矩 |
|---|---|
| **P1–P6** | 反掩盖：禁默认值兜底 / 禁容忍未知结构 / 数据自证 / 校验"真取到" / 异常值显形 / **错误就是错误，不许降级** |
| **X1** | **数据只有一份**（SSOT） |
| **X2** | **写原子** |
| **X4** | 键里**无源名 / 无前缀**（存 `AAPL` 而非 `US.AAPL` / `cboe`） |
| **F1/F3** | 源名不泄到上游；**换源只动 `fetch/`** |
| **G1/G2** | **`engine/` 零 IO** |
| **L1** | **交易日 = 美东日期**，不是本机日期 |
| **复权** | K 线统一存 **raw**；因子单独存；**换算归数据层**（唯一实现），下游只**声明**口径 |

---

## 6. 文档索引

| 想知道 | 去哪 |
|---|---|
| **模块能做什么、接口签名** | **本目录下各子模块的 `README.md`**（store / fetch / engine / provide） |
| 需求级契约、模块划分 | `docs/PRD/01-底层数据-PRD-2026-10-06.md` |
| 理念、反掩盖清单、实施记录 | `docs/plan/01-底层数据-plan-2026-10-06.md` |
| **富途平台怎么用（实测事实）** | `docs/reference/futu/`（5 册：总览 / opend / rest / 代码格式 / 复权因子） |
| 全仓层的划分与铁律 | `../Agent.md` |

---

## 7. 更新数据（只要一条命令）

```bash
cd data-layer
.venv/bin/python pipeline.py --daily          # ★ 一条命令：跑完即最新
```

**它替你处理了所有要想的事**：

| 你不用想 | 它怎么做 |
|---|---|
| 现在是盘中还是盘后？ | 自动取**最后一个已收盘的交易日**（未到 16:00 ET 就退到上一个工作日、跳过周末）—— **任何时间跑都不会把半成品当成一天** |
| 该补哪几天？ | 逐项**增量**：K 线只补库里最后一天起；**已是目标日就根本不发请求** |
| 连着跑会不会重复拉？ | 幂等：`snapshot` / `plate_list` 已在库则跳过 |
| 拉哪些标的？ | 按 `universe.py`（唯一设置源） |

**实测耗时**（2026-10-08，328 只）：

| 命令 | 耗时 | 什么时候用 |
|---|---|---|
| `--daily --skip-adjust` | **约 15 秒** | **日常**：复权事件一年才几次，跳过即可 |
| `--daily` | 约 3 分钟 | **每周一次**：完整更新，含复权因子 |
| `--kline` | 几秒 | 只补 K 线 |

### 为什么"复权因子"是最大的一笔开销

`get_rehab` 限频 **60 次 / 30 秒**，且**限流在请求前**生效 ⇒ 328 只 × 0.5s ≈ **3 分钟**。
而除权（分红 / 拆股）一年只有几次 —— 所以**日常用 `--skip-adjust`、每周跑一次完整版**。

> 顺带：K 线的 REST 限流器**只在翻页的间隙**生效（逐只请求之间不节流），
> 所以补缺口很快（实测 206 只 / 8 秒，并发 4）；服务端若回 429，源内已有 4 次退避重试。

### 其他单独入口

```bash
.venv/bin/python pipeline.py --kline                  # 只补 K 线
.venv/bin/python pipeline.py --adjust-factors         # 只补复权因子
.venv/bin/python pipeline.py --snapshot               # 只补全市场快照
.venv/bin/python pipeline.py --plates                 # 只补板块名册 + 成分股
.venv/bin/python pipeline.py --states                 # 只补行业 / 板块状态（需当天已有 snapshot）
.venv/bin/python pipeline.py --calendar --start 2026-01-01 --end 2026-10-07
.venv/bin/python pipeline.py --day 2026-10-07 --kline # 手动指定目标日（一般不用）
.venv/bin/python pipeline.py --workers 8 --daily      # 调并发（默认 4）
```

### 两个"日期语义"不同（**有意的**，不是 bug）

| 项 | 归属日 | 为什么 |
|---|---|---|
| **K线 / 快照 / 板块 / 状态** | **最后一个已收盘交易日** | bar 归属于"它**完成**的那天"；未收盘即半成品 |
| **期权链 `chain`** | **今天** | 它是**时刻快照**（CBOE 给的就是"此刻最新"） |

盘前跑时两者会差一天 —— 这是设计，不是错位。

### 已知的固定失败（不是故障）

`DJI` / `IXIC` / `SOX` / `SPX` / `USDCNY` 这 5 只是**指数与汇率**，
REST 不支持它们的 K 线与复权因子 ⇒ 每轮都会报 5 + 5 条失败。**预期行为**（见 `../Agent.md` §4.2）。

---

## 8. 环境

- Python **3.12**，虚拟环境 `data-layer/.venv`（`uv` 管理，无 `pip`）
- `protobuf < 5`（否则 OpenD `get_stock_screen` 报错）
- 凭据（仓库外，`chmod 600`）：`~/.config/futu/private_key.pem` + `appkey`
- OpenD 需本机运行（`127.0.0.1:11111`）

```bash
cd data-layer
for t in tests/test_*.py; do .venv/bin/python "$t"; done   # 全部测试（以全绿为准）
```
