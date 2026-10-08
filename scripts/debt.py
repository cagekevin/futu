#!/usr/bin/env python3
"""债务账本读写**唯一入口** —— 主表 `债务.md`（只留待办）+ 归档 `债务-归档.md`（已完成）。

【为什么存在】`债务.md` 若有两个手写点（登记时追加 + 改状态时再追一行），必然漂移：
  同 ID 两行、归类写法几十种、`|` 转义被误判、裸 `|` 撑破列致错位。
  收口判据：可写点 ≥2 且已漂移过 → 该收。故**写入者唯一 = 本脚本**。

【机制：写入口，不是闸】`add`/`resolve` 在**落盘前**拒非法枚举 / 含 `|` 的摘要 / 不存在的锚点 —— 表单校验，
  **不挂 CI**（写文档要的是**规范**，不是门禁）。

【活账 / 死账分离】`archive` 把已完成项移入 `债务-归档.md` → 主表只剩待办。
  读命令**默认跨两者**：查历史债用 `--all` / `--area` / `search`，不必手读归档文件。

【取号口径 = 主表 ∪ 归档 ∪ 全文归档】`add` 的"区内 max 序号"与"区域名"都取自 `loadAll()`，
  并在落盘前加**取号自检**（新 ID 已存在 → 拒写）。
  ⚠️ 只查主表 ⇒ 某区历史债全部归档后 maxN 归零 ⇒ 下次登记必撞已归档 ID；又因 `loadAll()` 按 ID 去重
  （主表优先），撞号会**静默遮蔽**归档同名债 = 账本静默腐坏。

【锚点口径 = 只认区域日志】锚点必须是无路径分隔符的区域日志命名 `<NN>-…-<日期>.md`（跨区轮次 `20-跨区-…` 同样匹配）。
  判据（`anchorProblem()`，写入前 + `audit` 只读巡检**共用同一份**）：
  ① 不含路径分隔符（堵死 `../…` 逃逸）；② 命名是区域日志；③ 文件真的存在。

【列错位为什么能无损解析】`split('|')` 与 `join('|')` 互逆：只要重新定出正确列边界，描述里的裸 `|`
  会被逐字还原。靠**四级校验**（标准 / A 尾部多余段 / B 右锚定 / C 左锚定状态起点），任一级不过就报 `manual`，**绝不猜**。

用法（读）：
  python scripts/debt.py list [--area 22] [--status 待还] [--class 增债] [--all] [--json]
    · 默认 = 主表待办；给了 `--area`/`--status`/`--class`/`--all` 之一 = **跨主表 + 归档**筛
  python scripts/debt.py area <NN>            # 某区**全部**历史债（含归档）
  python scripts/debt.py search <关键词> [--area 22]   # 跨区找同类问题（多词 AND）
  python scripts/debt.py show <TD-ID>         # 单条最新真相 + **解法入口**（锚点区域日志）
  python scripts/debt.py audit [--liveness]   # 只读体检（**不是闸**）
用法（写 / 维护）：
  python scripts/debt.py add --area 22 --summary "…" [--class 增债] [--rate 中] [--owner 结构债] [--anchor <区域文件>] [--refs "@见 TD-xx"]
  python scripts/debt.py resolve <TD-ID> [--status 已解决] [--note "…"] [--date YYYY-MM-DD]
  python scripts/debt.py edit <TD-ID> --field summary|solution|classify|rate --value "…"
  python scripts/debt.py reanchor <TD-ID> --anchor <区域文件>
  python scripts/debt.py move <TD-ID> --to <NN>
  python scripts/debt.py archive [--dry]
  python scripts/debt.py fix [--dry]
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "daily" / "架构日志"
LEDGER = LOG_DIR / "债务.md"
ARCHIVE = LOG_DIR / "债务-归档.md"

ADR_NUDGE = "   ↳ 读债前先过 `docs/adr/README.md` 索引 —— 哪些既有约定已被**推翻**（铁律 6：既有约定不是判据来源）"

# ── 规范真源（唯一）：改枚举 = 改这里 + 两份流程文档 ─────────────────────────
CLASSES = ["还债", "持平", "增债"]
RATES = ["高", "中", "低"]
OWNERS = ["结构债", "业务债"]
STATUS_WORDS = ["待还", "待用户拍板", "已解决", "已裁定非债", "已裁定", "待翻新", "已退役", "不做"]
TODO_WORDS = ["待还", "待用户拍板", "待翻新"]
DONE_WORDS = ["已解决", "已裁定", "已裁定非债", "已退役", "不做"]
STATUS_ALIAS = {
    "已自纠": "已解决", "已办": "已解决", "已闭": "已解决", "已修": "已解决",
    "待 G5 接线": "待还", "待人工跑": "待还", "待回改": "待还", "待做": "待还",
    "部分完成": "待还", "迁入中": "待还",
}
CLASS_ALIASES = {
    "还债+持平": "还债", "还债（源头收敛·设计+重构）": "还债", "还基（规则修正）": "还债",
    "增债→还": "增债", "持平→已还": "持平", "持平→待翻新": "持平", "持平→待还": "持平",
    "持平（半成品）": "持平", "持平（M1 必需项缺位）": "持平",
    "增债→不做": "增债", "增债→删": "增债", "增债→不建/复用": "增债", "增债→并入": "增债",
    "增债→建": "增债", "增债→已修": "增债", "增债→已办": "增债", "增债→升级": "增债",
    "增债→非债": "持平", "非债": "持平", "设计观察": "持平", "待还": "持平", "累计": "增债",
}
RATE_ALIASES = {"中高": "高", "低-中": "中", "—": "低", "-": "低"}

SYNONYMS = {
    "SSOT": ["双真相", "第二份", "两份真相", "同语义两处", "两处维护", "重复实现", "两处实现", "同语义两实现", "抄成多份"],
    "双真相": ["SSOT", "第二份", "两份"],
    "静默": ["吞错", "catch", "零日志", "不报错", "静默失败", "无信号", "吞掉", "无声"],
    "吞错": ["静默", "catch", "零日志"],
    "假收窄": ["as any", "as never", "as unknown", "断言", "绕过类型"],
    "死代码": ["0 引用", "零消费", "死导出", "死参数", "无人调用", "没用", "残留"],
    "唯一入口": ["绕过", "旁路", "直调", "入口", "绕过入口"],
    "兜底": ["回退", "降级", "fallback", "退化", "兜底式"],
    "假成功": ["伪装", "假绿", "谎报", "不报错"],
    "注释漂移": ["描述漂移", "失实注释", "过时注释", "stale"],
    "漏网": ["未覆盖", "盲区", "回潮", "复发"],
}
SYN_REV: dict[str, list[str]] = {}
for _k, _vs in SYNONYMS.items():
    for _v in _vs:
        SYN_REV.setdefault(_v.lower(), []).append(_k)


def expand_word(w: str) -> list[str]:
    out = {w}
    for v in SYNONYMS.get(w, []):
        out.add(v)
    for k in SYN_REV.get(w.lower(), []):
        out.add(k)
        out.update(SYNONYMS[k])
    return list(out)


MAP = {
    "TD": {"id": 0, "area": 1, "summary": 2, "class": 3, "rate": 4, "status": 5, "anchor": 6, "cols": 7},
    "MD": {"id": 0, "summary": 1, "class": 2, "status": 3, "anchor": 4, "cols": 5},
}
TABLE_ROW = re.compile(r"^\|\s*\*{0,2}(TD|MD)-\d+-\d+")
ESC = "\u0001"


def today() -> str:
    return date.today().isoformat()


def strip_bold(s) -> str:
    return re.sub(r"^\*+|\*+$", "", str(s if s is not None else "").strip()).strip()


def fail(msg: str) -> None:
    print("❌ " + msg, file=sys.stderr)
    sys.exit(1)


def arg_val(argv: list[str], flag_name: str) -> str:
    if flag_name in argv:
        i = argv.index(flag_name)
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            return argv[i + 1]
    return ""


# ── 解析（含列错位四级校验）─────────────────────────────────────────────────
def is_classish(s) -> bool:
    t = strip_bold(s)
    return t in CLASSES or t in CLASS_ALIASES or bool(re.match(r"^(还债|持平|增债)", t))


def is_rateish(s) -> bool:
    t = strip_bold(s)
    return t in RATES or t in RATE_ALIASES or bool(re.match(r"^[高中低]-?[高中低]", t)) or bool(re.match(r"^(高|中|低|—|-)", t))


def is_status_shape(s) -> bool:
    return bool(re.match(r"^\*{0,2}\[(已裁定非债|已解决|已裁定|待用户拍板|待翻新|已退役|待还|不做)",
                         str(s if s is not None else "").strip()))


def is_anchorish(s) -> bool:
    t = str(s if s is not None else "").strip()
    return bool(re.match(r"^\*{0,2}\[[^\]]*\]\([^)]*\)", t)) or strip_bold(s) == "—"


def read_row(line: str):
    if not TABLE_ROW.match(line):
        return None
    kind = TABLE_ROW.match(line).group(1)
    parts = [x.replace(ESC, "\\|") for x in line.replace("\\|", ESC).split("|")]
    if parts[0] != "" or parts[-1].strip() != "":
        return {"broken": True, "kind": kind, "parts": parts}
    inner = parts[1:-1]
    C = MAP[kind]

    def fields(arr):
        g = lambda k: (arr[C[k]] if C[k] < len(arr) else "")
        return {"kind": kind, "id": strip_bold(g("id")), "area": g("area").strip(),
                "summary": g("summary").strip(), "class": g("class").strip(),
                "rate": (g("rate").strip() if "rate" in C else ""),
                "status": g("status").strip(), "anchor": g("anchor").strip()}

    if len(inner) == C["cols"]:
        return {"kind": kind, "parts": parts, "inner": inner, "fields": fields(inner), "mode": "ok"}
    n = len(inner)
    if kind != "TD" or n < C["cols"]:
        return {"kind": kind, "parts": parts, "inner": inner, "fields": fields(inner), "mode": "manual"}
    # A：左端标准位置正确 + 尾部多段（同锚点被贴两次）
    if is_classish(inner[3]) and is_rateish(inner[4]) and is_status_shape(inner[5]) and all(is_anchorish(x) for x in inner[6:]):
        fixed = inner[:6] + [" · ".join(x.strip() for x in inner[6:] if x.strip())]
        return {"kind": kind, "parts": parts, "inner": inner, "fields": fields(fixed), "mode": "A", "fixedInner": fixed}
    # B：右锚定（末 4 段 = 归类 / 利率 / 状态 / 锚点）
    if is_classish(inner[n - 4]) and is_rateish(inner[n - 3]) and is_status_shape(inner[n - 2]) and is_anchorish(inner[n - 1]):
        fixed = [inner[0], inner[1], "|".join(inner[2:n - 4]).strip(), inner[n - 4].strip(),
                 inner[n - 3].strip(), inner[n - 2].strip(), inner[n - 1].strip()]
        return {"kind": kind, "parts": parts, "inner": inner, "fields": fields(fixed), "mode": "B", "fixedInner": fixed}
    # C：左找"状态起点" + 右锚定锚点
    if is_anchorish(inner[n - 1]):
        for i in range(4, n - 1):
            if not is_status_shape(inner[i]) or not is_classish(inner[i - 2]) or not is_rateish(inner[i - 1]):
                continue
            status = "｜".join(inner[i:n - 1])
            if not status_word(status):
                continue
            fixed = [inner[0], inner[1], "|".join(inner[2:i - 2]).strip(), inner[i - 2].strip(),
                     inner[i - 1].strip(), status.strip(), inner[n - 1].strip()]
            return {"kind": kind, "parts": parts, "inner": inner, "fields": fields(fixed), "mode": "C", "fixedInner": fixed}
    return {"kind": kind, "parts": parts, "inner": inner, "fields": fields(inner), "mode": "manual"}


# ── 枚举归一 ────────────────────────────────────────────────────────────────
def norm_class(v) -> dict:
    s = strip_bold(v)
    if s in CLASSES:
        return {"value": s}
    if s in CLASS_ALIASES:
        return {"value": CLASS_ALIASES[s], "from": str(v).strip()}
    m = re.match(r"^(还债|持平|增债)", s)
    return {"value": m.group(1), "from": str(v).strip()} if m else {"unknown": True, "value": s}


def norm_rate(v) -> dict:
    raw = str(v if v is not None else "").strip()
    if not raw:
        return {"value": ""}
    s = strip_bold(raw)
    if s in RATES:
        return {"value": s}
    if s in RATE_ALIASES:
        return {"value": RATE_ALIASES[s], "from": raw}
    if re.match(r"^[中高]-?[中高]|^中高", s):
        return {"value": "高", "from": raw}
    if re.match(r"^低-中|^中-低", s):
        return {"value": "中", "from": raw}
    m = re.match(r"^(高|中|低)", s)
    return {"value": m.group(1), "from": raw} if m else {"unknown": True, "value": s}


def norm_status(v) -> dict:
    raw = str(v if v is not None else "").strip()
    if not raw:
        return {"value": raw}
    tail = re.sub(r"^\*{0,2}\[\*{0,2}", "", raw)
    for k in STATUS_ALIAS:
        if tail.startswith(k):
            return {"value": f"[{STATUS_ALIAS[k]}{tail[len(k):]}", "from": raw}
    if is_status_shape(raw):
        return {"value": raw}
    bare = strip_bold(raw)
    m = re.match(r"^(已裁定非债|已解决|已裁定|待用户拍板|待翻新|已退役|待还|不做)(.*)$", bare)
    if m:
        return {"value": f"[{m.group(1)}{m.group(2)}]", "from": raw}
    if bare in STATUS_WORDS:
        return {"value": f"[{bare}]", "from": raw}
    return {"unknown": True, "value": raw}


def status_word(status: str) -> str:
    m = re.match(r"^\*{0,2}\[(已裁定非债|已解决|已裁定|待用户拍板|待翻新|已退役|待还|不做)",
                 norm_status(status)["value"])
    return m.group(1) if m else ""


def status_note(status: str, mx: int = 76) -> str:
    t = norm_status(status)["value"].replace("**", "")
    m = re.match(r"^\*{0,2}\[([^\]]+)\]", t)
    body = m.group(1) if m else t
    note = re.sub(r"^(已裁定非债|已解决|已裁定|待用户拍板|待翻新|已退役|待还|不做)\s*", "", body)
    note = re.sub(r"^\d{4}-\d{2}-\d{2}\s*", "", note)
    note = re.sub(r"^[·：:]\s*", "", note)
    if not note:
        return ""
    return note[:mx] + "…" if len(note) > mx else note


# ── 读取（主表 + 归档 + 全文归档）───────────────────────────────────────────
def parse_text(text: str, src: str) -> list[dict]:
    rows = []
    for li, line in enumerate(re.split(r"\r?\n", text)):
        r = read_row(line)
        if r and not r.get("broken"):
            rows.append({"li": li, "line": line, "src": src, **r})
    return rows


def load_ledger() -> dict:
    text = LEDGER.read_text(encoding="utf-8")
    return {"text": text, "lines": re.split(r"\r?\n", text), "rows": parse_text(text, "main")}


def load_all() -> list[dict]:
    main = LEDGER.read_text(encoding="utf-8")
    arch = ARCHIVE.read_text(encoding="utf-8") if ARCHIVE.exists() else ""
    full_archives = [
        (LOG_DIR / n).read_text(encoding="utf-8")
        for n in sorted(os.listdir(LOG_DIR)) if re.match(r"^债务-全文归档-.*\.md$", n)
    ]
    by_id: dict[str, dict] = {}
    for r in ([*parse_text(main, "main"), *parse_text(arch, "archive"),
               *[x for t in full_archives for x in parse_text(t, "full")]]):
        by_id.setdefault(r["fields"]["id"], r)
    return list(by_id.values())


SHAPES = [
    ("注释/文档漂移", re.compile(r"注释|漂移|过期|失真|失实")),
    ("静默吞/失败伪装成功", re.compile(r"静默|伪装|吞")),
    ("死代码/幽灵预留", re.compile(r"死代码|死参数|零消费|零引用|幽灵|预留|预支")),
    ("绕过唯一入口", re.compile(r"绕过|旁路|裸调|裸写|直调")),
    ("SSOT 第二份", re.compile(r"第二份|第二真相|SSOT|双源|同语义两|两份")),
    ("口径错位", re.compile(r"口径|不一致|对不上|错位")),
    ("假护栏/假守卫", re.compile(r"假护栏|假守卫|假豁免|恒真|恒绿|恒空")),
    ("复发/回潮", re.compile(r"复发|回潮|再犯")),
]
FIXES = [
    ("收口到唯一实现", re.compile(r"收口|唯一实现|下沉|委托|单点|单源")),
    ("先红后绿", re.compile(r"探针|先红后绿")),
    ("契约钉死", re.compile(r"契约|唯一真相|钉死")),
    ("删除", re.compile(r"已删|删除|删掉|删死")),
    ("加机器闸", re.compile(r"加闸|补闸|机器守卫|新增规则|闸")),
    ("改判非债", re.compile(r"改判非债|非债|已裁定")),
    ("用户裁定", re.compile(r"用户裁定|待用户拍板")),
]


def cmd_stats() -> None:
    rows = load_all()
    hit = lambda re_: len([r for r in rows if re_.search(r["line"])])  # noqa: E731
    cls = lambda w: len([r for r in rows if re.search(r"\|\s*\**" + w + r"\s*\**\|", r["line"])])  # noqa: E731
    print(f"📊 债务形态统计（主表 ∪ 归档，共 {len(rows)} 条）")
    print(f"   归类：还债 {cls('还债')} · 持平 {cls('持平')} · 增债 {cls('增债')}")
    print(f"   状态：已解决 {hit(re.compile('已解决'))} · 改判非债 {hit(re.compile('非债|已裁定'))} · 待还 {hit(re.compile('待还'))}")
    print("\n【问题形态】按频次降序 —— 高频的先用 grep 扫（检出信号见 债务登记5步法 §3.2）")
    for name, re_ in sorted(SHAPES, key=lambda x: -hit(x[1])):
        print(f"   {hit(re_):>4}  {name}")
    print("\n【解法分布】按频次降序 —— 排前的是本仓真正管用的手法（见 架构师改码7步法 附 B）")
    for name, re_ in sorted(FIXES, key=lambda x: -hit(x[1])):
        print(f"   {hit(re_):>4}  {name}")


def id_num(i: str) -> int:
    m = re.match(r"^TD-(\d+)-", i)
    return int(m.group(1)) if m else -1


def id_seq(i: str) -> int:
    m = re.match(r"^TD-\d+-(\d+)", i)
    return int(m.group(1)) if m else 0


def id_key(i: str) -> int:
    return id_num(i) * 1000 + id_seq(i)


def src_tag(r: dict) -> str:
    return "（归档）" if r["src"] == "archive" else ""


def print_row(r: dict) -> None:
    f = r["fields"]
    note = status_note(f["status"])
    s = f["summary"]
    sum_ = s[:84] + "…" if len(s) > 84 else s
    print(f"{f['id']} ｜ {f['area']} ｜ [{status_word(f['status']) or '?'}]{src_tag(r)} {('· ' + note) if note else ''}")
    print(f"        {norm_class(f['class'])['value']} / {norm_rate(f['rate'])['value']} ｜ {sum_}")


def load_main_count() -> int:
    return len([r for r in load_ledger()["rows"] if r["fields"]["kind"] == "TD"])


def cmd_list(argv: list[str]) -> None:
    area, status = arg_val(argv, "--area"), arg_val(argv, "--status")
    cls, rate = arg_val(argv, "--class"), arg_val(argv, "--rate")
    as_json, all_, archived = "--json" in argv, "--all" in argv, "--archived" in argv
    rows = [r for r in load_all() if r["fields"]["kind"] == "TD" and r["src"] != "full"]
    filtered = bool(area or status or cls or rate or all_ or archived)
    sel = rows
    if archived:
        sel = [r for r in sel if r["src"] == "archive"]
    elif not filtered:
        sel = [r for r in sel if r["src"] == "main" and status_word(r["fields"]["status"]) in TODO_WORDS]
    if area:
        sel = [r for r in sel if id_num(r["fields"]["id"]) == int(area)]
    if status:
        sel = [r for r in sel if status_word(r["fields"]["status"]) == status]
    if cls:
        sel = [r for r in sel if norm_class(r["fields"]["class"])["value"] == cls]
    if rate:
        sel = [r for r in sel if norm_rate(r["fields"]["rate"])["value"] == rate]
    sel.sort(key=lambda r: id_key(r["fields"]["id"]))

    if as_json:
        print(json.dumps([{**r["fields"], "src": r["src"], "line": r["li"] + 1} for r in sel],
                         ensure_ascii=False, indent=2))
        return
    scope = "主表待办" if not filtered else ("仅归档" if archived else "主表 + 归档")
    all_td = len([r for r in load_all() if r["fields"]["kind"] == "TD"])
    print(f"🔎 debt list ｜ {scope} ｜ 命中 {len(sel)} 条（全档 {all_td} · 主表 {load_main_count()}）")
    if not sel:
        print("   （无匹配记录）")
        return
    for r in sel:
        print_row(r)
    if not filtered:
        print("   ↳ 查历史债加 `--all` / `--area <NN>` / `search <关键词>`（历史在 `债务-归档.md`，本命令已打通）")
    print(ADR_NUDGE)


def cmd_area(argv: list[str]) -> None:
    nn = next((a for a in argv if not a.startswith("--")), None)
    if not nn:
        fail("用法：python scripts/debt.py area <NN>（如 area 22）")
    cmd_list(["--area", nn, "--all"])


def cmd_search(argv: list[str]) -> None:
    kw = argv[0] if argv and not argv[0].startswith("--") else arg_val(argv, "--q")
    if not kw:
        fail("用法：python scripts/debt.py search <关键词> [--area 22]（多词 = AND；精确无命中时自动同义词扩展）")
    area = arg_val(argv, "--area")
    words = [w for w in re.split(r"\s+", kw) if w]
    pool = [r for r in load_all() if r["fields"]["kind"] == "TD" and r["src"] != "full"]
    if area:
        pool = [r for r in pool if id_num(r["fields"]["id"]) == int(area)]

    def hay(r):
        f = r["fields"]
        return f"{f['id']} {f['area']} {f['summary']} {f['status']}"

    syn = False
    sel = [r for r in pool if all(w in hay(r) for w in words)]
    if not sel:
        syn = True
        sel = [r for r in pool if all(any(v.lower() in hay(r).lower() for v in expand_word(w)) for w in words)]
    sel.sort(key=lambda r: id_key(r["fields"]["id"]))
    in_main = len([r for r in sel if r["src"] == "main"])
    ext = " + ".join(
        (lambda e: f"{w}〔≈ {' / '.join(e)}〕" if e else w)([x for x in expand_word(w) if x != w])
        for w in words
    ) if syn else " + ".join(words)
    print(f"🔎 debt search「{ext}」{f' in 区 {area}' if area else ''}{'［同义词扩展］' if syn else ''} ｜ "
          f"命中 {len(sel)} 条（主表 {in_main} · 归档 {len(sel)-in_main}）")
    if not sel:
        print("   （无匹配）换个词试试：母体名（SSOT / 静默 / 假收窄 / 死代码 / 唯一入口 / 兜底 / 假成功 / 注释漂移）、")
        print("   症状（刷新 / 破图 / 丢输入 / 残留 / 回弹）、或文件名片段。")
        print("   兜底办法：`python scripts/debt.py area <NN>` 直接列该区全部历史债。")
        return
    for r in sel:
        print_row(r)
    print("   ↳ 某条的**完整解法 + 证据入口**：`python scripts/debt.py show <TD-ID>`")


def read_full_archive(id_: str):
    names = [n for n in os.listdir(LOG_DIR) if re.match(r"^债务-全文归档-.*\.md$", n)]
    if not names:
        return None
    raw = next((l for l in (LOG_DIR / names[0]).read_text(encoding="utf-8").split("\n")
                if f"| {id_} |" in l or f"**{id_}**" in l), None)
    if not raw:
        return None
    r = read_row(raw)
    return r if r and not r.get("broken") else None


def cmd_show(argv: list[str]) -> None:
    id_ = next((a for a in argv if re.match(r"^TD-\d+-\d+|^MD-\d+-\d+", a)), None)
    if not id_:
        fail("用法：python scripts/debt.py show <TD-ID>")
    hit = [r for r in load_all() if r["fields"]["id"] == id_]
    if not hit:
        fail(f"主表 ∪ 归档 ∪ 全文归档 中均无 {id_}")
    for r in hit:
        if r["src"] == "full":
            print(f"── {id_}（仅存于「全文归档」：账本压缩前的长叙述原文，无规范化行）")
            print("   ↳ 原文：daily/架构日志/债务-全文归档-*.md（搜该 ID）")
            continue
        f = r["fields"]
        warn = f" · 列错位已自动解析（{r['mode']}）" if r["mode"] in ("A", "B", "C") else (" · ⚠️ 列错位需人工" if r["mode"] == "manual" else "")
        print(f"── {f['id']}{src_tag(r)}{warn}")
        print(f"  状态   : [{status_word(f['status'])}]{re.sub(r'^\*{0,2}\[[^\]]*\]', '', f['status'])}")
        print(f"  归类   : {norm_class(f['class'])['value']}   利息率 : {norm_rate(f['rate'])['value']}   区域 : {f['area'] or ''}")
        print(f"  现象   : {f['summary']}")
        full = read_full_archive(id_)
        note = full["fields"]["status"] if full else f["status"]
        if note:
            print(f"  解法   : {note}")
        if full and full["fields"]["summary"] and full["fields"]["summary"] != f["summary"]:
            print(f"  现象·详: {full['fields']['summary']}")
        anchor_name = re.sub(r"^\**\[([^\]]+)\]\([^)]*\).*$", r"\1", f["anchor"])
        print(f"  入口   : daily/架构日志/{anchor_name}{'' if is_anchorish(f['anchor']) else '（锚点非标准形状，见原文）'}")
        if f["anchor"] and is_anchorish(f["anchor"]):
            print(f"           ↳ **下一跳**：该区域日志的「探债」段有最完整的决策、探针与证据（搜 `{id_}`）")
        if full:
            print("           ↳ 长叙述原文：daily/架构日志/债务-全文归档-*.md（搜该 ID）")


REGION_ANCHOR_RE = re.compile(r"^\d{2}-[^/\\|]+\.md$")


def anchor_problem(anchor: str) -> str:
    a = str(anchor if anchor is not None else "").strip()
    if not a or a == "—":
        return "锚点为空（明细必须落区域日志，禁 `—`）"
    if re.search(r"[/\\]", a):
        return f"锚点含路径分隔符（只准写 `daily/架构日志/` 下的文件名）：`{a}`"
    if not REGION_ANCHOR_RE.match(a):
        return f"锚点不是区域日志命名（`<NN>-<区域>-<日期>.md`）：`{a}`"
    if not (LOG_DIR / a).exists():
        return f"锚点文件不存在：daily/架构日志/{a}"
    return ""


def validate(cls, rate, owner, summary, anchor) -> None:
    errs = []
    if cls not in CLASSES:
        errs.append(f"归类 `{cls}` 不在白名单 [{' / '.join(CLASSES)}]")
    if rate and rate not in RATES:
        errs.append(f"利息率 `{rate}` 不在白名单 [{' / '.join(RATES)}]")
    if owner and owner not in OWNERS:
        errs.append(f"归属 `{owner}` 不在白名单 [{' / '.join(OWNERS)}]")
    if not summary:
        errs.append("摘要为空")
    if re.search(r"[|｜]", summary or ""):
        errs.append("摘要含竖线 → 用「／」代替（裸 `|` 会撑破表格列）")
    if summary and len(summary) > 120:
        errs.append(f"摘要 {len(summary)} 字 > 120（一行一债，长叙述写区域文件）")
    if anchor:
        p = anchor_problem(anchor)
        if p:
            errs.append(p)
    if errs:
        print("❌ 登记被拒（写入口校验，**不是闸**）：", file=sys.stderr)
        for e in errs:
            print("   · " + e, file=sys.stderr)
        sys.exit(1)


def cmd_add(argv: list[str]) -> None:
    area, summary = arg_val(argv, "--area"), arg_val(argv, "--summary")
    cls = arg_val(argv, "--class") or "增债"
    rate = arg_val(argv, "--rate") or "中"
    owner = arg_val(argv, "--owner") or "结构债"
    anchor = arg_val(argv, "--anchor")
    refs = arg_val(argv, "--refs")
    status = arg_val(argv, "--status") or "待还"
    if not area or not summary:
        fail('用法：add --area 22 --summary "…" [--class 增债] [--rate 中] [--anchor <区域文件>]')
    validate(cls, rate, owner, summary, anchor)
    text = load_ledger()["text"]
    nn = str(area)
    all_td = [r for r in load_all() if r["fields"]["kind"] == "TD"]
    same = [r for r in all_td if (re.match(r"^TD-(\d+)-", r["fields"]["id"]) or [None, None])[1] == nn]
    if any(r["src"] == "main" and r["mode"] == "manual" for r in same):
        fail('本区主表存在"列错位需人工"的行 → 先修，否则新 ID 可能撞号')
    max_n = max([0, *[id_seq(r["fields"]["id"]) for r in same]])
    id_ = f"TD-{nn}-{max_n + 1}"
    dup = next((r for r in all_td if r["fields"]["id"] == id_), None)
    if dup:
        fail(f"取号自检失败：{id_} 已存在于{'归档' if dup['src'] == 'archive' else '主表'} → 拒写（撞号会被 loadAll 静默遮蔽）")
    src_row = next((r for r in same if r["src"] == "main"), same[0] if same else None)
    area_raw = src_row["fields"]["area"] if src_row else ""
    area_text = re.sub(r"（@见[^）]*）", "", area_raw).strip() or nn
    anchor_text = f"[{anchor}](./{anchor})" if anchor else "—"
    row = f"| {id_} | {area_text}{f'（{refs}）' if refs else ''} | {summary} | {cls} | {rate} | [{status}] | {anchor_text} |"
    LEDGER.write_text(text.rstrip() + "\n" + row + "\n", encoding="utf-8")
    print(f"✅ 已登记 {id_}（{cls} · {rate} · {status}）")
    print("   " + row)


def cmd_resolve(argv: list[str]) -> None:
    id_ = next((a for a in argv if re.match(r"^TD-\d+-\d+|^MD-\d+-\d+", a)), None)
    if not id_:
        fail('用法：resolve <TD-ID> [--status 已解决] [--note "…"] [--date YYYY-MM-DD]')
    status = arg_val(argv, "--status") or "已解决"
    if status not in STATUS_WORDS:
        fail(f"状态 `{status}` 不在白名单 [{' / '.join(STATUS_WORDS)}]")
    note = arg_val(argv, "--note")
    dt = arg_val(argv, "--date") or today()
    if re.search(r"[|｜]", note):
        fail("note 含竖线 → 用「／」代替（裸管道符会撑破表格列）")
    main = load_ledger()
    targets = [{"file": LEDGER, "label": "主表", "lines": main["lines"], "rows": main["rows"]}]
    if ARCHIVE.exists():
        arch_text = ARCHIVE.read_text(encoding="utf-8")
        targets.append({"file": ARCHIVE, "label": "归档", "lines": re.split(r"\r?\n", arch_text),
                        "rows": parse_text(arch_text, "archive")})
    for t in targets:
        hit = [r for r in t["rows"] if r and not r.get("broken") and r.get("fields") and r["fields"]["id"] == id_]
        if not hit:
            continue
        if len(hit) > 1:
            fail(f"{id_} 在{t['label']}出现 {len(hit)} 次（重复 ID）→ 先处理重复")
        r = hit[0]
        if r["mode"] == "manual":
            fail(f"{id_} 在{t['label']}是「列错位需人工」行 → 先修列，再改状态")
        C = MAP[r["kind"]]
        parts = t["lines"][r["li"]].split("|")
        new_status = f"[{status} {dt}{' · ' + note if note else ''}]"
        parts[1 + C["status"]] = f" {new_status} "
        t["lines"][r["li"]] = "|".join(parts)
        t["file"].write_text("\n".join(t["lines"]), encoding="utf-8")
        print(f"✅ {id_} → {new_status}")
        print(f"   旧状态：{r['fields']['status']}")
        if t["label"] == "主表":
            print("   ↳ 收尾别忘了 `python scripts/debt.py archive`（把已完成项移入归档，主表只留待办）")
        else:
            print("   ↳ 改的是**归档**行（主表已无此 ID）；若只是修状态文案，无需再 `archive`")
        return
    fail(f"主表与归档中均无 {id_}")


def cmd_reanchor(argv: list[str]) -> None:
    id_ = next((a for a in argv if re.match(r"^TD-\d+-\d+|^MD-\d+-\d+", a)), None)
    anchor = arg_val(argv, "--anchor")
    if not id_ or not anchor:
        fail("用法：reanchor <TD-ID> --anchor <区域文件>（如 22-视频-剪辑器-M2计划审计-2026-09-14.md）")
    problem = anchor_problem(anchor)
    if problem:
        fail(f"锚点被拒（写入口校验，**不是闸**）：\n   · {problem}")
    files = [{"file": LEDGER, "src": "主表", "lines": load_ledger()["lines"]}]
    if ARCHIVE.exists():
        files.append({"file": ARCHIVE, "src": "归档", "lines": re.split(r"\r?\n", ARCHIVE.read_text(encoding="utf-8"))})
    for f in files:
        hits = [(read_row(l), i) for i, l in enumerate(f["lines"])]
        hits = [(r, i) for r, i in hits if r and not r.get("broken") and r.get("fields") and r["fields"]["id"] == id_]
        if not hits:
            continue
        hit = next(((r, i) for r, i in hits if r["mode"] != "manual"), None)
        if not hit:
            fail(f"{id_} 在{f['src']}是「列错位需人工」行 → 先修列，再改锚点")
        r, li = hit
        C = MAP[r["kind"]]
        parts = f["lines"][li].split("|")
        old = r["fields"]["anchor"]
        parts[1 + C["anchor"]] = f" [{anchor}](./{anchor}) "
        f["lines"][li] = "|".join(parts)
        f["file"].write_text("\n".join(f["lines"]), encoding="utf-8")
        print(f"✅ {id_} 锚点已改（{f['src']}）")
        print(f"   旧：{old}")
        print(f"   新：[{anchor}](./{anchor})")
        print("   ↳ 别忘了在新锚点文件的「探债」段补该债的明细（叙述不进账本）")
        return
    fail(f"主表与归档中均无 {id_}")


EDIT_FIELDS = {"summary": "现象", "solution": "解法", "classify": "归类", "rate": "利息率"}


def cmd_edit(argv: list[str]) -> None:
    id_ = next((a for a in argv if re.match(r"^TD-\d+-\d+|^MD-\d+-\d+", a)), None)
    field, value = arg_val(argv, "--field"), arg_val(argv, "--value")
    if not id_ or not field or not value:
        fail('用法：edit <TD-ID> --field summary|solution|classify|rate --value "…"')
    if field not in EDIT_FIELDS:
        fail("--field 只支持 `summary`（现象）／`solution`（解法）／`classify`（归类）／`rate`（利息率）；ID/区/状态/锚点请用 move / resolve / reanchor")
    if field == "classify" and value not in CLASSES:
        fail(f"归类 `{value}` 不在白名单 [{' / '.join(CLASSES)}]（带「→ 状态流转」的旧写法请填箭头前的那个值）")
    if field == "rate" and value not in RATES:
        fail(f"利息率 `{value}` 不在白名单 [{' / '.join(RATES)}]")
    if re.search(r"[|｜]", value):
        fail("value 含竖线 → 用「／」代替（裸管道符会撑破表格列）")
    if field == "summary" and len(value) > 120:
        fail(f"现象 {len(value)} 字 > 120（一行一债，长叙述写区域文件）")

    files = [{"file": LEDGER, "src": "主表", "lines": load_ledger()["lines"]}]
    if ARCHIVE.exists():
        files.append({"file": ARCHIVE, "src": "归档", "lines": re.split(r"\r?\n", ARCHIVE.read_text(encoding="utf-8"))})
    for f in files:
        hits = [(read_row(l), i) for i, l in enumerate(f["lines"])]
        hits = [(r, i) for r, i in hits if r and not r.get("broken") and r.get("fields") and r["fields"]["id"] == id_]
        if not hits:
            continue
        hit = next(((r, i) for r, i in hits if r["mode"] != "manual"), None)
        if not hit:
            fail(f"{id_} 在{f['src']}是「列错位需人工」行 → 先修列，再改文本")
        r, li = hit
        C = MAP[r["kind"]]
        if field == "rate" and "rate" not in C:
            fail(f"{id_} 是 {r['kind']} 行，没有利息率列")
        parts = f["lines"][li].split("|")
        old = {"summary": r["fields"]["summary"], "classify": r["fields"]["class"],
               "rate": r["fields"]["rate"]}.get(field, str(r["fields"].get("status", "")))
        if field == "summary":
            parts[1 + C["summary"]] = f" {value} "
        elif field == "classify":
            parts[1 + C["class"]] = f" {value} "
        elif field == "rate":
            parts[1 + C["rate"]] = f" {value} "
        else:
            cell = str(parts[1 + C["status"]] or "")
            m = re.match(r"^\s*\[\s*([^\]·]*)", cell)
            head = m.group(1).strip() if m else ""
            parts[1 + C["status"]] = f" [{head} · {value}] "
        f["lines"][li] = "|".join(parts)
        f["file"].write_text("\n".join(f["lines"]), encoding="utf-8")
        print(f"✅ {id_} 的{EDIT_FIELDS[field]}已改（{f['src']}）")
        print(f"   旧：{old}")
        print(f"   新：{value}")
        return
    fail(f"主表与归档中均无 {id_}")


def cmd_move(argv: list[str]) -> None:
    id_ = next((a for a in argv if re.match(r"^TD-\d+-\d+|^MD-\d+-\d+", a)), None)
    to = arg_val(argv, "--to")
    if not id_ or not to:
        fail("用法：move <TD-ID> --to <NN>（如 move TD-23-6 --to 02）")
    if not re.match(r"^\d{2}$", to):
        fail(f"目标区号非法：`{to}`（两位数字，如 02）")
    files = [{"file": LEDGER, "src": "主表", "lines": load_ledger()["lines"]}]
    if ARCHIVE.exists():
        files.append({"file": ARCHIVE, "src": "归档", "lines": re.split(r"\r?\n", ARCHIVE.read_text(encoding="utf-8"))})
    for f in files:
        hits = [(read_row(l), i) for i, l in enumerate(f["lines"])]
        hits = [(r, i) for r, i in hits if r and not r.get("broken") and r.get("fields") and r["fields"]["id"] == id_]
        if not hits:
            continue
        hit = next(((r, i) for r, i in hits if r["mode"] != "manual"), None)
        if not hit:
            fail(f"{id_} 在{f['src']}是「列错位需人工」行 → 先修列，再迁移")
        r, li = hit
        if r["fields"]["kind"] != "TD":
            fail(f"{id_} 不是 TD 行 → move 仅支持 TD")
        if id_num(r["fields"]["id"]) == int(to):
            fail(f"{id_} 已在区 {to}，无需迁移")
        all_td = [x for x in load_all() if x["fields"]["kind"] == "TD"]
        same = [x for x in all_td if (re.match(r"^TD-(\d+)-", x["fields"]["id"]) or [None, None])[1] == to]
        if any(x["src"] == "main" and x["mode"] == "manual" for x in same):
            fail(f"目标区 {to} 主表存在「列错位需人工」的行 → 先修，否则新 ID 可能撞号")
        max_n = max([0, *[id_seq(x["fields"]["id"]) for x in same]])
        new_id = f"TD-{to}-{max_n + 1}"
        dup = next((x for x in all_td if x["fields"]["id"] == new_id), None)
        if dup:
            fail(f"取号自检失败：{new_id} 已存在于{'归档' if dup['src'] == 'archive' else '主表'} → 拒写（撞号会被 loadAll 静默遮蔽）")
        src_row = next((x for x in same if x["src"] == "main"), same[0] if same else None)
        area_raw = src_row["fields"]["area"] if src_row else ""
        area_text = re.sub(r"（@见[^）]*）", "", area_raw).strip() or to
        C = MAP[r["kind"]]
        parts = f["lines"][li].split("|")
        old_id, old_area = r["fields"]["id"], r["fields"]["area"]
        parts[1 + C["id"]] = f" {new_id} "
        parts[1 + C["area"]] = f" {area_text} "
        f["lines"][li] = "|".join(parts)
        f["file"].write_text("\n".join(f["lines"]), encoding="utf-8")
        print(f"✅ {old_id} → {new_id}（{f['src']}；区域列：{old_area} → {area_text}）")
        print("   ↳ 记得同步改区域日志 / index.md 里对该旧 ID 的引用（账本外的叙述不在本脚本管辖内）")
        return
    fail(f"主表与归档中均无 {id_}")


def cmd_archive(argv: list[str]) -> None:
    dry = "--dry" in argv
    dt = today()
    led = load_ledger()
    lines, rows = led["lines"], led["rows"]
    done_rows = [r for r in rows if status_word(r["fields"]["status"]) in DONE_WORDS]
    keep_rows = [r for r in rows if status_word(r["fields"]["status"]) not in DONE_WORDS]
    if not done_rows:
        print(f"✅ 无需归档：主表已无「已完成」项（现有 {len(keep_rows)} 条待办）")
        return
    arch_text = ARCHIVE.read_text(encoding="utf-8") if ARCHIVE.exists() else ""
    by_id: dict[str, str] = {}
    for l in [*[x for x in re.split(r"\r?\n", arch_text) if TABLE_ROW.match(x)], *[r["line"] for r in done_rows]]:
        m = re.match(r"\|\s*\*{0,2}((?:TD|MD)-\d+-\d+)", l)
        if m:
            by_id[m.group(1)] = l
    ent = list(by_id.items())
    td_a = [l for i, l in sorted([(i, l) for i, l in ent if i.startswith("TD")], key=lambda x: id_key(x[0]))]
    md_a = [l for i, l in ent if i.startswith("MD")]
    out_arch = "\n".join([
        "# 债务账本 · 归档（已解决 / 已裁定 / 已退役 / 不做）", "",
        "> **由 `python scripts/debt.py archive` 自动维护**：把 `债务.md` 里的已完成项移入本文件，主表只留待办。",
        "> 读法：`list --all` / `area <NN>` / `search <关键词>` / `show <TD-ID>`（**均已打通归档，不必手读本文件**）。",
        "> 格式与读写规范见 [债务.md](./债务.md)；明细真源 = 各区域日志的「探债」段。",
        f"> 最近更新：{dt} ｜ 归档 {len(by_id)} 条", "",
        "## 元结构债（机制层，非代码；ID 前缀 `MD-<NN>`）", "",
        "| MD-ID | 现象 | 归类 | 状态 | 锚点 |", "| --- | --- | --- | --- | --- |", *md_a, "",
        "## 已登记债", "",
        "| TD-ID | 区域 | 现象摘要 | 归类 | 利息率 | 状态 | 锚点 |",
        "| --- | --- | --- | --- | --- | --- | --- |", *td_a, "",
    ])
    if dry:
        print(f"（dry-run）将归档 {len(done_rows)} 条 → 主表留 {len(keep_rows)} 条")
        return
    done_line_set = {r["li"] for r in done_rows}
    ARCHIVE.write_text(out_arch, encoding="utf-8")
    LEDGER.write_text("\n".join(l for i, l in enumerate(lines) if i not in done_line_set), encoding="utf-8")
    by: dict[str, int] = {}
    for r in keep_rows:
        w = status_word(r["fields"]["status"]) or "?"
        by[w] = by.get(w, 0) + 1
    print(f"✅ 归档 {len(done_rows)} 条 → daily/架构日志/债务-归档.md（累计 {len(by_id)} 条）")
    print(f"   主表保留 {len(keep_rows)} 条待办：{' · '.join(f'{k}={v}' for k, v in by.items()) or '（无）'}")


LIVENESS_FILE_RE = re.compile(r"`([A-Za-z0-9_][\w./-]*\.(?:py|mjs|cjs|js|ts|tsx))(?::[\d\-–、]+)?`")
LIVENESS_SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "coverage", ".vite", "release", "__pycache__", ".venv"}


def collect_repo_file_names() -> set[str]:
    names: set[str] = set()

    def walk(d: Path):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            if e.is_dir():
                if e.name not in LIVENESS_SKIP_DIRS:
                    walk(Path(e.path))
            else:
                names.add(e.name)

    walk(ROOT)
    return names


def liveness_issues(src_label: str, lines: list[str], repoNames: set[str]) -> list[dict]:
    out = []
    for li, line in enumerate(lines):
        r = read_row(line)
        if not r or r.get("broken") or r["mode"] == "manual":
            continue
        text = str(r["fields"]["summary"] or "")
        seen = set()
        for m in LIVENESS_FILE_RE.finditer(text):
            raw = m.group(1)
            base = raw.split("/")[-1]
            if base in seen:
                continue
            seen.add(base)
            if base not in repoNames:
                out.append({"at": f"{src_label}第 {li+1} 行", "kind": "落点疑似失效",
                            "detail": f"{r['fields']['id']}：`{raw}` —— 全仓无此文件（可能已随重构 / 清理 / 计划改道消失；偿债前先确认它还在不在）"})
    return out


def anchor_id_issues(src_label: str, lines: list[str]) -> list[dict]:
    out = []
    cache: dict[str, str | None] = {}
    for li, line in enumerate(lines):
        r = read_row(line)
        if not r or r.get("broken") or r["mode"] == "manual":
            continue
        id_ = r["fields"]["id"]
        m = re.match(r"^\**\[([^\]]+)\]\([^)]*\)", str(r["fields"]["anchor"] or ""))
        if not m:
            continue
        file = m.group(1)
        if not REGION_ANCHOR_RE.match(file):
            continue
        if file not in cache:
            p = LOG_DIR / file
            cache[file] = p.read_text(encoding="utf-8") if p.exists() else None
        text = cache[file]
        if text and id_ not in text:
            out.append({"at": f"{src_label}第 {li+1} 行", "kind": "锚点断链",
                        "detail": f"{id_}：锚点 {file} 内没有该债号（`show` 会给出「有证据可查」的假象）"})
    return out


AUTO_KINDS = ["列错位·可自动修", "归类非规范", "利息率非规范", "状态非规范", "多余表头"]


def cmd_audit(argv: list[str]) -> None:
    liveness = "--liveness" in argv
    repoNames = collect_repo_file_names() if liveness else None
    issues: list[dict] = []
    for file, src in [(LEDGER, "主表"), (ARCHIVE, "归档")]:
        if not file.exists():
            continue
        lines = re.split(r"\r?\n", file.read_text(encoding="utf-8"))
        seen: dict[str, int] = {}
        header_lines = []
        for li, line in enumerate(lines):
            if re.match(r"^\|\s*TD-ID\s*\|", line):
                header_lines.append(li)
            r = read_row(line)
            if not r:
                continue
            at = f"{src}第 {li+1} 行"
            if r.get("broken"):
                issues.append({"at": at, "kind": "断行", "detail": "缺少首/尾竖线"})
                continue
            f = r["fields"]
            if f["id"] in seen:
                issues.append({"at": at, "kind": "重复 ID", "detail": f"{f['id']}（首次在第 {seen[f['id']]+1} 行）"})
            else:
                seen[f["id"]] = li
            if r["mode"] == "A":
                issues.append({"at": at, "kind": "列错位·可自动修", "detail": f"{f['id']}：尾部多余段（同锚点贴两次）"})
            if r["mode"] == "B":
                issues.append({"at": at, "kind": "列错位·可自动修", "detail": f"{f['id']}：描述含裸 `|`（段数 {len(r['inner'])}）"})
            if r["mode"] == "C":
                issues.append({"at": at, "kind": "列错位·可自动修", "detail": f"{f['id']}：状态含裸 `|`（段数 {len(r['inner'])}）"})
            if r["mode"] == "manual":
                issues.append({"at": at, "kind": "列错位·需人工", "detail": f"{f['id']}：段数 {len(r['inner'])}，四级校验均不通过 → 不猜"})
            if r["mode"] != "manual" and not is_anchorish(f["anchor"]):
                issues.append({"at": at, "kind": "锚点异常", "detail": f"{f['id']}：`{str(f['anchor'])[:48]}`"})
            if r["mode"] != "manual" and is_anchorish(f["anchor"]):
                mt = re.match(r"^\*{0,2}\[[^\]]*\]\(([^)]*)\)", str(f["anchor"]))
                target = (mt.group(1) if mt else "").removeprefix("./")
                p = anchor_problem(target) if target else ""
                if p:
                    issues.append({"at": at, "kind": "锚点非区域文件", "detail": f"{f['id']}：{p}"})
            c = norm_class(f["class"])
            if c.get("unknown"):
                issues.append({"at": at, "kind": "归类未知", "detail": f"{f['id']}：`{f['class']}`"})
            elif c.get("from"):
                issues.append({"at": at, "kind": "归类非规范", "detail": f"{f['id']}：`{c['from']}` → `{c['value']}`"})
            if f["kind"] == "TD":
                rt = norm_rate(f["rate"])
                if rt.get("unknown"):
                    issues.append({"at": at, "kind": "利息率未知", "detail": f"{f['id']}：`{f['rate']}`"})
                elif rt.get("from"):
                    issues.append({"at": at, "kind": "利息率非规范", "detail": f"{f['id']}：`{rt['from']}` → `{rt['value']}`"})
            s = norm_status(f["status"])
            if s.get("unknown"):
                issues.append({"at": at, "kind": "状态未知", "detail": f"{f['id']}：`{f['status']}`"})
            elif s.get("from"):
                issues.append({"at": at, "kind": "状态非规范", "detail": f"{f['id']}：`{s['from']}` → `{s['value']}`"})
        if len(header_lines) > 1:
            for li in header_lines[1:]:
                issues.append({"at": f"{src}第 {li+1} 行", "kind": "多余表头", "detail": "追加时误贴的表头行"})
        if liveness:
            issues.extend(liveness_issues(src, lines, repoNames))
        issues.extend(anchor_id_issues(src, lines))

    main = len([r for r in load_ledger()["rows"] if r["fields"]["kind"] == "TD"])
    arch = len([r for r in parse_text(ARCHIVE.read_text(encoding="utf-8"), "a") if r["fields"]["kind"] == "TD"]) if ARCHIVE.exists() else 0
    print(f"🔍 debt audit（只读 · **不是闸**）｜ 主表 {main} 条 · 归档 {arch} 条 ｜ 违规 {len(issues)} 项")
    by_kind: dict[str, list] = {}
    for i in issues:
        by_kind.setdefault(i["kind"], []).append(i)
    for kind, lst in by_kind.items():
        print(f"\n── {kind}（{len(lst)}）")
        for i in lst[:30]:
            print(f"   {i['at']}：{i['detail']}")
        if len(lst) > 30:
            print(f"   … 另 {len(lst)-30} 项")
    auto = len([i for i in issues if i["kind"] in AUTO_KINDS])
    print(f"\n小结：可归一/修复 {auto} 项 · 需人工 {len(issues)-auto} 项")


def cmd_fix(argv: list[str]) -> None:
    dry = "--dry" in argv
    total = 0
    for file, label in [(LEDGER, "主表"), (ARCHIVE, "归档")]:
        if not file.exists():
            continue
        raw = file.read_text(encoding="utf-8")
        eol = "\r\n" if "\r\n" in raw else "\n"
        n = 0
        out = []
        for li, line in enumerate(re.split(r"\r?\n", raw)):
            r = read_row(line)
            if not r or r["mode"] not in ("A", "B", "C"):
                out.append(line)
                continue
            n += 1
            print(f"   {label}第 {li+1} 行 · {r['fields']['id']} · mode {r['mode']}：段数 {len(r['inner'])} → {len(r['fixedInner'])}")
            out.append("| " + " | ".join(r["fixedInner"]) + " |")
        if n:
            total += n
            if not dry:
                file.write_text(eol.join(out), encoding="utf-8")
            print(f"{'（dry·未写盘）' if dry else '✅'} {label}：{n} 行{' 待修' if dry else ' 已修'}")
        else:
            print(f"✅ {label}：0 行")
    print(f"\n共 {total} 行{'（去掉 --dry 生效）' if dry else '已规范化'}" if total else "\n无坏行（列错位 0 项）")
    if total and not dry:
        print("↳ 收尾请复跑 `python scripts/debt.py audit` 确认「可自动修」归零")


def main() -> None:
    argv = sys.argv[2:]
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    dispatch = {
        "list": cmd_list, "area": cmd_area, "search": cmd_search, "show": cmd_show,
        "add": cmd_add, "resolve": cmd_resolve, "reanchor": cmd_reanchor, "edit": cmd_edit,
        "move": cmd_move, "archive": cmd_archive, "audit": cmd_audit, "fix": cmd_fix,
    }
    if cmd == "stats":
        cmd_stats()
    elif cmd in dispatch:
        dispatch[cmd](argv)
    else:
        print("债务账本读写唯一入口（详见文件头注释）")
        print("  读：list [--area NN] [--status X] [--all] | area <NN> | search <关键词> | show <TD-ID>")
        print('  写：add --area NN --summary "…" | resolve <TD-ID> --note "…" | edit <TD-ID> --field summary｜solution｜classify｜rate --value "…" | reanchor <TD-ID> --anchor <区域文件> | move <TD-ID> --to <NN>')
        print("  维护：archive [--dry] | audit [--liveness] | fix [--dry]（规范化历史列错位行）")
        print("  统计：stats（形态/解法分布）")
        sys.exit(1 if cmd else 0)


if __name__ == "__main__":
    main()
