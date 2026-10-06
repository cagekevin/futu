"""多尺度一致性 —— **只给事实，不打分、不排序、不筛选**。

## 为什么需要它

RPS 有四个周期（20 / 50 / 120 / 250），但**排序只按主周期**（250）。
主周期一个数看不出「四个周期在不在同一件事上」。

实测（`data/scan/2026-10-04/picks.json`）：

| 集合 | 跨度中位 | ≥20 | ≥30 | ≥40 | ≥50 |
|---|---|---|---|---|---|
| `signals`（25 只有完整 RPS） | **39.0** | 80% | **76%** | 48% | 32% |
| `strong`（590） | 30.9 | 68% | 52% | 42% | 33% |

最极端的：`DMRA` 20=1.2 / 250=99.3（跨度 **98.1**）；
`ICHR` 20=95.9 / 50=1.5 / 250=97.8（**96.3**）。

**这些是完全不同的东西，但只按 `rps_main` 排，它们会挨在一起。**

## 它做什么、不做什么

**做**：给两个**纯事实** ——

  · `spread` **跨度** = 四个周期 RPS 的最高分 − 最低分（一个数）
  · `shape`  形状 = 短周期相对长周期落在哪一边（一个标签）

> **为什么叫「跨度」不叫「极差」**：`max − min` 在统计学里的正式名字是「极差」，
> 但**中文里「极差」会被读成「非常差」**，正好和它想表达的意思相反。
> 「跨度」字面就是「最高到最低跨了多远」，没有好坏含义。

**不做**（照 `pick.py` 第 12–14 行的约定）：

  · **不打分** —— 不把四周期压成一个分数
  · **不排序** —— 排序仍然只用主周期
  · **不筛选** —— 不因为冲突就丢票（`shape` 不是「好/坏」）

**`shape` 是描述，不是判断。** 「短低长高」既不比「短高长低」好，也不比它差 ——
它只是告诉你**这两只票不是一回事**。好坏由读的人定。

## 阈值

`SPREAD_SAME = 20`、`SHAPE_DIFF = 15` —— **都是约定值**（RPS 是 0–100 的百分位，
20 点 ≈ 两个十分位），**不是从数据算出来的**。改它们只改这一处。
"""

from __future__ import annotations

__all__ = ["PERIODS", "SPREAD_SAME", "SHAPE_DIFF", "describe", "spread", "shape"]

PERIODS = ("20", "50", "120", "250")

SPREAD_SAME = 20.0   # 跨度小于它 → 视为「一致」
SHAPE_DIFF = 15.0    # 短周期均值 − 长周期均值 超过它 → 才算有方向

# 形状标签 —— **纯描述**，不带好/坏
SAME = "一致"        # 四个周期都差不多
SHORT_LOW = "短低长高"   # 20/50 低于 120/250 → 长期强、最近歇着
SHORT_HIGH = "短高长低"  # 20/50 高于 120/250 → 最近强、长期不强
MIXED = "交错"       # 跨度大，但短≈长（例如 20 高、50 低、120 低、250 高）


def _vals(rps: dict | None) -> list[float]:
    """取四个周期的值，**缺的跳过**（缺数据 ≠ 0）。"""
    if not rps:
        return []
    out = []
    for k in PERIODS:
        v = rps.get(k)
        if v is not None:
            out.append(float(v))
    return out


def spread(rps: dict | None) -> float | None:
    """四周期跨度 `max − min`。不足两个周期 → `None`。"""
    v = _vals(rps)
    return (max(v) - min(v)) if len(v) >= 2 else None


def shape(rps: dict | None) -> str:
    """短周期（20/50）相对长周期（120/250）落在哪一边。**描述，不是判断。**

    返回 `一致` / `短低长高` / `短高长低` / `交错` / `—`（数据不足）。
    """
    v = _vals(rps)
    if len(v) < 4:
        return "—"
    s = spread(rps) or 0.0
    if s < SPREAD_SAME:
        return SAME
    short = (float(rps["20"]) + float(rps["50"])) / 2
    long_ = (float(rps["120"]) + float(rps["250"])) / 2
    d = short - long_
    if d > SHAPE_DIFF:
        return SHORT_HIGH
    if d < -SHAPE_DIFF:
        return SHORT_LOW
    return MIXED


def describe(rps: dict | None) -> dict:
    """一次给全：`{"spread": float|None, "shape": str}`。"""
    return {"spread": spread(rps), "shape": shape(rps)}
