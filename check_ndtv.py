import sqlite3
c = sqlite3.connect('news_cache.db').cursor()
c.execute("SELECT id, ticker, base_price, current_price, status, created_at FROM stock_impact WHERE news_id=117")
print("ndtv news:")
for row in c.fetchall():
    print(row)

c.execute("""SELECT s.id, s.ticker, s.base_price, s.current_price, n.headline FROM stock_impact s 
             JOIN news n ON n.id=s.news_id WHERE n.headline LIKE '%Are NSE, BSE Closed On April 14%'""")
print("\nall ndtv impacts:")
for row in c.fetchall():
    print(row)
