"""
MacroDataTracker — live commodity / FX / rates snapshot via Yahoo Finance's
free public chart endpoint (Brent, WTI, Gold, DXY, USD/INR, VIX, Nifty, US10Y,
...). Extracted verbatim from app.py.

Self-contained: stdlib (time, threading, concurrent.futures) + its own requests
session, with all caching held at class level (cls._cache, 5-min TTL). No app
import -> no cycle. app.py imports the class back and calls it class-level
(MacroDataTracker.get_snapshot() / .detect_shocks()).
"""
import time
import threading
import concurrent.futures
import requests

# Dedicated session (app.py keeps its own HTTP_SESSION for other callers).
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})


class MacroDataTracker:
    INSTRUMENTS = {
        'brent':     {'symbol': 'BZ=F',      'label': 'Brent Crude'},
        'wti':       {'symbol': 'CL=F',      'label': 'WTI Crude'},
        'natgas':    {'symbol': 'NG=F',      'label': 'Natural Gas'},
        'gold':      {'symbol': 'GC=F',      'label': 'Gold'},
        'silver':    {'symbol': 'SI=F',      'label': 'Silver'},
        'copper':    {'symbol': 'HG=F',      'label': 'Copper'},
        'dxy':       {'symbol': 'DX-Y.NYB',  'label': 'Dollar Index'},
        'usdinr':    {'symbol': 'INR=X',     'label': 'USD/INR'},
        'vix_us':    {'symbol': '^VIX',      'label': 'US VIX'},
        'vix_in':    {'symbol': '^INDIAVIX', 'label': 'India VIX'},
        'nifty':     {'symbol': '^NSEI',     'label': 'Nifty 50'},
        'banknifty': {'symbol': '^NSEBANK',  'label': 'Bank Nifty'},
        'us10y':     {'symbol': '^TNX',      'label': 'US 10Y Yield'},
    }

    # Per-instrument shock thresholds (% absolute 1-day move). These are
    # calibrated to "rare enough that it matters" for each asset class.
    # When the actual % move >= the MAJOR threshold, the macro detector
    # flags a "MAJOR" event. When it's between SIGNIFICANT and MAJOR, it's
    # a "SIGNIFICANT" event. Below SIGNIFICANT = ignore.
    # No keyword matching — purely quantitative on real prices.
    SHOCK_THRESHOLDS = {
        # Commodities — high baseline vol → tighter thresholds need 3-5%
        'brent':     {'significant': 3.0, 'major': 5.0},
        'wti':       {'significant': 3.0, 'major': 5.0},
        'natgas':    {'significant': 4.0, 'major': 7.0},
        'gold':      {'significant': 1.5, 'major': 3.0},
        'silver':    {'significant': 2.5, 'major': 5.0},
        'copper':    {'significant': 2.0, 'major': 4.0},
        # FX — low daily vol → tighter cutoffs
        'dxy':       {'significant': 0.8, 'major': 1.5},
        'usdinr':    {'significant': 0.5, 'major': 1.0},
        # Vol indices — usually move a lot when they move
        'vix_us':    {'significant': 12.0, 'major': 20.0},
        'vix_in':    {'significant': 10.0, 'major': 18.0},
        # Equity indices
        'nifty':     {'significant': 1.5, 'major': 2.5},
        'banknifty': {'significant': 1.8, 'major': 3.0},
        # Rates
        'us10y':     {'significant': 5.0, 'major': 8.0},
    }

    _cache = {}
    _cache_time = 0.0
    _CACHE_TTL = 300  # 5 minutes
    _lock = threading.Lock()

    @classmethod
    def _fetch_one(cls, key, meta):
        """Single instrument fetch via Yahoo's free chart endpoint."""
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{meta['symbol']}?range=5d&interval=1d"
            resp = HTTP_SESSION.get(url, timeout=4)
            if resp.status_code != 200:
                return None
            data = resp.json()
            result = (data.get('chart') or {}).get('result') or [{}]
            meta_data = (result[0] or {}).get('meta') or {}
            last = meta_data.get('regularMarketPrice')
            prev = meta_data.get('chartPreviousClose') or meta_data.get('previousClose')
            if last is None or prev is None or float(prev) == 0:
                return None
            last_f = float(last); prev_f = float(prev)
            pct = (last_f - prev_f) / prev_f * 100.0
            return {
                'key':            key,
                'symbol':         meta['symbol'],
                'label':          meta['label'],
                'last':           round(last_f, 4),
                'prev_close':     round(prev_f, 4),
                'change_pct_1d':  round(pct, 2),
                'is_shock_3pct':  abs(pct) >= 3.0,
                'is_shock_5pct':  abs(pct) >= 5.0,
            }
        except Exception:
            return None

    @classmethod
    def get_snapshot(cls):
        now = time.time()
        with cls._lock:
            if cls._cache and (now - cls._cache_time) < cls._CACHE_TTL:
                return cls._cache
        # Parallel fetch — 13 small HTTP calls. 4 workers keeps memory low
        # while still being ~3x faster than serial.
        snap = {}
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(cls._fetch_one, k, m): k
                           for k, m in cls.INSTRUMENTS.items()}
                for fut in concurrent.futures.as_completed(futures, timeout=15):
                    try:
                        result = fut.result(timeout=1)
                        if result:
                            snap[result['key']] = result
                    except Exception:
                        pass
        except Exception:
            pass
        with cls._lock:
            cls._cache = snap
            cls._cache_time = now
        return snap

    @classmethod
    def classify_shock(cls, instrument):
        """
        Given a snapshot row, returns:
          ('MAJOR' / 'SIGNIFICANT' / None, threshold_pct or None)
        Compared against SHOCK_THRESHOLDS specific to the instrument.
        Purely quantitative — no news, no keywords.
        """
        if not instrument or 'change_pct_1d' not in instrument:
            return (None, None)
        key = instrument.get('key')
        thr = cls.SHOCK_THRESHOLDS.get(key)
        if not thr:
            return (None, None)
        move = abs(instrument['change_pct_1d'])
        if move >= thr['major']:
            return ('MAJOR', thr['major'])
        if move >= thr['significant']:
            return ('SIGNIFICANT', thr['significant'])
        return (None, None)

    @classmethod
    def detect_shocks(cls):
        """
        Return current snapshot enriched with shock classification.
        Each item gets a `shock_level` field: 'MAJOR' / 'SIGNIFICANT' / None.
        Only items where level != None are returned, sorted by severity.
        """
        snap = cls.get_snapshot()
        out = []
        for inst in snap.values():
            level, threshold = cls.classify_shock(inst)
            if not level:
                continue
            row = dict(inst)
            row['shock_level'] = level
            row['threshold_pct'] = threshold
            out.append(row)
        # MAJOR first, then SIGNIFICANT; within each, biggest move first
        out.sort(key=lambda r: (0 if r['shock_level'] == 'MAJOR' else 1,
                                -abs(r['change_pct_1d'])))
        return out
