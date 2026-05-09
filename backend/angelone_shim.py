"""
Angel One SmartAPI Shim — Drop-in replacement for yfinance_twelvedata_shim.py

Provides identical interface: Ticker, FastInfo, download(), set_tz_cache_location()
Uses Angel One SmartAPI for all price & OHLC data.
Falls back to Yahoo Finance direct API if Angel One session is unavailable.
"""

import pandas as pd
import requests
import logging
import json
import time
import threading
import os
import socket
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

logger = logging.getLogger('angelone_shim')
logger.disabled = True
logger.propagate = False

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# ── Credentials ────────────────────────────────────────────────────────────────
AO_API_KEY     = os.environ.get("ANGELONE_API_KEY",     "Q3NL2DKd")
AO_CLIENT_ID   = os.environ.get("ANGELONE_CLIENT_ID",  "V60419002")
AO_PIN         = os.environ.get("ANGELONE_PIN",         "1729")
AO_TOTP_SECRET = os.environ.get("ANGELONE_TOTP_SECRET","IPDPRMRWSHYLQTMF7Y4MQLTXE4")

AO_BASE_URL = "https://apiconnect.angelone.in"

# ── Session State ──────────────────────────────────────────────────────────────
_jwt_token   = None
_session_date = None        # date of last successful auth (UTC)
_session_lock = threading.Lock()

# ── Scrip Master Cache ─────────────────────────────────────────────────────────
_scrip_cache  = {}          # "SYMBOL" -> token string (NSE EQ)
_bse_cache    = {}          # "SYMBOL" -> token string (BSE EQ)
_scrip_loaded = False
_scrip_lock   = threading.Lock()

_SCRIP_URL = (
    "https://margincalculator.angelbroking.com/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)

# ── Known Index Tokens ─────────────────────────────────────────────────────────
_INDEX_MAP = {
    "^NSEI":    ("NSE", "26000"),   # NIFTY 50
    "^BSESN":   ("BSE", "1"),       # SENSEX
    "^NSEBANK": ("NSE", "26009"),   # BANK NIFTY
    "^NSMIDCP": ("NSE", "26074"),   # NIFTY MIDCAP 50
}

# ── Quote Cache (30s TTL) ──────────────────────────────────────────────────────
# Stores full quote dicts keyed by ticker, avoids redundant API calls
_QUOTE_CACHE = {}       # ticker -> {"ltp", "prev", "high", "low", "ts"}
_QUOTE_CACHE_TTL = 30   # seconds

# ── Yahoo Finance Fallback ─────────────────────────────────────────────────────
_YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def set_tz_cache_location(*args, **kwargs):
    """No-op — compatibility stub for yfinance API."""
    pass


# ── Yahoo Finance helpers (fallback) ──────────────────────────────────────────
def _yahoo_get_quote(ticker):
    try:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{ticker}?range=5d&interval=1d"
        )
        resp = requests.get(url, headers=_YF_HEADERS, timeout=8)
        data = resp.json()
        result = data.get('chart', {}).get('result', [{}])[0]
        meta   = result.get('meta', {})
        lp = meta.get('regularMarketPrice') or meta.get('previousClose')
        
        quotes = result.get('indicators', {}).get('quote', [{}])[0]
        closes = [c for c in quotes.get('close', []) if c is not None]
        
        pc = None
        if closes and len(closes) >= 2:
            pc = closes[-2]
            
        if not lp:
            if closes:
                lp = closes[-1]
                
        lp = float(lp) if lp else 0.0
        pc = float(pc) if pc else lp
        return lp, pc
    except Exception:
        return 0.0, 0.0


def _yahoo_get_history(ticker, period='5d', interval='1d'):
    period_map = {
        '1d': '1d', '2d': '5d', '5d': '5d', '7d': '1mo',
        '14d': '1mo', '30d': '3mo', '60d': '6mo', '90d': '6mo',
        '1mo': '3mo', '3mo': '6mo',
    }
    yf_range = period_map.get(period, '1mo')
    try:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{ticker}?range={yf_range}&interval={interval}"
        )
        resp = requests.get(url, headers=_YF_HEADERS, timeout=12)
        data = resp.json()
        result     = data.get('chart', {}).get('result', [{}])[0]
        timestamps = result.get('timestamp', [])
        quotes     = result.get('indicators', {}).get('quote', [{}])[0]
        if not timestamps or not quotes:
            return pd.DataFrame()
        df = pd.DataFrame({
            'Open':   quotes.get('open',   []),
            'High':   quotes.get('high',   []),
            'Low':    quotes.get('low',    []),
            'Close':  quotes.get('close',  []),
            'Volume': quotes.get('volume', []),
        }, index=pd.to_datetime(timestamps, unit='s', utc=True))
        df = df.dropna(subset=['Close'])
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if col in df.columns:
                df[col] = df[col].astype(float)
        return df
    except Exception:
        return pd.DataFrame()


# ── Angel One Authentication ───────────────────────────────────────────────────
def _get_local_ip():
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def _ao_base_headers():
    return {
        "Content-Type":       "application/json",
        "Accept":             "application/json",
        "X-UserType":         "USER",
        "X-SourceID":         "WEB",
        "X-ClientLocalIP":    _get_local_ip(),
        "X-ClientPublicIP":   _get_local_ip(),
        "X-MACAddress":       "00:00:00:00:00:00",
        "X-PrivateKey":       AO_API_KEY,
    }


def _ao_login():
    """Authenticate with Angel One SmartAPI using pyotp TOTP."""
    global _jwt_token, _session_date
    try:
        import pyotp
        totp_code = pyotp.TOTP(AO_TOTP_SECRET).now()
        payload = {
            "clientcode": AO_CLIENT_ID,
            "password":   AO_PIN,
            "totp":       totp_code,
        }
        resp = requests.post(
            f"{AO_BASE_URL}/rest/auth/angelbroking/user/v1/loginByPassword",
            headers=_ao_base_headers(),
            json=payload,
            timeout=12,
        )
        data = resp.json()
        if data.get("status") and data.get("data"):
            _jwt_token    = data["data"]["jwtToken"]
            _session_date = datetime.now(timezone.utc).date()
            print(f"[AngelOne] Session established for {AO_CLIENT_ID}")
            return True
        else:
            print(f"[AngelOne] Login failed: {data.get('message', 'unknown')}")
            return False
    except Exception as e:
        print(f"[AngelOne] Login exception: {e}")
        return False


def _ensure_session():
    """Ensure a valid Angel One session, re-authenticating daily."""
    global _jwt_token, _session_date
    with _session_lock:
        today = datetime.now(timezone.utc).date()
        if _jwt_token is None or _session_date != today:
            return _ao_login()
        return True


def _ao_auth_headers():
    return {**_ao_base_headers(), "Authorization": f"Bearer {_jwt_token}"}


# ── Scrip Master ──────────────────────────────────────────────────────────────
def _load_scrip_master():
    """Download & cache Angel One scrip master (NSE + BSE EQ symbols → tokens)."""
    global _scrip_cache, _bse_cache, _scrip_loaded
    with _scrip_lock:
        if _scrip_loaded:
            return
        try:
            resp = requests.get(_SCRIP_URL, timeout=20)
            data = resp.json()
            nse_count = 0
            for entry in data:
                seg   = entry.get("exch_seg", "")
                sym   = entry.get("symbol", "")
                token = entry.get("token", "")
                if not token or not sym:
                    continue
                # NSE equities: instrumenttype="" and symbol ends with "-EQ"
                if seg == "NSE" and sym.endswith("-EQ"):
                    _scrip_cache[sym[:-3]] = token
                    nse_count += 1
                elif seg == "BSE" and sym.endswith("-EQ"):
                    _bse_cache[sym[:-3]] = token
            _scrip_loaded = True
            print(f"[AngelOne] Scrip master loaded: {nse_count} NSE EQ symbols")
        except Exception as e:
            print(f"[AngelOne] Scrip master load failed: {e} (Yahoo fallback active)")


def _get_exchange_token(ticker):
    """
    Convert yfinance-style ticker (e.g. RELIANCE.NS, ^NSEI) to
    (exchange, token) for Angel One API calls.
    Returns (None, None) if lookup fails.
    """
    # Indices first
    if ticker in _INDEX_MAP:
        return _INDEX_MAP[ticker]

    _load_scrip_master()

    if ticker.endswith(".NS"):
        sym   = ticker[:-3]
        token = _scrip_cache.get(sym)
        if token:
            return ("NSE", token)
    elif ticker.endswith(".BO"):
        sym   = ticker[:-3]
        token = _bse_cache.get(sym)
        if token:
            return ("BSE", token)

    return None, None


# ── Angel One Market Data ──────────────────────────────────────────────────────
def _ao_get_full_quote(exchange, token):
    """
    Fetch FULL quote from Angel One (ltp, prevClose, dayHigh, dayLow).
    Returns raw dict or None.
    """
    try:
        if not _ensure_session():
            return None
        payload = {
            "mode": "FULL",
            "exchangeTokens": {exchange: [token]},
        }
        resp = requests.post(
            f"{AO_BASE_URL}/rest/secure/angelbroking/market/v1/quote/",
            headers=_ao_auth_headers(),
            json=payload,
            timeout=8,
        )
        data = resp.json()
        if data.get("status") and data.get("data", {}).get("fetched"):
            return data["data"]["fetched"][0]
    except Exception as e:
        print(f"[AngelOne] Quote error for ({exchange},{token}): {e}")
    return None


def _ao_get_candle(exchange, token, ao_interval, from_dt, to_dt):
    """
    Fetch OHLCV candles from Angel One Historical Data API.
    Returns a pandas DataFrame indexed by UTC datetime (or empty DF on failure).
    """
    try:
        if not _ensure_session():
            return pd.DataFrame()

        IST = timezone(timedelta(hours=5, minutes=30))
        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=timezone.utc)
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=timezone.utc)

        from_str = from_dt.astimezone(IST).strftime("%Y-%m-%d %H:%M")
        to_str   = to_dt.astimezone(IST).strftime("%Y-%m-%d %H:%M")

        payload = {
            "exchange":    exchange,
            "symboltoken": token,
            "interval":    ao_interval,
            "fromdate":    from_str,
            "todate":      to_str,
        }
        resp = requests.post(
            f"{AO_BASE_URL}/rest/secure/angelbroking/historical/v1/getCandleData",
            headers=_ao_auth_headers(),
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if not data.get("status") or not data.get("data"):
            return pd.DataFrame()

        rows = []
        for c in data["data"]:
            try:
                rows.append({
                    'datetime': pd.to_datetime(c[0], utc=True),
                    'Open':   float(c[1]),
                    'High':   float(c[2]),
                    'Low':    float(c[3]),
                    'Close':  float(c[4]),
                    'Volume': float(c[5]) if len(c) > 5 else 0.0,
                })
            except Exception:
                continue

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).set_index('datetime')
        df = df.dropna(subset=['Close'])
        return df

    except Exception as e:
        print(f"[AngelOne] Candle error ({exchange},{token}): {e}")
        return pd.DataFrame()


# ── Period / Interval Conversion ──────────────────────────────────────────────
_PERIOD_DAYS = {
    '1d': 1, '2d': 2, '5d': 5, '7d': 7, '10d': 10, '14d': 14,
    '30d': 30, '60d': 60, '90d': 90,
    '1mo': 31, '2mo': 62, '3mo': 92, '6mo': 184, '1y': 365,
}

_INTERVAL_MAP = {
    '1m':  'ONE_MINUTE',
    '3m':  'THREE_MINUTE',
    '5m':  'FIVE_MINUTE',
    '10m': 'TEN_MINUTE',
    '15m': 'FIFTEEN_MINUTE',
    '30m': 'THIRTY_MINUTE',
    '60m': 'ONE_HOUR',
    '1h':  'ONE_HOUR',
    '1d':  'ONE_DAY',
    '1wk': 'ONE_DAY',
}


def _period_to_dates(period):
    days = _PERIOD_DAYS.get(period, 30)
    now  = datetime.now(timezone.utc)
    return now - timedelta(days=days), now


def _yf_to_ao_interval(interval):
    return _INTERVAL_MAP.get(interval, 'ONE_DAY')


# ── Public Interface ───────────────────────────────────────────────────────────
class FastInfo:
    def __init__(self, last_price, previous_close, day_high, day_low):
        self.last_price     = last_price
        self.previous_close = previous_close
        self.day_high       = day_high
        self.day_low        = day_low


class Ticker:
    def __init__(self, ticker):
        self.ticker              = ticker
        self._exchange, self._token = _get_exchange_token(ticker)

    @property
    def fast_info(self):
        # Use shared quote cache — no extra API call if get_ltp already fetched
        ltp, prev, dh, dl = _get_cached_quote(self.ticker)
        return FastInfo(ltp, prev, dh, dl)

    def history(self, period="60d", interval="1d"):
        # ── Primary: Angel One candles ──
        if self._exchange and self._token:
            try:
                ao_interval  = _yf_to_ao_interval(interval)
                from_dt, to_dt = _period_to_dates(period)
                df = _ao_get_candle(
                    self._exchange, self._token, ao_interval, from_dt, to_dt
                )
                if not df.empty:
                    return df
            except Exception:
                pass

        # ── Fallback: Yahoo Finance ──
        return _yahoo_get_history(self.ticker, period=period, interval=interval)


# Compatibility alias
Ticker.FastInfo = FastInfo


def _get_cached_quote(ticker):
    """
    Returns cached (ltp, prev, high, low) or fetches from Angel One & caches.
    30-second TTL. Used by both get_ltp() and Ticker.fast_info.
    """
    now = time.time()
    cached = _QUOTE_CACHE.get(ticker)
    if cached and (now - cached["ts"]) < _QUOTE_CACHE_TTL:
        return cached["ltp"], cached["prev"], cached["high"], cached["low"]

    exchange, token = _get_exchange_token(ticker)
    if exchange and token:
        try:
            q = _ao_get_full_quote(exchange, token)
            if q:
                ltp  = float(q.get("ltp",  0.0))
                prev = float(q.get("close", ltp))
                dh   = float(q.get("high",  ltp))
                dl   = float(q.get("low",   ltp))
                if ltp > 0:
                    _QUOTE_CACHE[ticker] = {"ltp": ltp, "prev": prev, "high": dh, "low": dl, "ts": now}
                    return ltp, prev, dh, dl
        except Exception:
            pass

    # Yahoo fallback
    lp, pc = _yahoo_get_quote(ticker)
    if lp and lp > 0:
        _QUOTE_CACHE[ticker] = {"ltp": lp, "prev": pc, "high": lp, "low": lp, "ts": now}
    return lp, pc, lp, lp


def get_ltp(ticker):
    """
    Public helper -- returns (ltp, prev_close) for a yfinance-style ticker.
    Uses 30-second quote cache to avoid redundant API calls.
    """
    ltp, prev, _, _ = _get_cached_quote(ticker)
    return ltp, prev


def get_ohlc(ticker, days=14):
    """
    Public helper — returns list of (datetime_utc, high, low, close) tuples.
    Used by _fetch_ohlc_direct() replacement in app.py.
    """
    exchange, token = _get_exchange_token(ticker)
    if exchange and token:
        try:
            now     = datetime.now(timezone.utc)
            from_dt = now - timedelta(days=days)
            df      = _ao_get_candle(exchange, token, 'ONE_DAY', from_dt, now)
            if not df.empty:
                rows = []
                for dt_idx, row in df.iterrows():
                    try:
                        rows.append((
                            dt_idx if dt_idx.tzinfo else dt_idx.replace(tzinfo=timezone.utc),
                            float(row['High']),
                            float(row['Low']),
                            float(row['Close']),
                        ))
                    except Exception:
                        continue
                return rows
        except Exception:
            pass
    return []


def download(tickers, period="7d", interval="1m", progress=False, auto_adjust=True):
    """Mimics yf.download() for a single ticker."""
    ticker_str = tickers if isinstance(tickers, str) else tickers[0]
    return Ticker(ticker_str).history(period=period, interval=interval)


# ── Auto-initialize session on import ─────────────────────────────────────────
def _bg_init():
    """Boot Angel One session and scrip master in background thread."""
    _load_scrip_master()
    _ensure_session()

threading.Thread(target=_bg_init, daemon=True).start()
