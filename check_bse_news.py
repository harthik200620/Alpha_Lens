import sqlite3
c = sqlite3.connect('news_cache.db').cursor()
c.execute("SELECT id, headline FROM news WHERE headline LIKE '%BSE loses%'")
for r in c.fetchall(): print(r)
