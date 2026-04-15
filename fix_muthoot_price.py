import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'news_cache.db')

CORRECT_BASE_PRICE = 3596.60
WRONG_BASE_PRICE_LOW = 3560.0
WRONG_BASE_PRICE_HIGH = 3565.0
TICKER = 'MUTHOOTFIN.NS'

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Find any MUTHOOTFIN.NS rows with the stale base_price around 3562
c.execute("""
    SELECT id, ticker, base_price, current_price, status, reason  
    FROM stock_impact 
    WHERE ticker = ? AND base_price BETWEEN ? AND ?
""", (TICKER, WRONG_BASE_PRICE_LOW, WRONG_BASE_PRICE_HIGH))

rows = c.fetchall()
print(f"Found {len(rows)} rows to fix:")
for row in rows:
    print(f"  id={row[0]} ticker={row[1]} base_price={row[2]} current={row[3]} status={row[4]}")

if rows:
    c.execute("""
        UPDATE stock_impact 
        SET base_price = ?
        WHERE ticker = ? AND base_price BETWEEN ? AND ?
    """, (CORRECT_BASE_PRICE, TICKER, WRONG_BASE_PRICE_LOW, WRONG_BASE_PRICE_HIGH))
    conn.commit()
    print(f"\nSUCCESS: Updated {c.rowcount} rows. base_price corrected to {CORRECT_BASE_PRICE}")
else:
    print("No rows found to fix.")

conn.close()
