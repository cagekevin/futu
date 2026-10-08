"""M2 暴露 —— 组装（M2 对外的**唯一入口函数**）。

依赖方向（**单向**，承 State 2.4）：
```
exposure_types  ←  {size_exposure, industry_exposure, exposure_coverage}  ←  exposure_builder
panel_types     ←  exposure_types（只取常量）
provide_reader  ←  exposure_builder（取 snapshot / raw 面板）
```
"""
from __future__ import annotations

from typing import Any, Mapping

from exposure.exposure_coverage import coverage_report
from exposure.exposure_types import (
    MIN_INDUSTRY_COUNT_RECOMMENDED, ExposureSet,
)
from exposure.industry_exposure import build_industry_exposure, industry_labels
from exposure.size_exposure import build_size_exposure, implied_shares
from panel.panel_convert import panel_from_provide
from panel.panel_types import CrossSectionPanel
from panel.provide_reader import Runner, read_kline_panel, read_snapshot

__all__ = ["read_exposures", "snapshot_rows"]


def snapshot_rows(record: Mapping[str, Any]) -> list[dict]:
    """`provide.cli get` 的外层记录 → 快照行列表。结构不符 → **报错**（承 P2）。"""
    if not isinstance(record, Mapping) or "value" not in record:
        raise ValueError(
            f"snapshot 契约不符（无 value）：{type(record).__name__}（承 P2：不猜结构）"
        )
    value = record["value"]
    rows = value.get("rows") if isinstance(value, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("snapshot 契约不符（value 里无 rows 列表）（承 P2）")
    return rows


def read_exposures(panel: CrossSectionPanel, *,
                   min_industry_count: int = MIN_INDUSTRY_COUNT_RECOMMENDED,
                   runner: Runner | None = None) -> ExposureSet:
    """读一次暴露集 —— M2 对外**唯一入口**。

    **`panel`**：M1 的 `CrossSectionPanel`。本函数从它取交易日轴、标的、每日票池，
    以及 **`snapshot_day`**（数据层判定票池时用的那个快照日 —— 同一个，不重复找）。

    ⚠️ **会再取一次 raw 面板**：市值反推必须用 **raw** close（见 `size_exposure`
    的说明 —— hfq 比值多一个因股而异的累积复权因子，会把 size 的横截面排序拧歪）。
    这是一次**额外取数**，不是冗余。

    ⚠️ `coverage["absorbed"] != 0` → **报错**（承 U3：被完全吸收的标的数必须为 0）。
    """
    dates = tuple(str(d) for d in panel.dates)
    symbols = sorted(str(s) for s in panel.symbols)
    if not dates or not symbols:
        raise ValueError("面板为空 —— 没有可构造暴露的交易日/标的（承 P1）")

    # ── ① raw 面板（只为了 close）────────────────────────────────────────
    raw_payload = read_kline_panel(symbols, days=list(dates), adjust=None,
                                   runner=runner)
    raw_close = panel_from_provide(raw_payload)["close"]

    # ── ② 快照（市值 + 行业）─────────────────────────────────────────────
    snapshot_day = str(panel.snapshot_day or "")
    if not snapshot_day:
        raise ValueError(
            "面板没有 `snapshot_day` —— 无法定位快照日（承 P1：缺就报，不猜）"
        )
    rows = snapshot_rows(read_snapshot(snapshot_day, runner=runner))

    # ── ③ 两个暴露 ───────────────────────────────────────────────────────
    size = build_size_exposure(raw_close, implied_shares(rows))
    industry = build_industry_exposure(
        industry_labels(rows), dates, symbols,
        present_by_day=panel.universe_by_day,
        min_industry_count=min_industry_count,
    )

    # ── ④ 覆盖率显形 + U3 硬校验 ─────────────────────────────────────────
    coverage = coverage_report(size, industry,
                               min_industry_count=min_industry_count)
    coverage["snapshot_day"] = snapshot_day
    if coverage["absorbed"] != 0:
        raise ValueError(
            f"有 {coverage['absorbed']} 个行业哑变量只覆盖 1 只标的 —— 它们会被"
            f"**完全吸收**（残差恒为 0，标的静默消失）。阈值 "
            f"{min_industry_count} 没挡住（承 U3）"
        )

    # ── ⑤ 近似标记（**逐日**，承 U4）────────────────────────────────────
    # 快照日**当天**是观测值（False）；其余天是反推 / 假设不变的（True）。
    size_approx = {day: day != snapshot_day for day in dates}
    industry_approx = {day: day != snapshot_day for day in dates}

    return ExposureSet(
        dates=dates,
        symbols=tuple(symbols),
        size=size,
        industry=industry,
        min_industry_count=int(min_industry_count),
        coverage=coverage,
        size_is_approximated=size_approx,
        industry_is_approximated=industry_approx,
    )
