"""M5 评估（evaluate）—— 判定因子有没有截面选股能力。

承 PRD `factor-layer/docs/PRD/01-截面因子-PRD-2026-10-06.md` §五 M5。

| 文件 | 职责 |
|---|---|
| `forward_return.py` | 标签 `log(open[t+2]/open[t+1])`（**全仓唯一实现点**，承 Q3）|
| `metrics/ic.py` | RankIC 序列 / ICIR / IC 胜率 / t 检验 |
| `metrics/quantile_returns.py` | 等频分箱 / 分组收益 / 多空 / 单调性 |
| `metrics/turnover.py` | 换手 |
| `judgement.py` | BH 校正 + **判决出口**（无裸判路径，承 Q5/J3）|
| `evaluator.py` | 组装 —— M5 对外唯一入口 `evaluate()`（**第 6 个文件，已声明偏离**）|

依赖方向（**单向**）：
`{forward_return, metrics/*, judgement} ← evaluator`

⚠️ 本模块**零 IO**、**只读**（承 J1：评估不许修改因子值）。
"""
