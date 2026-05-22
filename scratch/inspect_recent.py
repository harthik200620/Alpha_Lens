import sqlite3
import os

db_path = r"c:\Project rohan\Alpha_Lens\backend\news_cache.db"

def inspect():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        SELECT si.id, si.ticker, si.impact, si.base_price, si.current_price, si.status, si.created_at, n.headline, si.confidence_score
        FROM stock_impact si
        LEFT JOIN news n ON si.news_id = n.id
        ORDER BY si.created_at DESC
        LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()
    for r in rows:
        print(f"ID: {r[0]} | Ticker: {r[1]} | Dir: {r[2]} | Entry: {r[3]:.2f} | Current: {r[4]:.2f} | Status: {r[5]} | Conf: {r[8]} | Headline: {r[7][:50]}...")

if __name__ == "__main__":
    inspect()
