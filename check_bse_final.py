import sqlite3
c = sqlite3.connect('news_cache.db').cursor()
c.execute("SELECT s.id, s.ticker, s.base_price, s.current_price, n.news_time, n.headline FROM stock_impact s JOIN news n ON s.news_id = n.id WHERE s.ticker='BSE.NS' ORDER BY s.id DESC")
for r in c.fetchall(): print(r)
