"""板块成分股 —— 概念板块 / 行业板块的「一揽子股票」到底是谁。

**为什么需要**：全市场快照（`data/scan/<日期>/universe.json`）只带**一个**分类字段
`industry`（富途**行业**）。**概念板块**（存储概念 / 光通信 / 量子计算 / 稀土概念…）
不在里面 —— 所以要算概念板块的「集体行为」，得先知道它有哪些成分股。

**拿到成分股之后，算法和行业一模一样** —— 把板块名写进 `industry` 字段，直接喂
`engine.indicators.industry.industry_stats()`，一行都不用改。
**概念板块和行业都是一揽子股票的集合**，不该区别对待。

落盘 `data/plates.json`（**不在当天文件夹里**）—— 成分股变化很慢，没必要天天拉。

用法：
    python3 fetch/opend/plates.py                # 拉自选里那批板块（`PLATE`）
    python3 fetch/opend/plates.py --refresh      # 强制重拉
    python3 fetch/opend/plates.py --codes US.LIST23925,US.LIST2047
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fetch.opend.futu_data import DEFAULT_ROOT  # noqa: E402

__all__ = [
    "MEMBERS_FILE", "DEFAULT_MARKET", "NOISE_WORDS",
    "plates_path", "read_members", "plate_items", "discover",
    "fetch_plates", "load_or_fetch", "prune_noise",
]

# **不是「产业链」的板块** —— 组合 / 榜单 / 事件类。按成交额排时它们会顶到最前面
# （`ARK持仓` 1805 亿$、`佩洛西持仓` 1172 亿$），但「谁谁谁的持仓」不是一条链，得滤掉。
NOISE_WORDS = (
    "持仓", "合集", "ETF", "定投", "碎股", "列表",                     # 组合类
    "昨日", "热门中概股", "知名零售商", "股息贵族", "避险资产", "ESG",  # 榜单 / 风格
    "圣诞节", "黑色星期五", "双十一", "降息", "危机",                  # 事件类
    "特朗普", "BATMMAAN",                                             # 不是一条链
)

# 成分股（慢变，不按天）—— 别叫 `plates.json`，那是当天产物
# （`data/scan/<日期>/plates.json` = 板块的**集体行为**，和 `industry.json` 平行）。
MEMBERS_FILE = "plate_members.json"
DEFAULT_MARKET = "US"


def plates_path(root=None):
    """成分股表落在 `data/plate_members.json` —— **不进当天文件夹**（变化很慢）。"""
    return (Path(root) if root else DEFAULT_ROOT) / MEMBERS_FILE


def read_members(root=None):
    """**只读缓存，不联网** —— 没有就返回 None，让调用方提示去跑 `plates.py`。

    分析阶段（`scan.py` / `analyze.py`）走这个，**不在管线里偷偷连 OpenD**。
    """
    path = plates_path(root)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _bare(code, market=DEFAULT_MARKET):
    """`US.MU` → `MU`。**只剥市场前缀** —— `US.MOG.A` 不能被切成 `A`。"""
    code = str(code or "")
    prefix = f"{market}."
    return code[len(prefix):] if code.startswith(prefix) else code


def plate_items():
    """自选里的板块（`PLATE`）—— 「我重点观察的行业 / 概念」就是这些。"""
    from engine.screener.local import load_all

    return [{"symbol": d.get("symbol"), "name": d.get("name")}
            for d in load_all(types=("PLATE",)) if d.get("symbol")]


def discover(market=DEFAULT_MARKET, *, top=30, min_turnover=1e10,
             gateway=None, verbose=True):
    """按**成交额**排出美股概念板块，滤掉组合 / 榜单 / 事件类，取前 `top` 个。

    成交额 = **资金关注度**，比「我觉得这个热」客观。**要 OpenD**
    （`plate` + `snapshot` 两族，**都不吃历史K线额度**）。

    返回 `[{"symbol", "name", "turnover"}]`，可直接喂 `load_or_fetch`。
    """
    from futu_algo.futu_gateway import QuoteGateway

    own = gateway is None
    gw = gateway or QuoteGateway()
    try:
        _, lst = gw.call("plate", "get_plate_list",
                         lambda ctx: ctx.get_plate_list(market, "CONCEPT"))
        codes = lst["code"].tolist()
        names = dict(zip(lst["code"], lst["plate_name"]))
        snaps = {}
        for i in range(0, len(codes), 100):  # 分批，别一次塞太多
            _, df = gw.call("snapshot", "snap",
                            lambda ctx, b=codes[i:i + 100]: ctx.get_market_snapshot(b))
            for r in df.to_dict("records"):
                snaps[r["code"]] = r
    finally:
        if own:
            gw.close()

    out = []
    for c in codes:
        name = names.get(c) or ""
        if any(w in name for w in NOISE_WORDS):
            continue
        turn = (snaps.get(c) or {}).get("turnover") or 0
        if turn < min_turnover:
            continue
        out.append({"symbol": c, "name": name, "turnover": turn})
    out.sort(key=lambda r: -r["turnover"])
    if verbose:
        print(f"  发现 {len(out)} 个候选（成交额 ≥ {min_turnover / 1e8:,.0f} 亿$），取前 {top}")
    return out[:top]


def _members(gw, plate_code, market=DEFAULT_MARKET):
    """一个板块的成分股 —— `get_plate_stock` **一次返回全部**（SDK 不分页）。

    返回的是 DataFrame（列：`code` / `stock_name` / `stock_type` / …）。
    """
    result = gw.call("plate", f"get_plate_stock({plate_code})",
                     lambda ctx: ctx.get_plate_stock(plate_code))
    data = result[1]
    rows = data.to_dict("records") if hasattr(data, "to_dict") else (data or [])
    return [_bare(r.get("code"), market) for r in rows if r.get("code")]


def fetch_plates(items, *, market=DEFAULT_MARKET, gateway=None, verbose=True):
    """`[{"symbol", "name"}]` → `{板块代码: {"name": …, "codes": [...]}}`。"""
    from futu_algo.futu_gateway import QuoteGateway

    own = gateway is None
    gw = gateway or QuoteGateway()
    try:
        out = {}
        for it in items:
            code = it.get("symbol")
            if not code:
                continue
            codes = _members(gw, code, market)
            out[code] = {"name": it.get("name") or code, "codes": codes}
            if verbose:
                print(f"  {code:<14}{str(it.get('name'))[:20]:<22}{len(codes):>4} 只")
        return out
    finally:
        if own:
            gw.close()


def _dump(doc):
    """**一个板块一行** —— 代码列表内联，人能读、diff 也看得清。"""
    lines = ["{", f'  "market": "{doc["market"]}",',
             f'  "fetched_at": "{doc["fetched_at"]}",', '  "plates": {']
    items = list(doc["plates"].items())
    for i, (code, blk) in enumerate(items):
        comma = "" if i == len(items) - 1 else ","
        name = json.dumps(blk.get("name"), ensure_ascii=False)
        codes = json.dumps(blk.get("codes") or [])
        lines.append(f'    "{code}": {{"name": {name}, "codes": {codes}}}{comma}')
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def load_or_fetch(items=None, *, market=DEFAULT_MARKET, root=None,
                  refresh=False, gateway=None, verbose=True):
    """缓存覆盖了要的板块就直接复用，否则拉一次落盘（**并保留上次拉的**）。"""
    path = plates_path(root)
    cached = read_members(root)
    items = plate_items() if items is None else items
    want = {i["symbol"] for i in items if i.get("symbol")}
    have = set((cached or {}).get("plates") or {})
    if cached and not refresh and want <= have:
        if verbose:
            print(f"复用 {path}（{len(have)} 个板块，拉于 {cached.get('fetched_at')}）")
        return cached

    if verbose:
        miss = sorted(want - have)
        print(f"拉 {len(want)} 个板块的成分股" + (f"（其中 {len(miss)} 个是新的）" if miss else ""))
    plates = fetch_plates(items, market=market, gateway=gateway, verbose=verbose)
    if cached and not refresh:
        plates = {**cached["plates"], **plates}  # 别把上次拉的丢了
    doc = {"market": market,
           "fetched_at": datetime.now().isoformat(timespec="seconds"),
           "plates": plates}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(doc), encoding="utf-8")
    if verbose:
        print(f"落盘 {path}（{len(plates)} 个板块）")
    return doc


def prune_noise(*, root=None, verbose=True):
    """把**名字命中 `NOISE_WORDS`** 的板块从成分股表里删掉。

    滤词表改过之后（比如后来觉得「特朗普概念股」不算一条链），缓存里还留着旧的就是脏的。
    `load_or_fetch` 只会**合并**、不会删 —— 所以清一次要用这个。
    """
    path = plates_path(root)
    doc = read_members(root)
    if not doc:
        if verbose:
            print(f"没有 {path} —— 先跑 `python3 fetch/opend/plates.py`")
        return None
    plates = doc.get("plates") or {}
    drop = [c for c, b in plates.items()
            if any(w in (b.get("name") or "") for w in NOISE_WORDS)]
    if not drop:
        if verbose:
            print(f"没有要清的（{len(plates)} 个板块都干净）")
        return doc
    names = [plates[c].get("name") or c for c in drop]
    for c in drop:
        del plates[c]
    doc["plates"] = plates
    doc["pruned_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(_dump(doc), encoding="utf-8")
    if verbose:
        print(f"清掉 {len(drop)} 个：{'、'.join(names)}")
        print(f"剩 {len(plates)} 个 → {path}")
    return doc


def main():
    ap = argparse.ArgumentParser(description="拉板块成分股（概念板块 / 行业板块）")
    ap.add_argument("--market", default=DEFAULT_MARKET)
    ap.add_argument("--codes", default=None,
                    help="逗号分隔的板块代码；不传 = 自选里那批 `PLATE`")
    ap.add_argument("--discover", type=int, default=None, metavar="N",
                    help="按**成交额**发现 N 个热门概念板块（滤掉持仓 / 榜单 / 事件类）")
    ap.add_argument("--min-turnover", type=float, default=1e10,
                    help="发现模式的最低成交额，默认 100 亿$")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，强制重拉")
    ap.add_argument("--prune", action="store_true",
                    help="把名字命中滤词表的板块从缓存里**删掉**（改过滤词表之后清一次）")
    args = ap.parse_args()

    if args.prune:
        prune_noise()
        return

    if args.discover:
        items = discover(market=args.market, top=args.discover,
                         min_turnover=args.min_turnover)
        for it in items:
            print(f"    {it['symbol']:<14}{it['name']:<20}{it['turnover'] / 1e8:>10,.0f} 亿$")
    elif args.codes:
        items = [{"symbol": c.strip(), "name": None}
                 for c in args.codes.split(",") if c.strip()]
    else:
        items = None
    doc = load_or_fetch(items, market=args.market, refresh=args.refresh)
    print()
    for code, blk in doc["plates"].items():
        print(f"  {code:<14}{str(blk.get('name'))[:20]:<22}{len(blk.get('codes') or []):>4} 只")


if __name__ == "__main__":
    main()
