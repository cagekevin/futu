"""统计工具 —— **纯函数、无 IO**。

## 为什么需要它

项目里到处在报**裸数字**：「胜率 36.8%」「+1.28%」「62.4% 上涨」。

**裸数字没有样本量信息。** 36.8% 是 4,076 笔里算的，还是 25 笔里算的？差一个量级 ——
但两个都写成 `36.8%`，读的人分不出来。

**Wilson 区间解决这个**：它给出「同样条件下重来一次，这个比例会落在哪」。
**小样本时区间会很宽 —— 那正是重点**，它让你看见「这个数不可信」。

## 三个函数

  `wilson(k, n)`           比例的 Wilson 得分区间（小样本也稳，比正态近似好）
  `two_prop_ztest(...)`    两组比例差异的显著性（模型 vs 对照）
  `brier_skill(conf, y)`   概率预测的技能分（相对「只报基准率」提升多少）

**都不依赖 scipy** —— 只用 `math`，因为项目主环境不保证有 scipy。

用法：
    from engine.metrics import wilson, two_prop_ztest, brier_skill
    p, lo, hi = wilson(1500, 4076)          # (0.368, 0.353, 0.383)
    z, pv = two_prop_ztest(1500, 4076, 162000, 419993)
"""

from __future__ import annotations

import math

__all__ = ["wilson", "two_prop_ztest", "brier_skill", "fmt_pct", "fmt_rate"]

NAN = float("nan")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson 得分区间 —— 返回 `(点估计, 下界, 上界)`。`n <= 0` 时全 `nan`。

    **为什么不用 `p ± 1.96·√(p(1-p)/n)`**：那个在 p 接近 0 或 1 时会给出
    超出 [0,1] 的区间，小样本时也不准。Wilson 把区间**约束在 [0,1] 内**。

    `z=1.96` = 95% 置信。
    """
    if n <= 0:
        return (NAN, NAN, NAN)
    p = k / n
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def two_prop_ztest(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    """两独立比例 z 检验 —— 返回 `(z, 双尾 p)`。

    用途：**「模型组 vs 对照组」的差异是不是噪声**。
    样本为 0 或方差为 0 时返回 `(nan, nan)`（不硬算）。

    ⚠️ **它假设两组独立** —— 同一天出现的信号**不独立**（同日聚集），
    这时 p 值会偏小（假显著）。**同日样本要先按日聚合再喂进来。**
    """
    if n1 <= 0 or n2 <= 0:
        return (NAN, NAN)
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se < 1e-15:
        return (NAN, NAN)
    z = (p1 - p2) / se
    return (z, math.erfc(abs(z) / math.sqrt(2.0)))     # 双尾


def brier_skill(conf: list[float], y: list[int]) -> float:
    """Brier 技能分 = `1 − Brier / Brier_基准`。

    · `conf` 预测概率（0–1），`y` 实际结果（0/1）
    · **基准 = 只报样本基准率**（不用任何特征）
    · 返回值：**>0 说明预测比「只报基准率」好**；=0 说明一样；<0 说明更差

    **为什么用技能分而不是 Brier 本身**：Brier 的绝对值取决于基准率
    （基准率 0.5 时本来就难），跨数据集不可比。技能分是**相对提升**，可比。
    """
    n = len(y)
    if n == 0 or len(conf) != n:
        return NAN
    base = sum(y) / n
    bs = sum((c - t) ** 2 for c, t in zip(conf, y)) / n
    ref = sum((base - t) ** 2 for t in y) / n
    if ref <= 1e-15:
        return NAN
    return 1.0 - bs / ref


def fmt_pct(v: float | None, nd: int = 2) -> str:
    """`None`/`nan` → `—`；否则 `+x.xx%`。"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:+.{nd}f}%"


def fmt_rate(k: int, n: int, nd: int = 1) -> str:
    """把比例写成 **`36.8% [35.3, 38.3]`** —— 带 Wilson 区间。

    **项目里所有胜率都该走这个** —— 裸的 `36.8%` 看不出样本量。
    """
    if n <= 0:
        return "—"
    p, lo, hi = wilson(k, n)
    return f"{p * 100:.{nd}f}% [{lo * 100:.{nd}f}, {hi * 100:.{nd}f}]"
