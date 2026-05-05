"""
COMPREHENSIVE FIX:
1. Fix ALL active signals base_price and current_price regardless of date
2. Check historical OHLC to detect if target (3%) or stop (1.5%) was hit intraday
3. Mark resolved trades as 'Predicted Target Hit' or 'Stop Loss Hit'

This handles:
- Market-hours signals from any date (uses Yahoo 5d/1m for base_price lookup)
- After-hours signals (uses Yahoo official 3:30 PM close for base_price)
- Historical target/stop detection using daily OHLC
"""
import sqlite3, sys, io, requests, os
from datetime import datetime, timedelta, timezone, date
from email.utils import parsedate_to_datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
IST = timezone(timedelta(hours=5, minutes=30))

TARGET_PCT = 3.0   # 3% target
STOP_PCT   = 1.5   # 1.5% stop loss

# ── Cache ──────────────────────────────────────────────
_bar_cache_5d  = {}   # ticker -> list of (bar_dt, close)  [1-min, 5-day window]
_ohlc_cache    = {}   # ticker -> list of (date, open, high, low, close)  [daily]

def fetch_5d_1m(ticker):
    """Fetch 5d 1-min bars from Yahoo Finance."""
    if ticker in _bar_cache_5d:
        return _bar_cache_5d[ticker]
    try:
        h = {"User-Agent": "Mozilla/5.0"}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1m"
        r = requests.get(url, headers=h, timeout=10)
        data = r.json()
        result = data['chart']['result'][0]
        tss    = result['timestamp']
        closes = result['indicators']['quote'][0]['close']
        bars = []
        for ts, cl in zip(tss, closes):
            if cl is None:
                continue
            bar_dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST)
            bars.append((bar_dt, round(float(cl), 2)))
        bars.sort(key=lambda x: x[0])
        _bar_cache_5d[ticker] = bars
        print(f"    [Yahoo 5d/1m] {ticker}: {len(bars)} bars ({bars[0][0].strftime('%d-%b') if bars else 'N/A'} to {bars[-1][0].strftime('%d-%b') if bars else 'N/A'})")
        return bars
    except Exception as e:
        print(f"    [Yahoo 5d/1m ERROR] {ticker}: {e}")
        _bar_cache_5d[ticker] = []
        return []

def fetch_daily_ohlc(ticker, days=14):
    """Fetch daily OHLC from Yahoo Finance."""
    if ticker in _ohlc_cache:
        return _ohlc_cache[ticker]
    try:
        h = {"User-Agent": "Mozilla/5.0"}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={days}d&interval=1d"
        r = requests.get(url, headers=h, timeout=10)
        data = r.json()
        result = data['chart']['result'][0]
        tss  = result['timestamp']
        q    = result['indicators']['quote'][0]
        rows = []
        for ts, op, hi, lo, cl in zip(tss,
                                       q.get('open', [None]*len(tss)),
                                       q.get('high', [None]*len(tss)),
                                       q.get('low',  [None]*len(tss)),
                                       q.get('close',[None]*len(tss))):
            if hi is None or lo is None or cl is None:
                continue
            d = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST).date()
            rows.append((d, float(op or cl), float(hi), float(lo), float(cl)))
        _ohlc_cache[ticker] = rows
        return rows
    except Exception as e:
        print(f"    [Yahoo OHLC ERROR] {ticker}: {e}")
        _ohlc_cache[ticker] = []
        return []

def get_price_at_time(ticker, target_dt):
    """Get 1-min close at or just before target_dt (IST)."""
    bars = fetch_5d_1m(ticker)
    best = None
    for bar_dt, cl in bars:
        if bar_dt <= target_dt:
            best = cl
        else:
            break
    return best

def get_official_close_on_date(ticker, target_date):
    """Get official 3:30 PM close for ticker on target_date from 5d/1m bars."""
    bars = fetch_5d_1m(ticker)
    best = None
    for bar_dt, cl in bars:
        if bar_dt.date() != target_date:
            continue
        t = bar_dt.hour * 60 + bar_dt.minute
        if (15 * 60 + 28) <= t <= (15 * 60 + 31):
            best = cl
    if best:
        return best
    # Fallback: daily OHLC close for that date
    rows = fetch_daily_ohlc(ticker)
    for d, op, hi, lo, cl in rows:
        if d == target_date:
            return round(cl, 2)
    return None

def get_most_recent_close(ticker):
    """Get most recent 3:30 PM close from 5d/1m bars."""
    bars = fetch_5d_1m(ticker)
    best_p, best_dt = None, None
    for bar_dt, cl in bars:
        t = bar_dt.hour * 60 + bar_dt.minute
        if (15 * 60 + 28) <= t <= (15 * 60 + 31):
            best_p  = cl
            best_dt = bar_dt
    return best_p, best_dt

def prev_trading_day(dt_ist):
    """Most recent trading weekday before dt_ist."""
    d = dt_ist.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

def check_target_stop(ticker, base_price, since_date, is_bullish):
    """
    Check if target or stop was hit in OHLC data since since_date.
    Returns ('Predicted Target Hit'|'Stop Loss Hit'|None, pct)
    """
    if base_price <= 0:
        return None, None
    rows = fetch_daily_ohlc(ticker)
    for d, op, hi, lo, cl in rows:
        if d < since_date:
            continue
        h_pct = (hi - base_price) / base_price * 100
        l_pct = (lo - base_price) / base_price * 100
        if is_bullish:
            if l_pct <= -STOP_PCT:
                return 'Stop Loss Hit', round(l_pct, 2)
            if h_pct >= TARGET_PCT:
                return 'Predicted Target Hit', round(h_pct, 2)
        else:
            if h_pct >= STOP_PCT:
                return 'Stop Loss Hit', round(h_pct, 2)
            if l_pct <= -TARGET_PCT:
                return 'Predicted Target Hit', round(l_pct, 2)
    return None, None

# ── DB ────────────────────────────────────────────────
conn = sqlite3.connect('news_cache.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

rows = c.execute("""
    SELECT si.id, si.ticker, si.base_price, si.current_price,
           si.estimated_change_percent, si.status, si.impact,
           n.news_time, n.headline
    FROM stock_impact si
    JOIN news n ON si.news_id = n.id
    WHERE si.status IN ('Active View')
    ORDER BY si.id DESC
""").fetchall()

print(f"Processing {len(rows)} active signals...\n")
price_fixes = 0
status_fixes = 0
now_ist = datetime.now(IST)

for r in rows:
    try:
        pub_dt = parsedate_to_datetime(r['news_time']).astimezone(IST)
    except:
        continue

    ticker     = r['ticker']
    base       = r['base_price'] or 0
    stored_cur = r['current_price'] or 0
    is_bullish = (r['impact'] or 'bullish').lower() != 'bearish'
    pub_t      = pub_dt.hour * 60 + pub_dt.minute
    pub_date   = pub_dt.date()
    is_market  = (pub_dt.weekday() < 5 and (9*60+15) <= pub_t <= (15*60+30))

    # ── Step 1: Determine correct base_price ──────────────
    if is_market:
        # Market-hours: get 1-min price at exact publication time
        new_base = get_price_at_time(ticker, pub_dt)
        if not new_base:
            # Fallback to daily open for that date
            rows_ohlc = fetch_daily_ohlc(ticker)
            for d, op, hi, lo, cl in rows_ohlc:
                if d == pub_date:
                    new_base = round(op, 2)
                    break
    else:
        # After-hours/pre-market: base = official close of that trading day
        if pub_t >= (15*60+30):
            # Post-market: base = close of TODAY (the day news was published)
            base_date = pub_date
        else:
            # Pre-market: base = close of YESTERDAY
            base_date = prev_trading_day(pub_dt)
        new_base = get_official_close_on_date(ticker, base_date)

    # ── Step 2: Get most recent official close ─────────────
    new_cur, cur_dt = get_most_recent_close(ticker)

    if not new_base or new_base <= 0:
        print(f"  [SKIP base] ID={r['id']:>3} {ticker:18s} | pub={pub_dt.strftime('%d-%b %H:%M')} — could not fetch base")
        continue
    if not new_cur or new_cur <= 0:
        print(f"  [SKIP cur]  ID={r['id']:>3} {ticker:18s} | pub={pub_dt.strftime('%d-%b %H:%M')} — could not fetch close")
        continue

    new_pct  = round((new_cur - new_base) / new_base * 100, 2)
    base_changed = abs(new_base - base) > 0.1
    cur_changed  = abs(new_cur - stored_cur) > 0.1

    if base_changed or cur_changed:
        label = "MKTHR" if is_market else "AFTER"
        print(f"  [{label}] ID={r['id']:>3} {ticker:18s} | {pub_dt.strftime('%d-%b %H:%M')} "
              f"base: {base:.2f}→{new_base:.2f}  cur: {stored_cur:.2f}→{new_cur:.2f}  pct: {new_pct:+.2f}%")
        c.execute("UPDATE stock_impact SET base_price=?, current_price=?, estimated_change_percent=? WHERE id=?",
                  (new_base, new_cur, new_pct, r['id']))
        price_fixes += 1
        base = new_base
    else:
        new_pct = round((new_cur - new_base) / new_base * 100, 2)

    # ── Step 3: Check historical target/stop hit ───────────
    # Check from the day after signal creation to today
    since_date = pub_date  # include the day of publication for intraday check
    hit_status, hit_pct = check_target_stop(ticker, new_base, since_date, is_bullish)
    if hit_status:
        print(f"  [HIT!] ID={r['id']:>3} {ticker:18s} | {hit_status} pct={hit_pct:+.2f}%")
        c.execute("""
            UPDATE stock_impact
            SET status=?, estimated_change_percent=?, current_price=?
            WHERE id=?
        """, (hit_status, hit_pct, new_cur, r['id']))
        status_fixes += 1

conn.commit()

print(f"\n{'='*60}")
print(f"Price fixes:  {price_fixes}")
print(f"Status fixes: {status_fixes}")

# ── Final State ────────────────────────────────────────
print(f"\n=== All Active Signals (final) ===")
rows2 = c.execute("""
    SELECT si.id, si.ticker, si.base_price, si.current_price,
           si.estimated_change_percent, si.status, n.news_time
    FROM stock_impact si JOIN news n ON si.news_id = n.id
    WHERE si.status = 'Active View'
    ORDER BY si.id DESC LIMIT 30
""").fetchall()
for r in rows2:
    bp = r['base_price'] or 0
    cp = r['current_price'] or 0
    pct = r['estimated_change_percent'] or 0
    try:
        pub = parsedate_to_datetime(r['news_time']).astimezone(IST)
        ts = pub.strftime('%d-%b %H:%M')
    except:
        ts = "?"
    flag = " ←0%" if abs(cp - bp) < 0.5 else ""
    print(f"  ID={r['id']:>3} {r['ticker']:18s} [{ts}] base={bp:>9.2f} cur={cp:>9.2f} {pct:>+7.2f}%{flag}")

print(f"\n=== Resolved Signals ===")
rows3 = c.execute("""
    SELECT si.id, si.ticker, si.base_price, si.current_price,
           si.estimated_change_percent, si.status, n.news_time
    FROM stock_impact si JOIN news n ON si.news_id = n.id
    WHERE si.status IN ('Predicted Target Hit', 'Stop Loss Hit')
    ORDER BY si.id DESC LIMIT 20
""").fetchall()
for r in rows3:
    bp = r['base_price'] or 0
    cp = r['current_price'] or 0
    pct = r['estimated_change_percent'] or 0
    try:
        pub = parsedate_to_datetime(r['news_time']).astimezone(IST)
        ts = pub.strftime('%d-%b %H:%M')
    except:
        ts = "?"
    print(f"  ID={r['id']:>3} {r['ticker']:18s} [{ts}] base={bp:>9.2f} cur={cp:>9.2f} {pct:>+7.2f}% | {r['status']}")

conn.close()
print("\nDone!")
