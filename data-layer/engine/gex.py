"""M3.2 指标（indicators）—— GEX / flip / walls 等纯函数。

承 G1（零 IO）/ G2（只算不存）/ G3（时刻显式传入）/ G4（输出自带来源）/
G5（可独立重算）。

GEX 约定（SpotGamma "naive"）：
    GEX($ per 1% move) = gamma × OI × multiplier × spot² × 0.01
    call 计正、put 计负（做市商视角：long call / short put）。

入口是**纯数据**：`compute(chain, spot, *, as_of, r, ...)`。
`chain` 是归一化后的合约列表（`fetch.ChainResult.rows` 或库里读回的 chain），
每项含 `strike / cp / iv / open_interest / volume / expiry / delta / gamma`。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np

from config import CONTRACT_MULTIPLIER, MIN_T_YEARS, YEAR_SECONDS, ZG_RANGE, ZG_STEPS
from trading_time import to_unix_seconds
from . import greeks


# ── 纯计算辅助 ───────────────────────────────────────────────────────────

def _secs_to_expiry(expiries: list[str], now_et: datetime) -> np.ndarray:
    """到 16:00 ET 的秒数（负 = 已到期，应排除）。"""
    out = []
    for e in expiries:
        d = date.fromisoformat(e)
        exp_dt = datetime.combine(d, datetime.min.time()).replace(hour=16)
        out.append((exp_dt - now_et.replace(tzinfo=None)).total_seconds())
    return np.asarray(out, dtype=float)


def _enrich(chain: list[dict], spot: float, now_et: datetime, r: float) -> dict[str, np.ndarray]:
    """把原始链算成向量化的工作集：t / gamma / delta / gex / dex。

    P1：IV 为 0 或缺的合约 → 交给调用方决定显形（这里不静默填充，改用 0 gamma，
    但在 `_flags` 里记录退化数量）。
    """
    n = len(chain)
    strike = np.array([c["strike"] for c in chain], dtype=float)
    iv = np.array([c.get("iv", 0.0) or 0.0 for c in chain], dtype=float)
    oi = np.array([c.get("open_interest", 0.0) or 0.0 for c in chain], dtype=float)
    vol = np.array([c.get("volume", 0.0) or 0.0 for c in chain], dtype=float)
    is_call = np.array([str(c["cp"]).upper().startswith("C") for c in chain], dtype=bool)

    secs = _secs_to_expiry([c["expiry"] for c in chain], now_et)
    keep = secs > 0  # 排除已到期合约

    t = np.maximum(np.where(keep, secs, MIN_T_YEARS * YEAR_SECONDS),
                   MIN_T_YEARS * YEAR_SECONDS) / YEAR_SECONDS
    valid_iv = iv > 1e-4

    # IV 无效 → 退化到源提供的 gamma/delta（若有），并标记。
    src_gamma = np.array([c.get("gamma", 0.0) or 0.0 for c in chain], dtype=float)
    src_delta = np.array([c.get("delta", 0.0) or 0.0 for c in chain], dtype=float)

    g_bs = greeks.gamma(spot, strike, t, r, np.where(valid_iv, iv, 1.0))
    g = np.where(valid_iv, g_bs, src_gamma)
    d_call = greeks.call_delta(spot, strike, t, r, np.where(valid_iv, iv, 1.0))
    d = np.where(valid_iv, np.where(is_call, d_call, d_call - 1.0), src_delta)

    sign = np.where(is_call, 1.0, -1.0)
    gex = sign * g * oi * CONTRACT_MULTIPLIER * spot**2 * 0.01
    dex = -1.0 * d * oi * CONTRACT_MULTIPLIER * spot

    return {
        "keep": keep, "strike": strike, "iv": iv, "open_interest": oi,
        "volume": vol, "is_call": is_call, "t": t, "secs": secs,
        "gamma": g, "delta": d, "gex": gex, "dex": dex,
        "valid_iv": valid_iv,
    }


def _bucket_mask(w: dict, bucket: str, as_of: date) -> np.ndarray:
    """0DTE = 最近**未过期**到期；Semaine ≤7 天；Mois ≤35 天；Tout = 全部。

    ⚠️ 0DTE 的"最近到期"必须在**未过期**的合约里取最小值 —— 否则链里若
    残留一根昨日（已过期）的到期，`min()` 会选中它，0DTE 净 GEX 直接算成 0。
    """
    exp_days = np.array(
        [(date.fromisoformat(e) - as_of).days for e in w["_expiry"]], dtype=float)
    alive = exp_days >= 0
    if bucket == "0DTE":
        if not alive.any():
            return np.zeros_like(exp_days, dtype=bool)
        return exp_days == exp_days[alive].min()
    if bucket == "Semaine":
        return alive & (exp_days <= 7)
    if bucket == "Mois":
        return alive & (exp_days <= 35)
    return alive


# ── zero gamma / 墙（在 spot 网格上重算 gamma）─────────────────────────

def _net_gex_at(w: dict, spot: float, weight: np.ndarray, r: float) -> float:
    """给定 spot，重算净 GEX（IV 与 t 固定，只移动 spot）。"""
    g = greeks.gamma(spot, w["strike"], w["t"], r, np.where(w["valid_iv"], w["iv"], 1.0))
    sign = np.where(w["is_call"], 1.0, -1.0)
    gex = sign * g * weight * CONTRACT_MULTIPLIER * spot**2 * 0.01
    return float(gex[w["keep"] & (weight > 0)].sum())


def zero_gamma(w: dict, spot: float, r: float,
               weight_col: str = "open_interest") -> float | None:
    """净 GEX 穿越零的 spot（最靠近当前 spot 的过零点，线性插值）。"""
    weight = w[weight_col]
    sel = w["keep"] & (weight > 0) & w["valid_iv"]
    if not sel.any():
        return None
    grid = np.linspace(spot * (1 - ZG_RANGE), spot * (1 + ZG_RANGE), ZG_STEPS)
    wsel = weight.copy()
    wsel[~sel] = 0.0
    profile = np.array([_net_gex_at(w, s, wsel, r) for s in grid])
    crossings = np.where(np.diff(np.sign(profile)) != 0)[0]
    if len(crossings) == 0:
        return None
    idx = crossings[np.argmin(np.abs(grid[crossings] - spot))]
    x0, x1 = grid[idx], grid[idx + 1]
    y0, y1 = profile[idx], profile[idx + 1]
    if y1 == y0:
        return float(x0)
    return float(x0 - y0 * (x1 - x0) / (y1 - y0))


def gex_by_strike(w: dict, spot: float, r: float,
                  ref_spot: float | None = None) -> dict[float, float]:
    """按 strike 聚合净 GEX。`ref_spot` 给定时 gamma 重算到该参考价（冻结结构）。"""
    sel = w["keep"]
    ref = ref_spot if ref_spot is not None else spot
    g = greeks.gamma(ref, w["strike"], w["t"], r, np.where(w["valid_iv"], w["iv"], 1.0))
    sign = np.where(w["is_call"], 1.0, -1.0)
    gex = sign * g * w["open_interest"] * CONTRACT_MULTIPLIER * ref**2 * 0.01
    out: dict[float, float] = {}
    for k, v in zip(w["strike"][sel], gex[sel]):
        out[float(k)] = out.get(float(k), 0.0) + float(v)
    return out


def walls(w: dict, spot: float, r: float,
          ref_spot: float | None = None) -> dict[str, float | None]:
    """相对墙：现价上方正 GEX 最密处（call_wall）/ 下方负 GEX 最密处（put_wall）。"""
    agg = gex_by_strike(w, spot, r, ref_spot)
    out: dict[str, float | None] = {"call_wall": None, "put_wall": None}
    above = {k: v for k, v in agg.items() if k >= spot and v > 0}
    below = {k: v for k, v in agg.items() if k <= spot and v < 0}
    if above:
        out["call_wall"] = max(above, key=above.get)
    if below:
        out["put_wall"] = min(below, key=below.get)
    return out


def absolute_walls(w: dict, spot: float, r: float,
                   ref_spot: float | None = None) -> dict[str, float | None]:
    """绝对墙：正 GEX 最大处 / 负 GEX 最负处（**不按现价切**）。"""
    agg = gex_by_strike(w, spot, r, ref_spot)
    out: dict[str, float | None] = {"abs_call_wall": None, "abs_put_wall": None}
    up = {k: v for k, v in agg.items() if v > 0}
    dn = {k: v for k, v in agg.items() if v < 0}
    if up:
        out["abs_call_wall"] = max(up, key=up.get)
    if dn:
        out["abs_put_wall"] = min(dn, key=dn.get)
    return out


def put_call_ratios(w: dict) -> dict[str, float]:
    sel = w["keep"]
    oi = w["open_interest"]
    vol = w["volume"]
    is_call = w["is_call"]
    oc = float(oi[sel & is_call].sum())
    op = float(oi[sel & ~is_call].sum())
    vc = float(vol[sel & is_call].sum())
    vp = float(vol[sel & ~is_call].sum())
    return {
        "pc_oi": (op / oc) if oc > 0 else float("nan"),
        "pc_volume": (vp / vc) if vc > 0 else float("nan"),
    }


# ── 统一入口（G4：输出自带来源）──────────────────────────────────────────

def compute(
    chain: list[dict],
    spot: float,
    *,
    as_of: str,
    now_et: datetime,
    r: float,
    source_key: str,
    bucket: str = "Tout",
    weight_col: str = "open_interest",
) -> dict[str, Any]:
    """算一个标的的全部市场结构指标。

    - `as_of`：交易日（YYYY-MM-DD），用于 DTE 基准（承 G3：时刻显式传入）
    - `now_et`：计算时刻（显式传入，**不用 now()**，承 M3-C）
    - `source_key`：底层数据的键/版本（承 G4：输出自带来源，供 G6 校验）
    - 返回 dict 含 `source_key` + `computed_at`（承 G4）
    """
    if not chain:
        raise ValueError("engine.gex.compute：空链（不降级，承 P6）")
    if not isinstance(spot, float) or spot <= 0:
        raise ValueError(f"engine.gex.compute：spot 非法 {spot!r}")

    w = _enrich(chain, spot, now_et, r)
    w["_expiry"] = [c["expiry"] for c in chain]

    as_of_date = date.fromisoformat(as_of)
    mask = _bucket_mask(w, bucket, as_of_date)

    sel = w["keep"] & mask
    net_gex = float(w["gex"][sel].sum())
    net_dex = float(w["dex"][sel].sum())
    ratios = put_call_ratios(w)

    wb = {**w}
    for k in ("gex", "dex", "open_interest", "volume", "gamma", "delta", "iv",
              "strike", "is_call", "t", "keep", "valid_iv"):
        wb[k] = w[k][mask] if mask.shape == w[k].shape else w[k]
    wb["_expiry"] = [e for e, m in zip(w["_expiry"], mask) if m]

    zg = zero_gamma(wb, spot, r, weight_col)
    wl = walls(wb, spot, r)
    aw = absolute_walls(wb, spot, r)

    # 0DTE 净 GEX（最近到期）
    zero_mask = _bucket_mask(w, "0DTE", as_of_date)
    net_gex_0dte = float(w["gex"][w["keep"] & zero_mask].sum())

    exp_days = [(date.fromisoformat(e) - as_of_date).days for e in [c["expiry"] for c in chain]]
    dte = [d for d, m in zip(exp_days, mask) if m and d >= 0]

    return {
        # 市场结构
        "spot": float(spot),
        "net_gex": net_gex,
        "net_gex_0dte": net_gex_0dte,
        "net_dex": net_dex,
        "zero_gamma": zg,
        "call_wall": wl["call_wall"],
        "put_wall": wl["put_wall"],
        "abs_call_wall": aw["abs_call_wall"],
        "abs_put_wall": aw["abs_put_wall"],
        "pc_oi": ratios["pc_oi"],
        "pc_volume": ratios["pc_volume"],
        # 口径（随输出一起冻结，承 G4 / P3）
        "bucket": bucket,
        "weight_col": weight_col,
        "dte_lo": min(dte) if dte else None,
        "dte_hi": max(dte) if dte else None,
        # 自证（承 G4）：computed_at = **计算时刻的 Unix 秒**（承"时区统一"，
        # 且用显式传入的 now_et，不用 now() —— 承 G3 / M3-C）
        "source_key": source_key,
        "computed_at": to_unix_seconds(now_et),
        "as_of": as_of,
    }
