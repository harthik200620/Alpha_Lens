"""Diagnostic: dump all stock_impact rows with their news headlines."""
import sqlite3, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'news_cache.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT si.id, si.ticker, si.impact, si.base_price, si.current_price,
           si.estimated_change_percent, si.status, si.created_at,
           n.headline, n.news_time
    FROM stock_impact si
    JOIN news n ON n.id = si.news_id
    ORDER BY si.id DESC
    LIMIT 20
""").fetchall()

for r in rows:
    print(f"ID={r['id']} | {r['ticker']} | {r['impact']} | "
          f"base={r['base_price']} | cur={r['current_price']} | "
          f"pct={r['estimated_change_percent']} | {r['status']} | "
          f"created={r['created_at']} | news_time={r['news_time']} | "
          f"{str(r['headline'])[:70]}")

conn.close()
