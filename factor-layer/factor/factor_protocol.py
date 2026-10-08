"""M3 因子 —— 协议 + 输入 + 产物。

## 为什么输入要包一层 `FactorInput`

`FactorSpec.inputs` 声明了"这个因子依赖哪些字段"。若不强制，
因子可以偷用未声明的字段 —— 那**依赖清单就变成谎言**，
下游（缓存、重算、口径审计）全部建立在错的前提上。

`FactorInput` 只暴露**声明过的**字段：访问未声明字段 → **报错**（承 A3）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Protocol

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注
    import pandas as pd

from factor.factor_spec import FactorSpec

__all__ = ["Factor", "FactorInput", "FactorValues"]


@dataclass(frozen=True)
class FactorInput:
    """因子计算时**只**能看到它声明要的字段（承 A3：声明与使用一致）。"""

    fields: Mapping[str, "pd.DataFrame"]

    def field(self, name: str) -> "pd.DataFrame":
        """取一个字段的宽表；**没声明过 → 报错**（承 A3）。"""
        if name not in self.fields:
            raise KeyError(
                f"因子没声明要字段 {name!r} —— 它只能用它 `inputs` 里声明的："
                f"{sorted(self.fields)}（承 A3：声明与使用必须一致）"
            )
        return self.fields[name]


@dataclass(frozen=True, eq=False)
class FactorValues:
    """因子的**原始**值 —— M3 的产物。

    ⚠️ 承 A1：这里只有**原始值** —— 没有任何去极值 / 标准化 / 中性化 / 填充。

    ⚠️ `spec` 与 `values` **绑在一起**（防错配）：
       若让调用方自己配对"值"和"元信息"，迟早会配错
       （尤其 M4 改名后，`z_neu_ret20` 的值配 `ret60` 的 spec）。
    """

    spec: FactorSpec
    values: "pd.DataFrame"

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def direction(self) -> int:
        """先验看多方向 —— 供 M5 做方向归一（承 J5：**不由调用方传**）。"""
        return self.spec.direction


class Factor(Protocol):
    """因子协议：一个 `spec` + 一个「输入 → 原始值」的方法。

    ⚠️ 实现**不许**碰 IO、不许做预处理（承 A1）。
    """

    spec: FactorSpec

    def compute(self, data: FactorInput) -> "pd.DataFrame":
        ...
