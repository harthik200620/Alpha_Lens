import sqlite3
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

conn = sqlite3.connect('news_cache.db')
conn.execute("PRAGMA journal_mode=WAL")
c = conn.cursor()

print("--- CLEANING STOCK IMPACT ---")
c.execute("SELECT COUNT(*) FROM stock_impact")
print("Before stock_impact count:", c.fetchone()[0])
c.execute("""
    DELETE FROM stock_impact WHERE id NOT IN (
        SELECT MAX(s.id)
        FROM stock_impact s
        JOIN news n ON s.news_id = n.id
        GROUP BY n.headline, s.ticker
    )
""")
print("Deleted duplicate stock impacts:", c.rowcount)
c.execute("SELECT COUNT(*) FROM stock_impact")
print("After stock_impact count:", c.fetchone()[0])

print("--- CLEANING NEWS ---")
c.execute("SELECT COUNT(*) FROM news")
print("Before news count:", c.fetchone()[0])
c.execute("""
    DELETE FROM news WHERE id NOT IN (
        SELECT MIN(id) FROM news GROUP BY headline
    )
""")
print("Deleted duplicate news items:", c.rowcount)
c.execute("SELECT COUNT(*) FROM news")
print("After news count:", c.fetchone()[0])

conn.commit()
conn.close()
