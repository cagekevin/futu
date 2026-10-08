"""R2 对照抽样 —— 同日 / 同池 / 同数量，抽 R 次，得到「瞎选」的下标。

## 本模块**只做一件事**：抽下标

它**不知道**哪一边是真实的、**不知道**收益怎么算 —— 那是 R3 的事（承 PRD §二 额外检查）。
产物是**下标矩阵** `(R, K)`（指向外部传入的票池），R3 用它去取收益、算均值。

这样切的好处：
- **R2 无 IO、无标签依赖**（承 N3：票池由外部给，本模块不自建）；
- **可向量化** —— 11 个交易日 × 1,000 次抽取若用 Python 循环，
  在 1,112 天上是百万级 `random.choice` 调用；用 `argpartition` 一次算完。

## 三件事同时锁死（承 N1）

| 锁什么 | 怎么做 |
|---|---|
| **同一天** | `day` 是必填参数，随机源**由 `(seed, day)` 派生** ⇒ 日间独立（承 N2）|
| **同一个池** | 只接受 `pool_size`，**不自己取票池**（承 N3）|
| **同样的数量** | `k` 由调用方（R1 的选中数）给定，**本模块不猜** |

## 为什么是「无放回」（承 N4）

有放回会**重复抽到同一只** ⇒ 零分布被"重复计数"扭曲，
而**它不会报错**，只让"瞎选"看起来更集中 —— 正是要防的静默失败。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = ["SamplingConfig", "derive_day_seed", "sample_indices"]


@dataclass(frozen=True)
class SamplingConfig:
    """抽样参数 —— **全必填、构造时校验**（承 C3：口径消耗必填无默认）。

    ⚠️ **配置与抽样不拆成两个文件**（照 `preprocess_pipeline.py` 的先例）：
       参数只有两个字段，单独成文件会**沦为薄壳** —— 承 01-PRD §五 M4。
    """

    #: 每天抽多少次（PRD §6.3 定死 1,000，与「1,000 只猴子」同量级）。
    iterations: int
    #: 基准种子 —— **必填无默认**（承 C3）；日间由它派生，见 `derive_day_seed`。
    seed: int

    def __post_init__(self) -> None:
        if self.iterations < 1:
            raise ValueError(f"抽样次数必须 ≥ 1，收到 {self.iterations}（承 C3）")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError(f"seed 必须是 int，收到 {type(self.seed).__name__}（承 C3）")

    @property
    def params(self) -> dict[str, Any]:
        """供报告做指纹（与 R1 的 `params` 同构）。"""
        return {"iterations": self.iterations, "seed": self.seed}


def derive_day_seed(seed: int, day: str) -> int:
    """`(基准种子, 交易日)` → **该日专属种子**（承 N2：日间必须独立）。

    为什么必须按日派生（而不是每天都用同一个种子）：
      若每天都用同一个随机数序列，各日的"瞎选"之间会产生**人为相关**
      （第 i 次抽的总是一样的那几只）⇒ 零分布失去意义。
    """
    payload = f"{seed}:{day}".encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:16], 16)


def sample_indices(
    pool_size: int,
    k: int,
    *,
    config: SamplingConfig,
    day: str,
) -> np.ndarray:
    """从 `[0, pool_size)` 里**无放回**抽 `k` 个下标，重复 `config.iterations` 次。

    返回：`(iterations, k)` 的 `int64` 数组，值域 `[0, pool_size)`。

    **同日 / 同池 / 同数量**由签名强制（承 N1）：
    - 同一天 → `day` 决定随机源；
    - 同一个池 → 只认 `pool_size`（票池由调用方给，本模块不自建，承 N3）；
    - 同样的数量 → `k` 由调用方给（= R1 的选中数）。

    ⚠️ **`k > pool_size` → 报错**（承 N4）：
       静默少抽会造出"假分布"，而且**不报错**。

    **实现为什么用 `argpartition`**：
       等价于「给每只抽一个 U(0,1)，取最小的 k 只」——
       每个 k-子集等概率（**均匀无放回**），且**一次算完**，无 Python 循环。
    """
    if pool_size < 1:
        raise ValueError(f"{day}: 票池为空（pool_size={pool_size}）（承 N3/N4）")
    if k < 1:
        raise ValueError(f"{day}: 选中数必须 ≥ 1，收到 {k}")
    if k > pool_size:
        raise ValueError(
            f"{day}: 票池只有 {pool_size} 只，不足 K={k} —— "
            f"承 N4：**不静默少抽、不重复抽**，宁可报错"
        )

    rng = np.random.default_rng(derive_day_seed(config.seed, day))
    rand = rng.random((config.iterations, pool_size))
    return np.argpartition(rand, k - 1, axis=1)[:, :k]
