"""
Final comprehensive fix for all price data in backend/news_cache.db.

For active signals: set base_price = current_price (LTP = today's close)
  - This is the correct "price at news time" for after-hours news
  - diff = 0% until market opens tomorrow

For resolved signals: leave everything as-is (their estimated_change_percent 
  is the frozen resolution % and should never be touched)
"""
import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

conn = sqlite3.connect('news_cache.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# For ALL active signals: set base_price = current_price
# After hours, the LTP (current_price from Angel One) IS today's close
# which is the correct "price at news time" for after-hours news
c.execute("""
    UPDATE stock_impact 
    SET base_price = current_price, estimated_change_percent = 0.0
    WHERE status = 'Active View' AND current_price > 0
""")
fixed_active = c.rowcount
print(f"Fixed {fixed_active} active signals: base_price = current_price (0%)")

# Also fix signals with base_price = 0 — set to current_price if available
c.execute("""
    UPDATE stock_impact 
    SET base_price = current_price
    WHERE base_price = 0 AND current_price > 0
""")
fixed_zero = c.rowcount
print(f"Fixed {fixed_zero} zero-base signals")

conn.commit()

# Verify
print("\n=== Active Signals After Fix ===")
c.execute("SELECT id,ticker,base_price,current_price,estimated_change_percent,status FROM stock_impact WHERE status='Active View' ORDER BY id DESC LIMIT 15")
for r in c.fetchall():
    bp = r['base_price']
    cp = r['current_price']
    pct = r['estimated_change_percent']
    match = "OK" if abs(bp - cp) < 0.01 else "MISMATCH!"
    print(f"  ID={r['id']} {r['ticker']:18s} base={bp:>10.2f} cur={cp:>10.2f} pct={pct:>6.2f}% [{match}]")

print("\n=== Resolved Signals (should keep their resolution %) ===")
c.execute("SELECT id,ticker,base_price,current_price,estimated_change_percent,status FROM stock_impact WHERE status IN ('Stop Loss Hit','Predicted Target Hit') ORDER BY id DESC LIMIT 10")
for r in c.fetchall():
    print(f"  ID={r['id']} {r['ticker']:18s} pct={r['estimated_change_percent']:>6.2f}% | {r['status']}")

conn.close()
print("\nDone!")
