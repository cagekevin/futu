"""粗筛产物的统一落盘 —— **一天一个文件夹**：`data/scan/<YYYY-MM-DD>/`。

**为什么这样放**：

* **采集和分析分开** —— 采集要联网 / 要算，跑一遍落盘；分析只读这个文件夹，
  **不重新跑命令**。想换个筛选口径反复试，不用重新联网。
* **可回看** —— 想知道 10/03 是什么情况，直接看 `data/scan/2026-10-03/`，
  不会被当天的数据覆盖。
* **一天的产物在一处** —— 分析不用满仓库找文件。

日期用**市场所在地的今天**（`market_today`）—— 香港白天跑美股时，美股还在当天，
用本机日期会把「今天」记错一天。

产物（都是 JSON，`_manifest.json` 由 `save()` 自动维护）：

    universe.json    V2 全市场快照（9000+ → 服务端过滤后 ~3000）
    rps.json         个股 RPS 榜（全市场口径）
    industry.json    行业集体行为
    cd.json          CD 底背离候选
    guppy.json       顾比突破候选
    picks.json       综合分析
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fetch.opend.futu_data import DEFAULT_ROOT

SCAN_DIR_NAME = "scan"
MANIFEST = "_manifest.json"

# 产物里表示「多少条」的键，按顺序找第一个
_COUNT_KEYS = ("count", "hits", "rows", "items", "stats", "signals", "strong")

__all__ = ["market_today", "day_dir", "save", "load", "manifest", "has"]


def market_today(market="US") -> str:
    """市场所在地的今天（`YYYY-MM-DD`）—— 用 futu_algo 的时区表，别用本机日期。"""
    from futu_algo.market.instrument import MARKETS

    tz = MARKETS[market].tz if market in MARKETS else "UTC"
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def day_dir(date=None, market="US", root=None) -> Path:
    """当天的粗筛文件夹（不保证存在）。"""
    return (Path(root) if root else DEFAULT_ROOT) / SCAN_DIR_NAME / (date or market_today(market))


def _count(payload):
    for key in _COUNT_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, int):
            return value
    return None


def save(name, payload, *, date=None, market="US", root=None, note=None, params=None) -> Path:
    """落盘一个产物，并把它记进 `_manifest.json`。

    `payload` 会补上 `as_of`（哪一天）和 `saved_at`（什么时候跑的）；
    manifest 里记下条数 / 参数 / 备注 —— 回看时一眼知道当天跑了什么。
    """
    d = date or market_today(market)
    folder = day_dir(d, market, root)
    folder.mkdir(parents=True, exist_ok=True)

    doc = dict(payload)
    doc["as_of"] = d  # 文件夹日期说了算 —— 补跑历史日期时不能被 payload 里的「今天」覆盖
    doc["saved_at"] = datetime.now().isoformat(timespec="seconds")
    path = folder / f"{name}.json"
    # default=str：服务器端选股的行里有 Decimal / Timestamp 之类，别让落盘炸掉
    path.write_text(json.dumps(doc, ensure_ascii=False, default=str), encoding="utf-8")

    man_path = folder / MANIFEST
    man = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {
        "as_of": d, "market": market, "steps": {},
    }
    man["steps"][name] = {
        "saved_at": doc["saved_at"],
        "file": path.name,
        "bytes": path.stat().st_size,
        "count": _count(doc),
        "params": params if params is not None else doc.get("params"),
        "note": note,
    }
    man_path.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load(name, *, date=None, market="US", root=None):
    """读一个产物；**不存在返回 None** —— 分析阶段要能容忍某几步没跑。"""
    path = day_dir(date, market, root) / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def has(name, *, date=None, market="US", root=None) -> bool:
    return (day_dir(date, market, root) / f"{name}.json").exists()


def manifest(date=None, market="US", root=None):
    path = day_dir(date, market, root) / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
