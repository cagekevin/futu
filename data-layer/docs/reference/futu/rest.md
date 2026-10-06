# 富途 —— REST 通道

> `https://webapi.futunn.com`，**Ed25519 签名**。客户端：`fetch/sources/futu/rest_source.py`。
> **无额度** —— 批量历史 K线的正道。
> 总览见 `README.md`；OpenD 见 `opend.md`；代码格式见 `code-formats.md`。
> 事实来源：**端方源码 `fetch/rest/lib/futu-client.mjs` + `kline.mjs`**（逐行对照）+ **本机实测**。

---

## 1. 为什么需要它

- OpenD 拉历史 K线**吃额度**（见 `opend.md` §额度），全市场几千只一次撞墙；
- REST **不吃额度** ⇒ **批量历史数据走这里**。
- 端方 README:196：「① 数据 全走 REST —— 不需要 OpenD、不消耗任何额度。」

---

## 2. 鉴权（Ed25519）

**签名原文 5 段**（`futu-client.mjs:40`）：

```text
{timestamp_ms}\n{HTTP_METHOD}\n{request_path}\n{query_string}\n{body_sha256_hex}
```

- Ed25519 直接对原文签名，结果 **Base64** 放 `Authorization`（**不加 Bearer 前缀**）。
- 请求头：`X-Api-Key`（appkey）/ `X-Timestamp` / `X-Nonce`（`randomBytes(8).hex`）/ `Authorization`。
- **query 与签名原文共用同一字符串** —— 签名必须基于"最终发出的请求"，顺序/编码不一致 → 验签失败。
- 凭据（仓库外，`chmod 600`）：`~/.config/futu/private_key.pem` + `~/.config/futu/appkey`

**时钟**：富途要求偏移 ≤ 5s，否则 `-12006` → 先 `GET /api/v1.0/server-time` 校准
（`futu-client.mjs:31`：`offset = server_ms - round((t0+t1)/2)`）。

**客户端不设请求超时**（裸 `fetch`）—— 我们同样不设（`requests` 无 `timeout`）。

---

## 3. ktype（数字编号，★ 与 OpenD 不同）

实测扫 `ktype=0..30`，**有效集合**：`1 2 3 4 5 6 7 8 9 10 11 14 15 26 29`

| ktype | 周期 | 依据 |
|---|---|---|
| 1 | 1 分 | time_key 间隔 60s |
| 2 | 日 | 端方 `periods.mjs:9` |
| 3 | 周 | 端方 `depth.mjs:7` |
| 4 | 月 | 端方 `depth.mjs:7` |
| 5 | 季 | time_key 跨度大 |
| 6 | 5 分 | 端方 `depth.mjs:7` |
| 7 | 15 分 | 端方 `depth.mjs:7` |
| 8 | 30 分 | 端方 `periods.mjs:10` |
| 9 | 60 分 | 端方 `periods.mjs:11` |
| 10 | 3 分 | time_key 间隔实测 |
| 11 | 10 分 | time_key 间隔实测 |
| 14 | 120 分（2H） | 端方 `depth.mjs:7` |
| 15 | 180 分（3H） | time_key 间隔实测 |
| 26 / 29 | 待确认（日内） | time_key 间隔实测 |

⚠️ **REST 没有 240 分（4H）** —— `ktype=16` 返回 `-3 parameter invalid`。4H 必须走 OpenD。
⚠️ **REST 的 `5` = 季** —— 与 OpenD 的 `K_QUARTER` 不是同一映射，**两套别混**。

---

## 4. 翻页语义（★ 与 OpenD 相反）

`history-kline` 用 **`end` + `num`** 从今**往前**翻页（`kline.mjs:92-136`）：

```text
end = today
每页: GET history-kline?end={end}&num=370&ktype=..&autype=0
      收集 bars
      earliest = 本页最早那根
      if earliest <= since: 停
      end = earliest - 1天（日线） / earliest（分钟线）
```

**关键差异**（`kline.mjs:24-27`）：

| | 日线 | 分钟线 |
|---|---|---|
| 去重/排序键 | `date` | **`time_key`**（否则一天被压成一根） |
| 翻页 `end` | 本页最早日 **− 1 天** | 本页最早日**（不减 1）** ← 否则同日后半段被跳过 |
| 防死循环 | — | `earliest.time_key >= prevEarliest` 即停 |
| 窗口参数 | `years`（默认 2） | `months`（默认 3） |

⚠️ **`num` 上限 370**（`kline.mjs:102`）；REST **无额度**，但有**限频**。

---

## 5. 限流

两种表现（`futu-client.mjs:74-84`）：
- HTTP **429**，或
- HTTP **200 + `ret_code=-11`**（rate limit exceeded）。

等待策略：`retry-after` header，或错误里的 `retry after Ns` 正则；
兜底 `2^attempt * 3000ms`，**再 +1000ms**。`retries=4`。

**请求间隔**：端方 `GAP_MS = 700ms`（`kline.mjs:61`）；`depth.mjs` 同。

---

## 6. 已知坑

- **复权口径（★ 我们已统一，2026-10-06）**：端方 `kline.mjs` 用 **`autype=1`（前复权）**，
  而我们**统一用 `autype=0`（不复权）** —— 因为 OpenD 通道用不复权，
  两通道口径必须一致（否则同标的价格不一致 = P1 错）。
  复权因子单独存（`adjust_factor`，见 `opend.md`）。
- **前复权会回溯变化**：除权后历史复权价整体变；增量 append 会造成"前半段旧基准、后半段新基准"的断裂。
  端方做法：挑一根已收盘的已知 K线比对 `close`，不一致 ⇒ 全量重拉（`kline.mjs:321-339`）。
- **成交量精度**：部分品类（加密/外汇）`volume` 放大 10^n（`volume_precision`）。端方已还原。
- **"正在形成"的 bar 可能缺 OHLC**（加密 7×24 最常见）→ **直接丢弃**（否则下游 KeyError）。
- **落盘日期用「标的市场本地日期」**，不用本机日期（`time_zone` 字段）。

---

## 7. 端点是哪些

| 用途 | 端点 |
|---|---|
| 时钟校准 | `GET /api/v1.0/server-time` |
| 历史 K线 | `GET /api/v1.0/quote/{symbol}/history-kline` |
| 交易日历 | `GET /api/v1.0/quote/trading-days` |
| 自选分组 | `GET /api/v1.0/quote/user-security-group` |
| 分组标的 | `GET /api/v1.0/quote/user-security` |

（更多端点见端方 `fetch/rest/` 目录 + 技能 `futuapi/docs/API_REFERENCE.md`。）

---

## 8. 我们实现了什么

`fetch/sources/futu/rest_source.py::FutuRestSource`：
- Ed25519 签名 + 时钟校准 + 限流退避（逐参数对齐 `futu-client.mjs`）；
- `fetch_kline`：`end`+`num` 翻页，日线/分钟线分支（`time_key` 去重、`end` 不减 1 天）。

**参数全部抄端方**（超时=无 / retries=4 / GAP=700ms / num=370 / MAX_PAGES=60），不自创。
