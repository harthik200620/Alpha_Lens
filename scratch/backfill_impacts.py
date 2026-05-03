import sqlite3
import os
import sys
import json
from datetime import datetime

# Add backend to path to import app.py functions
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
import app  # type: ignore

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'news_cache.db')

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Get all news that don't have impacts yet
c.execute("SELECT id, headline, news_time FROM news")
articles = c.fetchall()

inserted = 0
print(f"Checking {len(articles)} articles for stock impacts...")

for row in articles:
    news_id, headline, news_time = row
    
    # Use the fast, rule-based keyword matcher (no API calls)
    candidates = app._fallback_get_candidate_stocks(headline)
    if not candidates:
        continue
        
    for ticker, impact in candidates:
        # Check if already exists
        c.execute("SELECT id FROM stock_impact WHERE news_id=? AND ticker=?", (news_id, ticker))
        if c.fetchone():
            continue
            
        base_price = 0.0
        try:
            # We don't necessarily need the exact historical minute price for this quick fix, 
            # we can just use the robust fetcher
            cp = app.get_robust_price(ticker, market_open=False)
            if cp and float(cp) > 0:
                base_price = round(float(cp), 2)
        except Exception as e:
            print(f"Price fetch error for {ticker}: {e}")
            
        c.execute('''INSERT INTO stock_impact 
            (news_id, ticker, impact, estimated_change_percent, view, reason, base_price, current_price, confidence_score, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
            (news_id, ticker, impact, 2.5, 'High Conviction', 'Rule-based mapping found direct keyword match.', base_price, base_price, 85, 'Active View')
        )
        inserted += 1

conn.commit()
conn.close()
print(f"Successfully inserted {inserted} stock impacts instantly!")
