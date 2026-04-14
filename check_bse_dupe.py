import sqlite3
c = sqlite3.connect('news_cache.db').cursor()
c.execute("SELECT id, headline, news_time, created_at FROM news WHERE headline LIKE '%Stock Market Holiday%' LIMIT 10")
for row in c.fetchall():
    print(row)
