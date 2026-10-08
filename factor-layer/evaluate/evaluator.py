"""M5 评估 —— 组装（M5 对外的**唯一入口**）+ 报告类型。

## ⚠️ 对 PRD §五 M5 子模块清单的偏离声明

PRD 列的 M5 子模块是 **5 个**。填充时发现「组装」（标签 → IC → 分组 → 换手 → 判决）
**需要一个落点**，理由与 M1/M2/M4 相同：它不能放进任何一个算法模块
（会形成反向依赖），也不能藏进 `__init__.py`。
⇒ **新增第 6 个文件 `evaluator.py`**，在此**显式声明**（承写码4步法「退回机制」：
不硬填，也不偷偷加）。这与 M1 的 `panel_builder.py`、M2 的 `exposure_builder.py`
是同一个模式。

## ★ 为什么有 `evaluate_many`

BH 的分母是「**这一批试了多少个因子**」（承 Q5）。
逐因子调用 `evaluate` 时 `n_tests` 恒为 1 —— 那**不是**"没校正"，
而是**没有信息可校正**。要真正过 BH，必须把整批因子一起送进来。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pandas as pd

from evaluate.forward_return import forward_return
from evaluate.judgement import JudgeEntry, JudgeThresholds, judge_batch
from evaluate.metrics.ic import ic_statistics, rank_ic_series
from evaluate.metrics.quantile_returns import monotonicity, quantile_returns
from evaluate.metrics.turnover import turnover
from panel.panel_types import CrossSectionPanel
from panel.stock_universe import universe_diagnostics

__all__ = [
    "EvaluateConfig", "EvaluationReport", "evaluate", "evaluate_many",
    "REQUIRED_METRICS",
]

#: J2 的**七项指标**（缺一不可）—— 报告里必须全部出现。
REQUIRED_METRICS = (
    "rank_ic_series", "icir", "ic_win_rate", "t_test",
    "monotonicity", "long_short", "turnover",
)


@dataclass(frozen=True)
class EvaluateConfig:
    """评估配置 —— **全部必填、无默认**（承 Q4）。

    `bins = 5` 与「等频」来自 PRD §6.4（定死）。
    """

    bins: int
    min_samples: int
    thresholds: JudgeThresholds

    def __post_init__(self) -> None:
        if not isinstance(self.bins, int) or self.bins < 2:
            raise ValueError(f"bins 必须是 ≥2 的整数：{self.bins!r}（承 §6.4）")
        if not isinstance(self.min_samples, int) or self.min_samples < self.bins:
            raise ValueError(
                f"min_samples={self.min_samples!r} 必须 ≥ bins={self.bins}"
                f"（否则分不出非空组，承 §6.4）"
            )


@dataclass(frozen=True, eq=False)
class EvaluationReport:
    """因子评估报告 —— M5 的产物。

    **七项指标**（承 J2）在字段里的对应：
      `rank_ic_series` / `icir` / `ic_win_rate` / `t_test` → `ic` + `ic_stats`
      `monotonicity` → `monotonicity`
      `long_short` → `long_short`
      `turnover` → `turnover`

    ⚠️ `direction` 来自**因子对象**（`FactorSpec`），不由调用方传（承 J5）。
    """

    factor_name: str
    direction: int
    ic: pd.DataFrame                 # rank_ic + n_valid（承 J4：有效样本显形）
    ic_stats: Mapping[str, Any]
    quantile_mean: pd.DataFrame
    quantile_counts: pd.DataFrame    # ★ 每组样本数（证明等频生效）
    long_short: pd.Series
    monotonicity: float
    turnover: Mapping[str, Any]
    judgement: Mapping[str, Any]
    log: Mapping[str, Any]
    #: ★ 票池诊断（承审计 A4）—— 显形「票池是不是幸存者集合」。
    #: 幸存者偏差**高估** IC ⇒ **「不显著」可信、「显著」不可信**。
    #: 看 `no_symbol_ever_left` 与 `caveat`。
    universe: Mapping[str, Any]

    def has_all_required_metrics(self) -> bool:
        """J2：七项指标的**字段**是否齐全（**结构**检查，不看值是否为 `NaN`）。

        ⚠️ **审计修正（2026-10-07）**：初版还检查"值非 NaN" ——
           那是把两件事混成了一个检查：

           | 情形 | 性质 | 该怎么办 |
           |---|---|---|
           | 字段**不在**（`ic` 缺 `n_valid` 列…）| **实现漏算** = bug | **报错** ✅ |
           | 字段在但值是 `NaN` | **数据不足**（如 warm-up 吃满窗口）| 合法结果 → 判决给 `insufficient_data` |

           混在一起的后果实测到了：**整批评估会因单个因子数据不足而全部抛异常**，
           而那恰恰是 `insufficient_data` 判决存在的意义。

        ⚠️ 值的非 `NaN` 检查由测试承担（`test_j2_seven_metrics_all_present`
           在**正常数据**下断言七项都有定义）—— 测试管质量，运行时守卫管结构。
        """
        ic_stats = self.ic_stats
        checks = {
            "rank_ic_series": {"rank_ic", "n_valid"} <= set(self.ic.columns),
            "icir": "icir" in ic_stats,
            "ic_win_rate": "ic_win_rate" in ic_stats,
            "t_test": {"t_stat", "p_value"} <= set(ic_stats),
            "monotonicity": hasattr(self, "monotonicity"),
            "long_short": hasattr(self, "long_short"),
            "turnover": {"mean", "series"} <= set(self.turnover),
        }
        return all(checks.values())

    def normalized_ic(self) -> pd.Series:
        """按 `direction` 归一后的 IC 序列（承 J5）—— 与 `monotonicity` 同符号。"""
        return self.ic["rank_ic_normalized"]


def _require_factor(factor: Any, panel: CrossSectionPanel) -> tuple[pd.DataFrame, int, str]:
    """从因子对象取出 `(values, direction, name)` —— 承 J5：方向**只能**来自对象。"""
    values = getattr(factor, "values", None)
    direction = getattr(factor, "direction", None)
    if values is None or direction is None:
        raise TypeError(
            "`factor` 必须同时提供 `values`（宽表）与 `direction` —— "
            "M3 的 `FactorValues` / M4 的 `PreprocessedFactor` 都满足。"
            "方向**不许**从参数传（承 J5）"
        )
    if tuple(values.index) != tuple(panel.dates) or \
            tuple(values.columns) != tuple(panel.symbols):
        raise ValueError("因子宽表与面板的形状不一致 —— 标签无法对齐（承 P2：不猜）")
    return values, int(direction), str(getattr(factor, "name", ""))


def evaluate_many(factors: Sequence[Any], panel: CrossSectionPanel, *,
                  config: EvaluateConfig) -> list[EvaluationReport]:
    """★ 批量评估 + **一次** BH 校正（承 Q5 / J3）。

    `factors`：每个元素需提供 `values`（宽表）+ `direction`（承 J5）。
      M3 的 `FactorValues` 与 M4 的 `PreprocessedFactor` 都满足。

    ⚠️ 承 J1：**只读** —— 不改任何 `factor.values`、不改 `panel`。
    """
    target = forward_return(panel)
    # ★ 票池诊断只算一次（它是**面板级**的，与具体因子无关）—— 承审计 A4
    universe = universe_diagnostics(panel)
    prepared: list[dict[str, Any]] = []

    for factor in factors:
        values, direction, name = _require_factor(factor, panel)
        raw_ic = rank_ic_series(values, target, min_samples=config.min_samples)
        # ★ J5：**IC 也按 `direction` 归一** —— 与单调性用同一套符号。
        #   不归一的话，报告里会出现「单调性 +1.0 但 ICIR 为负」这种自相矛盾的画面
        #   （其实是同一件事的两种表述）。原始值**保留**，归一值另起一列。
        ic = raw_ic.assign(rank_ic_normalized=raw_ic["rank_ic"] * direction)
        ic_stats = ic_statistics(ic["rank_ic_normalized"],
                                 min_days=config.thresholds.min_days)
        qmean, qcounts, long_short, skipped = quantile_returns(
            values, target, bins=config.bins, min_samples=config.min_samples)
        mono = monotonicity(qmean, direction)
        turn = turnover(values, target, bins=config.bins,
                        min_samples=config.min_samples)
        prepared.append({
            "name": name, "direction": direction, "ic": ic, "ic_stats": ic_stats,
            "quantile_mean": qmean, "quantile_counts": qcounts,
            "long_short": long_short, "monotonicity": mono, "turnover": turn,
            "skipped": skipped, "ic_stats_for_judge": ic_stats,
            # ★ 经济幅度（2026-10-08 审计补）：判决要消费它们，不只报出来。
            "long_short_normalized_mean": (
                float(long_short.mean()) * direction if len(long_short)
                else float("nan")),
            "turnover_mean": float(turn.get("mean", float("nan"))),
        })

    # ── ★ 一次 BH 校正**整批**（承 Q5：单个 p 做 BH 在数学上是错的）──────
    # ⚠️ `long_short_mean` 用**归一后**的（承 J5：与 IC / 单调性同一套符号）；
    #    `turnover_mean` 是无方向的（换手不分多空），故不归一。
    verdicts = judge_batch(
        [JudgeEntry(name=p["name"],
                    p_value=float(p["ic_stats"].get("p_value", float("nan"))),
                    icir=float(p["ic_stats"].get("icir", float("nan"))),
                    n_days=int(p["ic_stats"].get("n_days", 0)),
                    monotonicity=float(p["monotonicity"]),
                    long_short_mean=p["long_short_normalized_mean"],
                    turnover_mean=p["turnover_mean"])
         for p in prepared],
        thresholds=config.thresholds,
    )

    reports: list[EvaluationReport] = []
    for item, verdict in zip(prepared, verdicts):
        ic = item["ic"]
        turn = item["turnover"]
        counts = item["quantile_counts"]
        report = EvaluationReport(
            factor_name=item["name"],
            direction=item["direction"],
            ic=ic,
            ic_stats=item["ic_stats"],
            quantile_mean=item["quantile_mean"],
            quantile_counts=counts,
            long_short=item["long_short"],
            monotonicity=item["monotonicity"],
            turnover=turn,
            judgement=verdict,
            universe=universe,
            log={
                # ★ J4：有效样本显形
                "n_valid_min": int(ic["n_valid"].min()) if len(ic) else 0,
                "n_valid_median": float(ic["n_valid"].median()) if len(ic) else 0.0,
                "n_days_no_ic": int((ic["n_valid"] < config.min_samples).sum()),
                # 分箱显形（承 J2：每组样本数必须报出）
                "quantile_skipped_days": item["skipped"],
                "quantile_min_group_size": (
                    int(counts.to_numpy().min()) if counts.size else 0),
                "turnover_skipped_days": turn.get("skipped_days", []),
                "config_bins": config.bins,
                "config_min_samples": config.min_samples,
                # ⚠️ 多空收益**同时给原始值与归一值** ——
                #    IC / 单调性已按 `direction` 归一（承 J5），若只给原始多空，
                #    报告里会混用两套符号约定，读者会以为算错了。
                "long_short_mean_raw": (
                    item["long_short_normalized_mean"] / item["direction"]
                    if item["direction"] else float("nan")),
                "long_short_mean_normalized": item["long_short_normalized_mean"],
                "turnover_mean": item["turnover_mean"],
            },
        )
        if not report.has_all_required_metrics():
            raise ValueError(
                f"因子 {report.factor_name!r} 的评估报告缺指标"
                f"（承 J2：七项缺一不可）—— 要求 {sorted(REQUIRED_METRICS)}"
            )
        reports.append(report)
    return reports


def evaluate(factor: Any, panel: CrossSectionPanel, *,
             config: EvaluateConfig) -> EvaluationReport:
    """单因子便捷入口 = `evaluate_many([factor], ...)`。

    ⚠️ 此时 `n_tests = 1` —— **这不是"跳过了 BH"**，而是"这一批只有一个因子，
       没有可校正的多重性"。要真正过 BH，请用 `evaluate_many` 送**整批**。
    """
    return evaluate_many([factor], panel, config=config)[0]
