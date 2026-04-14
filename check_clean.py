import sqlite3

conn = sqlite3.connect('news_cache.db')
c = conn.cursor()

c.execute("""
    SELECT s.ticker, s.base_price, s.current_price, n.headline 
    FROM stock_impact s 
    JOIN news n ON n.id=s.news_id 
    ORDER BY s.id DESC LIMIT 10
""")
for row in c.fetchall():
    print(f"{row[0]} | Base: {row[1]} | Current: {row[2]} | {row[3][:50]}")

conn.close()
