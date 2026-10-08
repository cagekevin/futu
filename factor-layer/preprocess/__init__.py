"""M4 预处理（preprocess）—— 原始因子 → 可横截面比较的标准因子。

承 PRD `factor-layer/docs/PRD/01-截面因子-PRD-2026-10-06.md` §五 M4。

| 文件 | 职责 |
|---|---|
| `preprocess_pipeline.py` | 配置 + **顺序写死**的编排 + `PreprocessedFactor`（**唯一入口**）|
| `factor_winsorize.py` | ① 去极值（`mad`）|
| `factor_impute.py` | ② 补缺（`cross_section_median`）|
| `factor_standardize.py` | ③ 标准化（`zscore`）|
| `factor_neutralize.py` | ④ 中性化（逐日 OLS 取残差）|

**顺序（承 T1，写死不可换）**：
`去极值 → 补缺 → 标准化 → 中性化`

依赖方向（**单向**，承 PRD §五 M4 State 2.4）：
`{四个算法模块} ← preprocess_pipeline`（它是**唯一**同时 import 它们的地方）

⚠️ 本模块**零 IO**：输入是 M3 的值 + M2 的暴露，输出是内存对象，不落盘。
"""
