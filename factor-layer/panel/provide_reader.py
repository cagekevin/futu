"""M1 面板 —— **唯一取数入口**（承 PRD M1-1 / §7.1）。

本层**只经这里**拿数据；不 import `data-layer` 内部，也不自己解析存储。
用**跨进程 JSON**（`provide.cli`）—— 照抄 `backtest/data_source.py` 的模式。

## 为什么跨进程（根因，与 backtest 同一判据）

1. 数据层自己推荐跨进程（`Agent.md` §1）；同进程要求"在 `data-layer/` 内运行"；
2. 本层只该依赖**契约**，不该依赖数据层的**模块命名空间** ——
   它已占用 `config` / `engine` / `store` / `provide` / `fetch` / `data` /
   `pipeline` / `vendor` / `tests` 等顶层名，且仍在演进；
3. 代价几乎为零：一次取数 **3 个子进程**（`days` / `stocks` / `panel`），
   spawn 一个 Python 可忽略。

## ⚠️ 与 `backtest/data_source.py` 的关系：同构，但**不复用**

承 PRD §7.2（并列设计）：本层与 `backtest` **互不 import** ——
复用会形成**反向依赖**（下游的代码被上游 import）。
代价是重复约 15 行 subprocess 包装，**这是并列设计的已知成本**，不是疏忽。

## ★ 契约版本（诚实声明）

数据层 `provide/api.py` 有 `CONTRACT_VERSION = "1.0"`，本模块按 1.0 编写。
但跨进程**没有版本握手**（`provide.cli` 无 `version` 子命令）——
这是一处**已知缺口**（承 `backtest/data_source.py` 已声明的同一缺口），
不假装有机制：契约一旦变更，本层会以"字段缺失 / 结构不符"的形式**报错**（承 P2），
而不是静默错算。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from panel.panel_types import (
    ADJUST_HFQ, KLINE_ITEM, SNAPSHOT_ITEM, SNAPSHOT_UNIVERSE,
)

__all__ = [
    "CONTRACT_VERSION", "Runner",
    "read_days", "read_stocks", "read_kline_panel", "read_snapshot",
]

#: 本模块按数据层 `provide/api.py::CONTRACT_VERSION` 的此版本编写（声明，非机制）。
CONTRACT_VERSION = "1.0"

#: 取数器签名 —— 测试可注入替身（避免依赖真库 / 真子进程）。
Runner = Callable[..., Any]

# ── 集成细节：数据层路径（属本模块，不属"因子定义"）────────────────────────
FACTOR_LAYER_ROOT = Path(__file__).resolve().parent.parent
DATA_LAYER = FACTOR_LAYER_ROOT.parent / "data-layer"
DATA_LAYER_PY = DATA_LAYER / ".venv" / "bin" / "python"


def _run_provide_cli(*args: str) -> Any:
    """调 `provide.cli` → JSON（**默认实现**：真子进程）。

    失败**必须报错**，不静默返回空（承 F4/P6：错误就是错误，不许降级）。
    """
    proc = subprocess.run(
        [str(DATA_LAYER_PY), "-m", "provide.cli", *args],
        cwd=str(DATA_LAYER), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"provide.cli {' '.join(args)} 失败（exit={proc.returncode}）："
            f"{proc.stderr.strip()}"
        )
    return json.loads(proc.stdout)


def _call(args: tuple[str, ...], runner: Runner | None) -> Any:
    """走注入的 runner 或默认子进程 —— **唯一**的分发点。"""
    return (runner or _run_provide_cli)(*args)


def read_days(*, runner: Runner | None = None) -> list[str]:
    """库里有数据的交易日（升序）。"""
    return _call(("days",), runner)


def read_stocks(day: str, *, runner: Runner | None = None) -> dict:
    """该日的**股票票池**（`provide.cli stocks`）—— ★ 过滤由**数据层**做，本层不实现。

    返回**原样透传**（不加工）：
        {day, snapshot_day, snapshot_is_after_day, stocks,
         n_excluded_plate, n_excluded_reserved, n_excluded_non_stock}
    """
    return _call(("stocks", "--day", day), runner)


def read_kline_panel(symbols: list[str], *, days: list[str] | None = None,
                     adjust: str | None = ADJUST_HFQ,
                     runner: Runner | None = None) -> dict:
    """多标的 × 多日 × K线 → **截面面板**（`provide.cli panel`）。

    `adjust` **必须显式**（承 M3-A3 + 数据层 spec §4：不设默认口径）：
      · `"hfq"`（默认，本层的研究口径，严格因果）
      · `None`  → **原样 raw**（不传 `--adjust`）
      · `"qfq"` → 含未来除权信息，**只可用于展示**

    返回**原样透传**（不加工）：
        {item, days, symbols, adjust, contaminated_days, n_adjust_events, values}
        `values[symbol][day]` = bar（已展开；见数据层 `panel` 契约）
    """
    if adjust is not None and not isinstance(adjust, str):
        raise ValueError(f"adjust 必须是 str 或 None，收到 {adjust!r}（承 P1）")
    args = ["panel", "--symbols", *symbols, "--item", KLINE_ITEM]
    if days:
        args += ["--days", *days]
    if adjust is not None:
        args += ["--adjust", adjust]
    return _call(tuple(args), runner)


def read_snapshot(day: str, *, runner: Runner | None = None) -> dict:
    """全市场快照（`provide.cli get`）—— M2 暴露度的原料。

    返回 `provide.cli get` 的**外层记录**（原样透传）：
        {day, symbol: "UNIVERSE", item: "snapshot",
         value: {market, rows: [{symbol, name, industry, price, market_cap, …}], fetched_at}}

    ⚠️ 快照是**一个时点**的事实（实测库里只有极少数几天有）——
    "拿它去近似历史"的偏差由 **M2 显形**（承 U4），不在本函数里掩饰。
    """
    return _call(("get", "--day", str(day), "--symbol", SNAPSHOT_UNIVERSE,
                  "--item", SNAPSHOT_ITEM), runner)
