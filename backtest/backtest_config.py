"""唯一配置源（承 B1）—— 只放**交易假设**（成本 / 仓位 / 阈值 / 窗口）。

全系统只在**这一处**定义这些数。别处只引用变量，不许再写数字。

根因（B1）：成本率一旦多处硬编码，训练与回测就会各用各的数 —— 这是"口径漂移"，
评分会系统性误判，且两年后无从查起（改了一处、漏了另一处）。

边界：**数据层路径**（venv / data-layer 目录）属**集成细节**，不在这里 ——
它是 `data_source` 自己的事（见 `data_source.py`）。配置源只谈"怎么交易"。
"""
from __future__ import annotations

# ── 成本（训练 / 回测 / 实盘唯一来源，承 B1）────────────────────────────
COST_RATE = 0.0003            # 单边：手续费 0.02% + 滑点 0.01%

# ── 仓位 ────────────────────────────────────────────────────────────────
MIN_TRADE_EXPOSURE = 0.05     # |pos| < 此值 → 空仓（回测/实盘共用）
POSITION_CAP = 1.0

# ── 标签（承 D2）────────────────────────────────────────────────────────
TARGET_HORIZON = 2            # target_ret[t] = log(open[t+2] / open[t+1])

# ── 评估窗口 + 分段一致性 + purge gap（承 V2 / B5）──────────────────────
# ★ EVALUATION_WARMUP 是**唯一**的评估起点：前 warmup 根 bar 不参与**任何**统计
#   （指标 / 前后半段 / 单边占比 / 分段一致性）—— 避免"某处算、某处不算"的口径打架。
#   根因：指标 warm-up 期（策略尚未出信号）若只在部分统计里被排除，年化会被稀释、
#   单边占比会被摊薄 → 恰好在 V3 该抓 beta 时漏判。
EVALUATION_WARMUP = 60        # 前 warmup 根不计（指标 warm-up）
WF_FOLDS = 5                  # 把评估区间均分成几段（数"几段为正"）
WF_GAP = 20                   # 隔离带；必须 >= TARGET_HORIZON

# ── 判定阈值（承 V3）────────────────────────────────────────────────────
MIN_ANN_RET = 0.02            # 年化 < 2% → INVALID
MIN_SHARPE = 0.5              # Sharpe < 0.5 → SUSPICIOUS
MDD_SUSPICIOUS = 0.10         # MDD > 10% → SUSPICIOUS
MDD_INVALID = 0.20            # MDD > 20% → INVALID
MAX_SIDE_RATIO = 0.85         # 单边 > 85% → SUSPICIOUS（疑似 beta）
MIN_TRADES_PER_100_BARS = 0.01  # 每 100 bar 至少 1 笔

# ── 数据（承 D4）────────────────────────────────────────────────────────
MIN_BARS = 300                # 对齐交集的下限

# ── 自检（承 B5：gap 必须按 horizon 标定，不能写死）─────────────────────
# 根因：target_ret 本身是"未来收益"，train 段尾部 gap 根的标签会看到 val 段。
# gap < horizon → 泄漏；所以这里让"导入即报错"，而不是等回测跑出假高分才发现。
assert WF_GAP >= TARGET_HORIZON, (
    f"WF_GAP({WF_GAP}) 必须 >= TARGET_HORIZON({TARGET_HORIZON})（承 B5）"
)
