"""M3 因子实现 —— **导入即注册**（照 `backtest/strategies/` 的写法）。

⚠️ 用之前必须 `import factor.implementations`。
本模块**故意不做**自动导入 —— 若 `factor/__init__.py` 里 import 它，
会与 `factor_registry` 形成**循环 import**。
`get_factor()` 在注册表为空时会给出可操作的提示。

**加一个新因子 = 加一个文件 + 在本文件加一行 import**（承 A2）。
"""
from . import ret20, ret60, vol20  # noqa: F401

__all__ = ["ret20", "ret60", "vol20"]
