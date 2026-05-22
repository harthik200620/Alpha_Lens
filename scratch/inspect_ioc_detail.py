import sqlite3

db_path = r"backend/news_cache.db"

def main():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        SELECT si.id, si.ticker, si.impact, si.base_price, si.current_price, si.status, si.created_at, si.reason, n.headline
        FROM stock_impact si
        LEFT JOIN news n ON si.news_id = n.id
        WHERE si.ticker LIKE '%IOC%'
    """)
    for r in c.fetchall():
        print(f"ID: {r[0]}")
        print(f"  Ticker: {r[1]} | Impact: {r[2]} | Status: {r[5]}")
        print(f"  Prices: Base={r[3]}, Current={r[4]}")
        print(f"  Created: {r[6]}")
        print(f"  Headline: {r[8]}")
        print(f"  Reason: {r[7]}")
        print("-" * 50)
    conn.close()

if __name__ == "__main__":
    main()
