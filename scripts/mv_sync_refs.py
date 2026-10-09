#!/usr/bin/env python3
"""mv_sync_refs.py —— 机械改名 / 移动 / 目录搬运 + **全库同步 import**（Python 版）。

概览：本脚本只负责「文件位置与引用的一致」，不处理业务逻辑、契约表同步、测试验证、提交。
事务模型（Plan → Move → Commit）：
  1. Plan   内存规划全部 import 改写，不落盘；
  2. Move   执行物理改名/移动；
  3. Commit 物理【成功后才】把内存改动一次性刷盘。
  → 物理失败时改动自动丢弃，绝不产生「import 已改、文件却没搬」的脏写中间态。

【与源工具（TS 版）的差异 —— 为什么不能 1:1 照搬】
  源工具是 TS/JS 专用（`@babel/parser` + `typescript` LanguageService + 别名表 + 后缀约定）。
  本项目代码是 **Python**，其 import 语义与 JS 不同：
    · 模块路径由**文件相对模块根的路径**决定（`store/read.py` → `store.read`），无"说明符后缀"概念；
    · 有**相对 import**（`from .keys import Key` / `from . import read as _read`）需按导入方所在包重算；
    · 无别名（`@/`）机制。
  ⇒ 本版保留源工具**在 Python 里有意义的命令**（refs / find-dead / rename / move / move-dir / --dry / --undo），
     **不含** TS 专属命令（convert / plan / batch / rename-symbol / remove-unused-imports —— 在 Python 里无对应物）。
     取舍理由：那类命令的默认值是"猜"（后缀 / JSX 判定），本版**不猜**（承 P1）。

【模块根】本仓 import 是相对 `data-layer/` 与 `backtest/` 的（如 `from store.read import Missing`、
  `from engine.time_alignment import ...`、`import backtest_config`）⇒ `MODULE_ROOTS = ["data-layer", "backtest"]`。

【扫描集必须含"仓库根级件"】否则 `refs` 会系统性少报 fan-in（报 0 会诱导删活代码），
  `rename`/`move` 会**静默漏改**这些件里的 import。故扫描集 = 全部 `.py`（跳 `.venv` / `vendor` / `__pycache__` 等）。

用法：
  python scripts/mv_sync_refs.py refs <file> [<file2> …]      # 谁 import 它 + 字符串残留引用（只读）
  python scripts/mv_sync_refs.py find-dead [<dir>] [--strict]  # 孤儿文件（启发式，删前人工核实）
  python scripts/mv_sync_refs.py rename <file> <newName> [--dry]
  python scripts/mv_sync_refs.py move <file> <targetDirOrFile> [--dry]
  python scripts/mv_sync_refs.py move-dir <srcDir> <dstDir> [--dry]
  python scripts/mv_sync_refs.py move-dir --undo [--dry]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNDO_FILE = ROOT / "scripts" / ".move-dir-undo.json"
MODULE_ROOTS = ["data-layer", "backtest"]
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".probe", "vendor",
             "dist", "build", ".next", "coverage"}
# 入口件（不算孤儿）：即使 0 引用也不报死
ENTRY_NAMES = {"__init__.py", "__main__.py", "pipeline.py", "run_backtest.py",
               "fetch_cli.py", "cli.py", "independent_audit.py", "walk_forward_validation.py"}

IMPORT_FROM = re.compile(r"^(\s*)from\s+([.\w]+)\s+import\s+(.+?)\s*$")
IMPORT_PLAIN = re.compile(r"^(\s*)import\s+(.+?)\s*$")
IMPORT_ITEM = re.compile(r"^([.\w]+)(\s+as\s+\w+)?$")


def fail(msg: str, code: int = 1) -> None:
    print("❌ " + msg, file=sys.stderr)
    sys.exit(code)


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)).replace("\\", "/")


def iter_py_files() -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(Path(dirpath) / fn)
    return sorted(out)


def module_root_of(path: Path) -> str | None:
    for root in MODULE_ROOTS:
        try:
            path.relative_to(ROOT / root)
            return root
        except ValueError:
            continue
    return None


def module_of(path: Path) -> str | None:
    """文件 → 点分模块名（相对其模块根）；`__init__.py` 取目录名。"""
    root = module_root_of(path)
    if root is None:
        return None
    parts = list(path.relative_to(ROOT / root).parts)
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def package_of(path: Path) -> list[str]:
    """导入方所在包（其所在目录的点分路径）—— 用于相对 import 解析。"""
    root = module_root_of(path)
    if root is None:
        return []
    return list(path.relative_to(ROOT / root).parts[:-1])


def resolve_relative(mod: str, file_pkg: list[str]) -> str | None:
    """相对说明符（`.x` / `..x` / `.`）→ 绝对点分模块名；无法解析 → None。"""
    level = len(mod) - len(mod.lstrip("."))
    rest = mod.lstrip(".")
    keep = len(file_pkg) - (level - 1)
    if keep < 0:
        return None
    base = file_pkg[:keep]
    return ".".join([*base, *([rest] if rest else [])])


def relativize(target_abs: str, file_pkg: list[str]) -> str:
    """绝对点分模块名 → 相对导入写法（能相对则相对；否则返回绝对）。

    Python 语义：包 P 内的模块里，`from '.'*k import rest` 解析为 `P[:len(P)-(k-1)] + rest`。
    故从 k=1 起找第一个"祖先包是目标前缀"的 k；找不到（或祖先是根）→ 用绝对写法。
    """
    tp = target_abs.split(".") if target_abs else []
    for k in range(1, len(file_pkg) + 2):
        base_len = len(file_pkg) - (k - 1)
        if base_len <= 0:
            break  # 祖先是根 ⇒ 相对导入无意义 → 用绝对
        base = file_pkg[:base_len]
        if tp[: len(base)] == base:
            return "." * k + ".".join(tp[len(base):])
    return target_abs


def map_module(mod: str, file_pkg: list[str], old_mod: str, new_mod: str) -> str | None:
    """把一处说明符映射到新名；不需要改 → None。"""
    if mod.startswith("."):
        abs_ = resolve_relative(mod, file_pkg)
        if abs_ is None:
            return None
        if abs_ == old_mod or abs_.startswith(old_mod + "."):
            return relativize(new_mod + abs_[len(old_mod):], file_pkg)
        return None
    if mod == old_mod or mod.startswith(old_mod + "."):
        return new_mod + mod[len(old_mod):]
    return None


def _split_trailing_comment(line: str) -> tuple[str, str]:
    """把一行拆成「代码」与「行尾注释」。没有注释 → `(原文, "")`。

    ★ 为什么需要（**TD-05-27**）：第一版拿**整行**去匹配 `^import\\s+(.+?)$`，
    于是 `import statistics as _stats  # noqa: E402` 里的 `# noqa` 让 `IMPORT_ITEM`
    **匹配失败** ⇒ 该行被**静默漏改**。实测漏掉的正是本仓入口真正在用的
    `run_tugboat.py:56`（`--dry` 列表里根本没有它）。

    ⚠️ 只按**第一个 `#`** 切：Python 的 import 行里不存在含 `#` 的字符串字面量。
    """
    i = line.find("#")
    if i < 0:
        return line, ""
    return line[:i].rstrip(), line[i:]


def edge_can_resolve_to(edge: str, importer: Path | None, target: Path) -> bool:
    """`edge`（**绝对点分模块名**）解析到的**是不是 `target` 这个文件**？

    ## 为什么需要它（**TD-05-26**：一次真实的"改坏无关文件"）

    第一版只做**文本相等**（`mod == old_mod`）⇒ `data-layer/engine/industry.py` 的
    `import statistics`（**标准库**）被当成 `backtest/panel_statistics.py`；
    `--dry` 显示改名会把**两个无关文件**一起改坏。
    （`factor-layer/docs/PRD/02` 记过这个误报，但一直没修。）

    ## 判据：顶层段只在**导入方自己的模块根**里解析

    Python 只把"导入方所在的源根"放进 `sys.path` ⇒
    `X`（或 `X.y.z`）的**顶层段** `X` 只在 importer 自己的根里有意义：

    | 导入方 | 能解析到 `backtest/panel_statistics.py` 吗 |
    |---|---|
    | `backtest/entry_quality.py`（根 = `backtest`）| ✅ 同根 |
    | `data-layer/engine/industry.py`（根 = `data-layer`）| ❌ 不同根（那是**标准库**）|
    | `factor-layer/panel/stock_universe.py`（有自己的根，只是不在 `MODULE_ROOTS`）| ❌ 不同根 |
    | `run_tugboat.py`（**直接躺在仓库根**的跨层组装点，会 `sys.path.insert`）| ✅ **保守认** |

    ⇒ **宁可多改一行，也不漏真引用**；但"不同根"一律不认（那是假引用）。
    """
    if not edge or edge.startswith("."):
        return False
    imp_root = module_root_of(importer) if importer is not None else None
    if imp_root is not None:
        return imp_root == module_root_of(target)
    return importer is not None and importer.parent == ROOT


def line_edges(line: str, file_pkg: list[str]) -> set[str]:
    """一行 import 语句 → 它引用的**绝对点分模块名集合**。

    覆盖两种易漏形态（源工具用 AST 解决，本版用规则补足）：
      · `from X import a, b`  —— a/b 可能是**子模块**（如 `from store import axis`）⇒ 记 `X.a` / `X.b`；
      · `from . import read as _read` —— 相对形式引用子模块 ⇒ 记 `<pkg>.read`。
    """
    edges: set[str] = set()
    line, _cmt = _split_trailing_comment(line)   # ★ 行尾注释不能挡住匹配（TD-05-27）
    m = IMPORT_FROM.match(line)
    if m:
        mod, names = m.group(2), m.group(3)
        abs_ = resolve_relative(mod, file_pkg) if mod.startswith(".") else mod
        if abs_:
            edges.add(abs_)
            for p in names.split(","):
                nm = p.strip().split(" as ")[0].strip().strip("()")
                if nm and re.match(r"^\w+$", nm):
                    edges.add(f"{abs_}.{nm}")
        return edges
    m = IMPORT_PLAIN.match(line)
    if m:
        for p in m.group(2).split(","):
            mm = IMPORT_ITEM.match(p.strip())
            if mm:
                mod = mm.group(1)
                abs_ = resolve_relative(mod, file_pkg) if mod.startswith(".") else mod
                if abs_:
                    edges.add(abs_)
    return edges


def rewrite_content(text: str, file_pkg: list[str], old_mod: str, new_mod: str,
                    *, importer: Path | None = None, target: Path | None = None):
    """改写一份文件的 import；返回 (新文本, [(行号, 旧行, 新行)…])。

    `importer` / `target` 给定时，**只改"真的解析到 target"的那些**
    （`edge_can_resolve_to`）—— 这是"同名但不是它"（TD-05-26）的落点。

    ★ 匹配前先**拆出行尾注释**（TD-05-27）：`import x as y  # noqa` 不能被漏掉。
    """
    lines = text.split("\n")
    changes = []
    for i, raw in enumerate(lines):
        line, _cmt = _split_trailing_comment(raw)
        cmt = f"  {_cmt}" if _cmt else ""
        m = IMPORT_FROM.match(line)
        if m:
            indent, mod, names = m.groups()
            base = resolve_relative(mod, file_pkg) if mod.startswith(".") else mod
            if not base:
                continue
            if (importer is not None and target is not None
                    and not edge_can_resolve_to(base, importer, target)):
                continue
            # 情形①：`from X import …` 的 X 本身搬了
            new = map_module(mod, file_pkg, old_mod, new_mod)
            if new is not None and new != mod:
                lines[i] = f"{indent}from {new} import {names}{cmt}"
                changes.append((i + 1, line, lines[i]))
                continue
            # 情形②：`from X import sub` 里被导入的 **子模块** sub 搬了（如 `from . import read as _read`）
            kept, moved = [], []
            for p in [x.strip() for x in names.split(",")]:
                mm = re.match(r"^(\w+)(\s+as\s+\w+)?$", p)
                if mm:
                    nm, as_ = mm.group(1), mm.group(2) or ""
                    cand = f"{base}.{nm}"
                    if cand == old_mod or cand.startswith(old_mod + "."):
                        moved.append((new_mod + cand[len(old_mod):], as_))
                        continue
                kept.append(p)
            if moved:
                base_disp = relativize(base, file_pkg)
                under = [(a, s) for a, s in moved if a.startswith(base + ".")]
                outside = [(a, s) for a, s in moved if not a.startswith(base + ".")]
                out = []
                if kept or under:
                    names2 = kept + [f"{a[len(base)+1:]}{s}" for a, s in under]
                    out.append(f"{indent}from {base_disp} import {', '.join(names2)}")
                out += [f"{indent}import {a}{s}" for a, s in outside]
                lines[i] = "\n".join(out) + cmt
                changes.append((i + 1, line, lines[i]))
            continue
        m = IMPORT_PLAIN.match(line)
        if m:
            indent, rest = m.groups()
            new_parts, changed = [], False
            for p in [x.strip() for x in rest.split(",")]:
                mm = IMPORT_ITEM.match(p)
                if not mm:
                    new_parts.append(p)
                    continue
                mod, as_ = mm.group(1), mm.group(2) or ""
                if (importer is not None and target is not None
                        and not edge_can_resolve_to(mod, importer, target)):
                    new_parts.append(p)
                    continue
                new = map_module(mod, file_pkg, old_mod, new_mod)
                if new is not None and new != mod:
                    changed = True
                    new_parts.append(new + as_)
                else:
                    new_parts.append(p)
            if changed:
                lines[i] = f"{indent}import {', '.join(new_parts)}{cmt}"
                changes.append((i + 1, line, lines[i]))
    return "\n".join(lines), changes


# ── refs（只读：fan-in + 字符串残留）────────────────────────────────────────
def cmd_refs(args: list[str]) -> None:
    targets = [a for a in args if not a.startswith("--")]
    if not targets:
        fail("用法：python scripts/mv_sync_refs.py refs <file> [<file2> …]")
    files = iter_py_files()
    for t in targets:
        tf = (ROOT / t).resolve()
        if not tf.exists():
            print(f"⚠️  {t} 不存在 —— 跳过（若刚被删，用 `git log --diff-filter=D -- {t}` 查何时被删）")
            continue
        old_mod = module_of(tf)
        fname = tf.name
        print(f"\n🔗 refs「{t}」｜ 模块名 {old_mod or '（不在模块根下）'}")
        imports, residual = [], []
        for f in files:
            if f.resolve() == tf:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fpkg = package_of(f)
            for i, line in enumerate(text.split("\n"), 1):
                if line.strip().startswith(("import ", "from ")):
                    # ★ 与 `rename` **共用同一个谓词**：只认"真的解析到 tf"的那些
                    #   （`data-layer` 里的标准库 `statistics` **不是** `backtest/panel_statistics.py`）。
                    if old_mod and any(
                            (e == old_mod or e.startswith(old_mod + "."))
                            and edge_can_resolve_to(e, f, tf)
                            for e in line_edges(line, fpkg)):
                        imports.append((rel(f), i, line.strip()))
                    continue
                # 字符串残留：只认**模块点分名**或**文件名**（不含裸词干 —— 那会大面积误报）
                if (old_mod and old_mod in line) or fname in line:
                    residual.append((rel(f), i, line.strip()))
        print(f"   ① 谁 import 它（fan-in {len(imports)}）：")
        for r, i, line in imports[:60]:
            print(f"      {r}:{i}  {line[:110]}")
        if len(imports) > 60:
            print(f"      …（另 {len(imports)-60} 处）")
        if not imports:
            print("      （无 —— 0 引用且非入口 = 死代码候选；⚠️ 报 0 先怀疑扫描集是否漏了根级件）")
        print(f"   ② 字符串残留引用（{len(residual)}，含注释/文档，需人工剔除）：")
        for r, i, line in residual[:30]:
            print(f"      {r}:{i}  {line[:110]}")
        if len(residual) > 30:
            print(f"      …（另 {len(residual)-30} 处）")


# ── find-dead（启发式孤儿）───────────────────────────────────────────────────
def inbound_map() -> dict[str, list[tuple[str, int]]]:
    files = iter_py_files()
    mods = {module_of(f): f for f in files if module_of(f)}
    inbound: dict[str, list[tuple[str, int]]] = {m: [] for m in mods}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fpkg = package_of(f)
        for i, line in enumerate(text.split("\n"), 1):
            if not line.strip().startswith(("import ", "from ")):
                continue
            for e in line_edges(line, fpkg):
                if e in inbound and mods[e] != f and edge_can_resolve_to(e, f, mods[e]):
                    inbound[e].append((rel(f), i))
    return inbound


def cmd_find_dead(args: list[str]) -> None:
    strict = "--strict" in args
    scope = next((a for a in args if not a.startswith("--")), None)
    inbound = inbound_map()
    files = iter_py_files()
    if scope:
        files = [f for f in files if rel(f).startswith(scope.rstrip("/"))]
    dead, test_only = [], []
    for f in files:
        mod = module_of(f)
        if mod is None:
            continue
        refs = inbound.get(mod, [])
        is_entry = f.name in ENTRY_NAMES or f.name.startswith("test_") or f.name.startswith("verify_")
        if not is_entry:
            try:
                if 'if __name__ == "__main__"' in f.read_text(encoding="utf-8"):
                    is_entry = True
            except (OSError, UnicodeDecodeError):
                pass
        if not refs and not is_entry:
            dead.append(f)
        elif refs and all("/tests/" in r or r.startswith("data-layer/tests/") or r.startswith("backtest/tests/") for r, _ in refs) and not is_entry:
            test_only.append(f)
    print(f"🪦 find-dead（启发式 · 删前人工核实）｜ 扫描 {len(files)} 个 .py")
    print(f"\n── 0 引用且非入口（{len(dead)}）")
    for f in dead:
        print(f"   {rel(f)}")
    if not dead:
        print("   （无）")
    if not strict:
        print(f"\n── 仅被测试引用（{len(test_only)}）—— 问一句「它是不是只为这个测试而存在」")
        for f in test_only:
            print(f"   {rel(f)}")
        if not test_only:
            print("   （无）")
    print("\n⚠️ 本命令是**启发式**：`refs` 报 0 单独不算证据（字符串残留 / 动态引用可能漏）。删前按 7 步法 Step 6 四口径交叉。")


# ── rename / move / move-dir（事务：Plan → Move → Commit）───────────────────
def prune_empty_dirs(removed: list[Path]) -> None:
    """搬空后清理**空目录**（`move-dir` / `--undo` 的收尾）。

    为什么需要：逐文件搬移**不会**自动删掉源目录 ⇒ `move-dir a b` 后 `a/` 空着，
    `--undo` 后 `b/` 又空着 —— "一键回退"名不副实（留下空壳目录 = 回退不彻底）。
    `rmdir` 只对**空**目录成功，非空自动抛错跳过 ⇒ 天然安全（不会误删有内容的目录）。
    """
    dirs: set[Path] = set()
    for p in removed:
        d = p.parent
        while d != ROOT and ROOT in d.parents:
            dirs.add(d)
            d = d.parent
    for d in sorted(dirs, key=lambda x: len(x.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass


def do_move(old: Path, new: Path) -> None:
    new.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["git", "mv", str(old), str(new)], cwd=str(ROOT), check=True,
                       capture_output=True)
    except Exception:  # noqa: BLE001 非 git 跟踪 / 非 git 仓库 → 退化为物理移动
        shutil.move(str(old), str(new))


def apply_rewrites(mapping: dict[Path, Path], dry: bool) -> int:
    """mapping: 旧文件 → 新文件。Plan（内存）→ Move（物理）→ Commit（刷盘）。返回改写的 import 处数。"""
    old_to_new: dict[str, str] = {}
    old_mod_to_path: dict[str, Path] = {}
    for old, new in mapping.items():
        om, nm = module_of(old), module_of(new)
        if not om or not nm:
            fail(f"文件不在模块根下（{rel(old)} → {rel(new)}）：无法计算模块名")
        old_mod_to_path[om] = old
        if om != nm:
            old_to_new[om] = nm
    move_of = {o.resolve(): n for o, n in mapping.items()}

    # ── Plan：被搬的文件用**新路径的包**（搬完它就在那），其余用自身包 ──
    edited: dict[Path, tuple[str, list]] = {}
    if old_to_new:
        for f in iter_py_files():
            target = move_of.get(f.resolve(), f)
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            cur, all_changes = text, []
            for om, nm in old_to_new.items():
                # ★ `importer=f`（引用**来自哪个文件**）+ `target=`（判"解析到的是不是它"）
                #   —— 少一个，就是 TD-05-26 那类"改坏无关文件"。
                cur, ch = rewrite_content(cur, package_of(target), om, nm,
                                          importer=f, target=old_mod_to_path[om])
                all_changes += ch
            if all_changes:
                edited[target] = (cur, all_changes)

    total = 0
    for f, (new_text, changes) in edited.items():
        print(f"\n   {rel(f)}")
        for ln, old_line, new_line in changes:
            print(f"      L{ln}  - {old_line.strip()[:100]}")
            print(f"            + {new_line.strip()[:100]}")
            total += 1

    if dry:
        return total
    # ── Move：物理搬移（失败即中断，改动不落盘）──
    for old, new in mapping.items():
        do_move(old, new)
    prune_empty_dirs(list(mapping.keys()))  # 搬空后清理空目录（回退才彻底）
    # ── Commit：物理成功后才刷盘 ──
    for f, (new_text, _) in edited.items():
        f.write_text(new_text, encoding="utf-8")
    return total


def cmd_rename(args: list[str]) -> None:
    pos = [a for a in args if not a.startswith("--")]
    dry = "--dry" in args
    if len(pos) < 2:
        fail("用法：python scripts/mv_sync_refs.py rename <file> <newName> [--dry]")
    src = ROOT / pos[0]
    if not src.exists():
        fail(f"源文件不存在：{pos[0]}")
    new_name = pos[1] if pos[1].endswith(".py") else pos[1] + ".py"
    dst = src.parent / new_name
    if dst.exists():
        fail(f"目标已存在：{rel(dst)}（同目录不可覆盖）")
    print(f"{'（dry-run）' if dry else ''}rename：{rel(src)} → {rel(dst)}")
    n = apply_rewrites({src: dst}, dry)
    print(f"\n   共 {n} 处 import 待改写")
    print(f"{'（dry-run，未落盘）' if dry else '✅ 已改名并同步 import'}")


def cmd_move(args: list[str]) -> None:
    pos = [a for a in args if not a.startswith("--")]
    dry = "--dry" in args
    if len(pos) < 2:
        fail("用法：python scripts/mv_sync_refs.py move <file> <targetDirOrFile> [--dry]")
    src = ROOT / pos[0]
    if not src.exists():
        fail(f"源文件不存在：{pos[0]}")
    tgt = ROOT / pos[1]
    dst = (tgt / src.name) if tgt.is_dir() else tgt
    if dst.exists() and dst.resolve() != src.resolve():
        fail(f"目标已存在：{rel(dst)}")
    print(f"{'（dry-run）' if dry else ''}move：{rel(src)} → {rel(dst)}")
    n = apply_rewrites({src: dst}, dry)
    print(f"\n   共 {n} 处 import 待改写")
    print(f"{'（dry-run，未落盘）' if dry else '✅ 已移动并同步 import'}")


def cmd_move_dir(args: list[str]) -> None:
    dry = "--dry" in args
    if "--undo" in args:
        if not UNDO_FILE.exists():
            fail(f"没有回退记录：{rel(UNDO_FILE)}（只有跑过 `move-dir` 才有）")
        rec = json.loads(UNDO_FILE.read_text(encoding="utf-8"))
        mapping = {ROOT / n["new"]: ROOT / n["old"] for n in rec["files"]}
        print(f"{'（dry-run）' if dry else ''}move-dir --undo：{rec['src']} ← {rec['dst']}（{len(mapping)} 个文件）")
        n = apply_rewrites(mapping, dry)
        print(f"\n   共 {n} 处 import 待还原")
        if not dry:
            UNDO_FILE.unlink()
        print(f"{'（dry-run，未落盘）' if dry else '✅ 已整目录回退并还原 import'}")
        return

    pos = [a for a in args if not a.startswith("--")]
    if len(pos) < 2:
        fail("用法：python scripts/mv_sync_refs.py move-dir <srcDir> <dstDir> [--dry]")
    src_dir, dst_dir = ROOT / pos[0], ROOT / pos[1]
    if not src_dir.is_dir():
        fail(f"源目录不存在：{pos[0]}")
    files = [p for p in sorted(src_dir.rglob("*")) if p.is_file()]
    if not files:
        fail(f"源目录为空：{pos[0]}")
    mapping = {f: dst_dir / f.relative_to(src_dir) for f in files}
    print(f"{'（dry-run）' if dry else ''}move-dir：{pos[0]} → {pos[1]}（{len(mapping)} 个文件）")
    n = apply_rewrites(mapping, dry)
    print(f"\n   共 {n} 处 import 待改写")
    if not dry:
        UNDO_FILE.write_text(json.dumps({
            "src": pos[0], "dst": pos[1],
            "files": [{"old": rel(o), "new": rel(nw)} for o, nw in mapping.items()],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   ↳ 回退记录已写入 {rel(UNDO_FILE)}（`move-dir --undo` 一键回退）")
    print(f"{'（dry-run，未落盘）' if dry else '✅ 已整目录搬运并同步 import'}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    cmd, rest = args[0], args[1:]
    table = {"refs": cmd_refs, "find-dead": cmd_find_dead, "rename": cmd_rename,
             "move": cmd_move, "move-dir": cmd_move_dir}
    if cmd not in table:
        fail(f"未知命令：{cmd}（refs ／ find-dead ／ rename ／ move ／ move-dir）")
    table[cmd](rest)


if __name__ == "__main__":
    main()
