"""限频器测试（承 P2 + 先红后绿）：间隔真的生效、未声明的桶真的报错。

⚠️ 用 `t:*` 前缀的**测试桶**，并在开头 `reset()` —— 别碰源里声明的真桶。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetch import rate_limit  # noqa: E402


def test_undeclared_bucket_fails_loud() -> bool:
    """承 P2：没声明的桶 → 报错（不静默放行）。"""
    rate_limit.reset()
    try:
        rate_limit.wait("t:nope")
    except KeyError:
        print("[PASS] test_undeclared_bucket_fails_loud（报错而非放行）")
        return True
    print("[FAIL] test_undeclared_bucket_fails_loud（应报错）")
    return False


def test_duplicate_declare_fails_loud() -> bool:
    """承 P2：同一桶重复声明 → 报错（两份限额 = 两份真相）。"""
    rate_limit.reset()
    rate_limit.declare("t:dup", calls=10, per_seconds=1)
    try:
        rate_limit.declare("t:dup", calls=10, per_seconds=1)
    except ValueError:
        print("[PASS] test_duplicate_declare_fails_loud（报错而非覆盖）")
        return True
    print("[FAIL] test_duplicate_declare_fails_loud（应报错）")
    return False


def test_wait_enforces_interval() -> bool:
    """3 次调用应至少花掉 2 个间隔（第一次不等）。"""
    rate_limit.reset()
    rate_limit.declare("t:slow", calls=10, per_seconds=0.3)      # ≈31.5ms/次
    t0 = time.monotonic()
    for _ in range(3):
        rate_limit.wait("t:slow")
    elapsed = time.monotonic() - t0
    ok = elapsed >= 0.055                                        # 2 个间隔 ≈ 63ms
    print(f"{'[PASS]' if ok else '[FAIL]'} test_wait_enforces_interval"
          f"（{elapsed*1000:.0f}ms ≥ 55ms）")
    return ok


def test_buckets_are_isolated() -> bool:
    """不同桶互不影响（一个桶的等待不拖慢另一个桶的首次调用）。"""
    rate_limit.reset()
    rate_limit.declare("t:a", calls=1, per_seconds=5)
    rate_limit.declare("t:b", calls=1, per_seconds=5)
    rate_limit.wait("t:a")
    t0 = time.monotonic()
    rate_limit.wait("t:b")                                       # 不同桶 → 不该等
    elapsed = time.monotonic() - t0
    ok = elapsed < 0.5
    print(f"{'[PASS]' if ok else '[FAIL]'} test_buckets_are_isolated"
          f"（{elapsed*1000:.0f}ms < 500ms）")
    return ok


if __name__ == "__main__":
    results = [
        test_undeclared_bucket_fails_loud(),
        test_duplicate_declare_fails_loud(),
        test_wait_enforces_interval(),
        test_buckets_are_isolated(),
    ]
    print("-" * 50)
    print(f"  {sum(results)}/{len(results)} 通过")
    raise SystemExit(0 if all(results) else 1)
