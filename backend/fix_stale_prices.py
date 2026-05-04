"""
One-shot fix: Update current_price for all stock_impact rows
where current_price == base_price by fetching real prices via Yahoo Finance API.
"""
import sqlite3, os, sys, time, requests
import pandas as pd

DB = os.path.join(os.path.dirname(__file__), '..', 'news_cache.db')

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def get_latest_close(ticker):
    """Fetch last close price via Yahoo Finance chart API (no key needed)."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1mo&interval=1d"
        resp = requests.get(url, headers=YF_HEADERS, timeout=10)
        data = resp.json()
        result = data.get('chart', {}).get('result', [{}])[0]
        meta = result.get('meta', {})
        # Try regularMarketPrice first (most accurate)
        price = meta.get('regularMarketPrice') or meta.get('previousClose')
        if price:
            return round(float(price), 2)
        # Fallback to close series
        closes = result.get('indicators', {}).get('quote', [{}])[0].get('close', [])
        closes = [c for c in closes if c is not None]
        if closes:
            return round(float(closes[-1]), 2)
    except Exception as e:
        print(f"    Yahoo API error: {e}")
    return None

conn = sqlite3.connect(DB, timeout=30)
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("""
    SELECT id, ticker, base_price, current_price, status, created_at
    FROM stock_impact
    WHERE ABS(COALESCE(current_price, 0) - COALESCE(base_price, 0)) < 0.01
      AND base_price > 0
""")
rows = c.fetchall()
print(f"Found {len(rows)} rows with stale current_price. Fixing...\n")

updated = 0
for r in rows:
    sid    = r['id']
    ticker = r['ticker']
    bp     = float(r['base_price'])
    status = r['status']

    cp = get_latest_close(ticker)
    if cp and cp > 0:
        diff = round((cp - bp) / bp * 100, 2)
        conn.execute("UPDATE stock_impact SET current_price = ? WHERE id = ?", (cp, sid))
        conn.commit()
        arrow = "+" if diff >= 0 else ""
        print(f"  FIXED {ticker} (ID={sid}): base=Rs.{bp:.2f} -> current=Rs.{cp:.2f}  ({arrow}{diff:.2f}%)  [{status}]")
        updated += 1
    else:
        print(f"  SKIP  {ticker} (ID={sid}): Could not fetch price")
    time.sleep(0.3)

conn.close()
print(f"\nDone. Updated {updated}/{len(rows)} rows.")
