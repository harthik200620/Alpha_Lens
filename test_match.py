import sqlite3
c=sqlite3.connect('news_cache.db').cursor()
headline = "Stock Market Holiday: Are NSE, BSE closed on April 14? Check MCX and Bank timings here - The Financial Express"
c.execute("SELECT id FROM news WHERE headline=?", (headline,))
rows = c.fetchall()
print(f"COUNT using ?: {len(rows)}")
if len(rows) > 0:
    print(f"IDs: {rows}")
