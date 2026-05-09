import sqlite3
conn=sqlite3.connect('news_cache.db')
c=conn.cursor()
c.execute("UPDATE stock_impact SET status='Active View', estimated_change_percent=0.0 WHERE status IN ('Stop Loss Hit', 'Predicted Target Hit')")
conn.commit()
conn.close()
