import sys
import os
sys.path.append(os.path.abspath('backend'))
import app
import yfinance_twelvedata_shim as yf

ticker = "TATAMOTORS.NS"

print("1. Testing yf._get_cached_quote:")
try:
    ltp, prev, high, low = yf._get_cached_quote(ticker)
    print(f"LTP: {ltp}, Prev: {prev}")
except Exception as e:
    print(f"Error: {e}")

print("\n2. Testing app._get_yahoo_official_close:")
try:
    close = app._get_yahoo_official_close(ticker)
    print(f"Official Close: {close}")
except Exception as e:
    print(f"Error: {e}")

print("\n3. Testing get_base_price_at_time:")
try:
    # Get base price for today
    import datetime
    from zoneinfo import ZoneInfo
    now = datetime.datetime.now(ZoneInfo("Asia/Kolkata"))
    bp = app.get_base_price_at_time(ticker, now)
    print(f"Base price: {bp}")
except Exception as e:
    print(f"Error: {e}")
