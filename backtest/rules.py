"""**规则登记** —— 治「出处写在注释里、文档手写」与「并列规则集用布尔开关」这两类错。

## 为什么需要这个文件（两次真实的教训）

### 教训 1：出处会漂移

`rise_12m_min = 0.50`：

- **代码注释**写的是「他『过去 12 个月至少涨过 50%』」⇒ **他的原文**
- **文档**却把它算进「**我定的** 6 个阈值」

⇒ 于是我在 `--free-params` 里**动了它的原文数字**，还拿它当"结论关于我的近似"的**主证据**。

**根因**：出处**写了两遍**（注释一遍、文档一遍），必然漂移。

### 教训 2：布尔开关表达不了"并列"

我写了 `vcp_filter: bool`，于是把 **§6.1 通用条件** 与 **§7.1 VCP 条件** 当成"基础 + 附加"**连乘**：

```
16 条各自通过率相乘 = 4.0e-10  ⇒  满窗口 0.0001 个候选
```

**可他自己说得很清楚：VCP 只是三种形态里的"中间盘整"那一种** ⇒ 两套是**并列**的。

**根因**：布尔开关**只能表达"加/不加"**，表达不了"**二选一**"。

## 本文件的作用

| 做法 | 治什么 |
|---|---|
| `Rule.source` 是**数据**（不是注释）| 出处只有一处 ⇒ 不可能漂移 |
| 文档 / 表头 / `--free-params` 分组 **从 `Rule` 自动生成** | 手写的地方**全部消失** |
| `RuleSet` 是**对象**（`BASE` / `HTF` / `VCP`…）| 跑法是"**选一个 RuleSet**"，不是"给 base 加开关" ⇒ **连乘不可能** |
| `validate()` 强制：`原文` 必须有 `where`、`我定` 必须有 `value` | 想含糊其辞**登记不进去** |
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterator

__all__ = ["ALLOWED_UNITS", "Rule", "RuleSet", "Source", "render_rule_table"]

#: 允许的单位 —— **必须显式**（承 R1：裸数字没有单位是本项目最贵的一类错）。
#:
#: | 单位 | 含义 |
#: |---|---|
#: | `bool` | 布尔条件（无阈值）|
#: | `美元` | 价格 / 价格距离 |
#: | `比例` | `0.026` = 2.6%（ADR%、区间幅度、涨幅…）|
#: | `ATR倍数` | 以 `atr14` 为单位 |
#: | `ADR倍数` | 以 `adr_pct20` 为单位（他的止损口径）|
#: | `倍数` | 无量纲比值（缩量比、RSI 值…）|
#: | `百分位` | `0`–`1` 的截面排名 |
ALLOWED_UNITS: tuple[str, ...] = (
    "bool", "美元", "比例", "ATR倍数", "ADR倍数", "倍数", "百分位",
)


class Source(StrEnum):
    """这条规则的出处 —— **决定它能不能被当成"我的近似"来质疑**。"""

    ORIGINAL = "原文"    # 他资料里明写的（`where` 必填）
    INFERRED = "推断"    # 我据原文推出来的（如"盘整态看昨天"）
    CHOSEN = "我定"      # 他确实没给（`value` 必填）


class RuleError(ValueError):
    """登记不合法。**宁可炸，也不让含糊的规则混进去。**"""


@dataclass(frozen=True)
class Rule:
    """一条**可登记**的规则/阈值。

    ⚠️ **它不负责计算** —— 计算在 `RuleSet` 对应的实现里（用 `key` 关联）。
       本类只管"**这条规则是什么、出处在哪、单位是什么**"。
    """

    key: str
    label: str
    source: Source
    unit: str                       # "bool" / "比例" / "ADR倍数" / "美元" / "倍数"
    where: str = ""                 # 出处（`原文` 必填，例："§10.6① 行 1448"）
    value: Any = None               # 阈值（`我定` 必填）
    #: ★ **这条规则看哪一天的数据**：
    #:
    #: | 值 | 含义 |
    #: |---|---|
    #: | `"yesterday"` | 只看**截至昨天**（`shift(1)`）—— 所有"盘整态"条件 |
    #: | `"today"` | 含今天 —— **只有突破触发与止损宽度** |
    #:
    #: ## 为什么它必须登记在案（一条逻辑推断，不是保守）
    #:
    #: **信号日就是突破日**，而突破日必然"动了"。
    #: 若把「收缩到最后波动 < 1%」「均线收拢」这类**盘整态**条件放在**信号日**上算，
    #: 就等于**要求突破日不动** ⇒ **自相矛盾**。
    #:
    #: 他的原话也是这个意思：「成功的区间扩张**之前**，通常都会出现非常紧密的价格盘整」。
    #:
    #: ⇒ 这条我**推断**出来的（他每条没写"看哪天"）⇒ 登记成 `Source.INFERRED`
    #:   放在 `note` 里说清，而不是埋在某处 `shift(1)` 里。
    #: ⚠️ **默认 `"yesterday"`**（保守侧）—— 因为绝大多数条件是"盘整态"的，
    #:    而"盘整态"看今天会**自相矛盾**（见上表）。
    #:    忘了标也只是**偏保守**，不会引入前视。
    #:    只有「突破触发」「止损宽度」这两条**必须**显式标 `"today"`。
    as_of: str = "yesterday"
    note: str = ""
    #: ★ **这条是「加分项」还是「必须项」** —— 直接来自原文的用词。
    #:
    #: ## 为什么需要这一档（不是"软化"，是**忠实**）
    #:
    #: 原文描述他的六要点时，用词是**分档的**：
    #: 「他**一般**选 90 以上」（§7.1 行 704）、「**最好**不低于 52 周新高的 15%」（行 705）、
    #: 「波幅收缩，**最好**三次或以上」（行 706）——
    #: **三个"一般/最好"= 偏好**；而 ①「在 150 日线之上」、⑤「波动 < 1%」、⑥「配合成交量下跌」
    #: **没有任何修饰词 = 门槛**。
    #: ⇒ 把"一般/最好"实现成**硬 AND** 是**比原文更严**，不是"忠实"。
    #:   本字段就是那句用词的落点：`optional=True` 的条件**不参与排除**，
    #:   但仍会进 `diagnose()` 的漏斗（**他看的是六样**，一样不少）。
    #:
    #: ⚠️ **它不改变默认行为**（默认 `False`）—— 只有**逐条标过**的才变软。
    optional: bool = False

    def __post_init__(self) -> None:
        if self.source is Source.ORIGINAL and not self.where:
            raise RuleError(
                f"规则 {self.key!r} 标为「原文」⇒ **必须**给 `where`（出处行号）。"
                f"承 R3：出处是数据，不许靠注释兜底")
        if self.source is Source.CHOSEN and self.value is None:
            raise RuleError(
                f"规则 {self.key!r} 标为「我定」⇒ **必须**给 `value`（我定的数是多少）。"
                f"否则报告里说不清'到底哪个数是我编的'")
        if self.as_of not in ("today", "yesterday"):
            raise RuleError(
                f"规则 {self.key!r} 的 as_of={self.as_of!r} 不合法"
                f"（只能是 'today' / 'yesterday'）—— 承 R2："
                f"「这条看哪一天」必须显式，不许留给读者猜")
        if self.unit not in ALLOWED_UNITS:
            raise RuleError(
                f"规则 {self.key!r} 的 unit={self.unit!r} 不在允许清单里 —— "
                f"承 R1：单位必须显式，不许留白")


@dataclass(frozen=True)
class RuleSet:
    """**并列的一套规则**（不是"基础 + 开关"）。

    ⚠️ 跑法是「**选一个 RuleSet**」——
       不许"给 base 加 vcp 开关"，那正是把并列的连乘（见模块 docstring 教训 2）。
    """

    name: str
    label: str
    rules: tuple[Rule, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        keys = [r.key for r in self.rules]
        dup = {k for k in keys if keys.count(k) > 1}
        if dup:
            raise RuleError(f"RuleSet {self.name!r} 里 key 重复：{sorted(dup)}")

    def __iter__(self) -> Iterator[Rule]:
        return iter(self.rules)

    def __len__(self) -> int:
        return len(self.rules)

    def by_source(self, source: Source) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.source is source)

    def summary(self) -> str:
        n_o = len(self.by_source(Source.ORIGINAL))
        n_i = len(self.by_source(Source.INFERRED))
        n_c = len(self.by_source(Source.CHOSEN))
        return (f"{self.label}：共 {len(self)} 条"
                f"（原文 {n_o}｜推断 {n_i}｜**我定 {n_c}**）")


def render_rule_table(rs: RuleSet) -> str:
    """**从 `RuleSet` 生成文档表格** —— 手写的条件表全部作废。

    ⇒ 出处、单位、阈值只有一处来源，**不可能漂移**（承 R3）。
    """
    out = [f"| # | 条件 | 出处 | 单位 | 阈值 |", "|---|---|---|---|---|"]
    for i, r in enumerate(rs.rules, 1):
        val = "—" if r.value is None else f"`{r.value}`"
        where = r.where or ("—" if r.source is not Source.ORIGINAL else "⚠️缺")
        out.append(f"| {i} | {r.label} | **{r.source.value}** {where} | "
                   f"{r.unit} | {val} |")
    out.append("")
    out.append(f"> {rs.summary()}")
    return "\n".join(out)
