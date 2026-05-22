import sqlite3
import json

conn = sqlite3.connect('backend/news_cache.db')
c = conn.cursor()
c.execute("SELECT id, ticker, base_price, current_price, status, created_at FROM stock_impact WHERE ticker LIKE '%TATAMOTORS%'")
rows = c.fetchall()
print("TATAMOTORS rows:")
for r in rows:
    print(r)

print("\nAll active/recent rows where base_price or current_price is 0:")
c.execute("SELECT id, ticker, base_price, current_price, status, created_at FROM stock_impact WHERE (base_price = 0 OR current_price = 0) AND status = 'Active View'")
rows_zero = c.fetchall()
for r in rows_zero:
    print(r)

conn.close()
