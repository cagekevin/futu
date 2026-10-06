# 富途 —— OpenD 通道

> 本机 `127.0.0.1:11111`。客户端：`fetch/sources/futu/opend_source.py`（futu-api SDK）。
> 总览见 `README.md`；REST 见 `rest.md`；代码格式见 `code-formats.md`；**复权因子见 `adjustment-factor.md`**。
> 事实来源：**本机实测**（futu-api 10.11.7108，OpenD server_ver 1011）。

---

## 1. 能拿什么（取数能力）

| 接口 | 用途 | 吃历史额度？ | 我们的封装 |
|---|---|---|---|
| `request_history_kline` | 历史 K线（全周期） | **是** | `FutuSource.fetch_kline`（**默认不调用**，见下） |
| `get_stock_screen`（V2） | 全市场快照（价/市值/行业/涨幅） | **否** | `FutuSource.fetch_snapshot` |
| `get_market_snapshot` | 少量标的快照 | **否** | `FutuSource.fetch_market_snapshot` |
| `get_user_security_group` / `get_user_security` | 自选清单 | 否 | `FutuSource.fetch_watchlist` |
| `get_rehab` | **复权因子**（每除权日一条） | **否** | `FutuSource.fetch_rehab`；**详见 `adjustment-factor.md`** |
| `get_history_kl_quota` | 查历史额度余量 | 否 | — |
| `get_global_state` | 连接/登录状态 | 否 | — |

> ★ **默认不用 OpenD 拉 K线**（吃额度）。`fetch/fetch_api.py::kline` 默认走 REST；
> 只有**显式 `source="futu-opend"`** 才走 OpenD 的 `request_history_kline`（自担额度）。
> OpenD 在本系统的主要用途是**不吃额度**的那些：全市场快照（`get_stock_screen`）、
> 少量标的快照（`get_market_snapshot`）、自选。

---

## 2. ktype 全表（`futu.KLType`，**字符串**）

实测 `dir(KLType)` + 逐个 `ret=0` 确认，**原生全集**（`fetch/sources/futu/opend_source.py::NATIVE_KTYPES`）：

```text
K_1M  K_3M  K_5M  K_10M  K_15M  K_30M  K_60M
K_120M(2H)  K_180M(3H)  K_240M(4H)
K_DAY  K_WEEK  K_MON  K_QUARTER  K_YEAR
```

- **4H（`K_240M`）原生支持** —— 实测拿到 AAPL 真实 4H（每日 2 根：13:30 / 16:00 开盘）。
- `autype`：`0=不复权` / `1=前复权` / `2=后复权`。我们取数用 **`None`（不复权）**。
  ⚠️ **前复权含未来、后复权因果** —— 回测只能 hfq；口径与公式见 `adjustment-factor.md`。

⚠️ **与 REST 的数字编号是两套映射** —— 别混（见 `rest.md`）。

---

## 3. 坑（每条都真实发生过）

### 3.1 返回**三元组**（不是二元组）

```python
ret, data, page_req_key = ctx.request_history_kline(...)   # ✅
ret, data = ctx.request_history_kline(...)                 # ❌ ValueError: too many values
```

端方旧注释 / 旧文档写二元组 —— **实测三元组**（futu-api 10.11.7108）。

### 3.2 `request_history_kline` **忽略 `end`**，只认 `start`

实测：传 `end=2026-10-06` 或不传，**都从最早返回**（拿到 2025-10 的数据）。
想拿**最新**必须传 **`start`**（往前推足）。

```python
# ❌ 拿到上市最初的 N 根
ctx.request_history_kline("US.AAPL", end="2026-10-06", max_count=10)
# ✅ 用 start 从起点往后
ctx.request_history_kline("US.AAPL", start="2025-09-16", max_count=50)  # → 到 2026-10-05
```

⇒ **REST 相反**（`end`+`num` 往前翻）。是我们封装里 `_default_start` 存在的原因。

### 3.3 protobuf 必须 `< 5`

`get_stock_screen`（全市场快照）在 protobuf ≥ 5 直接报：

```
'google._upb._message.FieldDescriptor' object has no attribute 'label'
```

（5.x 删了 `FieldDescriptor.label`，futu-api 内部还在用。）
⇒ `requirements.txt` 锁 `protobuf<5`；实测降到 **4.25.9** 后正常。

### 3.4 分钟线去重键必须是 `time_key`

同一天多根，用 `date` 去重**会把一天压成一根**。我们封装已按此处理（`intraday` 分支）。

### 3.5 `page_from` 是**偏移量**，不是页号

端方 `screen_v2.py:24-27`：按页号翻页会**大面积重复**；必须 `0 / 200 / 400` 推进。

### 3.6 V1 `get_stock_filter` 已弃用

新版 SDK **没有** `StockFilter`（`ImportError`）。只用 V2 `get_stock_screen`。
（V1 只回代码、要本地补字段 → 美股 OTC 整批失败 → 撞限频：实测 90 秒 vs V2 的 0.84 秒。）

### 3.7 分钟线**历史深度有限**（实测）

`K_240M` 从 400 天窗口取，**末根只到 2026-09-01**（不是最新 10-05）。
⇒ OpenD 适合"给窗口取近期"；**长历史 & 最新 → 用 REST**。

### 3.8 美股**指数** K线拿不到（权限）

实测 `api.kline("SPX", index=True)` → `暂不支持美股指数`。
⇒ 指数（SPX/NDX）K线不走富途个股通道；本系统指数行情另有来源（CBOE）。

### 3.9 限频（技能 `API_LIMITS.md`）

规则：**30 秒内最多 n 次**。批量循环调用受限频接口时，**循环里要 `time.sleep()`**。
（`request_history_kline` 有额度限制，但**限频数字未单列**；保守按 700ms 间隔。）

---

## 4. 额度（分档，技能 `API_LIMITS.md`）

**历史 K线额度**：
- **最近 30 天内**，每请求 1 只占 1 个额度；
- 30 天内重复请求同标的不重复累计；**同标的不同周期只占 1 个**；
- 调 `request_history_kline` 前应先 `get_history_kl_quota(get_detail=True)` 查余量。

**分档**（订阅与历史 K线同档）：

| 用户类型 | 额度 |
|---|---|
| 开户用户 | 100 |
| 总资产达 1 万 HKD | **300** |
| 总资产达 50 万 HKD / 月交易笔数 > 200 / 月交易额 > 200 万 HKD（任一） | 1000 |
| 总资产达 500 万 HKD / 月交易笔数 > 2000 / 月交易额 > 2000 万 HKD（任一） | 2000 |

**本机实测**：`used=6 / remain=294 / total=300`（1 万 HKD 档）。
明细示例（`get_detail=True`）：`[{code: US.TSLA, request_time: 2026-10-06 16:00:22}, …]`。

**其他上限**（技能 `API_LIMITS.md`）：

| 接口 | 限制 |
|---|---|
| `get_market_snapshot` | 每次最多 **400** 个标的 |
| `request_history_kline` | 单次 `max_count` ≤ **1000**，超过用 `page_req_key` 翻页 |
| `get_stock_filter` | 单次最多 200 个结果 |

---

## 5. 版本 / 环境

- `futu-api` 实测 **10.11.7108**；`protobuf` **4.25.9**（**< 5**）。
- 早期版本坑：无 `ai_type` 参数（需 ≥ 10.4.6408）；无 `OpenCryptoTradeContext`（需 ≥ 10.5.6508）。
- 安装 / 升级：见技能 `install-opend/GUIDE.md`（本文不复制）。

> **我们不做交易** —— `OpenSecTradeContext` / 下单 / 解锁等见技能 `futuapi/GUIDE.md`，
> 不在本系统范围（plan §十：不做 UI、不做交易）。
