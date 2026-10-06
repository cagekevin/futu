"""Source d'options via **Futu OpenD** — variante de `gex/ingest.py` (CBOE).

Pourquoi ce module
------------------
`gex/ingest.py` tire toute la chaîne en **un GET** sur l'endpoint public CBOE
(délayé ~15 min, sans compte). Futu OpenD expose la même information
(bid/ask, IV, OI, volume, greeks) mais **contrat par contrat**, en deux temps :

    1. `get_option_chain(code, start, end)`   -> liste des contrats (métadonnées)
    2. `get_market_snapshot(codes)`           -> OI + greeks + bid/ask (400 max/appel)

Le seul champ que Futu OpenD ne donne **pas** ici est le spot de l'indice
(« 暂不支持美股指数 » sur `US..SPX` / `US..NDX` — c'est une question de
**droit de marché**, pas de symbole : les mêmes droits couvrent la chaîne
d'options mais pas le flux indice). Le spot est donc **reconstruit par la
parité put-call**, qui n'utilise que la chaîne elle-même :

    C - P = (F - K) · df        df = e^(-rT)
    => F  = K + (C - P) / df
    => S  = F · df

(SPX est de style européen, la parité y est exacte.)

Le résultat est un `ChainSnapshot` **identique** à celui produit par
`ingest.py` — `scheduler.py` et `metrics.py` n'ont donc rien à changer.

Périmètre
---------
Toute la chaîne SPX fait ~2000 contrats par échéance sur 61 échéances : tirer
tout serait absurde dans une boucle à 60 s. Par défaut on prend les
`max_expiries` échéances les plus proches (0DTE inclus) et on écrête les
strikes à ±`strike_band` du spot — la contribution au GEX des ailes extrêmes
est négligeable et n'existe que par l'open interest, publié une fois par jour.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from gex.ingest import ChainSnapshot

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# --- Paramètres -----------------------------------------------------------
HOST = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
PORT = int(os.getenv("FUTU_OPEND_PORT", "11111"))

# Échéances les plus proches à tirer (0DTE = la première).
MAX_EXPIRIES = int(os.getenv("FUTU_MAX_EXPIRIES", "8"))
# Bande de strikes autour du spot (fraction). 0 = pas d'écrêtage.
STRIKE_BAND = float(os.getenv("FUTU_STRIKE_BAND", "0.15"))
# Taille des lots d'appel snapshot (limite OpenD = 400).
BATCH = 400
# Taux sans risque utilisé si `gex.rates` n'est pas disponible.
R_FALLBACK = 0.04

# Sous-jacents Futu. **Double point** : `US..SPX` = indice, `US.SPX` n'existe pas.
FUTU_SYMBOLS: dict[str, str] = {
    "SPX": "US..SPX",
    "NDX": "US..NDX",
    "VIX": "US..VIX",
    "SPY": "US.SPY",
    "QQQ": "US.QQQ",
}


# --- Contexte OpenD (singleton) -------------------------------------------
_ctx = None


def _get_ctx():
    """Ouvre (une seule fois) la connexion à OpenD.

    La connexion est coûteuse — on la garde pour tout le process.
    """
    global _ctx
    if _ctx is not None:
        return _ctx

    from futu import OpenQuoteContext  # import paresseux : Futu absent = pas de crash au boot

    ctx = OpenQuoteContext(host=HOST, port=PORT)
    _ctx = ctx
    log.info("futu_source: connecté à OpenD %s:%s", HOST, PORT)
    return ctx


def close() -> None:
    """Ferme la connexion OpenD (appelé à l'arrêt du process)."""
    global _ctx
    if _ctx is not None:
        try:
            _ctx.close()
        finally:
            _ctx = None


# --- Helpers --------------------------------------------------------------
def _risk_free() -> float:
    """Taux sans risque : celui que le dashboard utilise déjà, sinon repli."""
    try:
        from gex.rates import get_rate  # type: ignore

        r = get_rate()
        if r is not None and 0 < float(r) < 0.25:
            return float(r)
    except Exception:  # noqa: BLE001 — pas bloquant
        pass
    return R_FALLBACK


def _num(df: pd.DataFrame, *names: str) -> pd.Series:
    """Colonne numérique tolérante.

    ⚠️ Futu **renomme ses champs** : la fourchette est `bid_price` / `ask_price`,
    pas `bid` / `ask` (vérifié sur `US.SPX261016C...`). Certaines colonnes
    peuvent aussi manquer selon l'instrument. On essaie les variantes dans
    l'ordre et on retombe sur 0.0 — un `.fillna` sur un scalaire (`pd.to_numeric`
    d'une colonne absente renvoie `nan`, pas une Series) a déjà cassé ce module.
    """
    for n in names:
        if n in df.columns:
            s = pd.to_numeric(df[n], errors="coerce")
            if not isinstance(s, pd.Series):
                s = pd.Series([s] * len(df), index=df.index)
            return s.fillna(0.0)
    log.debug("futu_source: colonnes %s absentes (dispo: %s)", names, list(df.columns)[:10])
    return pd.Series(0.0, index=df.index)


def _snapshot_batched(ctx, codes: list[str]) -> pd.DataFrame:
    """`get_market_snapshot` par lots de `BATCH` (limite OpenD)."""
    from futu import RET_OK

    frames = []
    for i in range(0, len(codes), BATCH):
        ret, df = ctx.get_market_snapshot(codes[i : i + BATCH])
        if ret != RET_OK:
            log.warning("futu_source: snapshot lot %d échoué — %s", i // BATCH, df)
            continue
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _compute_spot(chain: pd.DataFrame, r: float, now: datetime) -> float:
    """Spot par parité put-call, moyenné sur les strikes proches de la monnaie.

    Chaque échéance donne un forward `F = K + (C - P)/df` ; on agrège les
    strikes les plus liquides (OI le plus élevé) pour limiter le bruit de
    cotation. Repli : la moyenne des forwards, puis `NaN` si rien n'est
    exploitable.
    """
    est: list[float] = []
    for _exp, g in chain.groupby("expiry", sort=True):
        c = g[g["type"] == "C"].set_index("strike")
        p = g[g["type"] == "P"].set_index("strike")
        common = c.index.intersection(p.index)
        if common.empty:
            continue
        cc, pp = c.loc[common], p.loc[common]
        # milieu de fourchette, on écarte les contrats sans cotation
        cm = (cc["bid"] + cc["ask"]) / 2
        pm = (pp["bid"] + pp["ask"]) / 2
        ok = (cc["bid"] > 0) & (cc["ask"] > 0) & (pp["bid"] > 0) & (pp["ask"] > 0)
        common = common[ok.to_numpy()]
        if common.empty:
            continue
        cm, pm = cm.loc[common], pm.loc[common]
        oi = (cc["open_interest"].loc[common] + pp["open_interest"].loc[common]).fillna(0)
        # les 15 strikes les plus liquides de l'échéance
        top = oi.sort_values(ascending=False).head(15).index

        t_years = max((_exp - now.date()).days, 0) / 365.0 + 1e-9
        df = math.exp(-r * t_years)
        fwd = (top.to_series().astype(float) + (cm.loc[top] - pm.loc[top]) / df).mean()
        est.append(fwd * df)

    if not est:
        return float("nan")
    return sum(est) / len(est)


# --- Point d'entrée (même contrat que `ingest.fetch_chain`) ---------------
def fetch_chain(
    symbol: str,
    futu_symbol: str | None = None,
    timeout: int = 60,  # noqa: ARG001 — parité de signature avec ingest.fetch_chain
) -> ChainSnapshot:
    """Tire une chaîne complète depuis Futu OpenD.

    `symbol`      : clé interne du dashboard ("SPX").
    `futu_symbol` : code Futu ("US..SPX"). Déduit de `FUTU_SYMBOLS` sinon.
    """
    from futu import RET_OK

    code = futu_symbol or FUTU_SYMBOLS.get(symbol.upper())
    if not code:
        raise ValueError(f"futu_source: pas de code Futu connu pour {symbol!r}")

    ctx = _get_ctx()
    fetched_at = datetime.utcnow()

    # 1) échéances
    ret, exps = ctx.get_option_expiration_date(code)
    if ret != RET_OK:
        raise RuntimeError(f"futu_source: get_option_expiration_date({code}) -> {exps}")
    dates = sorted(str(x) for x in exps["strike_time"].tolist())[:MAX_EXPIRIES]
    if not dates:
        raise RuntimeError(f"futu_source: aucune échéance pour {code}")

    # 2) contrats échéance par échéance
    rows: list[dict] = []
    for d in dates:
        ret, ch = ctx.get_option_chain(code, start=d, end=d)
        if ret != RET_OK:
            log.warning("futu_source: option_chain %s %s -> %s", code, d, ch)
            continue
        if ch.empty:
            continue
        sub = ch
        if STRIKE_BAND > 0:
            # le spot n'est pas encore connu : on écrête plus tard, on garde tout
            pass
        rows.extend(
            {
                "code": r["code"],
                "expiry": pd.to_datetime(r["strike_time"]).date(),
                "cp": "C" if str(r["option_type"]).upper().startswith("C") else "P",
                "strike": float(r["strike_price"]),
            }
            for _, r in sub.iterrows()
        )

    if not rows:
        raise RuntimeError(f"futu_source: aucune chaîne pour {code}")

    meta = pd.DataFrame(rows).drop_duplicates(subset=["code"])

    # 3) OI + greeks + fourchette (par lots)
    snap = _snapshot_batched(ctx, meta["code"].tolist())
    if snap.empty:
        raise RuntimeError(f"futu_source: snapshot vide pour {code}")

    df = meta.merge(snap, left_on="code", right_on="code", how="inner")
    out = pd.DataFrame(
        {
            "contract": df["code"],
            "expiry": df["expiry"],
            "type": df["cp"],
            "strike": df["strike"],
            # ⚠️ Futu : `bid_price` / `ask_price` (pas `bid` / `ask`)
            "bid": _num(df, "bid_price", "bid"),
            "ask": _num(df, "ask_price", "ask"),
            # Futu exprime l'IV en pourcent (46.0 = 46 %) ; CBOE en fraction.
            # ⚠️ Les ailes profondes renvoient 0 ou des valeurs aberrantes (832 %) :
            # cohérent avec `metrics_core` qui écarte les contrats dont le prix
            # ne dépend pas assez de la vol pour la déterminer.
            "iv": _num(df, "option_implied_volatility") / 100.0,
            "open_interest": _num(df, "option_open_interest"),
            "volume": _num(df, "volume"),
            "delta_cboe": _num(df, "option_delta"),
            "gamma_cboe": _num(df, "option_gamma"),
            "last_trade_price": _num(df, "last_price"),
            # Le multiplicateur est fourni (100 pour SPX) — ne pas le coder en dur.
            "multiplier": _num(df, "option_contract_multiplier").replace(0.0, 100.0),
        }
    )
    out = out[out["strike"] > 0].reset_index(drop=True)

    # 4) spot par parité
    r = _risk_free()
    spot = _compute_spot(out, r, datetime.now(_ET).replace(tzinfo=None))
    if not (spot == spot) or spot <= 0:  # NaN ou absurde
        log.warning("futu_source: parité inexploitable, repli sur le strike le plus traité")
        atm = out.sort_values("open_interest", ascending=False).head(1)
        spot = float(atm["strike"].iloc[0]) if not atm.empty else float("nan")

    # 5) écrêtage en bande autour du spot
    if STRIKE_BAND > 0 and spot == spot:
        lo, hi = spot * (1 - STRIKE_BAND), spot * (1 + STRIKE_BAND)
        before = len(out)
        out = out[(out["strike"] >= lo) & (out["strike"] <= hi)].reset_index(drop=True)
        log.info(
            "futu_source: %s écrêtage ±%.0f%% -> %d/%d contrats",
            symbol, STRIKE_BAND * 100, len(out), before,
        )

    feed_ts = datetime.now(_ET).replace(tzinfo=None)  # Futu = temps réel, pas de délai

    log.info(
        "futu_source: %s ok — spot=%.2f (parité) %d contrats, %d échéances, r=%.4f",
        symbol, spot, len(out), out["expiry"].nunique(), r,
    )
    return ChainSnapshot(
        symbol=symbol,
        spot=float(spot),
        feed_timestamp=feed_ts,
        fetched_at=fetched_at,
        options=out,
    )
