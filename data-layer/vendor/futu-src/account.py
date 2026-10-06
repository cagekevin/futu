"""账户 —— 把整个仓库压到同一个分母上：**钱**。

    python3 account.py              # 算 + 落盘（reports/account/ + data/account/paper.csv）
    python3 account.py --no-save    # 只看，不落盘

## 为什么要有这个东西（2026-10-05）

用户原话：**「我不知道我们写了这么多，最终的目标是什么。」**

查下来根因很清楚：

    reports/ 下 12 个目录 —— 全是研究报告
    净值 / equity 只出现在 5 个回测脚本的肚子里，从没落盘成一个「账户」

**10 个课题说了「世界是什么样的」，没有一个说「我的账户会变成什么样」。**
所有结论都是 pp / 分位 / 胜率 —— 它们**没有共同的分母**，
所以越攒越多、越攒越茫然。

**这个文件就是那个分母。** 它只回答一个问题：

> **如果从 2019 年就这么做，今天账户是多少？**

## 它是什么，不是什么

| 是 | 不是 |
|---|---|
| **规则净值** —— 按 tsmom 的仓位纪律 + 自选池等权，算出来的 | **实盘净值** —— 我不知道你实际买了什么 |
| 一个**可执行的今日建议**（该几成仓） | 一个选股建议（**选股那一格还是空的**） |
| **一个数** —— 把 10 个课题的结论压在一起 | 一个新结论 |

⚠️ **它不产生新证据**，它是**已有证据的合并报表**。
新证据仍然只能从 `research/` 来（`docs/plan-research-layer.md`）。

## 边界（读数字前先读这段）

  · **有幸存者偏差 + POOL_LOOKAHEAD** —— 自选池是**今天**选的，
    能活到今天的票本来偏强。**净值偏乐观**（坑 `SURVIVORSHIP` / `POOL_LOOKAHEAD`）
  · 等权持有 343 只在**实操上不可能**（钱要够、手续费），
    它是「**什么都不做**」的对照，不是建议
  · 成本只是**假设**（单边 10bp），真实取决于券商和规模
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine.portfolio import compound_by_month, drawdown_series, nav_stats  # noqa: E402
from engine.screener.local import load_all  # noqa: E402
from engine.tsmom import HOLD, LOOKBACK, monthly_returns  # noqa: E402

COST_BP = 10.0
REPORTS = ROOT / "reports" / "account"
PAPER = ROOT / "data" / "account" / "paper.csv"


def _ymd(d) -> str:
    """`20261002` → `2026-10-02`；`202111` → `2021-11`。"""
    d = str(d)
    if len(d) >= 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return f"{d[:4]}-{d[4:6]}" if len(d) == 6 else d


def _pct(v, nd=1, sign=True) -> str:
    if v is None:
        return "—"
    return f"{v*100:+.{nd}f}%" if sign else f"{v*100:.{nd}f}%"


def collect(items, lookback: int, hold: int):
    """把 347 只票摊成三样东西：逐月回测行 · 月度收益桶 · 当前在场名单。"""
    rows, ts_m, bh_m, now_in, last = [], {}, {}, [], 0
    for doc in items:
        bars = doc.get("bars") or []
        if len(bars) <= lookback:
            continue
        r = monthly_returns(bars, lookback, hold)
        if r:
            rows.extend(r)
            for d, inm, ret, sw in r:
                ts_m.setdefault(str(d)[:6], []).append(ret if inm else 0.0)
                bh_m.setdefault(str(d)[:6], []).append(ret)
        # 「今天该几成仓」= 用**最新一根**判在不在场（不要求对齐到再平衡日）
        inm_now = bars[-1]["close"] / bars[-1 - lookback]["close"] - 1.0 > 0
        if inm_now:
            now_in.append(doc.get("symbol"))
        last = max(last, bars[-1]["date"])
    return rows, ts_m, bh_m, now_in, last


def build_report(items, *, lookback: int, hold: int, cost_bp: float,
                 min_symbols: int = 30) -> tuple[str, dict]:
    """`(报告正文, 摘要)` —— 摘要给 `main()` 写前瞻账本用（**不重算**）。

    ⚠️ **只保留「当月标的数 ≥ `min_symbols`」的月份**：
    2020-07 ~ 2021-10 这 16 个月**只有 1 只标的**（`FX.USDCNY`，唯一从 2019-07 起
    有 252 根历史的）。那段「策略不跌、基准跌 7%」**不是纪律好，是一只汇率** ——
    而且它把 CAGR 的**分母拉长了 16 个月**（虚高）。
    **一个「账户」必须从样本足够的那天起算。** 第一次跑出来就是这个错（2026-10-05）。
    """
    rows, ts_m, bh_m, now_in, last = collect(items, lookback, hold)
    n_sym = sum(1 for d in items if len(d.get("bars") or []) > lookback)

    # ★ 剔除样本不足的月份（`min_symbols`）
    if rows:
        cnt = Counter(str(r[0])[:6] for r in rows)
        keep = {m for m, c in cnt.items() if c >= min_symbols}
        dropped = len(cnt) - len(keep)
        rows = [r for r in rows if str(r[0])[:6] in keep]
    else:
        dropped = 0

    cost = cost_bp / 10000.0
    ts_c: dict[str, list[float]] = {}
    bh_c: dict[str, list[float]] = {}
    for d, inm, ret, sw in rows:
        ts_c.setdefault(str(d)[:6], []).append((ret if inm else 0.0) - (cost if sw else 0.0))
        bh_c.setdefault(str(d)[:6], []).append(ret)

    months, t_nav = compound_by_month(ts_c)
    _m, h_nav = compound_by_month(bh_c)
    st, sh_ = nav_stats(t_nav), nav_stats(h_nav)
    t_dd, h_dd = drawdown_series(t_nav), drawdown_series(h_nav)

    pos = len(now_in) / n_sym if n_sym else 0.0
    n_sw = sum(1 for _d, _i, _r, s in rows if s)

    L: list[str] = []
    p = L.append
    p(f"# 账户 —— {_ymd(last)}")
    p("")
    p(f"> **如果从 {_ymd(months[0]) if months else '—'} 就这么做，今天账户是多少。**")
    p(f"> 命令：`python3 account.py`")
    p(f"> 口径：tsmom **{lookback}/{hold}**（long/flat）+ 自选池等权，"
      f"成本单边 **{cost_bp:g}bp**")
    p(f"> 样本：**{n_sym}** 只（{months[0] if months else '—'} → {_ymd(last)}，"
      f"{st['months'] if st else 0} 个月，换仓 {n_sw} 次）")
    if dropped:
        p(f"> ⏭ **前面 {dropped} 个月已剔除**（当月标的数 < {min_symbols}）—— "
          f"那段只有 1 只标的，不是组合")
    p("> ⚠️ **这是规则净值，不是实盘** —— 实盘还要看你自己买了什么")
    p("")

    # ── 一、账户
    p("## 一、账户")
    p("")
    p("| | **策略**（tsmom 仓位） | 基准（一直满仓） |")
    p("|---|---|---|")
    p(f"| 累计 | **{_pct(st['total'])}** | {_pct(sh_['total'])} |")
    p(f"| 年化 | **{_pct(st['cagr'])}** | {_pct(sh_['cagr'])} |")
    p(f"| Sharpe | **{st['sharpe']:.2f}** | {sh_['sharpe']:.2f} |")
    p(f"| 月波动 | {st['sd']*100:.2f}% | {sh_['sd']*100:.2f}% |")
    p(f"| **最大回撤** | **{_pct(st['mdd'])}** | **{_pct(sh_['mdd'])}** |")
    p(f"| 今天仓位 | **{pos*100:.0f}%** | 100% |")
    p("")
    gain = (sh_["cagr"] - st["cagr"]) * 100 if sh_["cagr"] and st["cagr"] else 0
    cut = (st["mdd"] - sh_["mdd"]) * 100
    p(f"**这不是「更赚」，是「用少赚 {gain:.1f}pp 年化，换少亏 {abs(cut):.1f}pp 回撤」。**")
    p("")

    # ── ★ 消融：策略和基准不是两件事，是【同一个系统的两个配置】
    p("### ★ 消融 —— 上面两列不是「策略」和「别的什么」，是**同一个系统的两个配置**")
    p("")
    p("| 配置 | 净值 | 年化 | Sharpe | **最大回撤** |")
    p("|---|---|---|---|---|")
    p(f"| **A. 满仓全买**（**拿掉 tsmom**） | {h_nav[-1]:.3f} | {_pct(sh_['cagr'])} "
      f"| {sh_['sharpe']:.2f} | **{_pct(sh_['mdd'])}** |")
    p(f"| **B. 加上 tsmom 择时**（现在这个系统） | {t_nav[-1]:.3f} "
      f"| {_pct(st['cagr'])} | {st['sharpe']:.2f} | **{_pct(st['mdd'])}** |")
    p("")
    p(f"**⇒ 一个部件（tsmom）的贡献：年化 −{gain:.1f}pp（少赚）、"
      f"**回撤 −{abs(cut):.1f}pp（少亏）**。**它买的是回撤，不是收益。**")
    p("")
    p(f"> 换句话说：**拿掉 tsmom 你会多赚 {gain:.1f}pp/年，但最坏的时候会多亏 "
      f"{abs(cut):.1f}pp。** 这个交换划不划算，取决于**你在 −33% 的时候拿不拿得住**。")
    p("")
    p("> **这就是「交易系统」和「找圣杯」的区别**：")
    p("> - **找圣杯** = 找一个能在 A 之上**加收益**的信号 —— **我们试了 10 条，全否**")
    p(">   （而市场有效的话，那本来就该全否 —— **否掉不是成绩，是方法的必然**）")
    p("> - **造系统** = 把**都不完美**的部件拼起来，让**整体**活得久 ——")
    p(">   `tsmom` **不预测方向**，它只决定「在不在场」；")
    p(">   **它在收益上是负的、在回撤上是正的** —— 而它是**唯一一个通过检验**的东西")
    p("")
    p("> **另一个部件在 `research/sizing/`**（第六节）：**买几只**。")
    p("> 池里随机挑 N 只：**收益中位数几乎不随 N 变**（差 21pp），"
      "**最坏 5% 回撤差 33pp**（N=3 的 −59.1% vs N=100 的 −26.1%）。")
    p("> ⇒ **两个部件都不是靠预测，都是靠「少犯错」。**")
    p("")

    # ── 二、今天
    p("## 二、★ 今天该怎么做（可执行）")
    p("")
    p(f"    在场 **{pos*100:.0f}%** —— {n_sym} 只自选里，"
      f"过去 {lookback} 根收益 > 0 的有 **{len(now_in)}** 只")
    p(f"    ⇒ 仓位 **{pos*100:.0f}%**，其余现金")
    p("")
    if len(now_in) < 40:
        p(f"    {len(now_in)} 只" + "：`" + "` `".join(sorted(now_in)[:40]) + "`")
    else:
        p(f"    前 40 只：`" + "` `".join(sorted(now_in)[:40]) + "`" + (" …" if len(now_in) > 40 else ""))
    p("")

    # ── 三、逐月账本
    p("## 三、逐月账本（**这就是那条曲线**）")
    p("")
    p("> **「标的」这一列不能省**（`Agent.md` 硬规矩 ④：不许报裸比例）。")
    p("> 已剔除样本不足的月份，所以**这里的「在场%」是组合的，不是单只票的**。")
    p("")
    p("| 月份 | 标的 | 在场% | 月收益 | 策略净值 | 基准净值 | 策略回撤 | 基准回撤 |")
    p("|---|---|---|---|---|---|---|---|")
    for i, m in enumerate(months):
        inm = sum(1 for d, v, _r, _s in rows if str(d)[:6] == m and v)
        tot = sum(1 for d, _v, _r, _s in rows if str(d)[:6] == m)
        mr = t_nav[i + 1] / t_nav[i] - 1
        p(f"| {m[:4]}-{m[4:6]} | {tot} | {inm/tot*100:.0f}% | {_pct(mr, 2)} "
          f"| {t_nav[i+1]:.3f} | {h_nav[i+1]:.3f} "
          f"| {_pct(t_dd[i+1], 1)} | {_pct(h_dd[i+1], 1)} |")
    p("")
    p(f"**净值起点 1.000 ⇒ 今天 策略 {t_nav[-1]:.3f} / 基准 {h_nav[-1]:.3f}**")
    p("")

    # ── 四、边界
    p("## 四、这张表不能证明什么")
    p("")
    p("| 边界 | 后果 |")
    p("|---|---|")
    p("| **自选池是今天选的**（`SURVIVORSHIP` / `POOL_LOOKAHEAD`） | 净值**偏乐观** |")
    p("| 等权持有全部自选**实操不可能** | 基准是「什么都不做」的对照，不是建议 |")
    p(f"| 成本只是假设（单边 {cost_bp:g}bp） | 真实取决于券商和规模 |")
    p("| **选股那一格还是空的**（见 `research/README.md` 产品映射） | 这不是选股策略 |")
    p("")
    p("> **结论只在这句话上**：在这个偏乐观的池子上，"
      "**tsmom 的仓位纪律把最大回撤砍了一半**。")
    p("> **它没让你多赚 —— `<docs/discipline.md>` §3 的原话：它卖的是「少亏」。**")
    return "\n".join(L) + "\n", {
        "asof": str(last), "position": pos,
        "nav_tsmom": t_nav[-1], "nav_hold": h_nav[-1],
    }


def write_paper(asof: str, pos: float, t_nav: float, h_nav: float) -> str:
    """★ **前瞻账本** —— append-only，一天一行，**同一个日期不重复写**。

    它的作用：三个月后你能看见「**我当时的判断**」和「**后来发生了什么**」。
    回测净值会随数据更新而变，**这行不会** —— 所以它才是证据。
    """
    PAPER.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    rows: list[list[str]] = []
    if PAPER.exists():
        with PAPER.open(encoding="utf-8", newline="") as f:
            rd = list(csv.reader(f))
        rows = rd[1:] if rd and rd[0] and rd[0][0] == "date" else rd
    if any(r and r[0] == today for r in rows):
        return f"⏭ 今天（{today}）已经记过一行，不重复写"
    with PAPER.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "data_asof", "position", "nav_tsmom", "nav_hold"])
        w.writerows(rows)
        w.writerow([today, asof, f"{pos:.4f}", f"{t_nav:.4f}", f"{h_nav:.4f}"])
    return f"✅ 已追一行到 {PAPER.relative_to(ROOT)}（{today} 仓位 {pos*100:.0f}%）"


def main() -> int:
    ap = argparse.ArgumentParser(description="账户 —— 把结论压成一个数")
    ap.add_argument("--lookback", type=int, default=LOOKBACK)
    ap.add_argument("--hold", type=int, default=HOLD)
    ap.add_argument("--min-bars", type=int, default=300)
    ap.add_argument("--min-symbols", type=int, default=30,
                    help="当月标的数少于这个就剔除（早期只有 1 只票的月份不算组合）")
    ap.add_argument("--cost-bp", type=float, default=COST_BP)
    ap.add_argument("--save", dest="save", action="store_true", default=True)
    ap.add_argument("--no-save", dest="save", action="store_false")
    args = ap.parse_args()

    items = load_all(None, min_bars=args.min_bars)
    text, info = build_report(items, lookback=args.lookback, hold=args.hold,
                              cost_bp=args.cost_bp,
                              min_symbols=args.min_symbols)
    print(text)

    if not args.save:
        print("（--no-save：没落盘）")
        return 0

    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{info['asof']}.md"
    path.write_text(text, encoding="utf-8")
    print(f"✅ 已落盘 {path.relative_to(ROOT)}")
    print(write_paper(info["asof"], info["position"],
                      info["nav_tsmom"], info["nav_hold"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
