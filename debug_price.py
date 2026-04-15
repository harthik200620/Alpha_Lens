import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
ticker = "MUTHOOTFIN.NS"

# Simulate pub time: April 15 2026 at 15:27 IST
pub_dt = datetime(2026, 4, 15, 15, 27, 0, tzinfo=IST)
print(f"News pub time: {pub_dt}")
print()

# --- Step 1: What does the 1-min download return? ---
print("=== Step 1: 1-min bars (period=7d) ===")
hist = yf.download(ticker, period='7d', interval='1m', progress=False, auto_adjust=True)
if hist.empty:
    print("1-min data EMPTY")
else:
    hist.index = pd.to_datetime(hist.index).tz_convert(IST)
    print(f"1-min data spans: {hist.index[0]} --> {hist.index[-1]}")
    print(f"Total bars: {len(hist)}")
    
    # 30-min window search
    window_start = pub_dt - timedelta(minutes=30)
    window_ticks = hist[(hist.index >= window_start) & (hist.index <= pub_dt)]
    print(f"\n30-min window ({window_start} to {pub_dt}):")
    print(f"Bars found in window: {len(window_ticks)}")
    if not window_ticks.empty:
        print(window_ticks[['Close']].tail(5).to_string())
        cv = window_ticks.iloc[-1]['Close']
        if hasattr(cv, 'iloc'): cv = cv.iloc[0]
        print(f"\n>>> Would use base_price: {round(float(cv), 2)}")
    else:
        # No bars in window
        all_past = hist[hist.index <= pub_dt]
        print(f"All bars before pub_dt: {len(all_past)}")
        if not all_past.empty:
            print(all_past[['Close']].tail(3).to_string())
            cv = all_past.iloc[-1]['Close']
            if hasattr(cv, 'iloc'): cv = cv.iloc[0]
            print(f"\n>>> Would use base_price (broad fallback): {round(float(cv), 2)}")
        else:
            print("NO past bars at all — would fall to daily")

print()

# --- Step 2: What does the daily download return? ---
print("=== Step 2: Daily bars (period=1mo) ===")
hist_d = yf.download(ticker, period='1mo', interval='1d', progress=False, auto_adjust=True)
if not hist_d.empty:
    hist_d.index = pd.to_datetime(hist_d.index).tz_localize(None)
    hist_d.index = hist_d.index + timedelta(hours=15, minutes=30)
    hist_d.index = hist_d.index.tz_localize(IST)
    past_days = hist_d[hist_d.index <= pub_dt]
    print(past_days[['Close']].tail(5).to_string())
    cv = past_days.iloc[-1]['Close']
    if hasattr(cv, 'iloc'): cv = cv.iloc[0]
    print(f"\n>>> Daily fallback would give base_price: {round(float(cv), 2)}")

print()
print("=== Step 3: Live price NOW ===")
t = yf.Ticker(ticker)
fast_info = t.fast_info
print(f"Live last price: {fast_info.last_price}")
