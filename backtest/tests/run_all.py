#!/usr/bin/env python3
"""**跑回测层全部测试** —— 因为 `unittest discover` 收不全。

## 为什么需要它（一次真实的低报）

`python3 -m unittest discover -s tests` **只收到 39 项**
（= `test_causality` 3 + `test_statistics` 36），
而 `tests/` 下有 **8 个文件、共 100+ 项**。

原因：本层大多数测试是**函数式**（`def test_xxx() -> bool` + 自己的 runner），
不是 `unittest.TestCase` ⇒ `discover` **一个都收不到**。

⇒ 于是"backtest 97 项全绿"这句话，作为**测试规模**没错，
但作为"**核心代码被验证过**"的印象是**失真**的：
真正管 Tugboat 的（simulator / exposure / metrics / causality）**全在 discover 之外**。

（这个低报是**外部独立复审**指出来的 —— 项目文档当时只提醒了
 `test_performance_metrics` 一个漏网，实际漏 **4 个文件**。）

## 用法

```bash
cd backtest
python3 tests/run_all.py            # 跑全部，失败则退出码非 0
```

⚠️ **判定不只看最后一行**：本层有测试在失败时最后一行仍是 `26/27 passed`
（不含 "FAIL" 字样）⇒ 一律**扫全文找 `[FAIL]`**。
（这也是一个真实踩过的坑：旧的统计脚本只看最后一行，**把失败当成了通过**。）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: 不是"某个功能的测试"的文件（`run_all` 自己、共享夹具…）
SKIP = {"run_all.py", "__init__.py"}

#: ★ 子进程的 `PYTHONPATH` 必须**先有 `backtest/`**。
#:
#: 为什么：直接跑 `python tests/test_x.py` 时 `sys.path[0]` 是 `tests/`，
#: 本层的 `import trade_simulator` / `import backtest_config` **全部解析不到**。
#: （`unittest discover` 恰好把它放进了 path ⇒ discover 下能过、直接跑不能过 ——
#:  这种"换种跑法就红"的测试最容易被误当成绿。）
#:
#: ⚠️ 这里**曾经**还有第二个理由：`backtest/statistics.py` 与**标准库同名**。
#: 该文件已于 2026-10-09 改名 `panel_statistics.py`（TD-05-18）⇒ **同名问题已消除**。
_ENV = {**os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(HERE.parent), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)}


def _count(out: str) -> int:
    """从输出里取测试项数 —— `N/M passed` 或 `N/M 通过` 或 `Ran N tests`。"""
    m = re.search(r"Ran (\d+) tests", out)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*/\s*\d+\s*(?:passed|通过)", out)
    return int(m.group(1)) if m else 0


def main() -> int:
    files = sorted(p for p in HERE.glob("test_*.py") if p.name not in SKIP)
    total = failed_files = 0
    print(f"回测层全部测试：{len(files)} 个文件\n")
    for path in files:
        proc = subprocess.run([sys.executable, str(path)], capture_output=True,
                              text=True, env=_ENV, cwd=str(HERE.parent))
        out = proc.stdout + proc.stderr
        n = _count(out)
        total += n
        # ★ **扫全文**找 `[FAIL]`（不许只看最后一行 —— 有测试失败时末行仍是
        #   "26/27 passed"，不含 FAIL 字样）
        bad = "[FAIL]" in out or proc.returncode != 0
        print(f"  {'❌' if bad else '✅'} {path.name:34s} {n:>4d} 项"
              f"（退出码 {proc.returncode}）")
        if bad:
            failed_files += 1
            for line in out.splitlines():
                if "[FAIL]" in line or "Error" in line or "Traceback" in line:
                    print(f"        {line.strip()[:110]}")
    print(f"\n合计 {total} 项｜失败文件 {failed_files} / {len(files)} 个")
    if failed_files:
        print("⛔ 有失败 —— 不许当'全绿'报出去。")
    return 1 if failed_files else 0


if __name__ == "__main__":
    raise SystemExit(main())
