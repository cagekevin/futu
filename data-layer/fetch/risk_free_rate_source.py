"""无风险利率取数（SOFR，NY Fed 公开接口）。

**放在 fetch/ 而非 engine/**：它要联网取数（IO），而 engine/ 必须零 IO（承 G1）。
engine 通过参数 `r` 接收利率（承 G3：时刻显式传入）。

承 P1（回退必须显形）：网络失败 → **不静默用常量**，而是返回
`RateResult(value, source, is_fallback)`，让编排层决定是否报。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from config import RISK_FREE_RATE_FALLBACK, SOFR_URL

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateResult:
    value: float
    source: str          # "sofr" | "fallback"
    is_fallback: bool    # True = 用了常量兜底（必须显形，承 P1）
    detail: str = ""


def fetch_rate(*, timeout: int = 15,
               allow_fallback: bool = False) -> RateResult:
    """取隔夜 SOFR。

    `allow_fallback=False`（默认）：失败 → 抛错（承 P6：不降级）。
    `allow_fallback=True`：失败 → 返回常量兜底，但 `is_fallback=True`（承 P1）。
    """
    try:
        resp = requests.get(SOFR_URL, timeout=timeout)
        resp.raise_for_status()
        item = resp.json()["refRates"][0]
        rate = float(item["percentRate"]) / 100.0
        if not (0.0 <= rate <= 0.20):
            raise ValueError(f"SOFR 超出合理区间：{item.get('percentRate')}")
        return RateResult(rate, "sofr", False, item.get("effectiveDate", ""))
    except Exception as e:  # noqa: BLE001
        if not allow_fallback:
            raise RuntimeError(f"取无风险利率失败（不降级，承 P6）：{e}") from e
        log.warning("SOFR 不可用，回退到常量 r=%.4f（已显形，承 P1）",
                    RISK_FREE_RATE_FALLBACK)
        return RateResult(RISK_FREE_RATE_FALLBACK, "fallback", True, str(e))
