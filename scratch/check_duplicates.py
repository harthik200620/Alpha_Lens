import sqlite3
import os
from datetime import datetime

db_path = r"c:\Project rohan\Alpha_Lens\backend\news_cache.db"

def check():
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Let's query all active views (status = 'Active View' or status != 'Expired'/'Completed')
    c.execute("""
        SELECT id, ticker, impact, base_price, created_at, status, reason
        FROM stock_impact
        WHERE status = 'Active View'
        ORDER BY ticker, created_at
    """)
    rows = c.fetchall()
    conn.close()
    
    print(f"Total Active View rows: {len(rows)}")
    
    # Group by ticker and impact
    by_group = {}
    for r in rows:
        db_id, ticker, impact, bp, created_at, status, reason = r
        key = (ticker, impact)
        if key not in by_group:
            by_group[key] = []
        by_group[key].append(r)
        
    duplicates = 0
    for key, items in by_group.items():
        if len(items) > 1:
            print(f"\nGroup {key}: {len(items)} active signals")
            for item in items:
                print(f"  ID: {item[0]} | Price: {item[3]} | Created: {item[4]} | Reason: {item[6][:80]}...")
            duplicates += 1
            
    print(f"\nFound {duplicates} duplicate groups.")

if __name__ == "__main__":
    check()
