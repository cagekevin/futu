# M1 panel —— 截面面板（数据结构 + 取数 + 票池）

> **本层唯一与 `provide` 交互的模块**（承 M1-1）。M2–M5 **禁止**自行 spawn `provide.cli`。
> 依赖方向：`panel_types`（零依赖）→ `{panel_convert, provide_reader, stock_universe}` → `panel_builder`（唯一入口）。

---

## 1. 职责

| 干 | 不干 |
|---|---|
| 定义截面数据的**唯一表示**（宽表 ⇄ 长表） | **不知道「因子是什么」**，只搬运「值」 |
| 从 `provide` 取数、缓存 | **不做标的类型判定**（`LIST*` 前缀 / ETF 名单）—— 那是数据层的事（承 M1-2） |
| 按交易日**切出票池** | 不做任何填充（缺就是缺，承 M1-3） |

---

## 2. 数据结构 `panel_types.py`

```python
@dataclass(frozen=True, eq=False)
class CrossSectionPanel:
    dates              # 交易日（升序）
    symbols            # 标的（升序）
    fields             # {字段名: DataFrame}，index=日期，columns=标的
    universe_by_day    # {day: tuple[symbol, ...]}
    n_adjust_events    # 复权用到的事件数（0 = 复权是空操作）
    contaminated_days  # 派现日（不可精确复权，须显形）
    adjust             # 本面板的口径（hfq / qfq）
    snapshot_day       # 票池判定用的快照日
    snapshot_is_after_day   # ⚠️ true = 含未来信息

    def field(self, name: str) -> pd.DataFrame   # 缺失 → KeyError
```

**契约常量**：
`KLINE_ITEM="kline"`、`ADJUST_HFQ="hfq"`、`ADJUST_MODES=("hfq","qfq")`、
`KLINE_BAR_FIELDS=("open","high","low","close","volume")`、`PANEL_LONG_COLUMNS`、
`SNAPSHOT_ITEM="snapshot"`、`SNAPSHOT_UNIVERSE="UNIVERSE"`、`SNAPSHOT_FIELDS=("symbol","industry","price","market_cap")`

---

## 3. 取数入口 `provide_reader.py`（★唯一出口）

```python
CONTRACT_VERSION = "1.0"
Runner = ...        # 子进程替身，测试可注入

read_days(*, runner=None) -> list[str]
read_stocks(day, *, runner=None) -> dict
read_kline_panel(symbols, *, days=None, adjust=ADJUST_HFQ, runner=None) -> dict
read_snapshot(day, *, runner=None) -> dict
```

> `grep -r "provide.cli\|subprocess" factor-layer/` → **只应命中本文件**。

---

## 4. 票池切片 `stock_universe.py`

| 函数 | 作用 |
|---|---|
| `stocks_from_payload(payload) -> set[str]` | `stocks` 返回 → 股票集合 |
| `universe_by_day(dates, symbols, fields, stocks) -> dict[str, tuple[str, ...]]` | 每日票池 = 股票集合 ∩ 该日任一字段非 NaN 的标的 |
| **`universe_diagnostics(panel) -> dict`** | ★**票池诊断**（是否幸存者集合） |

`universe_diagnostics` 返回：
`n_days` / `n_symbols_ever` / `n_symbols_first_day` / `n_symbols_last_day` /
`n_ended_early` / `ended_early_sample` / `last_seen_position_median` /
`no_symbol_ever_left` / **`caveat`**

> **实测（2026-10-08）**：`n_symbols_ever = 270`、`n_ended_early = 0`、
> `no_symbol_ever_left = True` ⇒ **面板里没有任何标的退出，大概率是幸存者集合**
> ⇒ 截面 IC 被**高估** ⇒ **「不显著」可信、「显著」不可信**。

**设计纪律**：它**只报事实 + 含义，不做判决**（判据只用面板自身信息，是**启发式**）。

---

## 5. 唯一入口 `panel_builder.py`

```python
read_panel(symbols, *, days=None, stocks_day=None,
           adjust=ADJUST_HFQ, runner=None) -> CrossSectionPanel
```

流程：取数 → 转换 → 切片 → 组装。
- `days` 不给时恰好 **3 次子进程**（`days` / `stocks` / `panel`）
- 取股票清单用 `read_stocks(day)["stocks"]`

---

## 6. 长宽转换 `panel_convert.py`

| 函数 | 作用 |
|---|---|
| `panel_from_provide(payload) -> dict[str, pd.DataFrame]` | `provide.cli panel` 返回 → `{字段: 宽表}`（缺保持 NaN，结构不符**报错**） |
| `panel_to_long(values) -> list[dict]` | 宽表 → `[{trade_date, symbol, value}]`（缺的格子不出现） |
| `long_to_panel(rows, *, dates=None, symbols=None) -> pd.DataFrame` | 长表 → 宽表（**重复键报错**） |

---

## 7. 约束（可验证标准）

| # | 约束 | 验收 |
|---|---|---|
| **M1-1** | 取数唯一入口 | `grep → 只命中 provide_reader.py` |
| **M1-2** | 票池由数据层给，本层**不实现过滤** | `grep → 无 "LIST" / ETF 名单 / 标的类型判定`；接口缺失 → **报错** |
| **M1-3** | 转换无损 + 缺就是缺 | 往返后 NaN 位置与数量**完全一致**；`grep panel/ → 无 ffill/bfill/fillna`；重复 `(date, symbol)` → 报错 |
| **M1-4** | 取数参数**必填无默认** | 日期范围 / 标的集合 / 数据项，缺一即报错 |
| **M1-5** | 批量取数可复现 | 连跑两次 → 第二次 **0 次**跨进程调用，且与第一次 `assert_frame_equal` 通过 |

---

## 8. 陷阱

| # | 陷阱 | 后果 |
|---|---|---|
| T1 | **`symbols(day)` 不能当票池** | 它返回 27 个里可能有 12 个是 `LIST*` 板块代码 → 把「板块」当「股票」排序，**且不报错**。**要用 `stocks(day)`** |
| T2 | `universe.txt` 混入 ETF / 指数 | 实测 328 只里有 `IWM QQQ SPX SPY`；58 只不在 `snapshot` 中，**全是 ETF** |
| T3 | 长宽转换**静默丢数据** | `pivot` 默认丢 NaN 组合 → 「某日某标的缺值」变成「该组合不存在」 |
| T4 | 面板口径与因子声明不符 | `run_factor` 会**报错**（故意的：防除权日假跳变） |

---

## 9. 测试

`tests/test_panel.py` —— 27 项（含跨层：`backtest/data_source.py` 的契约一致性）
