import sqlite3

conn = sqlite3.connect('news_cache.db')
c = conn.cursor()

c.execute("SELECT id, headline, news_time FROM news WHERE headline LIKE '%ICICI Prudential AMC Q4 results, final%' ORDER BY id DESC LIMIT 10")
for row in c.fetchall():
    print(f"ID={row[0]} | {row[1]} | {row[2]}")

c.execute("SELECT COUNT(*) FROM news WHERE headline LIKE '%ICICI Prudential AMC Q4 results, final%'")
print("COUNT:", c.fetchone()[0])
conn.close()
