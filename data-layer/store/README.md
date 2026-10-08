# store —— 库（按 `(交易日, 标的, 数据项)` 组织）

> **零依赖层**：只依赖标准库，**不许 import 其它 data-layer 子模块**。
> 落盘形态：`data/<交易日>/<标的>.<数据项>.json`（一天一个目录）。

---

## 1. 职责

| 干 | 不干 |
|---|---|
| 定义**键**与**受控数据项清单** | 不取数（那是 `fetch/`） |
| 原子写、读、列举 | 不算指标（那是 `engine/`） |
| 缺就是缺（**绝不 ffill / bfill / 补造**） | 不做选择/过滤/语义判断 |

**核心判据：停牌的职责只有一句 —— 不造假。**

---

## 2. 模块与接口

### `keys.py` —— 键与受控清单

```python
UNIVERSE_SYMBOL = "UNIVERSE"        # 保留约定代码（"整市场一份"的数据）
PLATE_CODE_PREFIX = "LIST"          # 板块代码前缀
GLOBAL_PREFIX = "_"                 # 全局项文件名前缀
KNOWN_ITEMS: frozenset              # 受控数据项清单（未注册 → 报错）
GLOBAL_ITEMS = frozenset({"calendar", "plate_list", "industry_state"})
```

| 函数 | 作用 |
|---|---|
| `normalize_day(day) -> str` | 日期规整 |
| `normalize_symbol(symbol) -> str \| None` | 标的规整（`None` = 全局项） |
| `normalize_item(item) -> str` | 数据项规整；**未注册 → `UnregisteredItem`** |
| `is_global_item(item) -> bool` | 是否全局项 |

异常：`StoreError`、`UnregisteredItem(StoreError)`

`Key`（`@dataclass(frozen=True, slots=True)`）：`day, symbol, item`

- `Key.make(day, symbol, item) -> Key` —— **配对约束**：全局项必须 `symbol=None`，
  非全局必须非 `None`；错配 **当场报错**
- `is_global` property、`__str__`

### `storage.py` —— 物理读写

| 函数 | 作用 |
|---|---|
| `path_for(root, key) -> Path` | 键 → 路径 |
| `write_json_atomic(path, payload)` | **原子写**（临时文件 + `os.replace`） |
| `read_json(path)` | 读；不存在 → `None` |
| `list_days(root)` / `list_symbols(root, day)` / `list_items(root, day, symbol)` / `list_globals(root, day)` | 列举 |

### `read.py` —— 读取

```python
class Missing(KeyError)        # 缺值哨兵
```

| 函数 | 作用 |
|---|---|
| `exists(...)` / `has(...)` | 存在性 |
| `get(root, day, symbol, item, *, default=Missing)` | 单点取值；缺且未给 default → `Missing` |
| `series(root, symbol, item, *, days=None)` | 跨日序列 `[(day, value), …]` |
| `latest_day(root, symbol, item) -> str \| None` | 最新有值日 |
| `days / symbols / items / global_items` | 列举 |

### `write.py` —— 写入

| 函数 | 作用 |
|---|---|
| `put(root, day, symbol, item, payload) -> Path` | 单条写（原子） |
| `put_many(root, day, records) -> list[Path]` | **批原子**：全成 / 全不写 |

### `axis.py` —— 统一门面（默认 `DATA_DIR`）

模块级函数 `days / symbols / items / global_items / get / series / latest_day / put / put_many / exists`
（均带可选 `root`），以及等价类 `Axis(root=None)`。

`__all__` 重导出：`StoreError` / `UnregisteredItem` / `KNOWN_ITEMS` / `GLOBAL_ITEMS` / `Key` / `Missing`

---

## 3. 陷阱

| # | 陷阱 | 防护 |
|---|---|---|
| T1 | 直接开文件写 → 中断留半截文件 | **必须走 `put` / `put_many`**（原子写，承 X2） |
| T2 | 新增一个无标的数据项却没登记 | `KNOWN_ITEMS` 受控，**未注册即报错** |
| T3 | 全局项带了标的 / 非全局项没带 | `Key.make` 的配对约束拦下 |
| T4 | 用"文件在不在"当"数据对不对" | 承 P4：**校验"真取到"** |

---

## 4. 测试

`tests/test_store.py` —— M1 可验证标准 + 全局约束（读 / 写 / 原子 / 键 / 时间轴）
