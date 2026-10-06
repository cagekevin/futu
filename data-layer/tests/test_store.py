"""S2 验证 —— 对照 PRD M1 的可验证标准 + 全局约束。

跑法：.venv/bin/python -m pytest tests/test_store.py -q
或（无 pytest）：.venv/bin/python tests/test_store.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store import axis  # noqa: E402
from store.keys import (  # noqa: E402
    Key, StoreError, UnregisteredItem, normalize_day, normalize_symbol,
    normalize_item, KNOWN_ITEMS,
)


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="td-test-"))


# ── K1：键 = 三元组，规范化，键里无源名 ──────────────────────────────────

def test_k1_day_normalization():
    assert normalize_day("2026-10-06") == "2026-10-06"
    for bad in ("20261006", "2026/10/06", "26-10-06", "2026-13-01"):
        try:
            normalize_day(bad)
        except StoreError:
            pass
        else:
            raise AssertionError(f"应拒绝不规范交易日：{bad}")


def test_k1_symbol_normalization():
    assert normalize_symbol("aapl") == "AAPL"
    assert normalize_symbol("SPX") == "SPX"
    for bad in ("US.AAPL", "US..SPX", "cboe_spx", ""):
        try:
            normalize_symbol(bad)
        except StoreError:
            pass
        else:
            raise AssertionError(f"应拒绝带源名/前缀的标的：{bad!r}")


def test_k1_item_must_be_registered():
    assert normalize_item("net_gex") == "net_gex"
    try:
        normalize_item("gex_flip")  # 未注册（应为 zero_gamma）
    except UnregisteredItem:
        pass
    else:
        raise AssertionError("未注册数据项应报错（承 K3）")


def test_k1_same_key_one_representation():
    """写两次同一天同标的 → 只有一个键 / 一个文件。"""
    root = _tmp()
    axis.put("2026-10-06", "SPX", "net_gex", 1.0, root=root)
    axis.put("2026-10-06", "SPX", "net_gex", 2.0, root=root)
    files = list((root / "2026-10-06").glob("*.json"))
    assert len(files) == 1, files
    assert axis.get("2026-10-06", "SPX", "net_gex", root=root) == 2.0


# ── K2：写入原子（中途崩不留半截文件）────────────────────────────────────

def test_k2_no_partial_file_on_failure():
    """序列化中途抛错 → 目的地不存在、无残留临时文件。"""
    root = _tmp()

    class Boom:
        pass  # json.dump 无法序列化 → 抛 TypeError

    try:
        axis.put("2026-10-06", "SPX", "net_gex", Boom(), root=root)
    except TypeError:
        pass
    else:
        raise AssertionError("应抛出")

    d = root / "2026-10-06"
    if d.exists():
        leftovers = [p.name for p in d.iterdir()]
        assert leftovers == [], f"不应留下任何文件：{leftovers}"


def test_k2_batch_atomic():
    """批次里有一条坏 → 全不写（承 X2/L4：一天一个原子单位）。"""
    root = _tmp()
    axis.put("2026-10-05", "SPX", "net_gex", 1.0, root=root)

    class Boom:
        pass

    try:
        axis.put_many("2026-10-06", [
            ("SPX", "net_gex", 5.0),
            ("SPX", "spot", Boom()),
        ], root=root)
    except TypeError:
        pass
    else:
        raise AssertionError("应抛出")

    # 坏批次不应落地任何 10-06 的数据
    assert not (root / "2026-10-06").exists() or not list((root / "2026-10-06").glob("*.json"))


# ── K3：数据项是受控清单 ────────────────────────────────────────────────

def test_k3_write_unregistered_item_errors():
    root = _tmp()
    try:
        axis.put("2026-10-06", "SPX", "not_registered", 1.0, root=root)
    except UnregisteredItem:
        pass
    else:
        raise AssertionError("用未注册名写 → 应报错")


# ── K4：读不降级（缺就是缺）─────────────────────────────────────────────

def test_k4_missing_returns_missing_not_neighbor():
    root = _tmp()
    axis.put("2026-10-05", "SPX", "net_gex", 111.0, root=root)
    # 10-06 不存在：绝不能返回 10-05 的值
    try:
        axis.get("2026-10-06", "SPX", "net_gex", root=root)
    except axis.Missing:
        pass
    else:
        raise AssertionError("缺失应抛 Missing，不得回退到邻近日期（承 K4）")
    # 显式 default 才允许
    assert axis.get("2026-10-06", "SPX", "net_gex", default=None, root=root) is None


def test_k4_series_does_not_backfill():
    root = _tmp()
    axis.put("2026-10-05", "SPX", "net_gex", 1.0, root=root)
    axis.put("2026-10-07", "SPX", "net_gex", 3.0, root=root)  # 10-06 缺
    s = axis.series("SPX", "net_gex", root=root)
    assert [d for d, _ in s] == ["2026-10-05", "2026-10-07"], s
    assert len(s) == 2  # 不补 10-06


# ── K5：M1 零依赖 ───────────────────────────────────────────────────────

def test_k5_no_upstream_imports():
    root = Path(__file__).resolve().parent.parent / "store"
    bad = []
    for p in root.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for mod in ("fetch", "engine", "provide"):
            if f"import {mod}" in text or f"from {mod}" in text:
                bad.append((p.name, mod))
    assert not bad, f"store/ 不应依赖上层模块：{bad}"


# ── 时间轴：days/symbols/items ──────────────────────────────────────────

def test_axis_enumeration():
    root = _tmp()
    axis.put("2026-10-05", "SPX", "net_gex", 1.0, root=root)
    axis.put("2026-10-06", "SPX", "net_gex", 2.0, root=root)
    axis.put("2026-10-06", "SPX", "spot", 7000.0, root=root)
    axis.put("2026-10-06", "QQQ", "spot", 500.0, root=root)

    assert axis.days(root=root) == ["2026-10-05", "2026-10-06"]
    assert axis.symbols("2026-10-06", root=root) == ["QQQ", "SPX"]
    assert axis.items("2026-10-06", "SPX", root=root) == ["net_gex", "spot"]


def test_global_item_calendar():
    """全局项（无标的，如 calendar）：可写可读，且不出现在标的列表里。"""
    root = _tmp()
    axis.put("2026-10-06", None, "calendar",
             {"market": "US", "is_trading_day": True}, root=root)
    # 读回
    got = axis.get("2026-10-06", None, "calendar", root=root)
    assert got["is_trading_day"] is True
    # 文件是 `_calendar.json`
    assert (root / "2026-10-06" / "_calendar.json").exists()
    # 不出现在"标的"列表（它不是标的）
    axis.put("2026-10-06", "SPX", "spot", 7000.0, root=root)
    assert axis.symbols("2026-10-06", root=root) == ["SPX"]
    # 出现在"全局项"列表
    assert axis.global_items("2026-10-06", root=root) == ["calendar"]


def test_global_item_pairing_enforced():
    """配对约束：全局项不带标的、非全局项必须带标的（承 P2）。"""
    root = _tmp()
    # 全局项却带了标的 → 报错
    try:
        axis.put("2026-10-06", "SPX", "calendar", {}, root=root)
    except StoreError:
        pass
    else:
        raise AssertionError("calendar 带标的应报错")
    # 非全局项却不带标的 → 报错
    try:
        axis.put("2026-10-06", None, "net_gex", 1.0, root=root)
    except StoreError:
        pass
    else:
        raise AssertionError("net_gex 无标的应报错")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
