"""交叉筛选：**行业 → 个股 → 信号**。

README 定的选股顺序是「行业 → 个股 → 信号」，所以这里**先按行业状态分层**（强 → 弱），
再在每层里看**个股**的 RPS，最后叠**信号**（信号是择时确认，放最后）。

**视角是「行业优先」** —— 全市场达标名单（RPS 主周期 ≥ `RPS_MIN`）**按所属行业的状态重排**，
**不按 RPS 平铺**。按行业分组才看得见「哪些行业有货、哪个行业一只达标票都没有」——
一个平铺榜给不了这个信息。

三条铁律（都是 README 定的，这里只是执行）：

1. **不打分** —— 不把「20 和 250 都达标」压成一个 true/false 或分数去排序。
   四个周期的 RPS 原样列出，**排序只用主周期降序**，判断留给读的人
   （README「只给数字，不下判断」）。
2. **不截断** —— RPS 达标是**门槛**，门槛筛过的名单全列。
3. **不预判哪些状态算「强势」** —— 按 `STATE_ORDER` 从强到弱**全部列出**，
   空状态也列（「这个状态一个行业都没有」也是信息）。
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.indicators.industry import STATE_ORDER  # noqa: E402
from engine.screener import multiscale  # noqa: E402
from engine.screener.table import md_table, pad  # noqa: E402

__all__ = [
    "cross", "cross_columns", "cross_rows",
    "print_cross", "cross_markdown",
]

# 行业状态里「一个行业都没货」的，用这行提示 —— 别让人以为是漏了
BLANK_HINT = "0 达标 / 0 信号"

# 状态机只收**成分股 ≥ 5 只**的行业，所以总有些票的行业不在任何状态里。
# 这一组**不能省** —— 省了就是悄悄丢票（踩过：295 只只显示 262 只）。
UNCOVERED = "（状态机没覆盖）"
UNCOVERED_NOTE = (
    "成分股 < 5 只的行业（状态机不收）+ 快照里没行业的（`—`）—— "
    "**这些票不能丢**，只是没有行业状态可依。"
)


def _bare(code, market="US"):
    """`US.VSAT` → `VSAT`。**只剥市场前缀**。

    不能用 `split(".")[-1]` —— `MOG.A` / `PBR.A` 这种带点的会被切成 `A`。
    """
    code = str(code or "")
    prefix = f"{market}."
    return code[len(prefix):] if code.startswith(prefix) else code


def _num(value, digits=2, scale=1.0):
    return "—" if value is None else f"{value * scale:.{digits}f}"


def _rps(value):
    return "—" if value is None else f"{value:.1f}"


def _ms(r):
    """多尺度那一列 —— `形状 跨度`（如 `短低长高 39`）。**只显示，不参与排序/筛选。**

    形状来自 `engine.screener.multiscale`，是**描述**不是判断：
    `一致` / `短低长高` / `短高长低` / `交错`。

    **数字是「跨度」= 四个周期 RPS 的最高分减最低分。**
    叫「跨度」不叫统计学的「极差」—— 后者在中文里会被读成「非常差」，正好反了。
    """
    ms = r.get("ms") or {}
    s, sp = ms.get("shape"), ms.get("spread")
    if not s or s == "—":
        return "—"
    return f"{s} {sp:.0f}" if sp is not None else s


def _merge(qualify, hits, market, main):
    """一个行业的票 = 达标票 ∪ 信号票，**按代码去重**（两条路都命中的合成一行）。

    返回按**主周期 RPS 降序**排好的列表 —— **不按任何布尔/分数排序**。
    """
    by_code = {}
    for r in qualify:
        bare = _bare(r.get("code"), market)
        by_code[bare] = {
            "code": r.get("code") or bare,
            "name": r.get("name"),
            "rps": r.get("rps") or {},
            "ms": multiscale.describe(r.get("rps")),
            "price": r.get("price"),
            "mcap": r.get("mcap"),
            "signals": [],
        }
    for s in hits:
        bare = _bare(s.get("code"), market)
        row = by_code.setdefault(bare, {
            "code": bare,
            "name": s.get("name"),
            "rps": s.get("rps") or {},
            "ms": multiscale.describe(s.get("rps")),
            "price": s.get("price"),
            "mcap": s.get("mcap"),
            "signals": [],
        })
        row["signals"] = list(s.get("signals") or [])
    # `rps` 的键是**字符串**（`{"20": ...}`），`main` 是 int —— 不 `str()` 就全取不到
    return sorted(by_code.values(),
                  key=lambda r: (r["rps"].get(str(main)) is None,
                                 -(r["rps"].get(str(main)) or 0)))


def cross(picks):
    """把「达标票 + 信号票」按**行业状态**分层。纯计算，不读文件。

    返回 `dict` —— **是 `picks` 的纯函数，不落盘**（落盘就是把同一批票再存一遍）。
    渲染时现算（`render` / `to_markdown` 都拿得到 `picks`）：

      params     口径（periods / 主周期 / rps_min）
      density    各状态的 行业数 / 达标票数 / 信号票数
      groups     按 `STATE_ORDER` 强 → 弱；每组带行业清单（每个行业带合并好的票）
      empty      一个行业都没有的状态
      both       **达标 且 有信号**的（两条路都指向它）
      orphans    有信号、但**不达标**的（信号多是回踩型，RPS 主周期常常不到门槛）
    """
    params = picks.get("params") or {}
    periods = tuple(params.get("periods") or (20, 50, 120, 250))
    main = params.get("days") or periods[-1]
    market = picks.get("market") or "US"

    by_state, state_of = {}, {}
    for g in picks.get("states") or []:
        by_state[g["state"]] = g.get("industries") or []
        for ind in g.get("industries") or []:
            state_of[ind.get("industry")] = g["state"]

    qualify = defaultdict(list)  # 行业 → 达标票
    for r in picks.get("strong") or []:
        qualify[r.get("industry") or "—"].append(r)

    hits = defaultdict(list)  # 行业 → 信号票
    for s in picks.get("signals") or []:
        hits[s.get("industry") or "—"].append(s)

    groups = []
    for st in STATE_ORDER:
        inds, blank = [], []
        for ind in by_state.get(st) or []:
            name = ind.get("industry")
            q, h = qualify.get(name, []), hits.get(name, [])
            if not q and not h:
                blank.append(name)  # 没货的行业不单列，压成一行
                continue
            inds.append({
                "industry": name,
                "count": ind.get("count"),
                "n_qualify": len(q),
                "n_hits": len(h),
                "rows": _merge(q, h, market, main),
            })
        if not inds and not blank:
            continue  # 这个状态今天一个行业都没有 —— 交给 `empty` 说
        groups.append({
            "state": st,
            "industries": inds,
            "blank": blank,
            "n_industries": len(inds),
            "n_qualify": sum(i["n_qualify"] for i in inds),
            "n_hits": sum(i["n_hits"] for i in inds),
        })

    # **状态机没覆盖到的行业** —— 成分股 < 5 只的（状态机只收 ≥5 只的），
    # 以及快照里根本没行业的（`—`）。这些票**不能丢**，单列一组。
    known = {i.get("industry") for inds in by_state.values() for i in inds}
    leftover = sorted({k for k in set(qualify) | set(hits) if k not in known},
                      key=lambda k: (-len(qualify.get(k, [])), k))
    if leftover:
        inds = [{
            "industry": name,
            "count": None,
            "n_qualify": len(qualify.get(name, [])),
            "n_hits": len(hits.get(name, [])),
            "rows": _merge(qualify.get(name, []), hits.get(name, []), market, main),
        } for name in leftover]
        groups.append({
            "state": UNCOVERED,
            "industries": inds,
            "blank": [],
            "n_industries": len(inds),
            "n_qualify": sum(i["n_qualify"] for i in inds),
            "n_hits": sum(i["n_hits"] for i in inds),
        })

    qualify_codes = {_bare(r.get("code"), market) for r in picks.get("strong") or []}
    both, orphans = [], []
    for s in picks.get("signals") or []:
        (both if _bare(s.get("code"), market) in qualify_codes else orphans).append(s)

    # ── 自选板块（**含概念板块**）────────────────────────────────
    # **概念板块和行业是同一回事** —— 都是一揽子股票的集合，所以用**同一套算法**：
    # 同一个 `classify`、同一套上涨占比（`scan.step_plates` 里就是把板块名当行业名喂给
    # `industry_stats`）。区别只在**成分股来源** —— 快照里有富途行业字段，没有概念板块，
    # 所以板块成分股来自 `data/plate_members.json`。
    plates_doc = picks.get("plates") or {}
    members = plates_doc.get("members") or {}
    stats_by_code = {s.get("plate_code"): s for s in (plates_doc.get("stats") or [])}
    plates = []
    for code, blk in members.items():
        name = blk.get("name") or code
        st = stats_by_code.get(code) or {}
        codes = {_bare(c, market) for c in (blk.get("codes") or [])}
        q = [r for r in picks.get("strong") or [] if _bare(r.get("code"), market) in codes]
        h = [s for s in picks.get("signals") or [] if _bare(s.get("code"), market) in codes]
        plates.append({
            "plate": name,
            "plate_code": code,
            # 名字同时也是行业名 → 它就是个**行业板块**（上面已经按行业列过了，这里不重复展开）
            "kind": "行业板块" if name in state_of else "概念板块",
            "state": st.get("state"),
            "members": len(blk.get("codes") or []),  # 板块的**全部**成分股
            "count": st.get("count"),                # 落在「可投资域」样本里的
            "by_days": st.get("by_days") or {},
            "n_qualify": len(q),
            "n_hits": len(h),
            "rows": _merge(q, h, market, main),
        })
    # 按**状态强弱**排（不是按达标票数 —— 那是把判断藏进排序）
    _order = {st: i for i, st in enumerate(STATE_ORDER)}
    plates.sort(key=lambda p: (_order.get(p["state"], len(STATE_ORDER)), p["plate"]))

    return {
        "params": {"periods": list(periods), "main": main,
                   "rps_min": params.get("rps_min")},
        "density": [
            {"state": g["state"], "industries": g["n_industries"],
             "qualify": g["n_qualify"], "hits": g["n_hits"]}
            for g in groups
        ],
        "groups": groups,
        "empty": [st for st in STATE_ORDER if not (by_state.get(st) or [])],
        "both": both,
        "orphans": orphans,
        "plates": plates,
    }


def cross_columns(periods):
    """票表的列定义 `(标题, 宽度, 右对齐, 取值)` —— 终端和 Markdown **共用这一份**。

    **没有「达标」这种布尔列** —— 达标线写在表头（RPS 主周期 ≥ 门槛），
    旁边就是 RPS 数字，读的人自己看（README「只给数字，不下判断」）。
    """
    return (
        [("代码", 12, False, lambda r: str(r.get("code") or "")),
         ("名称", 20, False, lambda r: (r.get("name") or "")[:18])]
        + [(f"RPS{n}", 6, True, lambda r, n=n: _rps(r["rps"].get(str(n)))) for n in periods]
        + [("周期跨度", 13, False, lambda r: _ms(r)),
           ("现价", 9, True, lambda r: _num(r.get("price"))),
           ("市值(亿$)", 9, True, lambda r: _num(r.get("mcap"), 1, 1e-8)),
           ("信号", 16, False, lambda r: "+".join(r.get("signals") or []))]
    )


def cross_rows(rows, cols):
    """把合并好的票转成单元格 —— 终端和 Markdown 共用。"""
    return [[str(fn(r)) for _, _, _, fn in cols] for r in rows]


PLATE_COLUMNS = [
    ("板块", 20, False),
    ("类型", 10, False),
    ("状态", 12, False),
    ("成分股", 7, True),
    ("快照内", 7, True),
    ("达标票", 7, True),
    ("信号票", 7, True),
]


def plate_overview(plates):
    """自选板块总览 —— 终端和 Markdown **共用这一份**。

    `成分股` = 板块的全部成员；`快照内` = 其中落在「可投资域」样本里的
    （差得多很正常：样本要求市值 ≥ 10 亿$，`小型价值股` 241 只全是 ETF → 0）。
    """
    return [[
        p["plate"], p["kind"], p["state"] or "—",
        str(p["members"]),
        "—" if p["count"] is None else str(p["count"]),
        str(p["n_qualify"]), str(p["n_hits"]),
    ] for p in plates]


def _head(rps_min, main):
    return (f"（达标线 RPS{main} ≥ {rps_min:g}）" if rps_min is not None
            else f"（达标线 RPS{main}）")


def _ind_title(ind, bold=False):
    """行业小标题。

    `count` 可能是 None（状态机没覆盖的那组不知道家数）—— 别显示 `None 只`；
    行业名可能是 `—`（快照里就没有行业字段）—— 别显示成一个孤零零的破折号。
    """
    n = f"{ind['count']} 只" if ind.get("count") else "家数不详"
    name = str(ind.get("industry") or "")
    if name.strip() in ("", "—", "-"):
        name = "（快照里没行业字段）"
    if bold:
        name = f"**{name}**"
    return f"{name}（{n}）｜达标 {ind['n_qualify']} 只｜信号 {ind['n_hits']} 只"


def print_cross(picks):
    """终端打印交叉筛选 —— 强 → 弱，**全列不截断**。"""
    c = cross(picks)
    periods = c["params"]["periods"]
    main, rps_min = c["params"]["main"], c["params"]["rps_min"]
    cols = cross_columns(periods)

    print(f"\n【交叉筛选】行业 → 个股 → 信号{_head(rps_min, main)}"
          " —— **按行业状态强 → 弱，全列**")

    print("\n  各状态密度（先看哪个状态有货）：")
    dcols = [("状态", 18, False), ("行业数", 7, True), ("达标票", 7, True), ("信号票", 7, True)]
    print("    " + " ".join(pad(t, w, r) for t, w, r in dcols))
    print("    " + "-" * (sum(w for _, w, _ in dcols) + len(dcols) - 1))
    for d in c["density"]:
        print("    " + " ".join([
            pad(d["state"], 18), pad(str(d["industries"]), 7, True),
            pad(str(d["qualify"]), 7, True), pad(str(d["hits"]), 7, True),
        ]))

    # ★ 自选板块 —— **概念板块和行业同一套算法**
    if c["plates"]:
        print(f"\n  ── ★ 自选板块（{len(c['plates'])} 个｜你盯的）"
              "｜概念板块和行业**同一套算法**，只是成分股来源不同")
        print("    " + " ".join(pad(t, w, r) for t, w, r in PLATE_COLUMNS))
        print("    " + "-" * (sum(w for _, w, _ in PLATE_COLUMNS) + len(PLATE_COLUMNS) - 1))
        for cells in plate_overview(c["plates"]):
            print("    " + " ".join(
                pad(v, w, r) for v, (_, w, r) in zip(cells, PLATE_COLUMNS)
            ))
        for p in c["plates"]:
            if not p["rows"]:
                continue
            print(f"\n     {p['plate']}（{p['kind']}｜{p['state'] or '—'}｜"
                  f"成分股 {p['members']}"
                  f" · 快照内 {'—' if p['count'] is None else p['count']}）")
            print("     " + " ".join(pad(t, w, r) for t, w, r, _ in cols))
            print("     " + "-" * (sum(w for _, w, _, _ in cols) + len(cols) - 1))
            for cells in cross_rows(p["rows"], cols):
                print("     " + " ".join(
                    pad(v, w, r) for v, (_, w, r, _) in zip(cells, cols)
                ))

    for g in c["groups"]:
        print(f"\n  ── {g['state']}"
              f"（{g['n_industries'] + len(g['blank'])} 个行业｜"
              f"达标 {g['n_qualify']} 只｜信号 {g['n_hits']} 只）")
        if g["state"] == UNCOVERED:
            print(f"     {UNCOVERED_NOTE}")
        for ind in g["industries"]:
            print(f"\n     {_ind_title(ind)}")
            print("     " + " ".join(pad(t, w, r) for t, w, r, _ in cols))
            print("     " + "-" * (sum(w for _, w, _, _ in cols) + len(cols) - 1))
            for cells in cross_rows(ind["rows"], cols):
                print("     " + " ".join(
                    pad(v, w, r) for v, (_, w, r, _) in zip(cells, cols)
                ))
        if g["blank"]:
            print(f"\n     （{BLANK_HINT}）：{'、'.join(g['blank'])}")

    if c["empty"]:
        print(f"\n  ── 空状态（0 个行业）：{'、'.join(c['empty'])}")

    print(f"\n  达标 **且** 有信号（两条路都指向它）：{len(c['both'])} 只")
    for s in c["both"]:
        print(f"    {str(s.get('code')):<12}{(s.get('name') or '')[:18]:<20}"
              f"{'+'.join(s.get('signals') or []):<14}{s.get('industry') or '—'}")
    print(f"  有信号但不达标：{len(c['orphans'])} 只"
          "（信号 ≠ 达标 —— 信号多是回踩型，见第二节）")


def cross_markdown(picks):
    """报告里的一节 —— 和终端**同一份数据**（`cross`），只是排版不同。"""
    c = cross(picks)
    periods = c["params"]["periods"]
    main, rps_min = c["params"]["main"], c["params"]["rps_min"]
    cols = cross_columns(periods)

    out = [
        f"## 四、交叉筛选：行业 → 个股 → 信号{_head(rps_min, main)}",
        "",
        "**全市场达标名单的「行业优先」视角** —— 按**行业状态强 → 弱**分层，",
        "每层里列出该行业的**达标票 + 信号票**（按代码去重，两条路都命中的合成一行）。",
        "",
        "**为什么按行业分组、不按 RPS 平铺**：选股顺序是「行业 → 个股 → 信号」——",
        "先知道**哪些行业有货、哪个行业一只达标票都没有**，比一个几百行的平铺榜有用。",
        "每只票四个周期的 RPS 原样列着，想按个股强度找直接看数字。",
        "",
        "> **不打分**（四个周期 RPS 原样列出，排序只用主周期降序）·"
        " **不截断**（达标是门槛，筛过的全列）·"
        " **不预判哪些状态算「强势」**（强 → 弱全列，空状态也列）。",
        "",
        "### 4.0 各状态密度（先看哪个状态有货）",
        "",
    ]
    out += [md_table(
        ["状态", "行业数", "达标票", "信号票"],
        [[d["state"], d["industries"], d["qualify"], d["hits"]] for d in c["density"]],
    ), ""]

    # 4.1 ★ 自选板块 —— **概念板块和行业同一套算法**，不是另一套
    if c["plates"]:
        out += [
            f"### 4.1 ★ 自选板块（{len(c['plates'])} 个｜你盯的）",
            "",
            "**概念板块和行业是同一回事** —— 都是一揽子股票的集合，所以用**同一套算法**"
            "（同一个状态机、同一套上涨占比）。区别只在**成分股来源**："
            "行业来自快照的 `industry` 字段，板块来自 `data/plate_members.json`。",
            "",
            md_table([t for t, _, _ in PLATE_COLUMNS], plate_overview(c["plates"])),
            "",
            "> **快照内** = 该板块有这么多成分股落在「可投资域」样本里"
            "（价 ≥ $1 · 市值 ≥ 10 亿$ · 有量 · 上市 ≥ 120 天）。"
            "**成分股 ≠ 快照内** —— 差得多的（如 `小型价值股` 241 → 0，成分股全是 ETF）"
            "**不是漏了，是样本口径**。",
            "",
            "**类型**：`行业板块` = 名字同时也是富途行业名（上面的行业段里已经列过它的票）；"
            "`概念板块` = 富途概念板块，快照里没有，只在这里。",
            "",
        ]
        for p in c["plates"]:
            if not p["rows"]:
                continue
            out += [
                f"**{p['plate']}**（{p['kind']}｜{p['state'] or '—'}｜成分股 {p['members']}"
                f" · 快照内 {'—' if p['count'] is None else p['count']}）｜"
                f"达标 {p['n_qualify']} 只｜信号 {p['n_hits']} 只",
                "",
                md_table([t for t, _, _, _ in cols], cross_rows(p["rows"], cols)),
                "",
            ]

    for i, g in enumerate(c["groups"], 2):
        out += [
            f"### 4.{i} {g['state']}"
            f"（{g['n_industries'] + len(g['blank'])} 个行业｜"
            f"达标 {g['n_qualify']} 只｜信号 {g['n_hits']} 只）",
            "",
        ]
        if g["state"] == UNCOVERED:
            out += [f"> {UNCOVERED_NOTE}", ""]
        if not g["industries"]:
            out += ["这个状态有行业，但**一个达标票 / 信号票都没有**。", ""]
        for ind in g["industries"]:
            out += [
                _ind_title(ind, bold=True),
                "",
                md_table([t for t, _, _, _ in cols], cross_rows(ind["rows"], cols)),
                "",
            ]
        if g["blank"]:
            out += [f"**其余行业（{BLANK_HINT}）**：{'、'.join(g['blank'])}", ""]

    if c["empty"]:
        out += [
            f"**空状态（0 个行业）：{'、'.join(c['empty'])}**",
            "",
            "> **没有成员也是信息** —— 比如「启动」为空 = **今天没有行业在启动**，"
            "没有新方向在起。",
            "",
        ]

    out += [
        f"### 4.{len(c['groups']) + 2} 两条路都指向的（达标 **且** 有信号）",
        "",
        f"**{len(c['both'])} 只** —— 行业强、个股 RPS 达标、又出了信号，三个条件同时满足：",
        "",
    ]
    if c["both"]:
        out += [md_table(
            ["代码", "名称", "信号", "行业"],
            [[f"`{s.get('code')}`", s.get("name") or "",
              " + ".join(s.get("signals") or []), s.get("industry") or "—"]
             for s in c["both"]],
        ), ""]
    else:
        out += ["（无）", ""]
    out += [
        f"**有信号但不达标：{len(c['orphans'])} 只** —— 信号是**回踩型**，"
        "RPS 主周期常常不到门槛（README：「20 低、250 高 = 长期强、最近歇着」）。"
        "**信号 ≠ 达标**，别把这两张表当一回事。",
        "",
    ]
    return out


if __name__ == "__main__":
    import argparse

    from fetch import day as day_store  # noqa: E402

    ap = argparse.ArgumentParser(
        description="交叉筛选：行业 → 个股 → 信号（读 data/scan/<日期>/picks.json，不联网）")
    ap.add_argument("--date", default=None, help="哪一天（默认市场所在地今天）")
    ap.add_argument("--market", default="US", help="市场（默认 US）")
    args = ap.parse_args()

    # `root` 不传 —— `day` 的默认根就是 `data/`，传项目根会变成 `scan/<日期>` 找不到
    _picks = day_store.load("picks", date=args.date, market=args.market)
    if not _picks:
        raise SystemExit("没有 picks.json —— 先跑 `python3 scan.py --analyze-only`")
    print_cross(_picks)
