"""M2 暴露（exposure）—— 构造 `date × symbol` 的风险暴露矩阵，供 M4 中性化使用。

承 PRD `factor-layer/docs/PRD/01-截面因子-PRD-2026-10-06.md` §五 M2。

| 文件 | 职责 |
|---|---|
| `exposure_types.py` | 数据结构 `ExposureSet` + 契约常量（**零依赖**）|
| `size_exposure.py` | `size = log(市值)` + 历史反推（**必须用 raw close**）|
| `industry_exposure.py` | 行业标签 + **逐日**低频分组（归 `other`）|
| `exposure_coverage.py` | 覆盖率显形（含 `absorbed`，**必须为 0**）|
| `exposure_builder.py` | 组装 —— M2 对外唯一入口 `read_exposures()` |

**取数**：一律经 `panel/provide_reader.py`（承 M1-1：本层唯一取数入口）。
"""
