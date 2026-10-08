"""M1 面板 —— 组装（M1 对外的**唯一入口函数**）。

## ⚠️ 对 State 1 的偏离声明

（承写码4步法「退回机制」：**不硬填，也不偷偷加**）

State 1 定的子模块是 **4 个**。填充时发现「组装」这一步
（取数 → 转换 → 切片 → 装成 `CrossSectionPanel`）**需要一个落点**：

| 候选落点 | 为什么不行 |
|---|---|
| `panel_types.py` | 它必须**零依赖**（承 State 2.4 写死的依赖方向）|
| `provide_reader.py` | 会让它**反向依赖** `panel_convert` / `stock_universe` —— 违反 State 2.4 |
| `__init__.py` | 逻辑不该藏在包的初始化里（不可发现、难测试）|

⇒ **新增第 5 个文件** `panel_builder.py`。它是**唯一**同时 import 那四个模块的地方，
依赖方向仍然单向（见下），没有破坏 State 2 的任何一条。

```
panel_types  ←  panel_convert   ←┐
panel_types  ←  provide_reader  ←┤── panel_builder
                stock_universe  ←┘
```
"""
from __future__ import annotations

from panel.panel_convert import panel_from_provide
from panel.panel_types import ADJUST_HFQ, CrossSectionPanel
from panel.provide_reader import Runner, read_days, read_kline_panel, read_stocks
from panel.stock_universe import stocks_from_payload, universe_by_day

__all__ = ["read_panel"]


def read_panel(symbols, *, days: list[str] | None = None,
               stocks_day: str | None = None,
               adjust: str | None = ADJUST_HFQ,
               runner: Runner | None = None) -> CrossSectionPanel:
    """读一次截面面板 —— M1 对外的**唯一入口**。

    步骤（`days` 不给时**恰好 3 次**子进程调用）：

    1. `days`                    → 交易日轴（升序）
    2. `stocks(stocks_day)`      → **股票集合**（★「哪些是股票」的**唯一**判定来源）
    3. `panel(symbols, days, …)` → 多标的 × 多日 × K线（展开后的 bar）

    **`symbols`**：要取的标的 —— **由调用方给**（"取哪些标的"是研究决策，
    不是数据语义）。取股票清单用 `read_stocks(day)["stocks"]`。

    **`stocks_day`**：用哪一天的 `provide.cli stocks` 判定「哪些是股票」。
      · 不给 → 用交易日轴的**最后一天**；
      · 那天的股票集合为空（如库里最新一天只跑了指数/板块）→ **报错**，
        并提示显式传 `stocks_day`（承 P1：缺就报，不猜）。

    **`adjust`**：**必须显式**（承 M3-A3 / 数据层 spec §4）——
    `"hfq"`（本层默认口径）/ `None`（原样 raw）/ `"qfq"`（含未来，只可展示）。
    """
    axis = [str(d) for d in days] if days else [str(d) for d in read_days(runner=runner)]
    if not axis:
        raise ValueError("库里没有任何交易日 —— 先跑 data-layer 的 pipeline（承 P1）")

    day_for_stocks = str(stocks_day) if stocks_day else axis[-1]
    stocks_payload = read_stocks(day_for_stocks, runner=runner)
    stocks = stocks_from_payload(stocks_payload)
    if not stocks:
        raise ValueError(
            f"{day_for_stocks} 的股票票池为空（那天库里可能只有指数/板块）—— "
            f"请显式传 `stocks_day=` 指定一个库里有股票记录的交易日"
            f"（承 P1：缺就报，不猜）"
        )

    columns = sorted(str(s) for s in symbols)
    payload = read_kline_panel(columns, days=axis, adjust=adjust, runner=runner)

    # 回显校验：数据层返回的标的必须与请求的一致 ——
    # 不一致说明契约对不上，**报错**（承 P2：不猜结构），不静默用其中一边。
    echoed = tuple(sorted(str(s) for s in (payload.get("symbols") or ())))
    if echoed and echoed != tuple(columns):
        raise ValueError(
            f"面板回显的标的与请求的不一致：请求 {columns}，回显 {list(echoed)}"
            f"（承 P2：不猜结构）"
        )

    fields = panel_from_provide(payload)
    panel_days = tuple(sorted(str(d) for d in (payload.get("days") or ())))
    panel_symbols = tuple(columns)
    universe = universe_by_day(panel_days, panel_symbols, fields, stocks)

    return CrossSectionPanel(
        dates=panel_days,
        symbols=panel_symbols,
        fields=fields,
        universe_by_day=universe,
        # ── 显形字段：**原样透传**，不许在本层吞掉（承 P5 / State 2.5）──────
        n_adjust_events={str(k): int(v)
                         for k, v in (payload.get("n_adjust_events") or {}).items()},
        contaminated_days={
            str(k): tuple(str(d) for d in (v or ()))
            for k, v in (payload.get("contaminated_days") or {}).items()
        },
        # `payload["adjust"]` 就是本次取数用的口径（`None` = 原样 raw）——
        # 透传它，`FactorSpec.adjust` 才有东西可校验（承 A3）。
        adjust=payload.get("adjust"),
        snapshot_day=str(stocks_payload.get("snapshot_day") or ""),
        snapshot_is_after_day=bool(stocks_payload.get("snapshot_is_after_day")),
    )
