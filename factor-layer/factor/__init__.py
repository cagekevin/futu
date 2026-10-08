"""M3 因子（factor）—— 注册表 + 因子实现，产出**原始**因子值。

承 PRD `factor-layer/docs/PRD/01-截面因子-PRD-2026-10-06.md` §五 M3。

| 文件 | 职责 |
|---|---|
| `factor_spec.py` | `FactorSpec`（**六要素**）+ `FactorName`（命名规范）|
| `factor_protocol.py` | `Factor` 协议 + `FactorInput`（严格输入）+ `FactorValues` |
| `factor_registry.py` | 注册表 + `run_factor()`（**唯一入口**）|
| `implementations/` | 每个因子一个文件（**导入即注册**）|

用法：

```python
import factor.implementations                     # 导入即注册
from factor.factor_registry import run_factor
values = run_factor("ret20", panel)               # → FactorValues
```

依赖方向（**单向**，承 PRD §五 M3 State 2.4）：
`factor_spec ← factor_protocol ← factor_registry ← implementations/*`

⚠️ 本模块**零 IO**：输入是 M1 的面板，输出是内存对象，不落盘、不取数。
"""
