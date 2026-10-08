# provide —— 对外唯一接缝

> **下游唯一允许碰的地方。** 回测层 / 因子层 / 前端**只经这里**取数，不许知道
> `store/` `fetch/` `engine/` 怎么实现（承 `Agent.md` §1、P2）。
> 契约版本：`CONTRACT_VERSION = "1.0"`

---

## 1. 两种调用方式

| 方式 | 入口 | 备注 |
|---|---|---|
| **跨进程 JSON**（推荐） | `python -m provide.cli <cmd>` | 任何语言都能调；**下游默认用这个** |
| 同进程 Python | `from provide.api import Access` | 仅限在 `data-layer/` 目录内运行 |

---

## 2. `Access` 全部公开方法（`provide/api.py`）

```python
Access(root: Path | None = None)     # 默认指向 config.DATA_DIR
```

| 方法 | 返回 | 作用 |
|---|---|---|
| `days()` | `list[str]` | 库内全部交易日（升序）—— **含只有复权因子的早期日期，见 §5 陷阱** |
| `symbols(day)` | `list[str]` | 某天**所有键**（含板块 `LIST*`、保留码，不含全局项） |
| `stocks(day)` | `dict` | ★**股票票池**（过滤归本层）。见 §3 |
| `items(day, symbol)` | `list[str]` | 某天某标的的数据项 |
| `global_items(day)` | `list[str]` | 某天全局数据项（如 `calendar`） |
| `latest_day()` | `str \| None` | 最新有值日 |
| `value(day, symbol, item, *, default=Missing)` | 任意 | **单点取值**（已解包 `{"value":…}`）；`symbol=None` = 全局项 |
| `record(day, symbol, item)` | `dict` | 完整行式记录（含 `source_key` / `computed_at` / `bucket` / `weight_col` / `as_of` 自证字段） |
| `batch(requests)` | `list[Record]` | ★批量取 `[(day, symbol, item), …]`；**缺的跳过不补** |
| `matrix(day, items, symbols=None)` | `dict[str, dict]` | ★横截面二维表 `{symbol: {item: value}}` |
| `timeseries(symbol, item, days=None)` | `list[Record]` | 单标的跨日行式序列 |
| `panel(symbols, item, days=None, *, adjust=None)` | `dict` | ★**截面**语义：按日分组、**组内可缺、不做对齐** |
| `align_panel(symbols, *, item="kline", days=None, min_bars=..., adjust=None)` | `dict` | ★**时序对齐**：默认交集，不够才降级并集 + 仅 ffill |
| `export_rows(day, items, symbols=None)` | `list[dict]` | 行式导出 `[{day, symbol, item, value}, …]` |

### 命名错位警告

> **`Access` 没有 `get()`，也没有 `export()`。**
> 单点取值叫 **`value()`** / **`record()`**；导出叫 **`export_rows()`**。
> CLI 的 `get` 子命令映射到的是 **`record()`**。

---

## 3. `stocks(day)` 的返回结构（关键）

```json
{
  "day": "2026-10-05",
  "snapshot_day": "2026-10-06",
  "snapshot_is_after_day": true,
  "stocks": ["AA", "AAOI", "AAPL", ...],
  "n_excluded_plate": 23,
  "n_excluded_reserved": 1,
  "n_excluded_non_stock": 58
}
```

- `stocks` = 该日**股票**票池（已排除板块代码 / 保留码 `UNIVERSE` / ETF-指数）
- ⚠️ **`snapshot_is_after_day = true` 表示"用了晚于该日的快照来判定票池"** ——
  即**含未来信息**。接口已显形（承 P5），**用的人必须读这个字段并在报告里声明**。
- 无快照 → `ValueError`（不静默返回空）

### `symbols` 与 `stocks` 的区别（别混用）

| | `symbols(day)` | `stocks(day)` |
|---|---|---|
| 是什么 | 该日**所有键** | 该日**股票票池** |
| 含板块 `LIST*` | 含 | 不含 |
| 含 `UNIVERSE` | 含 | 不含 |
| 用途 | 探查库里有什么 | **做截面研究的正确入口** |

### `align` 与 `panel` 的区别（语义相反）

| | `align` | `panel` |
|---|---|---|
| 语义 | **时序对齐**（按 bar 时间戳） | **截面面板**（按交易日分组） |
| 缺数据 | 交集；不够才降级并集 + ffill | **组内可缺，不对齐**（缺就是缺） |
| 返回 | `{index, values, strategy, …}` | `{item, days, symbols, values[symbol][day], …}` |
| 谁用 | `backtest`（单标的 × 跨日） | `factor-layer`（多标的 × 单日） |

---

## 4. CLI 全部子命令（`provide/cli.py`）

输出恒为 JSON。`get` 查不到时输出 `{"error":"missing"}` 且**退出码 3**。

| 子命令 | 参数 | 底层调用 |
|---|---|---|
| `days` | — | `days()` |
| `symbols` | `--day`(必填) | `symbols()` |
| `stocks` | `--day`(必填) | `stocks()` |
| `items` | `--day` `--symbol`(必填) | `items()` |
| `get` | `--day` `--symbol` `--item`(必填) | `record()` |
| `matrix` | `--day` `--items`(必填, 可多个) `--symbols`(可多个) | `matrix()` |
| `timeseries` | `--symbol` `--item`(必填) | `timeseries()` |
| `export` | `--day` `--items`(必填) `--symbols` | `export_rows()` |
| `align` | `--symbols`(必填) `--item`(默认 kline) `--days` `--min-bars` `--adjust` | `align_panel()` |
| `panel` | `--symbols`(必填) `--item`(默认 kline) `--days` `--adjust` | `panel()` |

**无 CLI 入口**（仅 Python 层可用）：`batch`、`latest_day`、`global_items`。

### `--adjust` 语义（复权）

- **不传** = 原样 raw（**不是默认 hfq**）
- `--adjust hfq` = 后复权（**严格因果，回测必须用这个**）
- `--adjust qfq` = 前复权（**含未来除权信息，禁止用于回测**）
- 返回里带 `n_adjust_events`（本次用到几个除权事件，**0 = 复权是空操作**）
  与 `contaminated_days`（派现日，**不可精确复权**，下游须显形处理）

---

## 5. 已知陷阱（用之前必读）

| # | 陷阱 | 说明 |
|---|---|---|
| **T1** | **`days()` 包含 1987 年起的大量日期** | 它返回「库里有**任何**数据的日期」，而那些早期日期只有 `adjust_factor`。**它不是「有 K 线的交易日」**——做截面研究请用 `stocks(day)` 或 kline 实际覆盖 |
| **T2** | `snapshot_is_after_day` | 见 §3，含未来信息，必须读 |
| **T3** | `panel` 对 K 线**多根/日**会报错 | 那不是它的问题 —— 多根/日请用 `align_panel` |
| **T4** | `panel` 的 `adjust` 必须显式 | M3 因子的 `spec.adjust` 若与面板口径不符会报错（这是**故意的**，防除权日假跳变） |

---

## 6. 契约测试在哪

- `tests/test_provide.py` —— M4 标准 P1–P4（对外接口 / 票池 / 对齐 / 复权）
- `tests/test_usecase_timing.py` —— 只靠后端（经 provide）取全择时数据
- 跨层：`factor-layer/tests/test_panel.py`、`backtest/data_source.py` 均钉在此契约上
