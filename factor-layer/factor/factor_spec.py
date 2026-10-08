"""M3 因子 —— `FactorSpec`（元信息）+ `FactorName`（命名规范）。

## `FactorSpec` 六要素（承 A3：**必填、无默认**）

| 要素 | 含义 | 缺了会怎样 |
|---|---|---|
| `name` | 原始因子名 | 无法登记 / 无法改名 |
| `inputs` | 依赖的字段 | 因子偷偷用别的字段（声明与使用不一致）|
| `min_window` | warm-up 长度 | **warm-up 期的垃圾值混进 IC**（承 A4）|
| `frequency` | 频率 | 换周期时口径不明 |
| `adjust` | 复权口径 | **除权日假跳变**（承 A3 / backtest D5、D8）|
| `direction` | 先验看多方向 | 单调性符号错 → **有效因子被误判**（承 J5）|

## `direction` 的语义（**先验**，不是结论）

`+1` = 因子值**越大越看多**；`-1` = 越小越看多。

⚠️ 它是**设计意图**，会被 IC 检验推翻。所以：
  · 每个因子的 docstring 必须写明它的先验来源；
  · M5 的报告应**同时**给出"按 direction 归一"与"原始符号"的单调性 ——
    这样方向猜错时看得出来（承 J5）。

## 命名规范（承 A5：血缘编码在名字里）

```
ret20  →  z_ret20  →  z_neu_ret20
（原始）   （标准化）   （中性化）
```

⚠️ **必须先查 `z_neu_`**：`z_neu_ret20` 也以 `z_` 开头，先查短前缀会得到
`neu_ret20`（错）。这是 zer0factor 的同款实现，照抄其顺序。
"""
from __future__ import annotations

from dataclasses import dataclass

from panel.panel_types import ADJUST_MODES

__all__ = [
    "FactorSpec", "FactorName",
    "DIRECTION_LONG", "DIRECTION_SHORT", "DIRECTIONS",
    "STANDARDIZED_PREFIX", "NEUTRALIZED_PREFIX",
]

#: 看多方向：值越大越看多。
DIRECTION_LONG = 1
#: 看空方向：值越小越看多。
DIRECTION_SHORT = -1
DIRECTIONS = (DIRECTION_LONG, DIRECTION_SHORT)

#: 标准化后的因子名前缀（M4 产出）。
STANDARDIZED_PREFIX = "z_"
#: 中性化后的因子名前缀（M4 产出）。
NEUTRALIZED_PREFIX = "z_neu_"

_FREQUENCIES = ("1d",)


@dataclass(frozen=True)
class FactorSpec:
    """因子元信息（六要素，**全部必填、无默认** —— 承 A3）。

    ⚠️ **构造时校验**（承 M4-T4 的同一判据）：非法参数在**构造时**报错，
       不等到运行时 —— 早报错 = 早发现 = 少返工。
    """

    name: str
    inputs: tuple[str, ...]
    min_window: int
    frequency: str
    adjust: str
    direction: int

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError(f"因子名必须是非空字符串：{self.name!r}（承 A3）")
        if self.name.startswith((STANDARDIZED_PREFIX, NEUTRALIZED_PREFIX)):
            raise ValueError(
                f"因子名 {self.name!r} 带派生前缀 —— `{STANDARDIZED_PREFIX}` / "
                f"`{NEUTRALIZED_PREFIX}` 是 **M4 的产物**，原始因子不许用（承 A5）"
            )
        if not self.inputs:
            raise ValueError(f"因子 {self.name!r} 必须声明至少一个 `inputs`（承 A3）")
        if len(set(self.inputs)) != len(self.inputs):
            raise ValueError(f"因子 {self.name!r} 的 `inputs` 有重复：{self.inputs}")
        for field in self.inputs:
            if not isinstance(field, str) or not field.isidentifier():
                raise ValueError(
                    f"因子 {self.name!r} 的 `inputs` 含非法字段名：{field!r}（承 A3）"
                )
        if not isinstance(self.min_window, int) or self.min_window < 1:
            raise ValueError(
                f"因子 {self.name!r} 的 `min_window` 必须是 ≥1 的整数："
                f"{self.min_window!r}（承 A4）"
            )
        if self.frequency not in _FREQUENCIES:
            raise ValueError(
                f"因子 {self.name!r} 的 `frequency` 非法：{self.frequency!r}"
                f"（可选 {_FREQUENCIES}）"
            )
        if self.adjust not in ADJUST_MODES:
            raise ValueError(
                f"因子 {self.name!r} 的 `adjust` 非法：{self.adjust!r}"
                f"（可选 {ADJUST_MODES}；**无默认** —— 承 A3）"
            )
        if self.direction not in DIRECTIONS:
            raise ValueError(
                f"因子 {self.name!r} 的 `direction` 非法：{self.direction!r}"
                f"（可选 {DIRECTIONS}）"
            )


@dataclass(frozen=True)
class FactorName:
    """因子名（原始名 ⇄ 派生名）—— 承 A5。"""

    raw: str

    @classmethod
    def parse(cls, name: str) -> "FactorName":
        """从任意（可能带前缀的）名字**反解原始名**。

        ⚠️ **先查 `z_neu_`**：`z_neu_ret20` 也以 `z_` 开头，
           先查短前缀会得到 `neu_ret20`（错）。
        """
        if not isinstance(name, str) or not name:
            raise ValueError(f"因子名必须是非空字符串：{name!r}（承 A5）")
        if name.startswith(NEUTRALIZED_PREFIX):
            return cls(name[len(NEUTRALIZED_PREFIX):])
        if name.startswith(STANDARDIZED_PREFIX):
            return cls(name[len(STANDARDIZED_PREFIX):])
        return cls(name)

    @property
    def standardized(self) -> str:
        return f"{STANDARDIZED_PREFIX}{self.raw}"

    @property
    def neutralized(self) -> str:
        return f"{NEUTRALIZED_PREFIX}{self.raw}"
