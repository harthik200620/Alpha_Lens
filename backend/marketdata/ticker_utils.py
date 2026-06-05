"""
Ticker normalization + news-candidate screening helpers, extracted verbatim
from app.py:
  * normalize_ticker / ticker_base / is_supported_equity_ticker
  * _keyword_mentions_ticker / _macro_mentions / _headline_direction
  * candidate_quality_score

Depends only on stdlib re, the angelone_shim (yf) scrip caches, and the pure
data/rule modules — never on app.py — so there is no import cycle. app.py
imports these names back, so every call site is unchanged.
"""
import re
import angelone_shim as yf
from newsproc.news_rules import STOCK_KEYWORD_MAP, BULLISH_KEYWORDS, BEARISH_KEYWORDS
from newsproc.news_data import (
    INDEX_LIKE_SYMBOLS, MACRO_IMPACT_MAP, MATERIAL_EVENT_KEYWORDS, LOW_SIGNAL_PHRASES,
)


def normalize_ticker(ticker):
    if not ticker:
        return None
    t = str(ticker).upper().strip()
    t = t.replace("NSE:", "").replace("BSE:", "")
    t = t.replace(".NSE", ".NS").replace(".BSE", ".BO")
    t = re.sub(r'[^A-Z0-9&.\-]', '', t)
    if not t or t.startswith("^"):
        return None
    if not (t.endswith(".NS") or t.endswith(".BO")):
        if re.fullmatch(r'[A-Z0-9&\-]{2,16}', t):
            t = f"{t}.NS"
        else:
            return None
    base = t.rsplit(".", 1)[0]
    suffix = t.rsplit(".", 1)[1]
    
    # Resolve common AI ticker hallucinations / aliases
    _TICKER_ALIASES = {
        'INTERGLOBE': 'INDIGO',
        'INTERGLOBEAVIATION': 'INDIGO',
        'MAHINDRA': 'M&M',
        'MAHINDRA&MAHINDRA': 'M&M',
        'MAHINDRAANDMAHINDRA': 'M&M',
        'MANDM': 'M&M',
        'LARSEN': 'LT',
        'LARSEN&TOUBRO': 'LT',
        'L&T': 'LT',
        'LANDT': 'LT',
        'LARSENANDTOUBRO': 'LT',
        'BAJAJAUTO': 'BAJAJ-AUTO',
        'TATACONSUMER': 'TATACONSUM',
        'HUL': 'HINDUNILVR',
        'KOTAK': 'KOTAKBANK',
        'SBI': 'SBIN',
        'TATAMOTORS': 'TMPV',
        'TATAMOTOR': 'TMPV',
    }
    if base in _TICKER_ALIASES:
        base = _TICKER_ALIASES[base]
        t = f"{base}.{suffix}"

    if base in INDEX_LIKE_SYMBOLS:
        return None
    return t

def ticker_base(ticker):
    t = normalize_ticker(ticker)
    return t.rsplit(".", 1)[0] if t else ""

def is_supported_equity_ticker(ticker):
    t = normalize_ticker(ticker)
    if not t:
        return False
    known = {normalize_ticker(v) for v in STOCK_KEYWORD_MAP.values()}
    if t in known:
        return True
    base = ticker_base(t)
    try:
        # If the Angel One scrip master is already loaded, use it as a guard
        # against obvious AI hallucinations. If it is not loaded, stay permissive
        # so the news worker does not block on a network call.
        if getattr(yf, "_scrip_loaded", False):
            if t.endswith(".NS"):
                return base in getattr(yf, "_scrip_cache", {})
            if t.endswith(".BO"):
                return base in getattr(yf, "_bse_cache", {})
    except Exception:
        pass
    return re.fullmatch(r'[A-Z0-9&\-]{2,16}\.(NS|BO)', t) is not None

def _keyword_mentions_ticker(text, ticker):
    if not text or not ticker:
        return False
    text_l = text.lower()
    ticker_n = normalize_ticker(ticker)
    for keyword, mapped_ticker in STOCK_KEYWORD_MAP.items():
        if normalize_ticker(mapped_ticker) != ticker_n:
            continue
        pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
        if re.search(pattern, text_l):
            return True
    base = ticker_base(ticker_n).lower()
    return bool(base and re.search(r'\b' + re.escape(base) + r'\b', text_l))

def _macro_mentions(text):
    text_l = (text or "").lower()
    return [kw for kw in MACRO_IMPACT_MAP if kw in text_l]

def _headline_direction(headline, context=""):
    h = f"{headline or ''} {context or ''}".lower()
    bull_score = sum(1 for kw in BULLISH_KEYWORDS if kw in h)
    bear_score = sum(1 for kw in BEARISH_KEYWORDS if kw in h)
    # Bug #4 fix: when no sentiment keywords match at all, return NEUTRAL instead
    # of silently defaulting to BULLISH.  Also changed >= to > so a genuine tie
    # (both sides have the same non-zero score) is also treated as NEUTRAL.
    if bull_score == 0 and bear_score == 0:
        return 'NEUTRAL'
    return 'BULLISH' if bull_score > bear_score else 'BEARISH'

def candidate_quality_score(headline, context, ticker, source="rule", materiality_hint=65):
    text = f"{headline or ''} {context or ''}"
    text_l = text.lower()
    try:
        score = int(float(re.sub(r'[^0-9.]', '', str(materiality_hint or 65)) or 65))
    except Exception:
        score = 65
    source_l = (source or "rule").lower()

    if source_l == "llm":
        score += 12
    elif source_l == "macro":
        score += 6
    else:
        score += 4

    if _keyword_mentions_ticker(headline, ticker):
        score += 22
    elif _keyword_mentions_ticker(context, ticker):
        score += 10

    macro_hits = _macro_mentions(text_l)
    if macro_hits:
        score += min(14, len(macro_hits) * 5)

    material_hits = sum(1 for kw in MATERIAL_EVENT_KEYWORDS if kw in text_l)
    score += min(18, material_hits * 4)

    low_signal_hits = sum(1 for phrase in LOW_SIGNAL_PHRASES if phrase in text_l)
    score -= min(18, low_signal_hits * 6)

    if not _keyword_mentions_ticker(text, ticker) and not macro_hits and source_l != "llm":
        score -= 14

    return max(10, min(99, score))
