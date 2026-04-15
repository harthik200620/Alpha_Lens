import sqlite3
import yfinance as yf
from datetime import datetime, timezone, timedelta
import pandas as pd

def is_market_open():
    return True

def _extract_scalar(val):
    if hasattr(val, 'item'):
        return val.item()
    if isinstance(val, (pd.Series, pd.DataFrame)):
        return val.iloc[-1] if not val.empty else 0.0
    return float(val)

def get_robust_price(ticker, market_open=True):
    try:
        t = yf.Ticker(ticker)
        fi = t.fast_info
        if market_open:
            if hasattr(fi, 'last_price') and fi.last_price is not None:
                p = _extract_scalar(fi.last_price)
                if p > 0: return float(p)
        hist = t.history(period="1d", interval="1m")
        if not hist.empty:
            p = _extract_scalar(hist['Close'].iloc[-1])
            if p > 0: return float(p)
    except Exception as e:
        pass
    try:
        hist = yf.download(ticker, period="1d", interval="1m", progress=False)
        if not hist.empty:
            p = _extract_scalar(hist['Close'].iloc[-1])
            if p > 0: return float(p)
    except Exception:
        pass
    return 0.0

conn = sqlite3.connect('news_cache.db')
c = conn.cursor()
seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
c.execute("SELECT id, ticker, current_price, status FROM stock_impact WHERE status != 'Expired' AND created_at > ?", (seven_days_ago,))
active_stocks = c.fetchall()

print(f"Found {len(active_stocks)} stocks to update.")
updates = []
for row in active_stocks:
    stock_id, ticker, cp, status = row
    live_price = get_robust_price(ticker)
    if live_price > 0:
        updates.append((live_price, stock_id))
        if ticker == 'BSE.NS':
            print(f"BSE.NS: DB Value {cp} -> Live Value {live_price}")

if updates:
    c.executemany("UPDATE stock_impact SET current_price = ? WHERE id = ?", updates)
    conn.commit()
    print(f"Updated {len(updates)} rows in DB.")
conn.close()
