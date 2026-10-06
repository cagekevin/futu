"""富途 REST 源适配器（M2.1）—— **无额度**拉全量 K线。

为什么需要它（重要，实测 + 端方 README 确认）：
- **OpenD 通道** 拉历史 K线吃「300 标的 / 7 天滚动」额度 —— 全市场几千只一次就撞墙。
- **REST 通道**（`webapi.futunn.com`，Ed25519 签名）**无额度**，是**批量历史数据**的正道。

端方 README 第 196 行：「① 数据 全走 REST —— 不需要 OpenD、不消耗任何额度。」
本模块**逐参数复刻**其 `fetch/rest/lib/futu-client.mjs` + `fetch/rest/kline.mjs`
（不自己发明任何数值，见每个常量旁的出处注释）。

⚠️ 与 OpenD 的关键差别（实测，踩过）：
- REST 的 `history-kline` 用 **`end` + `num`** 从今**往前**翻页 → 能拿**最新**数据；
- OpenD 的 `request_history_kline` **忽略 end**，只有 `start` 有效 → 拿最新得给足 start。

承 F1（源名不外泄）/ F4（失败必报）。凭据在 `~/.config/futu/`（仓库外，chmod 600）。
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from ...fetch_types import FetchError, Request
from trading_time import to_unix_seconds

# ── 常量：全部来自端方源码，不自己发明 ──────────────────────────────────
_BASE = "https://webapi.futunn.com"                    # futu-client.mjs:21
_CFG_DIR = Path(os.getenv("FUTU_REST_CFG_DIR", Path.home() / ".config" / "futu"))
_KEY_PEM = _CFG_DIR / "private_key.pem"                # futu-client.mjs:24
_APPKEY = _CFG_DIR / "appkey"                          # futu-client.mjs:25

_RETRIES = 4                                           # futu-client.mjs:49
_LIMIT_BACKOFF_MS = 3000                               # futu-client.mjs:80 (2**attempt*3000)
_LIMIT_EXTRA_WAIT_MS = 1000                            # futu-client.mjs:82 (+1000)
# ⚠️ 端方 REST 客户端**不设请求超时**（裸 fetch）—— 本模块同样不设（None）。
_NUM_MAX = 370                                         # kline.mjs:102 (num: 370)
_MAX_PAGES = 60                                        # kline.mjs:63 (MAX_PAGES)
_GAP_MS = 700                                          # kline.mjs:61 (GAP_MS 默认 700)

# REST history-kline 的 ktype 编号 —— **实测扫 0..30 得出的有效集合**（不是猜的）：
#   有效：1 2 3 4 5 6 7 8 9 10 11 14 15 26 29
#   已知映射：1=1分 6=5分 7=15分 8=30分 9=60分 14=120分 2=日 3=周 4=月 5=季
#   10=3分 11=10分（time_key 间隔实测推得）
# ⚠️⚠️ REST 的编号与 OpenD 的 `KLType` 名字是**两套不同映射**，别混：
#   - REST：数字，且**没有 240分**（ktype=16 返回 invalid）；
#   - OpenD：字符串 `K_240M`，**有 4H**。
# ⇒ **4H(240分) 必须走 OpenD**；REST 只用于日线及以上的长历史。
# autype: 0=不复权 1=前复权 2=后复权。
#
# ★ 复权口径（2026-10-06 定，承 P1）：**K线统一存不复权（raw）**。
#   原 REST 用 1（前复权）而 OpenD 用 None（不复权）—— **同标的两通道价格不一致**，
#   是 P1 级错。现统一为 **0（不复权）**：原价客观、不随除权回溯变。
#   复权因子单独存（数据项 `adjust_factor`，见 fetch/futu.py::fetch_rehab）。
KTYPE = {
    "K_1M": 1, "K_3M": 10, "K_5M": 6, "K_10M": 11, "K_15M": 7,
    "K_30M": 8, "K_60M": 9, "K_120M": 14,
    "K_DAY": 2, "K_WEEK": 3, "K_MON": 4, "K_QUARTER": 5,
}
# REST 支持的日内编号（去重/排序键用 time_key）。
_INTRADAY_KTYPES = frozenset({1, 6, 7, 8, 9, 10, 11, 14, 15, 26, 29})
_AUTYPE_NONE = 0                                       # ★ 不复权（raw）—— 统一口径，见上

# K线通用字段（含 time_key —— 分钟线去重/排序靠它，kline.mjs:24）。
KLINE_FIELDS = ("time_key", "date", "open", "high", "low", "close", "volume")


def _ymd_to_iso(d) -> str:
    """`YYYYMMDD`（int/str）→ `YYYY-MM-DD`。"""
    s = str(d)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _bar_key(b: dict, intraday: bool):
    """去重/排序键：分钟线用 `time_key`，日线及以上用 `date`（kline.mjs:22-25）。"""
    return b["time_key"] if intraday else b["date"]


def _bar_row(b: dict) -> dict:
    """REST K线一条 → 归一化 dict。

    `time_key` **统一为 Unix 秒**（承"时区统一"）：REST 原生是**毫秒纪元数**，
    经 `trading_time.to_unix_seconds` 收敛成秒，与 OpenD 通道一致（否则跨通道不可比）。
    """
    return {
        "time_key": to_unix_seconds(b["time_key"], unit="ms"),  # 源是毫秒 → 显式声明
        "date": _ymd_to_iso(b["date"]),
        "open": float(b["open"]), "high": float(b["high"]),
        "low": float(b["low"]), "close": float(b["close"]),
        "volume": float(b.get("volume") or 0.0),
    }


def _load_creds():
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
    except ImportError as e:  # pragma: no cover
        raise FetchError("REST 源不可用：需要 cryptography（pip install cryptography）") from e
    if not _KEY_PEM.exists() or not _APPKEY.exists():
        raise FetchError(
            f"REST 源不可用：缺凭据（{_KEY_PEM} / {_APPKEY}）。"
            f"端方约定：Ed25519 私钥 + appkey，chmod 600（承 F4）"
        )
    key = load_pem_private_key(_KEY_PEM.read_bytes(), password=None)
    appkey = _APPKEY.read_text(encoding="utf-8").strip()
    return key, appkey


@dataclass
class Rows:
    """通用行式出口（K线）。"""

    symbol: str
    fetched_at: datetime
    rows: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


class FutuRestSource:
    """富途 REST 源（无额度）。`name` 仅内部使用（承 F1）。"""

    name = "futu-rest"

    def __init__(self) -> None:
        self._key = None
        self._appkey = None
        self._clock_offset_ms = 0
        self._session = requests.Session()

    # ── 签名 / 请求（复刻 futu-client.mjs）──────────────────────────────
    def _creds(self):
        if self._key is None:
            self._key, self._appkey = _load_creds()
        return self._key, self._appkey

    def sync_time(self) -> int:
        """服务端时间校准（富途要求偏移 ≤ 5s，否则 -12006）。无需鉴权。

        复刻 futu-client.mjs:31 syncTime：offset = server_ms - round((t0+t1)/2)。
        """
        t0 = int(time.time() * 1000)
        try:
            r = self._session.get(f"{_BASE}/api/v1.0/server-time")
            r.raise_for_status()
            server_ms = int(r.json()["server_time_ms"])
        except Exception as e:  # noqa: BLE001
            raise FetchError(f"REST 源：时钟校准失败（{e}）") from e
        t1 = int(time.time() * 1000)
        self._clock_offset_ms = server_ms - round((t0 + t1) / 2)
        return self._clock_offset_ms

    def _build_sign_string(self, ts_ms: str, method: str, path: str,
                           query: str, body: str) -> str:
        """签名原文 5 段（futu-client.mjs:40）：ts\\nMETHOD\\npath\\nquery\\nbody_sha256。"""
        body_hash = hashlib.sha256(body.encode()).hexdigest() if body else ""
        return f"{ts_ms}\n{method}\n{path}\n{query}\n{body_hash}"

    def _signed_headers(self, method: str, path: str, query: str, body: str = "") -> dict:
        key, appkey = self._creds()
        if self._clock_offset_ms == 0:
            self.sync_time()
        ts = str(int(time.time() * 1000) + self._clock_offset_ms)
        nonce = os.urandom(8).hex()                       # randomBytes(8).toString('hex')
        sign_str = self._build_sign_string(ts, method, path, query, body)
        sig = base64.b64encode(key.sign(sign_str.encode())).decode()
        return {
            "X-Api-Key": appkey,
            "X-Timestamp": ts,
            "X-Nonce": nonce,
            "Authorization": sig,                          # 不加 Bearer 前缀
        }

    def _get(self, path: str, params: dict) -> dict:
        """GET（含限流退避）。query 与签名原文共用同一字符串（futu-client.mjs:49-88）。"""
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        url = f"{_BASE}{path}" + (f"?{query}" if query else "")
        for attempt in range(_RETRIES + 1):
            headers = self._signed_headers("GET", path, query)
            r = self._session.get(url, headers=headers)    # ⚠️ 不设超时（对齐端方）
            try:
                js = r.json()
            except ValueError as e:
                raise FetchError(f"REST 源：响应非 JSON（HTTP {r.status_code}）") from e
            ret_code = js.get("ret_code")
            # 限流两种表现：HTTP 429，或 200 + ret_code -11（futu-client.mjs:74-84）
            rate_limited = r.status_code == 429 or ret_code == -11
            if rate_limited and attempt < _RETRIES:
                from_header = float(r.headers.get("retry-after") or 0) * 1000
                msg = (js.get("error") or {}).get("message") or ""
                import re
                m = re.search(r"retry after (\d+)", msg)
                wait = from_header or (float(m.group(1)) * 1000 if m else 2 ** attempt * _LIMIT_BACKOFF_MS)
                time.sleep((wait + _LIMIT_EXTRA_WAIT_MS) / 1000)
                continue
            if ret_code not in (0, None):
                raise FetchError(f"REST 源：返回码 {ret_code} — {js.get('ret_msg')}")
            return js
        raise FetchError("REST 源：重试耗尽")

    # ── 历史 K线（无额度，复刻 kline.mjs::pull）─────────────────────────
    def fetch_kline(self, req: Request, *, ktype: str = "K_DAY",
                    years: int = 3, months: int | None = None,
                    autype: int = _AUTYPE_NONE) -> Rows:
        """取历史 K线 —— `end` + `num` 从今**往前**翻页（kline.mjs:92-136）。

        日线（ktype=2）与分钟线（6/7/8/9/14）的**三个关键差异**（kline.mjs:24-27）：
        1. 去重/排序键：日线用 `date`，**分钟线用 `time_key`**（否则一天被压成一根）；
        2. 翻页 `end`：日线取「本页最早日 - 1 天」；**分钟线取「本页最早日」（不减 1 天）**
           —— 否则同日后半段会被跳过；
        3. 防死循环：`earliest.time_key >= prevEarliest` 即停。

        窗口：日线用 `years`，分钟线用 `months`（kline.mjs 的 winArg/winDefault）。
        """
        kt = KTYPE.get(ktype, int(ktype) if str(ktype).isdigit() else 2)
        intraday = kt in _INTRADAY_KTYPES
        end = req.as_of or date.today().isoformat()
        # 窗口：分钟线默认 3 个月，日线默认 years 年（periods.mjs:9-11）
        if intraday:
            m = months if months is not None else 3
            since = (date.fromisoformat(end) - timedelta(days=int(m * 30.44))).isoformat()
        else:
            since = (date.fromisoformat(end) - timedelta(days=int(years * 365.25))).isoformat()

        bars: dict[Any, dict] = {}
        pages = 0
        prev_earliest = None
        for _ in range(_MAX_PAGES):
            js = self._get(
                f"/api/v1.0/quote/{self._code(req.symbol)}/history-kline",
                {"end": end, "ktype": str(kt), "autype": str(autype),
                 "num": str(_NUM_MAX)},
            )
            lst = (js.get("data") or {}).get("kline_list") or []
            if not lst:
                break
            for b in lst:
                # 丢「半成品」bar（缺 OHLC）—— kline.mjs:112-115
                if b.get("open") is None or b.get("close") is None:
                    continue
                bars[_bar_key(b, intraday)] = _bar_row(b)
            pages += 1
            earliest = lst[0]
            earliest_iso = _ymd_to_iso(earliest["date"])
            if earliest_iso <= since:                     # 窗口够了（日线与分钟线通用）
                break
            if intraday:
                # 分钟线：end 用本页最早那根的**日期**（不减 1 天），靠 time_key 去重
                if prev_earliest is not None and earliest["time_key"] >= prev_earliest:
                    break  # 无进展，防死循环（kline.mjs:125 / depth.mjs:43）
                prev_earliest = earliest["time_key"]
                end = earliest_iso
            else:
                # 日线：end = 本页最早日 - 1 天（kline.mjs:123）
                if earliest_iso == prev_earliest:
                    break
                prev_earliest = earliest_iso
                end = (date.fromisoformat(earliest_iso) - timedelta(days=1)).isoformat()
            time.sleep(_GAP_MS / 1000)                     # kline.mjs:128 (GAP_MS)

        rows = [bars[k] for k in sorted(bars)]
        if not rows:
            raise FetchError(f"REST 源：K线为空（{req.symbol}）")
        return Rows(symbol=req.symbol.upper(), fetched_at=datetime.now(),
                    rows=rows, extra={"ktype": kt, "autype": autype,
                                      "intraday": intraday, "pages": pages})

    # ── 交易日历（全局数据，无额度）────────────────────────────────────
    def fetch_trading_days(self, market: str, start: str, end: str) -> Rows:
        """某市场 [start, end] 的**开市日清单**（端方 `kline.mjs:271` 同款）。

        返回**只含开市日**（非交易日不在列表里 —— 这是接口原生形态，不补）。
        端点：`GET /api/v1.0/quote/trading-days`。
        ⚠️ 实测单次上限约 **4000 条**（超了从末端截断）→ 超长历史需分段。
        归一化每行：`{market, trade_date_type, trade_second}`（接口原样）。
        """
        js = self._get("/api/v1.0/quote/trading-days",
                       {"market": market, "start": start, "end": end})
        days = (js.get("data") or {}).get("trading_days") or []
        rows = [{
            "day": str(d["time"]),                       # YYYY-MM-DD
            "market": market,
            "trade_date_type": d.get("trade_date_type"),  # WHOLE / PRE_AFTER…
            "trade_second": d.get("trade_second"),
        } for d in days]
        return Rows(symbol=f"CALENDAR:{market}", fetched_at=datetime.now(),
                    rows=rows, extra={"market": market, "start": start,
                                      "end": end, "count": len(rows)})

    @staticmethod
    def _code(symbol: str) -> str:
        """内部纯代码 → REST 代码，带市场前缀。**仅本模块内使用**（承 F1）。"""
        s = symbol.upper()
        if "." in s:
            return s
        return f"US.{s}"


def make() -> FutuRestSource:
    return FutuRestSource()


from ... import source_registry  # noqa: E402

source_registry.register("futu-rest", make)
