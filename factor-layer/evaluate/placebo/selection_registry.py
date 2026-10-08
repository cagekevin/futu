"""R1 选择集 —— 注册表 + 唯一入口（照 `factor/factor_registry.py` 的已验证写法）。

## 为什么是注册表（承 H2）

「加一个选择规则 = 加一个文件 + 登记一个名字，框架其余**零改动**」——
这是本能力 PRD §一「**可回答**」验收判据的落地：
给你一个没见过的筛选规则，不改任何框架代码就能跑出完整对照报告。

## 唯一入口 `run_selection` 干了三件**必须集中在一处**的事

1. **校验依赖齐备**（承 H1）：`requires` 里有、但送进来的没有 → 报错，不静默用 0。
2. **物理切片 `[:day]`**（承 H3，仿 backtest V7）：
   规则拿到的因子表**根本不含** `day` 之后的数据 —— 想违规也没有数据可拿。
3. **校验产物**（承 H1）：选中集必须是 `universe` 的**子集**，且类型正确。

⚠️ 与 `factor/factor_registry.py` **同构但各自独立**（承本 PRD §二 C1：
本能力不 import `backtest/`；同理不与 M3 共用注册表 —— 它们的"注册对象"不同）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from .selection_contract import Selection, SelectionInput

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注
    import pandas as pd

__all__ = [
    "SelectionResult",
    "available_selections",
    "get_selection",
    "register_selection",
    "run_selection",
]

_SELECTION_REGISTRY: dict[str, Selection] = {}


@dataclass(frozen=True)
class SelectionResult:
    """一次选择的产物（**不可变** —— 供 R2/R3 与报告直接消费）。"""

    name: str
    day: str
    picked: frozenset[str]
    params_fingerprint: str
    n_universe: int

    def __len__(self) -> int:
        return len(self.picked)


def register_selection(selection: Selection) -> Selection:
    """把选择规则登记进注册表。

    重名 / 空名 → **报错**，不静默覆盖（承 P2：不猜、不掩盖）。
    """
    name = getattr(selection, "name", "")
    if not name or not isinstance(name, str):
        raise ValueError("选择规则必须定义非空 `name`（承 P2）")
    if not isinstance(getattr(selection, "requires", None), tuple):
        raise TypeError(
            f"选择规则 {name!r} 必须定义 `requires: tuple[str, ...]`（承 H1）"
        )
    if not callable(getattr(selection, "select", None)):
        raise TypeError(f"选择规则 {name!r} 必须实现 `select(data)` 方法（承 H1）")
    if not isinstance(getattr(selection, "params", None), Mapping):
        raise TypeError(
            f"选择规则 {name!r} 必须定义 `params`（Mapping，供复现，承 H3）"
        )
    if name in _SELECTION_REGISTRY:
        raise ValueError(f"选择规则名重复：{name}（承 P2：不静默覆盖）")
    _SELECTION_REGISTRY[name] = selection
    return selection


def get_selection(name: str) -> Selection:
    """按注册名取规则；未知名字 → 报错并列出可用名字（承 P1：不静默兜底）。"""
    if not _SELECTION_REGISTRY:
        raise RuntimeError(
            "选择规则注册表为空 —— 先 `import evaluate.placebo.implementations`"
            "（导入即注册，承 H2）"
        )
    if name not in _SELECTION_REGISTRY:
        raise KeyError(
            f"未知选择规则 {name!r}；可用：{available_selections()}（承 P1）"
        )
    return _SELECTION_REGISTRY[name]


def available_selections() -> list[str]:
    """已注册的规则名（升序）。"""
    return sorted(_SELECTION_REGISTRY)


def params_fingerprint(params: Mapping[str, object]) -> str:
    """参数的**内容指纹** —— 进报告，回答"当时用的是哪个阈值"（承 H3 / P3）。

    相同内容 → 相同指纹；改任一阈值 → 指纹变。
    """
    payload = json.dumps(dict(params), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def run_selection(
    name: str,
    day: str,
    factors: Mapping[str, "pd.DataFrame"],
    universe: tuple[str, ...],
) -> SelectionResult:
    """跑一个选择规则 —— R1 对外的**唯一入口**。

    步骤：取规则 → **校验依赖齐备** → **物理切片 `[:day]`** → `select` → **校验产物**。

    ⚠️ **为什么切片放在这里、而不交给规则自己做**：
       若把整张（含未来的）表交给规则，就只剩"约定不要看未来" ——
       承 backtest §4.6：**注释写"严格因果"是弱手段，切片隔离才是强手段**。
    """
    selection = get_selection(name)

    missing = [r for r in selection.requires if r not in factors]
    if missing:
        raise KeyError(
            f"选择规则 {name!r} 需要因子 {missing}，但送进来的只有 "
            f"{sorted(factors)}（承 H1：依赖必须显式供给）"
        )

    # ★ 两件物理隔离，都在这里做（承 H3 / H4）：
    #   ① 切到 `[:day]` —— 规则**拿不到** `day` 之后的数据；
    #   ② **只装 `requires` 里声明过的** —— 规则**读不到**未声明的因子。
    #
    # ⚠️ 为什么是"只装"而不是"读的时候报错"（本条由测试先红后绿抓出）：
    #   `SelectionInput` 若装**全部**送进来的因子、只在 `.factor()` 里做名字检查，
    #   规则**仍能扫到**那些表（遍历 `data.factors`）—— 只是读不到名字而已。
    #   **"不装"才是隔离**；"检查"只是提醒。照 M3 的 `factor_inputs`：
    #   它也只把 `spec.inputs` 装进 `FactorInput`。
    sliced = {key: factors[key].loc[:day] for key in selection.requires}

    data = SelectionInput(day=day, factors=sliced, universe=tuple(universe))
    picked = selection.select(data)

    if not isinstance(picked, (set, frozenset)):
        raise TypeError(
            f"选择规则 {name!r} 的 `select` 必须返回 set，"
            f"收到 {type(picked).__name__}（承 H1）"
        )
    outside = set(picked) - set(universe)
    if outside:
        raise ValueError(
            f"选择规则 {name!r} 选中了票池外的标的：{sorted(outside)[:5]}"
            f"……（共 {len(outside)} 个）—— 越界选择会让对照失效"
            f"（承 C2：抽样/选择都只在该日票池内）"
        )

    return SelectionResult(
        name=name,
        day=day,
        picked=frozenset(picked),
        params_fingerprint=params_fingerprint(selection.params),
        n_universe=len(universe),
    )
