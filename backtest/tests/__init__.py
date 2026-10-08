"""**把函数式测试也收进门禁** —— 用 `unittest` 本来就有的扩展点。

## 为什么需要它（这个错在本仓库犯了**两次**）

本层的测试有两种写法：

| 写法 | 例子 | `unittest discover` 收得到吗 |
|---|---|---|
| `class X(unittest.TestCase)` | `test_causality.py` / `test_statistics.py` | ✅ |
| `def test_x() -> bool` + 自己的 runner | **其余 5 个文件** | ❌ **一个都收不到** |

于是：

```
cd backtest && python -m unittest discover -s tests
→ Ran 39 tests   ← 只有 causality(3) + statistics(36)
```

而 `tests/` 下实际有 **8 个文件、117 项** ⇒ **72 项在门禁外**。

⚠️ **仓库自己已经记过这件事**（`test_causality.py` 的 docstring）：

> 原先本文件是「函数 + `__main__`」风格 —— `python tests/test_causality.py` 能跑，
> 但门禁口径 `python -m unittest discover -s tests` **扫不到它**
> ⇒ 本仓的 V9 曾经是「**名义上的 auto**」：测试存在、门禁不执行。

**而我在新写的、最关键的 5 个文件上又犯了一次** ——
于是"backtest 97 项全绿"是**名义绿**：单独跑确实全过，但**不在闸里**。

## 做法：`load_tests` 协议（不是打补丁）

`unittest` **本来就为这种情况留了扩展点**：包级的 `load_tests()`。
它让"**门禁口径**"和"**单独跑**"两条路**收到同一批测试**。

⇒ 好处：
- 不用改 5 个文件的写法 ✓
- **以后新加的函数式测试自动进闸** ✓（不需要谁记得去注册）
- `python -m unittest discover -s tests` 从此是**完整**的 ✓
"""
from __future__ import annotations

import importlib
import inspect
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: 不是"某个功能的测试"的文件。
SKIP = {"run_all.py", "__init__.py"}

#: ★ 让 `backtest/` 进 `sys.path`。
#:
#: 为什么必须：`backtest/statistics.py` 与**标准库同名**。
#: `discover -s tests` 只把 `tests/` 放进 path ⇒ `import statistics` 命中**标准库**
#: ⇒ `ImportError: cannot import name 'SINGLE_SERIES_IN_CLUSTER'`。
#: （表现是"discover 下能过、直接跑不能过" —— 这种**换种跑法就红**的测试
#:  最容易被当成绿。）
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))


def _wrap(fn):
    """把 `def test_x() -> bool` 包成 unittest 认的"失败即抛"形式。"""
    def run() -> None:
        assert fn(), f"{fn.__name__} 返回 False（测试自己已经打印了细节）"
    run.__name__ = fn.__name__
    return run


def load_tests(loader, tests, pattern):      # noqa: ARG001
    """★ `unittest` 的包级扩展点 —— 把**两种写法**都收进来。

    ## 为什么不直接 `suite.addTests(tests)`

    包级调用时 `tests` 可能是**空的**（包自己没有测试，子模块是分别加载的）。
    实测：只靠 `addTests(tests)` 会**漏掉全部 `TestCase`**
    （`Ran 78` 而不是 `117`，而且耗时从 0.65s 掉到 0.12s —— 一眼可见少跑了一批）。

    ⇒ 这里**自己遍历模块**：`TestCase` 子类与函数式 `test_*` **都收**，
      不依赖 `discover` 怎么调我们。
    """
    suite = unittest.TestSuite()
    suite.addTests(tests)                    # 先把传进来的收下（有就收）
    for path in sorted(HERE.glob("test_*.py")):
        if path.name in SKIP:
            continue
        module = importlib.import_module(f"{__name__}.{path.stem}")
        for name, obj in sorted(vars(module).items()):
            # ⚠️ **`TestCase` 子类不要求类名以 `test_` 开头** ——
            #    unittest 的约定是"**方法名**以 test_ 开头"，类名随意。
            #    （第一版我要求了类名 ⇒ 把 `test_statistics.py` 的类全漏掉，
            #      收进 78 而不是 117 —— 又一次"看着绿其实少跑"。）
            if inspect.isclass(obj) and issubclass(obj, unittest.TestCase):
                suite.addTests(loader.loadTestsFromTestCase(obj))
            elif name.startswith("test_") and inspect.isfunction(obj):
                suite.addTest(unittest.FunctionTestCase(
                    _wrap(obj), description=f"{path.stem}::{name}"))
    return suite
