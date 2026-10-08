"""限频器 —— 「每窗口最多 N 次」的**主动节流**，**按桶隔离**（公共组件）。

## 为什么要有它（根因）

数据源的好几个接口族都限频（OpenD 多族都是「30 秒内最多 10 次」），而
**每个族各算各的桶**（自选 / 板块 / 服务器端筛互不影响）。

此前把这行 `time.sleep(3.1)` **抄在三个循环里** —— 抄漏一处就撞限流。
本项目已因此踩了**三次**（自选 21 组、板块 23 个、全市场快照 30+ 页），
每次都是"跑一半才炸"。

⇒ 抽成一个组件：**桶名 + 限额只声明一处**，调用前 `wait(桶名)`。
   没声明的桶直接报错（承 P2）—— "抄漏"这件事从概率问题变成不可能。

## 设计取舍

- 用**最小间隔**（`窗口 / 次数 × 安全余量`），不用令牌桶 —— 这里只需要"匀速"，
  不需要突发；令牌桶会引入不必要的状态与参数。
- 状态放**进程内模块级** —— 限频是**跨请求**的，天然属于进程（不是每次调用）。
- **只给吃限的接口用**：别对不吃限的（如 REST K线本身）套 —— 会把批量拉取拖成龟速。
- 桶名带**通道前缀**（`opend:` / `rest:`）—— 避免两通道撞名。
"""
from __future__ import annotations

import threading
import time

# 安全余量：接口说"30 秒 10 次"，按 3.0s 打点会顶在边界上 —— 留 5% 余量。
_SAFETY_FACTOR = 1.05

_MIN_INTERVAL_SECONDS: dict[str, float] = {}
_LAST_CALL_AT: dict[str, float] = {}
_LOCK = threading.Lock()

__all__ = ["declare", "wait", "declared_buckets", "reset"]


def declare(bucket: str, *, calls: int, per_seconds: float) -> None:
    """声明一个桶的限额：`per_seconds` 秒内最多 `calls` 次（**一处定义**）。

    重复声明同一个桶 → **报错**（承 P2：不静默覆盖 —— 两份限额 = 两份真相）。
    """
    if calls <= 0 or per_seconds <= 0:
        raise ValueError(
            f"限额必须为正：{bucket}（calls={calls}, per_seconds={per_seconds}）"
        )
    if bucket in _MIN_INTERVAL_SECONDS:
        raise ValueError(f"限频桶重复声明：{bucket!r}（承 P2：不静默覆盖）")
    _MIN_INTERVAL_SECONDS[bucket] = per_seconds / calls * _SAFETY_FACTOR


def wait(bucket: str) -> None:
    """调用前等一等：距上次**同桶**调用不足最小间隔就 sleep。

    未声明的桶 → **报错**（承 P2：不猜）。这逼着"限额在源里声明"，
    而不是随手 `time.sleep(3.1)` —— 那正是重复与漏改的源头。
    """
    if bucket not in _MIN_INTERVAL_SECONDS:
        raise KeyError(
            f"未声明的限频桶：{bucket!r} —— 先在源里 "
            f"`declare(bucket, calls=…, per_seconds=…)`（承 P2：不猜）"
        )
    interval = _MIN_INTERVAL_SECONDS[bucket]
    with _LOCK:
        gap = time.monotonic() - _LAST_CALL_AT.get(bucket, 0.0)
        if gap < interval:
            time.sleep(interval - gap)
        _LAST_CALL_AT[bucket] = time.monotonic()


def declared_buckets() -> dict[str, float]:
    """已声明的桶 → 最小间隔（秒）。供诊断 / 测试。"""
    return dict(_MIN_INTERVAL_SECONDS)


def reset() -> None:
    """清空所有桶与计时（**仅测试用**）。"""
    with _LOCK:
        _MIN_INTERVAL_SECONDS.clear()
        _LAST_CALL_AT.clear()
