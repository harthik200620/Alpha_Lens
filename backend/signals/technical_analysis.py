"""
Technical Analysis Helper Module for Alpha Lens v5.0
Advanced quant-grade indicators: EMA, MACD, ADX, ATR, Stochastic RSI,
VWAP, OBV, Bollinger %B + Bandwidth, Fibonacci Retracement.
Provides institutional-level market context for ensemble predictions.
"""
import angelone_shim as yf
import logging
import math
import collections
from datetime import datetime, timedelta

logger = logging.getLogger('yfinance')
logger.disabled = True
logger.propagate = False


# =========================================================
# CORE INDICATOR COMPUTATIONS
# =========================================================

def compute_ema(closes, period):
    """Compute Exponential Moving Average."""
    if len(closes) < period:
        return None
    multiplier = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period  # seed with SMA
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return round(ema, 2)


def compute_rsi(closes, period=14):
    """Compute RSI (Relative Strength Index) from a list of closing prices."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_stochastic_rsi(closes, rsi_period=14, stoch_period=14, smooth_k=3, smooth_d=3):
    """
    Compute Stochastic RSI — catches momentum shifts at RSI extremes.
    Returns (stoch_rsi_k, stoch_rsi_d) or (None, None).
    """
    if len(closes) < rsi_period + stoch_period + smooth_k + smooth_d:
        return None, None

    # Compute RSI series
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:rsi_period]) / rsi_period
    avg_loss = sum(losses[:rsi_period]) / rsi_period

    rsi_values = []
    for i in range(rsi_period, len(gains)):
        avg_gain = (avg_gain * (rsi_period - 1) + gains[i]) / rsi_period
        avg_loss = (avg_loss * (rsi_period - 1) + losses[i]) / rsi_period
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))

    if len(rsi_values) < stoch_period:
        return None, None

    # Stochastic of RSI
    stoch_rsi_raw = []
    for i in range(stoch_period - 1, len(rsi_values)):
        window = rsi_values[i - stoch_period + 1:i + 1]
        low = min(window)
        high = max(window)
        if high == low:
            stoch_rsi_raw.append(50.0)
        else:
            stoch_rsi_raw.append(((rsi_values[i] - low) / (high - low)) * 100)

    # Smooth %K
    if len(stoch_rsi_raw) < smooth_k:
        return None, None
    k_values = []
    for i in range(smooth_k - 1, len(stoch_rsi_raw)):
        k_values.append(sum(stoch_rsi_raw[i - smooth_k + 1:i + 1]) / smooth_k)

    # Smooth %D
    if len(k_values) < smooth_d:
        return None, None
    d_values = []
    for i in range(smooth_d - 1, len(k_values)):
        d_values.append(sum(k_values[i - smooth_d + 1:i + 1]) / smooth_d)

    return round(k_values[-1], 2), round(d_values[-1], 2)


def compute_macd(closes, fast=12, slow=26, signal=9):
    """
    Compute MACD line, Signal line, and Histogram.
    Returns (macd_line, signal_line, histogram) or (None, None, None).
    """
    if len(closes) < slow + signal:
        return None, None, None

    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None

    # Compute full MACD series for signal line
    mult_fast = 2.0 / (fast + 1)
    mult_slow = 2.0 / (slow + 1)

    ef = sum(closes[:fast]) / fast
    es = sum(closes[:slow]) / slow

    macd_series = []
    for i, price in enumerate(closes):
        if i < fast:
            ef = sum(closes[:i + 1]) / (i + 1)
        else:
            ef = (price - ef) * mult_fast + ef
        if i < slow:
            es = sum(closes[:i + 1]) / (i + 1)
        else:
            es = (price - es) * mult_slow + es
        if i >= slow - 1:
            macd_series.append(ef - es)

    if len(macd_series) < signal:
        return None, None, None

    # Signal line = EMA of MACD
    mult_sig = 2.0 / (signal + 1)
    sig = sum(macd_series[:signal]) / signal
    for val in macd_series[signal:]:
        sig = (val - sig) * mult_sig + sig

    macd_line = round(macd_series[-1], 4)
    signal_line = round(sig, 4)
    histogram = round(macd_line - signal_line, 4)

    return macd_line, signal_line, histogram


def compute_adx(highs, lows, closes, period=14):
    """
    Compute ADX (Average Directional Index) — measures trend strength.
    Returns ADX value (0-100) or None.
    """
    n = len(closes)
    if n < period * 2 + 1:
        return None

    tr_list = []
    plus_dm_list = []
    minus_dm_list = []

    for i in range(1, n):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]

        plus_dm = high_diff if high_diff > low_diff and high_diff > 0 else 0
        minus_dm = low_diff if low_diff > high_diff and low_diff > 0 else 0

        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        tr_list.append(tr)
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    # Smoothed averages
    atr = sum(tr_list[:period]) / period
    plus_dm_avg = sum(plus_dm_list[:period]) / period
    minus_dm_avg = sum(minus_dm_list[:period]) / period

    dx_list = []
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        plus_dm_avg = (plus_dm_avg * (period - 1) + plus_dm_list[i]) / period
        minus_dm_avg = (minus_dm_avg * (period - 1) + minus_dm_list[i]) / period

        if atr == 0:
            continue
        plus_di = (plus_dm_avg / atr) * 100
        minus_di = (minus_dm_avg / atr) * 100

        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx_list.append(0)
        else:
            dx_list.append(abs(plus_di - minus_di) / di_sum * 100)

    if len(dx_list) < period:
        return None

    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period

    return round(adx, 2)


def compute_atr(highs, lows, closes, period=14):
    """
    Compute ATR (Average True Range) — measures volatility.
    Returns ATR value or None.
    """
    if len(closes) < period + 1:
        return None

    tr_list = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        tr_list.append(tr)

    atr = sum(tr_list[:period]) / period
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period

    return round(atr, 2)


def compute_bollinger(closes, period=20):
    """
    Compute Bollinger Bands: %B position + Bandwidth.
    %B < 0 = below lower band, %B > 1 = above upper band.
    Bandwidth squeeze (< 0.1) signals imminent breakout.
    Returns (percent_b, bandwidth) or (None, None).
    """
    if len(closes) < period:
        return None, None
    sma = sum(closes[-period:]) / period
    std = (sum((c - sma) ** 2 for c in closes[-period:]) / period) ** 0.5
    if std == 0:
        return 0.5, 0.0
    upper = sma + 2 * std
    lower = sma - 2 * std
    band_width = upper - lower
    if band_width == 0:
        return 0.5, 0.0
    percent_b = round((closes[-1] - lower) / band_width, 4)
    bandwidth = round(band_width / sma, 4)  # normalized bandwidth
    return percent_b, bandwidth


def compute_obv_trend(closes, volumes, lookback=20):
    """
    Compute OBV (On-Balance Volume) trend direction.
    Returns: 'ACCUMULATING', 'DISTRIBUTING', or 'FLAT'.
    Detects volume-price divergences used by institutional traders.
    """
    if len(closes) < lookback + 1 or len(volumes) < lookback + 1:
        return 'UNKNOWN'

    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])

    # Linear regression slope of last `lookback` OBV values
    recent_obv = obv[-lookback:]
    n = len(recent_obv)
    x_mean = (n - 1) / 2
    y_mean = sum(recent_obv) / n
    numerator = sum((i - x_mean) * (recent_obv[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return 'FLAT'

    slope = numerator / denominator
    # Normalize slope relative to average OBV magnitude
    avg_obv = abs(y_mean) if y_mean != 0 else 1
    normalized_slope = slope / avg_obv

    if normalized_slope > 0.01:
        return 'ACCUMULATING'
    elif normalized_slope < -0.01:
        return 'DISTRIBUTING'
    return 'FLAT'


def compute_vwap(highs, lows, closes, volumes):
    """
    Compute VWAP (Volume-Weighted Average Price) — institutional reference price.
    Uses all available data as the anchor period.
    Returns VWAP price or None.
    """
    if not highs or not volumes or len(highs) == 0:
        return None
    total_volume = 0
    cumulative_tp_vol = 0
    for i in range(len(closes)):
        typical_price = (highs[i] + lows[i] + closes[i]) / 3
        vol = volumes[i] or 0
        cumulative_tp_vol += typical_price * vol
        total_volume += vol
    if total_volume == 0:
        return None
    return round(cumulative_tp_vol / total_volume, 2)


def compute_anchored_vwap(highs, lows, closes, volumes, anchor_bars_back):
    """
    Compute VWAP anchored to a specific point in the past.

    `anchor_bars_back` = how many bars back the anchor is. Examples:
      - 5  → VWAP over the most recent 5 bars (a recent-event anchor proxy)
      - 20 → VWAP over the most recent 20 bars (intermediate anchor)
      - len(closes) → identical to compute_vwap (full history)

    Institutions use Anchored VWAP from significant events: earnings,
    52-week lows, breakout days, news catalysts. With daily bars we
    approximate by anchoring N bars back.

    Returns the anchored VWAP price or None.
    """
    if not closes or anchor_bars_back is None or anchor_bars_back <= 0:
        return None
    anchor_bars_back = min(anchor_bars_back, len(closes))
    h = highs[-anchor_bars_back:]
    l = lows[-anchor_bars_back:]
    c = closes[-anchor_bars_back:]
    v = volumes[-anchor_bars_back:]
    return compute_vwap(h, l, c, v)


def compute_keltner_channels(highs, lows, closes, period=20, atr_mult=1.5):
    """
    Keltner Channels = EMA(close, period) ± atr_mult × ATR(period).
    Needed for the TTM Squeeze indicator.

    Returns (upper, middle, lower) or (None, None, None).
    """
    if len(closes) < period * 2 + 1:
        return None, None, None
    middle = compute_ema(closes, period)
    atr = compute_atr(highs, lows, closes, period)
    if middle is None or atr is None:
        return None, None, None
    upper = middle + atr_mult * atr
    lower = middle - atr_mult * atr
    return round(upper, 2), round(middle, 2), round(lower, 2)


def compute_ttm_squeeze(highs, lows, closes, bb_period=20, bb_std=2.0,
                        kc_period=20, kc_mult=1.5):
    """
    TTM Squeeze — detect volatility contraction (squeeze on) and breakout (squeeze fire).

    The squeeze is ON when Bollinger Bands are completely INSIDE Keltner Channels.
    That means the market is unusually quiet vs its own trend volatility — a coil
    that historically resolves with a sharp directional move.

    The squeeze FIRES when the BB widens back outside KC.  Direction of the fire
    is inferred from the slope of the last 10 closes (linear regression).

    Returns dict:
      in_squeeze        : bool — currently coiled
      squeeze_release   : 'BULLISH' / 'BEARISH' / None — just fired this bar
      momentum          : float — slope/y_mean signed magnitude proxy
    """
    default = {'in_squeeze': False, 'squeeze_release': None, 'momentum': 0.0}
    min_bars_needed = max(bb_period, kc_period * 2 + 1) + 1
    if len(closes) < min_bars_needed:
        return default

    def _squeeze_at(h, l, c):
        """Is BB inside KC at this snapshot of price history?"""
        sma = sum(c[-bb_period:]) / bb_period
        std = (sum((x - sma) ** 2 for x in c[-bb_period:]) / bb_period) ** 0.5
        bb_upper = sma + bb_std * std
        bb_lower = sma - bb_std * std
        kc_upper, _, kc_lower = compute_keltner_channels(h, l, c, kc_period, kc_mult)
        if kc_upper is None or kc_lower is None:
            return None
        return bb_upper < kc_upper and bb_lower > kc_lower

    sq_now = _squeeze_at(highs, lows, closes)
    sq_prev = _squeeze_at(highs[:-1], lows[:-1], closes[:-1])
    if sq_now is None or sq_prev is None:
        return default

    # Momentum proxy: slope of last 10 closes normalized by mean
    momentum = 0.0
    if len(closes) >= 10:
        recent = closes[-10:]
        n = 10
        x_mean = (n - 1) / 2
        y_mean = sum(recent) / n
        num = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        if den > 0 and y_mean != 0:
            momentum = (num / den) / y_mean

    release = None
    if sq_prev and not sq_now:
        if momentum > 0.0005:
            release = 'BULLISH'
        elif momentum < -0.0005:
            release = 'BEARISH'

    return {
        'in_squeeze': sq_now,
        'squeeze_release': release,
        'momentum': round(momentum, 5),
    }


def get_oi_buildup(ticker):
    """
    Detect Open Interest build-up pattern for F&O-eligible stocks.

    Returns one of:
      'LONG_BUILDUP'   — price up + OI up   (bullish, institutional longs)
      'SHORT_COVERING' — price up + OI down (bullish but weak follow-through)
      'SHORT_BUILDUP'  — price down + OI up (bearish, institutional shorts)
      'LONG_UNWINDING' — price down + OI down (bearish but weak follow-through)
      'NEUTRAL'        — no clear pattern (price move below threshold)
      'NOT_FNO'        — stock not in F&O segment
      'UNKNOWN'        — bhavcopy fetch failed or unavailable

    Delegates to oi_data.get_oi_buildup_for_ticker, which downloads NSE's
    daily F&O bhavcopy once per process (cached 4h) and aggregates OI across
    all stock-future expiries. Safe — never raises; returns 'UNKNOWN' on any
    failure so the ensemble degrades gracefully.
    """
    try:
        from marketdata import oi_data
        return oi_data.get_oi_buildup_for_ticker(ticker)
    except Exception as e:
        # Never let an OI fetch break the technical-context build
        print(f"[OI] get_oi_buildup({ticker!r}) failed: {e}")
        return 'UNKNOWN'


def compute_fibonacci_levels(highs, lows, lookback=60):
    """
    Compute Fibonacci retracement levels from recent swing high/low.
    Returns dict with levels and current position, or None.
    """
    if len(highs) < lookback:
        lookback = len(highs)
    if lookback < 5:
        return None

    recent_highs = highs[-lookback:]
    recent_lows = lows[-lookback:]
    swing_high = max(recent_highs)
    swing_low = min(recent_lows)
    diff = swing_high - swing_low

    if diff <= 0:
        return None

    levels = {
        'swing_high': round(swing_high, 2),
        'swing_low': round(swing_low, 2),
        'fib_236': round(swing_high - 0.236 * diff, 2),
        'fib_382': round(swing_high - 0.382 * diff, 2),
        'fib_500': round(swing_high - 0.500 * diff, 2),
        'fib_618': round(swing_high - 0.618 * diff, 2),
        'fib_786': round(swing_high - 0.786 * diff, 2),
    }
    return levels


def compute_fibonacci_position(current_price, fib_levels):
    """
    Determine where the current price sits relative to Fibonacci levels.
    Returns a string: 'ABOVE_SWING_HIGH', 'BETWEEN_0_236', etc.
    """
    if fib_levels is None:
        return 'UNKNOWN'
    sh = fib_levels['swing_high']
    sl = fib_levels['swing_low']
    diff = sh - sl
    if diff <= 0:
        return 'UNKNOWN'
    position = (sh - current_price) / diff
    if position <= 0:
        return 'ABOVE_SWING_HIGH'
    elif position <= 0.236:
        return 'ABOVE_FIB_236'
    elif position <= 0.382:
        return 'BETWEEN_236_382'
    elif position <= 0.500:
        return 'BETWEEN_382_500'
    elif position <= 0.618:
        return 'BETWEEN_500_618'
    elif position <= 0.786:
        return 'BETWEEN_618_786'
    else:
        return 'BELOW_FIB_786'


def compute_volume_profile(closes, volumes, bins=10):
    """
    Compute Volume Profile over the available data.
    Returns Point of Control (POC), Value Area High (VAH), and Value Area Low (VAL).
    """
    if not closes or not volumes or len(closes) < 10:
        return None, None, None

    min_price = min(closes)
    max_price = max(closes)
    
    if max_price == min_price:
        return closes[-1], closes[-1], closes[-1]
        
    bin_size = (max_price - min_price) / bins
    profile = collections.defaultdict(float)
    
    for c, v in zip(closes, volumes):
        bin_idx = int((c - min_price) / bin_size)
        if bin_idx == bins:
            bin_idx -= 1
        bin_price = min_price + (bin_idx + 0.5) * bin_size
        profile[bin_price] += v
        
    if not profile:
        return None, None, None
        
    poc = max(profile.items(), key=lambda x: x[1])[0]
    
    # Calculate Value Area (70% of total volume)
    total_vol = sum(profile.values())
    target_vol = total_vol * 0.7
    
    sorted_prices = sorted(profile.keys())
    poc_idx = sorted_prices.index(poc)
    
    current_vol = profile[poc]
    low_idx = poc_idx
    high_idx = poc_idx
    
    while current_vol < target_vol and (low_idx > 0 or high_idx < len(sorted_prices) - 1):
        vol_below = profile[sorted_prices[low_idx - 1]] if low_idx > 0 else -1
        vol_above = profile[sorted_prices[high_idx + 1]] if high_idx < len(sorted_prices) - 1 else -1
        
        if vol_below >= vol_above and vol_below != -1:
            low_idx -= 1
            current_vol += vol_below
        elif vol_above > vol_below and vol_above != -1:
            high_idx += 1
            current_vol += vol_above
        else:
            break
            
    val = sorted_prices[low_idx]
    vah = sorted_prices[high_idx]
    
    return round(poc, 2), round(vah, 2), round(val, 2)


def compute_liquidity_sweeps(highs, lows, closes, lookback=20):
    """
    Detect if the price recently swept liquidity (took out a recent swing high/low and immediately reversed).
    Returns string: 'BULLISH_SWEEP' (swept lows and recovered), 'BEARISH_SWEEP' (swept highs and rejected), or 'NONE'.
    """
    if len(closes) < lookback + 5:
        return 'NONE'
        
    # Find recent swing high/low in the lookback window (excluding the last 3 days)
    recent_highs = highs[-(lookback+3):-3]
    recent_lows = lows[-(lookback+3):-3]
    
    swing_high = max(recent_highs)
    swing_low = min(recent_lows)
    
    last_3_highs = highs[-3:]
    last_3_lows = lows[-3:]
    
    if min(last_3_lows) < swing_low and closes[-1] > swing_low:
        return 'BULLISH_SWEEP'
        
    if max(last_3_highs) > swing_high and closes[-1] < swing_high:
        return 'BEARISH_SWEEP'
        
    return 'NONE'


# =========================================================
# MAIN CONTEXT BUILDER
# =========================================================

def get_stock_technical_context(ticker, lookback_days=90):
    """
    Fetch comprehensive technical context for a stock ticker using
    advanced quant-grade indicators.
    Returns a dict with all technical indicators, or None if data unavailable.
    """
    try:
        if not ticker.endswith('.NS') and not ticker.endswith('.BO'):
            ticker += '.NS'

        stock = yf.Ticker(ticker)
        hist = stock.history(period=f"{lookback_days}d")

        if hist.empty or len(hist) < 30:
            return None

        closes = hist['Close'].tolist()
        highs = hist['High'].tolist()
        lows = hist['Low'].tolist()
        volumes = hist['Volume'].tolist()
        current_price = round(closes[-1], 2)

        # ── Price Returns ──
        ret_1d = round(((closes[-1] - closes[-2]) / closes[-2]) * 100, 2) if len(closes) >= 2 else 0
        ret_5d = round(((closes[-1] - closes[-6]) / closes[-6]) * 100, 2) if len(closes) >= 6 else 0
        ret_20d = round(((closes[-1] - closes[-21]) / closes[-21]) * 100, 2) if len(closes) >= 21 else 0

        # ── 52-week High/Low (using available data) ──
        high_52w = round(max(highs), 2)
        low_52w = round(min(lows), 2)
        range_52w = high_52w - low_52w
        pct_from_high = round(((current_price - high_52w) / high_52w) * 100, 2) if high_52w > 0 else 0
        pct_from_low = round(((current_price - low_52w) / low_52w) * 100, 2) if low_52w > 0 else 0
        range_position = round((current_price - low_52w) / range_52w, 2) if range_52w > 0 else 0.5

        # ── EMAs (9, 21, 50) ──
        ema_9 = compute_ema(closes, 9)
        ema_21 = compute_ema(closes, 21)
        ema_50 = compute_ema(closes, min(50, len(closes)))

        # ── EMA Alignment Score ──
        # Perfect alignment: Price > EMA9 > EMA21 > EMA50 (bullish) or reverse (bearish)
        ema_alignment = 'UNKNOWN'
        if ema_9 and ema_21 and ema_50:
            if current_price > ema_9 > ema_21 > ema_50:
                ema_alignment = 'PERFECT_BULLISH'
            elif current_price > ema_9 and ema_9 > ema_21:
                ema_alignment = 'BULLISH'
            elif current_price < ema_9 < ema_21 < ema_50:
                ema_alignment = 'PERFECT_BEARISH'
            elif current_price < ema_9 and ema_9 < ema_21:
                ema_alignment = 'BEARISH'
            else:
                ema_alignment = 'MIXED'

        # ── RSI + Stochastic RSI ──
        rsi = compute_rsi(closes, 14)
        stoch_rsi_k, stoch_rsi_d = compute_stochastic_rsi(closes)

        # ── MACD ──
        macd_line, macd_signal, macd_histogram = compute_macd(closes)
        macd_crossover = 'NONE'
        if macd_line is not None and macd_signal is not None:
            if macd_line > macd_signal and macd_histogram and macd_histogram > 0:
                macd_crossover = 'BULLISH_CROSSOVER'
            elif macd_line < macd_signal and macd_histogram and macd_histogram < 0:
                macd_crossover = 'BEARISH_CROSSOVER'

        # ── ADX (Trend Strength) ──
        adx = compute_adx(highs, lows, closes)
        trend_strength = 'UNKNOWN'
        if adx is not None:
            if adx >= 40:
                trend_strength = 'VERY_STRONG_TREND'
            elif adx >= 25:
                trend_strength = 'STRONG_TREND'
            elif adx >= 20:
                trend_strength = 'MODERATE_TREND'
            else:
                trend_strength = 'WEAK_NO_TREND'

        # ── ATR (Volatility) ──
        atr = compute_atr(highs, lows, closes)
        atr_pct = round((atr / current_price) * 100, 2) if atr and current_price > 0 else None

        # ── Bollinger Bands %B + Bandwidth ──
        bb_percent_b, bb_bandwidth = compute_bollinger(closes)
        bb_squeeze = bb_bandwidth is not None and bb_bandwidth < 0.04  # tight squeeze

        # ── OBV Trend ──
        obv_trend = compute_obv_trend(closes, volumes)

        # ── VWAP (rolling 20-day) ──
        vwap = compute_vwap(highs[-20:], lows[-20:], closes[-20:], volumes[-20:])
        above_vwap = current_price > vwap if vwap else None

        # ── Anchored VWAP (recent 5-bar) ──
        # Acts as a "news-event anchor" proxy. When a fresh catalyst lands,
        # institutions watch this short-window VWAP to gauge whether the
        # post-event order flow is sustaining the move.
        anchored_vwap_5 = compute_anchored_vwap(highs, lows, closes, volumes, 5)
        above_anchored_vwap = (current_price > anchored_vwap_5) if anchored_vwap_5 else None

        # ── TTM Squeeze ──
        # Bollinger Bands inside Keltner Channels = volatility contraction.
        # When BB widens back outside KC, the squeeze "fires" — historically a
        # premier directional trigger.
        ttm = compute_ttm_squeeze(highs, lows, closes)
        ttm_in_squeeze = ttm.get('in_squeeze', False)
        ttm_release = ttm.get('squeeze_release')  # 'BULLISH' / 'BEARISH' / None
        ttm_momentum = ttm.get('momentum', 0.0)

        # ── OI Build-up (F&O stocks only) ──
        # Currently returns 'UNKNOWN' — placeholder until NSE F&O data wired.
        # The score model treats 'UNKNOWN' as neutral so this is safe to ship.
        oi_buildup = get_oi_buildup(ticker)

        # ── Fibonacci Retracement ──
        fib_levels = compute_fibonacci_levels(highs, lows, lookback=60)
        fib_position = compute_fibonacci_position(current_price, fib_levels)

        # ── Volume Profile & POC ──
        poc, vah, val = compute_volume_profile(closes[-60:], volumes[-60:])
        
        # ── Liquidity Sweeps ──
        liquidity_sweep = compute_liquidity_sweeps(highs, lows, closes)

        # ── Volume Analysis ──
        avg_volume_20d = round(sum(volumes[-20:]) / 20) if len(volumes) >= 20 else round(sum(volumes) / len(volumes))
        latest_volume = volumes[-1]
        volume_ratio = round(latest_volume / avg_volume_20d, 2) if avg_volume_20d > 0 else 1.0

        # ── Composite Trend Determination ──
        if ema_alignment in ('PERFECT_BULLISH', 'BULLISH') and adx and adx >= 25:
            trend = 'STRONG_UPTREND'
        elif ema_alignment in ('PERFECT_BULLISH', 'BULLISH'):
            trend = 'UPTREND'
        elif ema_alignment in ('PERFECT_BEARISH', 'BEARISH') and adx and adx >= 25:
            trend = 'STRONG_DOWNTREND'
        elif ema_alignment in ('PERFECT_BEARISH', 'BEARISH'):
            trend = 'DOWNTREND'
        elif adx and adx < 20:
            trend = 'SIDEWAYS'
        else:
            trend = 'MIXED'

        # ── Momentum Signal (composite) ──
        if rsi is not None:
            if rsi > 75 and stoch_rsi_k and stoch_rsi_k > 80:
                momentum_signal = 'EXTREME_OVERBOUGHT'
            elif rsi > 65:
                momentum_signal = 'OVERBOUGHT'
            elif rsi > 55:
                momentum_signal = 'BULLISH_MOMENTUM'
            elif rsi < 25 and stoch_rsi_k and stoch_rsi_k < 20:
                momentum_signal = 'EXTREME_OVERSOLD'
            elif rsi < 35:
                momentum_signal = 'OVERSOLD'
            elif rsi < 45:
                momentum_signal = 'BEARISH_MOMENTUM'
            else:
                momentum_signal = 'NEUTRAL'
        else:
            momentum_signal = 'UNKNOWN'

        return {
            "ticker": ticker,
            "current_price": current_price,
            # Returns
            "return_1d_pct": ret_1d,
            "return_5d_pct": ret_5d,
            "return_20d_pct": ret_20d,
            # EMAs
            "ema_9": ema_9,
            "ema_21": ema_21,
            "ema_50": ema_50,
            "ema_alignment": ema_alignment,
            # RSI + Stochastic RSI
            "rsi_14": rsi,
            "stoch_rsi_k": stoch_rsi_k,
            "stoch_rsi_d": stoch_rsi_d,
            # MACD
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_histogram": macd_histogram,
            "macd_crossover": macd_crossover,
            # ADX
            "adx": adx,
            "trend_strength": trend_strength,
            # ATR
            "atr": atr,
            "atr_pct": atr_pct,
            # Bollinger
            "bb_percent_b": bb_percent_b,
            "bb_bandwidth": bb_bandwidth,
            "bb_squeeze": bb_squeeze,
            # OBV
            "obv_trend": obv_trend,
            # VWAP
            "vwap": vwap,
            "above_vwap": above_vwap,
            "anchored_vwap_5": anchored_vwap_5,
            "above_anchored_vwap": above_anchored_vwap,
            # TTM Squeeze
            "ttm_in_squeeze": ttm_in_squeeze,
            "ttm_release": ttm_release,
            "ttm_momentum": ttm_momentum,
            # F&O OI Build-up
            "oi_buildup": oi_buildup,
            # Fibonacci
            "fib_levels": fib_levels,
            "fib_position": fib_position,
            # Volume Profile
            "vp_poc": poc,
            "vp_vah": vah,
            "vp_val": val,
            # Liquidity
            "liquidity_sweep": liquidity_sweep,
            # Volume
            "volume_ratio_vs_20d_avg": volume_ratio,
            # 52-week
            "high_52w": high_52w,
            "low_52w": low_52w,
            "pct_from_52w_high": pct_from_high,
            "pct_from_52w_low": pct_from_low,
            "range_position_52w": range_position,
            # Composite
            "trend": trend,
            "momentum_signal": momentum_signal,
        }
    except Exception as e:
        return None


def get_market_regime():
    """
    Determine overall market regime using NIFTY 50 with
    ADX + MACD + breadth analysis.
    Returns: 'RISK_ON', 'RISK_OFF', or 'NEUTRAL'
    """
    try:
        nifty = yf.Ticker("^NSEI")
        hist = nifty.history(period="60d")
        if hist.empty or len(hist) < 30:
            return "UNKNOWN"

        closes = hist['Close'].tolist()
        highs = hist['High'].tolist()
        lows = hist['Low'].tolist()

        ret_5d = ((closes[-1] - closes[-6]) / closes[-6]) * 100 if len(closes) >= 6 else 0
        ret_20d = ((closes[-1] - closes[-21]) / closes[-21]) * 100 if len(closes) >= 21 else 0

        rsi = compute_rsi(closes, 14)
        adx = compute_adx(highs, lows, closes)
        macd_line, macd_signal, macd_hist = compute_macd(closes)

        # Scoring system for regime
        score = 0

        # Short-term momentum
        if ret_5d > 2:
            score += 2
        elif ret_5d > 0.5:
            score += 1
        elif ret_5d < -2:
            score -= 2
        elif ret_5d < -0.5:
            score -= 1

        # Medium-term momentum
        if ret_20d > 3:
            score += 2
        elif ret_20d > 0:
            score += 1
        elif ret_20d < -3:
            score -= 2
        elif ret_20d < 0:
            score -= 1

        # RSI
        if rsi and rsi > 60:
            score += 1
        elif rsi and rsi < 40:
            score -= 1

        # MACD
        if macd_hist and macd_hist > 0:
            score += 1
        elif macd_hist and macd_hist < 0:
            score -= 1

        # ADX trending confirmation
        if adx and adx > 25:
            # ADX amplifies the direction
            if score > 0:
                score += 1
            elif score < 0:
                score -= 1

        if score >= 3:
            return "RISK_ON"
        elif score <= -3:
            return "RISK_OFF"
        else:
            return "NEUTRAL"
    except:
        return "UNKNOWN"


def format_technical_context_for_prompt(tech_data):
    """
    Format technical data into a concise string for inclusion in the AI prompt.
    Uses advanced quant indicators.
    """
    if tech_data is None:
        return "Technical data unavailable."

    lines = [
        f"Ticker: {tech_data['ticker']}",
        f"Current Price: ₹{tech_data['current_price']}",
        f"Returns: 1D={tech_data['return_1d_pct']}% | 5D={tech_data['return_5d_pct']}% | 20D={tech_data['return_20d_pct']}%",
        f"EMAs: 9={tech_data['ema_9']} | 21={tech_data['ema_21']} | 50={tech_data['ema_50']} | Alignment={tech_data['ema_alignment']}",
        f"RSI(14): {tech_data['rsi_14']} | StochRSI K={tech_data['stoch_rsi_k']} D={tech_data['stoch_rsi_d']}",
        f"MACD: Line={tech_data['macd_line']} Signal={tech_data['macd_signal']} Hist={tech_data['macd_histogram']} ({tech_data['macd_crossover']})",
        f"ADX: {tech_data['adx']} ({tech_data['trend_strength']})",
        f"ATR: {tech_data['atr']} ({tech_data['atr_pct']}% of price)",
        f"Bollinger: %B={tech_data['bb_percent_b']} Bandwidth={tech_data['bb_bandwidth']} {'⚡SQUEEZE' if tech_data['bb_squeeze'] else ''}",
        f"OBV Trend: {tech_data['obv_trend']} | VWAP: ₹{tech_data['vwap']} (Price {'above' if tech_data['above_vwap'] else 'below'})",
        f"Anchored VWAP (5-bar): ₹{tech_data.get('anchored_vwap_5')} (Price {'above' if tech_data.get('above_anchored_vwap') else 'below'})",
        f"TTM Squeeze: {'IN_SQUEEZE' if tech_data.get('ttm_in_squeeze') else 'NO_SQUEEZE'}" + (f" → FIRING {tech_data['ttm_release']}" if tech_data.get('ttm_release') else "") + f" (momentum={tech_data.get('ttm_momentum', 0)})",
        f"OI Buildup (F&O): {tech_data.get('oi_buildup', 'UNKNOWN')}",
        f"Volume Profile: POC=₹{tech_data.get('vp_poc')} | VAH=₹{tech_data.get('vp_vah')} | VAL=₹{tech_data.get('vp_val')}",
        f"Liquidity Sweep: {tech_data.get('liquidity_sweep', 'NONE')}",
        f"Fibonacci Position: {tech_data['fib_position']}",
        f"52W Range: ₹{tech_data['low_52w']} - ₹{tech_data['high_52w']} | Position: {tech_data['range_position_52w']}",
        f"Volume vs 20D Avg: {tech_data['volume_ratio_vs_20d_avg']}x",
        f"Trend: {tech_data['trend']} | Momentum: {tech_data['momentum_signal']}"
    ]
    return "\n".join(lines)


def get_batch_technical_context(tickers, lookback_days=90):
    """Fetch technical context for multiple tickers at once."""
    results = {}
    for ticker in tickers:
        results[ticker] = get_stock_technical_context(ticker, lookback_days)
    return results
