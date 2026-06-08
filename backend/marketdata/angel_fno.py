"""
Angel One INTRADAY F&O source (#5) — live open-interest during market hours.

Builds the SAME snapshot shape marketdata.oi_data produces from the EOD NSE
bhavcopy, but from Angel One SmartAPI's FULL-mode quotes (which carry live
`opnInterest`). The intraday OI CHANGE is measured against the previous trading
day's persisted EOD snapshot (the baseline), so the buildup quadrants / market
bias / unusual-OI all move through the session.

Scope (bounded by Angel's ≤50-token/request quote limit + ~1 req/s rate):
  • FUTURES for every F&O underlying + indices  → the buildup engine (headline).
  • INDEX option chains (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY) → the index matrix.
  • Stock option chains stay EOD (fetching every strike of ~190 names intraday is
    infeasible within the rate limit) — the board labels this honestly.

Design notes:
  • OFF by default. Enabled only when the four Angel creds are set AND
    ANGEL_FNO_ENABLED=1. On the Render datacenter IP Angel One is blocked, so it
    stays off there and the board is pure EOD.
  • Stale-while-revalidate: a request NEVER blocks on Angel. get_intraday_snapshot
    returns the cached intraday snapshot (or None → caller uses EOD) and triggers
    a single background refresh when the cache is stale. So the first F&O poll
    shows EOD, the next shows LIVE.
  • Fully defensive: any failure anywhere → None → the caller falls back to EOD.

The two assemble_* helpers are PURE (list of quote dicts → snapshot dict) and are
unit-tested in tests/test_angel_fno.py. Nothing here imports oi_data (the EOD
baseline is passed in), so there is no import cycle.
"""
import os
import threading
import time
from datetime import datetime, timezone

try:
    import angelone_shim as _angel          # backend/ is on sys.path (--chdir backend)
except Exception:                            # pragma: no cover - shim always present in app
    _angel = None


# ── Config ─────────────────────────────────────────────────────────────────
def _enabled_flag():
    return os.environ.get("ANGEL_FNO_ENABLED", "0") == "1"


_TTL_SECS = int(os.environ.get("ANGEL_FNO_TTL_SECS", "180"))   # intraday cache window
_INDEX_UNDERLYINGS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
_INDEX_SET = set(getattr(_angel, "FNO_INDEX_UNDERLYINGS", set())) or {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

# SWR cache
_CACHE = {"snap": None, "built_at": 0.0}
_CACHE_LOCK = threading.Lock()
_BUILDING = False
_BUILD_LOCK = threading.Lock()
# Consecutive failed builds — lets status() say "unavailable" (e.g. Angel blocked
# from a datacenter IP) instead of "building" forever.
_consec_fails = 0


def is_enabled():
    """True only when creds are present AND the feature flag is on."""
    if _angel is None or not _enabled_flag():
        return False
    try:
        return bool(_angel.angel_configured())
    except Exception:
        return False


def _market_open():
    """NSE session check (IST 9:15–15:30, weekday). Uses market_calendar if present."""
    try:
        from marketdata import market_calendar
        if hasattr(market_calendar, "is_market_open"):
            return bool(market_calendar.is_market_open())
    except Exception:
        pass
    try:
        ist = datetime.now(timezone.utc).astimezone()
        from datetime import timedelta
        ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        if ist.weekday() >= 5:
            return False
        mins = ist.hour * 60 + ist.minute
        return 9 * 60 + 15 <= mins <= 15 * 60 + 30
    except Exception:
        return False


# ── PURE assembly helpers (unit-tested) ────────────────────────────────────
def _q_num(q, *keys):
    for k in keys:
        v = q.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (ValueError, TypeError):
                continue
    return 0.0


def assemble_futures(quotes, token_to_name, baseline_futures, index_set=None):
    """
    Build the FUTURES half of an intraday snapshot from FULL-mode quote dicts.

    quotes          : list of Angel 'fetched' dicts (symbolToken, ltp, close,
                      opnInterest, tradeVolume).
    token_to_name   : {token_str: underlying_name}.
    baseline_futures: previous EOD {name: {front_oi/oi_total, …}} — the OI baseline.
    Returns {name: futures_row} in oi_data._parse_bhavcopy_full's shape. Pure.
    """
    index_set = index_set or _INDEX_SET
    baseline_futures = baseline_futures or {}
    out = {}
    for q in quotes or []:
        try:
            tok = str(q.get("symbolToken") or q.get("token") or "")
            name = token_to_name.get(tok)
            if not name:
                continue
            curr_oi = int(_q_num(q, "opnInterest", "opninterest", "oi"))
            ltp = _q_num(q, "ltp", "ltP", "lastPrice")
            prev = _q_num(q, "close", "previousClose") or ltp
            vol = _q_num(q, "tradeVolume", "volume", "tradevolume")

            base = baseline_futures.get(name) or {}
            base_oi = int(base.get("front_oi") or base.get("oi_total") or 0)
            oi_chg_total = curr_oi - base_oi
            px_chg_pct = round((ltp - prev) / prev * 100, 3) if prev > 0 else 0.0
            fresh = base_oi <= 0 and curr_oi > 0
            if base_oi > 0:
                oi_chg_pct = round(oi_chg_total / base_oi * 100, 2)
            elif fresh:
                oi_chg_pct = 200.0
            else:
                oi_chg_pct = 0.0
            out[name] = {
                "oi_total": curr_oi, "oi_chg_total": oi_chg_total,
                "oi_chg_pct": oi_chg_pct, "px_chg_pct": px_chg_pct,
                "front_close": round(ltp, 2), "front_prev": round(prev, 2),
                "front_xpry": None,
                "vol": int(vol), "val_cr": round(vol * ltp / 1e7, 2),
                "is_index": name in index_set, "fresh_oi": fresh,
                "front_oi": curr_oi, "next_oi": 0, "next_close": 0.0, "next_xpry": None,
            }
        except Exception:
            continue
    return out


def assemble_index_chain(quotes, token_meta, baseline_strikes, spot, expiry):
    """
    Build one index's intraday option-chain entry from FULL-mode quote dicts.

    quotes          : list of Angel 'fetched' dicts for this index's option tokens.
    token_meta      : {token_str: {"strike": float, "opt_type": "CE"|"PE"}}.
    baseline_strikes: {strike: {"ce_oi", "pe_oi"}} from EOD (for ΔOI). May be {}.
    spot            : underlying spot proxy (front-future ltp).
    Returns an options[sym] entry in _parse_bhavcopy_full's shape. Pure.
    """
    by_strike = {}
    for q in quotes or []:
        try:
            tok = str(q.get("symbolToken") or q.get("token") or "")
            meta = token_meta.get(tok)
            if not meta:
                continue
            k = round(float(meta["strike"]), 2)
            otp = meta["opt_type"]
            oi = int(_q_num(q, "opnInterest", "opninterest", "oi"))
            ltp = _q_num(q, "ltp", "lastPrice")
            cell = by_strike.setdefault(k, {
                "ce_oi": 0, "pe_oi": 0, "ce_chg": 0, "pe_chg": 0,
                "ce_vol": 0, "pe_vol": 0, "ce_ltp": 0.0, "pe_ltp": 0.0,
                "ce_settle": 0.0, "pe_settle": 0.0,
            })
            base = (baseline_strikes or {}).get(k) or {}
            if otp == "CE":
                cell["ce_oi"] = oi
                cell["ce_chg"] = oi - int(base.get("ce_oi") or 0)
                cell["ce_ltp"] = round(ltp, 2)
                cell["ce_settle"] = round(ltp, 2)   # intraday mark for IV
            else:
                cell["pe_oi"] = oi
                cell["pe_chg"] = oi - int(base.get("pe_oi") or 0)
                cell["pe_ltp"] = round(ltp, 2)
                cell["pe_settle"] = round(ltp, 2)
        except Exception:
            continue

    strikes, ce_oi, pe_oi, ce_chg, pe_chg = [], 0, 0, 0, 0
    for k in sorted(by_strike.keys()):
        c = by_strike[k]
        strikes.append({"strike": k, **c})
        ce_oi += c["ce_oi"]; pe_oi += c["pe_oi"]
        ce_chg += c["ce_chg"]; pe_chg += c["pe_chg"]
    return {
        "expiry": expiry, "spot": round(float(spot or 0), 2), "is_index": True,
        "ce_oi": ce_oi, "pe_oi": pe_oi, "ce_chg": ce_chg, "pe_chg": pe_chg,
        "strikes": strikes,
    }


# ── SWR orchestration (live) ───────────────────────────────────────────────
def get_intraday_snapshot(eod_snapshot):
    """
    Return the cached intraday snapshot (LIVE), or None to signal "use EOD".

    Never blocks on Angel: if the cache is stale it kicks ONE background refresh
    and returns the (possibly stale) cached snapshot, or None on the very first
    call. `eod_snapshot` supplies the OI baseline + the trading-day stamp.
    """
    if not is_enabled() or not _market_open():
        return None
    now = time.time()
    with _CACHE_LOCK:
        snap, built_at = _CACHE["snap"], _CACHE["built_at"]
    if snap is not None and (now - built_at) < _TTL_SECS:
        return snap
    _maybe_refresh(eod_snapshot)
    return snap   # stale snap if we have one, else None → caller uses EOD this cycle


def _maybe_refresh(eod_snapshot):
    global _BUILDING
    with _BUILD_LOCK:
        if _BUILDING:
            return
        _BUILDING = True
    t = threading.Thread(target=_build, args=(eod_snapshot,), daemon=True)
    t.start()


def status():
    """
    Live-build status for the UI badge. One of:
      off         — feature disabled (no creds / ANGEL_FNO_ENABLED!=1)
      closed      — enabled but the NSE session is closed
      live        — a fresh (or last-good) intraday snapshot exists
      building    — enabled + open, snapshot not built yet (build in progress)
      unavailable — enabled + open but builds keep failing (e.g. Angel blocked
                    from this IP) → the board stays on EOD
    """
    if not is_enabled():
        return {"state": "off"}
    if not _market_open():
        return {"state": "closed"}
    now = time.time()
    with _CACHE_LOCK:
        snap, built_at = _CACHE["snap"], _CACHE["built_at"]
    if snap is not None:
        return {"state": "live", "as_of": snap.get("as_of"),
                "age_secs": int(now - built_at)}
    if _consec_fails >= 2:
        return {"state": "unavailable"}
    return {"state": "building"}


def _build(eod_snapshot):
    """Background: pull Angel quotes and assemble the intraday snapshot into cache."""
    global _BUILDING, _consec_fails
    ok = False
    try:
        eod_snapshot = eod_snapshot or {}
        base_fut = eod_snapshot.get("futures") or {}
        base_opt = eod_snapshot.get("options") or {}

        # 1) FUTURES — every underlying's front-month future.
        fut_map = _angel.nfo_front_future_tokens()      # {name: (token, expiry)}
        token_to_name = {tok: name for name, (tok, _exp) in fut_map.items()}
        fut_quotes = _angel.get_full_quotes("NFO", list(token_to_name.keys()))
        futures = assemble_futures(fut_quotes, token_to_name, base_fut, _INDEX_SET)

        if not futures:
            return   # Angel unreachable/empty → leave cache; caller stays on EOD

        # 2) INDEX option chains (bounded set).
        options = {}
        for idx in _INDEX_UNDERLYINGS:
            try:
                expiry, opts = _angel.nfo_front_option_tokens(idx)
                if not opts:
                    continue
                token_meta = {o["token"]: {"strike": o["strike"], "opt_type": o["opt_type"]}
                              for o in opts}
                oq = _angel.get_full_quotes("NFO", list(token_meta.keys()))
                if not oq:
                    continue
                base_strikes = {s["strike"]: s for s in (base_opt.get(idx, {}).get("strikes") or [])}
                spot = (futures.get(idx) or {}).get("front_close") or 0
                options[idx] = assemble_index_chain(oq, token_meta, base_strikes, spot, expiry)
            except Exception:
                continue

        snap = {
            "bhavcopy_date": eod_snapshot.get("bhavcopy_date"),
            "as_of": datetime.now(timezone.utc).isoformat(),
            "source": "intraday",
            "futures": futures,
            "options": options,
        }
        with _CACHE_LOCK:
            _CACHE["snap"] = snap
            _CACHE["built_at"] = time.time()
        ok = True
        print(f"[AngelFNO] intraday snapshot built: {len(futures)} futures, "
              f"{len(options)} index chains")
    except Exception as exc:
        print(f"[AngelFNO] intraday build failed: {exc}")
    finally:
        with _BUILD_LOCK:
            _BUILDING = False
        # Track consecutive failures so status() can report 'unavailable' (rather
        # than a perpetual 'building') when Angel is unreachable from this host.
        _consec_fails = 0 if ok else (_consec_fails + 1)
