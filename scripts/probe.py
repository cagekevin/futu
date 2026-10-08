#!/usr/bin/env python3
"""「先红后绿」探针执行器 —— **临时注入 → 跑命令 → 精确断言 → 自动还原**。

【为什么存在】`架构师改码7步法` §7.1 / `债务登记5步法`（工具债硬要求）都强制要求"先红后绿"证据：
  ① **行为探针**：把修复**临时还原成旧实现** → 期待测试**精确变红**；
  ② **闸探针（负例探针）**：往源码注入一处违规 → 期待检查脚本**精确报该处**（exit≠0 + 命中提示）。
  但每次都是**手工改源码 + 手工还原**，有三个真实风险：
    · **忘还原** → 注入态留在工作区/git 里（假账 + 后续测试带着违规跑）；
    · **红的不是那点** → 注入点不唯一/没命中时，命令照样失败，人眼分不清"打中了"还是"本来就红"；
    · **证据靠手抄** → 探针结论要人工搬进区域日志，易漏易失真。
  本工具把这三件事变成机器判定：**原子还原 + 精确断言 + 结论块直出**。

【只做一件事】临时改一个文件的一处 → 跑一条命令 → 比对"退出码 + 输出命中" → 无条件还原。

【保命机制（四条）】
  1. **journal 兜底**：注入前把**原文全文**写进 `scripts/.probe/<ts>-<name>.journal.json`；还原在 finally。
     启动时先 `recover_stale()` —— 若发现上次被 Ctrl+C/崩溃打断留下的 journal，**先还原再干活**。
  2. **注入点唯一性**：字面量/正则默认必须**恰好命中 1 处**；0 处 = 探针无效（exit 2），>1 处须显式 `--all`。
     0 处也要报错，否则"命令失败"会被误当"探针命中"（假护栏恒绿同款教训）。
  3. **还原自校验**：还原后重算 sha256，与注入前不一致 → 大声报错并保留 journal（供手工恢复）。
  4. **并发编辑守卫**：注入前 / 还原前各校验一次目标文件 sha；与期望不符 ⇒ **拒绝覆盖** + exit 2。

【用法】
  # 行为探针：临时还原旧实现 → 期待测试变红
  python scripts/probe.py --label "TD-xx 行为探针" \\
    --file data-layer/engine/gex.py --find "<新实现>" --replace "<旧实现>" \\
    --run "data-layer/.venv/bin/python -m unittest data-layer/tests/test_engine.py" \\
    --expect-exit 1 --expect-out "AssertionError"

  # 正则注入（含捕获组，\\1..\\9 可用）+ 干跑预览（不写盘）
  python scripts/probe.py --file a.py --find-re "foo\\(\\s*\\)" --replace "foo(1)" --run "..." --dry

【参数】
  --file <相对仓库根的路径>     必填
  --find <字面量> | --find-re <正则>   必填，二选一
  --replace <替换文本>          必填（正则模式支持 \\1..\\9）
  --run "<shell 命令>"          必填，cwd = 仓库根
  --expect-exit <N>            期待退出码（默认不校验退出码，显式给才校验）
  --expect-out <子串>          期待命中（stdout+stderr 合并后）
  --expect-not-out <子串>      期待不出现（防"红在别处"）
  --label <名字>               探针名（进结论块，便粘贴）
  --all                        允许注入点出现多处时全部替换
  --dry                        只预览注入 diff，不写盘、不跑命令
  --suggest-out                跑**未注入**命令，列出可用作 --expect-out 的候选串
  --skip-baseline              跳过"基线必绿"前置检查（默认开启）

【基线必绿（默认开启）】注入前先跑一遍**未注入**的同命令；若它本来就失败，则注入后的"红"与你的修复无关
  ⇒ 直接拒跑（exit 2）。否则写进日志就是一条假证据。

【退出码】0 = 观测与期望**一致**（探针命中）；1 = 不一致（探针未命中）；2 = 探针本身无效。
【界限】临时改文件这一动作**只允许通过本工具做**；用完即还原，**不允许 --keep 式保留**（那是假账的入口）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOURNAL_DIR = ROOT / "scripts" / ".probe"

ANSI_RE = re.compile(r"\u001b\[[0-9;]*m")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def fail(msg: str, code: int = 2):
    print(f"❌ 探针无效：{msg}", file=sys.stderr)
    sys.exit(code)


argv = sys.argv[1:]


def val(flag_name: str) -> str:
    if flag_name in argv:
        i = argv.index(flag_name)
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            return argv[i + 1]
    return ""


def has(flag_name: str) -> bool:
    return flag_name in argv


def run_cmd(cmd: str):
    """跑一条 shell 命令 → (exit_code, 合并输出)。

    ⚠️ `subprocess.run` **不会**在非零退出时抛错（与 JS `execSync` 相反）⇒ 必须显式取 `returncode`，
    否则任何失败都会被当成 exit=0（= 假绿，正是本工具要消灭的东西）。
    """
    try:
        p = subprocess.run(cmd, shell=True, cwd=str(ROOT), capture_output=True, text=True)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def extract_candidates(text: str) -> list[tuple[str, int]]:
    """从未注入输出里提取"适合当 --expect-out 的候选串"（按出现次数降序）。"""
    out: dict[str, int] = {}

    def bump(s: str):
        t = s.strip()
        if 4 <= len(t) <= 100:
            out[t] = out.get(t, 0) + 1

    for raw in text.split("\n"):
        l = raw.strip()
        if not l:
            continue
        for m in re.finditer(r"([\w./-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs)):(\d+)(?::\d+)?", l):
            bump(f"{m.group(1)}:{m.group(2)}")
        for m in re.finditer(r"\b(AssertionError|Error|FAILED|Traceback|assertEqual|assertRaises)\b", l):
            bump(m.group(1))
        if re.match(r"^(FAILED|ERROR|✖|×|✕|✗)\b", l) or re.match(r"^[\w./-]+\.py:\d+:", l):
            bump(l[:90])
    return sorted(out.items(), key=lambda x: -x[1])[:24]


# ── --suggest-out：只跑"未注入"的命令，不需要注入参数 ──────────────────────
_run = val("--run")
if not _run:
    fail("缺 --run")
if has("--suggest-out"):
    print(f"\n🧭 建议断言串｜在**未注入**状态下跑一次，提取可用作 --expect-out 的候选\n   命令: {_run}\n")
    base_exit, base_out = run_cmd(_run)
    clean = strip_ansi(base_out)
    cands = extract_candidates(clean)
    if not clean.strip():
        print("   ⚠️  未注入时命令**零输出**。这通常意味着：")
        print("       · 命令本身跑不起来（先确认基线），或")
        print("       · 这组用例会把进程挂死。→ 挂死时 probe 不适用，改用受控手工注入 + git diff 核对。")
    elif not cands:
        print("   （未提取到候选——输出里没有 文件:行 / 失败标记行）")
        print("   原始输出尾部：")
        for l in [x for x in clean.split("\n") if x][-10:]:
            print(f"   | {l[:160]}")
    else:
        print(f"   未注入基线: exit={base_exit}\n")
        print("   候选（按出现次数降序；次数高 = 多条失败路径都含它 = 更稳）:")
        for s, n in cands:
            print(f"     {n:>2}×  {s}")
        print("\n   ⚠️  基线必须为绿（exit=0）才有意义：基线是红的 ⇒ 红的是环境不是你的注入。")
        if base_exit != 0:
            print(f"   ⛔ 实测基线 exit={base_exit} → **当前命令本来就失败**，不能用它做探针。")
    sys.exit(0)

file_arg = val("--file")
find_lit = val("--find")
find_re = val("--find-re")
if "--replace" in argv:
    i = argv.index("--replace")
    replace = argv[i + 1] if i + 1 < len(argv) else None
else:
    replace = None
label = val("--label") or "probe"
expect_exit_raw = val("--expect-exit")
expect_out = val("--expect-out")
expect_not_out = val("--expect-not-out")
allow_all = has("--all")
dry = has("--dry")
skip_baseline = has("--skip-baseline")

if not file_arg:
    fail("缺 --file")
if not find_lit and not find_re:
    fail("缺 --find 或 --find-re")
if find_lit and find_re:
    fail("--find 与 --find-re 二选一，别同时给")
if replace is None:
    fail('缺 --replace（要删成空串也请显式传 --replace ""）')
if not dry and not _run:
    fail("缺 --run（或用 --dry 只预览）")

target = ROOT / file_arg
if not target.exists():
    fail(f"--file 不存在：{file_arg}")


def _purge_bytecode_cache(module_file: Path) -> int:
    """删掉目标文件对应的 `.pyc` —— **还原后必须清**。

    为什么：Python 按 `(mtime 秒级, size)` 校验字节码缓存。探针的「注入 → 跑 → 还原」
    常发生在**同一秒内**；若 size 恰好未变，缓存会被判定为有效
    ⇒ **源码已还原，但执行的仍是注入期的字节码**。

    ⚠️ 实测踩过（2026-10-06，TD-05-1 先红后绿）：探针还原后 `sed` 看源码确为
    「簇级重采样」，跑出来却是「单观测重采样」的宽度 —— 加载了注入期编译的 `.pyc`。
    **源码与实际执行的代码不一致 = 假账**，且比"忘还原"更难发现（源码是对的）。
    门禁（跑测试）抓住了它；`sha256` 自校验**抓不到**（它只校验文本，不校验字节码）。

    ⇒ 还原后清缓存，把「下次 import 必然重编译」变成确定事实，而不是靠 mtime 碰运气。
    """
    cache_dir = module_file.parent / "__pycache__"
    if not cache_dir.is_dir():
        return 0
    removed = 0
    for cached in cache_dir.glob(f"{module_file.stem}.*.pyc"):
        cached.unlink()
        removed += 1
    return removed


def recover_stale() -> None:
    """启动自愈：还原上次被中断的探针（journal 里存的是原文全文）。"""
    if not JOURNAL_DIR.exists():
        return
    stale = [n for n in os.listdir(JOURNAL_DIR) if n.endswith(".journal.json")]
    if not stale:
        return
    print(f"⚠️  发现 {len(stale)} 份上次残留的探针 journal → 先自愈还原：")
    for name in stale:
        p = JOURNAL_DIR / name
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            t = ROOT / j["file"]
            cur = t.read_text(encoding="utf-8") if t.exists() else ""
            if sha(cur) != j["sha256"]:
                t.write_text(j["original"], encoding="utf-8")
                _purge_bytecode_cache(t)
                print(f"   已还原 {j['file']}（{name}）")
            else:
                print(f"   无需还原 {j['file']}（已是原文，{name}）")
            p.unlink()
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠️ journal 解析/还原失败（保留原文件待人工）：{name} — {e}")


recover_stale()

original = target.read_text(encoding="utf-8")
original_sha = sha(original)

# ── 计算注入结果（并强制注入点唯一）─────────────────────────────────────────
if find_re:
    hit_count = len(re.findall(find_re, original))
    # `replace` 原样交给 re.sub（正则模式支持 \1..\9 捕获组引用）
    injected = re.sub(find_re, replace, original, count=0 if allow_all else 1)
else:
    hit_count = original.count(find_lit)
    injected = original.replace(find_lit, replace) if allow_all else original.replace(find_lit, replace, 1)

if hit_count == 0:
    fail(f"注入点未命中（探针无效）：--{'find-re' if find_re else 'find'} 在 {file_arg} 里 0 处匹配。\n"
         "   → 0 处也要报错：否则\"命令本来就失败\"会被误当\"探针命中\"。")
if hit_count > 1 and not allow_all:
    fail(f"注入点不唯一：在 {file_arg} 里命中 {hit_count} 处 → 红的可能不是你要验的那点。\n"
         "   → 加长 --find 上下文使其唯一，或确知要全改时显式 --all。")
if injected == original:
    fail("注入后内容与原文相同（--replace 与 --find 等价）→ 什么都没改，不构成探针")

# ── 基线必绿前置 ────────────────────────────────────────────────────────────
if not dry and _run and not skip_baseline:
    base_exit, base_out = run_cmd(_run)
    clean_base = strip_ansi(base_out)
    if base_exit != 0:
        print(f"❌ 探针无效（基线不绿）：未注入时同命令已经 exit={base_exit} → 注入后的\"红\"与你的修复无关。\n"
              "   → 这不是\"先红成功\"，是环境造成的**假红**；写进日志就是假证据。\n"
              "   → 正确处置：① 换用该测试被设计运行的方式；② 或先用 --suggest-out 看未注入的真实输出/退出码。\n"
              "   → 确认这条命令的失败与你的注入无关时，显式加 --skip-baseline 跳过本检查。", file=sys.stderr)
        print("   ── 未注入输出尾部 ──", file=sys.stderr)
        for l in [x for x in clean_base.split("\n") if x][-8:]:
            print(f"   | {l[:160]}", file=sys.stderr)
        sys.exit(2)

# ── 预览 ────────────────────────────────────────────────────────────────────
if dry:
    before = original.split("\n")
    after = injected.split("\n")
    first = next((i for i in range(max(len(before), len(after)))
                  if (before[i] if i < len(before) else None) != (after[i] if i < len(after) else None)), 0)
    print(f"🔍 干跑预览（未写盘）｜{file_arg}｜命中 {hit_count} 处｜sha {original_sha} → {sha(injected)}")
    print(f"   首个差异行 L{first + 1}:")
    print(f"     - {(before[first] if first < len(before) else '').strip()[:140]}")
    print(f"     + {(after[first] if first < len(after) else '').strip()[:140]}")
    sys.exit(0)

# ── 注入 + 跑 + 还原 ────────────────────────────────────────────────────────
stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%f")
journal_path = JOURNAL_DIR / f"{stamp}-{re.sub(r'[^\w\u4e00-\u9fa5-]+', '_', label)}.journal.json"
injected_sha = sha(injected)


def guard_no_concurrent_edit(phase: str) -> None:
    want = original_sha if phase == "before-inject" else injected_sha
    cur = sha(target.read_text(encoding="utf-8"))
    if cur == want:
        return
    print(f"❌ 探针无效（{phase}：目标文件被并发改动）｜{file_arg}", file=sys.stderr)
    print(f"   当前 sha={cur} ≠ 期望 sha={want}", file=sys.stderr)
    print("   → 已**拒绝覆盖**（防静默丢弃别人的改动）。确认无并发编辑后再跑。", file=sys.stderr)
    if phase == "before-inject":
        print("   → 本次**未写盘**，文件保持改动后的现状（探针没有污染它）。", file=sys.stderr)
    else:
        print(f"   → 注入前原文保留在 journal，请手工合并：{journal_path.relative_to(ROOT)}", file=sys.stderr)
    sys.exit(2)


guard_no_concurrent_edit("before-inject")
JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
journal_path.write_text(json.dumps({"file": file_arg, "label": label, "cmd": _run,
                                    "sha256": original_sha, "original": original},
                                   ensure_ascii=False), encoding="utf-8")
target.write_text(injected, encoding="utf-8")

exit_code, output = 0, ""
try:
    exit_code, output = run_cmd(_run)
finally:
    guard_no_concurrent_edit("before-restore")
    target.write_text(original, encoding="utf-8")
    restored = sha(target.read_text(encoding="utf-8"))
    if restored != original_sha:
        print(f"❌ 还原自校验失败！{file_arg} 当前 sha={restored} ≠ 原 sha={original_sha}", file=sys.stderr)
        print(f"   原文已保留在 journal，请手工恢复：{journal_path.relative_to(ROOT)}", file=sys.stderr)
        sys.exit(2)
    purged = _purge_bytecode_cache(target)
    if purged:
        print(f"   ↳ 已清 {purged} 个 .pyc（防还原后仍执行注入期字节码）")
    journal_path.unlink()

clean = strip_ansi(output)

# ── 断言（观测 == 期望 才算"命中"）──────────────────────────────────────────
checks = []
if expect_exit_raw != "":
    want = int(expect_exit_raw)
    checks.append((f"退出码 == {want}", exit_code == want, f"实际 {exit_code}"))
if expect_out:
    ok = expect_out in clean
    checks.append((f"输出含「{expect_out}」", ok, "命中" if ok else "未命中"))
if expect_not_out:
    ok = expect_not_out not in clean
    checks.append((f"输出**不含**「{expect_not_out}」", ok, "出现了" if not ok else "未出现"))
if not checks:
    print("\n⚠️  未给任何断言（--expect-exit / --expect-out / --expect-not-out）→ 只报观测值，**不构成探针证据**。")

passed = all(c[1] for c in checks)

print(f"\n🔬 探针结论｜{label}")
print(f"   文件   : {file_arg}（命中 {hit_count} 处，已还原 sha={original_sha}）")
print(f"   命令   : {_run}")
print(f"   观测   : exit={exit_code}" + (f"｜输出 {len([x for x in clean.split(chr(10)) if x])} 行" if clean else ""))
for name, ok, got in checks:
    print(f"   {'✅' if ok else '❌'} {name} — {got}")

if not checks:
    sys.exit(0 if passed else 1)
if passed:
    print("\n✅ 探针命中（观测与期望一致）—— 可直接粘贴本块进区域日志作为\"先红后绿\"证据。")
    sys.exit(0)
print("\n❌ 探针未命中（观测 ≠ 期望）—— 探针本身没问题，是\"你修的那点\"没被测到；回去改断言或改探针，别跳过。")
if output:
    print("   ── 输出尾部（定位用）──")
    for l in [x for x in clean.split("\n") if x][-12:]:
        print(f"   | {l[:160]}")
sys.exit(1)
