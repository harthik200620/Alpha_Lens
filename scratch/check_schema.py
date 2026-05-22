import sqlite3, os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend', 'news_cache.db')
conn = sqlite3.connect(db_path)
c = conn.cursor()

print("=== NEWS table columns ===")
c.execute("PRAGMA table_info(news)")
for row in c.fetchall():
    print(row)

print("\n=== STOCK_IMPACT table columns ===")
c.execute("PRAGMA table_info(stock_impact)")
for row in c.fetchall():
    print(row)

print("\n=== ROW COUNTS ===")
for t in ['news', 'stock_impact', 'historical_patterns', 'stock_universe']:
    c.execute(f"SELECT count(*) FROM {t}")
    print(f"  {t}: {c.fetchone()[0]}")

print("\n=== SAMPLE NEWS ROW ===")
c.execute("SELECT * FROM news LIMIT 1")
row = c.fetchone()
print(row)

conn.close()
