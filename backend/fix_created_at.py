import sqlite3
conn=sqlite3.connect('news_cache.db')
c=conn.cursor()
c.execute("UPDATE stock_impact SET created_at='2026-05-05 15:55:00' WHERE created_at=''")
conn.commit()
conn.close()
