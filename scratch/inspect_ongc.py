import sqlite3
import os

db_path = r"c:\Project rohan\Alpha_Lens\backend\news_cache.db"

def inspect():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id, news_id, ticker, impact, base_price, status, created_at FROM stock_impact WHERE id IN (19, 21)")
    rows = c.fetchall()
    conn.close()
    for r in rows:
        print(r)

if __name__ == "__main__":
    inspect()
