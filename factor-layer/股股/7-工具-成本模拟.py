"""
股股 · 卫星桶模拟：$6,000 + $2/笔
================================================
目的：把"成本"和"收益分布"从嘴上，变成能看见的数字。
不构成投资建议。所有参数都是"假设"，不是"事实"。

两部分：
  A. 成本表（确定性）—— 笔数 → 年成本 → 占本金
  B. 收益分布（蒙特卡洛）—— 在"不同胜率/赔率"假设下，一年后剩多少
"""

import numpy as np

E0 = 6000.0        # 卫星桶本金
COST = 2.0         # 每笔往返成本（买 1 + 卖 1）


# ---------------------------------------------------------------
# A. 成本表（确定性，不用模拟）
# ---------------------------------------------------------------
def cost_table():
    print("=" * 62)
    print("A. 成本表：$6,000，$2/笔")
    print("=" * 62)
    print(f"{'年笔数':>6} {'每周':>6} {'年成本':>9} {'占本金':>8}   {'需要多少年亏光'}")
    print("-" * 62)
    for n in [30, 60, 90, 120, 150, 250, 300, 500, 990, 3000]:
        c = n * COST
        pct = c / E0
        weeks = n / 52
        years = E0 / c if c > 0 else float("inf")
        print(f"{n:>6} {weeks:>6.1f} ${c:>8.0f} {pct:>7.1%}   {years:>6.1f} 年")
    print()
    print("读法：$2/笔看着小，但 990 笔/年 = 33%（他上次的水平）。")
    print()


# ---------------------------------------------------------------
# B. 收益分布（蒙特卡洛）
#    每笔：以概率 p 赚 R_win 个 R，否则亏 1 个 R
#    单笔风险 = 账户 × risk_pct（Minervini 的做法：约 1%）
#    R = 单笔风险金额；止损 8% → 仓位 ≈ risk_pct / 8%
# ---------------------------------------------------------------
def simulate(n_trades, p, R_win, risk_pct=0.01, cost=COST,
             E0=E0, n_paths=20000, seed=42):
    rng = np.random.default_rng(seed)
    win = rng.random((n_paths, n_trades)) < p
    mult = np.where(win, R_win, -1.0)
    E = np.full(n_paths, E0, dtype=float)
    for t in range(n_trades):
        risk = E * risk_pct
        E = E + mult[:, t] * risk - cost
        E = np.maximum(E, 0.0)
    return E


def describe(E):
    q = np.percentile(E, [5, 25, 50, 75, 95])
    return {
        "p5": q[0], "p25": q[1], "median": q[2], "p75": q[3], "p95": q[4],
        "loss_prob": float((E < E0).mean()),
        "blowup": float((E < E0 * 0.2).mean()),
        "mean": float(E.mean()),
    }


def report(tag, n_trades, p, R_win):
    E = simulate(n_trades, p, R_win)
    d = describe(E)
    exp_r = p * R_win - (1 - p) * 1.0
    print(f"{tag:<26} 每笔期望={exp_r:+.2f}R  "
          f"5%={d['p5']:>6.0f}  中位={d['median']:>6.0f}  95%={d['p95']:>7.0f}  "
          f"亏损概率={d['loss_prob']:>5.1%}  剩<20%概率={d['blowup']:>5.1%}")
    return d


def sensitivity():
    print("=" * 118)
    print("B. 收益分布（20,000 条路径，一年；单笔风险 1% of 账户；成本 $2/笔）")
    print("=" * 118)
    print("假设：每笔 = 以概率 p 赚 R_win 个 R，否则亏 1 个 R。")
    print("     R = 单笔风险金额 = 账户 × 1%（即：止损 8% 时，仓位 ≈ 账户的 12.5%）")
    print()
    for n in [60, 90]:
        print(f"--- 一年 {n} 笔 ---")
        for p in [0.50, 0.45, 0.40, 0.35, 0.30]:
            for R in [1.0, 1.5, 2.0, 3.0]:
                report(f"p={p:.2f}, 赢={R:.1f}R", n, p, R)
        print()
    print("注意：这不是预测。它只回答一个问题——")
    print("     『如果』你的胜率和赔率是这些数，一年后会怎样。")
    print("     而这些数，是你猜的，不是你知道的。")
    print()


# ---------------------------------------------------------------
# C. 成本的边际影响：同样参数，$2/笔 vs 0 成本
# ---------------------------------------------------------------
def cost_impact():
    print("=" * 118)
    print("C. 成本到底吃掉多少？（同样假设，对比 有成本 vs 零成本）")
    print("=" * 118)
    for n in [60, 90, 150, 300]:
        a = simulate(n, 0.40, 3.0, cost=0.0)
        b = simulate(n, 0.40, 3.0, cost=COST)
        da, db = describe(a), describe(b)
        print(f"{n:>4} 笔/年 | 中位终值：零成本 ${da['median']:>7.0f}  →  $2/笔 ${db['median']:>7.0f}"
              f"  （成本吃掉 {da['median'] - db['median']:>6.0f} 美元，"
              f"占本金 {(da['median'] - db['median']) / E0:>5.1%}）")
    print()


if __name__ == "__main__":
    cost_table()
    sensitivity()
    cost_impact()
