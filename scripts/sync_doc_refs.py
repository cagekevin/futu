#!/usr/bin/env python3
"""sync_doc_refs.py —— 文档引用的批量同步（`.md` / `.py` 注释 / `.txt`）。

## 为什么需要它

`scripts/mv_sync_refs.py` 只处理 **Python 模块**（`.py` 的 import 与字符串）。
**文档（`.md`）之间的引用一直没有工具** —— 于是"改名 / 搬移后引用会漂"这件事，
在文档侧只能靠人肉维护。本工具补上这一半。

配套纪律（见 `Agent.md` §7「文档引用」）：

> **文档引用只写「`关于策略/<主题>/<文件名>`」——不写主题内部的物理层级。**
> **主题是逻辑分区（稳定）；主题内部怎么嵌套，不进引用。**

## 用法

```bash
python scripts/sync_doc_refs.py <映射表>            # 默认 dry-run（只报告）
python scripts/sync_doc_refs.py <映射表> --apply    # 实际改写
```

## 映射表格式（UTF-8，一行一条）

```
# 以 # 开头的是注释
旧串 -> 新串
旧串<TAB>新串
```

## 行为

- **扫描范围**：仓库内所有 `.md` / `.py` / `.txt`
- **跳过**：`.git` `.venv` `__pycache__` `node_modules`，以及**任何点开头的文件 / 目录**（所以映射表本身可以放在仓库里）
- **匹配**：**字面替换，长串优先**（避免短编号先吃掉长全名 —— 例：`…/11` 不该先于 `…/11-验证-…-2026-10-08.md` 命中）
- **输出**：逐文件的命中数 + 总计；**未命中的条目会单独列出**（防止映射表里有过时条目）
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXTS = {".md", ".py", ".txt"}
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}


def iter_targets() -> list[Path]:
    """仓库内所有可扫描文件，跳过隐藏路径与依赖目录。"""
    out: list[Path] = []
    for p in REPO.rglob("*"):
        if not p.is_file() or p.suffix not in EXTS:
            continue
        rel = p.relative_to(REPO)
        if any(part.startswith(".") or part in SKIP_DIRS for part in rel.parts):
            continue
        out.append(p)
    return sorted(out)


def load_map(path: Path) -> list[tuple[str, str]]:
    """读映射表；返回按「旧串长度降序」排序的列表（长串优先）。"""
    rules: list[tuple[str, str]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "->" in line:
            old, new = line.split("->", 1)
        elif "\t" in line:
            old, new = line.split("\t", 1)
        else:
            raise SystemExit(f"❌ 映射表第 {lineno} 行无法解析（需要 `旧 -> 新` 或 TAB 分隔）：{line!r}")
        old, new = old.strip(), new.strip()
        if not old:
            raise SystemExit(f"❌ 映射表第 {lineno} 行旧串为空")
        rules.append((old, new))
    if not rules:
        raise SystemExit("❌ 映射表为空")
    rules.sort(key=lambda r: len(r[0]), reverse=True)
    return rules


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply_ = "--apply" in sys.argv
    if len(args) != 1:
        print(__doc__.strip().split("## 用法")[1].split("## 映射表格式")[0].strip())
        return 2

    map_path = Path(args[0])
    if not map_path.is_absolute():
        map_path = REPO / map_path
    if not map_path.is_file():
        raise SystemExit(f"❌ 映射表不存在：{map_path}")

    rules = load_map(map_path)
    files = iter_targets()
    print(f"{'（dry-run）' if not apply_ else '（apply）'}sync_doc_refs"
          f"：映射 {len(rules)} 条 ｜ 扫描 {len(files)} 个文件（.md/.py/.txt）\n")

    hit_by_rule = [0] * len(rules)
    changed_files = 0
    total_hits = 0

    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        original = text
        hits_here = 0
        for i, (old, new) in enumerate(rules):
            n = text.count(old)
            if n:
                text = text.replace(old, new)
                hit_by_rule[i] += n
                hits_here += n
        if hits_here:
            changed_files += 1
            total_hits += hits_here
            print(f"  ✏️  {f.relative_to(REPO).as_posix()}  ({hits_here} 处)")
            if apply_:
                f.write_text(text, encoding="utf-8")
            assert text != original or hits_here == 0

    print(f"\n命中：{total_hits} 处 ｜ 涉及 {changed_files} 个文件")
    if not apply_:
        print("（dry-run：未写盘。确认无误后加 --apply）")

    unused = [(r[0], hit_by_rule[i]) for i, r in enumerate(rules) if hit_by_rule[i] == 0]
    if unused:
        print(f"\n⚠️ 未命中的映射条目（{len(unused)} 条）—— 可能已过时，建议核对：")
        for old, _ in unused:
            print(f"     {old}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
