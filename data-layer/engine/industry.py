"""M3.2 指标 —— 行业集体行为 + 行业状态机。**纯函数，零 IO**（承 G1）。

RPS **不在这里**（在 `engine/rps.py`，那边是权威口径）。这里只按行业聚合。

两个轴别混：
- **方向轴** → `up_ratio`（上涨占比，自带方向）
- **幅度轴** → `std`（涨跌幅标准差，涨得均不均匀）

⚠️ `std` 大 ≠ 不集体；`agreement` 不含方向，判状态**一律用 `up_ratio`**。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, replace

from . import rps as _rps

__all__ = [
    "IndustryStat", "classify", "industry_stats",
    "STATE_FLAT", "STATE_NOTE", "STATE_ORDER", "STATE_THRESHOLD",
]

STATE_THRESHOLD = 0.6
STATE_FLAT = 0.05

STATE_ORDER = (
    "持续涨", "回调", "回调转强", "转弱",
    "启动·横盘", "启动·跌后",
    "横盘",
    "走弱·横盘", "走弱·跌后",
    "持续跌",
)

STATE_NOTE = {
    "持续涨": "全周期都强 —— 长期健康、中期也强、现在还在动",
    "回调": "长期强、中期强，只有最近在歇 —— 强势回踩，看能不能收回",
    "回调转强": "长期强，中期歇过一段，最近又起来了 —— 强势股回踩后反弹",
    "转弱": "长期还健康，但中期已经弱了、现在也在跌 —— 在走坏",
    "启动·横盘": "长期横盘（没跌过）、中期转强、现在还在动 —— 蓄势突破，最该重视",
    "启动·跌后": "长期跌过、中期转强、现在还在动 —— 跌后反弹，可能是死猫跳",
    "横盘": "各周期中位都接近 0 —— 没方向，等（启动前的状态）",
    "走弱·横盘": "长期横盘、中期转过强、现在又歇了 —— 蓄势中断，再动就是启动",
    "走弱·跌后": "长期跌过、中期反弹过、现在又跌了 —— 时机难把握，次优",
    "持续跌": "长期和中期都不健康 —— 不看",
}


@dataclass(frozen=True)
class IndustryStat:
    """一个行业的集体行为画像。"""

    industry: str
    count: int
    median: float
    mean: float
    std: float
    up_ratio: float
    rps: float

    @property
    def direction(self) -> str:
        return "涨" if self.median >= 0 else "跌"

    @property
    def agreement(self) -> float:
        """同向占比 0~1（**不含方向**；判状态请用 `up_ratio`）。"""
        return self.up_ratio if self.median >= 0 else 1.0 - self.up_ratio


def classify(by_days, *, thr=STATE_THRESHOLD, flat=STATE_FLAT) -> str:
    """给一个行业的多周期统计判状态。

    `by_days`：`{周期: {"median":…, "up_ratio":…}}`（周期键 int 或 str 都认）。
    「状态」必须**跨周期**看：250 长期 / 120 中期 / 20 现在。
    """
    def cell(n):
        got = by_days or {}
        return got.get(n) or got.get(str(n)) or {}

    def m(n):
        return cell(n).get("median")

    def u(n):
        return cell(n).get("up_ratio")

    m20, m120, m250 = m(20), m(120), m(250)
    u20, u120, u250 = u(20), u(120), u(250)
    if None in (m20, m120, m250, u20, u120, u250):
        return "数据不全"

    if abs(m20) < flat and abs(m120) < flat and abs(m250) < flat:
        return "横盘"
    if u250 < thr and u120 < thr:
        return "持续跌"
    if u250 >= thr:
        if u120 >= thr:
            return "持续涨" if u20 >= thr else "回调"
        return "回调转强" if u20 >= thr else "转弱"
    side = "横盘" if m250 > 0 else "跌后"
    return f"启动·{side}" if u20 >= thr else f"走弱·{side}"


def industry_stats(rows, key: str = "chg250", min_count: int = 5) -> list[IndustryStat]:
    """按行业分组算集体行为。`rows` 每项含 `industry` 和 `key` 字段。

    `min_count`：成分股太少的行业统计没意义，默认剔掉 < 5 只的。
    离散度用**总体标准差**（拿到的是全部成分股，不是抽样）。
    """
    groups: dict[str, list[float]] = {}
    for row in rows:
        industry = (row.get("industry") or "").strip()
        value = row.get(key)
        if not industry or value is None:
            continue
        groups.setdefault(industry, []).append(float(value))

    stats: list[IndustryStat] = []
    for industry, values in groups.items():
        n = len(values)
        if n < min_count:
            continue
        stats.append(IndustryStat(
            industry=industry,
            count=n,
            median=statistics.median(values),
            mean=statistics.fmean(values),
            std=statistics.pstdev(values) if n > 1 else 0.0,
            up_ratio=sum(1 for v in values if v > 0) / n,
            rps=0.0,
        ))

    ranks = _rps.rps({s.industry: s.median for s in stats})
    return [replace(s, rps=ranks.get(s.industry, 0.0)) for s in stats]
