import yfinance as yf
from datetime import datetime

ticker = 'RELIANCE.NS'
start = '2026-05-14'
end = '2026-05-17'
base = 1335.90

t = yf.Ticker(ticker)
hist = t.history(start=start, end=end, interval='15m')
print('rows', len(hist))
if hist.empty:
    print('no data')
else:
    print(hist[['High','Low','Close']].tail())
    print('max high', hist['High'].max())
    print('min low', hist['Low'].min())
    print('last close', hist['Close'].iloc[-1])
    for idx, row in hist.iterrows():
        high_pct = (row['High'] - base) / base * 100
        low_pct = (row['Low'] - base) / base * 100
        if high_pct >= 1.0:
            print('HIGH HIT', idx, high_pct)
        if low_pct <= -2.0:
            print('LOW HIT target', idx, low_pct)
