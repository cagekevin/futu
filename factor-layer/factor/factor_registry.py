"""M3 因子 —— 注册表 + 唯一入口（照 `backtest/strategy_contract.py` 的已验证写法）。

## 为什么是注册表（承 A2）

「加一个新因子 = 加一个文件 + 登记一个名字，框架其余**零改动**」——
这是 PRD 定位里「**可回答**」验收判据的落地：
给你一个没见过的因子，不改任何框架代码就能跑出完整判决。

⚠️ 与 `backtest/strategy_contract.py` **同构但不复用**（承 PRD §7.2 并列设计）：
   本层与 backtest **互不 import** —— 复用会形成反向依赖。
"""
from __future__ import annotations

import pandas as pd

from factor.factor_protocol import Factor, FactorInput, FactorValues
from factor.factor_spec import FactorSpec
from panel.panel_types import CrossSectionPanel

__all__ = [
    "register_factor", "get_factor", "available_factors",
    "factor_inputs", "run_factor",
]

_FACTOR_REGISTRY: dict[str, Factor] = {}


def register_factor(factor: Factor) -> Factor:
    """把因子实例登记进注册表。

    重名 / 空名 → **报错**，不静默覆盖（承 P2：不猜、不掩盖）。
    """
    spec = getattr(factor, "spec", None)
    name = getattr(spec, "name", "")
    if not name:
        raise ValueError("因子必须定义非空 `spec.name`（承 P2）")
    if name in _FACTOR_REGISTRY:
        raise ValueError(f"因子名重复：{name}（承 P2：不静默覆盖）")
    _FACTOR_REGISTRY[name] = factor
    return factor


def get_factor(name: str) -> Factor:
    """按注册名取因子；未知名字 → 报错并列出可用名字（承 P1：不静默兜底）。"""
    if not _FACTOR_REGISTRY:
        raise RuntimeError(
            "因子注册表为空 —— 先 `import factor.implementations`"
            "（导入即注册，承 A2）"
        )
    if name not in _FACTOR_REGISTRY:
        raise KeyError(f"未知因子 {name!r}；可用：{available_factors()}（承 P1）")
    return _FACTOR_REGISTRY[name]


def available_factors() -> list[str]:
    """已注册的因子名（升序）。"""
    return sorted(_FACTOR_REGISTRY)


def factor_inputs(spec: FactorSpec, panel: CrossSectionPanel) -> FactorInput:
    """按 `spec.inputs` 从面板取字段 —— **只取声明的那些**（承 A3）。"""
    return FactorInput(fields={name: panel.field(name) for name in spec.inputs})


def _check_values(values: "pd.DataFrame", spec: FactorSpec,
                  panel: CrossSectionPanel) -> None:
    """校验因子产物：形状对齐 + **warm-up 期必须是 `NaN`**（承 A4）。

    ⚠️ 后一条是关键：`ret20` 的前 20 天必然是 `NaN`。若被填成 0 或常数，
       这些**垃圾值会混进 IC 计算** —— 而 IC 不会报错，只会失真。
       （你在 backtest 已踩过：R7「前 199 根特征 ≠ ±5 常数」、R8「算子 t=0 输出 = 0」。）
    """
    if not isinstance(values, pd.DataFrame):
        raise TypeError(
            f"因子 {spec.name!r} 的 `compute` 必须返回 DataFrame，"
            f"收到 {type(values).__name__}（承 P2）"
        )
    if tuple(values.index) != tuple(panel.dates):
        raise ValueError(
            f"因子 {spec.name!r} 的索引与面板交易日不一致"
            f"（面板 {len(panel.dates)} 天，产物 {len(values.index)} 行）（承 P2）"
        )
    if tuple(values.columns) != tuple(panel.symbols):
        raise ValueError(
            f"因子 {spec.name!r} 的列与面板标的不一致"
            f"（面板 {len(panel.symbols)} 只，产物 {len(values.columns)} 列）（承 P2）"
        )
    head = values.iloc[:spec.min_window]
    if spec.min_window and not bool(head.isna().to_numpy().all()):
        raise ValueError(
            f"因子 {spec.name!r} 声明 min_window={spec.min_window}，"
            f"但前 {spec.min_window} 行**不全是 NaN** —— "
            f"warm-up 期的值会污染 IC（承 A4：不许填 0、不许填常数）"
        )


def run_factor(name: str, panel: CrossSectionPanel) -> FactorValues:
    """跑一个因子 —— M3 对外的**唯一入口**。

    步骤：注册表取因子 → 按 `spec.inputs` 取字段 → **校验口径** → `compute` → 校验产物。

    ⚠️ **口径必须匹配**：因子声明 `adjust="hfq"`，而面板若是 raw
       （或反过来）→ **报错**。不匹配时算出来的因子是错的（除权日假跳变），
       而且**不会报错** —— 只会让 IC 悄悄失真（承 A3 / backtest D5、D8）。
    """
    factor = get_factor(name)
    spec = factor.spec
    if panel.adjust != spec.adjust:
        raise ValueError(
            f"因子 {spec.name!r} 声明口径 `{spec.adjust}`，但面板的口径是 "
            f"`{panel.adjust}` —— 不匹配（除权日会出现假跳变）。"
            f"请用 `read_panel(..., adjust={spec.adjust!r})` 重取（承 A3）"
        )
    values = factor.compute(factor_inputs(spec, panel))
    _check_values(values, spec, panel)
    return FactorValues(spec=spec, values=values)
