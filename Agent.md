# Agent.md —— 接手本项目的入口

> **写给下一个 AI：先读完这页，再动手。**
> 本文回答三个问题：**这是什么 / 能力在哪查 / 什么绝对不能做**。
> 建立：2026-10-06 ｜ 结构更新：2026-10-06（分两层）

---

## 0. 一句话

**本仓库 = 数据层 + 回测层 + 前端，三层并列。**

```
trading-desk/
├── data-layer/     ← ★ 数据后端（独立一层）：自己取数、计算、组织，对外提供数据
├── backtest/       ← 回测/验证层（独立一层）：只消费 data-layer，不碰其内部
├── frontend/       ← 前端：只消费 data-layer 提供的数据
└── Agent.md        ← 本文件（全仓库入口）
```

**数据层不做 UI、不做交易、不做筛选。** 下游（回测 / 前端 / 量化 / 研究）来拿数据就用。

- 数据层需求：`data-layer/docs/PRD/01-底层数据-PRD-2026-10-06.md`
- 数据层计划：`data-layer/docs/plan/01-底层数据-plan-2026-10-06.md`（含实施记录与验收）
- **平台资料（富途怎么用）：`data-layer/docs/reference/futu/`**
- **回测/验证系统设计：`backtest/docs/design/01-回测与验证系统-design-2026-10-06.md`**
- 数据层目录细节见 §3「分层」。

---

## 1. 三层的关系

```
frontend/  ──（只通过 provide 的契约）──►  data-layer/
backtest/  ──（只通过 provide 的契约）──►  data-layer/
```

> `backtest/`（回测/验证）是**独立一层**，与 `frontend/` 平级；**不属于数据层**，
> 也不许知道 `data-layer/` 里 `store/` `fetch/` `engine/` 的实现（承 P2）。

**接缝 = `data-layer/provide/`**：

| 方式 | 入口 | 适合 |
|---|---|---|
| **跨进程 JSON**（推荐） | `python -m provide.cli <cmd>`（子进程调用） | 任何前端（HTML/JS/Node/Python） |
| 同进程 Python | `from provide.api import Access` | 仅 Python 前端（需在 `data-layer/` 内运行） |
| HTTP | 前端自己加一个薄壳转发上面两者 | Web 前端 |

**前端不许知道** `data-layer/` 里 `store/` `fetch/` `engine/` 怎么实现（承 P2）。
**数据层不许管**前端怎么展示（不做 UI）。

> ⚠️ 目录名 `data-layer` **带连字符，不是合法 Python 包名** —— 所以**不能** `import data-layer`。
> Python 前端若要同进程调用，须在 `data-layer/` 目录内运行（那里才是 `sys.path` 根）。

---

## 2. ★ 能力在哪查（最重要的一节）

**问："OpenD / REST 支持什么能力？" → 去这三个地方，按顺序**（都在 `data-layer/` 内）：

### ① 命令行探查（最快，有事实）

```bash
cd data-layer
.venv/bin/python -m fetch.fetch_cli sources      # 有哪些源：cboe / futu-opend / futu-rest
.venv/bin/python -m fetch.fetch_cli ktypes       # 两通道各支持什么周期（实测）
.venv/bin/python -m fetch.fetch_cli chain SPX    # 期权链
.venv/bin/python -m fetch.fetch_cli kline AAPL --ktype K_DAY     # K线（默认 REST）
.venv/bin/python -m fetch.fetch_cli snapshot --max-pages 1       # 全市场快照
.venv/bin/python -m fetch.fetch_cli watchlist
```

### ② 平台资料文档（为什么、坑在哪）

**`data-layer/docs/reference/futu/`** —— 按通道拆 5 份：

| 文件 | 查什么 |
|---|---|
| `README.md` | **总览**：两通道分工 / 周期路由 / 额度 / 实现映射 |
| `opend.md` | **OpenD**：能拿什么、ktype 全表、额度分档、6 个坑 |
| `rest.md` | **REST**：Ed25519 签名、翻页语义、限流 |
| `code-formats.md` | 代码格式（`US.AAPL`）、市场推断、校验 |
| `adjustment-factor.md` | **复权因子**：官方公式、A/B 语义、qfq 含未来 / hfq 因果 |

### ③ 代码（精确签名）

- `data-layer/fetch/fetch_api.py` —— **统一入口**（`chain`/`kline`/`snapshot`/`watchlist`）
- `data-layer/fetch/sources/futu/opend_source.py` —— OpenD 源（`FutuSource`）
- `data-layer/fetch/sources/futu/rest_source.py` —— REST 源（`FutuRestSource`）
- `data-layer/fetch/registry.py` —— 源注册表

### 三个源的定位

| 源 | 拿什么 | 吃额度？ |
|---|---|---|
| `cboe` | 期权链（SPX/NDX/SPY/QQQ…） | 无（公开） |
| `futu-rest` | **历史 K线（日线/长历史）** | **无** ★默认 |
| `futu-opend` | 全市场快照、少量快照、自选、分钟线含 4H、复权因子 | **历史 K线吃额度** |

### 下游查数据（provide）

```bash
cd data-layer
.venv/bin/python -m provide.cli days
.venv/bin/python -m provide.cli matrix --day 2026-10-06 --items net_gex zero_gamma
.venv/bin/python -m provide.cli align --symbols SPY QQQ IWM --item kline   # 多品种对齐
```

### 数据项分两类（承"存在无标的数据项"）

| 类型 | 键 | 物理文件 | 例子 |
|---|---|---|---|
| **带标的** | `(日, SPX, net_gex)` | `SPX.net_gex.json` | 期权指标 |
| **全局（无标的）** | `(日, None, calendar)` | `_calendar.json` | **交易日历** |

- 全局项清单见 `store/keys.py::GLOBAL_ITEMS`（显式，承 P2）。
- 配对约束：全局项必须无标的、非全局项必须有标的，错配 → 报错。
- **交易日历**（`calendar`）：某市场某天的开市事实（`{市场: {trade_date_type, trade_second}}`），
  **只含开市日**（非交易日缺，承 K4）。
- **复权因子**（`adjust_factor`）：某标的的**每个除权日**一行（键的"交易日"位 = `ex_div_date`）。
  与 K线（不复权）配套：下游要前/后复权价时**自算**。

```bash
# 跑交易日历（全局项，一天一条）
.venv/bin/python pipeline.py --calendar --markets US HK --start 2026-01-01 --end 2026-10-06
# 跑复权因子（按除权日）
.venv/bin/python pipeline.py --adjust-factors --symbols AAPL MSFT
# 探查（取数能力）
.venv/bin/python -m fetch.fetch_cli trading-days --market US --start 2026-10-01 --end 2026-10-06
```

---

## 3. data-layer 的分层（放新文件前先想清楚属于哪层）

```
data-layer/
├── store/      库     —— 按 (交易日, 标的, 数据项) 组织；**零依赖**
├── fetch/      取数   —— 拉原始数据、归一化；**换源只动这里**
├── engine/     计算   —— 纯计算，**不碰 IO**
│   └── time_alignment.py  多品种对齐（纯算子：交集优先，降级仅 ffill）
├── provide/    提供   —— 对外暴露数据（批量/矩阵/导出/跨进程）
├── pipeline.py 管线   —— 定交易日 → 取数 → 计算 → 入库
├── trading_time.py 时区 —— 时间戳统一（**Unix 秒 + 美东判定**）
├── tests/      测试   —— 每次改动都该跑
├── vendor/     参照   —— 老项目源码，**只读**，吸收完即删（见 plan §九）
└── docs/       文档   —— PRD / plan / reference
```

**放错层比写错代码更难收拾。** 判据：
- 在 `fetch/` 里算指标 → ❌；在 `engine/` 里读文件/联网 → ❌。

---

## 4. 铁律（违反 = bug）

### 4.1 反掩盖（plan §零）

| # | 原则 |
|---|---|
| **P1** | **禁止默认值兜底**。缺字段/类型错/空值 → 报错。回退必须**显形**。 |
| **P2** | **禁止"容忍未知结构"**。字段清单写死；多/少一个键 → 当场报错。 |
| **P3** | **数据自证**。每个数带来源时刻；口径指纹变了 → 报。 |
| **P4** | **校验"真取到"**，不看"文件在不在"。 |
| **P5** | **异常值必须显形**（NaN/符号矛盾/日期倒挂 → 报警 + 原值，不修正不丢弃）。 |
| **P6** | **错误就是错误，不许降级**。缺/错/不一致 → 停下报清。 |

### 4.2 数据与源

| # | 规矩 |
|---|---|
| **X1** | **数据只有一份**（SSOT）。指标只存"怎么算"（`source_key`+参数），不冗余存底层值。 |
| **X2** | **写原子**。靠库接口写，不直接开文件写。 |
| **X4** | **键里无源名 / 无前缀 / 无服务器名**（存 `AAPL` 而非 `US.AAPL` / `cboe`）。 |
| **F1** | **源名不泄到上游**。`fetch/` 之外不得出现 `cboe` / `futu` 字样。 |
| **F3** | **换源只动 `fetch/`**。 |
| **F4** | **失败必报**，不静默返回空。 |
| **G1/G2** | **`engine/` 零 IO**（不联网、不读文件）。 |
| **L1** | **交易日 = 美东日期**，不是本机日期。 |
| **复权** | **K线统一存不复权（raw）**；复权因子单独存（`adjust_factor`），下游自算。**两通道口径必须一致**（曾 REST=前复权 / OpenD=不复权 → P1 错，已修）。 |

### 4.3 取数默认原则（2026-10-06 定）

> ★ **K线默认一律走 REST，绝不隐式走 OpenD。**
> - OpenD 拉**任何**历史 K线（含 4H）都**吃额度**（实测拉一只 → `used` +1）；
> - `fetch/fetch_api.py::kline` **默认 REST**；
> - 4H（`K_240M`）REST 不支持 → 默认**报错**，要 OpenD 必须**显式** `source="futu-opend"`；
> - OpenD 的**正确用途**：那些**不吃额度**的 —— 全市场快照、少量快照、自选。

### 4.4 ★ 数据层四问（2026-10-06 定，唯一判据 = 正确性）

> 建数据层前先问自己这四个问题。**答不上来 = 地基不稳。**

| 问 | 数据层的职责 | 现状 |
|---|---|---|
| **时区** | 所有时间戳**统一成 Unix 秒**；交易日一律按**美东**判定 | `trading_time.py`（`to_unix_seconds` / `trading_day` / `et_datetime`）。两通道 K线 `time_key`、`feed_timestamp` / `fetched_at` / `computed_at` 均为 Unix 秒。**数字时间戳必须显式 `unit="s"｜"ms"`**（不猜）+ **量级校验**（越界报错）；**统一到秒 ⇒ 不含逐笔（tick 另论）** |
| **复权** | 股票要处理除权除息 → **K线存不复权（raw）+ 因子单独存**，下游自算 | `adjust_factor` + `pipeline.py --adjust-factors` |
| **停牌** | **缺就是缺，不补、不造**（存储 / 读取绝不 ffill / bfill） | `store` 缺即 `Missing`，不降级 |
| **缓存/增量** | 本地缓存 + **增量更新**（不每次重拉两年） | K线：`pipeline.py --kline` 按 bar 的**美东日**入库；`axis.latest_day` 定"从哪天补" |

**多品种时间对齐（`engine/time_alignment.py`）的定位** —— ★ 别搞错：
- 它**不是"停牌"的解法** —— 停牌的职责就一句：**不造假**（存储不补）。对齐是**查询 / 组织**能力。
- 它依赖"品种集合"这个**下游参数** → 属**对外提供**层；故是**纯算子、按需、默认交集、禁 bfill、不存储、不强制**。
- **默认交集**（消除休市 ffill 造出的假 bar）；交集太小才降级**并集 + 仅 ffill**（禁 bfill = 禁未来函数）。
- 对齐对象是**整根 bar**（OHLCV 记录），不是单列 —— 与 spec 的 `_align_timelines` 一致。
- **已通过 provide 暴露**：`provide.cli align --symbols A B --item kline`（或 `Access.align_panel`）——
  下游只调 provide，不碰 `engine`。

**复权口径：谁决定？**（★ 数据层**不替**下游选口径）
- **决定权在消费方**（回测 / 研究）：同一份 qfq，展示没问题、**回测就是未来函数** —— 是"口径 × 用法"决定它成不成立。
- 数据层的边界：**只存 raw + 因子**（前 / 后复权因子都在），**不烘焙任何口径、不设默认**。
- **回测用 hfq（后复权，严格因果）；qfq（前复权）含未来除权信息，禁止用于回测。**
  （公式与实测见 `data-layer/docs/reference/futu/adjustment-factor.md`。）
- 若将来加复权辅助入口 → `mode` 必须**显式必填、无默认**（承 P1：口径消耗必须显形）。

---

## 5. 环境

- Python **3.12**。虚拟环境在 **`data-layer/.venv`**（用 `uv` 管理；**无 pip**，用 `uv pip`）。
- 依赖见 `data-layer/requirements.txt`。
- ⚠️ **`protobuf < 5`** —— 否则 OpenD 的 `get_stock_screen` 报 `FieldDescriptor.label`。
- 富途凭据（仓库外，`chmod 600`）：`~/.config/futu/private_key.pem` + `appkey`。
- OpenD 需本机运行（`127.0.0.1:11111`），才能用 `futu` 源。

---

## 6. 跑起来

```bash
cd data-layer

# 跑一天（取数→计算→入库）
.venv/bin/python pipeline.py --symbols SPX SPY QQQ IWM

# 下游取数（只用 provide）
.venv/bin/python -m provide.cli days
.venv/bin/python -m provide.cli matrix --day 2026-10-06 --items net_gex zero_gamma

# 全部测试（每次改动都该跑；以全绿为准，不写数量 —— 会漂移）
for t in tests/test_*.py; do .venv/bin/python "$t"; done
```

---

## 7. 改动纪律

1. **先读对应文档**（能力 → `data-layer/docs/reference/futu/`；设计 → `data-layer/docs/plan/`）。
2. **能力有来源**：新事实要标来源（源码行号 / 实测命令），**不猜**；不确定写"待确认"。
3. **改动配测试**：`tests/` 里有对应约束（K/F/G/L/P）。加了约束就加测试。
4. **vendor 只读**，吸收完即删（plan §九）。
5. **别把"用途"做进数据层**（如"择时专用字段"）—— 数据层只提供数据，不做判断（plan §十）。
6. **前端只依赖 `provide` 的契约**，不碰 `data-layer/` 内部实现。
7. **★ 命名：用长名，一看就懂；禁止短名/缩写**（2026-10-06 定）。
   - 反例：`api.py` / `_types.py` / `futu.py`（看不出干什么）
   - 正例：`fetch_api.py` / `fetch_types.py` / `opend_source.py`
   - 新文件一律照此。数据源放 `fetch/sources/`（**一个平台一处**，换平台只看这里）。
