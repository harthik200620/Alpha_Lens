import sqlite3
import pandas as pd
conn = sqlite3.connect('news_cache.db')
query = """
SELECT s.id, s.ticker, s.base_price, s.current_price, n.news_time, n.headline 
FROM stock_impact s 
JOIN news n ON s.news_id = n.id 
WHERE s.id = 3087
"""
print(pd.read_sql_query(query, conn))
