import sqlite3
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# Hardcode holiday dates exactly as in app.py
NSE_HOLIDAYS_2026 = {
    (1, 26), (2, 19), (3, 25), (4, 2), (4, 10), (4, 14),
    (5, 1), (6, 2), (8, 15), (8, 27), (10, 2), (10, 21),
    (10, 22), (11, 5), (11, 6), (12, 25)
}

IST = timezone(timedelta(hours=5, minutes=30))
conn = sqlite3.connect('news_cache.db')
c = conn.cursor()

c.execute("""
    SELECT s.id, s.ticker, s.base_price, s.current_price, n.news_time
    FROM stock_impact s
    JOIN news n ON s.news_id = n.id
""")
signals = c.fetchall()

fixes = []
for sid, ticker, bp, cp, nt in signals:
    try:
        pub_dt = parsedate_to_datetime(nt).astimezone(IST)
        base_price = bp
        
        _is_trading = True
        if pub_dt.weekday() >= 5 or (pub_dt.month, pub_dt.day) in NSE_HOLIDAYS_2026:
            _is_trading = False
        else:
            _t = pub_dt.hour * 60 + pub_dt.minute
            if not ((9 * 60 + 15) <= _t <= (15 * 60 + 30)):
                _is_trading = False
        
        _new_bp = 0.0
        
        if _is_trading:
            # 1m fetch logic
            _hist = yf.download(ticker, period='7d', interval='1m', progress=False)
            if not _hist.empty:
                _hist.index = pd.to_datetime(_hist.index).tz_convert(IST)
                _past_ticks = _hist[_hist.index <= pub_dt]
                if not _past_ticks.empty:
                    _closest_bar = _past_ticks.iloc[-1]
                    _cv = _closest_bar['Close']
                    if hasattr(_cv, 'iloc'): _cv = _cv.iloc[0]
                    _new_bp = round(float(_cv), 2)
                    
        if _new_bp == 0.0:
            # 1d fetch logic fallback
            _hist_d = yf.download(ticker, period='1mo', interval='1d', progress=False)
            if not _hist_d.empty:
                _hist_d.index = pd.to_datetime(_hist_d.index).tz_localize(None)
                _hist_d.index = _hist_d.index + timedelta(hours=15, minutes=30)
                _hist_d.index = _hist_d.index.tz_localize(IST)
                
                _past_days = _hist_d[_hist_d.index <= pub_dt]
                if not _past_days.empty:
                    _cv = _past_days.iloc[-1]['Close']
                    if hasattr(_cv, 'iloc'): _cv = _cv.iloc[0]
                    _new_bp = round(float(_cv), 2)
                        
        if _new_bp > 0 and abs(_new_bp - bp) > 0.01:
            fixes.append((_new_bp, sid))
            print(f"[{ticker}] fixed! Base: {bp} -> {_new_bp}")
            
    except Exception as e:
        print(f"Failed {ticker}: {e}")

if fixes:
    c.executemany("UPDATE stock_impact SET base_price=? WHERE id=?", fixes)
    conn.commit()
    print(f"Successfully overhauled {len(fixes)} signals.")
else:
    print("Database is already perfectly snapped to the minute!")
    
conn.close()
