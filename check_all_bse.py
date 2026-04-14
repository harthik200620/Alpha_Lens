import sqlite3

conn = sqlite3.connect('news_cache.db')
c = conn.cursor()

c.execute("SELECT s.id, s.base_price, s.current_price, n.headline FROM stock_impact s JOIN news n ON s.news_id = n.id WHERE s.ticker='BSE.NS'")
for r in c.fetchall(): print(r)
