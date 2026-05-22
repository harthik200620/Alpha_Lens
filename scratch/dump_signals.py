import sqlite3

conn = sqlite3.connect('backend/news_cache.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("--- TABLES ---")
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
for row in c.fetchall():
    print(dict(row))

print("\n--- SCHEMA OF stock_impact ---")
c.execute("PRAGMA table_info(stock_impact)")
for row in c.fetchall():
    print(dict(row))

print("\n--- ALL SIGNALS IN stock_impact ---")
c.execute("""
    SELECT si.*, n.headline 
    FROM stock_impact si
    LEFT JOIN news n ON si.news_id = n.id
    ORDER BY si.id DESC
""")
for row in c.fetchall():
    print(dict(row))

conn.close()
