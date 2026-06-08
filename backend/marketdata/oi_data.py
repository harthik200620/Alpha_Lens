"""
F&O data fetcher — NSE daily derivatives bhavcopy parser.

Downloads the daily NSE F&O bhavcopy ONCE (cached for 4 hours) and parses it
into TWO things:

  1. Per-stock FUTURES OI + price-change snapshot  → OI-buildup pattern
     (the original purpose; feeds the TechnicalAlignmentModel).
  2. Per-symbol OPTIONS chain (CE/PE OI by strike, OI change, spot)
     → powers the F&O Smart-Money board (PCR, max-pain, OI walls).

Both come from the SAME already-downloaded file, so the richer options data
costs ZERO extra network calls.

Bhavcopy format (NSE UDiFF, 2024+):
  URL: https://archives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip
  Published ~7-8 PM IST on every trading day.
  FinInstrmTp:  STF (stock future) · STO (stock option)
                IDF (index future) · IDO (index option)
  Key cols:     TckrSymb, OptnTp(CE/PE), StrkPric, XpryDt, OpnIntrst,
                ChngInOpnIntrst, ClsPric, PrvsClsgPric, UndrlygPric,
                TtlTradgVol, TtlTrfVal

Futures buildup patterns (front-month):
  LONG_BUILDUP   — price up + OI up   (institutional longs adding)
  SHORT_COVERING — price up + OI down (forced buying, weaker)
  SHORT_BUILDUP  — price down + OI up (institutional shorts adding)
  LONG_UNWINDING — price down + OI down (profit-taking, weaker)
  NEUTRAL        — tiny price move
  NOT_FNO        — stock isn't in F&O segment
  UNKNOWN        — bhavcopy fetch failed (network/NSE-side)

NOTE on reachability: this hits archives.nseindia.com — the STATIC archive CDN,
which is a different host from the datacenter-IP-blocked api.nseindia.com. The
archive CDN has historically been reachable from the server, but the live record
parse should still be validated in production (see /api/debug-worker-status and
the [OI]/[FNO] worker logs). Every public function is failure-tolerant.

Thread-safe (singleton cache + lock), failure-tolerant, bandwidth-friendly (one
fetch / 4h serves the entire process).
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
#   "futures": dict[str, dict] — symbol -> futures summary
#   "options": dict[str, dict] — symbol -> {expiry, spot, is_index, strikes[]}
#   "date": date object — which trading day's bhavcopy this is from
#   "fetched_at": datetime UTC — when we put this in cache
_CACHE: dict = {"futures": None, "options": None, "date": None, "fetched_at": None}
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


def _f(row, key, default=0.0):
    """Tolerant float read from a (whitespace-stripped) row dict."""
    try:
        return float(row.get(key) or default)
    except (ValueError, TypeError):
        return default


def _i(row, key, default=0):
    try:
        return int(float(row.get(key) or default))
    except (ValueError, TypeError):
        return default


def _parse_bhavcopy_full(csv_text: str) -> dict:
    """Parse the bhavcopy CSV into per-symbol FUTURES + OPTIONS.

    Returns {"futures": {...}, "options": {...}}.

    futures[sym] = {oi_total, oi_chg_total, oi_chg_pct, px_chg_pct, front_close,
                    front_prev, front_xpry, vol, val_cr, is_index, fresh_oi,
                    front_oi, next_oi, next_close, next_xpry}   # last 4 → rollover
    options[sym] = {expiry, spot, is_index, ce_oi, pe_oi, ce_chg, pe_chg,
                    strikes: [{strike, ce_oi, pe_oi, ce_chg, pe_chg, ce_vol, pe_vol,
                               ce_ltp, pe_ltp, ce_settle, pe_settle}, ...]}
        ce_settle/pe_settle (SttlmPric) is the EOD mark used for IV; ce_ltp/pe_ltp
        (ClsPric) is the last traded price (0/stale on illiquid strikes).
    """
    reader = _csv.DictReader(io.StringIO(csv_text))

    # futures scratch: sym -> {xpry -> {oi, oi_chg, close, prev, vol, val}}
    fut_tmp: dict[str, dict] = {}
    fut_is_index: set = set()

    # options scratch: sym -> expiry -> strike -> cell
    opt_tmp: dict[str, dict] = {}
    opt_spot: dict[str, float] = {}
    opt_is_index: set = set()

    for raw in reader:
        # Normalize: strip whitespace from keys AND values (cash files have
        # leading-space headers; FO is clean but be defensive).
        row = {
            (k or "").strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in raw.items()
        }
        fit = (row.get("FinInstrmTp") or "").strip().upper()
        sym = (row.get("TckrSymb") or "").strip().upper()
        if not sym:
            continue

        # ── FUTURES (stock STF + index IDF) ──
        if fit in ("STF", "IDF"):
            xpry = (row.get("XpryDt") or "").strip()
            if not xpry:
                continue
            cell = (
                fut_tmp.setdefault(sym, {})
                       .setdefault(xpry, {"oi": 0, "oi_chg": 0, "close": 0.0,
                                          "prev": 0.0, "vol": 0, "val": 0.0})
            )
            cell["oi"] += _i(row, "OpnIntrst")
            cell["oi_chg"] += _i(row, "ChngInOpnIntrst")
            cell["close"] = _f(row, "ClsPric")          # one row per (sym, expiry)
            cell["prev"] = _f(row, "PrvsClsgPric")
            cell["vol"] += _i(row, "TtlTradgVol")
            cell["val"] += _f(row, "TtlTrfVal")
            if fit == "IDF":
                fut_is_index.add(sym)

        # ── OPTIONS (stock STO + index IDO) ──
        elif fit in ("STO", "IDO"):
            otp = (row.get("OptnTp") or "").strip().upper()
            if otp not in ("CE", "PE"):
                continue
            strike = _f(row, "StrkPric")
            xpry = (row.get("XpryDt") or "").strip()
            if strike <= 0 or not xpry:
                continue
            oi = _i(row, "OpnIntrst")
            oi_chg = _i(row, "ChngInOpnIntrst")
            vol = _i(row, "TtlTradgVol")
            ltp = _f(row, "ClsPric")
            settle = _f(row, "SttlmPric")
            spot = _f(row, "UndrlygPric")

            cell = (
                opt_tmp.setdefault(sym, {})
                       .setdefault(xpry, {})
                       .setdefault(strike, {
                           "ce_oi": 0, "pe_oi": 0, "ce_chg": 0, "pe_chg": 0,
                           "ce_vol": 0, "pe_vol": 0, "ce_ltp": 0.0, "pe_ltp": 0.0,
                           "ce_settle": 0.0, "pe_settle": 0.0,
                       })
            )
            if otp == "CE":
                cell["ce_oi"] += oi; cell["ce_chg"] += oi_chg; cell["ce_vol"] += vol
                cell["ce_ltp"] = ltp; cell["ce_settle"] = settle
            else:
                cell["pe_oi"] += oi; cell["pe_chg"] += oi_chg; cell["pe_vol"] += vol
                cell["pe_ltp"] = ltp; cell["pe_settle"] = settle
            if spot > 0:
                opt_spot[sym] = spot
            if fit == "IDO":
                opt_is_index.add(sym)

    # Finalize futures: aggregate OI across expiries, derive 1d change, rollover.
    futures: dict[str, dict] = {}
    for sym, by_exp in fut_tmp.items():
        if not by_exp:
            continue
        exps = sorted(by_exp.keys())          # ISO dates → chronological
        front_x = exps[0]
        next_x = exps[1] if len(exps) > 1 else None
        fcell = by_exp[front_x]
        oi_total = sum(c["oi"] for c in by_exp.values())
        oi_chg_total = sum(c["oi_chg"] for c in by_exp.values())
        vol = sum(c["vol"] for c in by_exp.values())
        val = sum(c["val"] for c in by_exp.values())
        prv = fcell["prev"]
        px_chg_pct = round((fcell["close"] - prv) / prv * 100, 3) if prv > 0 else 0.0
        prev_oi = oi_total - oi_chg_total
        # FIX: a brand-new / all-fresh contract (prev_oi <= 0 but OI added today) must
        # NOT read as 0% change — that hides the most aggressive positioning. Flag it
        # and give it a high (capped) surge value so it isn't filtered out.
        fresh_oi = prev_oi <= 0 and oi_chg_total > 0
        if prev_oi > 0:
            oi_chg_pct = round(oi_chg_total / prev_oi * 100, 2)
        elif fresh_oi:
            oi_chg_pct = 200.0
        else:
            oi_chg_pct = 0.0
        futures[sym] = {
            "oi_total": oi_total, "oi_chg_total": oi_chg_total,
            "oi_chg_pct": oi_chg_pct, "px_chg_pct": px_chg_pct,
            "front_close": fcell["close"], "front_prev": prv, "front_xpry": front_x,
            "vol": vol, "val_cr": round(val / 1e7, 2),
            "is_index": sym in fut_is_index, "fresh_oi": fresh_oi,
            "front_oi": fcell["oi"],
            "next_oi": (by_exp[next_x]["oi"] if next_x else 0),
            "next_close": (by_exp[next_x]["close"] if next_x else 0.0),
            "next_xpry": next_x,
        }

    # Finalize options: keep only the FRONT (nearest) expiry per symbol.
    options: dict[str, dict] = {}
    for sym, by_exp in opt_tmp.items():
        if not by_exp:
            continue
        front = min(by_exp.keys())  # ISO date strings → lexicographic = chronological
        strikes_map = by_exp[front]
        strikes = []
        ce_oi = pe_oi = ce_chg = pe_chg = 0
        for k in sorted(strikes_map.keys()):
            c = strikes_map[k]
            strikes.append({
                "strike": round(k, 2),
                "ce_oi": c["ce_oi"], "pe_oi": c["pe_oi"],
                "ce_chg": c["ce_chg"], "pe_chg": c["pe_chg"],
                "ce_vol": c["ce_vol"], "pe_vol": c["pe_vol"],
                "ce_ltp": round(c["ce_ltp"], 2), "pe_ltp": round(c["pe_ltp"], 2),
                "ce_settle": round(c["ce_settle"], 2), "pe_settle": round(c["pe_settle"], 2),
            })
            ce_oi += c["ce_oi"]; pe_oi += c["pe_oi"]
            ce_chg += c["ce_chg"]; pe_chg += c["pe_chg"]
        options[sym] = {
            "expiry": front,
            "spot": round(opt_spot.get(sym, 0.0), 2),
            "is_index": sym in opt_is_index,
            "ce_oi": ce_oi, "pe_oi": pe_oi,
            "ce_chg": ce_chg, "pe_chg": pe_chg,
            "strikes": strikes,
        }

    return {"futures": futures, "options": options}


def _ensure_cache() -> dict:
    """Return {"futures":..., "options":...}, fetching if not cached or stale."""
    now_utc = datetime.now(timezone.utc)

    with _CACHE_LOCK:
        if _CACHE["futures"] is not None and _CACHE["fetched_at"] is not None:
            age_s = (now_utc - _CACHE["fetched_at"]).total_seconds()
            if age_s < _CACHE_TTL_HOURS * 3600:
                return {"futures": _CACHE["futures"], "options": _CACHE["options"]}

    # Try the last 7 candidate trading days in case of holidays / stale CDN
    target = _last_likely_bhavcopy_date()
    for back in range(7):
        d = target - timedelta(days=back)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        csv_text = _fetch_bhavcopy(d)
        if not csv_text:
            continue
        parsed = _parse_bhavcopy_full(csv_text)
        if parsed.get("futures") or parsed.get("options"):
            with _CACHE_LOCK:
                _CACHE["futures"] = parsed["futures"]
                _CACHE["options"] = parsed["options"]
                _CACHE["date"] = d
                _CACHE["fetched_at"] = now_utc
            print(
                f"[OI] Loaded F&O bhavcopy {d}: "
                f"{len(parsed['futures'])} futures, "
                f"{len(parsed['options'])} option symbols"
            )
            return {"futures": parsed["futures"], "options": parsed["options"]}

    # All candidates failed — cache a brief empty so we don't hammer NSE
    print("[OI] No bhavcopy reachable — OI/F&O features will return UNKNOWN/empty")
    with _CACHE_LOCK:
        _CACHE["futures"] = {}
        _CACHE["options"] = {}
        _CACHE["date"] = None
        _CACHE["fetched_at"] = now_utc
    return {"futures": {}, "options": {}}


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

        snap = _ensure_cache()
        futures = snap.get("futures") or {}
        if not futures:
            return "UNKNOWN"

        entry = futures.get(sym)
        if not entry:
            # bhavcopy loaded fine but the symbol isn't there → not F&O
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


def get_fno_raw_snapshot() -> dict:
    """
    Full F&O snapshot for the Smart-Money board.

    Returns:
      {
        "bhavcopy_date": "YYYY-MM-DD" | None,
        "fetched_at":    iso str | None,
        "age_seconds":   float | None,
        "futures":       {SYM: {...}},     # see _parse_bhavcopy_full
        "options":       {SYM: {...}},     # front-expiry chains (stocks + indices)
      }

    Never raises — returns empty maps on any failure.
    """
    try:
        snap = _ensure_cache()
    except Exception as exc:
        print(f"[FNO] get_fno_raw_snapshot failed: {exc}")
        snap = {"futures": {}, "options": {}}

    with _CACHE_LOCK:
        date = _CACHE.get("date")
        fetched = _CACHE.get("fetched_at")

    age = (datetime.now(timezone.utc) - fetched).total_seconds() if fetched else None
    return {
        "bhavcopy_date": (date.isoformat() if date else None),
        "fetched_at": (fetched.isoformat() if fetched else None),
        "age_seconds": age,
        "futures": snap.get("futures") or {},
        "options": snap.get("options") or {},
    }


def get_option_chain_raw(symbol: str) -> dict:
    """Front-expiry option chain (strike ladder) for one symbol, or {} if absent."""
    try:
        sym = (symbol or "").upper().replace(".NS", "").replace(".BO", "").strip()
        if not sym:
            return {}
        snap = _ensure_cache()
        return (snap.get("options") or {}).get(sym, {}) or {}
    except Exception as exc:
        print(f"[FNO] get_option_chain_raw({symbol!r}) failed: {exc}")
        return {}


def cache_status() -> dict:
    """Helpful introspection: how fresh is our F&O snapshot?"""
    with _CACHE_LOCK:
        fut = _CACHE.get("futures")
        opt = _CACHE.get("options")
        date = _CACHE.get("date")
        fetched = _CACHE.get("fetched_at")
    return {
        "futures_loaded": (len(fut) if isinstance(fut, dict) else 0),
        "option_symbols_loaded": (len(opt) if isinstance(opt, dict) else 0),
        "bhavcopy_date": (date.isoformat() if date else None),
        "fetched_at": (fetched.isoformat() if fetched else None),
        "age_seconds": (
            (datetime.now(timezone.utc) - fetched).total_seconds() if fetched else None
        ),
    }


# ════════════════════════════════════════════════════════════════════════════
# OPTIONAL SECONDARY SOURCES — delivery % + bulk/block deals
#
# Both hit the NSE equity archive CDN (different files from the FO bhavcopy).
# Each is fully ISOLATED: failure → {} / [] and a worker log line, never breaks
# the Smart-Money board (the board is built primarily on the FO bhavcopy above).
# ⚠️ Live-record reachability/format should be validated in PRODUCTION — the
# equity archive endpoints can behave differently from the FO archive host, and
# could not be exercised against live data in the build env. Watch the [FNO]
# logs + /api/debug-worker-status.
# ════════════════════════════════════════════════════════════════════════════
_ARCHIVE_HOSTS = ["https://nsearchives.nseindia.com", "https://archives.nseindia.com"]

_DELIV_CACHE: dict = {"data": None, "date": None, "fetched_at": None}
_DELIV_LOCK = threading.Lock()

_DEALS_CACHE: dict = {"data": None, "fetched_at": None}
_DEALS_LOCK = threading.Lock()


def _fetch_archive_csv(path: str, min_bytes: int = 100):
    """GET a CSV from the NSE equity archive CDN (tries both hosts). Text or None."""
    for host in _ARCHIVE_HOSTS:
        try:
            resp = requests.get(host + path, headers=_HEADERS, timeout=20)
            if resp.status_code == 200 and len(resp.content) >= min_bytes:
                return resp.text
        except Exception as exc:
            print(f"[FNO] archive fetch failed {host}{path}: {exc}")
    return None


def get_delivery_map() -> dict:
    """
    {SYMBOL: delivery_pct} from the latest NSE cash bhavdata (EQ series).

    High delivery % = buyers taking delivery (genuine accumulation) vs intraday
    churn — a classic smart-money confirmation. Cached 4h. {} on any failure.
    """
    now_utc = datetime.now(timezone.utc)
    with _DELIV_LOCK:
        if _DELIV_CACHE["data"] is not None and _DELIV_CACHE["fetched_at"] is not None:
            if (now_utc - _DELIV_CACHE["fetched_at"]).total_seconds() < _CACHE_TTL_HOURS * 3600:
                return _DELIV_CACHE["data"]

    target = _last_likely_bhavcopy_date()
    for back in range(7):
        d = target - timedelta(days=back)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        path = f"/products/content/sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
        txt = _fetch_archive_csv(path, min_bytes=1000)
        if not txt:
            continue
        out: dict = {}
        try:
            reader = _csv.DictReader(io.StringIO(txt))
            for raw in reader:
                row = {(k or "").strip(): (v.strip() if isinstance(v, str) else v)
                       for k, v in raw.items()}
                if (row.get("SERIES") or "").upper() != "EQ":
                    continue
                sym = (row.get("SYMBOL") or "").upper().strip()
                if not sym:
                    continue
                try:
                    dp = float(row.get("DELIV_PER") or 0)
                except (ValueError, TypeError):
                    continue   # "-" for non-deliverable rows
                if dp > 0:
                    out[sym] = round(dp, 2)
        except Exception as exc:
            print(f"[FNO] delivery parse failed: {exc}")
            continue
        if out:
            with _DELIV_LOCK:
                _DELIV_CACHE.update({"data": out, "date": d, "fetched_at": now_utc})
            print(f"[FNO] Loaded delivery% for {len(out)} stocks (bhavdata {d})")
            return out

    with _DELIV_LOCK:
        _DELIV_CACHE.update({"data": {}, "date": None, "fetched_at": now_utc})
    return {}


def _parse_deals(txt: str, kind: str) -> list:
    """Parse a bulk/block deals CSV into normalized dicts. Tolerant of headers."""
    out = []
    try:
        reader = _csv.DictReader(io.StringIO(txt))
        for raw in reader:
            row = {(k or "").strip(): (v.strip() if isinstance(v, str) else v)
                   for k, v in raw.items()}
            sym = (row.get("Symbol") or row.get("SYMBOL") or "").upper().strip()
            if not sym:
                continue
            client = (row.get("Client Name") or row.get("CLIENT NAME")
                      or row.get("Name of Client") or "").strip()
            side = (row.get("Buy/Sell") or row.get("BUY/SELL") or "").upper().strip()
            qty = (row.get("Quantity Traded") or row.get("QUANTITY TRADED")
                   or row.get("Quantity") or "").strip()
            price = (row.get("Trade Price / Wght. Avg. Price")
                     or row.get("Trade Price/Wght.Avg.Price")
                     or row.get("TRADE PRICE / WGHT. AVG. PRICE")
                     or row.get("Price") or "").strip()
            date = (row.get("Date") or row.get("DATE") or "").strip()
            out.append({
                "symbol": sym,
                "client": client[:60],
                "side": ("BUY" if side.startswith("B")
                         else "SELL" if side.startswith("S") else side),
                "qty": qty,
                "price": price,
                "date": date,
                "kind": kind,
            })
    except Exception as exc:
        print(f"[FNO] deals parse failed ({kind}): {exc}")
    return out


def get_bulk_block_deals() -> list:
    """
    Latest NSE bulk + block deals — direct institutional footprints.

    bulk.csv / block.csv carry the most recent session's deals. Cached 4h.
    Returns a capped list of normalized deal dicts; [] on any failure.
    """
    now_utc = datetime.now(timezone.utc)
    with _DEALS_LOCK:
        if _DEALS_CACHE["data"] is not None and _DEALS_CACHE["fetched_at"] is not None:
            if (now_utc - _DEALS_CACHE["fetched_at"]).total_seconds() < _CACHE_TTL_HOURS * 3600:
                return _DEALS_CACHE["data"]

    deals: list = []
    bulk = _fetch_archive_csv("/content/equities/bulk.csv")
    if bulk:
        deals += _parse_deals(bulk, "bulk")
    block = _fetch_archive_csv("/content/equities/block.csv")
    if block:
        deals += _parse_deals(block, "block")

    deals = deals[:60]
    with _DEALS_LOCK:
        _DEALS_CACHE.update({"data": deals, "fetched_at": now_utc})
    if deals:
        print(f"[FNO] Loaded {len(deals)} bulk/block deals")
    return deals


# ════════════════════════════════════════════════════════════════════════════
# FII / DII / Pro / Client participant-wise OI — the literal "smart money" tape
#
# NSE publishes daily participant-wise OPEN INTEREST on the SAME static archive CDN
# as the bhavcopy: /content/nsccl/fao_participant_oi_<DDMMYYYY>.csv
# ⚠️ DATE FORMAT IS DDMMYYYY ('%d%m%Y') — DIFFERENT from the bhavcopy's YYYYMMDD.
# The file is: title row, then a header row (some headers carry trailing TABs), then
# rows for Client / DII / FII / Pro / TOTAL. Each cohort's positions split across
# index/stock futures Long/Short and index/stock option Call/Put Long/Short.
# Defensive (→ {} + degraded), cached 4h. Reachability is high-confidence (same CDN
# path family as the proven bhavcopy) but validate in production via the [FNO] logs.
# ════════════════════════════════════════════════════════════════════════════
_PARTICIPANT_CACHE: dict = {"data": None, "date": None, "fetched_at": None}
_PARTICIPANT_LOCK = threading.Lock()

_PARTICIPANT_FIELDS = {
    "fut_idx_long": "Future Index Long", "fut_idx_short": "Future Index Short",
    "fut_stk_long": "Future Stock Long", "fut_stk_short": "Future Stock Short",
    "opt_idx_call_long": "Option Index Call Long", "opt_idx_put_long": "Option Index Put Long",
    "opt_idx_call_short": "Option Index Call Short", "opt_idx_put_short": "Option Index Put Short",
    "opt_stk_call_long": "Option Stock Call Long", "opt_stk_put_long": "Option Stock Put Long",
    "opt_stk_call_short": "Option Stock Call Short", "opt_stk_put_short": "Option Stock Put Short",
    "total_long": "Total Long Contracts", "total_short": "Total Short Contracts",
}


def _parse_participant_oi(txt: str) -> dict:
    """Parse fao_participant_oi CSV → {COHORT: {field: int}} for Client/DII/FII/Pro/TOTAL."""
    out: dict = {}
    try:
        lines = [ln for ln in txt.splitlines() if ln.strip()]
        hdr_idx = next((i for i, ln in enumerate(lines) if "Client Type" in ln), None)
        if hdr_idx is None:
            return {}
        rows = list(_csv.reader(lines[hdr_idx:]))
        header = [(h or "").replace("\t", "").strip() for h in rows[0]]
        col = {name: i for i, name in enumerate(header)}

        def _gi(r, name):
            i = col.get(name)
            if i is None or i >= len(r):
                return 0
            try:
                return int(float((r[i] or "0").replace(",", "").replace("\t", "").strip() or 0))
            except (ValueError, TypeError):
                return 0

        for r in rows[1:]:
            if not r or not (r[0] or "").strip():
                continue
            ctype = (r[0] or "").replace("\t", "").strip().upper()
            if not ctype:
                continue
            out[ctype] = {k: _gi(r, name) for k, name in _PARTICIPANT_FIELDS.items()}
    except Exception as exc:
        print(f"[FNO] participant-OI parse failed: {exc}")
        return {}
    return out


def get_participant_oi() -> dict:
    """
    {COHORT: {field: contracts}} for the latest FII/DII/Pro/Client participant-wise OI.
    The single most-watched institutional-positioning dataset. Cached 4h. {} on failure.
    """
    now_utc = datetime.now(timezone.utc)
    with _PARTICIPANT_LOCK:
        if _PARTICIPANT_CACHE["data"] is not None and _PARTICIPANT_CACHE["fetched_at"] is not None:
            if (now_utc - _PARTICIPANT_CACHE["fetched_at"]).total_seconds() < _CACHE_TTL_HOURS * 3600:
                return _PARTICIPANT_CACHE["data"]

    target = _last_likely_bhavcopy_date()
    for back in range(7):
        d = target - timedelta(days=back)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        # ⚠️ DDMMYYYY here, NOT the bhavcopy's YYYYMMDD.
        path = f"/content/nsccl/fao_participant_oi_{d.strftime('%d%m%Y')}.csv"
        txt = _fetch_archive_csv(path, min_bytes=200)
        if not txt:
            continue
        parsed = _parse_participant_oi(txt)
        if parsed:
            parsed["_date"] = d.isoformat()
            with _PARTICIPANT_LOCK:
                _PARTICIPANT_CACHE.update({"data": parsed, "date": d, "fetched_at": now_utc})
            print(f"[FNO] Loaded participant OI ({d}): {[k for k in parsed if k != '_date']}")
            return parsed

    with _PARTICIPANT_LOCK:
        _PARTICIPANT_CACHE.update({"data": {}, "date": None, "fetched_at": now_utc})
    return {}
