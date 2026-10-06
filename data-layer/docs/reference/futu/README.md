# 富途平台资料 —— 总览（两通道分工）

> **本目录只写"实测确认的事实"**，每条标来源（端方源码行号 / 本机实测命令 / 技能文档）。
> 不确定的一律写「待确认」，**不猜**（承 plan P1 反掩盖）。
>
> 建立：2026-10-06 ｜ 分册：`opend.md` / `rest.md` / `code-formats.md` / `adjustment-factor.md`

---

## 0. 为什么分成 5 份

富途**不是一个 API，是两个独立通道**，各有各的鉴权 / 额度 / 坑：

| 分册 | 覆盖 |
|---|---|
| **`README.md`**（本文） | 总览 + 两通道对照 + 周期路由 + 我们的实现映射 |
| **`opend.md`** | OpenD 通道专章（SDK、ktype、三元组、protobuf、坑） |
| **`rest.md`** | REST 通道专章（Ed25519 签名、翻页语义、坑） |
| **`code-formats.md`** | 代码格式 / 市场推断 / 校验（跨通道，取数必需） |
| **`adjustment-factor.md`** | 复权因子专章（官方公式、A/B 语义、qfq/hfq 口径） |

**为什么要总览**：两通道**必须对照着看**（ktype 编号不同、翻页语义相反），
混写一份会失控，分开又不便对照 —— 所以总览串起来、细节各自成篇。

---

## 1. 核心：两个通道，分工明确

| | **REST**（`webapi.futunn.com`） | **OpenD**（本机 `127.0.0.1:11111`） |
|---|---|---|
| 客户端 | `fetch/sources/futu/rest_source.py`（Python 复刻端方 `futu-client.mjs`） | `fetch/sources/futu/opend_source.py`（futu-api SDK） |
| 鉴权 | **Ed25519 签名**（`~/.config/futu/private_key.pem` + `appkey`） | 本机 OpenD 已登录 |
| **额度** | **无** | **历史K线额度**（本机实测 total=300）+ **订阅额度**；**服务器端筛不吃额度** |
| 规模 | **全量**（几百到几千只，长历史） | **少量**（个股深挖）+ **全市场快照**（不吃额度） |
| 依赖 | Python + `cryptography` | Python + `futu-api` + **protobuf < 5** |
| 擅长 | **批量历史 K线**（日线及以上，可翻很多年） | **分钟线全周期（含 4H）**、**全市场快照**、少量实时 |

**一句话**：**历史/批量 → REST；实时/快照/少量 → OpenD。**

> ★ **默认原则（2026-10-06 定）：K线取数默认一律走 REST，绝不隐式走 OpenD。**
> 因为 **OpenD 拉任何历史 K线（含 4H）都吃额度**（实测：拉 TSLA 4H → `used` 5→6）。
> ⇒ `fetch/fetch_api.py::kline` **默认 REST**；要 OpenD **必须显式传 `source="futu-opend"`**（自担额度）。
> 4H（`K_240M`）REST 不支持 → 默认**明确报错**，不静默吃额度（承 P1：资源消耗必须显式）。

- 端方 README:138：「REST 把全量数据搬回家；OpenD 负责服务器端粗筛和个股深挖。」
- 端方 README:196：「① 数据 全走 REST —— 不需要 OpenD、不消耗任何额度。」

---

## 2. 周期 → 走哪个通道（★ 最容易踩）

**两通道的 ktype 是两套不同映射**（详见 `opend.md` §ktype / `rest.md` §ktype）：

| 周期 | 走哪 | 原因 |
|---|---|---|
| 日 / 周 / 月 / 季 | **REST** | 无额度，可翻很多年 |
| 1/3/5/10/15/30/60/120/180 分 | **REST 或 OpenD** | 两通道都有；REST 用数字编号，OpenD 用 `K_xxM` |
| **240 分（4H）** | **OpenD（`K_240M`）** | **REST 不支持**（ktype=16 invalid） |

**代码里怎么走**：`fetch/fetch_api.py::kline` **默认走 REST**。
4H（`K_240M`）REST 不支持 → 默认**报错**（提示显式传 `source="futu-opend"`），**不静默吃额度**。

---

## 3. 额度（唯一的硬约束）

> 详细分级与数字见 `opend.md` §额度。这里只放结论。

| 项 | 限制 | 来源 |
|---|---|---|
| **历史 K线** | 30 天窗口内，**每标的占 1 个**（同标的不同周期只占 1） | 技能 `API_LIMITS.md` |
| 历史 K线**额度分档** | 开户 100 / 总资产≥1万HKD **300** / ≥50万HKD 1000 / ≥500万HKD 2000 | 技能 `API_LIMITS.md` |
| 本机账号 | 实测 `used=6 / remain=294 / total=300`（1万HKD 档） | 本机实测 |
| **订阅** | 每 (标的,类型) 占 1；断开 ~1 分钟归还 | 技能 / 端方 |
| **服务器端筛（快照）** | **不吃历史额度** | 端方 README:599 |
| **REST** | **无额度** | 端方 README:196 |

⇒ **铁律**：**绝不用 OpenD 批量拉历史 K线**（一次就撞墙）。
全市场快照走 OpenD 服务器端筛（不吃额度）；历史 K线走 REST。

> ⚠️ **口径提示**：端方 README 写「300 标的 / 7 天滚动释放」，
> 技能 `API_LIMITS.md` 写「30 天窗口 + 分档 100/300/1000/2000」。
> 两者**不完全一致**（可能版本不同）。本机实测 **total=300**，与技能"1万HKD 档"吻合。
> 以**技能文档 + 实测**为准，端方数字仅作参照。

---

## 4. 我们的实现映射（`fetch/`）

| 数据 | 我们走哪 | 文件 | 状态 |
|---|---|---|---|
| **期权链** | CBOE（公开） | `fetch/sources/cboe_options_source.py` | ✅ |
| **全市场快照**（chg/行业/市值） | OpenD V2 `get_stock_screen` | `fetch/sources/futu/opend_source.py::FutuSource.fetch_snapshot` | ✅ |
| **市场快照**（少量代码） | OpenD `get_market_snapshot` | `fetch/sources/futu/opend_source.py::FutuSource.fetch_market_snapshot` | ✅ |
| **自选清单** | OpenD `get_user_security_group` | `fetch/sources/futu/opend_source.py::FutuSource.fetch_watchlist` | ✅ |
| **复权因子** | OpenD `get_rehab`（不吃额度） | `fetch/sources/futu/opend_source.py::FutuSource.fetch_rehab`；见 `adjustment-factor.md` | ✅ |
| **K线（日线/长历史）** | REST（无额度） | `fetch/sources/futu/rest_source.py::FutuRestSource.fetch_kline` | ✅ |
| **K线（分钟线，含 4H）** | OpenD（`K_60M`/`K_120M`/`K_240M`） | `fetch/sources/futu/opend_source.py::FutuSource.fetch_kline` | ✅ |
| **无风险利率** | NY Fed SOFR | `fetch/rates.py` | ✅ |

**统一入口** `fetch/fetch_api.py`：`chain` / `kline` / `snapshot` / `watchlist` / `spot`。
上游**永远不 import 具体源**（承 plan F1）。

**探查命令**：`python -m fetch.fetch_cli sources|ktypes|chain|kline|snapshot|watchlist`

---

## 5. 待确认（不猜）

- REST 的 `ktype=26` / `29` 具体周期（日内，间隔已测但未命名）。
- OpenD 分钟线历史深度的确切边界（已知 240M 到 2026-09-01，未系统探测）。
- `K_QUARTER` / `K_YEAR` 在 REST 的编号（REST 只确认了 `5`=季）。
- 额度口径：「30 天窗口」与「7 天滚动」哪个准（需连续跟踪实测）。
- **复权因子多事件复合的精确形式**（后复权派现是加法、拆股是乘法）—— 需拿富途自己的
  `autype=HFQ/QFQ` K线**对拍**确认；详见 `adjustment-factor.md` §5。

---

## 6. 本目录的资料来自哪

| 来源 | 用途 |
|---|---|
| **端方 futu 项目**（`~/Documents/futu`） | REST 客户端实现、kline 翻页、快照 V2 —— 逐行对照 |
| **futu 技能**（`~/.codebuddy/skills/futu`） | OpenD API 参考、额度分档、错误处理、代码格式 |
| **富途官方文档**（`openapi.futunn.com`） | 接口签名、**复权因子公式**、限频 |
| **本机实测** | 所有标「实测」的结论 |
