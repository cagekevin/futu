# 下游消费层 debug 法（本项目专属调试方法）

> **适用场景**：数据层测试已绿（`data-layer/tests/` 通过），但**下游（`backtest/` / `frontend/`）用起来有 bug**——问题出在下游怎么消费 `provide` 的数据：契约字段、时区单位、复权口径、多品种对齐、增量起点、缓存。
> **核心思路**：debug 的本质是**先画出链路全景，再标清每一跳的契约（数据结构）与边界**；链路清楚了，断点自然一眼可定位。本文件给出本项目主要链路的"地图 + 契约 + 断点"，均来自真实代码取证（标注 `文件:行`）。
> **通用思考底座**：步进式、防绕圈、无日志不推演见 `排查5步法.md`（管"怎么想"）；本文件管"这个项目**链路长什么样、契约是什么**"。
> **最高优先铁律**：改下游 bug **第一步先拿真实数据复现**（跑 `provide.cli` / `fetch_cli`），拿真实 JSON 再定位，禁止没证据就翻代码猜根因改逻辑。

---

## 〇、第一步：拿证据（所有 bug 都先这步）

本项目**没有前端 debug 开关**；证据 = **真实 JSON / 真实报错**。常用取证命令：

```bash
cd data-layer
.venv/bin/python -m provide.cli days
.venv/bin/python -m provide.cli matrix --day 2026-10-06 --items net_gex zero_gamma
.venv/bin/python -m provide.cli get --day 2026-10-06 --symbol SPX --item net_gex
.venv/bin/python -m provide.cli timeseries --symbol SPX --item net_gex
.venv/bin/python -m provide.cli align --symbols SPY QQQ IWM --item kline
# 取数能力探查（怀疑"数据层没取到"时）
.venv/bin/python -m fetch.fetch_cli sources|ktypes|chain SPX|kline AAPL --ktype K_DAY|snapshot|watchlist
```

> 拿数据后若怀疑"到底是数据层没给，还是下游读错字段"，用上面命令直接对账（见第五节），**不要直接改数据层逻辑**——本文件假定数据层已绿。

---

## 一、链路地图 ①：回测取数链路（最高频，数据层绿下游错都在这）

从「回测要面板」到「拿到逐 bar 数值」，共 5 跳。每跳标【入】输入契约、【出】输出契约、【断】常见断点（均基于真实代码）。

```
[1] backtest 发起（唯一数据入口）
    backtest/data_source.py::align_panel (data_source.py:70)
    【入】symbols、item="kline"、days、min_bars(=backtest_config.MIN_BARS)
    【出】拼成 provide.cli align 参数 → 子进程
    【断】🔴 回测只该经这里拿数据；若在别处自己 import data-layer 内部 / 自己写对齐 → 违反 01 §0.3（"回测不重建对齐"）
         用同进程 import（而非跨进程）→ 回测会被 data-layer 的顶层命名空间绑住（data_source.py:6-11）
        ↓
[2] _run_provide_cli（跨进程 JSON）
    data_source.py::_run_provide_cli (data_source.py:47)
    【入】[python, -m, provide.cli, …]，cwd=data-layer，DATA_LAYER_PY=.venv/bin/python
    【出】json.loads(stdout)
    【断】🔴 失败必须报错（returncode != 0 → RuntimeError），禁止静默返回空（承 F4/P6）
          用错 Python（非 data-layer/.venv）→ 依赖缺失 / 版本不符
        ↓
[3] provide.cli align → Access.align_panel
    provide/cli.py:70 → provide/api.py::align_panel (api.py:139)
    【出】{index, values, strategy, n_symbols, n_bars, n_union, n_dropped, n_filled}
    【断】🔴 只认**逐 bar** 数据项（kline）—— 其它项（net_gex/spot/chain，每天一条、无 bar 级时间戳）→ 明确报错（api.py:191-202，承 P2）
          strategy 可能是 "union_ffill"（交集不足降级）—— 含 ffill 捏造 bar
        ↓
[4] 对齐本身（数据层保证，承 D1）
    engine/time_alignment.py::align_by_intersection (time_alignment.py:65)
    【出】默认交集（time_alignment.py:101）；交集 < min_bars(=DEFAULT_MIN_BARS 30) 才降级并集 + **仅 ffill**（:108，禁 bfill）
    【断】🔴 重复时间戳 → 报错（:89，承 P2）；品种无报价 → 报错（:94）
          降级路径会 ffill 出假 bar（OHLC 四值相等）—— 下游必须能识别
        ↓
[5] 下游抽字段
    data_source.py::extract_field (data_source.py:93)
    【出】{symbol: [float, …]}
    【断】🔴 strategy != "intersection" → **硬报错**（:100），绝不静默用 ffill 假数据（承 D1/P6）
          bar 为 None → 报错（:109）
          字段名以 KLINE_BAR_FIELDS 为准（:36，open/high/low/close/volume）——别处不许再抄一份
```

**真相源契约（红线，整条链路守此）**：**对齐由数据层保证**（`backtest/data_source.py:1-3`），回测**不重建、不自写对齐逻辑**；`strategy` 必须为 `intersection` 才可用（`data_source.py:38` 的 `ALIGNMENT_INTERSECTION`）。

**debug 落点口诀**：面板拿不到→查 [2] 子进程是否报错；字段对不上→查 [3] 契约字段 / [5] `KLINE_BAR_FIELDS`；出现假 bar→查 [4] 是否降级为 `union_ffill`；`net_gex` 对齐报错→它本就不是逐 bar（承 P2）。

---

## 二、链路地图 ②：单点 / 矩阵 / 时序取数链路

从「下游要某天某标的某项」到「拿到值」，共 4 跳（基于 `provide/cli.py` / `provide/api.py` / `store/`）。

```
[1] 下游调 provide.cli <子命令>
    provide/cli.py:32  main（days/symbols/items/get/matrix/timeseries/export/align）
    【断】get 命中缺失 → 输出 {"error":"missing",…} 且 **exit=3**（cli.py:59-63）—— 下游若不看 exit code 会把错误当数据
        ↓
[2] Access（对外句柄）
    provide/api.py::Access (api.py:44)
    【出】days/symbols/items/global_items/latest_day；value/record/batch/matrix/timeseries/panel/align_panel/export_rows
    【断】🔴 接口参数/返回**无"用途"语义**（承 P1，api.py:8）；下游不知道路径/格式/源名（承 P2）
        ↓
[3] 解包内部结构
    provide/api.py::_unwrap (api.py:206) / _to_row (api.py:218)
    【出】内部记录 {"value":…, 自证字段…} → 下游默认只要 value；自证字段（source_key/computed_at/bucket/weight_col/as_of）在 _to_row 里透出
    【断】🔴 chain（期权链）**不含 value 键** → 原样返回整条记录（api.py:210-215）；下游若直接取 ["value"] 会 KeyError
        ↓
[4] 读库（M1）
    store/read.py::get (read.py:31) / series (read.py:51) / latest_day (read.py:76)
    【断】🔴 **缺就是缺**：缺 10-06 绝不回退到 10-05（read.py:39，承 K4）；要"取最新"必须显式用 latest_day（:76，语义是"更新到哪了"，不是"某天有没有"）
          matrix 缺失项**不补**（api.py:113，记录里没有该键即"缺"）
```

**字符串契约（红线常量）**：键 `(交易日, 标的, 数据项)` —— 交易日 `YYYY-MM-DD`（`20261006` 报错）、标的**纯代码无前缀**（`US.AAPL` 报错，承 X4）、数据项在 `KNOWN_ITEMS` 受控清单（未注册报错，承 K3）。见 `store/keys.py:23/53/100/120/157`。

---

## 三、链路地图 ③：K线入库与增量链路（"刷新后少了一段 / 多了一段"类 bug）

```
[1] pipeline.py::run_kline (pipeline.py:218)
    【入】symbols、ktype（默认 K_DAY）、full_years=3、refresh_last_day=True
    【断】🔴 4H（K_240M）默认走 REST → **报错**（REST 不支持），要 OpenD 必须显式 source="futu-opend"（承 P1，不静默吃额度）
        ↓
[2] 定增量起点
    axis.latest_day(sym,"kline") (pipeline.py:245) → since（最后有值那天；refresh_last_day=True 会重取最后一天）
    【断】🔴 首次（last=None）取全窗口；已有只补从最后一天起，**不重拉全历史**（承"缓存+增量"）
          起点算错 → 少一段历史 / 重复写
        ↓
[3] 取数
    fetch_api.kline (pipeline.py:253) → res.rows（每根 bar 含 time_key=Unix 秒）
    【断】🔴 time_key 单位：库里已是 Unix **秒**；回读时传 unit="s"（pipeline.py:259）
        ↓
[4] 按 bar 的美东日分组入库
    bar_et_day(b["time_key"], unit="s") (pipeline.py:259) → 键的"交易日"位
    【断】🔴 交易日 = **美东日**，不是本机日（承 L1）；用本机时区分组 → 跨日 bar 归错天
        ↓
[5] 原子入库
    axis.put(d, sym, "kline", payload) (pipeline.py:271) —— 一天一个原子单位（承 L4/X2）
    【断】中途失败 → 当天不残（承 L4）
```

**日历 / 复权因子链路**：`run_calendar`（全局项 `(交易日, None, calendar)`，**只写开市日**，承 K4，`pipeline.py:137`）；`run_adjust_factors`（`(除权日, 标的, adjust_factor)`，走 OpenD `get_rehab` 不吃额度，`pipeline.py:188`）。

---

## 四、链路地图 ④：时区 / 复权 / 对齐契约红线（跨层，最易踩）

### 4.1 时区与时间戳（P1 级）

- **唯一时间表示 = Unix 秒（int）**，交易日一律按**美东**判定。见 `trading_time.py:1-24`。
- 🔴 **数字时间戳必须显式给 `unit`（`"s"`/`"ms"`），不猜**（`trading_time.py:90`）；自描述输入（`datetime`/ISO）不给 unit。
- 🔴 **量级校验**：结果落在 `[1980, 2100)` 之外 → 报错（`trading_time.py:80`）。常见越界 = 单位错（秒÷1000 → 1970）。
- 🔴 **统一到秒 ⇒ 不含逐笔（tick）**（tick 需亚秒精度，另论）。
- **两通道 `time_key` 写法不同**：REST 是毫秒纪元数、OpenD 是美东本地 naive 字符串 —— 混在一张表里排序/去重/对齐全错。收敛点 = `trading_time`。

### 4.2 复权口径（谁决定？）

- 数据层**只存 raw + 因子**（`kline` 存不复权 + `adjust_factor` 单独存），**不烘焙任何口径、不设默认**。
- **决定权在消费方**（回测/研究）：**回测用 hfq（后复权，严格因果）；qfq（前复权）含未来除权信息，禁止用于回测**。
- 详见 `data-layer/docs/reference/futu/adjustment-factor.md` 与 `Agent.md` §4.4。

### 4.3 多品种对齐

- 对齐是**纯算子、按需、默认交集、禁 bfill、不存储、不强制**（`engine/time_alignment.py:1-18`）。
- 停牌的数据层职责 = **不造假**（存储/读取不补），**不是**"对齐"——别把两者搞混。

### 4.4 已知缺口（不假装有机制）

- **跨进程无版本握手**：`provide/api.py:27` 有 `CONTRACT_VERSION="1.0"`，但 `provide.cli` 没有 `version` 子命令 → 回测拿不到数据层契约版本，**无法运行时比对**（`backtest/data_source.py:13-17`）。契约变更时下游以"字段缺失/结构不符"报错，而非静默错算。
- **非 bar 级日频数据项对齐未接**：`backtest/data_source.py::align_daily_items`（:118）是 `NotImplementedError` —— 是**能力缺口**，不是"已经能用的预留"。

---

## 五、对账武器：provide.cli / fetch_cli（确认断在数据层还是下游）

当日志显示"下游没拿到预期结果"，用 CLI 对账**数据层真实落库与返回**，区分断点层：

- `provide.cli days` / `symbols --day <D>` / `items --day <D> --symbol <S>`：库里有啥。
- `provide.cli get|matrix|timeseries`：某键的真实值 / 某天横截面 / 某标的序列。
- `provide.cli align --symbols <S…> --item kline`：对齐面板（看 `strategy` / `n_dropped` / `n_filled` 自证字段）。
- `fetch_cli chain|kline|snapshot|watchlist`：**取数能力**本身对不对（区分"没取到" vs "取到了没读对"）。

> 若对账证明数据层返回完全正确、只是下游读错 → 回到第一~四节修下游的字段/口径对齐，**勿动数据层**。

---

## 六、与通用排查5步法的衔接

- **State 1（现象对齐）**：走通用引擎，复述痛点。
- **State 2（提取证据）**：先跑〇节的 `provide.cli` / `fetch_cli` 拿真实 JSON；疑取数问题用 `fetch_cli` 对账。
- **State 3（物理断点）**：**对照第一~四节链路地图，指"第几跳、哪个契约没守"**（均带 `文件:行` 定位）。
- **State 4（靶向修复）**：守红线（对齐由数据层保证 / 缺就是缺 / 数字时间戳显式单位 / 复权口径由消费方定 / 4H 不隐式走 OpenD），最小改动。
- **State 5（闭环验证）**：呼应 State 1，跑相关 `data-layer/tests/test_*.py`（必要时 L2/L3 门禁）；下游侧跑 `backtest/tests/`。
