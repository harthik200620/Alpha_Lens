"""Reset signals that were wrongly marked as Target Hit / Stop Loss Hit
when actual current % change doesn't justify it."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'news_cache.db')
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Find signals where status is Target Hit but pct < 3%, or Stop Loss but pct > -1.5%
c.execute("""
    SELECT id, ticker, base_price, current_price, estimated_change_percent, status, impact
    FROM stock_impact 
    WHERE (status = 'Predicted Target Hit' AND ABS(estimated_change_percent) < 3.0)
    OR (status = 'Stop Loss Hit' AND ABS(estimated_change_percent) < 1.5)
""")
rows = c.fetchall()
print(f"Found {len(rows)} wrongly-triggered signals")

fixed = 0
for r in rows:
    sid, ticker, base, curr, pct, status, impact = r
    print(f"  Resetting {ticker:15s} ID={sid} pct={pct}% {status} -> Active View")
    c.execute("UPDATE stock_impact SET status='Active View' WHERE id=?", (sid,))
    fixed += 1

conn.commit()
conn.close()
print(f"\nReset {fixed} signals to Active View")
