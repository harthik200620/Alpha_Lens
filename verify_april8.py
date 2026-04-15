import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# News pub time: Wed, 08 Apr 2026 08:36:01 GMT = 14:06 IST April 8
pub_dt = datetime(2026, 4, 8, 14, 6, 0, tzinfo=IST)
print(f"Actual news time: {pub_dt}")
print()

print("=== LODHA.NS daily closes around April 8 ===")
hist = yf.download('LODHA.NS', start='2026-04-06', end='2026-04-16', interval='1d', progress=False, auto_adjust=True)
print(hist[['Close']].to_string())

print()
print("=== DLF.NS daily closes around April 8 ===")
hist2 = yf.download('DLF.NS', start='2026-04-06', end='2026-04-16', interval='1d', progress=False, auto_adjust=True)
print(hist2[['Close']].to_string())
