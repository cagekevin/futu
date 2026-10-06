"""本地日 K 的读取 —— `data/kline/daily/*.json`（由 `fetch/rest/kline.mjs` 拉取，这里只读）。

**CD / 顾比 / 自选股 RPS 三个脚本都从这里读** —— 只此一份，别各写一个 `load_all`。

**这批文件是「自选股」** —— 全市场那套走 `fetch/opend/universe.py` 的快照，
**不读 K 线**（见 `market_scan.py`）。
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "kline" / "daily"

# **唯一标准：能不能交易。**
# 排除的只有这三类 —— 买不了的：
#   IDX     指数（标普500、纳指…）
#   PLATE   板块指数（富途自己的，如 US.LIST2016）
#   BOND    我们只有 `BD.US10Y`，那是**国债收益率**，不是债券
# 其余全留：STOCK / ETF / FUTURE（期货主连）/ CRYPTO / FOREX —— 都能下单。
TRADABLE_TYPES = ("STOCK", "ETF", "FUTURE", "CRYPTO", "FOREX")

__all__ = ["DATA_DIR", "TRADABLE_TYPES", "load_all", "load_one"]


def load_all(types=TRADABLE_TYPES, *, min_bars=0, limit=None):
    """读全部本地日 K，返回 `list[dict]`。

    `types`      默认**只留能交易的**（见 `TRADABLE_TYPES`）；传 `None` 表示不过滤
    `min_bars`   历史太短的跳过（CD 要 60 根、顾比 61、Mansfield 260 —— 各调用方自己传）
    `limit`      够这么多就停（摸口径时用）
    """
    out = []
    for path in sorted(glob.glob(str(DATA_DIR / "*.json"))):
        if os.path.basename(path) == "_manifest.json":
            continue
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if types and doc.get("stock_type") not in types:
            continue
        if min_bars and len(doc.get("bars") or []) < min_bars:
            continue
        out.append(doc)
        if limit and len(out) >= limit:
            break
    return out


def load_one(symbol):
    """单独读一只，**不受类型过滤**。

    基准是指数（`US..SPX` 这种 `IDX`）—— 它**买不了所以不在 `TRADABLE_TYPES` 里**，
    但算 RS 线要拿它当参照，所以走这个函数单独读。
    """
    path = DATA_DIR / f"{symbol}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
