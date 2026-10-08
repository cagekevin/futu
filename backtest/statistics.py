"""回测层统计判据工具（承 V11 / V12）。

为什么有这个模块
----------------
本仓是**多品种面板**：同一天所有品种共享同一次市场冲击，所以观测**不独立**。
而 Wilson 区间由二项分布推出，**隐含「观测独立」** ⇒ 用在面板上区间**系统性偏窄**，
会放行假阳性。`design 01` §4.11 的实测反例：

    同一批信号（胜率 60%）：

    天真 n=200 观测 → Wilson [53.1%, 66.5%]  → ✅ 显著优于 50%
    真实只有 5 个交易日 →      [23.1%, 88.2%]  → ❌ 什么都不显著

    区间宽度差 5 倍，判决完全相反。

⇒ **区间宽度靠 `1/√n` 缩小，但只有独立观测才能贡献这个缩小。**
把同一个信息重复 200 遍，不会让你多知道 200 倍。

五条硬规矩（结构上强制，不靠自觉）
----------------------------------
1. **比例的区间一律簇级** —— `clustered_interval` 的入参**必须是簇**，
   没有"只传一个 n"的入口。
2. **报比例必须同时报观测数与簇数** —— 走 `format_rate_with_clusters`，
   **没有只报裸比例的出口**（V12 的验收判据）。
3. `wilson_interval` 硬校验 `n_clusters == 1` —— 面板上调用**必然报错**，
   只能改用簇级口径。
4. **报 p 值一律经多重检验校正** —— 走 `benjamini_hochberg`（BH），
   **没有"拿单个 p 值直接下结论"的出口**（V18 / TD-05-2 的验收判据）。
   根因：策略注册表 + 阈值网格 ⇒ 必然试 N 次；1 − 0.95¹⁸ ≈ **60%**
   —— **只报单个 p<0.05 而不报分母，等于没做统计。**
5. **事件级统计前先去重** —— 走 `dedupe_overlapping_events`（`gap >= 持有期`），
   否则持有期内的重叠收益被重复计入（TD-05-4）。

簇的定义
--------
默认簇 = **交易日**（同日共享冲击是最强的一阶相关；相邻日的惯性由重采样整簇覆盖）。
标的维度的相关性交给 **V5 单品种拆解**，不混进V11 的分层（design 01 §4.10b）。

边界
----
纯标准库 · 纯函数 · 零IO（承 `design 02` §0.5）。
入参是**已算好的数字**，本模块不取数、不落盘、不打印。
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence

# `wilson_interval` 唯一合法的 scope —— 显式声明"这是簇内单序列"。
SINGLE_SERIES_IN_CLUSTER = "single_series_in_cluster"

# 默认置信水平与抽样参数（要改只改这里 —— 承B1「唯一配置源」的精神）。
DEFAULT_ALPHA = 0.05
DEFAULT_BOOTSTRAP_ITERS = 2000
DEFAULT_BOOTSTRAP_SEED = 20261006


class ClusteredInterval:
    """簇级 bootstrap 区间 —— 含观测数与簇数（自带自证字段）。"""

    __slots__ = ("point_estimate", "lower", "upper", "observations", "clusters", "alpha")

    def __init__(
        self,
        *,
        point_estimate: float,
        lower: float,
        upper: float,
        observations: int,
        clusters: int,
        alpha: float,
    ) -> None:
        self.point_estimate = point_estimate
        self.lower = lower
        self.upper = upper
        self.observations = observations
        self.clusters = clusters
        self.alpha = alpha

    @property
    def independence_gap(self) -> float:
        """`1 - 簇数/观测数` —— 0 表示独立，接近 1 表示严重非独立。"""
        if self.observations <= 0:
            return 0.0
        return 1.0 - (self.clusters / self.observations)

    def __repr__(self) -> str:
        return (
            f"ClusteredInterval(point={self.point_estimate:.4f}, "
            f"[{self.lower:.4f}, {self.upper:.4f}], "
            f"n={self.observations} 观测 / {self.clusters} 簇)"
        )


class ClusteredCmhResult:
    """CMH 分层比较结果（承 V11）。

    ⚠️ **本类只产出 `p_value`，不做判决**（TD-05-2）：一次 CMH 只是**家族里的一次检验**，
    单看它无法知道"总共试了几次"。判决**必须**走 `benjamini_hochberg([...])`
    —— 与 V12「没有裸比例出口」同款结构：**没有裸 p 值判决的出口**。
    """

    __slots__ = ("chi_square", "p_value", "odds_ratio", "strata_count")

    def __init__(
        self,
        *,
        chi_square: float,
        p_value: float,
        odds_ratio: float,
        strata_count: int,
    ) -> None:
        self.chi_square = chi_square
        self.p_value = p_value
        self.odds_ratio = odds_ratio
        self.strata_count = strata_count


class MultipleTestResult:
    """BH 校正结果 —— **报 p 值的唯一判决出口**（承 V18 / TD-05-2）。

    自带 `family_size`（本节做了几次检验）—— **判决与分母绑死**，
    想只看一个 p 值也拿不到判决。
    """

    __slots__ = ("family_size", "alpha", "adjusted", "significant", "threshold")

    def __init__(
        self,
        *,
        family_size: int,
        alpha: float,
        adjusted: Sequence[float],
        significant: Sequence[bool],
        threshold: float,
    ) -> None:
        self.family_size = family_size
        self.alpha = alpha
        self.adjusted = list(adjusted)
        self.significant = list(significant)
        self.threshold = threshold

    @property
    def n_discoveries(self) -> int:
        """校正后仍被判显著的数量（= 拒绝集大小）。"""
        return sum(1 for flag in self.significant if flag)

    def is_significant(self, index: int) -> bool:
        """第 `index` 项在**整个家族**校正后是否显著。"""
        if not 0 <= index < self.family_size:
            raise IndexError(
                f"下标 {index} 超出家族大小 {self.family_size}（承 P5：不静默夹紧）"
            )
        return self.significant[index]

    def __repr__(self) -> str:
        return (
            f"MultipleTestResult(m={self.family_size}, alpha={self.alpha}, "
            f"发现 {self.n_discoveries} 项, 阈值={self.threshold})"
        )


# ── 内部工具（纯函数）─────────────────────────────────────────────────────
def _mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("统计量收到空序列")
    return sum(values) / len(values)


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    """线性插值分位数（`sorted_values` 必须已升序）。"""
    if not sorted_values:
        raise ValueError("分位数收到空序列")
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"probability 必须落在 [0, 1]，收到 {probability}")
    position = probability * (len(sorted_values) - 1)
    lower_index = int(math.floor(position))
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    weight = position - lower_index
    return sorted_values[lower_index] * (1.0 - weight) + sorted_values[upper_index] * weight


def _normalize_clusters(clusters: Mapping[str, Sequence[float]]) -> list[list[float]]:
    if not clusters:
        raise ValueError("clusters 为空 —— 没有簇就没有区间（承 V12：缺就是缺，不拿默认值兜底）")
    normalized: list[list[float]] = []
    for cluster_id, values in clusters.items():
        values_list = list(values)
        if not values_list:
            raise ValueError(f"簇 `{cluster_id}` 是空的 —— 空簇会让区间虚窄")
        normalized.append(values_list)
    return normalized


# ── 簇级区间（本仓默认口径）──────────────────────────────────────────────
def clustered_interval(
    clusters: Mapping[str, Sequence[float]],
    *,
    statistic: Callable[[Sequence[float]], float] = _mean,
    iterations: int = DEFAULT_BOOTSTRAP_ITERS,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    alpha: float = DEFAULT_ALPHA,
) -> ClusteredInterval:
    """簇级 bootstrap 区间 —— **报比例/均值时的默认口径**。

    重采样单位是**簇**（不是观测）⇒ 区间宽度自动吸收同簇内的相关性。

    `clusters` 是必填的聚簇映射：`{交易日: 该日的所有观测值}`。
    比例场景传 0/1（`1` = 成功）；均值场景传原始数值。
    """
    if iterations <= 0:
        raise ValueError(f"iterations 必须 > 0，收到 {iterations}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha 必须落在 (0, 1)，收到 {alpha}")

    cluster_values = _normalize_clusters(clusters)
    all_values = [value for values in cluster_values for value in values]
    observed = statistic(all_values)

    random_generator = random.Random(seed)
    cluster_count = len(cluster_values)
    resampled_statistics: list[float] = []
    for _ in range(iterations):
        resample: list[float] = []
        for _ in range(cluster_count):
            resample.extend(cluster_values[random_generator.randrange(cluster_count)])
        resampled_statistics.append(statistic(resample))
    resampled_statistics.sort()

    return ClusteredInterval(
        point_estimate=observed,
        lower=_quantile(resampled_statistics, alpha / 2.0),
        upper=_quantile(resampled_statistics, 1.0 - alpha / 2.0),
        observations=len(all_values),
        clusters=cluster_count,
        alpha=alpha,
    )


# ── 有效样本量（TD-05-4：n 不等于独立信息量）────────────────────────────
def effective_sample_size(clusters: Mapping[str, Sequence[float]]) -> tuple[int, int]:
    """返回 `(观测数, 簇数)` —— 报任何`n` 时都要同时给出簇数。"""
    cluster_values = _normalize_clusters(clusters)
    observations = sum(len(values) for values in cluster_values)
    return observations, len(cluster_values)


# ── 簇内单序列专用的 Wilson（面板上必然报错）─────────────────────────────
def wilson_interval(
    successes: int,
    n: int,
    *,
    n_clusters: int,
    scope: str,
    z_value: float = 1.96,
) -> tuple[float, float, float]:
    """Wilson 得分区间 —— **仅限簇内单序列**。

    ⚠️ **面板数据请改用 `clustered_interval`**（承 V12）。
    硬校验：`n_clusters != 1` ⇒ 直接报错，不给"凑合用"的余地。
    """
    if scope != SINGLE_SERIES_IN_CLUSTER:
        raise ValueError(
            f"scope 只能是 {SINGLE_SERIES_IN_CLUSTER!r}，收到 {scope!r}"
            " —— 本函数不做作用域推断，必须显式声明"
        )
    if n_clusters != 1:
        raise ValueError(
            f"观测落在 {n_clusters} 个簇上（面板数据），Wilson 的独立性假设不成立（承 V12）。"
            f" 请改用 clustered_interval(clusters, ...)"
        )
    if n <= 0:
        raise ValueError(f"n 必须 > 0，收到 {n}")
    if not 0 <= successes <= n:
        raise ValueError(f"successes={successes} 超出 [0, {n}]")

    z_squared = z_value * z_value
    denominator = n + z_squared
    center = (successes + z_squared / 2.0) / denominator
    half_width = (z_value / denominator) * math.sqrt(
        successes * (n - successes) / n + z_squared / 4.0
    )
    return (center - half_width, center + half_width)


# ── V11：分层比较（CMH）─────────────────────────────────────────────────
def clustered_cmh(
    strata: Mapping[str, Mapping[str, Sequence[bool]]],
) -> ClusteredCmhResult:
    """CMH 分层比较（承 V11）—— **分层维度由调用方决定，本仓默认 = 交易日**。

    `strata` 形如 `{"2026-01-05": {"treated": [...], "control": [...]}}`，
    每层的两组各含 0/1 结果。分层维度须与报告里声明的一致（design 01 §4.10b）。

    ⚠️ **不接 `alpha`**：本函数只产出 `p_value`，判决走 `benjamini_hochberg`
    （TD-05-2）—— 在这里放 alpha 等于邀请"拿单个 p 值直接下结论"。
    """
    if not strata:
        raise ValueError("strata 为空 —— 没有层就没有可合并的证据")

    chi_square = 0.0
    variance_sum = 0.0
    numerator_sum = 0.0
    denominator_sum = 0.0
    used_strata = 0

    for stratum_id, arms in strata.items():
        treated = list(arms.get("treated", []))
        control = list(arms.get("control", []))
        if not treated or not control:
            raise ValueError(f"层 `{stratum_id}` 缺一组（treated / control 必须都有观测）")

        treated_successes = sum(1 for value in treated if value)
        control_successes = sum(1 for value in control if value)
        treated_n = len(treated)
        control_n = len(control)
        total_n = treated_n + control_n
        total_successes = treated_successes + control_successes

        if total_successes == 0 or total_successes == total_n:
            # 该层无对比信息（两组全同）⇒ 该层对 MH 的贡献为 0，跳过而不是报错。
            continue

        expected = treated_n * total_successes / total_n
        variance = (
            treated_n
            * control_n
            * total_successes
            * (total_n - total_successes)
            / (total_n**2 * (total_n - 1))
        )
        chi_square += (treated_successes - expected) ** 2
        variance_sum += variance

        # MH 合并优势比 —— 2x2 表 [a=treated 成功, b=treated 失败, c=control 成功, d=control 失败]
        #   OR_MH = Σ(a·d / n) / Σ(b·c / n)
        # ⚠️ 分子里的 d 是**对照组失败数**，分母里的 b 是**处理组失败数**
        #（不是对照组/处理组的总数 —— 用总数会让优势比恒等于 1 的错值）。
        treated_failures = treated_n - treated_successes
        control_failures = control_n - control_successes
        numerator_sum += treated_successes * control_failures / total_n
        denominator_sum += treated_failures * control_successes / total_n
        used_strata += 1

    if used_strata == 0 or variance_sum <= 0.0:
        raise ValueError(
            "没有任何一层提供对比信息（各层两组结果完全相同）⇒ CMH 无定义，"
            "不给默认值兜底"
        )

    chi_square /= variance_sum
    # 卡方 1 自由度的上侧概率 = erfc(sqrt(x/2))（标准库 math.erfc，纯标准库实现）
    p_value = math.erfc(math.sqrt(chi_square / 2.0))
    odds_ratio = numerator_sum / denominator_sum if denominator_sum > 0.0 else math.inf

    return ClusteredCmhResult(
        chi_square=chi_square,
        p_value=p_value,
        odds_ratio=odds_ratio,
        strata_count=used_strata,
    )


# ── V18：多重检验校正（TD-05-2）──────────────────────────────────────────
def benjamini_hochberg(
    p_values: Sequence[float],
    *,
    alpha: float = DEFAULT_ALPHA,
) -> MultipleTestResult:
    """**Benjamini–Hochberg** —— 控制错误发现率（FDR）。

    为什么必须有它（TD-05-2 的实测）：策略注册表 + 阈值网格 ⇒ 必然试 N 次；
    1 − 0.95¹⁸ ≈ **60%** ⇒ **只报单个 p<0.05 而不报分母，等于没做统计。**

    | | 控制 | 代价 |
    | --- | --- | --- |
    | Bonferroni | 至少一次假阳性的概率（FWER） | **太保守**，检验一多就什么都不显著 |
    | **BH（本函数）** | 假阳性占「发现」的比例（FDR） | 平衡 —— **探索性研究该用它** |

    返回 `MultipleTestResult`（含 `family_size` / 逐项 q 值 / 拒绝集）——
    **没有"拿单个 p 值直接下结论"的出口**。

    ⚠️ `m == 1` 时 BH 退化为 `p < alpha`（单次检验无需校正）—— 所以
    **"只做了一次检验"也必须走这里**，口径统一、不需两套判决。
    """
    values = list(p_values)
    if not values:
        raise ValueError("p_values 为空 —— 没有检验就没有校正（承 P1：缺就是缺）")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha 必须落在 (0, 1)，收到 {alpha}")
    for index, value in enumerate(values):
        if value is None or value != value:
            raise ValueError(
                f"p_values[{index}] 是 {value!r} —— 缺失/NaN 必须显形，"
                f"不许静默丢弃（承 P5）"
            )
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"p_values[{index}]={value} 超出 [0, 1]（承 P5：异常值显形）")

    family_size = len(values)
    # 升序 rank 表：order[k-1] = 第 k 小的 p 值在原序列里的下标
    order = sorted(range(family_size), key=lambda index: values[index])

    # ① 调整后的 q 值：从**最大**的 p 往回累积最小值（BH 的 step-up）
    #    q(k) = min over j >= k of ( m / j * p(j) )
    adjusted = [1.0] * family_size
    running = 1.0
    for rank in range(family_size, 0, -1):
        index = order[rank - 1]
        running = min(running, values[index] * family_size / rank)
        adjusted[index] = min(1.0, running)

    # ② 找**最大**的 k 使 p(k) <= (k/m)·alpha —— 它之前的全部拒绝（step-up）
    cutoff_rank = 0
    for rank in range(1, family_size + 1):
        if values[order[rank - 1]] <= alpha * rank / family_size:
            cutoff_rank = rank
    significant = [False] * family_size
    for rank in range(1, cutoff_rank + 1):
        significant[order[rank - 1]] = True
    threshold = values[order[cutoff_rank - 1]] if cutoff_rank else float("nan")

    return MultipleTestResult(
        family_size=family_size,
        alpha=alpha,
        adjusted=adjusted,
        significant=significant,
        threshold=threshold,
    )


# ── TD-05-4：事件去重（重叠收益不许重复计入）────────────────────────────
def dedupe_overlapping_events(
    event_positions: Sequence[int],
    *,
    min_gap: int,
) -> list[int]:
    """事件去重 —— **持有期内重叠的触发只保留一个**（承 TD-05-4）。

    ⚠️ 为什么需要：持有期 20 天时，**相邻触发日的收益互相重叠**；同日 N 个信号
    若被当 N 次独立实验，`n` 会系统性虚高（futu 实测：4,076 笔只落在 **992 天**，
    单日最多 **108 笔** —— 按笔 vs 按日，结论**符号翻转**）。

    `event_positions` = 事件在 bar 轴上的位置（整数、**非递减**，通常是 bar 下标）；
    **同日多信号 = 相同位置** ⇒ 会被压成 1 个。
    `min_gap` = 持有期（bar 数）—— **必填**，不猜（承 P1：口径消耗必须显形）。

    贪心：保留第一个；之后只保留与**上一个保留项**距离 `>= min_gap` 的。
    返回**保留项在输入中的下标**（调用方据此同步保留其他字段）。

    去重后报样本量时，应配合 `effective_sample_size` 同时给出观测数与簇数。
    """
    positions = list(event_positions)
    if min_gap <= 0:
        raise ValueError(
            f"min_gap 必须 > 0（= 持有期 bar 数），收到 {min_gap}"
            f" —— 0 会让去重变成空操作（承 P1）"
        )
    for index in range(1, len(positions)):
        if positions[index] < positions[index - 1]:
            raise ValueError(
                f"event_positions 必须非递减，但 [{index - 1}]={positions[index - 1]} "
                f"> [{index}]={positions[index]} —— 顺序错会让去重结果依赖输入顺序"
            )

    kept: list[int] = []
    for index, position in enumerate(positions):
        if not kept or position - positions[kept[-1]] >= min_gap:
            kept.append(index)
    return kept


# ── 报比例的唯一出口（V12 的验收格式）──────────────────────────────────
def format_rate_with_clusters(
    successes: int,
    clusters: Mapping[str, Sequence[bool]],
    *,
    title: str = "胜率",
    alpha: float = DEFAULT_ALPHA,
) -> str:
    """产出带簇级区间与簇数的比例字符串 —— **V12 的硬格式**。

    `clusters` 必填 ⇒ **不存在"只报裸比例"的出口**：

        胜率 60.0% [簇级 95%: 23.1%, 88.2%]   n=200 观测 / 5 簇
    """
    successes_as_float = float(successes)
    observations, cluster_count = effective_sample_size(clusters)
    if not 0 <= successes <= observations:
        raise ValueError(f"successes={successes} 超出 [0, {observations}]")

    interval = clustered_interval(clusters, alpha=alpha)
    return (
        f"{title} {successes_as_float / observations * 100:.1f}% "
        f"[簇级 {(1 - alpha) * 100:.0f}%: {interval.lower * 100:.1f}%, "
        f"{interval.upper * 100:.1f}%] "
        f"n={observations} 观测 / {cluster_count} 簇"
    )


__all__ = [
    "ClusteredCmhResult",
    "ClusteredInterval",
    "MultipleTestResult",
    "SINGLE_SERIES_IN_CLUSTER",
    "benjamini_hochberg",
    "clustered_cmh",
    "clustered_interval",
    "dedupe_overlapping_events",
    "effective_sample_size",
    "format_rate_with_clusters",
    "wilson_interval",
]