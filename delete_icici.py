import sqlite3
conn=sqlite3.connect('news_cache.db')
c=conn.cursor()
c.execute("DELETE FROM stock_impact WHERE ticker='ICICIBANK.NS'")
c.execute("DELETE FROM news WHERE headline LIKE '%ICICI Prudential AMC%'")
conn.commit()
print("Deleted")
conn.close()
