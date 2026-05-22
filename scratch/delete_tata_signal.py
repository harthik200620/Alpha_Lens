import sqlite3

conn = sqlite3.connect('backend/news_cache.db')
c = conn.cursor()
c.execute("DELETE FROM stock_impact WHERE id = 36")
print("Deleted rows count:", c.rowcount)
conn.commit()

# Also let's check if there are any other rows for TATAMOTORS
c.execute("SELECT id, ticker, base_price, current_price, status FROM stock_impact WHERE ticker LIKE '%TATAMOTORS%'")
rows = c.fetchall()
print("Remaining TATAMOTORS rows:", rows)

conn.close()
