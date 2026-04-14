import sqlite3
c = sqlite3.connect('news_cache.db').cursor()
c.execute("SELECT id, ticker, base_price, current_price, updated_at, status FROM stock_impact ORDER BY id DESC LIMIT 10")
for r in c.fetchall(): print(r)
