"""
F&O Open Interest data fetcher for OI-buildup pattern detection.

Downloads the daily NSE F&O bhavcopy ONCE (cached for 4 hours), parses
it into a per-stock OI + price-change snapshot, and exposes
`get_oi_buildup_for_ticker(ticker)` to the technical model.

Bhavcopy format (NSE, 2024+):
  URL: https://archives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip
  Published ~7-8 PM IST on every trading day.

Patterns surfaced for each F&O-eligible stock (front-month futures):
  LONG_BUILDUP   — price up + OI up   (institutional longs adding)
  SHORT_COVERING — price up + OI down (forced buying, weaker)
  SHORT_BUILDUP  — price down + OI up (institutional shorts adding)
  LONG_UNWINDING — price down + OI down (profit-taking, weaker)
  NEUTRAL        — tiny price move
  NOT_FNO        — stock isn't in F&O segment
  UNKNOWN        — bhavcopy fetch failed (network/NSE-side)

Thread-safe (singleton cache + lock), failure-tolerant (UNKNOWN is the
default if anything goes wrong), and bandwidth-friendly (one fetch / 4h
serves the entire process).
"""
import csv as _csv
import io
import threading
import zipfile
from datetime import datetime, timedelta, timezone

import requests


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_IST = timezone(timedelta(hours=5, minutes=30))
_CACHE_TTL_HOURS = 4

# Module-level cache. Keys:
#   "data": dict[str, dict] — symbol -> {oi_total, oi_chg_total, px_chg_pct, front_xpry}
#   "date": date object — which trading day's bhavcopy this is from
#   "fetched_at": datetime UTC — when we put this in cache
_CACHE: dict = {"data": None, "date": None, "fetched_at": None}
_CACHE_LOCK = threading.Lock()


def _ist_now() -> datetime:
    return datetime.now(_IST)


def _last_likely_bhavcopy_date():
    """The most recent trading day for which the bhavcopy is likely published.

    NSE publishes daily F&O bhavcopy around 7-8 PM IST. Before that, we have
    to use yesterday's file. Rolls back through Sat/Sun automatically; we'll
    further fall back through the most recent N days if the chosen date 404s.
    """
    now = _ist_now()
    d = now.date()
    if now.hour < 19:
        d -= timedelta(days=1)
    while d.weekday() >= 5:  # Saturday=5, Sunday=6
        d -= timedelta(days=1)
    return d


def _fetch_bhavcopy(date_obj):
    """GET the bhavcopy ZIP for `date_obj` (a date). Returns CSV text or None."""
    ymd = date_obj.strftime("%Y%m%d")
    url = (
        f"https://archives.nseindia.com/content/fo/"
        f"BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"
    )
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        if resp.status_code != 200 or len(resp.content) < 1000:
            return None
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        for name in zf.namelist():
            if name.lower().endswith(".csv"):
                return zf.read(name).decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"[OI] bhavcopy fetch failed for {date_obj}: {exc}")
    return None


def _parse_bhavcopy(csv_text: str) -> dict:
    """Parse the bhavcopy CSV into per-stock OI summary.

    Aggregates OI across all expiries of stock futures (FinInstrmTp == 'STF').
    Captures the front-month (nearest expiry) close + previous close to derive
    the day's price change.
    """
    reader = _csv.DictReader(io.StringIO(csv_text))
    out: dict[str, dict] = {}

    for row in reader:
        if (row.get("FinInstrmTp") or "").strip() != "STF":
            continue
        sym = (row.get("TckrSymb") or "").strip().upper()
        if not sym:
            continue
        try:
            oi       = int(float(row.get("OpnIntrst") or 0))
            oi_chg   = int(float(row.get("ChngInOpnIntrst") or 0))
            cls_pric = float(row.get("ClsPric") or 0)
            prv_pric = float(row.get("PrvsClsgPric") or 0)
            xpry     = (row.get("XpryDt") or "").strip()
        except (ValueError, TypeError):
            continue

        entry = out.get(sym)
        if entry is None:
            entry = {
                "oi_total":     0,
                "oi_chg_total": 0,
                "front_close":  cls_pric,
                "front_prev":   prv_pric,
                "front_xpry":   xpry,
            }
            out[sym] = entry

        # OI aggregates across all stock-future expiries
        entry["oi_total"]     += oi
        entry["oi_chg_total"] += oi_chg

        # Track front (nearest) expiry for the price-change read
        if xpry and (not entry["front_xpry"] or xpry < entry["front_xpry"]):
            entry["front_close"] = cls_pric
            entry["front_prev"]  = prv_pric
            entry["front_xpry"]  = xpry

    # Derive percent price change per stock
    for entry in out.values():
        prv = entry["front_prev"]
        if prv > 0:
            entry["px_chg_pct"] = round(
                (entry["front_close"] - prv) / prv * 100, 3
            )
        else:
            entry["px_chg_pct"] = 0.0

    return out


def _ensure_cache() -> dict:
    """Return the per-stock OI dict, fetching if not cached or stale."""
    now_utc = datetime.now(timezone.utc)

    with _CACHE_LOCK:
        if _CACHE["data"] is not None and _CACHE["fetched_at"] is not None:
            age_s = (now_utc - _CACHE["fetched_at"]).total_seconds()
            if age_s < _CACHE_TTL_HOURS * 3600:
                return _CACHE["data"]

    # Try the last 7 candidate trading days in case of holidays / stale CDN
    target = _last_likely_bhavcopy_date()
    for back in range(7):
        d = target - timedelta(days=back)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        csv_text = _fetch_bhavcopy(d)
        if not csv_text:
            continue
        parsed = _parse_bhavcopy(csv_text)
        if parsed:
            with _CACHE_LOCK:
                _CACHE["data"]       = parsed
                _CACHE["date"]       = d
                _CACHE["fetched_at"] = now_utc
            print(f"[OI] Loaded F&O OI for {len(parsed)} stocks (bhavcopy {d})")
            return parsed

    # All candidates failed — cache a brief empty so we don't hammer NSE
    print("[OI] No bhavcopy reachable — OI features will return UNKNOWN")
    with _CACHE_LOCK:
        _CACHE["data"]       = {}
        _CACHE["date"]       = None
        _CACHE["fetched_at"] = now_utc
    return {}


# Tiny dead-zone so a 0.02% move isn't called "directional"
_PX_NEUTRAL_THRESHOLD = 0.05  # percent


def get_oi_buildup_for_ticker(ticker: str) -> str:
    """
    Returns one of:
      LONG_BUILDUP / SHORT_COVERING / SHORT_BUILDUP / LONG_UNWINDING
      NEUTRAL / NOT_FNO / UNKNOWN

    Safe — never raises. On any failure or unknown ticker, returns 'UNKNOWN' /
    'NOT_FNO', both of which the TechnicalAlignmentModel treats as neutral.
    """
    try:
        sym = (ticker or "").upper().replace(".NS", "").replace(".BO", "").strip()
        if not sym:
            return "UNKNOWN"

        data = _ensure_cache()
        if not data:
            return "UNKNOWN"

        entry = data.get(sym)
        if not entry:
            # NSE bhavcopy loaded fine but the symbol isn't there → not F&O
            return "NOT_FNO"

        px_chg = entry.get("px_chg_pct", 0.0)
        oi_chg = entry.get("oi_chg_total", 0)

        if abs(px_chg) < _PX_NEUTRAL_THRESHOLD:
            return "NEUTRAL"

        px_up = px_chg > 0
        oi_up = oi_chg > 0

        if px_up and oi_up:
            return "LONG_BUILDUP"
        if px_up and not oi_up:
            return "SHORT_COVERING"
        if not px_up and oi_up:
            return "SHORT_BUILDUP"
        return "LONG_UNWINDING"
    except Exception as exc:
        print(f"[OI] get_oi_buildup_for_ticker({ticker!r}) failed: {exc}")
        return "UNKNOWN"


def cache_status() -> dict:
    """Helpful introspection: how fresh is our OI snapshot?"""
    with _CACHE_LOCK:
        d        = _CACHE.get("data")
        date     = _CACHE.get("date")
        fetched  = _CACHE.get("fetched_at")
    return {
        "stocks_loaded": (len(d) if isinstance(d, dict) else 0),
        "bhavcopy_date": (date.isoformat() if date else None),
        "fetched_at":    (fetched.isoformat() if fetched else None),
        "age_seconds": (
            (datetime.now(timezone.utc) - fetched).total_seconds() if fetched else None
        ),
    }
