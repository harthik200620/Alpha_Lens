import sqlite3
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

IST = timezone(timedelta(hours=5, minutes=30))
conn = sqlite3.connect('news_cache.db')
c = conn.cursor()

c.execute("""
    SELECT s.id, s.ticker, s.base_price, s.current_price, n.news_time
    FROM stock_impact s
    JOIN news n ON s.news_id = n.id
    WHERE s.id = 3087 OR s.ticker = 'BSE.NS' OR s.ticker = 'ICICIBANK.NS'
""")
signals = c.fetchall()

fixes = []
for sid, ticker, bp, cp, nt in signals:
    try:
        pub_dt = parsedate_to_datetime(nt).astimezone(IST)
        # Check if during market hours
        h, m = pub_dt.hour, pub_dt.minute
        if not (9 * 60 + 15 <= h * 60 + m <= 15 * 60 + 30):
            continue
            
        start_date = pub_dt.strftime('%Y-%m-%d')
        end_date = (pub_dt + timedelta(days=1)).strftime('%Y-%m-%d')
        
        df = yf.download(ticker, start=start_date, end=end_date, interval='1m', progress=False)
        if df.empty:
            continue
            
        df.index = pd.to_datetime(df.index).tz_convert(IST)
        time_diffs = abs(df.index - pub_dt)
        closest_idx = time_diffs.argmin()
        bar = df.iloc[closest_idx]
        
        close_val = bar['Close']
        if hasattr(close_val, 'iloc'):
            close_val = close_val.iloc[0]
        intraday_price = round(float(close_val), 2)
        
        # We always want to fix it if the current base_price is missing the actual movement
        if intraday_price > 0 and abs(intraday_price - bp) > 0.01:
            fixes.append((intraday_price, sid))
            print(f"[{ticker} row {sid}] fixed! Base: {bp} -> {intraday_price}")
            
    except Exception as e:
        print(f"Failed {ticker}: {e}")

if fixes:
    c.executemany("UPDATE stock_impact SET base_price=? WHERE id=?", fixes)
    conn.commit()
    print(f"Successfully fixed {len(fixes)} signals.")
else:
    print("No signals needed fixing.")
    
conn.close()
