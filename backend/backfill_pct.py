"""
Backfill estimated_change_percent for resolved signals
using daily OHLC (covers older signals beyond 7-day 15m window).

For BULLISH Stop Loss Hit → find the worst LOW price since signal creation
For BULLISH Target Hit    → find the best HIGH price since signal creation
"""
import sqlite3, os, sys, time, requests
from datetime import datetime, timedelta, timezone

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'news_cache.db')
YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def get_resolution_pct(ticker, base_price, direction, status, signal_time_str):
    """
    Download daily OHLC from signal date to today.
    For BULLISH Stop Loss Hit → return worst (most negative) daily low %
    For BULLISH Target Hit    → return best (most positive) daily high %
    For BEARISH: inverse.
    """
    try:
        # Convert signal time to a date
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                sig_dt = datetime.strptime(signal_time_str, fmt)
                break
            except ValueError:
                continue
        else:
            return None

        is_bull = 'BULL' in direction.upper()
        is_stop = 'Stop' in status or 'Reacted' in status

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1mo&interval=1d"
        resp = requests.get(url, headers=YF_HEADERS, timeout=10)
        data = resp.json()
        result = data.get('chart', {}).get('result', [{}])[0]
        timestamps = result.get('timestamp', [])
        quotes = result.get('indicators', {}).get('quote', [{}])[0]

        if not timestamps:
            return None

        highs  = quotes.get('high',  [])
        lows   = quotes.get('low',   [])
        closes = quotes.get('close', [])

        best_pct = None
        for i, ts in enumerate(timestamps):
            day = datetime.utcfromtimestamp(ts)
            if day.date() < sig_dt.date():
                continue  # skip days before signal

            try:
                h = float(highs[i])  if highs[i]  else None
                l = float(lows[i])   if lows[i]   else None
                cl= float(closes[i]) if closes[i] else None
            except (IndexError, TypeError, ValueError):
                continue

            if h is None and l is None:
                continue

            if is_bull and is_stop:
                # Find worst LOW (most negative %)
                if l:
                    pct = round((l - base_price) / base_price * 100, 2)
                    if best_pct is None or pct < best_pct:
                        best_pct = pct
            elif is_bull and not is_stop:
                # Find best HIGH (most positive %)
                if h:
                    pct = round((h - base_price) / base_price * 100, 2)
                    if best_pct is None or pct > best_pct:
                        best_pct = pct
            elif not is_bull and is_stop:
                # BEARISH Stop: find worst HIGH (most positive from base = bad)
                if h:
                    pct = round((h - base_price) / base_price * 100, 2)
                    if best_pct is None or pct > best_pct:
                        best_pct = pct
            else:
                # BEARISH Target: find best LOW (most negative from base = good)
                if l:
                    pct = round((l - base_price) / base_price * 100, 2)
                    if best_pct is None or pct < best_pct:
                        best_pct = pct

        return best_pct

    except Exception as e:
        print(f"    Error: {e}")
        return None


conn = sqlite3.connect(DB, timeout=30)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Target ALL resolved signals where estimated_change_percent doesn't match status
# e.g. Stop Loss Hit but estimated_change_percent is POSITIVE (wrong)
c.execute("""
    SELECT id, ticker, impact, base_price, status, created_at, estimated_change_percent
    FROM stock_impact
    WHERE status IN ('Stop Loss Hit', 'Predicted Target Hit', 'Reacted Against Prediction')
      AND base_price > 0
""")
rows = c.fetchall()

# Filter to rows where the stored pct doesn't make sense for the status
to_fix = []
for r in rows:
    status = r['status']
    ecp = r['estimated_change_percent']
    is_stop = 'Stop' in status or 'Reacted' in status
    is_bull = 'bull' in r['impact'].lower()
    # Stop Loss should be negative for BULLISH or positive for BEARISH
    wrong = False
    if ecp is not None:
        if is_bull and is_stop and float(ecp) > 0:
            wrong = True  # Stop loss can't be positive for BULLISH
        if not is_bull and is_stop and float(ecp) < 0:
            wrong = True  # Stop loss can't be negative for BEARISH
        if not is_stop and is_bull and float(ecp) < 0:
            wrong = True  # Target hit can't be negative for BULLISH
    else:
        wrong = True
    if wrong:
        to_fix.append(r)

print(f"Found {len(to_fix)} signals with incorrect estimated_change_percent\n")

fixed = 0
for r in to_fix:
    sid        = r['id']
    ticker     = r['ticker']
    bp         = float(r['base_price'])
    status     = r['status']
    created_at = r['created_at']
    direction  = 'BULLISH' if 'bull' in r['impact'].lower() else 'BEARISH'

    print(f"  {ticker} (ID={sid}) {direction} {status} | current stored: {r['estimated_change_percent']}")
    pct = get_resolution_pct(ticker, bp, direction, status, created_at)

    if pct is not None:
        conn.execute(
            "UPDATE stock_impact SET estimated_change_percent = ? WHERE id = ?",
            (pct, sid)
        )
        conn.commit()
        print(f"    -> Updated to {pct:+.2f}%")
        fixed += 1
    else:
        print(f"    -> Could not determine (data unavailable)")
    time.sleep(0.4)

conn.close()
print(f"\nDone. Fixed {fixed}/{len(to_fix)} signals.")
