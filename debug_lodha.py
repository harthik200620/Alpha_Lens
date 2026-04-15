import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
ticker = "LODHA.NS"

# News was at 02:05 PM on April 15 2026
pub_dt = datetime(2026, 4, 15, 14, 5, 0, tzinfo=IST)
print(f"News pub time: {pub_dt}")
print()

print("=== 1-min download (period=1d, interval=1m) ===")
hist1d = yf.download(ticker, period='1d', interval='1m', progress=False, auto_adjust=True)
print(f"Shape: {hist1d.shape}")
print(f"Columns: {hist1d.columns.tolist()}")
print(f"Index type: {type(hist1d.index)}")
if not hist1d.empty:
    hist1d.index = pd.to_datetime(hist1d.index).tz_convert(IST)
    print(f"First bar: {hist1d.index[0]}")
    print(f"Last bar:  {hist1d.index[-1]}")
    # Try to get close column
    close_col = hist1d['Close']
    print(f"Close column type: {type(close_col)}")
    print(f"Close tail:\n{close_col.tail(3)}")
    
print()
print("=== 7d 1-min download ===")
hist7d = yf.download(ticker, period='7d', interval='1m', progress=False, auto_adjust=True)
print(f"Shape: {hist7d.shape}")
if not hist7d.empty:
    hist7d.index = pd.to_datetime(hist7d.index).tz_convert(IST)
    print(f"Date range: {hist7d.index[0]} --> {hist7d.index[-1]}")
    window_start = pub_dt - timedelta(minutes=30)
    window = hist7d[(hist7d.index >= window_start) & (hist7d.index <= pub_dt)]
    print(f"Bars in 30-min window: {len(window)}")
    if not window.empty:
        last_row = window.iloc[-1]
        print(f"Last row:\n{last_row}")
        close_val = last_row['Close']
        print(f"Close value: {close_val}, type: {type(close_val)}")
        if hasattr(close_val, 'iloc'):
            close_val = close_val.iloc[0]
            print(f"After iloc[0]: {close_val}")
        print(f">>> Base price would be: {round(float(close_val), 2)}")
    else:
        print("NO bars in window!")

print()
print("=== fast_info check ===")
t = yf.Ticker(ticker)
fi = t.fast_info
print(f"last_price: {fi.last_price}")
print(f"previous_close: {fi.previous_close}")
