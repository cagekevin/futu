"""M1.3 写入（Write）—— 原子写入（全成 / 全不写）。

承 K2 / X2。写多键时：先全部落临时文件，再统一 rename（**批原子**）。
一天 = 一个原子单位（承 L4）：某天写入失败 → 库里当天不残。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .keys import Key, normalize_day
from .storage import path_for, write_json_atomic


def put(root: Path, day, symbol: str | None, item: str, payload: object) -> Path:
    """写单个键。全成 / 全不写。返回落盘路径。

    `symbol=None` = 写全局项（如 `calendar`，承 keys.GLOBAL_ITEMS）。
    """
    key = Key.make(day, symbol, item)
    path = path_for(root, key)
    write_json_atomic(path, payload)
    return path


def put_many(root: Path, day,
             records: list[tuple[str | None, str, object]]) -> list[Path]:
    """写多个键，作为一个**批次**：要么全成、要么全不写。

    实现：先把每个记录写进唯一临时文件（同目录）；全部成功后统一 rename。
    任一失败 → 清理所有临时文件并抛出，目的地保持原样。
    """
    day_s = normalize_day(day)
    staged: list[tuple[Path, Path]] = []  # (tmp, final)
    try:
        for symbol, item, payload in records:
            key = Key.make(day_s, symbol, item)
            final = path_for(root, key)
            final.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(final.parent), prefix=f".{final.name}.", suffix=".tmp"
            )
            tmp = Path(tmp_name)
            _dump_json(fd, tmp, payload)
            staged.append((tmp, final))
        # 全部准备好了，统一发布
        for tmp, final in staged:
            os.replace(tmp, final)
    except BaseException:
        for tmp, _final in staged:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return [final for _tmp, final in staged]


def _dump_json(fd: int, tmp: Path, payload: object) -> None:
    import json

    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, allow_nan=True)
        f.flush()
        os.fsync(f.fileno())
