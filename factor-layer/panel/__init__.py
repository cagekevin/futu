"""M1 面板（panel）—— 截面数据结构的唯一定义 + 唯一取数入口 + 按日票池切片。

承 PRD `factor-layer/docs/PRD/01-截面因子-PRD-2026-10-06.md` §五 M1。

| 文件 | 职责 |
|---|---|
| `panel_types.py` | 数据结构 `CrossSectionPanel` + 契约常量（**零依赖**）|
| `provide_reader.py` | **唯一**取数入口（跨进程 `provide.cli`）|
| `panel_convert.py` | 长表 ⇄ 宽表 |
| `stock_universe.py` | 每日票池切片（**不做**标的类型判定）|
| `panel_builder.py` | 组装 —— M1 对外唯一入口 `read_panel()` |

依赖方向（**单向**，承 PRD §五 State 2.4）：
`panel_types ← {panel_convert, provide_reader, stock_universe} ← panel_builder`
"""
