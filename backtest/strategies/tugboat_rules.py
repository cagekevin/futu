"""**Tugboat 的规则登记** —— 他的每一条规则，连带**出处 / 单位 / 阈值**。

## 这个文件存在的理由（三个我实际犯过的错）

### 1. 出处漂移

`rise_12m_min = 0.50` 是**他的原文**（§6.1 行 490「过去 12 个月至少涨过 50%」），
但我在文档里把它算进「**我定的 6 个阈值**」—— 于是我在 `--free-params` 里
**动了它的原文数字**，还拿它当"结论关于我的近似"的**主证据**。

⇒ 现在出处是 **`Rule.source`**，登记时强制校验 ⇒ **不可能再漂移**。

### 2. 该登记的规则没登记

我第一版**漏了他明写的**：`ADR% ≥ 2.5%`（§10.6①）、`RS ≥ 90`（§8.1 要点2）、
`§11.5 五条 RSI`、`盘整 2 周–2 个月`（§8.1②）。

⇒ 现在**规则清单就是这个文件**；漏一条，`check_coverage()` 会报。

### 3. 把并列的规则集当成串联

我用 `vcp_filter: bool` 把 **§6.1 通用条件**与 **§7.1 VCP 条件**连乘 ⇒ 候选归零。
他自己说 **VCP 只是三种形态里的"中间盘整"那一种**。

⇒ 现在**三个 `RuleSet` 并列**（`BASE` / `VCP` / `RSI_TIGHT`），跑法是「**选一个**」。

## ⚠️ 本文件**只登记，不计算**

计算在 `tugboat_breakout.py`（用 `key` 关联）。
`check_coverage()` 保证两边**一一对应** —— 这是"审计必须穷举"那条设计的落点。
"""
from __future__ import annotations

from rules import Rule, RuleSet, Source

__all__ = [
    "BASE", "RSI_TIGHT", "RULESETS", "VCP", "check_coverage", "rule_values",
]

_O = Source.ORIGINAL
_I = Source.INFERRED
_C = Source.CHOSEN

# ══════════════════════════════════════════════════════════════════════════
# ① `BASE` —— §6.1「Tight Range + 四条件」+ 他的两个**选股过滤器**
# ══════════════════════════════════════════════════════════════════════════

BASE = RuleSet(
    name="base",
    label="§6.1 通用突破条件（+ §10.6① / §8.1 选股过滤器）",
    note=(
        "这是他**通用**的入场条件。VCP 六要点**不在这一套里** —— "
        "那是三种形态之一的标准，见 `VCP`（并列，不是叠加）。"),
    rules=(
        Rule(key="tight_range", label="T1 右侧紧密低幅盘整（5 日区间）",
             source=_C, unit="比例", value=0.06,
             note="原文只说「够紧」，**没给数字** ⇒ 这个 6% 是我定的"),
        Rule(key="ma_converge", label="T2 均线平行**或开始收拢**",
             source=_I, where="§6.1 行 483", unit="比例", value=0.02,
             note="★ **条件是原文（「平行或开始收拢」），但阈值 2% 是我定的** ⇒ "
                  "按「阈值由谁定」归类为**推断**，不标「原文」"
                  "（标原文会让读者以为 2% 也是他给的）"),
        Rule(key="above_200ma", label="T3 在 200 日均线之上（上升趋势）",
             source=_O, where="§6.1 行 488", unit="bool"),
        Rule(key="near_support", label="T4 盘整靠近支撑位",
             source=_I, where="§6.1 行 489", unit="ATR倍数", value=1.0,
             note="原文只说「靠近支撑」；我据 §11.5 条件②"
                  "（「到 5 条均线任一条的误差 ≤1 个 ATR」）实现"),
        Rule(key="rise_12m", label="T5 过去 12 个月至少涨过 50%",
             source=_O, where="§6.1 行 490", unit="比例", value=0.50,
             note="★ **这个 50% 是他的原文，不是我定的** "
                  "（我曾在文档里标反，见模块 docstring）"),
        Rule(key="no_dump", label="T6 最近没有「高动能下跌」",
             source=_I, where="§6.1 行 491", unit="ADR倍数", value=2.0,
             note="★ 原文只说「没有突兀的高动能下跌」，**没给倍数** ⇒ "
                  "按「阈值由谁定」归类为**推断**（2×ADR 是我定的）"),
        Rule(key="not_overextended", label="T7 不过度延伸",
             source=_O, where="§11.4 行 1569", unit="ATR倍数", value=10.0,
             note="★ 出处是 §11.4：「ATR% multiple from 50MA 超过 **10 倍 ATR**"
                  " ⇒ 过度延伸」。我第一版写的是「距 50MA ≤15% 价格」—— "
                  "**那是另一个东西**（ATR 倍数 vs 价格比例）"),
        Rule(key="ma200_rising", label="T8 200MA 本身不向下",
             source=_O, where="§11.5 行 1569", unit="bool",
             note="★ 必须比 **200MA 本身**，不是「离 200MA 的距离」"
                  "（后者是我写错过的，56% 的格子结论不同）"),
        Rule(key="adr_floor", label="选股过滤器：ADR% ≥ 2.5%",
             source=_O, where="§10.6① 行 1448", unit="比例", value=0.025,
             note="★ 他明写的**选股过滤器**：「< 2.5% 直接排除」"
                  "（§10.6①、§11.5 筛选器、Deepvue 示范三处都写了）。"
                  "我第一版**完全没实现**"),
        Rule(key="rs_rank", label="选股过滤器：相对强度 ≥ 90",
             source=_O, where="§8.1 要点2 行 931", unit="百分位", value=0.90,
             note="原文「RS > 97（**他有时放宽到 > 90**）」⇒ 取放宽值 90。"
                  "参考系：**池内截面百分位**（与 MarketSmith 的"
                  "「全市场百分位」**不等价**，我们更严 —— 池子本身是强票）"),
        Rule(key="breakout", label="区间突破触发（收盘 > 前 5 日最高）",
             source=_O, where="§6.1 行 470", unit="bool",
             note="★ 入场③「偷步买」**不要求**这一条（那是它的定义）"),
        Rule(key="stop_width", label="止损距离 ≤ 1.5 倍 ADR",
             source=_O, where="§10.6② 行 1448", unit="ADR倍数", value=1.5,
             note="★ 原文：「止损距离：控制在 **1–1.5 倍 ADR** 之内」"
                  "（§8.1④「止损幅度不应该大于该股票的 ADR」同义）。"
                  "我第一版因为**量纲写错**（美元 ≤ 比例）而误判成"
                  "「日线做不到」—— **那是我的 bug，不是他的规则做不到**"),
    ),
)

# ══════════════════════════════════════════════════════════════════════════
# ② `VCP` —— §7.1 VCP 六要点（**替代** BASE，不是叠加）
# ══════════════════════════════════════════════════════════════════════════

VCP = RuleSet(
    name="vcp",
    label="§7.1 VCP 六要点（三种形态里的「中间盘整」）",
    note=(
        "★ **这是并列的另一套，不是 BASE 之上再加六条。**\n"
        "他自己说：VCP 只是三种形态里的**中间盘整（VCP Cheat）**那一种"
        "（§6.1 行 494–500）。\n"
        "⚠️ 我第一版把它当「基础 + 附加」**连乘** ⇒ 16 条单独通过率相乘 = **4.0e-10**"
        " ⇒ 满窗口 0.0001 个候选。**每条都不荒唐**（最稀的 1.43%），但连乘必然归零。"),
    rules=(
        Rule(key="above_150ma", label="① 股价在 150 日线之上",
             source=_O, where="§7.1 行 703", unit="bool"),
        Rule(key="rs_rank", label="② 相对强度 ≥ 90",
             source=_O, where="§7.1 行 704", unit="百分位", value=0.90,
             note="原文「MarketSmith 的 RS 值，**他一般选 90 以上**」"),
        Rule(key="near_52w_high", label="③ 接近 52 周新高（不低于 15%）",
             source=_O, where="§7.1 行 705", unit="比例", value=0.85,
             note="原文「最好**不低于 52 周新高的 15%**」⇒ `close/high_52w ≥ 0.85`"),
        Rule(key="contractions", label="④ 波幅收缩**三次或以上**",
             source=_O, where="§7.1 行 706", unit="倍数", value=3,
             note="原文「波幅收缩，**最好三次或以上**」"),
        Rule(key="final_range", label="⑤ 收缩到最后，股价波动 < 1%",
             source=_O, where="§7.1 行 707", unit="比例", value=0.01,
             note="★ 原文没说「当日」还是「多日」。取**当日振幅**"
                  "（多日极差 ≤1% = 两周总共动不到 1% ⇒ 那是**停牌**，语义上不成立）"),
        Rule(key="volume_decline", label="⑥ 最后配合成交量下跌",
             source=_O, where="§7.1 行 708", unit="倍数", value=1.0,
             note="`vol_ratio10_50 < 1`（盘整末期缩量）"),
        Rule(key="breakout", label="区间突破触发",
             source=_O, where="§6.1 行 470", unit="bool"),
    ),
)

# ══════════════════════════════════════════════════════════════════════════
# ③ `RSI_TIGHT` —— §11.5「RSI 紧密盘整」五条（他自己说"最实用、可以直接抄"）
# ══════════════════════════════════════════════════════════════════════════

RSI_TIGHT = RuleSet(
    name="rsi_tight",
    label="§11.5 RSI 紧密盘整（第三方工具口径，他说「可以直接抄」）",
    note=("★ 我第一版**完全没实现**这一套。它已在 `evaluate/placebo/` 里单独做过"
          "随机对照（见 `关于策略/12`：排位 53.2%，无可辨识优势）—— "
          "这里把它作为**并列的第三个 RuleSet** 接进来。"),
    rules=(
        Rule(key="rsi_change_3d", label="① RSI 3–4 日变化 < 3 且累计 ≤ 5",
             source=_O, where="§11.5 条件①", unit="倍数", value=3.0),
        Rule(key="atr_pct_floor", label="④ ATR / 收盘 > 2.5%",
             source=_O, where="§11.5 条件④", unit="比例", value=0.025),
        Rule(key="rsi_above_50", label="⑤ RSI > 50",
             source=_O, where="§11.5 条件⑤", unit="倍数", value=50.0),
        Rule(key="near_ma", label="② 贴近 5 条均线任一条（≤1 个 ATR）",
             source=_O, where="§11.5 条件②", unit="ATR倍数", value=1.0),
        Rule(key="breakout", label="区间突破触发",
             source=_O, where="§6.1 行 470", unit="bool"),
    ),
)

#: 全部并列的规则集 —— **跑法是"选一个"**（不是给 base 加开关）。
RULESETS: dict[str, RuleSet] = {r.name: r for r in (BASE, VCP, RSI_TIGHT)}


def rule_values(rs: RuleSet) -> dict[str, object]:
    """取出某套规则里**所有 `value`**（供构造参数用）。

    ⇒ 阈值只有一处来源（`Rule.value`），不再散落在 `DEFAULTS` 与文档里。
    """
    return {r.key: r.value for r in rs.rules if r.value is not None}


def check_coverage(rs: RuleSet, implemented: set[str]) -> None:
    """★ **审计必须穷举** —— 规则与实现必须一一对应。

    ## 为什么需要它（一次真实的教训）

    我的条件对账工具里有这么一句：

    ```python
    if k in prod:      # ← 只比"两边都有"的键
        ...
    ```

    ⇒ 它**静默跳过**了生产侧独有的键 —— 而**唯一有 bug 的那一条**
      （`止损宽度`）恰好就是被跳过的那一条。
      「14 条逐格一致」实际只覆盖了 9 条。

    ⇒ 这里改成**缺一个就报错**。
    """
    declared = {r.key for r in rs.rules}
    missing_impl = sorted(declared - implemented)
    extra_impl = sorted(implemented - declared)
    if missing_impl:
        raise ValueError(
            f"规则集 {rs.name!r} 里这些规则**没有实现**：{missing_impl}\n"
            f"承 R5：审计必须穷举 —— 审不了就报错，**不许静默跳过**"
            f"（上一版就是靠 `if k in prod` 跳过了唯一有 bug 的那条）")
    if extra_impl:
        raise ValueError(
            f"实现里有这些 key **没在规则集里登记**：{extra_impl}\n"
            f"⇒ 要么补进 {rs.name!r}，要么从实现里删掉 —— 不许有'无出处的条件'")
