import sqlite3
import pandas as pd
conn = sqlite3.connect('news_cache.db')
print(pd.read_sql_query("SELECT id, news_id, ticker, base_price, current_price, status FROM stock_impact", conn))
