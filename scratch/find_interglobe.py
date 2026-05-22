import sqlite3

conn = sqlite3.connect('backend/news_cache.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("--- Querying for INTERGLOBE in stock_impact ---")
c.execute("SELECT * FROM stock_impact WHERE ticker LIKE '%INTERGLOBE%'")
rows = c.fetchall()
print(f"Found {len(rows)} rows:")
for r in rows:
    print(dict(r))
    # Fetch news
    c.execute("SELECT * FROM news WHERE id = ?", (r['news_id'],))
    n = c.fetchone()
    if n:
        print("  -> Associated News:", dict(n))

print("\n--- Querying for 'Dollar' in news ---")
c.execute("SELECT * FROM news WHERE headline LIKE '%Dollar%' ORDER BY created_at DESC LIMIT 5")
rows = c.fetchall()
for r in rows:
    print(dict(r))
    c.execute("SELECT * FROM stock_impact WHERE news_id = ?", (r['id'],))
    impacts = c.fetchall()
    for imp in impacts:
        print("    -> Associated Impact:", dict(imp))

conn.close()
