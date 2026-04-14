import sqlite3

conn = sqlite3.connect('news_cache.db')
c = conn.cursor()

c.execute("""
    SELECT s.id, s.base_price, s.current_price, s.created_at, n.headline 
    FROM stock_impact s 
    JOIN news n ON n.id=s.news_id 
    WHERE n.headline LIKE '%HDFC, ICICI Bank Q4 results%'
""")
for row in c.fetchall():
    print(row)
