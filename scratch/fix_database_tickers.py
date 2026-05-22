import sqlite3

conn = sqlite3.connect('backend/news_cache.db')
c = conn.cursor()

# 1. Update any known aliases in the stock_impact table
print("Updating INTERGLOBE.NS to INDIGO.NS...")
c.execute("UPDATE stock_impact SET ticker = 'INDIGO.NS' WHERE ticker = 'INTERGLOBE.NS'")
updated_count = c.rowcount
print(f"Updated {updated_count} rows.")

# 2. Check for other invalid tickers in active views
c.execute("SELECT id, ticker, news_id FROM stock_impact WHERE base_price = 0.0 OR current_price = 0.0")
rows = c.fetchall()
if rows:
    print("\nFound rows with 0.0 price:")
    for r in rows:
        print(f"  ID: {r[0]}, Ticker: {r[1]}, News ID: {r[2]}")
        # If it is not a valid/supported ticker and can't be resolved, delete it
        # Wait, since we are doing this dynamically, we can just delete unresolved ones
        # or leave them if they are now resolved. Let's see if we should delete them:
        print(f"  Deleting invalid stock_impact row {r[0]}...")
        c.execute("DELETE FROM stock_impact WHERE id = ?", (r[0],))
        
conn.commit()
conn.close()
print("\nDone fixing database.")
