"""M1.2 存储（Storage）—— 数据物理落在哪、怎么存。

承 §6.3：一天一个目录 `store/data/<交易日>/`；一个 (标的, 数据项) 一个文件；
原子写：临时文件 + rename（承 K2）。

文件格式留白（写码时定）：这里用 **JSON**（底层数据快照 + 指标都为 Python
原生结构，可读、可自证）。大表（期权链）走 JSON 亦够用（单日单标的）。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .keys import GLOBAL_PREFIX, Key


def _day_dir(root: Path, day: str) -> Path:
    return root / day


def path_for(root: Path, key: Key) -> Path:
    """键 → 物理路径：

    - 带标的：`<root>/<交易日>/<标的>.<数据项>.json`
    - 全局项：`<root>/<交易日>/_<数据项>.json`（`_` 前缀，与任何标的天然不冲突）

    一个标的/数据项一个文件（承 §6.3）。文件名不含源名（承 X4）。
    """
    stem = key.item if key.is_global else f"{key.symbol}.{key.item}"
    if key.is_global:
        stem = f"{GLOBAL_PREFIX}{stem}"
    return _day_dir(root, key.day) / f"{stem}.json"


def write_json_atomic(path: Path, payload: object) -> None:
    """原子写：同目录临时文件 → `os.replace`（承 K2）。

    ⚠️ 临时名必须**每次唯一**（pid + 计数），不能与目的地同名派生 ——
    两个并发写会互相踩对方的临时文件，rename 后目的地损坏。
    中断（异常/kill）时临时文件被清理，目的地**要么完整、要么不存在**。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, allow_nan=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # 原子替换（同一文件系统）
    except BaseException:
        # 绝不留半个临时文件
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def read_json(path: Path) -> object | None:
    """读取 JSON。文件不存在 → 返回 None（**这不等于降级**：
    缺就是缺，由上层决定是"无"还是报错，承 K4）。"""
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── 时间轴枚举：靠目录/文件扫描，不靠索引文件（避免第二份真相，承 X1）──

def list_days(root: Path) -> list[str]:
    """有哪些交易日（升序）。只认 `YYYY-MM-DD` 目录名。"""
    if not root.exists():
        return []
    out = []
    for d in root.iterdir():
        if d.is_dir() and len(d.name) == 10 and d.name[4] == "-" and d.name[7] == "-":
            out.append(d.name)
    return sorted(out)


def list_symbols(root: Path, day: str) -> list[str]:
    """某天有哪些标的（升序）。从 `<标的>.<数据项>.json` 文件名解析。

    **不含全局项**（`_<数据项>.json`）—— 它们没有标的（见 `list_globals`）。
    """
    d = _day_dir(root, day)
    if not d.exists():
        return []
    syms = set()
    for p in d.glob("*.json"):
        if p.name.startswith(GLOBAL_PREFIX):
            continue  # 全局项，不是标的
        parts = p.name.split(".")
        if len(parts) >= 3:  # SYM.ITEM.json（SYM 自身可能含 '.'，如 BRK.B）
            syms.add(".".join(parts[:-2]))
    return sorted(syms)


def list_items(root: Path, day: str, symbol: str) -> list[str]:
    """某天某标的有哪些数据项（升序）。"""
    d = _day_dir(root, day)
    if not d.exists():
        return []
    prefix = f"{symbol.upper()}."
    items = []
    for p in d.glob("*.json"):
        if p.name.startswith(GLOBAL_PREFIX):
            continue
        parts = p.name.split(".")
        if len(parts) >= 3:
            sym = ".".join(parts[:-2])
            if sym == symbol.upper():
                items.append(parts[-2])
    return sorted(items)


def list_globals(root: Path, day: str) -> list[str]:
    """某天有哪些**全局数据项**（无标的，如 `calendar`）。文件名 `_<数据项>.json`。"""
    d = _day_dir(root, day)
    if not d.exists():
        return []
    out = []
    for p in d.glob(f"{GLOBAL_PREFIX}*.json"):
        stem = p.stem  # `_calendar`
        out.append(stem[len(GLOBAL_PREFIX):])
    return sorted(out)
