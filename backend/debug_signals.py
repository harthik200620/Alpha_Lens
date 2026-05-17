"""Quick diagnostic: show the exact values in stock_impact table."""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'news_cache.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("\n=== STOCK IMPACT TABLE (ALL ROWS) ===")
c.execute("""
    SELECT si.id, si.ticker, si.impact, si.base_price, si.current_price,
           si.confidence_score, si.status, si.created_at,
           n.headline
    FROM stock_impact si
    JOIN news n ON si.news_id = n.id
    ORDER BY si.id DESC
""")
rows = c.fetchall()
print(f"Total rows: {len(rows)}\n")
for r in rows:
    bp = r['base_price']
    cp = r['current_price']
    pct = round((cp - bp)/bp*100, 2) if bp and bp > 0 else 0
    print(f"ID={r['id']} | {r['ticker']} | {r['impact']}")
    print(f"  base_price={bp}  current_price={cp}  => pct={pct}%")
    print(f"  score={r['confidence_score']} | status={r['status']}")
    print(f"  created_at={r['created_at']}")
    print(f"  headline: {r['headline'][:70]}")
    print()

print("\n=== NEWS TABLE SUMMARY ===")
c.execute("SELECT COUNT(*) FROM news")
print(f"Total news: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM news WHERE id IN (SELECT DISTINCT news_id FROM stock_impact)")
print(f"News with stock signals: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM news WHERE id NOT IN (SELECT DISTINCT news_id FROM stock_impact)")
print(f"News WITHOUT stock signals: {c.fetchone()[0]}")

conn.close()
