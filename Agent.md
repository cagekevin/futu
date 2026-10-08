# Agent.md —— 接手本项目的入口

> **写给下一个 AI：先读完这页，再动手。**
> 本文回答三个问题：**这是什么 / 能力在哪查 / 什么绝对不能做**。
> 建立：2026-10-06 ｜ 结构更新：2026-10-07（四层并列）

---

## 0. 一句话

**本仓库 = 数据层 + 截面因子层 + 回测层 + 前端，四层并列。**

```
trading-desk/
├── data-layer/     ← ★ 数据后端（独立一层）：自己取数、计算、组织，对外提供数据
├── factor-layer/   ← 截面因子层（独立一层）：因子 → 预处理 → 截面有效性判决
├── backtest/       ← 回测/验证层（独立一层）：只消费 data-layer，不碰其内部
├── frontend/       ← 前端：只消费 data-layer 提供的数据
└── Agent.md        ← 本文件（全仓库入口）
```

**数据层不做 UI、不做交易、不做筛选。** 下游（回测 / 前端 / 量化 / 研究）来拿数据就用。

- 数据层需求：`data-layer/docs/PRD/01-底层数据-PRD-2026-10-06.md`
- 数据层计划：`data-layer/docs/plan/01-底层数据-plan-2026-10-06.md`（含实施记录与验收）
- **平台资料（富途怎么用）：`data-layer/docs/reference/futu/`**
- **回测/验证系统设计：`backtest/docs/design/01-回测与验证系统-design-2026-10-06.md`**
- **截面因子层 PRD：`factor-layer/docs/PRD/01-截面因子-PRD-2026-10-06.md`**
- 数据层目录细节见 §3「分层」。

---

## 1. 四层的关系

```
factor-layer/  ──（只通过 provide 的契约）──►  data-layer/
backtest/      ──（只通过 provide 的契约）──►  data-layer/
frontend/      ──（只通过 provide 的契约）──►  data-layer/
```

> `factor-layer/`（截面因子）与 `backtest/`（时序验证）是**两个并列的下游**，
> **互不 import** —— 只经「候选池文件」交接。
>
> **为什么并列而不是串联**：两者消费同一份契约、但**数据形状相反** ——
> `factor-layer` 要「多标的 × 单日」（截面），`backtest` 要「单标的 × 时间序列」。
> 串联会让上游反向依赖下游的代码（`data-layer` 的判据：**只该依赖契约，不该依赖模块命名空间**）。

### ★ 分层原则（2026-10-07 定）

> **`data-layer` 承担全部「数据语义」（什么标的、什么口径）；下游各层只做各自的逻辑。**

| 谁 | 负责 | 举例 |
|---|---|---|
| **`data-layer`** | 数据语义 | 哪些代码是股票（排除板块 / ETF / 非美股）；复权换算；交易日历 |
| **下游各层** | 各自的逻辑 | 因子定义；预处理方法；评估指标；回测判决 |

**推论（写死）**：
1. **过滤归数据层** —— 下游**不得**自己判断代码前缀 / 标的类型（用 `provide.cli stocks`）
2. **复权归数据层** —— 下游只**声明**要哪个口径（`--adjust hfq`），**不自己算**（用 `provide.cli panel/align`）
3. **接口不可用时必须报错**，不许 fallback 到自己实现（否则原则立刻失效）

> `backtest/`（回测/验证）是**独立一层**，与 `frontend/` 平级；**不属于数据层**，
> 也不许知道 `data-layer/` 里 `store/` `fetch/` `engine/` 的实现（承 P2）。

**接缝 = `data-layer/provide/`**：

| 方式 | 入口 | 适合 |
|---|---|---|
| **跨进程 JSON**（推荐） | `python -m provide.cli <cmd>`（子进程调用） | 任何前端（HTML/JS/Node/Python） |
| 同进程 Python | `from provide.api import Access` | 仅 Python 前端（需在 `data-layer/` 内运行） |
| HTTP | 前端自己加一个薄壳转发上面两者 | Web 前端 |

**下游（截面因子 / 回测 / 前端）不许知道** `data-layer/` 里 `store/` `fetch/` `engine/` 怎么实现（承 P2）。
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
.venv/bin/python -m provide.cli stocks --day 2026-09-30          # ★ 该日的**股票**票池
.venv/bin/python -m provide.cli matrix --day 2026-10-06 --items net_gex zero_gamma
.venv/bin/python -m provide.cli align --symbols SPY QQQ IWM --item kline --adjust hfq  # 时序对齐（交集）
.venv/bin/python -m provide.cli panel --symbols AAPL MSFT --item kline --adjust hfq    # ★ 截面面板
```

**`stocks` 与 `symbols` 的区别**（★ 别混用）：
- `symbols(day)` = 该天库里有记录的**所有键**（**含板块代码** `LIST*` 与保留代码 `UNIVERSE`）；
- `stocks(day)` = 该天的**股票**票池（排除板块 / 保留代码 / ETF-指数），并显形 `snapshot_day`
  与 `snapshot_is_after_day`（后者为真 = 用了晚于该日的快照判定，**含未来信息**，承 P5）。

**`align` 与 `panel` 的区别**（两者都做「多标的 × 多日」，**语义相反**）：
- `align` = **时序对齐**：按 bar 时间戳对齐，默认**交集**，不够才降级并集 + 仅 ffill；
- `panel` = **截面面板**：按交易日**分组、组内可缺、不做任何对齐**（缺就是缺，承 K4）。

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
  与 K线（不复权）配套：**换算归数据层**（`engine/adjust.py` 是**唯一实现**，承"数据只有一份"）——
  下游**不自己算**，只在 `provide.cli align/panel --adjust {hfq|qfq}` 上**声明**口径（不传 = 原样 raw）。

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
| **复权** | **K线统一存不复权（raw）**；复权因子单独存（`adjust_factor`）；**换算归数据层**（`engine/adjust.py` 唯一实现），下游只**声明**口径。**两通道口径必须一致**（曾 REST=前复权 / OpenD=不复权 → P1 错，已修）。 |

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
| **复权** | 股票要处理除权除息 → **K线存不复权（raw）+ 因子单独存**；**换算归数据层**，下游只声明口径 | `adjust_factor` + `engine/adjust.py`（唯一实现）+ `provide.cli align/panel --adjust` |
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
- **复权辅助入口已落地（2026-10-07）**：`engine/adjust.py` 是**唯一实现**，
  经 `provide.cli align/panel --adjust {hfq|qfq}` 暴露；`mode` **显式必填、无默认**
  （**不传 = 原样 raw**，承 P1：口径消耗必须显形）。
  ⚠️ **下游不许自建复权** —— 只声明口径（承"数据只有一份"）。
  ⚠️ **派现日不可精确复权**（后复权是加法项，形式未定）→ 该日进返回里的 `contaminated_days`，
  下游**显形**处理，不许当 0、不许 ffill（承 K4/P6）。
- **返回里带 `n_adjust_events`（每个标的）**：本次复权**用到几个除权事件**，
  **0 = 复权是空操作**（承 P5：显形）。**不能因此报错** —— `adjust_factor` 只记
  "有事件的日子"，无法区分"真没除权"与"我们没拉"（见 `engine/adjust.py` §边界）。
- **复权因子数据状态（2026-10-07 全量补齐）**：`universe.txt` 328 只中 ——
  **215 只有除权事件**（共 8970 条）、**108 只真无事件**（不派息/不拆股的成长股，
  已抽查确认，非缺数据）、**5 只取数失败**（`DJI` / `IXIC` / `SOX` / `SPX` / `USDCNY`
  —— 指数与汇率，本就无复权因子）。⇒ 现在 `n_adjust_events: 0` 明确表示**真无除权**。
- ⚠️ **`get_rehab` 有限频（60 次 / 30 秒）**，已在 `opend_source.py` 声明
  `opend:rehab` 桶并 `wait`。此前只在 docstring 写了限额、**没接限流器** →
  批量 328 只时 **268 只被打回**（实测报错原文：「每30秒最多60次」）。
  限额数字**来自实测报错**，不是猜的。

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

---

## 8. 治理工具链（`scripts/`）

> 全部**零第三方依赖**（只用标准库）。命令形如 `data-layer/.venv/bin/python scripts/<x>.py …`。
> 登记见 [`scripts/README.md`](scripts/README.md)；规程见 [`.codebuddy/commands/`](.codebuddy/commands/)。

| 工具 | 落点 | 干什么 | 谁在用 |
|---|---|---|---|
| `scripts/adr.py` | `docs/adr/`（判据本体 + `README.md` 索引是**产物**） | ADR 读写唯一入口（`list/show/search/add/status/audit/hygiene/refs/doctor`） | `ADR守护者.md` |
| `scripts/debt.py` | `daily/架构日志/债务.md`（待办）+ `债务-归档.md`（已完成） | 债务账本读写唯一入口（`list/area/search/show/add/resolve/archive/audit/fix/stats`） | `债务登记5步法.md` |
| `scripts/mv_sync_refs.py` | 全仓 `.py` | 改名/移动/目录搬运 + **全库同步 import**；`refs`（fan-in）/ `find-dead`（孤儿） | `写代码4步法` · `架构5步法` · `债务登记5步法` · `系统治理5步法` · `排查5步法` |
| `scripts/probe.py` | `scripts/.probe/`（journal） | **先红后绿**探针（注入→跑→断言→**无条件还原**） | `架构师改码7步法` §7.1 |

**判据的分工**（防两份真相）：

| 问 | 落点 |
|---|---|
| 它凭什么成立？被谁推翻过？ | **`docs/adr/`**（走 `scripts/adr.py`，**禁手改索引**） |
| 物理红线 / 铁律（撬不动） | **本文 §4**（P1–P6 / X / F / G / L / 复权） |
| 需求级约束（K/F/G/L/P） | `data-layer/docs/PRD/` |
| 链路怎么走 / 施工记录 / 债明细 | `daily/架构日志/<NN>-<区域>-<日期>.md`（区域日志）+ `data-layer/docs/plan/` |

**三条纪律**：
1. **写入者唯一**：`docs/adr/**` 只经 `adr.py`，`债务*.md` 只经 `debt.py` —— **禁手写表格行**。
2. **引用检索走 `mv_sync_refs.py`**：`refs <file>` 看 fan-in；改名/搬移**一律走它**（禁手写 `mv` / 手改 import）。
3. **"先红后绿"走 `probe.py`**：禁手工注入后忘记还原（journal + sha256 自校验兜底）。
