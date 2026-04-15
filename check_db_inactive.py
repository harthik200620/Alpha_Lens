import sqlite3
import pandas as pd

conn = sqlite3.connect('news_cache.db')
query = """
SELECT id, ticker, status, base_price, current_price, impact 
FROM stock_impact 
WHERE status != 'Active View' 
ORDER BY id DESC LIMIT 10
"""
df = pd.read_sql_query(query, conn)
print(df)
conn.close()
