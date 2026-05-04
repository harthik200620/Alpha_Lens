import sqlite3, os
db = os.path.join('..', 'news_cache.db')
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=== ALL RESOLVED SIGNALS ===")
c.execute("""SELECT id,ticker,impact,status,estimated_change_percent,base_price,current_price
             FROM stock_impact WHERE status IN ('Stop Loss Hit','Predicted Target Hit','Reacted Against Prediction')""")
for r in c.fetchall():
    print(f"  ID={r['id']} {r['ticker']:20s} {r['impact']:15s} {r['status']:22s} ecp={r['estimated_change_percent']} base={r['base_price']}")

print("\n=== BASE_PRICE=0 SIGNALS (never initialized) ===")
c.execute("SELECT COUNT(*) FROM stock_impact WHERE base_price=0 OR base_price IS NULL")
print(f"  Count: {c.fetchone()[0]}")

c.execute("SELECT id,ticker,impact,status,created_at FROM stock_impact WHERE (base_price=0 OR base_price IS NULL) LIMIT 10")
for r in c.fetchall():
    print(f"  ID={r['id']} {r['ticker']:20s} {r['impact']:15s} {r['status']} @ {r['created_at']}")

conn.close()
