import sqlite3

db_path = r"backend/news_cache.db"

def main():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id, news_id, ticker, impact, base_price, current_price, status, created_at, reason FROM stock_impact WHERE ticker LIKE '%IOC%'")
    for r in c.fetchall():
        print(r)
    conn.close()

if __name__ == "__main__":
    main()
