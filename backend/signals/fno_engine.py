"""
F&O Smart-Money engine — pure, deterministic institutional-positioning analytics.

NO network, NO DB, NO LLM. Takes a parsed bhavcopy snapshot (from
marketdata.oi_data.get_fno_raw_snapshot) + optional FII/DII participant OI, and
produces the F&O Smart-Money board: the read a derivatives desk builds from the
daily OI tape.

What it computes (all deterministic, from the daily F&O bhavcopy + free archives):

  • Buildup quadrants (futures, OI×price): LONG_BUILDUP / SHORT_BUILDUP /
    SHORT_COVERING / LONG_UNWINDING, each ranked by a conviction score.
  • Conviction (OI-surge × DIRECTIONAL price-confirm × liquidity × delivery).
  • Unusual OI surges; delivery-conviction spikes.
  • Option chain per symbol: PCR(OI), max-pain (spot-tie-broken), ranked call/put
    OI walls (+fresh flag), per-strike IV + Delta (Black-76), ATM IV, IV skew.
  • Futures basis (futures vs spot) and rollover % (next/(front+next) OI).
  • Index option matrix (NIFTY / BANKNIFTY / FINNIFTY …) with basis + ATM IV.
  • FII/DII/Pro/Client participant positioning (the literal smart money).
  • Sector clustering, market-wide bias, deterministic setups + English narrative.

Design mirrors signals/ripple_engine.py: pure functions, module-constant tunables,
fully unit-testable, instant, reproducible, never hallucinates. IV/Greeks live in
signals/options_math.py (also pure) to keep this import-clean.
"""
from __future__ import annotations

from signals.options_math import iv_and_greeks, years_to_expiry

# ── Tunables (module constants → keep the module import-pure) ──────────────
NEUTRAL_PX_PCT = 0.05        # |price move| below this = NEUTRAL buildup
UNUSUAL_OI_PCT = 18.0        # |ΔOI%| at/above this = "unusual" surge
MIN_VAL_CR = 1.0             # below this futures turnover (₹cr) = thin, deprioritized
MAX_LIST = 12                # max rows per buildup table
MAX_UNUSUAL = 10
MAX_DELIVERY = 10
MAX_WALLS = 3                # top-N OI walls each side
MAX_SETUPS = 6              # deterministic setups for the top names
BULLISH_BIAS_CUT = 12.0      # bias score (-100..100) thresholds
BEARISH_BIAS_CUT = -12.0
PCR_BULL = 1.20              # index/stock PCR sentiment bands
PCR_BULL_STRONG = 1.50
PCR_BEAR = 0.80
PCR_BEAR_STRONG = 0.60
IV_RICH = 40.0               # ATM IV % above this = rich (sell premium)
IV_CHEAP = 18.0             # below this = cheap (buy options)

# Display order + labels for the index option matrix. (SENSEX/BANKEX are BSE
# indices that never appear in the NSE FO bhavcopy this parser reads — excluded.)
INDEX_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"]
INDEX_LABELS = {
    "NIFTY": "Nifty 50", "BANKNIFTY": "Bank Nifty", "FINNIFTY": "Fin Nifty",
    "MIDCPNIFTY": "Midcap Nifty", "NIFTYNXT50": "Nifty Next 50",
}

# ── Static F&O sector map (top ~190 liquid NSE F&O names) ──────────────────
# Lets us cluster positioning by sector with ZERO per-ticker fundamentals
# lookups. Unknown symbols fall back to "Other". A test asserts no symbol is in
# two sectors — keep buckets disjoint.
_SECTOR_GROUPS = {
    "Banks": ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "INDUSINDBK",
              "BANKBARODA", "PNB", "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "BANDHANBNK",
              "RBLBANK", "CANBK", "INDIANB", "UNIONBANK", "BANKINDIA"],
    "Financials": ["BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "SHRIRAMFIN", "MUTHOOTFIN",
                   "LICHSGFIN", "M&MFIN", "PEL", "SBICARD", "HDFCLIFE", "SBILIFE",
                   "ICICIPRULI", "ICICIGI", "LICI", "HDFCAMC", "PFC", "RECLTD", "IRFC",
                   "POLICYBZR", "PAYTM", "ABCAPITAL", "MANAPPURAM", "IEX", "BSE",
                   "ANGELONE", "CDSL", "MCX", "HUDCO"],
    "IT": ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "LTTS", "MPHASIS",
           "COFORGE", "PERSISTENT", "OFSS", "TATAELXSI"],
    "Auto": ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "HEROMOTOCO", "EICHERMOT",
             "TVSMOTOR", "ASHOKLEY", "BHARATFORG", "MOTHERSON", "BOSCHLTD",
             "BALKRISIND", "MRF", "TIINDIA", "EXIDEIND"],
    "Metals": ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "JINDALSTEL", "SAIL",
               "NMDC", "NATIONALUM", "HINDZINC", "APLAPOLLO", "JSL", "HINDCOPPER"],
    "Energy": ["RELIANCE", "ONGC", "IOC", "BPCL", "HPCL", "GAIL", "PETRONET", "OIL",
               "IGL", "MGL", "GUJGASLTD", "ATGL", "ADANIGREEN", "ADANIENSOL", "COALINDIA"],
    "Power": ["NTPC", "POWERGRID", "TATAPOWER", "ADANIPOWER", "JSWENERGY", "NHPC",
              "SJVN", "TORNTPOWER", "CESC"],
    "Pharma": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "AUROPHARMA", "LUPIN",
               "ALKEM", "TORNTPHARM", "BIOCON", "ZYDUSLIFE", "GLENMARK", "LAURUSLABS",
               "GRANULES", "SYNGENE", "MANKIND"],
    "Healthcare": ["APOLLOHOSP", "MAXHEALTH", "FORTIS", "LALPATHLAB", "METROPOLIS"],
    "FMCG": ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "MARICO",
             "GODREJCP", "COLPAL", "TATACONSUM", "UBL", "UNITDSPR", "VBL", "PGHH",
             "EMAMILTD", "RADICO"],
    "Cement": ["ULTRACEMCO", "SHREECEM", "AMBUJACEM", "ACC", "DALBHARAT", "RAMCOCEM",
               "JKCEMENT", "INDIACEM"],
    "Infra": ["LT", "ADANIPORTS", "GMRINFRA", "GMRAIRPORT", "IRB", "NCC", "NBCC",
              "RVNL", "IRCON", "KEC", "CONCOR", "IRCTC"],
    "Realty": ["DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "LODHA", "PHOENIXLTD", "BRIGADE"],
    "Telecom": ["BHARTIARTL", "IDEA", "INDUSTOWER", "TATACOMM", "HFCL"],
    "Chemicals": ["PIDILITIND", "SRF", "UPL", "PIIND", "AARTIIND", "DEEPAKNTR",
                  "NAVINFLUOR", "TATACHEM", "ATUL", "GNFC", "CHAMBLFERT", "COROMANDEL",
                  "FACT", "SUMICHEM"],
    "Consumer": ["TITAN", "DMART", "TRENT", "ASIANPAINT", "BERGEPAINT", "HAVELLS",
                 "VOLTAS", "CROMPTON", "DIXON", "KAJARIACER", "BATAINDIA", "RELAXO",
                 "PAGEIND", "ABFRL", "NYKAA", "KALYANKJIL", "JUBLFOOD", "INDHOTEL"],
    "Capital Goods": ["SIEMENS", "ABB", "BHEL", "BEL", "HAL", "CUMMINSIND", "THERMAX",
                      "POLYCAB", "MAZDOCK", "BDL", "SOLARINDS", "CGPOWER"],
    "Media": ["ZEEL", "SUNTV", "PVRINOX", "NETWORK18"],
    "Diversified": ["ADANIENT", "GRASIM"],
    "PSU": ["GMDCLTD", "BEML", "COCHINSHIP", "IREDA", "NLCINDIA", "MOIL"],
}
_SYM_SECTOR = {}
for _sec, _syms in _SECTOR_GROUPS.items():
    for _s in _syms:
        _SYM_SECTOR.setdefault(_s, _sec)


def normalize_ticker(t):
    return (t or "").upper().replace(".NS", "").replace(".BO", "").strip()


def sector_for(symbol):
    return _SYM_SECTOR.get(normalize_ticker(symbol), "Other")


# ── Buildup classification ────────────────────────────────────────────────
def classify_buildup(px_chg_pct, oi_chg):
    """Map (price %change, OI change) → one of the four quadrants or NEUTRAL."""
    try:
        px = float(px_chg_pct or 0.0)
        oi = float(oi_chg or 0.0)
    except (ValueError, TypeError):
        return "NEUTRAL"
    if abs(px) < NEUTRAL_PX_PCT:
        return "NEUTRAL"
    px_up, oi_up = px > 0, oi > 0
    if px_up and oi_up:
        return "LONG_BUILDUP"
    if px_up and not oi_up:
        return "SHORT_COVERING"
    if not px_up and oi_up:
        return "SHORT_BUILDUP"
    return "LONG_UNWINDING"


BUILDUP_META = {
    "LONG_BUILDUP":   {"label": "Long Buildup",   "dir": "bullish", "strong": True},
    "SHORT_COVERING": {"label": "Short Covering",  "dir": "bullish", "strong": False},
    "SHORT_BUILDUP":  {"label": "Short Buildup",   "dir": "bearish", "strong": True},
    "LONG_UNWINDING": {"label": "Long Unwinding",  "dir": "bearish", "strong": False},
    "NEUTRAL":        {"label": "Neutral",         "dir": "neutral", "strong": False},
}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def conviction_score(oi_chg_pct, px_chg_pct, val_cr, delivery_pct=None, buildup=None):
    """0–99 conviction that the futures move reflects real positioning.

    Aggressive OI change is the spine; the price-confirmation reward is ONLY granted
    when the price move agrees with the buildup direction (bullish quadrants need
    px>0, bearish need px<0) — a contradictory move adds nothing. Liquidity and a
    delivery spike add weight. (When `buildup` is None the price term is magnitude-
    only, preserving the legacy contract.)
    """
    oi_mag = abs(float(oi_chg_pct or 0.0))
    px = float(px_chg_pct or 0.0)
    px_mag = abs(px)
    score = 38.0
    score += min(oi_mag, 30.0)                 # OI surge (cap +30)
    confirm = True
    if buildup:
        d = BUILDUP_META.get(buildup, {}).get("dir")
        if d == "bullish":
            confirm = px > 0
        elif d == "bearish":
            confirm = px < 0
    if confirm:
        score += min(px_mag * 2.5, 15.0)       # directional price confirmation (cap +15)
    if val_cr and val_cr >= 50:                # liquid name
        score += 8.0
    elif val_cr and val_cr >= 10:
        score += 4.0
    if delivery_pct is not None and delivery_pct >= 60:
        score += 8.0                           # strong delivery → genuine accumulation
    elif delivery_pct is not None and delivery_pct >= 45:
        score += 4.0
    return int(round(_clamp(score, 0.0, 99.0)))


# ── Option analytics ──────────────────────────────────────────────────────
def pcr(ce_oi, pe_oi):
    ce = float(ce_oi or 0.0)
    pe = float(pe_oi or 0.0)
    if ce <= 0:
        return None
    return round(pe / ce, 2)


def max_pain(strikes, spot=None):
    """Strike that minimizes total ITM value to option BUYERS (writers' max pain).

    Price tends to gravitate toward this level into expiry. On a tie (symmetric/flat
    chains) the strike NEAREST spot wins (instead of the lowest), if spot is given.
    strikes: list of {strike, ce_oi, pe_oi}.
    """
    ks = [s for s in (strikes or []) if s.get("strike")]
    if not ks:
        return None
    best_k, best_loss = None, None
    for cand in ks:
        K0 = cand["strike"]
        total = 0.0
        for s in ks:
            K = s["strike"]
            if K0 > K:                          # calls ITM at expiry price K0
                total += (s.get("ce_oi") or 0) * (K0 - K)
            elif K0 < K:                        # puts ITM
                total += (s.get("pe_oi") or 0) * (K - K0)
        if best_loss is None or total < best_loss - 1e-9:
            best_loss, best_k = total, K0
        elif spot is not None and abs(total - best_loss) <= 1e-9:
            if abs(K0 - spot) < abs(best_k - spot):
                best_k = K0
    return best_k


def oi_walls(strikes):
    """(call_wall, put_wall) — strikes with the largest call / put OI."""
    call_wall = put_wall = None
    best_ce = best_pe = 0   # a zero-OI strike must NOT be reported as a wall
    for s in (strikes or []):
        ce = s.get("ce_oi") or 0
        pe = s.get("pe_oi") or 0
        if ce > best_ce:
            best_ce, call_wall = ce, s.get("strike")
        if pe > best_pe:
            best_pe, put_wall = pe, s.get("strike")
    return call_wall, put_wall


def ranked_walls(strikes, side, top=MAX_WALLS):
    """Top-N OI walls for 'ce' (resistance) or 'pe' (support), with a fresh-OI flag.

    fresh = today's ΔOI is a large fraction of the standing prior OI (new writing).
    """
    oi_k, chg_k = (side + "_oi", side + "_chg")
    cand = [s for s in (strikes or []) if (s.get(oi_k) or 0) > 0]
    cand.sort(key=lambda s: -(s.get(oi_k) or 0))
    out = []
    for s in cand[:top]:
        oi = s.get(oi_k) or 0
        chg = s.get(chg_k) or 0
        prior = max(oi - chg, 1)
        out.append({"strike": s.get("strike"), "oi": oi, "chg": chg,
                    "fresh": chg > 0.5 * prior})
    return out


def option_sentiment(pcr_val, ce_chg, pe_chg):
    """Directional read from PCR level + fresh option writing (ΔOI)."""
    score = 0
    if pcr_val is not None:
        if pcr_val >= PCR_BULL_STRONG:
            score += 2
        elif pcr_val >= PCR_BULL:
            score += 1
        elif pcr_val <= PCR_BEAR_STRONG:
            score -= 2
        elif pcr_val <= PCR_BEAR:
            score -= 1
    cec = float(ce_chg or 0.0)
    pec = float(pe_chg or 0.0)
    if pec - cec > 0:
        score += 1
    elif cec - pec > 0:
        score -= 1
    if score >= 2:
        return "BULLISH", score
    if score <= -2:
        return "BEARISH", score
    return "NEUTRAL", score


def _opt_premium(cell, side):
    """EOD mark for IV: settlement price, falling back to last-traded (close)."""
    return (cell.get(side + "_settle") or cell.get(side + "_ltp") or 0) or 0


def option_chain_view(symbol, opt_entry, futures_entry=None, asof_date=None):
    """
    Per-symbol option analytics + full strike ladder (the drill-down). When the
    matching futures price (forward F) and the bhavcopy date are supplied, attaches
    per-strike IV + Delta (Black-76), ATM IV and IV skew. Degrades gracefully (IV
    None) when inputs are missing.
    """
    if not opt_entry:
        return None
    strikes = opt_entry.get("strikes") or []
    ce_oi = opt_entry.get("ce_oi", 0)
    pe_oi = opt_entry.get("pe_oi", 0)
    ce_chg = opt_entry.get("ce_chg", 0)
    pe_chg = opt_entry.get("pe_chg", 0)
    spot = opt_entry.get("spot", 0.0)
    expiry = opt_entry.get("expiry")

    # Forward F for Black-76 = matching-expiry futures price; fall back to spot.
    F = None
    if futures_entry and futures_entry.get("front_close"):
        F = float(futures_entry["front_close"])
    elif spot:
        F = float(spot)
    T = years_to_expiry(expiry, asof_date) if (expiry and asof_date) else None

    p = pcr(ce_oi, pe_oi)
    mp = max_pain(strikes, spot=(spot or F))
    sentiment, sent_score = option_sentiment(p, ce_chg, pe_chg)

    # Per-strike IV + Delta (off the forward F), plus moneyness; attach to ladder.
    ladder = []
    for s in strikes:
        K = s.get("strike")
        r = dict(s)
        r["moneyness_pct"] = (round((K - F) / F * 100, 2) if (F and K) else None)
        r["ce_iv"] = r["pe_iv"] = r["ce_delta"] = r["pe_delta"] = None
        if F and T and K:
            cp = _opt_premium(s, "ce")
            pp = _opt_premium(s, "pe")
            if cp:
                cg = iv_and_greeks(cp, F, K, T, True)
                if cg["iv"] is not None:
                    r["ce_iv"] = round(cg["iv"] * 100, 2)
                    r["ce_delta"] = cg["delta"]
            if pp:
                pg = iv_and_greeks(pp, F, K, T, False)
                if pg["iv"] is not None:
                    r["pe_iv"] = round(pg["iv"] * 100, 2)
                    r["pe_delta"] = pg["delta"]
        ladder.append(r)

    # ATM IV = avg(CE,PE IV) at the strike nearest the forward.
    atm_iv = atm_strike = None
    if F and ladder:
        atm = min(ladder, key=lambda r: abs((r.get("strike") or 0) - F))
        atm_strike = atm.get("strike")
        ivs = [v for v in (atm.get("ce_iv"), atm.get("pe_iv")) if v is not None]
        atm_iv = round(sum(ivs) / len(ivs), 2) if ivs else None

    # IV skew = IV(~5% OTM put) − IV(~5% OTM call). +ve = downside fear.
    iv_skew = None
    if F and ladder:
        puts = [r for r in ladder if r.get("strike") and r["strike"] < F * 0.985 and r.get("pe_iv") is not None]
        calls = [r for r in ladder if r.get("strike") and r["strike"] > F * 1.015 and r.get("ce_iv") is not None]
        if puts and calls:
            put = min(puts, key=lambda r: abs(r["strike"] - F * 0.95))
            call = min(calls, key=lambda r: abs(r["strike"] - F * 1.05))
            iv_skew = round(put["pe_iv"] - call["ce_iv"], 2)

    call_walls = ranked_walls(ladder, "ce")
    put_walls = ranked_walls(ladder, "pe")
    cw = call_walls[0]["strike"] if call_walls else None
    pw = put_walls[0]["strike"] if put_walls else None

    mp_gap = round((spot - mp) / mp * 100, 2) if (mp and spot) else None

    return {
        "symbol": normalize_ticker(symbol),
        "is_index": bool(opt_entry.get("is_index")),
        "expiry": expiry,
        "spot": spot,
        "forward": (round(F, 2) if F else None),
        "pcr": p,
        "max_pain": mp,
        "max_pain_gap_pct": mp_gap,
        "call_wall": cw,
        "put_wall": pw,
        "call_walls": call_walls,
        "put_walls": put_walls,
        "atm_iv": atm_iv,
        "atm_strike": atm_strike,
        "iv_skew": iv_skew,
        "ce_oi": ce_oi, "pe_oi": pe_oi,
        "ce_chg": ce_chg, "pe_chg": pe_chg,
        "sentiment": sentiment,
        "sentiment_score": sent_score,
        "ladder": ladder,
    }


# ── Day-over-day diffing (#4) ──────────────────────────────────────────────
def diff_snapshots(curr_snapshot, prev_snapshot):
    """
    Compare two F&O snapshots' FUTURES maps and surface what changed since the
    previous trading day — the honest way to make end-of-day data feel alive.

    Returns {"by_symbol": {SYM: vs_prev}, "summary": {...}} where vs_prev is:
        {is_new, buildup_prev, buildup_prev_label, flipped, oi_delta_pct}
      • flipped     — the buildup direction switched bullish↔bearish vs prev day
      • is_new      — the symbol wasn't in the previous snapshot
      • oi_delta_pct— day-over-day change in total OI (%), None if no baseline

    Pure / never raises — a malformed snapshot yields an empty diff.
    """
    curr_fut = (curr_snapshot or {}).get("futures") or {}
    prev_fut = (prev_snapshot or {}).get("futures") or {}
    by_symbol, flipped, newly = {}, [], []
    for sym, cf in curr_fut.items():
        try:
            s = normalize_ticker(sym)
            if not s or cf.get("is_index"):
                continue
            cb = classify_buildup(cf.get("px_chg_pct"), cf.get("oi_chg_total"))
            cdir = BUILDUP_META.get(cb, {}).get("dir")
            pf = prev_fut.get(sym) or prev_fut.get(s)
            if not pf:
                by_symbol[s] = {"is_new": True, "buildup_prev": None,
                                "buildup_prev_label": None, "flipped": False,
                                "oi_delta_pct": None}
                if cdir in ("bullish", "bearish"):
                    newly.append(s)
                continue
            pb = classify_buildup(pf.get("px_chg_pct"), pf.get("oi_chg_total"))
            pdir = BUILDUP_META.get(pb, {}).get("dir")
            c_oi = cf.get("oi_total") or 0
            p_oi = pf.get("oi_total") or 0
            oi_delta = round((c_oi - p_oi) / p_oi * 100, 1) if p_oi > 0 else None
            is_flip = (cdir in ("bullish", "bearish") and pdir in ("bullish", "bearish")
                       and cdir != pdir)
            by_symbol[s] = {"is_new": False, "buildup_prev": pb,
                            "buildup_prev_label": BUILDUP_META.get(pb, {}).get("label"),
                            "flipped": is_flip, "oi_delta_pct": oi_delta}
            if is_flip:
                flipped.append({"symbol": s, "from": pdir, "to": cdir,
                                "buildup": cb, "buildup_label": BUILDUP_META[cb]["label"]})
        except Exception:
            continue
    summary = {
        "prev_date": (prev_snapshot or {}).get("bhavcopy_date"),
        "flipped_count": len(flipped),
        "flipped": flipped[:8],
        "new_count": len(newly),
        "new_names": newly[:8],
    }
    return {"by_symbol": by_symbol, "summary": summary}


# ── Board assembly ────────────────────────────────────────────────────────
def _fmt_pct(v):
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return 0.0


def _basis_pct(front_close, spot):
    try:
        front_close = float(front_close or 0)
        spot = float(spot or 0)
    except (ValueError, TypeError):
        return None
    return round((front_close - spot) / spot * 100, 2) if (spot and front_close) else None


def _rollover_pct(front_oi, next_oi):
    f = front_oi or 0
    n = next_oi or 0
    return round(n / (f + n) * 100, 1) if (f + n) > 0 else None


def _build_row(sym, fut, opt_entry, delivery_pct, watchset):
    px = _fmt_pct(fut.get("px_chg_pct"))
    oi_chg_pct = _fmt_pct(fut.get("oi_chg_pct"))
    val_cr = _fmt_pct(fut.get("val_cr"))
    buildup = classify_buildup(px, fut.get("oi_chg_total"))
    conv = conviction_score(oi_chg_pct, px, val_cr, delivery_pct, buildup=buildup)
    p = pcr(opt_entry.get("ce_oi"), opt_entry.get("pe_oi")) if opt_entry else None
    spot = (opt_entry.get("spot") if opt_entry else 0) or 0
    return {
        "symbol": sym,
        "sector": sector_for(sym),
        "buildup": buildup,
        "buildup_label": BUILDUP_META[buildup]["label"],
        "direction": BUILDUP_META[buildup]["dir"],
        "px_chg_pct": px,
        "oi_chg_pct": oi_chg_pct,
        "oi": fut.get("oi_total", 0),
        "close": _fmt_pct(fut.get("front_close")),
        "val_cr": val_cr,
        "conviction": conv,
        "pcr": p,
        "basis_pct": _basis_pct(fut.get("front_close"), spot),
        "rollover_pct": _rollover_pct(fut.get("front_oi"), fut.get("next_oi")),
        "fresh_oi": bool(fut.get("fresh_oi")),
        "delivery_pct": (round(delivery_pct, 1) if delivery_pct is not None else None),
        "in_watchlist": sym in watchset,
    }


def _index_matrix(options, futures=None, asof_date=None):
    futures = futures or {}
    out = []
    for sym in INDEX_SYMBOLS:
        ent = options.get(sym)
        if not ent:
            continue
        fut = futures.get(sym)
        view = option_chain_view(sym, ent, fut, asof_date)
        if not view:
            continue
        fdict = fut or {}
        out.append({
            "symbol": sym,
            "label": INDEX_LABELS.get(sym, sym),
            "spot": view["spot"],
            "forward": view["forward"],
            "pcr": view["pcr"],
            "max_pain": view["max_pain"],
            "max_pain_gap_pct": view["max_pain_gap_pct"],
            "call_wall": view["call_wall"],
            "put_wall": view["put_wall"],
            "atm_iv": view["atm_iv"],
            "iv_skew": view["iv_skew"],
            "sentiment": view["sentiment"],
            "ce_chg": view["ce_chg"],
            "pe_chg": view["pe_chg"],
            "basis_pct": _basis_pct(fdict.get("front_close"), view["spot"]),
            "rollover_pct": _rollover_pct(fdict.get("front_oi"), fdict.get("next_oi")),
        })
    return out


def _sector_clustering(rows):
    """Net conviction-weighted bias per sector (strong quadrants full, weak ×0.5)."""
    agg = {}
    for r in rows:
        sec = r["sector"]
        meta = BUILDUP_META[r["buildup"]]
        d = meta["dir"]
        w = r["conviction"] * (1.0 if meta["strong"] else 0.5)
        a = agg.setdefault(sec, {"sector": sec, "bull": 0.0, "bear": 0.0, "n": 0,
                                 "names": []})
        a["n"] += 1
        if d == "bullish":
            a["bull"] += w
        elif d == "bearish":
            a["bear"] += w
        a["names"].append(r["symbol"])
    out = []
    for a in agg.values():
        if a["n"] < 2 or a["sector"] == "Other":
            continue
        tot = a["bull"] + a["bear"]
        net = round((a["bull"] - a["bear"]) / tot * 100, 1) if tot else 0.0
        out.append({
            "sector": a["sector"], "count": a["n"], "net_bias": net,
            "direction": ("bullish" if net > 15 else "bearish" if net < -15 else "mixed"),
            "names": a["names"][:6],
        })
    out.sort(key=lambda x: abs(x["net_bias"]), reverse=True)
    return out


def _market_bias(rows, index_matrix):
    """Conviction-weighted long vs short pressure, overlaid with index PCR."""
    bull = bear = 0.0
    for r in rows:
        meta = BUILDUP_META[r["buildup"]]
        w = r["conviction"] * (1.0 if meta["strong"] else 0.5)
        if meta["dir"] == "bullish":
            bull += w
        elif meta["dir"] == "bearish":
            bear += w
    tot = bull + bear
    score = round((bull - bear) / tot * 100, 1) if tot else 0.0

    nifty = next((i for i in index_matrix if i["symbol"] == "NIFTY"), None)
    if nifty and nifty.get("pcr") is not None:
        p = nifty["pcr"]
        if p >= PCR_BULL_STRONG:
            score += 8
        elif p >= PCR_BULL:
            score += 4
        elif p <= PCR_BEAR_STRONG:
            score -= 8
        elif p <= PCR_BEAR:
            score -= 4
    score = round(_clamp(score, -100.0, 100.0), 1)
    label = ("BULLISH" if score >= BULLISH_BIAS_CUT
             else "BEARISH" if score <= BEARISH_BIAS_CUT else "NEUTRAL")
    return {"score": score, "label": label,
            "bull_pressure": round(bull, 0), "bear_pressure": round(bear, 0)}


# ── FII / DII / Pro / Client positioning ──────────────────────────────────
def _participant_positioning(participant):
    """Turn raw participant-OI contracts into a per-cohort net/long-share read.

    The headline is FII net index-futures position — the single most-cited
    institutional directional gauge. Client is the contrarian retail overlay.
    """
    if not participant:
        return {"applicable": False}

    def _net(d, lk, sk):
        return (d.get(lk, 0) or 0) - (d.get(sk, 0) or 0)

    cohorts = []
    for c in ("FII", "DII", "PRO", "CLIENT"):
        d = participant.get(c)
        if not d:
            continue
        tl = d.get("total_long", 0) or 0
        ts = d.get("total_short", 0) or 0
        cohorts.append({
            "cohort": c.title() if c != "FII" and c != "DII" else c,
            "fut_index_net": _net(d, "fut_idx_long", "fut_idx_short"),
            "fut_stock_net": _net(d, "fut_stk_long", "fut_stk_short"),
            "opt_index_call_net": _net(d, "opt_idx_call_long", "opt_idx_call_short"),
            "opt_index_put_net": _net(d, "opt_idx_put_long", "opt_idx_put_short"),
            "total_long": tl, "total_short": ts,
            "long_share": (round(tl / (tl + ts) * 100, 1) if (tl + ts) > 0 else None),
        })
    if not cohorts:
        return {"applicable": False}

    fii = next((x for x in cohorts if x["cohort"] == "FII"), None)
    headline = None
    if fii:
        n = fii["fut_index_net"]
        bias = "BULLISH" if n > 0 else "BEARISH" if n < 0 else "NEUTRAL"
        call_net = fii["opt_index_call_net"]
        put_net = fii["opt_index_put_net"]
        opt_read = ("put-writing (supportive)" if put_net < 0 and abs(put_net) > abs(call_net)
                    else "call-writing (capping)" if call_net < 0 and abs(call_net) > abs(put_net)
                    else "mixed")
        headline = {
            "fii_index_fut_net": n,
            "bias": bias,
            "fii_index_long_share": fii["long_share"],
            "fii_option_read": opt_read,
            "summary": (f"FII net {'+' if n >= 0 else ''}{n:,} index-fut contracts "
                        f"({bias.lower()}); index options {opt_read}."),
        }
    return {"applicable": True, "date": participant.get("_date"),
            "cohorts": cohorts, "headline": headline}


# ── Deterministic setup suggestions (bias + levels, NOT advice) ────────────
def suggest_setup(row, view=None):
    view = view or {}
    bu = row.get("buildup")
    dirn = row.get("direction")
    support = view.get("put_wall")
    resistance = view.get("call_wall")
    magnet = view.get("max_pain")
    atm_iv = view.get("atm_iv")

    if dirn == "bullish":
        stance = "Bullish"
        idea = ("Fresh long buildup — favor longs / bull-call-spread toward resistance"
                if bu == "LONG_BUILDUP"
                else "Short covering — squeeze potential, momentum long")
    elif dirn == "bearish":
        stance = "Bearish"
        idea = ("Fresh short buildup — favor shorts / bear-put-spread toward support"
                if bu == "SHORT_BUILDUP"
                else "Long unwinding — longs exiting, avoid fresh longs")
    else:
        stance = "Neutral"
        idea = "Range-bound — no clear directional edge"

    if atm_iv is not None:
        if atm_iv >= IV_RICH:
            idea += f"; IV rich ({atm_iv}%) — prefer spreads / sell premium"
        elif atm_iv <= IV_CHEAP:
            idea += f"; IV cheap ({atm_iv}%) — buying options favorable"

    return {
        "symbol": row.get("symbol"),
        "buildup_label": row.get("buildup_label"),
        "stance": stance,
        "idea": idea,
        "conviction": row.get("conviction"),
        "atm_iv": atm_iv,
        "levels": {"support": support, "resistance": resistance, "magnet": magnet},
    }


def _pcr_word(p):
    if p is None:
        return "n/a"
    if p >= PCR_BULL_STRONG:
        return "heavy put-writing (bullish)"
    if p >= PCR_BULL:
        return "put-heavy (mildly bullish)"
    if p <= PCR_BEAR_STRONG:
        return "heavy call-writing (bearish)"
    if p <= PCR_BEAR:
        return "call-heavy (mildly bearish)"
    return "balanced"


def _narrative(bias, buildups, index_matrix, sectors, unusual, participant=None):
    """Deterministic English institutional read (no LLM)."""
    parts = []
    nlong = len(buildups["LONG_BUILDUP"])
    nshort = len(buildups["SHORT_BUILDUP"])
    nsc = len(buildups["SHORT_COVERING"])
    nlu = len(buildups["LONG_UNWINDING"])
    lab = bias["label"].capitalize()
    parts.append(
        f"Derivatives desk reads {lab.upper()} (bias {bias['score']:+.0f}). "
        f"{nlong} names with fresh long buildup and {nsc} short-covering vs "
        f"{nshort} short buildup and {nlu} long-unwinding."
    )
    if participant and participant.get("headline"):
        parts.append(participant["headline"]["summary"])
    top = None
    for cat in ("LONG_BUILDUP", "SHORT_BUILDUP", "SHORT_COVERING", "LONG_UNWINDING"):
        if buildups[cat]:
            cand = buildups[cat][0]
            if top is None or cand["conviction"] > top["conviction"]:
                top = cand
    if top:
        parts.append(
            f"Highest-conviction footprint: {top['symbol']} "
            f"({BUILDUP_META[top['buildup']]['label']}, OI {top['oi_chg_pct']:+.1f}%, "
            f"price {top['px_chg_pct']:+.1f}%, conviction {top['conviction']})."
        )
    nifty = next((i for i in index_matrix if i["symbol"] == "NIFTY"), None)
    if nifty:
        mp = f"max-pain {int(nifty['max_pain'])}" if nifty.get("max_pain") else "max-pain n/a"
        iv = f", ATM IV {nifty['atm_iv']}%" if nifty.get("atm_iv") else ""
        parts.append(f"Nifty PCR {nifty.get('pcr')} — {_pcr_word(nifty.get('pcr'))}; {mp}{iv}.")
    if sectors:
        s = sectors[0]
        if s["direction"] != "mixed":
            parts.append(
                f"Clustered {s['direction']} positioning in {s['sector']} "
                f"({s['count']} names)."
            )
    if unusual:
        u = unusual[0]
        parts.append(
            f"Largest OI surge: {u['symbol']} ({u['oi_chg_pct']:+.0f}% OI, "
            f"{u['buildup_label'].lower()})."
        )
    return " ".join(parts)


# ── Tomorrow's Outlook — plain-English next-session synthesis (no LLM) ──────
# Weighs the institutional factors a top desk reads pre-open and renders them so
# a NORMAL investor understands what tomorrow could look like. Deterministic:
# each factor maps to a signed lean (-1 bearish .. +1 bullish); the weighted blend
# is the headline stance. Honest by construction — it caps confidence, frames a
# range (not a point), and labels itself a probability read, not a guarantee.
OUTLOOK_WEIGHTS = {
    "breadth": 0.30,    # how many F&O stocks are building longs vs shorts
    "fii": 0.26,        # FII net index-futures (the literal smart money)
    "pcr": 0.18,        # Nifty put-call ratio (option writers' bias)
    "magnet": 0.10,     # max-pain pull into expiry
    "opt_flow": 0.06,   # today's option flow (down-weighted: partly overlaps PCR)
    "skew": 0.09,       # IV skew = demand for downside protection vs its norm
}
_ANN = 252 ** 0.5       # trading days/yr → annualized vol ÷ this = 1-day σ
# FII index-futures net is read by SIZE, not just sign: a handful of contracts is
# noise. base lean scales to ±0.6 at ±FII_SCALE contracts; below FII_NET_FLAT it
# is treated as "roughly flat" (no directional wording / no option overlay).
FII_SCALE = 40000.0
FII_NET_FLAT = 8000.0
# NIFTY index options carry a STRUCTURAL positive put-skew, so "skew > 0" is the
# resting state, not fresh fear. We read skew RELATIVE to this baseline.
SKEW_BASE = 1.0
# Implied vol persistently overstates realized (variance risk premium); haircut the
# 1-day σ so the "likely range" isn't chronically too wide.
VOL_RP_HAIRCUT = 0.85


def _pcr_lean(p):
    if p is None:
        return None
    if p >= PCR_BULL_STRONG:
        return 0.8
    if p >= PCR_BULL:
        return 0.5
    if p >= 1.0:
        return 0.2
    if p > PCR_BEAR:
        return -0.2
    if p > PCR_BEAR_STRONG:
        return -0.5
    return -0.8


def _lean_word(x):
    return "bullish" if x > 0.12 else "bearish" if x < -0.12 else "neutral"


def _stance_for(score):
    if score >= 30:
        return "Bullish", "bullish"
    if score >= 12:
        return "Cautiously Bullish", "bullish"
    if score <= -30:
        return "Bearish", "bearish"
    if score <= -12:
        return "Cautiously Bearish", "bearish"
    return "Range-bound", "neutral"


def _fmt_lvl(v):
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def build_tomorrow_outlook(bias, index_matrix, participant_view, counts,
                           sectors, india_vix, unusual):
    """Deterministic 'what tomorrow could look like' synthesis for the NIFTY.

    Returns a JSON-safe dict (headline stance + confidence, expected range +
    key levels, per-factor plain-English cards, scenarios, a common-man summary,
    and an honest disclaimer) or {"applicable": False} when there is nothing to
    read. Pure — never raises on well-formed board inputs.
    """
    bias = bias or {}
    counts = counts or {}
    nifty = next((i for i in (index_matrix or []) if i.get("symbol") == "NIFTY"), None)
    factors = []
    contrib = []   # (key, weight, lean)

    # 1. Stock-universe buildup breadth (how the broad F&O list is positioning)
    b_lean = _clamp((bias.get("score") or 0) / 100.0, -1.0, 1.0)
    nlong = counts.get("Long Buildup", 0)
    nshort = counts.get("Short Buildup", 0)
    nsc = counts.get("Short Covering", 0)
    nlu = counts.get("Long Unwinding", 0)
    factors.append({
        "key": "breadth", "name": "Stock buildup breadth",
        "reading": (f"{nlong} long-buildup + {nsc} short-covering vs "
                    f"{nshort} short-buildup + {nlu} long-unwinding"),
        "plain": ("More F&O stocks are seeing fresh buying than selling — a positive "
                  "undertone for the broader market."
                  if b_lean > 0.12 else
                  "More F&O stocks are seeing fresh selling than buying — a cautious "
                  "undertone for the broader market."
                  if b_lean < -0.12 else
                  "Buying and selling across F&O stocks is roughly balanced — no clear "
                  "breadth edge."),
        "lean": _lean_word(b_lean),
    })
    contrib.append(("breadth", OUTLOOK_WEIGHTS["breadth"], b_lean))

    # 2. FII positioning — net index futures + their option read (the smart money)
    head = ((participant_view or {}).get("headline")
            if (participant_view or {}).get("applicable") else None)
    if head:
        net = head.get("fii_index_fut_net") or 0
        meaningful = abs(net) >= FII_NET_FLAT
        f_lean = _clamp(net / FII_SCALE, -1.0, 1.0) * 0.6   # size-scaled, not binary
        rd = head.get("fii_option_read") or ""
        if meaningful and "supportive" in rd:
            f_lean = _clamp(f_lean + 0.2, -1.0, 1.0)
        elif meaningful and "capping" in rd:
            f_lean = _clamp(f_lean - 0.2, -1.0, 1.0)
        factors.append({
            "key": "fii", "name": "FII positioning (smart money)",
            "reading": head.get("summary"),
            "plain": ("Big foreign investors are net LONG index futures — a key "
                      "institutional vote for an up day."
                      if (meaningful and net > 0) else
                      "Big foreign investors are net SHORT index futures — a key "
                      "institutional vote for a down day."
                      if (meaningful and net < 0) else
                      "Foreign investors are positioned roughly flat in index futures — "
                      "no strong directional bet."),
            "lean": _lean_word(f_lean),
        })
        contrib.append(("fii", OUTLOOK_WEIGHTS["fii"], f_lean))

    # 3. Nifty PCR — option writers' confidence
    if nifty and nifty.get("pcr") is not None:
        p = nifty["pcr"]
        pl = _pcr_lean(p) or 0.0
        if pl > 0:
            pcr_plain = ("More puts than calls are being written, meaning option sellers "
                         "expect support to hold (positive).")
            if p >= PCR_BULL_STRONG:
                pcr_plain += (" But a very high PCR can also be a crowded, contrarian "
                              "signal — don't read it as a green light on its own.")
        elif pl < 0:
            pcr_plain = ("More calls than puts are being written, meaning option sellers "
                         "expect upside to be capped (negative).")
        else:
            pcr_plain = ("Put and call writing are balanced — options aren't signalling a "
                         "strong direction.")
        factors.append({
            "key": "pcr", "name": "Options put-call ratio",
            "reading": f"Nifty PCR {p} — {_pcr_word(p)}",
            "plain": pcr_plain,
            "lean": _lean_word(pl),
        })
        contrib.append(("pcr", OUTLOOK_WEIGHTS["pcr"], pl))

    # 4. Max-pain magnet — expiry gravity
    if nifty and nifty.get("max_pain") is not None and nifty.get("max_pain_gap_pct") is not None:
        gap = nifty["max_pain_gap_pct"]   # (spot − maxpain)/maxpain × 100
        m_lean = _clamp(-gap / 2.5, -1.0, 1.0)   # gentle — magnet bites mostly near expiry
        mw = _lean_word(m_lean)
        factors.append({
            "key": "magnet", "name": "Expiry magnet (max pain)",
            "reading": (f"Max pain {_fmt_lvl(nifty['max_pain'])} vs spot "
                        f"{_fmt_lvl(nifty.get('spot'))} ({gap:+.1f}%)"),
            "plain": ("Price is below max pain, which can pull it gently UP — the effect "
                      "strengthens as weekly expiry nears."
                      if mw == "bullish" else
                      "Price is above max pain, which can pull it gently DOWN — the effect "
                      "strengthens as weekly expiry nears."
                      if mw == "bearish" else
                      "Price is sitting close to max pain — the options market is balanced "
                      "here."),
            "lean": mw,
        })
        contrib.append(("magnet", OUTLOOK_WEIGHTS["magnet"], m_lean))

    # 5. Today's index option flow
    if nifty and nifty.get("sentiment"):
        s = nifty["sentiment"]
        o_lean = 0.7 if s == "BULLISH" else -0.7 if s == "BEARISH" else 0.0
        factors.append({
            "key": "opt_flow", "name": "Today's option flow",
            "reading": f"Index option flow {str(s).title()}",
            "plain": ("Fresh option activity today leaned toward supporting the market."
                      if o_lean > 0 else
                      "Fresh option activity today leaned toward pressuring the market."
                      if o_lean < 0 else
                      "Fresh option activity today was directionally mixed."),
            "lean": _lean_word(o_lean),
        })
        contrib.append(("opt_flow", OUTLOOK_WEIGHTS["opt_flow"], o_lean))

    # 6. IV skew — demand for downside protection
    if nifty and nifty.get("iv_skew") is not None:
        sk = nifty["iv_skew"]
        eff = sk - SKEW_BASE                       # vs the index's STRUCTURAL put-skew
        sk_lean = _clamp(-eff / 6.0, -1.0, 1.0)    # elevated put-skew → bearish lean
        factors.append({
            "key": "skew", "name": "Downside-protection demand (IV skew)",
            "reading": f"IV skew {sk:+.1f} (put IV − call IV)",
            "plain": ("Traders are paying MORE than usual for downside protection — a mild "
                      "caution flag."
                      if eff > 0.8 else
                      "Downside protection is unusually cheap (calls bid up) — traders lean "
                      "toward upside, a mild positive."
                      if eff < -0.8 else
                      "Option skew is around its usual level for the index — no fresh fear "
                      "or greed in option prices."),
            "lean": _lean_word(sk_lean),
        })
        contrib.append(("skew", OUTLOOK_WEIGHTS["skew"], sk_lean))

    if not contrib:
        return {"applicable": False}

    wsum = sum(w for _, w, _ in contrib)
    net = (sum(w * l for _, w, l in contrib) / wsum) if wsum else 0.0
    score = int(round(_clamp(net * 100, -100.0, 100.0)))
    stance, direction = _stance_for(score)

    # Confidence: magnitude + how many factors AGREE with the net − a vol penalty.
    if net != 0:
        agree = sum(1 for _, _, l in contrib if l != 0 and (l > 0) == (net > 0)) / len(contrib)
    else:
        agree = 0.5
    conf = 30 + abs(net) * 45 + (agree - 0.5) * 30
    try:
        vix = float(india_vix) if india_vix is not None else None
    except (TypeError, ValueError):
        vix = None
    if vix is not None and vix > 15:
        conf -= _clamp((vix - 15) * 1.5, 0.0, 18.0)
    confidence = int(round(_clamp(conf, 25.0, 80.0)))

    # Expected 1-day move + range (≈68% / ±1σ). Prefer India VIX, else ATM IV,
    # else a sane 0.8%/day default (expressed annualized so ÷√252 → 0.8).
    if vix and vix > 0:
        vol_ann = vix
    elif nifty and nifty.get("atm_iv"):
        vol_ann = nifty["atm_iv"]
    else:
        vol_ann = 0.8 * _ANN
    sigma_1d = round(vol_ann / _ANN * VOL_RP_HAIRCUT, 2)   # IV→realized haircut
    spot = nifty.get("spot") if nifty else None
    rng_lo = rng_hi = None
    if spot:
        rng_lo = round(spot * (1 - sigma_1d / 100.0))
        rng_hi = round(spot * (1 + sigma_1d / 100.0))

    support = nifty.get("put_wall") if nifty else None
    resistance = nifty.get("call_wall") if nifty else None
    magnet = nifty.get("max_pain") if nifty else None

    # ── common-man narrative ──
    fii_clause = ""
    if head:
        nnet = head.get("fii_index_fut_net") or 0
        if abs(nnet) >= FII_NET_FLAT:
            fii_clause = (f" Foreign institutions are net {'long' if nnet > 0 else 'short'} "
                          f"in index futures, a key institutional tell.")
        else:
            fii_clause = " Foreign institutions are positioned roughly flat in index futures."
    pcr_clause = (f" The options market is {_pcr_word(nifty.get('pcr'))}."
                  if (nifty and nifty.get("pcr") is not None) else "")
    sec_clause = ""
    for s in (sectors or []):
        if s.get("direction") in ("bullish", "bearish"):
            sec_clause = (f" Money is rotating "
                          f"{'into' if s['direction'] == 'bullish' else 'out of'} "
                          f"{s.get('sector')} ({s.get('count', 0)} names).")
            break
    lvl_clause = ""
    if spot and support and resistance:
        lvl_clause = (f" Likely trade is between roughly {_fmt_lvl(rng_lo)} and "
                      f"{_fmt_lvl(rng_hi)}, with {_fmt_lvl(magnet)} acting as a magnet, "
                      f"the {_fmt_lvl(support)} put-wall as a floor and the "
                      f"{_fmt_lvl(resistance)} call-wall as a ceiling.")

    one_liner = {
        "Bullish": "Derivatives data points to a positive session tomorrow.",
        "Cautiously Bullish": "A cautiously positive setup for tomorrow.",
        "Range-bound": "A balanced, range-bound session looks most likely tomorrow.",
        "Cautiously Bearish": "A cautiously weak setup for tomorrow.",
        "Bearish": "Derivatives data points to a weak session tomorrow.",
    }[stance]

    summary = (f"Reading today's F&O close, tomorrow looks {stance.lower()} "
               f"(conviction {confidence}/100)." + fii_clause + pcr_clause
               + sec_clause + lvl_clause)

    bull_case = (f"A sustained move above {_fmt_lvl(resistance)} opens more upside."
                 if resistance else "A decisive break above today's high opens more upside.")
    bear_case = (f"A break below {_fmt_lvl(support)} turns the tape weak and invites fresh shorts."
                 if support else "A break below today's low turns the tape weak.")
    base = (f"Most likely a {stance.lower()} session between {_fmt_lvl(rng_lo)} and "
            f"{_fmt_lvl(rng_hi)}." if spot else f"Most likely a {stance.lower()} session.")

    return {
        "applicable": True,
        "headline": {"stance": stance, "direction": direction, "score": score,
                     "confidence": confidence, "one_liner": one_liner},
        "index": {"symbol": "NIFTY", "label": "Nifty 50", "spot": spot,
                  "expected_move_pct": sigma_1d, "range_low": rng_lo, "range_high": rng_hi,
                  "support": support, "resistance": resistance, "magnet": magnet},
        "factors": factors,
        "scenario": {"base": base, "bull_case": bull_case, "bear_case": bear_case,
                     "flip_level": support},
        "summary": summary,
        "disclaimer": ("A probability-based read of today's closing derivatives data — "
                       "not a guarantee. Overnight global cues, gap-openings and news can "
                       "override it."),
        "india_vix": vix,
    }


def build_smart_money_board(snapshot, watchlist=None, delivery=None, deals=None,
                            participant=None, india_vix=None, prev_snapshot=None):
    """
    Assemble the full F&O Smart-Money board from a raw bhavcopy snapshot.

    snapshot     : {"bhavcopy_date","fetched_at","futures":{...},"options":{...}}
    watchlist    : optional list of tickers to flag/highlight.
    delivery     : optional {SYM: deliv_pct} from cash bhavdata (conviction + spikes).
    deals        : optional bulk/block deal dicts (passed through).
    participant  : optional {COHORT: {...}} from oi_data.get_participant_oi().
    india_vix    : optional float (India VIX level) for the volatility context tile.
    prev_snapshot: optional previous trading day's snapshot — when supplied, every
                   row gets a `vs_prev` diff (flipped / new / OI delta) and the
                   board carries a top-level `changes` summary (#4 day-over-day).

    Returns a JSON-safe board dict. Never raises.
    """
    snapshot = snapshot or {}
    futures = snapshot.get("futures") or {}
    options = snapshot.get("options") or {}
    asof = snapshot.get("bhavcopy_date")
    delivery = {normalize_ticker(k): v for k, v in (delivery or {}).items()}
    watchset = {normalize_ticker(t) for t in (watchlist or [])}

    # Day-over-day diff (optional). Attach vs_prev to every row by symbol; because
    # the buildup tables reference the SAME row dicts, the tags propagate there too.
    diff = None
    if prev_snapshot and (prev_snapshot.get("futures")):
        try:
            diff = diff_snapshots({"futures": futures}, prev_snapshot)
        except Exception:
            diff = None
    diff_by_sym = (diff or {}).get("by_symbol") or {}

    rows = []
    for sym, fut in futures.items():
        sym = normalize_ticker(sym)
        if not sym:
            continue
        if fut.get("is_index"):
            continue                        # index futures → index matrix, not buildup
        opt_entry = options.get(sym) or {}
        deliv = delivery.get(sym)
        row = _build_row(sym, fut, opt_entry, deliv, watchset)
        if diff_by_sym:
            row["vs_prev"] = diff_by_sym.get(sym)
        rows.append(row)

    # Buildup tables (drop thin, ranked by conviction)
    buildups = {k: [] for k in
                ("LONG_BUILDUP", "SHORT_BUILDUP", "SHORT_COVERING", "LONG_UNWINDING")}
    for r in rows:
        if r["buildup"] in buildups and r["val_cr"] >= MIN_VAL_CR:
            buildups[r["buildup"]].append(r)
    for k in buildups:
        buildups[k].sort(key=lambda x: x["conviction"], reverse=True)
        buildups[k] = buildups[k][:MAX_LIST]

    unusual = [r for r in rows
               if abs(r["oi_chg_pct"]) >= UNUSUAL_OI_PCT and r["val_cr"] >= MIN_VAL_CR
               and r["buildup"] != "NEUTRAL"]
    # Rank by measured surge magnitude, but keep brand-new/all-fresh contracts (whose
    # ΔOI% is a synthetic sentinel) BELOW genuinely-measured surges so they can't claim
    # "largest OI surge".
    unusual.sort(key=lambda x: (x.get("fresh_oi", False), -abs(x["oi_chg_pct"])))
    unusual = unusual[:MAX_UNUSUAL]

    deliv_rows = [r for r in rows if r["delivery_pct"] is not None and r["delivery_pct"] >= 55]
    deliv_rows.sort(key=lambda x: x["delivery_pct"], reverse=True)
    deliv_rows = deliv_rows[:MAX_DELIVERY]

    index_matrix = _index_matrix(options, futures, asof)
    # The headline bias + sector clustering must use only LIQUID names — every other
    # panel drops thin names, and with a fixed conviction base, penny/illiquid futures
    # would otherwise skew the single most prominent number on the board.
    liquid = [r for r in rows if r["val_cr"] >= MIN_VAL_CR]
    sectors = _sector_clustering(liquid)
    bias = _market_bias(liquid, index_matrix)
    participant_view = _participant_positioning(participant)
    narrative = _narrative(bias, buildups, index_matrix, sectors, unusual, participant_view)

    # Deterministic setups for the top-conviction directional names.
    top_rows = sorted([r for r in rows if r["buildup"] != "NEUTRAL" and r["val_cr"] >= MIN_VAL_CR],
                      key=lambda x: x["conviction"], reverse=True)[:MAX_SETUPS]
    setups = []
    for r in top_rows:
        view = option_chain_view(r["symbol"], options.get(r["symbol"]),
                                 futures.get(r["symbol"]), asof)
        setups.append(suggest_setup(r, view))

    watch_rows = [r for r in rows if r["in_watchlist"]]
    watch_rows.sort(key=lambda x: x["conviction"], reverse=True)

    counts = {BUILDUP_META[k]["label"]: len([r for r in rows if r["buildup"] == k])
              for k in BUILDUP_META}

    # Plain-English next-session synthesis (the common-man overview), built from
    # the same factors the board already computed. Pure; guarded so a bad input
    # can never break the board.
    try:
        outlook = build_tomorrow_outlook(bias, index_matrix, participant_view,
                                         counts, sectors, india_vix, unusual)
    except Exception:
        outlook = {"applicable": False}

    return {
        "bhavcopy_date": snapshot.get("bhavcopy_date"),
        "fetched_at": snapshot.get("fetched_at"),
        "age_seconds": snapshot.get("age_seconds"),
        # Source/as-of provenance (set by oi_data: 'eod' | 'eod_restored' |
        # 'intraday'). The frontend uses this to label LIVE vs END-OF-DAY honestly.
        "source": snapshot.get("source", "eod"),
        "as_of": snapshot.get("as_of"),
        # Day-over-day change summary (#4): None when no previous snapshot exists.
        "changes": (diff["summary"] if diff else None),
        "changes_since": ((diff["summary"].get("prev_date") if diff else None)),
        "universe_count": len(rows),
        "india_vix": india_vix,
        "market_bias": bias,
        "outlook": outlook,
        "narrative": narrative,
        "counts": counts,
        "index_matrix": index_matrix,
        "buildups": buildups,
        "unusual_oi": unusual,
        "delivery_spikes": deliv_rows,
        "sectors": sectors,
        "participant": participant_view,
        "setups": setups,
        "deals": (deals or [])[:30],
        "watchlist": watch_rows,
        "applicable": bool(rows),
    }
