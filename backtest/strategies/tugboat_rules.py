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

from collections.abc import Mapping

from rules import Rule, RuleSet, Source

__all__ = [
    "ALL_KEYS", "BASE", "PARAM_OF", "RSI_TIGHT", "RULESETS", "VCP",
    "check_coverage", "rule_params", "rule_values",
]

_O = Source.ORIGINAL
_I = Source.INFERRED
_C = Source.CHOSEN

# ══════════════════════════════════════════════════════════════════════════
# ① `BASE` —— §6.1「Tight Range + 四条件」+ 他的两个**选股过滤器**
# ══════════════════════════════════════════════════════════════════════════

BASE = RuleSet(
    name="base",
    label="§6.1 通用突破条件（+ §10.6① / §8.1 选股过滤器 + §10 指标层两条）",
    note=(
        "这是他**通用**的入场条件。VCP 六要点**不在这一套里** —— "
        "那是三种形态之一的标准，见 `VCP`（并列，不是叠加）。"),
    rules=(
        Rule(key="tight_range", label="T1 右侧紧密低幅盘整（5 日区间）",
             source=_C, unit="比例", value=0.06,
             note="原文只说「够紧」，**没给数字** ⇒ 这个 6% 是我定的"),
        Rule(key="ma_converge", label="T2 均线平行**或开始收拢**",
             source=_I, where="§6.1 行 483", unit="比例", value=0.02,
             note="★ **条件是原文（「平行**或**开始收拢」），但阈值 2% 是我定的** ⇒ "
                  "按「阈值由谁定」归类为**推断**，不标「原文」"
                  "（标原文会让读者以为 2% 也是他给的）。"
                  "⚠️ **实现必须是「或」（`|`）** —— 曾经写成「且」（`&`），"
                  "比原文更严（治 TD-05-29）；`_impl_masks` 里有对账注释。"),
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
        Rule(key="rs_rank", label="选股过滤器：相对强度 ≥ 85",
             source=_C, where="§8.1 要点2 行 931", unit="百分位", value=0.85,
             note="原文「RS > 97（**他有时放宽到 > 90**）」。"
                  "★ **我们取 0.85**（2026-10-09，用户决定）："
                  "参考系是**池内**截面百分位，与 MarketSmith 的「全市场百分位」"
                  "**不等价** —— 池子本身是「从全市场挑出来的强票」"
                  "⇒ 池内前 10% 在**绝对强度上更严**，故放宽；"
                  "取 85（而非 80/90）是**在已实测的两端之间取中**，"
                  "实测对照见 `daily/架构日志/05-跨区-策略验证Tugboat-2026-10-09.md` §二十七。"),
        # ── §10 技术指标里**直接进选股**的两条（治 TD-05-38 的前两条）──
        # ⚠️ **§10.2「多头排列 10>20>50>200」试过，撤回了**（2026-10-09 实测）：
        #    它与本规则集的 **T2（紧密盘整：均线平行或收拢）语义互斥** ——
        #    盘整时均线必然**收拢**，而「多头排列」要求均线**有序发散**，两条不可能同时成立。
        #    漏斗实测：加上它 ⇒ 累计从 **648 格直接归零**（单独通过率 15.32%，正是压死的那一刀）
        #    ⇒ **一条票都选不出来**。
        #    ⇒ 它是 §10.1 四层框架里**趋势层**的描述（"做多成功率较高"），
        #      **不是**一条入场筛选器；趋势那一层已由 T3（200MA 之上）+ T8（200MA 不向下）表达。
        #    （弯路留痕：这是我读错原文的一次，证据不删。）
        Rule(key="rsi_above_50", label="§10.8② 突破确认：RSI > 50",
             source=_O, where="§10.8 行 1491", unit="倍数", value=50.0,
             note="原文「**RSI > 50 = 突破交易的核心确认讯号**：紧密盘整 + RSI 站上 50 = "
                  "**双重确认**（**光看紧密盘整不够 —— 假突破太多**）」。"
                  "⚠️ 原来只把它放进 `RSI_TIGHT`，**`BASE` 没有** —— 而这一条讲的正是"
                  "**突破交易**的确认，不是 RSI 那一套的。"),
        Rule(key="adr_contracting", label="§10.6 进阶：ADR% 在收缩（突破前兆）",
             source=_C, where="§10.6 行 1461", unit="倍数", value=20,
             note="原文「**ADR% 从 5% 收缩到 2% = 波动收敛、能量累积 → 突破前兆**」。"
                  "⚠️ 原文**没给比较窗口**（「把回溯期设成 3 天会更敏感」说的是 ADR 的**计算窗口**）"
                  "⇒ **20 天这个数是我定的**（与 `adr20` 的窗口一致），故标「我定」而非「原文」。"),
        Rule(key="breakout", label="区间突破触发（收盘 > 前 5 日最高）",
             source=_O, where="§6.1 行 470", as_of="today", unit="bool",
             note="★ 入场③「偷步买」**不要求**这一条（那是它的定义）"),
        Rule(key="stop_width", label="止损距离 ≤ 1.5 倍 ADR",
             source=_O, where="§10.6② 行 1448", as_of="today", unit="ADR倍数", value=1.5,
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
        Rule(key="rs_rank", label="② 相对强度 ≥ 85",
             source=_C, where="§7.1 行 704", unit="百分位", value=0.85,
             note="原文「MarketSmith 的 RS 值，**他一般选 90 以上**」。"
                  "★ 我们取 **0.85**（同 `BASE`，理由见那一处：参考系是池内而非全市场）"),
        Rule(key="near_52w_high", label="③ 接近 52 周新高（放宽版：≥ 70%）",
             source=_C, where="§7.1 行 705", unit="比例", value=0.70,
             note="原文「**最好**不低于 52 周新高的 15%」⇒ 原读法 `close/high_52w ≥ 0.85`。"
                  "★ **「最好」不是「必须」**（逐字依据：原文用词）⇒ 2026-10-09 放宽到 0.70"
                  "（距高点 30%），并把出处从「原文」改成「**我定**」——"
                  "因为 0.70 这个数是**我拍的**，只有「最好」两个字是他的。"),
        Rule(key="contractions", label="④ 波幅收缩（放宽版：≥ 2 次）",
             source=_C, where="§7.1 行 706", unit="倍数", value=2,
             note="原文「波幅收缩，**最好三次或以上**」⇒ 原读法 `≥ 3`。"
                  "★ 同上：**「最好三次」⇒ 3 是最佳，2 次可接受** ⇒ 2026-10-09 放宽到 2，"
                  "出处标「**我定**」（2 这个数是我拍的）。"),
        Rule(key="final_range", label="⑤ 收缩到最后，股价波动 < 2%（日线近似）",
             source=_C, where="§7.1 行 707", unit="比例", value=0.02,
             note="原文「收缩到最后，股价波动 **< 1%**」。\n"
                  "★ **2026-10-09 取证后改**（原实现 = 当日振幅 ≤ **1%**）：\n"
                  "  · 日线振幅**中位 3.80%**（p10 = 1.71%）⇒ 「≤1%」是**低于 p10 的罕见一天**，"
                  "单条通过率仅 **1.43%**，且它是 VCP 的**唯一瓶颈**"
                  "（放宽 ③④ 后累计 12,946 格，⑤ 一过只剩 **18**，吃掉 **99.86%**）。\n"
                  "  · 备选读法**全都更严**（3 天每日 ≤1% = 0.53%；3 天区间 ≤1% = 0.45%）"
                  "⇒ 「多日」不是放宽。\n"
                  "  · ⇒ **最可能的解释：他是日内交易者**（入场看 1/5/30 分钟），"
                  "「波动 < 1%」说的是**分钟级**波动 —— **日线里没有这个信息**，"
                  "拿日线振幅量它**必然过严**。\n"
                  "  · ⇒ 按「日线等价的极紧」（中位 3.80% 的一半）取 **2%**，"
                  "出处改标「**我定**」：**2% 这个数是我拍的**，原文只有「< 1%」。"),
        Rule(key="volume_decline", label="⑥ 最后配合成交量下跌",
             source=_O, where="§7.1 行 708", unit="倍数", value=1.0,
             note="`vol_ratio10_50 < 1`（盘整末期缩量）"),
        Rule(key="breakout", label="区间突破触发",
             source=_O, where="§6.1 行 470", as_of="today", unit="bool"),
    ),
)

# ══════════════════════════════════════════════════════════════════════════
# ③ `RSI_TIGHT` —— §11.5「RSI 紧密盘整」五条（他自己说"最实用、可以直接抄"）
# ══════════════════════════════════════════════════════════════════════════

RSI_TIGHT = RuleSet(
    name="rsi_tight",
    label="§11.5 RSI 紧密盘整（第三方工具口径，他说「可以直接抄」）",
    note=("★ 我第一版**完全没实现**这一套。它已在 `evaluate/placebo/` 里单独做过"
          "随机对照（曾单独测过：排位 53.2%，无可辨识优势；过程文档已删）—— "
          "这里把它作为**并列的第三个 RuleSet** 接进来。"),
    rules=(
        Rule(key="rsi_change_daily", label="①a RSI **每日**变化 < 3（最近 3 天）",
             source=_O, where="§11.5 条件①", unit="倍数", value=3.0,
             note="★ 原文是**两个条件**，第一版只实现了「累计」那一半（治 TD-05-09）"),
        Rule(key="rsi_change_cum", label="①b RSI **累计**变化 ≤ 5（3 天）",
             source=_O, where="§11.5 条件①", unit="倍数", value=5.0),
        Rule(key="atr_pct_floor", label="④ ATR / 收盘 > 2.5%",
             source=_O, where="§11.5 条件④", unit="比例", value=0.025),
        Rule(key="rsi_above_50", label="⑤ RSI > 50",
             source=_O, where="§11.5 条件⑤", unit="倍数", value=50.0),
        Rule(key="near_ma", label="② 贴近 5 条均线任一条（≤1 个 ATR）",
             source=_O, where="§11.5 条件②", unit="ATR倍数", value=1.0),
        Rule(key="breakout", label="区间突破触发",
             source=_O, where="§6.1 行 470", as_of="today", unit="bool"),
    ),
)

#: 全部并列的规则集 —— **跑法是"选一个"**（不是给 base 加开关）。
RULESETS: dict[str, RuleSet] = {r.name: r for r in (BASE, VCP, RSI_TIGHT)}

#: **所有规则集里出现过的 key 的并集** —— 供 `check_coverage` 判"无出处的条件"。
#:
#: ⚠️ 实现里出现、但**任何规则集都没登记**的 key ⇒ 报错。
#:    那种条件事后**没人能审它**（不知道它从哪来、阈值是谁定的）。
ALL_KEYS: frozenset[str] = frozenset(
    r.key for rs in RULESETS.values() for r in rs.rules)


def rule_values(rs: RuleSet) -> dict[str, object]:
    """取出某套规则里**所有 `value`**（键是**规则键**）—— 供审计/对账用。

    ⚠️ 要**构造参数**请用 `rule_params()`：那个会把键换成 `DEFAULTS` 的参数名。
    """
    return {r.key: r.value for r in rs.rules if r.value is not None}


#: ★ **规则键 → `DEFAULTS` 里的参数名**（治 TD-05-29 的顺带发现）。
#:
#: ## 为什么需要它
#:
#: 阈值的**值**写在 `Rule.value`（本文件，**带出处**），而**参数名**写在
#: `tugboat_breakout.DEFAULTS` —— 两边键名不同（`rs_rank` vs `rs_min`），
#: 于是**同一个数字在两处各写一遍**（例如 `0.90` 出现过两次）。
#: ⇒ 这张表把它们对上，`DEFAULTS` 的阈值部分改成**从 `Rule.value` 派生**
#:   （见 `tugboat_breakout._THRESHOLDS`）⇒ **值只有一处**。
#:
#: ⚠️ 穷举守卫：`Rule` 有 `value` 却不在本表里 ⇒ `rule_params()` **报错**
#:   （不许"悄悄多一个没人对得上的阈值"）。
PARAM_OF: Mapping[str, str] = {
    # BASE
    "tight_range": "tight_range_max",
    "ma_converge": "ma_converge_max",
    "rise_12m": "rise_12m_min",
    "no_dump": "no_dump_adr",
    "not_overextended": "overextend_max",
    "adr_floor": "adr_floor",
    "rs_rank": "rs_min",
    "stop_width": "stop_width_adr",
    # VCP
    "near_52w_high": "near_high_min",
    "contractions": "contractions_min",
    "final_range": "final_range_max",
    "volume_decline": "volume_decline_max",
    # §10 指标层（治 TD-05-38）
    "adr_contracting": "adr_contract_days",
    # RSI_TIGHT
    "rsi_change_daily": "rsi_daily_change_max",
    "rsi_change_cum": "rsi_cum_change_max",
    "atr_pct_floor": "atr_pct_min",
    "rsi_above_50": "rsi_min",
    "near_ma": "near_ma_max_atr",
    # ⚠️ `near_support`（BASE 的 T4）与 `near_ma`（RSI_TIGHT 的 ②）是**同一个阈值**
    #    ⇒ 映射到同一个参数名（`rule_params` 允许，值必须一致）。
    "near_support": "near_ma_max_atr",
}


def rule_params(*rulesets: RuleSet) -> dict[str, object]:
    """把若干 `RuleSet` 的**阈值**摊成 `DEFAULTS` 用的 `{参数名: 值}`。

    ⇒ **阈值只有一处来源**（`Rule.value`，带出处），`DEFAULTS` 不再抄一份。
    同一个参数名在两个规则集里值不同 ⇒ **报错**（那说明它们本该是两个参数）。
    """
    out: dict[str, object] = {}
    for rs in rulesets:
        for r in rs.rules:
            if r.value is None:
                continue
            if r.key not in PARAM_OF:
                raise ValueError(
                    f"规则 {r.key!r} 有阈值 {r.value!r}，但 `PARAM_OF` 里没有它的参数名"
                    " —— 承 R5：阈值必须能对到 `DEFAULTS` 的参数上（穷举，不许漏）")
            name = PARAM_OF[r.key]
            if name in out and out[name] != r.value:
                raise ValueError(
                    f"参数 {name!r} 在两个规则集里值不同：{out[name]!r} vs {r.value!r}"
                    " ⇒ 那说明它们本该是两个参数")
            out[name] = r.value
    return out


def check_coverage(rs: RuleSet, implemented: set[str],
                   *, all_declared: set[str] | None = None) -> None:
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
    universe = all_declared if all_declared is not None else declared
    missing_impl = sorted(declared - implemented)
    if missing_impl:
        raise ValueError(
            f"规则集 {rs.name!r} 里这些规则**没有实现**：{missing_impl}\n"
            f"承 R5：审计必须穷举 —— 审不了就报错，**不许静默跳过**"
            f"（上一版就是靠 `if k in prod` 跳过了唯一有 bug 的那条）")
    unknown = sorted(implemented - universe)
    if unknown:
        raise ValueError(
            f"实现里有这些 key **在任何规则集里都没登记**：{unknown}\n"
            f"⇒ 要么补登记（连同出处），要么从实现里删掉 —— "
            f"不许有「无出处的条件」（那种条件事后没人能审它）")
