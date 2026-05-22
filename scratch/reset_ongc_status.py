import sqlite3

db_path = r"backend/news_cache.db"

def main():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("UPDATE stock_impact SET status = 'Active View' WHERE id IN (16, 17, 19, 21)")
    conn.commit()
    print("Updated rows:", c.rowcount)
    
    # Let's inspect the results
    c.execute("SELECT id, ticker, status, estimated_change_percent, reason FROM stock_impact WHERE id IN (16, 17, 19, 21)")
    for r in c.fetchall():
        print(r)
    conn.close()

if __name__ == "__main__":
    main()
