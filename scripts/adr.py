#!/usr/bin/env python3
"""ADR 读写**唯一入口** —— `docs/adr/ADR-NNNN-*.md`（各条决议）+ `docs/adr/README.md`（**索引是产物**）。

【为什么存在】ADR 的价值全在"**读债 / 普查时顺手看一眼**"（7 步法 铁律 6 · ADR-0001）。
  若索引靠人手维护，就会出现与 `债务.md` 同款的老问题：同一条约定两处维护 → **必然漂移** →
  索引失真后没人再信它 → ADR 退化成"写了没人看"。故：**真源 = 各 ADR 文件的头部字段；README 的索引表 = 生成物**。

【机制：写入口，不是闸】`add`/`status` 在**落盘前**拒非法状态枚举 / 缺必填字段 / 标题重复 / 取号撞号。
  与 `debt.py` 同款定位：写文档要的是**规范**，不是门禁。

【申诉口三问（本脚本自己也要能答）】
  ① 守什么：ADR 头部字段的**形状**与索引的**一致性**（红线？否 —— 属**结构偏好**，可被证据推翻）。
  ② 什么时候该改它：新增了必要的头部字段 / 状态枚举需扩充 / 索引渲染要变 → 直接改本脚本，别改产物。
  ③ 怎么改：改本文件的 `FIELDS` / `STATUS` / `render_index`；README 的手写段落在 `README_*` 常量里。

【规模维护（ADR 会越来越多 —— 这是本工具的第二个存在理由）】
  ① **默认只看现行**：`list` 只列非退出态；已退出要 `--all` 才见；`search` 则**含全部**。
  ② **索引分两张表**：现行 / 已退出 —— 读者不必在几十条里挑现行判据。**不搬家**（搬家会断链）。
  ③ **正文是一页**：`bodyLines` 的口径 = **整个文件行数**（含标题与头部字段行），超 `MAX_BODY_LINES` 即 `audit` 报错。
  ④ **毕业机制**：判据一旦能升为**可自动检查的载体**（结构上不可能 > 契约层 > 唯一入口 > 对账测试），
     就 `status --to 已毕业 --note "<载体:文件:行>"` —— **能被自动检查的才算毕业**。
  ⑤ **不可变性（修正在先）**：**原文本身写错（按原样执行会导出错动作）必须回改原文**；
     **写新 ADR 取代只用于「决策变了」**。顺序：**修正 > 改主意**。回改**必须留痕**（标日期 + 原作 ⇒ 现作 + 为什么）。
     头部状态行（`状态` / `取代` / `被取代于` / `毕业去向`）任何情形都可改。
  ⑥ **写入门槛**：`add` 拒标题重复 + 拒结论重复；只写「推翻/确立约定 · 用户裁定 · **被否决的方案**」。

【AI 友好】`--json`（list/show/audit/stats）· `--dry`（不落盘）· 错误一律带"怎么修"。

用法（读）：
  python scripts/adr.py list [--all] [--json]      # 默认只列现行；--all 含已退出
  python scripts/adr.py show <NNNN|文件名片段>      # 单条全文
  python scripts/adr.py search <关键词> [--json]    # 标题 / 结论 / 触发 / 正文 全文匹配（多词 AND，含已退出）
  python scripts/adr.py index [--json]             # 【幂等】索引过期则自动重生成（不阻断）
  python scripts/adr.py audit [--json]             # 只读体检（**不是闸**）
  python scripts/adr.py stats                      # 状态分布
用法（写 / 维护）：
  python scripts/adr.py add --title "…" --conclusion "…" --user-approved
                         [--status 生效] [--decider 用户|架构师] [--trigger "…"] [--date YYYY-MM-DD] [--supersedes NNNN] [--dry]
  python scripts/adr.py index --write              # 重生成 README 索引块（禁手改索引表）
  python scripts/adr.py status <NNNN> --to <状态> [--by <NNNN>] [--note "…"] [--dry]
  python scripts/adr.py rm <NNNN> --reason "…" [--force]
用法（守护者 · 只读）：
  python scripts/adr.py refs <NNNN>                # 谁引用了它 —— 按「必须回改 / 不动」分三类（A7 回改的唯一入口）
  python scripts/adr.py refs --check               # 全仓断链：引用了**已不存在**的编号 ⇒ exit 1
  python scripts/adr.py doctor                     # Step 1 一键编排：audit + hygiene + 断链（**不是闸**）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADR_DIR = ROOT / "docs" / "adr"
README = ADR_DIR / "README.md"

STATUS = ["草案", "生效", "已取代", "已弃用", "已否决", "已毕业"]
STATUS_ICON = {
    "草案": "📝",
    "生效": "✅",
    "已取代": "🔄",
    "已弃用": "🗑",
    "已否决": "❌",
    "已毕业": "🎓",
}
RETIRED = ["已取代", "已弃用", "已否决", "已毕业"]

FIELDS = ["状态", "结论", "日期", "裁定人", "触发", "取代", "被取代于", "毕业去向"]
REQUIRED = ["状态", "日期", "裁定人", "结论"]
MAX_BODY_LINES = 80

LEN_HINT = (
    "\n     ⇒ **别急着抠字** —— 篇幅超标是信号，不是病。先答三问："
    "① **这条 ADR 要让下一个 AI 做出什么不同判断？**（答得出 ⇒ 围着这一句重写，**其余都是噪音**）；"
    "② **哪些是「讲道理 / 来历 / 教训」？**（不改变任何动作 ⇒ 整块抽去 daily/架构日志/，判据只留「照做」）；"
    "③ **是不是一条里装了好几个子判据？**（那是结构问题 ⇒ 该拆/该收编，**不是字数问题**）。"
    "\n     ⇒ 停在「只读结论 + 决议，下一个 AI 仍能做对」处；**为过闸而删字 = 用可测量冒充正确**。"
)

SIMILAR_THRESHOLD = 0.55
FIND_WARN = 60
FIND_CRITICAL = 150
IDX_BEGIN = "<!-- ADR-INDEX:BEGIN（由 python scripts/adr.py index --write 生成 · 禁手改） -->"
IDX_END = "<!-- ADR-INDEX:END -->"

REQUIRED_SECTIONS = ["背景", "判据", "决议", "后果"]

# ── 引用面扫描根（本仓适配：数据层 / 回测层 / 文档 / 流程 / 顶层入口）──────────
REFS_SKIP = {"node_modules", "dist", ".git", ".probe", ".venv", "__pycache__"}
REFS_EXT = re.compile(r"\.(md|py|js|jsx|mjs|cjs|ts|tsx)$")
REFS_ROOTS = ["docs", "data-layer", "backtest", ".codebuddy", "daily", "Agent.md"]
# 过程文档（**不回改**）：区域日志 + 施工方案/轮次快照 —— 判据 = "会不会被当入口再查一次"
PROCESS_DOC_RE = re.compile(r"^(daily/|data-layer/docs/plan/|backtest/docs/design/)")
EXEMPT_REF = re.compile(r"已删号|已删|已退役|已移除|已改名|原名|此前|旧版|曾经|tombstone|墓碑|\]\(ADR-\d{4}-")
RE_ADR = re.compile(r"\bADR-\d{4}(?!-?\d)\b")
RETIRED_HINT = re.compile(r"已删号|已删|已撤|误建|撤回|作废|退役")


def die(msg: str, fix: str | None = None) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    if fix:
        print(f"   ↳ 怎么修：{fix}", file=sys.stderr)
    sys.exit(2)


def val(name: str, dflt: str | None = None) -> str | None:
    """取 `--name value` 的值（值不得以 `--` 开头）。"""
    argv = sys.argv
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv) and argv[i + 1] and not argv[i + 1].startswith("--"):
            return argv[i + 1]
    return dflt


def flag(name: str) -> bool:
    return name in sys.argv


def norm_status(raw) -> str:
    return re.sub(r"[✅⛔📝\s]", "", str(raw or ""))


def display_title(a: dict) -> str:
    """已退出条目的标题在渲染时**自带状态**（否则被摘出来引用时会像现行判据）。"""
    if a["status"] not in RETIRED:
        return a["title"]
    if re.match(r"^【[^】]*】", a["title"]):
        return a["title"]
    return f"【{a['status']}】{a['title']}"


def retired_banner(a: dict) -> str:
    if a["status"] not in RETIRED:
        return ""
    return (
        f"⛔ 【{a['status']}】本条目已退出，**不承载现行判据** —— 引用前先确认它被谁取代/毕业到了哪。\n"
        f"   ↳ 查现行版：`python scripts/adr.py list`（默认只看现行）或 `search <关键词>`\n\n"
    )


def adr_files() -> list[str]:
    if not ADR_DIR.exists():
        die(f"ADR 目录不存在：{ADR_DIR}", "mkdir docs/adr 或先跑 `add`")
    return sorted(
        f.name for f in ADR_DIR.iterdir() if re.match(r"^ADR-\d{4}-.*\.md$", f.name)
    )


def normalize_for_compare(s) -> str:
    return re.sub(r"[\s`*·、，。；：（）()「」【】\[\]{}<>/\\|—\-_\"'‘’“”]", "", str(s)).lower()


def dice_coefficient(a: str, b: str) -> float:
    A, B = set(a), set(b)
    if not A or not B:
        return 0.0
    inter = len(A & B)
    return (2 * inter) / (len(A) + len(B))


def find_similar(adrs: list[dict], text: str, exclude_no: str | None):
    target = normalize_for_compare(text)
    best = None
    for a in adrs:
        if a["no"] == exclude_no:
            continue
        score = dice_coefficient(target, normalize_for_compare(a["conclusion"]))
        if score >= SIMILAR_THRESHOLD and (best is None or score > best["score"]):
            best = {"adr": a, "score": score}
    return best


PLACEHOLDER_LINE = re.compile(r"^\s*(?:[-*]\s*)?<[^<>\n]{2,80}>\s*$")


def placeholder_lines(text: str) -> list[dict]:
    out = []
    for i, line in enumerate(text.split("\n")):
        if PLACEHOLDER_LINE.match(line):
            out.append({"line": i + 1, "text": line.strip()})
    return out


def missing_sections(text: str) -> list[str]:
    return [s for s in REQUIRED_SECTIONS if not re.search(rf"^##\s*{s}", text, re.M)]


def parse_adr(file: str) -> dict:
    abs_ = ADR_DIR / file
    text = abs_.read_text(encoding="utf-8")
    h = re.search(r"^#\s+ADR-(\d{4})\s*·\s*(.+?)\s*$", text, re.M)
    if not h:
        die(f"{file}：缺标题行「# ADR-NNNN · <标题>」", "补上标题行（编号四位，与文件名一致）")
    meta = {}
    for m in re.finditer(
        r"^-\s+\*\*(状态|结论|日期|裁定人|触发|取代|被取代于|毕业去向)\*\*：\s*(.*?)\s*$",
        text,
        re.M,
    ):
        meta[m.group(1)] = m.group(2)
    return {
        "no": h.group(1),
        "id": f"ADR-{h.group(1)}",
        "title": h.group(2),
        "status": norm_status(meta.get("状态")),
        "conclusion": meta.get("结论", ""),
        "date": meta.get("日期", ""),
        "decider": meta.get("裁定人", ""),
        "trigger": meta.get("触发", ""),
        "supersedes": meta.get("取代", ""),
        "supersededBy": meta.get("被取代于", ""),
        "graduatedTo": meta.get("毕业去向", ""),
        "bodyLines": len(text.split("\n")),
        "file": file,
        "path": f"docs/adr/{file}",
        "text": text,
    }


def load_adrs() -> list[dict]:
    return [parse_adr(f) for f in adr_files()]


def render_index(adrs: list[dict]) -> str:
    def row(a):
        return (
            f"| [{a['no']}]({a['file']}) | {a['title']} | "
            f"{STATUS_ICON.get(a['status'], '❔')} {a['status']} | {a['conclusion']} |"
        )

    head = "| # | 标题 | 状态 | 结论一句话 |\n| --- | --- | --- | --- |"
    active = [a for a in adrs if a["status"] not in RETIRED]
    graduated = [a for a in adrs if a["status"] == "已毕业"]
    retired = [a for a in adrs if a["status"] in RETIRED and a["status"] != "已毕业"]
    parts = [head, *[row(a) for a in active]]
    if graduated:
        parts += ["", "**🎓 已毕业**（判据**仍现行**，载体已升为自动检查 · **不是作废**）", "", head,
                  *[row(a) for a in graduated]]
    if retired:
        parts += ["", "**已退出**（保留作记录 · **勿再引用为现行判据**）", "", head,
                  *[row(a) for a in retired]]
    return f"{IDX_BEGIN}\n" + "\n".join(parts) + f"\n{IDX_END}"


def read_index_block() -> tuple[str, int, int, str]:
    if not README.exists():
        die(f"README 不存在：{README}", "先建 docs/adr/README.md")
    t = README.read_text(encoding="utf-8")
    s, e = t.find(IDX_BEGIN), t.find(IDX_END)
    if s < 0 or e < 0:
        die("README 里找不到索引块标记", f"补上 {IDX_BEGIN} … {IDX_END}")
    return t, s, e, t[s : e + len(IDX_END)]


def write_index(adrs: list[dict]) -> None:
    text, s, e, _ = read_index_block()
    next_ = text[:s] + render_index(adrs) + text[e + len(IDX_END) :]
    README.write_text(next_, encoding="utf-8")


def slug(t: str) -> str:
    s = re.sub(r'[\\/:*?"<>|()\[\]{}]', "", t)
    s = re.sub(r"\s+", "-", s)
    return s[:40]


def cmd_index() -> None:
    adrs = load_adrs()
    want = render_index(adrs)
    _, _, _, block = read_index_block()
    same = want == block
    if flag("--json"):
        print(json.dumps({"consistent": same, "count": len(adrs)}, ensure_ascii=False, indent=2))
        return
    if flag("--write"):
        write_index(adrs)
        print(f"✅ 已重生成 docs/adr/README.md 索引块（{len(adrs)} 条）")
        return
    if same:
        print(f"✅ README 索引与 ADR 文件一致（{len(adrs)} 条）")
        return
    write_index(adrs)
    print(f"✅ 索引已过期 → 已自动刷新（{len(adrs)} 条）")
    print("   ↳ 请 `git add docs/adr/README.md` 一并提交（禁手改索引表）。")


def cmd_list() -> None:
    all_ = load_adrs()
    show_all = flag("--all")
    adrs = all_ if show_all else [a for a in all_ if a["status"] not in RETIRED]
    retired = len(all_) - len(adrs)
    if flag("--json"):
        print(json.dumps([{k: v for k, v in a.items() if k != "text"} for a in adrs],
                         ensure_ascii=False, indent=2))
        return
    tail = (
        f"（--all：含已退出 {retired} 条）" if show_all
        else (f"（另有已退出 {retired} 条，--all 可见）" if retired else "")
    )
    print(f"📚 ADR 现行判据 ｜ {len(adrs)} 条{tail}")
    for a in adrs:
        print(f"{a['id']} ｜ {STATUS_ICON.get(a['status'], '❔')} {a['status']} ｜ {display_title(a)}")
        if a["conclusion"]:
            print(f"        {a['conclusion']}")
    print("   ↳ 全文：`show <NNNN>` · 检索（**含已退出**）：`search <关键词>` · 看全部：`list --all`")


def cmd_show() -> None:
    if len(sys.argv) < 3:
        die("用法：python scripts/adr.py show <NNNN|文件名片段>")
    q = sys.argv[2]
    hit = [a for a in load_adrs() if a["no"] == q or a["id"] == q or q in a["file"]]
    if not hit:
        die(f"没有匹配的 ADR：{q}", "先 `list` 看编号")
    sys.stdout.write(retired_banner(hit[0]))
    print(hit[0]["text"])


def cmd_search() -> None:
    words = [a for a in sys.argv[2:] if not a.startswith("--")]
    if not words:
        die("用法：python scripts/adr.py search <关键词> [关键词2 …]（多词 AND）")
    hit = [a for a in load_adrs() if all(w in a["text"] for w in words)]
    if flag("--json"):
        print(json.dumps([{k: v for k, v in a.items() if k != "text"} for a in hit],
                         ensure_ascii=False, indent=2))
        return
    print(f"🔎 adr search「{' + '.join(words)}」｜ 命中 {len(hit)} 条")
    n_retired = len([a for a in hit if a["status"] in RETIRED])
    if n_retired:
        print(f"   ⚠️ 其中 {n_retired} 条**已退出**（标题带【状态】前缀）—— 它们**不承载现行判据**，别直接引用。")
    for a in hit:
        print(f"{a['id']} ｜ {STATUS_ICON.get(a['status'], '❔')} {a['status']} ｜ {display_title(a)}")


SIX_QUESTIONS = """
  ── 落笔前先答这 6 问（答不出 ⇒ 它多半不该是 ADR）──
  ⓿ **它涉及几个面？** 换个地方还会不会遇到它？只影响**一处** ⇒ **不写**（死日记）→ 区域日志。
  ① **它推翻了什么既有约定？** 答不出 ⇒ 它更可能是"记录"而非"判据"（→ 区域日志）。
  ② **既有的现行条里有同义的吗？** 已自动查过近义（上面没被拦 = 结论不相似）；
     但仍要人工扫一遍：`python scripts/adr.py list`。
  ③ **能不能不用 ADR 守？** 结构上不可能 / 契约层 / 唯一入口 / 对账测试里有没有更根本的？
     有 ⇒ 用那个（ADR 属手段优先级 5，是**孵化器不是仓库**）。
  ④ **谁会发现它被违反？** 写得出「📌 违反时的判据」吗？写不出 ⇒ 它是**没有防线**的声明。
  ⑤ **写下它之后，下一个 AI 的具体动作会变吗？** 答不出"我该做什么 / 不该做什么" ⇒ **不写**（不占编号）。

  ⚠️ 本打印**只是提醒**（"该不该写"不可机器验证）—— 别把"命令跑过了"当成"六问答过了"。
  🔴 **答卷人是 AI（你）；流程是「答 → 给用户看 → 用户决策」**：
     ① 你**逐条回答**这 6 问（不是"都过了"）；
     ② 把**这 6 问 + 你的逐条答卷原样贴进对话**，让用户看见 —— 这一步**没有机器防线**，靠你自觉；
     ③ **用户决策**：同意 ⇒ 加 `--user-approved` 落盘；不同意 ⇒ 不写。
"""


def cmd_add() -> None:
    title = val("--title")
    conclusion = val("--conclusion")
    if not title:
        die("缺 --title", 'python scripts/adr.py add --title "…" --conclusion "…"')
    if not conclusion:
        die("缺 --conclusion（索引表要它）", "补一句话结论，≤120 字")
    if len(conclusion) > 120:
        die(f"--conclusion 过长（{len(conclusion)} 字 > 120）", "压到一句话")
    if "|" in conclusion:
        die("--conclusion 含裸竖线 `|`（会撑破索引表格）", "用「／」代替")

    adrs = load_adrs()
    if any(a["title"] == title for a in adrs):
        die(f"标题已存在：{title}", "换个标题，或改既有那条")
    dup = next((a for a in adrs if a["conclusion"] == conclusion), None)
    if dup:
        die(f"结论与 {dup['id']} 重复",
            f"同义 ADR 应合并 —— 改 {dup['id']} 那条，或换一条**真正不同**的判据")
    near = find_similar(adrs, conclusion, None)
    if near and not flag("--force"):
        die(f"结论与 {near['adr']['id']} **近义**（相似度 {near['score']*100:.0f}%）",
            f"同义 ADR 应合并 —— 改 {near['adr']['id']} 那条，或改写成**真正不同**的判据。"
            f" 确属不同判据时：改措辞使两者判据分明，再 --force 跳过本检查。")
    status = norm_status(val("--status", "生效"))
    if status not in STATUS:
        die(f"状态非法：{val('--status')}", f"白名单：{' / '.join(STATUS)}")
    supersedes = val("--supersedes")
    if supersedes and not any(a["no"] == supersedes for a in adrs):
        die(f"--supersedes {supersedes} 不存在", "先确认被取代的编号（`list`）")

    high_water = max([0, *[int(a["no"]) for a in adrs], *retired_nos()])
    no = str(high_water + 1).zfill(4)
    date = val("--date", __import__("datetime").date.today().isoformat())
    decider = val("--decider", "架构师（取证后自定）")
    trigger = val("--trigger", "—")
    file = f"ADR-{no}-{slug(title)}.md"
    body = "\n".join([
        f"# ADR-{no} · {title}",
        "",
        f"- **状态**：{status}",
        f"- **结论**：{conclusion}",
        f"- **日期**：{date}",
        f"- **裁定人**：{decider}",
        f"- **触发**：{trigger}",
        *([f"- **取代**：ADR-{supersedes}"] if supersedes else []),
        "",
        "## 背景",
        "",
        "<旧约定是什么、它当初为什么这么定>",
        "",
        "## 判据（它凭什么成立 / 为什么不成立）",
        "",
        "<取证：物理红线？契约层？唯一入口？对账测试？—— 答不出就是待消灭的对象>",
        "",
        "## 决议",
        "",
        "<新约定是什么；落点（文件:行）>",
        "",
        "## 后果",
        "",
        "- ✅ 收益",
        "- ⚠️ 代价 / 回潮风险",
        "- 📌 违反时的判据（怎么发现「又回去了」）",
        "",
    ])

    print(SIX_QUESTIONS)

    if flag("--dry"):
        print(f"（dry-run）将创建 docs/adr/{file} 并重生成索引")
        print(body)
        return

    if not flag("--user-approved"):
        print("""
  ❌ 拒绝落盘：缺 `--user-approved`（用户已决策"同意"）。

     🔴 **流程是「AI 逐条答 → 贴进对话给用户看 → 用户决策」**：
     ① 你**逐条回答**上面 6 问（"都过了"不算答）；
     ② 把**这 6 问 + 你的答卷原样贴进对话**，让用户看见 —— 这一步**没有机器防线**，靠你自觉；
     ③ **用户决策**：同意 ⇒ 加 `--user-approved`；不同意 ⇒ 不写。
""")
        sys.exit(2)

    (ADR_DIR / file).write_text(body, encoding="utf-8")
    if supersedes:
        target = next(a for a in adrs if a["no"] == supersedes)
        new_text = re.sub(r"^- \*\*状态\*\*：.*$", "- **状态**：已取代", target["text"], count=1, flags=re.M)
        new_text = new_text.rstrip() + f"\n- **被取代于**：ADR-{no}\n"
        (ADR_DIR / target["file"]).write_text(new_text, encoding="utf-8")
    write_index(load_adrs())
    print(f"✅ 已创建 docs/adr/{file}（{status}）并重生成索引")
    print("   ↳ 用户裁决：同意")
    print("   ↳ ⚠️ 现在它是**空壳**（四段仍是占位符）。`audit` 会报「模板占位符残留」——")
    print("     把 §背景 / §判据 / §决议 / §后果 写成真内容才算落盘（占位符留着 = 假账）")


def cmd_status() -> None:
    if len(sys.argv) < 3:
        die("用法：python scripts/adr.py status <NNNN> --to <状态> [--by NNNN] [--note \"…\"]")
    q = sys.argv[2]
    to = norm_status(val("--to"))
    if not q or not to:
        die("用法：python scripts/adr.py status <NNNN> --to <生效|已取代|已弃用|已否决|已毕业|草案> [--by NNNN] [--note \"…\"]")
    if to not in STATUS:
        die(f"状态非法：{val('--to')}", f"白名单：{' / '.join(STATUS)}")
    adrs = load_adrs()
    target = next((a for a in adrs if a["no"] == q or a["id"] == q), None)
    if not target:
        die(f"没有匹配的 ADR：{q}", "先 `list`")
    by = val("--by")
    if by and not any(a["no"] == by for a in adrs):
        die(f"--by {by} 不存在", "先 `list`")
    note = val("--note")
    if to == "已毕业" and not note:
        die('--to 已毕业 必须同时给 --note "<升到哪个载体：文件:行>"',
            '如 --note "契约层：store/keys.py:120 Key.make"')
    if to == "已毕业" and note and "|" in note:
        die("--note 含裸竖线 `|`", "用「／」代替")

    next_ = re.sub(r"^- \*\*状态\*\*：.*$", f"- **状态**：{to}", target["text"], count=1, flags=re.M)
    if by and not re.search(r"^- \*\*被取代于\*\*", next_, re.M):
        next_ = next_.rstrip() + f"\n- **被取代于**：ADR-{by}\n"
    if note:
        if re.search(r"^- \*\*毕业去向\*\*", next_, re.M):
            next_ = re.sub(r"^- \*\*毕业去向\*\*：.*$", f"- **毕业去向**：{note}", next_, count=1, flags=re.M)
        else:
            next_ = next_.rstrip() + f"\n- **毕业去向**：{note}\n"
    if flag("--dry"):
        print(f"（dry-run）{target['id']} 状态 → {to}"
              f"{f'（被 ADR-{by} 取代）' if by else ''}{f'（毕业去向：{note}）' if note else ''}")
        return
    (ADR_DIR / target["file"]).write_text(next_, encoding="utf-8")
    if by:
        newer = next(a for a in adrs if a["no"] == by)
        if not re.search(r"^- \*\*取代\*\*", newer["text"], re.M):
            (ADR_DIR / newer["file"]).write_text(
                newer["text"].rstrip() + f"\n- **取代**：{target['id']}\n", encoding="utf-8")
    write_index(load_adrs())
    print(f"✅ {target['id']} → {to}"
          f"{f'（被 ADR-{by} 取代）' if by else ''}{f'（毕业去向：{note}）' if note else ''}，索引已重生成")


def cmd_rm() -> None:
    if len(sys.argv) < 3:
        die('用法：python scripts/adr.py rm <NNNN> --reason "误建/重复" [--force]')
    q = sys.argv[2]
    reason = val("--reason")
    if not q or not reason:
        die('用法：python scripts/adr.py rm <NNNN> --reason "误建/重复" [--force]')
    adrs = load_adrs()
    t = next((a for a in adrs if a["no"] == q or a["id"] == q), None)
    if not t:
        die(f"没有匹配的 ADR：{q}", "先 `list --all`")
    in_chain = bool(t["supersedes"] or t["supersededBy"])
    if in_chain and not flag("--force"):
        die(f"{t['id']} 参与取代链（取代 {t['supersedes'] or '—'} / 被 {t['supersededBy'] or '—'} 取代），不许删",
            "确认是**误建**才加 --force（会同时解开取代链）；正常退役请用 status --to 已取代/已弃用/已毕业")
    if flag("--dry"):
        print(f"（dry-run）将删除 docs/adr/{t['file']}{' + 解开取代链' if in_chain else ''} 并重生成索引（原因：{reason}）")
        return
    if flag("--force") and in_chain:
        def find(ref):
            return next((a for a in adrs if a["no"] == ref or a["id"] == ref), None)

        old = find(t["supersedes"]) if t["supersedes"] else None
        if old:
            txt = re.sub(r"^- \*\*状态\*\*：.*$", "- **状态**：生效", old["text"], count=1, flags=re.M)
            txt = re.sub(r"^- \*\*被取代于\*\*：.*\n?", "", txt, count=1, flags=re.M)
            (ADR_DIR / old["file"]).write_text(txt, encoding="utf-8")
        newer = find(t["supersededBy"]) if t["supersededBy"] else None
        if newer:
            txt = re.sub(r"^- \*\*取代\*\*：.*\n?", "", newer["text"], count=1, flags=re.M)
            (ADR_DIR / newer["file"]).write_text(txt, encoding="utf-8")
    (ADR_DIR / t["file"]).unlink()
    write_index(load_adrs())
    print(f"🗑 {t['id']} 已删除（原因：{reason}）{'，取代链已解开' if in_chain else ''}，索引已重生成")


def cmd_audit() -> None:
    adrs = load_adrs()
    by_no = {}
    for a in adrs:
        by_no[a["no"]] = a
        by_no[a["id"]] = a
    problems, notes = [], []
    seen, conclusions = {}, {}
    near_dup = set()
    for a in adrs:
        if a["status"] != "生效":
            continue
        for b in adrs:
            if b["status"] != "生效" or b["no"] <= a["no"]:
                continue
            score = dice_coefficient(normalize_for_compare(a["conclusion"]), normalize_for_compare(b["conclusion"]))
            if score >= SIMILAR_THRESHOLD:
                near_dup.add(f"{a['id']} 与 {b['id']} 结论近义（相似度 {score*100:.0f}%）⇒ 同义 ADR 应合并（人工判：确属不同判据可忽略）")
    for a in adrs:
        if a["no"] in seen:
            problems.append(f"{a['id']}：编号重复（与 {seen[a['no']]} 撞号）")
        seen[a["no"]] = a["file"]
        req = {"状态": a["status"], "结论": a["conclusion"], "日期": a["date"], "裁定人": a["decider"]}
        for f in REQUIRED:
            if not req[f]:
                problems.append(f"{a['id']}：缺必填字段「{f}」")
        if a["status"] not in STATUS:
            problems.append(f"{a['id']}：状态非法「{a['status']}」")
        if f"ADR-{a['no']}-" not in a["file"]:
            problems.append(f"{a['id']}：文件名编号与标题不一致")
        if len(a["conclusion"]) > 120:
            problems.append(f"{a['id']}：结论超 120 字（{len(a['conclusion'])}）{LEN_HINT}")
        ph = placeholder_lines(a["text"])
        if ph:
            problems.append(f"{a['id']}：正文残留 {len(ph)} 处**模板占位符**（第 {'/'.join(str(p['line']) for p in ph)} 行）⇒ 这是空壳 ADR，不是判据")
        miss = missing_sections(a["text"])
        if miss:
            problems.append(f"{a['id']}：缺必填段落「{' / '.join(miss)}」")
        if a["bodyLines"] > MAX_BODY_LINES:
            problems.append(f"{a['id']}：整个文件 {a['bodyLines']} 行 > {MAX_BODY_LINES}（口径 = **文件总行数**，含头部字段行）{LEN_HINT}")
        if a["conclusion"] in conclusions:
            problems.append(f"{a['id']}：结论与 {conclusions[a['conclusion']]} 重复（同义 ADR 应合并）")
        conclusions[a["conclusion"]] = a["id"]
        if a["status"] == "生效" and "违反时的判据" not in a["text"]:
            problems.append(f"{a['id']}：生效 ADR 缺「📌 违反时的判据」（没有它 = 发现不了回潮）")
        if a["supersedes"]:
            t = by_no.get(a["supersedes"])
            if not t:
                problems.append(f"{a['id']}：取代了不存在的 {a['supersedes']}")
            else:
                if t["status"] != "已取代":
                    problems.append(f"{a['id']} 取代 {t['id']}，但 {t['id']} 状态是「{t['status']}」（应为 已取代）")
                if t["supersededBy"] != a["id"]:
                    problems.append(f"{t['id']}：被 {a['id']} 取代，但未标「被取代于：{a['id']}」（取代链断）")
            if not re.search(r"##\s*Lessons Learned", a["text"], re.I):
                problems.append(f"{a['id']}：取代了 {a['supersedes']} 却没写「## Lessons Learned」（取代轮必须交代从被取代那条学到什么）")
        if a["supersededBy"] and a["status"] != "已取代":
            problems.append(f"{a['id']}：标了被取代但状态是「{a['status']}」（应为 已取代）")
        if a["status"] == "已取代" and not a["supersededBy"]:
            problems.append(f"{a['id']}：已取代但没写「被取代于」")
        if a["status"] == "已弃用" and a["supersededBy"]:
            problems.append(f"{a['id']}：已弃用却写了「被取代于」（已弃用 = 无取代者；被取代请用「已取代」）")
        if a["status"] == "已毕业" and not a["graduatedTo"]:
            problems.append(f"{a['id']}：已毕业但没写「毕业去向」（约束升到哪个更强的载体了）")

    _, _, _, block = read_index_block()
    if block != render_index(adrs):
        write_index(adrs)
        notes.append(f"README 索引已过期 → 已自动重生成（{len(adrs)} 条）")

    if len(adrs) >= FIND_CRITICAL:
        problems.append(f"⚠️ 共 {len(adrs)} 条 ≥ {FIND_CRITICAL}：索引表**已不可导航** ⇒ 必须换载体（发布站点 / 按 tag 拆多索引）")
    elif len(adrs) >= FIND_WARN:
        problems.append(f"⚠️ 共 {len(adrs)} 条 ≥ {FIND_WARN}：接近索引表导航上限（行业实测 80 条）⇒ 启动 Find 升级（按 status/tag 分表）")

    notes.extend(sorted(near_dup))
    active = len([a for a in adrs if a["status"] not in RETIRED])
    if flag("--json"):
        print(json.dumps({"count": len(adrs), "active": active, "problems": problems, "notes": notes},
                         ensure_ascii=False, indent=2))
        return
    print(f"🔍 adr audit（只读 · **不是闸**）｜ {len(adrs)} 条（现行 {active} · 已退出 {len(adrs)-active}）｜ 问题 {len(problems)} 项")
    for p in problems:
        print(f"   {p}")
    if not problems:
        print("   （无）")
    if notes:
        print(f"   —— 另 {len(notes)} 条**建议**（人工判，不影响 exit code）：")
        for n in notes:
            print(f"   💡 {n}")


def cmd_stats() -> None:
    adrs = load_adrs()
    print(f"📊 ADR 状态分布（共 {len(adrs)} 条）")
    for s in STATUS:
        print(f"   {STATUS_ICON[s]} {s}：{len([a for a in adrs if a['status'] == s])}")
    deciders: dict[str, int] = {}
    for a in adrs:
        label = re.split(r"[（(]", a["decider"])[0].replace("**", "").strip()[:24]
        deciders[label] = deciders.get(label, 0) + 1
    print("   裁定人：" + " · ".join(f"{k} ×{v}" for k, v in deciders.items()))


def cmd_hygiene() -> None:
    adrs = load_adrs()
    active = [a for a in adrs if a["status"] == "生效"]
    total = len(adrs)
    shells = [a for a in active if placeholder_lines(a["text"]) or missing_sections(a["text"])]
    parent = {a["no"]: a["no"] for a in active}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs = 0
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            s = dice_coefficient(normalize_for_compare(active[i]["conclusion"]),
                                 normalize_for_compare(active[j]["conclusion"]))
            if s >= SIMILAR_THRESHOLD:
                pairs += 1
                parent[find(active[i]["no"])] = find(active[j]["no"])
    clusters: dict[str, list] = {}
    for a in active:
        clusters.setdefault(find(a["no"]), []).append(a)
    dup_clusters = [c for c in clusters.values() if len(c) > 1]

    print(f"🩺 adr hygiene（只读 · **不是闸**）｜ {total} 条（现行 {len(active)} · 已退出 {total-len(active)}）")
    print("")
    print(f"   ① 空壳率        {'✅ 0' if not shells else f'❌ {len(shells)} —— ' + ' '.join(a['id'] for a in shells)}")
    print(f"   ② 近义簇        {'✅ 0' if not dup_clusters else f'⚠️ {len(dup_clusters)} 簇（{pairs} 对）'}")
    for c in dup_clusters:
        print(f"        {' ↔ '.join(a['id'] for a in c)}  ⇒ 同一件事散在 {len(c)} 条，考虑合并")
    print(f"   ③ 未毕业        ⚠️ {len(active)}/{len(active)} —— ADR 是**判据的孵化器，不是永久仓库**")
    print("        手段优先级：结构上不可能 ＞ 契约层 ＞ 唯一入口 ＞ 对账测试 ＞ **文档留痕** ＞ 机器闸")
    print("        能升为自动检查的 ⇒ `status <NNNN> --to 已毕业 --note \"<载体:文件:行>\"`")

    dates = sorted(a["date"] for a in active if a["date"])
    if dates:
        from collections import Counter
        today = __import__("datetime").date.today().isoformat()
        today_n = Counter(dates).get(today, 0)
        extra = ' —— 一天 ≥3 条通常意味着"没先想清楚内容类别"' if today_n >= 3 else ""
        print(f"   ④ 今日新增      {'✅ 0' if today_n == 0 else f'⚠️ {today_n} 条'}{extra}")

    print("")
    print("   ── 判断口径（不看感觉，看这四个数）──")
    print("   · ① 必须为 0（硬伤）；② 必须为 0（同义就该合并）；")
    print("   · ③ 高不一定是病（判据还没找到更强载体），但**长期不降**要问\"是不是该升级了\"；")
    print("   · ④ 是**预警**：一天加多条时，回头问归位表 —— 这几条属于**同一类**吗？")

    if flag("--json"):
        return
    sys.exit(1 if (shells or dup_clusters) else 0)


def walk_refs(target: Path, out: list[Path]) -> list[Path]:
    if not target.exists():
        return out
    if target.is_file():
        if REFS_EXT.search(target.name):
            out.append(target)
        return out
    for name in os.listdir(target):
        if name in REFS_SKIP:
            continue
        walk_refs(target / name, out)
    return out


def is_process_doc(rel: str) -> bool:
    return bool(PROCESS_DOC_RE.match(rel))


def retired_nos() -> set[int]:
    out: set[int] = set()
    for root in REFS_ROOTS:
        for abs_ in walk_refs(ROOT / root, []):
            for line in abs_.read_text(encoding="utf-8", errors="replace").split("\n"):
                if not RETIRED_HINT.search(line):
                    continue
                for m in RE_ADR.findall(line):
                    out.add(int(m[4:]))
    return out


def scan_adr_refs(include_process_docs: bool = True) -> list[dict]:
    rows = []
    for r in REFS_ROOTS:
        for abs_ in walk_refs(ROOT / r, []):
            rel = str(abs_.relative_to(ROOT)).replace("\\", "/")
            if not include_process_docs and is_process_doc(rel):
                continue
            for i, line in enumerate(abs_.read_text(encoding="utf-8", errors="replace").split("\n")):
                m = RE_ADR.findall(line)
                if not m or EXEMPT_REF.search(line):
                    continue
                rows.append({"rel": rel, "line": i + 1, "text": line.strip()[:110], "refs": sorted(set(m))})
    return rows


def cmd_refs() -> None:
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    known = {f[4:8] for f in adr_files()}

    if not arg or arg == "--check":
        rows = scan_adr_refs(include_process_docs=False)
        ghosts: dict[str, list] = {}
        for r in rows:
            for id_ in r["refs"]:
                if id_[4:] in known:
                    continue
                ghosts.setdefault(id_, []).append(r)
        if flag("--json"):
            print(json.dumps({
                "scannedRefs": len(rows), "knownIds": len(known),
                "ghosts": [{"id": k, "at": [f"{x['rel']}:{x['line']}" for x in v]} for k, v in ghosts.items()],
            }, ensure_ascii=False, indent=2))
            sys.exit(1 if ghosts else 0)
        print(f"🔗 adr refs --check（断链：编号是否还存在）｜ 扫到 {len(rows)} 处引用 · 有效编号 {len(known)} 个")
        print("   范围：docs/ data-layer/ backtest/ .codebuddy/ daily/ Agent.md —— **不含过程文档**"
              "（`daily/**` + `data-layer/docs/plan/**` + `backtest/docs/design/**`；它们必带历史编号，扫 = 常红）")
        if not ghosts:
            print("   ✅ 0 断链（所有引用都指向存在的 ADR）")
            sys.exit(0)
        print(f"   ❌ {len(ghosts)} 个编号被引用但**已不存在**：")
        for id_, at in ghosts.items():
            print(f"      {id_}  ← {len(at)} 处")
            for a in at[:5]:
                print(f"         {a['rel']}:{a['line']}  {a['text']}")
            if len(at) > 5:
                print(f"         …（另 {len(at)-5} 处）")
        print("""
   ── 怎么修 ──
   ① 生效判据 / 活跃文档里的 ⇒ **改指现编号**（整句已作废则删该引用）；
   ② 过程文档（`daily/**` + `data-layer/docs/plan/**` + `backtest/docs/design/**`）里的 ⇒ **不动**（当时快照，回改反失真）；
   ③ 是"**ADR-00xx 已删号**"这类**历史叙述** ⇒ 已豁免，无需处理。""")
        sys.exit(1)

    rows = scan_adr_refs()
    no = re.sub(r"^ADR-?", "", str(arg), flags=re.I).zfill(4)
    if no not in known:
        die(f"ADR-{no} 不存在（可能是已删号）", f"现有编号：{' '.join(sorted(known))}")
    self_prefix = f"docs/adr/ADR-{no}-"
    mine = [r for r in rows if f"ADR-{no}" in r["refs"] and not r["rel"].startswith(self_prefix)]
    g1 = [r for r in mine if r["rel"].startswith("docs/adr/")]
    g3 = [r for r in mine if is_process_doc(r["rel"])]
    g2 = [r for r in mine if not r["rel"].startswith("docs/adr/") and not is_process_doc(r["rel"])]
    print(f"🔗 adr refs「ADR-{no}」｜ {len(mine)} 处引用（须回改 {len(g1)+len(g2)} · 过程文档 {len(g3)}）")

    def dump(title, lst):
        if not lst:
            return
        print(f"\n   {title}")
        for r in lst:
            print(f"      {r['rel']}:{r['line']}  {r['text']}")

    dump("① 生效判据（其他 ADR）—— 必须回改：", g1)
    dump("② 活跃文档 —— 必须回改：", g2)
    dump("③ 过程文档（daily/ · data-layer/docs/plan/ · backtest/docs/design/）—— **不动**：", g3)
    if not mine:
        print("   （无引用 —— 这条还没被别处当论据用过）")
    print("\n   ⇒ 回改：①② 每处都改；③ 一律不动。改完 `index --write && audit` 必须 0 问题。")


def cmd_doctor() -> None:
    self_path = os.path.abspath(__file__)
    py = sys.executable

    def run(args):
        try:
            out = subprocess.run([py, self_path, *args], cwd=str(ROOT), capture_output=True, text=True)
            return out.stdout.strip() or out.stderr.strip(), out.returncode
        except Exception as e:  # noqa: BLE001
            return str(e), 1

    def step(label, res):
        out, code = res
        print(f"\n{'✅' if code == 0 else '❌'} {label}")
        if out:
            print("\n".join(f"   {l}" for l in out.split("\n")))

    print("🩺 adr doctor —— Step 1 体检编排（只读 · **不是闸**）｜ 顺序：先 ADR，再弯路")
    a = run(["audit"])
    h = run(["hygiene"])
    r = run(["refs", "--check"])
    step("① 形式对账（audit）", a)
    step("② 可数体检（hygiene）", h)
    step("③ 跨条断链（refs --check）", r)
    print("\n④ 前人弯路（extract-detours）—— ⏳ **本项目未搬该工具**，此步跳过。")
    print("\n⓿ 结论句 vs 它自己的正文（**机器查不到** —— 语义判断；硬凑 grep 是假防线）")
    print("   ⇒ 人工：逐条并读 `- **结论**：` 与 §判据 / §决议 / §后果 / 头部补充（ADR守护者 §十问⓿）。")
    print("\n── 下一步 ──")
    print("   · 有 ❌ ⇒ 按各项自带的「怎么修」处置；")
    print("   · 要动某条 ⇒ 先 `refs <NNNN>` 拿引用面：①② 回改、③ 不动；")
    print("   · 改动落盘后：`index --write && audit` 必须 0 问题。")
    sys.exit(1 if (a[1] or h[1] or r[1]) else 0)


COMMANDS = {
    "list": cmd_list, "show": cmd_show, "search": cmd_search, "index": cmd_index,
    "add": cmd_add, "status": cmd_status, "rm": cmd_rm, "audit": cmd_audit,
    "hygiene": cmd_hygiene, "stats": cmd_stats, "refs": cmd_refs, "doctor": cmd_doctor,
}
SELF_HEAL = {"list", "show", "search", "add", "status", "audit", "hygiene", "stats", "refs"}


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if not cmd or cmd not in COMMANDS:
        die(f"未知命令：{cmd or '(空)'}",
            "list ／ show <NNNN> ／ search <关键词> ／ index [--write] ／ add ／ status ／ audit ／ "
            "hygiene ／ stats ／ refs <NNNN|--check> ／ doctor")
    if cmd in SELF_HEAL:
        try:
            adrs = load_adrs()
            _, _, _, block = read_index_block()
            if block != render_index(adrs):
                write_index(adrs)
        except Exception:  # noqa: BLE001 自愈失败不阻断主命令
            pass
    COMMANDS[cmd]()


if __name__ == "__main__":
    main()
