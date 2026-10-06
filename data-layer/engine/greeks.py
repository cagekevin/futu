"""M3.1 希腊值（greeks）—— Black-Scholes 纯函数（价格 / 希腊值 / IV）。

承 G1（零 IO）：只用 numpy/scipy。承 G5（可独立重算）：纯函数，无状态。
约定：
- t 以年为单位（365 天），sigma 为年化波动率（0.20 = 20%），r 为连续无风险利率。
- 接受标量或 ndarray（numpy 广播）。
- 无股息（q=0）。
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

_EPS = 1e-12


def _d1_d2(s, k, t, r, sigma):
    s = np.asarray(s, dtype=float)
    k = np.asarray(k, dtype=float)
    t = np.maximum(np.asarray(t, dtype=float), _EPS)
    sigma = np.maximum(np.asarray(sigma, dtype=float), _EPS)
    d1 = (np.log(s / k) + (r + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
    d2 = d1 - sigma * np.sqrt(t)
    return d1, d2


def call_price(s, k, t, r, sigma):
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    return s * norm.cdf(d1) - k * np.exp(-r * t) * norm.cdf(d2)


def put_price(s, k, t, r, sigma):
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    return k * np.exp(-r * t) * norm.cdf(-d2) - s * norm.cdf(-d1)


def call_delta(s, k, t, r, sigma):
    d1, _ = _d1_d2(s, k, t, r, sigma)
    return norm.cdf(d1)


def put_delta(s, k, t, r, sigma):
    return call_delta(s, k, t, r, sigma) - 1.0


def gamma(s, k, t, r, sigma):
    """Gamma，call / put 相同。"""
    d1, _ = _d1_d2(s, k, t, r, sigma)
    t = np.maximum(np.asarray(t, dtype=float), _EPS)
    sigma = np.maximum(np.asarray(sigma, dtype=float), _EPS)
    return norm.pdf(d1) / (np.asarray(s, dtype=float) * sigma * np.sqrt(t))


def vega(s, k, t, r, sigma):
    """Vega（每 1 点波动率，未除 100）。"""
    d1, _ = _d1_d2(s, k, t, r, sigma)
    t = np.maximum(np.asarray(t, dtype=float), _EPS)
    return np.asarray(s, dtype=float) * norm.pdf(d1) * np.sqrt(t)


def call_theta(s, k, t, r, sigma):
    """Call theta（年化；除 365 得日 theta）。"""
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    t = np.maximum(np.asarray(t, dtype=float), _EPS)
    return (
        -np.asarray(s, dtype=float) * norm.pdf(d1) * sigma / (2 * np.sqrt(t))
        - r * np.asarray(k, dtype=float) * np.exp(-r * t) * norm.cdf(d2)
    )


def put_theta(s, k, t, r, sigma):
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    t = np.maximum(np.asarray(t, dtype=float), _EPS)
    return (
        -np.asarray(s, dtype=float) * norm.pdf(d1) * sigma / (2 * np.sqrt(t))
        + r * np.asarray(k, dtype=float) * np.exp(-r * t) * norm.cdf(-d2)
    )


def vanna(s, k, t, r, sigma):
    """∂delta/∂sigma = ∂vega/∂S，call / put 相同。"""
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    sigma = np.maximum(np.asarray(sigma, dtype=float), _EPS)
    return -norm.pdf(d1) * d2 / sigma


def charm(s, k, t, r, sigma):
    """∂delta/∂t（交易者约定），无股息时 call / put 相同。"""
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    t = np.maximum(np.asarray(t, dtype=float), _EPS)
    sigma = np.maximum(np.asarray(sigma, dtype=float), _EPS)
    return -norm.pdf(d1) * (2 * r * t - d2 * sigma * np.sqrt(t)) / (
        2 * t * sigma * np.sqrt(t)
    )


def charm_per_day(s, k, t, r, sigma):
    return charm(s, k, t, r, sigma) / 365.0


def implied_vol(price, s, k, t, r, is_call, tol=1e-6, max_iter=60):
    """Newton-Raphson 求 IV（向量化）。不收敛 / 低于内在价值 → NaN。"""
    price = np.asarray(price, dtype=float)
    s = np.broadcast_to(np.asarray(s, dtype=float), price.shape).copy()
    k = np.broadcast_to(np.asarray(k, dtype=float), price.shape).copy()
    t = np.broadcast_to(np.asarray(t, dtype=float), price.shape).copy()
    is_call = np.broadcast_to(np.asarray(is_call, dtype=bool), price.shape)

    intrinsic = np.where(
        is_call,
        np.maximum(s - k * np.exp(-r * t), 0.0),
        np.maximum(k * np.exp(-r * t) - s, 0.0),
    )
    valid = (price > intrinsic + 1e-10) & (t > 0)

    sigma = np.full(price.shape, 0.5)
    for _ in range(max_iter):
        model = np.where(is_call, call_price(s, k, t, r, sigma),
                         put_price(s, k, t, r, sigma))
        v = vega(s, k, t, r, sigma)
        step = np.where(v > 1e-12, (model - price) / np.maximum(v, 1e-12), 0.0)
        sigma = np.clip(sigma - np.clip(step, -0.5, 0.5), 1e-4, 10.0)
    model = np.where(is_call, call_price(s, k, t, r, sigma),
                     put_price(s, k, t, r, sigma))
    converged = np.abs(model - price) < np.maximum(tol, 1e-4 * price)
    return np.where(valid & converged, sigma, np.nan)
