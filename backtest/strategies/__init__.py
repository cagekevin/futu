"""策略登记处：**导入即注册**。

加一个新策略 / 新指标 = 在这里加一行 import。
框架其余部分（取数 / PnL / 指标 / 判决）一行不改。
"""
from strategies import momentum_strategy  # noqa: F401
from strategies import regime_gated_momentum_strategy  # noqa: F401
