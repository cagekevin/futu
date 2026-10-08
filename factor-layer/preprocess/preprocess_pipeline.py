"""M4 预处理 —— 配置 + **顺序写死**的编排 + 产物类型。

## ★ 顺序（承 T1：**写死，不是配置项**）

```
原始因子 → ① 去极值 → ② 补缺 → ③ 标准化 → ④ 中性化 → 标准因子
```

**为什么不给"顺序"配置项**：给了就有人调换；调换后结果**完全不同**，
而且**不会报错** —— 只会让所有历史结论悄悄作废（不可比）。
所以顺序**只写一遍**，在 `read_preprocessed_factor()` 里。

## 配置（承 T4：frozen + **构造时**校验 + 无默认）

所有字段**必填**、**无默认值**。`neutralize_method` **禁止 `None`** ——
要"不做中性化"必须**显式**写 `"none"`（承 Q4：口径消耗必须显形）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from exposure.exposure_types import ExposureSet
from factor.factor_protocol import FactorValues
from factor.factor_spec import FactorName, FactorSpec
from preprocess.factor_impute import impute
from preprocess.factor_neutralize import NEUTRALIZE_METHODS, neutralize
from preprocess.factor_standardize import standardize
from preprocess.factor_winsorize import winsorize

__all__ = [
    "PreprocessConfig", "PreprocessedFactor",
    "WINSORIZE_METHODS", "IMPUTE_METHODS", "STANDARDIZE_METHODS",
    "NEUTRALIZE_METHODS", "derived_name", "read_preprocessed_factor",
]

WINSORIZE_METHODS = ("mad", "none")
IMPUTE_METHODS = ("cross_section_median", "none")
STANDARDIZE_METHODS = ("zscore", "none")


@dataclass(frozen=True)
class PreprocessConfig:
    """预处理配置 —— **全部必填、无默认**（承 T4 / Q4）。

    ⚠️ 非法参数在**构造时**报错，不等到运行时 —— 早报错 = 早发现 = 少返工。
    """

    winsorize_method: str
    winsorize_n: float
    impute_method: str
    impute_threshold: float
    standardize_method: str
    neutralize_method: str

    def __post_init__(self) -> None:
        self._require_choice("winsorize_method", self.winsorize_method,
                             WINSORIZE_METHODS)
        self._require_choice("impute_method", self.impute_method, IMPUTE_METHODS)
        self._require_choice("standardize_method", self.standardize_method,
                             STANDARDIZE_METHODS)
        self._require_choice("neutralize_method", self.neutralize_method,
                             NEUTRALIZE_METHODS)
        if not (isinstance(self.winsorize_n, (int, float))
                and self.winsorize_n > 0):
            raise ValueError(
                f"winsorize_n 必须为正数：{self.winsorize_n!r}（承 T4）"
            )
        if not (isinstance(self.impute_threshold, (int, float))
                and 0 < self.impute_threshold <= 1):
            raise ValueError(
                f"impute_threshold 必须在 (0, 1]：{self.impute_threshold!r}（承 T4）"
            )

    @staticmethod
    def _require_choice(field: str, value: Any, allowed: tuple[str, ...]) -> None:
        if value not in allowed:
            raise ValueError(
                f"{field} 非法：{value!r}（可选 {allowed}；"
                f"**无默认** —— 承 T4/Q4）"
            )


@dataclass(frozen=True, eq=False)
class PreprocessedFactor:
    """标准因子 —— M4 的产物。

    ⚠️ 为什么不复用 M3 的 `FactorValues`：那个类型要求 `spec.name` 是**原始名**，
       而 `FactorSpec.__post_init__` **禁止**派生前缀。派生结果需要**另一条**
       表达血缘的方式 —— 就是下面的 `name` + `source`。

    `source`：**原始 spec**（血缘）。改名后仍能追回"这值是从哪个因子、用什么
    窗口、什么口径算出来的"（承 A5 / T5）。
    `log`：每步改动量（承 T2）—— clip 几个 / 填几个 / 剔除几个 / 相关验收值。
    """

    name: str
    source: FactorSpec
    values: pd.DataFrame
    log: Mapping[str, Any]

    @property
    def direction(self) -> int:
        """先验看多方向（**继承自原始 spec**）—— 供 M5 做方向归一（承 J5）。"""
        return self.source.direction


def derived_name(source_name: str, config: PreprocessConfig) -> str:
    """原始名 → **派生名**（承 A5 / T5）。

    · 只标准化（不中性化）→ `z_<name>`
    · 做了中性化         → `z_neu_<name>`

    ⚠️ 前缀的拼接**只在这里**（`FactorName` 是唯一出处）。
    """
    name = FactorName(source_name)
    if config.neutralize_method == "none":
        return name.standardized
    return name.neutralized


def read_preprocessed_factor(values: FactorValues, exposures: ExposureSet,
                             config: PreprocessConfig) -> PreprocessedFactor:
    """★ M4 对外**唯一入口** —— 四步**顺序写死**（承 T1）。

    步骤：
      ① `winsorize`  → ② `impute` → ③ `standardize` → ④ `neutralize`

    ⚠️ **原始值不被修改**：每一步都返回**新**宽表（承 T5）。
    """
    frame = values.values
    log: dict[str, Any] = {"source_name": values.name}

    frame, step = winsorize(frame, method=config.winsorize_method,
                            n=config.winsorize_n)
    log.update(step)

    frame, step = impute(frame, method=config.impute_method,
                         threshold=config.impute_threshold)
    log.update(step)

    frame, step = standardize(frame, method=config.standardize_method)
    log.update(step)

    frame, step = neutralize(frame, exposures, method=config.neutralize_method)
    log.update(step)

    return PreprocessedFactor(
        name=derived_name(values.name, config),
        source=values.spec,
        values=frame,
        log=log,
    )
