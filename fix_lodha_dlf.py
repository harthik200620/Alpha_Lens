"""
Targeted fix: repair LODHA.NS and DLF.NS records with known wrong base prices
"""
import sqlite3
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'news_cache.db')
IST = timezone(timedelta(hours=5, minutes=30))

def extract_scalar(val):
    if val is None:
        return None
    if hasattr(val, 'iloc'):
        val = val.iloc[0]
    try:
        return float(val)
    except:
        return None

def get_price_at_time(ticker, pub_dt):
    try:
        hist = yf.download(ticker, period='7d', interval='1m', progress=False, auto_adjust=True)
        if not hist.empty:
            hist.index = pd.to_datetime(hist.index).tz_convert(IST)
            close_series = hist['Close'].iloc[:, 0] if isinstance(hist.columns, pd.MultiIndex) else hist['Close']
            window = close_series[(close_series.index >= pub_dt - timedelta(minutes=45)) & (close_series.index <= pub_dt)]
            if not window.empty:
                return round(float(extract_scalar(window.iloc[-1])), 2)
            past = close_series[close_series.index <= pub_dt]
            if not past.empty:
                return round(float(extract_scalar(past.iloc[-1])), 2)
    except Exception as e:
        print(f"Error: {e}")
    try:
        fi = yf.Ticker(ticker).fast_info
        today = datetime.now(IST).date()
        if pub_dt.date() == today:
            lp = extract_scalar(fi.last_price)
            if lp and lp > 0: return round(lp, 2)
        pc = extract_scalar(fi.previous_close)
        if pc and pc > 0: return round(pc, 2)
    except: pass
    return None

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Look at ALL records - show what's in there for LODHA and DLF
print("=== All LODHA.NS records ===")
c.execute("SELECT si.id, si.base_price, si.status, n.news_time FROM stock_impact si JOIN news n ON si.news_id = n.id WHERE si.ticker = 'LODHA.NS' ORDER BY si.id DESC LIMIT 10")
lodha_rows = c.fetchall()
for r in lodha_rows:
    print(f"  id={r[0]} base={r[1]} status={r[2]} time={r[3]}")

print("\n=== All DLF.NS records ===")
c.execute("SELECT si.id, si.base_price, si.status, n.news_time FROM stock_impact si JOIN news n ON si.news_id = n.id WHERE si.ticker = 'DLF.NS' ORDER BY si.id DESC LIMIT 10")
dlf_rows = c.fetchall()
for r in dlf_rows:
    print(f"  id={r[0]} base={r[1]} status={r[2]} time={r[3]}")

# Fix LODHA rows with base ~777.95
print("\n=== Fixing LODHA.NS @ 14:05 ===")
pub_dt_lodha = datetime(2026, 4, 15, 14, 5, 0, tzinfo=IST)
correct_lodha = get_price_at_time('LODHA.NS', pub_dt_lodha)
print(f"Correct price at 14:05: {correct_lodha}")
if correct_lodha:
    c.execute("UPDATE stock_impact SET base_price=? WHERE ticker='LODHA.NS' AND base_price BETWEEN 770 AND 790", (correct_lodha,))
    print(f"Updated {c.rowcount} LODHA rows")

# Fix DLF rows with base ~571.80
print("\n=== Fixing DLF.NS @ 14:05 ===")
pub_dt_dlf = datetime(2026, 4, 15, 14, 5, 0, tzinfo=IST)
correct_dlf = get_price_at_time('DLF.NS', pub_dt_dlf)
print(f"Correct price at 14:05: {correct_dlf}")
if correct_dlf:
    c.execute("UPDATE stock_impact SET base_price=? WHERE ticker='DLF.NS' AND base_price BETWEEN 565 AND 578", (correct_dlf,))
    print(f"Updated {c.rowcount} DLF rows")

conn.commit()
conn.close()
print("\nDONE.")
