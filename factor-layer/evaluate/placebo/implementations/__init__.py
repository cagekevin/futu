"""R1 选择规则的实现（**导入即注册**，承 H2：加规则 = 1 文件 + 1 行）。

加规则时在这里加一行，**框架其余零改动**：

    from . import <规则模块>   # noqa: F401

## 已实现的规则

| 名字 | 规则 | 出处 |
|---|---|---|
| `rsi_tight_consolidation` | **RSI 紧密盘整**（5 个技术条件）| `关于策略/Tugboat/9-资料-TradingTugboat.md` §11.5 |

⚠️ 该规则的 `name` 默认是 `rsi_tight_consolidation`；
跑**参数变体**时要显式给别的 `name`（注册表需要不同的键），
并把每个变体当成**独立实验**报（多个 p 值一起过 BH，承 C5）。
"""
from . import rsi_tight_consolidation  # noqa: F401

__all__ = ["rsi_tight_consolidation"]
