# fetch —— 取数（换源只动这里）

> **唯一允许联网的地方**（承 F3）。上游（`engine/` / `provide/`）**不许出现源名**
> —— `cboe` / `futu` 这类字样只在本目录内出现（承 F1）。

---

## 1. 三个源的定位（先看这张表）

| 源 | 注册名 | 拿什么 | 吃额度？ |
|---|---|---|---|
| **CBOE** | `cboe` | 期权链（SPX / NDX / SPY / QQQ / IWM） | 无（公开） |
| **富途 REST** | `futu-rest` | **历史 K 线（日线 / 长历史）** | **无** ← 默认 |
| **富途 OpenD** | `futu-opend` | 全市场快照、少量快照、自选、分钟线（含 4H）、复权因子、板块 | **历史 K 线吃额度** |

**取数默认原则（写死）**：

> **K 线默认一律走 REST，绝不隐式走 OpenD。**（OpenD 拉任何历史 K 线都吃额度，实测拉一只 `used` +1）
> OpenD 的**正确用途**：**不吃额度**的那些 —— 全市场快照 / 少量快照 / 自选 / 复权因子 / 板块。
> **4H（`K_240M`）REST 不支持 → 默认报错**，要用必须**显式** `source="futu-opend"`。

---

## 2. 统一入口 `fetch_api.py`

```python
available_sources() -> list[str]

chain(symbol, *, as_of=None, source=None) -> ChainResult
spot(symbol, *, source=None) -> tuple[float, object]
kline(symbol, *, as_of=None, ktype="K_DAY", count=1000, index=False,
      years=3, months=None, source=None)
snapshot(*, market="US", source=None)
watchlist(*, source=None)
plate_list(*, market="US", plate_type="CONCEPT", source=None)
plate_members(plate_code, *, source=None)
trading_days(market, start, end, *, source=None)
rehab(symbol, *, source=None)
```

---

## 3. 各文件职责

| 文件 | 作用 |
|---|---|
| `fetch_api.py` | ★**统一入口**（上面那批函数） |
| `source_registry.py` | 源注册表：`register(name, factory)` / `keys()` / `get_source(name)` / `default_source_name()`（读 `TRADING_DESK_SOURCE`，默认 `cboe`） |
| `rate_limit.py` | 限频器：`declare(bucket, *, calls, per_seconds)` / `wait(bucket)` / `declared_buckets()` / `reset()` |
| `fetch_types.py` | `FetchError`、`Request`、`ChainResult`、`CHAIN_FIELDS` |
| `risk_free_rate_source.py` | SOFR 取数：`fetch_rate(*, timeout=15, allow_fallback=False) -> RateResult`（失败默认抛错；回退必须**显形**） |
| `fetch_cli.py` | 探查 CLI（见 §5） |
| `sources/cboe_options_source.py` | `cboe` 源 |
| `sources/futu/opend_source.py` | `futu-opend` 源 |
| `sources/futu/rest_source.py` | `futu-rest` 源 |

> ⚠️ **文件名注意**：文档里常写的 `fetch/registry.py`，**实际文件是 `fetch/source_registry.py`**。

---

## 4. 限频桶（实测数字，非猜）

| 桶 | 限额 | 备注 |
|---|---|---|
| `rest:kline` | 1 / 0.7s | REST K 线 |
| `opend:watchlist` / `opend:plate` / `opend:screen` | 10 / 30s | OpenD 快照类 |
| `opend:rehab` | 60 / 30s | **复权因子**。曾只写在 docstring 没接限流器 → 批量 328 只时 **268 只被打回** |

---

## 5. 探查 CLI（`fetch_cli.py`）

```bash
cd data-layer
.venv/bin/python -m fetch.fetch_cli sources
.venv/bin/python -m fetch.fetch_cli ktypes
.venv/bin/python -m fetch.fetch_cli chain SPX
.venv/bin/python -m fetch.fetch_cli kline AAPL --ktype K_DAY
.venv/bin/python -m fetch.fetch_cli snapshot --max-pages 1
.venv/bin/python -m fetch.fetch_cli watchlist
.venv/bin/python -m fetch.fetch_cli trading-days --market US --start 2026-10-01 --end 2026-10-06
```

子命令：`sources` | `ktypes` | `chain` | `kline` | `snapshot` | `watchlist` | `trading-days`

---

## 6. 已知边界（刻意未接）

| # | 边界 | 说明 |
|---|---|---|
| 1 | **`spot()` 仅 `cboe` 实现** | 富途两源无 `fetch_spot` → 调用会抛 `FetchError`（不支持 spot 取数） |
| 2 | **REST 无 `K_240M`（4H）、无 `K_YEAR`** | 需显式 `source="futu-opend"` |
| 3 | **美股指数 K 线拿不到** | OpenD 权限所限（见 `docs/reference/futu/opend.md` §3.8） |
| 4 | 凭据在仓库外 | `~/.config/futu/private_key.pem` + `appkey`（`chmod 600`） |

---

## 7. 平台资料（实测事实库）

**`../docs/reference/futu/`** —— 遇"OpenD / REST 支持什么"的问题，**先查这里，别读源码**：

| 文件 | 查什么 |
|---|---|
| `README.md` | 两通道分工 / 周期路由 / 额度 |
| `opend.md` | OpenD：能拿什么、ktype 全表、额度分档、6 个坑 |
| `rest.md` | REST：Ed25519 签名、翻页语义、限流 |
| `code-formats.md` | 代码格式（`US.AAPL`）、市场推断、校验 |
| `adjustment-factor.md` | 复权因子官方公式、A/B 语义、qfq 含未来 / hfq 因果 |

---

## 8. 测试

- `tests/test_fetch.py` —— M2 标准 F1–F5（源注册 / 统一出口 / 失败必报）
- `tests/test_futu_sources.py` —— 富途两通道取数能力
- `tests/test_rate_limit.py` —— 限频器（间隔生效、未声明桶报错）
- `tests/test_s8_screen.py` —— RPS / 行业 + 富途源失败必报
