import sqlite3

conn = sqlite3.connect('news_cache.db')
c = conn.cursor()

c.execute("SELECT id, headline, aam_janta_translation FROM news ORDER BY id DESC LIMIT 10")
for row in c.fetchall():
    print(row)
