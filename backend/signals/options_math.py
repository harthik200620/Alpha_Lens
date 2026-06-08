"""
options_math.py — Black-76 implied volatility + Greeks for NSE options.

PURE (stdlib `math` + `os`/`datetime` only; no network, DB, or LLM) → import-safe,
unit-testable, deterministic. Computes per-option IV and Greeks from the daily F&O
bhavcopy alone, so the option chain becomes a real options desk read with zero feeds.

WHY BLACK-76 (not spot Black-Scholes):
  NSE index AND stock options are EUROPEAN-style (SEBI-mandated since 2010), and the
  bhavcopy gives us a clean per-expiry FUTURES price. Black-76 prices a European
  option off the forward/futures F directly, so cost-of-carry AND dividends are already
  baked into F — we never have to estimate a per-stock dividend yield (the single
  biggest error source if we used spot BSM). F = the matching-expiry futures settle.

KEY CONVENTIONS (verified):
  • premium = option SETTLEMENT price (SttlmPric), falling back to close (ClsPric).
    Settlement is assigned to EVERY contract at EOD; close is 0/stale on illiquid wings.
  • T = CALENDAR days to expiry / 365 (r and σ are calendar-annualized).
  • r is continuous-compounding; in Black-76 r ONLY discounts (e^-rT), it is NOT in d1.
  • vega is reported PER 1% vol; theta is reported PER CALENDAR DAY.
  • The IV solver returns None (never a fabricated number) when the price is at/below
    intrinsic, vega collapses, or it fails to bracket/converge — the board shows
    "IV n/a" honestly rather than garbage.
"""
import os
import math
from datetime import date, datetime

_SQRT2PI = math.sqrt(2.0 * math.pi)


def _envf(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


# India risk-free (continuous-comp). 91-day T-bill ~5.5% / 10Y ~7%; 6.5% sits between.
# Black-76 is nearly insensitive to r for sub-90-day expiries (r only discounts), so a
# static constant is fine and keeps the engine offline/deterministic.
RISK_FREE = _envf('IV_RISK_FREE_RATE', 0.065)

_T_FLOOR = 0.5 / 365.0   # ~half a day; avoids div-by-zero on expiry day
IV_LO, IV_HI = 1e-4, 5.0  # search bounds: 0.01% .. 500% vol


def _N(x):
    """Standard normal CDF."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _n(x):
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / _SQRT2PI


def _d1d2(F, K, T, sigma):
    sd = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / sd
    return d1, d1 - sd


def black76_price(F, K, T, r, sigma, is_call):
    """European option price under Black-76 (option on a forward/future F)."""
    if T <= 0 or sigma <= 0 or F <= 0 or K <= 0:
        intr = max(F - K, 0.0) if is_call else max(K - F, 0.0)
        return math.exp(-r * max(T, 0.0)) * intr
    d1, d2 = _d1d2(F, K, T, sigma)
    disc = math.exp(-r * T)
    if is_call:
        return disc * (F * _N(d1) - K * _N(d2))
    return disc * (K * _N(-d2) - F * _N(-d1))


def _vega_raw(F, K, T, r, sigma):
    """Vega per 1.0 (100%) vol — used internally by the Newton solver."""
    if T <= 0 or sigma <= 0 or F <= 0 or K <= 0:
        return 0.0
    d1, _ = _d1d2(F, K, T, sigma)
    return F * math.exp(-r * T) * _n(d1) * math.sqrt(T)


def black76_greeks(F, K, T, r, sigma, is_call):
    """
    Black-76 Greeks. delta (wrt F), gamma, vega per-1%-vol, theta per-calendar-day.
    Returns Nones on degenerate inputs.
    """
    if T <= 0 or sigma <= 0 or F <= 0 or K <= 0:
        return {'delta': None, 'gamma': None, 'vega': None, 'theta': None}
    d1, d2 = _d1d2(F, K, T, sigma)
    disc = math.exp(-r * T)
    nd1 = _n(d1)
    sqrtT = math.sqrt(T)
    if is_call:
        delta = disc * _N(d1)
        theta = (-F * disc * nd1 * sigma / (2.0 * sqrtT)
                 - r * K * disc * _N(d2) + r * F * disc * _N(d1)) / 365.0
    else:
        delta = -disc * _N(-d1)
        theta = (-F * disc * nd1 * sigma / (2.0 * sqrtT)
                 + r * K * disc * _N(-d2) - r * F * disc * _N(-d1)) / 365.0
    gamma = disc * nd1 / (F * sigma * sqrtT)
    vega = F * disc * nd1 * sqrtT / 100.0   # per 1% vol
    return {
        'delta': round(delta, 4),
        'gamma': round(gamma, 6),
        'vega': round(vega, 4),
        'theta': round(theta, 4),
    }


def implied_vol_black76(premium, F, K, T, r=None, is_call=True):
    """
    Back out IV from a market premium via bracketed Newton-Raphson with a hard
    bisection fallback. Returns the annualized vol (e.g. 0.18 = 18%) or None.

    Returns None (never a fabricated IV) when:
      • inputs are degenerate (premium/F/K/T <= 0),
      • the premium is at/below discounted intrinsic (σ→0, undefined — common on deep
        ITM EOD settlement), or at/above the no-arb max,
      • the root can't be bracketed in [0.01%, 500%], or it fails to converge.
    """
    if r is None:
        r = RISK_FREE
    try:
        premium = float(premium); F = float(F); K = float(K); T = float(T)
    except (TypeError, ValueError):
        return None
    # Reject sub-tick premiums: NSE prices to paise (min 0.05), so a premium below this
    # floor carries no recoverable time value and would otherwise return the IV lower
    # bound (a fabricated ~0.0001) instead of an honest None.
    if premium < 1e-3 or F <= 0 or K <= 0 or T <= 0:
        return None

    disc = math.exp(-r * T)
    intrinsic = disc * (max(F - K, 0.0) if is_call else max(K - F, 0.0))
    cap = disc * (F if is_call else K)          # no-arb upper bound
    eps = 1e-8
    if premium <= intrinsic + eps or premium >= cap - eps:
        return None                              # at/below intrinsic or above max

    def f(sig):
        return black76_price(F, K, T, r, sig, is_call) - premium

    lo, hi = IV_LO, IV_HI
    flo, fhi = f(lo), f(hi)
    if flo > 0 or fhi < 0:
        return None                              # not bracketed (price monotonic in σ)

    # Brenner-Subrahmanyam ATM seed, clamped into bounds.
    sigma = max(lo, min(hi, math.sqrt(2.0 * math.pi / T) * premium / F))
    tol = max(1e-6, premium * 1e-6)

    for _ in range(60):
        fx = f(sigma)
        if abs(fx) < tol:
            return round(sigma, 4)
        # maintain bracket for the fallback
        if fx > 0:
            hi = sigma
        else:
            lo = sigma
        v = _vega_raw(F, K, T, r, sigma)
        if v < 1e-8 or not math.isfinite(v):
            break                                # collapse to bisection
        step = fx / v
        nxt = sigma - step
        if not (lo < nxt < hi) or not math.isfinite(nxt):
            break                                # Newton left the bracket
        sigma = nxt

    # Bisection fallback — guaranteed to converge (price is monotonic in σ).
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if abs(fm) < tol or (hi - lo) < 1e-7:
            return round(mid, 4)
        if fm > 0:
            hi = mid
        else:
            lo = mid
    return round(0.5 * (lo + hi), 4)


def years_to_expiry(expiry_str, asof_str):
    """
    Calendar-day year fraction from the bhavcopy date to the option expiry, floored
    so expiry-day math never divides by zero. Both args 'YYYY-MM-DD'. None on bad input.
    """
    try:
        e = _to_date(expiry_str)
        a = _to_date(asof_str)
        if e is None or a is None:
            return None
        days = (e - a).days
        return max(days, 0) / 365.0 if days > 0 else _T_FLOOR
    except Exception:
        return None


def _to_date(s):
    if isinstance(s, (date, datetime)):
        return s if isinstance(s, date) and not isinstance(s, datetime) else s.date()
    s = str(s or '').strip()[:10]
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        return None


def iv_and_greeks(premium, F, K, T, is_call, r=None):
    """
    Convenience: solve IV then compute Greeks at that IV.
    Returns {iv, delta, gamma, vega, theta} (iv None ⇒ Greeks None).
    """
    if r is None:
        r = RISK_FREE
    iv = implied_vol_black76(premium, F, K, T, r, is_call)
    if iv is None:
        return {'iv': None, 'delta': None, 'gamma': None, 'vega': None, 'theta': None}
    g = black76_greeks(F, K, T, r, iv, is_call)
    return {'iv': round(iv, 4), **g}
