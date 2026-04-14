import yfinance as yf
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime as _parse_rss_time

ticker = 'ICICIBANK.NS'
news_time_str = "Mon, 13 Apr 2026 06:22:50 GMT"

try:
    _ist = timezone(timedelta(hours=5, minutes=30))
    _pub_dt = _parse_rss_time(news_time_str).astimezone(_ist)
    print("Pub DT:", _pub_dt)
    
    _start = _pub_dt.strftime('%Y-%m-%d')
    _end_dt = _pub_dt + timedelta(days=1)
    _end = _end_dt.strftime('%Y-%m-%d')
    
    print(f"Downloading {ticker} from {_start} to {_end}")
    _hist = yf.download(ticker, start=_start, end=_end, interval='1m', progress=False)
    
    if not _hist.empty:
        import pandas as pd
        _hist.index = pd.to_datetime(_hist.index).tz_convert(_ist)
        _tdiffs = abs(_hist.index - _pub_dt)
        argmin = _tdiffs.argmin()
        _closest = _hist.iloc[argmin]
        _cv = _closest['Close']
        if hasattr(_cv, 'iloc'):
            _cv = _cv.iloc[0]
        base_price = round(float(_cv), 2)
        print("Success! Base Price:", base_price)
    else:
        print("Empty history")
except Exception as e:
    print("EXCEPTION:", repr(e))
