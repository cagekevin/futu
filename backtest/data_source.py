"""唯一的数据入口（承 01 §0.3 / 02 §2）。

回测**只经这里**拿数据；不 import data-layer 内部，也不自己对齐。
用**跨进程 JSON**（provide.cli）。

为什么用跨进程（根因，**不是**因为"改名麻烦"）：
  1. 数据层自己推荐跨进程（Agent.md §1）；同进程要求"在 data-layer/ 内运行"，回测不满足；
  2. 回测只该依赖**契约**，不该依赖数据层的**模块命名空间** —— 它已占用
     config / engine / store / provide / fetch / data / pipeline / vendor / tests 等顶层名，
     且仍在演进；同进程会让回测的文件名被它的命名空间绑住；
  3. 代价几乎为零：回测一次加载全部标的（align_panel 一把拿全），spawn 一个 Python 可忽略。

★ 契约版本（诚实声明）：数据层 `provide/api.py` 有 `CONTRACT_VERSION = "1.0"`，
本模块**按 1.0 编写**。但跨进程**目前没有版本握手** —— `provide.cli` 没有 `version`
子命令，回测**拿不到**数据层的契约版本，也就**无法在运行时比对**。
这是一处**已知缺口**（B3 版本锁定未落地），不假装有机制：一旦数据层契约变更，
回测会以"字段缺失/结构不符"的形式报错（承 P2），而不是静默错算。

扩展缝：
  - **逐 bar 数据项**（K线，有 time_key）→ `align_panel`（本模块已接）。
  - **非 bar 级日频数据项**（net_gex / spot / rps …，每天一条）→ `align_daily_items`（**未接**）。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import backtest_config

# 本模块按数据层 `provide/api.py::CONTRACT_VERSION` 的此版本编写（声明，非机制）。
CONTRACT_VERSION = "1.0"

# K线 bar 的字段（与 data-layer/fetch 的 KLINE_FIELDS 一致，承 P2：不猜结构）。
# ★ 这是**唯一**的字段清单：别处（如 run_backtest）一律引用它，不许再抄一份。
KLINE_BAR_FIELDS = ("open", "high", "low", "close", "volume")

# 数据层对齐契约里的"交集"标记 —— 命名常量，避免魔法字符串散落多处（承 D1）。
ALIGNMENT_INTERSECTION = "intersection"

# ── 集成细节：数据层路径（属本模块，不属"交易假设"配置源）──────────────
BACKTEST_ROOT = Path(__file__).resolve().parent
DATA_LAYER = BACKTEST_ROOT.parent / "data-layer"
DATA_LAYER_PY = DATA_LAYER / ".venv" / "bin" / "python"


def _run_provide_cli(*args: str):
    """调 provide.cli → JSON。

    失败**必须报错**，不静默返回空（承 F4/P6：错误就是错误，不许降级）。
    """
    proc = subprocess.run(
        [str(DATA_LAYER_PY), "-m", "provide.cli", *args],
        cwd=str(DATA_LAYER),
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"provide.cli {' '.join(args)} 失败（exit={proc.returncode}）："
            f"{proc.stderr.strip()}"
        )
    return json.loads(proc.stdout)


def trading_days() -> list[str]:
    """库里有数据的交易日（升序）。"""
    return _run_provide_cli("days")


def align_panel(symbols: list[str], *, item: str = "kline",
                days: list[str] | None = None,
                min_bars: int = backtest_config.MIN_BARS) -> dict:
    """多品种**逐 bar**对齐面板（交集）。

    对齐由**数据层**保证（承 D1）—— 回测不重建、不自写对齐逻辑。

    返回（provide.cli align 的契约）：
        {
          "index":    [Unix 秒, ...],             # 公共时间戳（升序）
          "values":   {"SPY": [bar | None, ...]}, # 与 index 等长
          "strategy": "intersection" | "union_ffill",
          "n_symbols", "n_bars", "n_union", "n_dropped", "n_filled",
        }
    bar = {time_key, date, open, high, low, close, volume}
    """
    args = ["align", "--symbols", *symbols, "--item", item,
            "--min-bars", str(min_bars)]
    if days:
        args += ["--days", *days]
    return _run_provide_cli(*args)


def extract_field(panel: dict, field: str) -> dict[str, list[float]]:
    """从面板抽出某字段的逐 bar 值。

    ⚠️ **降级即拒用**：`strategy != "intersection"` 时，面板里有 ffill 出来的
    **捏造 bar**（它们**不是 None**，挡不住）→ **硬报错**，绝不静默用假数据（承 D1/P6）。
    缺 bar（None）同样报错，不补。
    """
    if panel.get("strategy") != ALIGNMENT_INTERSECTION:
        raise ValueError(
            f"对齐降级为 {panel.get('strategy')}（交集不足）—— 含捏造 bar，拒绝使用"
            f"（承 D1/P6：宁可报错，不喂假数据）"
        )
    out: dict[str, list[float]] = {}
    for symbol, column in panel["values"].items():
        values: list[float] = []
        for i, bar in enumerate(column):
            if bar is None:
                raise ValueError(
                    f"{symbol} 第 {i} 根 bar 为 None（对齐降级？）—— 承 P6 不补"
                )
            values.append(float(bar[field]))
        out[symbol] = values
    return out


def align_daily_items(symbols: list[str], *, item: str,
                      days: list[str] | None = None) -> dict:
    """【未接通】非 bar 级日频数据项（每天一条，如 net_gex / spot / rps）的对齐入口。

    ⚠️ 这里**不是**"先留个空位、等有需求再填" —— 是**契约对不上、现在填不了**：
      · 本函数**能**产出的是**按交易日**对齐的面板（`days` 为键，见下）；
      · 但消费端 `MarketFacts.extra` 要求与 `times`（**按 bar** 的 Unix 秒）等长。
      · "日 → bar"的映射**从未定义** ⇒ 有需求也接不通（不是需求的问题）。

    接通前必须先定死两件事（属**不可逆**的契约决策，承铁律 6）：
      1. 一根 bar 归属哪个交易日？（bar 自带 `date`，可直接用）；
      2. 某交易日**无值**时怎么办？—— 承 D3/P6：**只许 ffill、禁 bfill；缺就不补**，
         所以默认应是"缺 → 该 bar 无值"，而非"沿用上一日"。
    定完再实现本函数，并把结果按上述规则铺到 bar 轴上喂 `extra`。

    本函数**将来**的返回契约（按交易日，与 `align_panel` 同构）：
        {"days": ["2026-09-30", ...], "values": {"SPY": [value | None, ...]},
         "strategy": "intersection"}
    实现路径：`provide.cli timeseries --symbol <S> --item <I>` 逐标的取 → 按交易日取交集。
    这些量是**客观派生**（关于市场）→ 归数据层；回测只消费。
    """
    raise NotImplementedError(
        "非 bar 级日频数据项对齐尚未接入（承 D1：对齐由数据层保证，回测不重建）。"
        "接入时按上契约实现：逐标的取 timeseries，按交易日取交集。"
    )
