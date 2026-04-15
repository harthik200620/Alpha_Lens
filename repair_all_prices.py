"""
Alpha Lens — Comprehensive Base Price Repair Script
Fixes ALL existing stock_impact records that have wrong base prices.
Compares the stored base_price against the actual 1-min price at news time
and updates any record that is significantly off.
"""
import sqlite3
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import os, time

DB_PATH = os.path.join(os.path.dirname(__file__), 'news_cache.db')
IST = timezone(timedelta(hours=5, minutes=30))

# ─── Bulletproof scalar extractor ───────────────────────────────────────────
def extract_scalar(val):
    if val is None:
        return None
    if hasattr(val, 'iloc'):
        val = val.iloc[0]
    try:
        return float(val)
    except (TypeError, ValueError):
        return None

# ─── Bulletproof price at time ───────────────────────────────────────────────
def get_price_at_time(ticker, pub_dt):
    """Returns the stock price at or just before pub_dt."""
    try:
        hist = yf.download(ticker, period='7d', interval='1m', 
                          progress=False, auto_adjust=True)
        if not hist.empty:
            hist.index = pd.to_datetime(hist.index).tz_convert(IST)
            # Use MultiIndex-safe column access
            if isinstance(hist.columns, pd.MultiIndex):
                close_series = hist['Close'].iloc[:, 0]
            else:
                close_series = hist['Close']
            
            # 45-min window before pub_dt
            window_start = pub_dt - timedelta(minutes=45)
            window = close_series[
                (close_series.index >= window_start) & 
                (close_series.index <= pub_dt)
            ]
            if not window.empty:
                price = extract_scalar(window.iloc[-1])
                if price and price > 0:
                    return round(price, 2), '1min-window'
            
            # Broadest: any bar before pub_dt
            past = close_series[close_series.index <= pub_dt]
            if not past.empty:
                price = extract_scalar(past.iloc[-1])
                if price and price > 0:
                    return round(price, 2), '1min-broad'
    except Exception as e:
        print(f"   [!] 1-min fetch error for {ticker}: {e}")

    # Fallback: fast_info
    try:
        fi = yf.Ticker(ticker).fast_info
        # If today, use last_price; otherwise use previous_close
        today = datetime.now(IST).date()
        if pub_dt.date() == today:
            lp = extract_scalar(fi.last_price)
            if lp and lp > 0:
                return round(lp, 2), 'live-fastinfo'
        pc = extract_scalar(fi.previous_close)
        if pc and pc > 0:
            return round(pc, 2), 'prev-close'
    except Exception as e:
        print(f"   [!] fast_info fallback error for {ticker}: {e}")

    return None, 'failed'


# ─── Main repair loop ─────────────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Get all stock_impact rows joined with their news publication time
c.execute("""
    SELECT si.id, si.ticker, si.base_price, si.status, n.news_time
    FROM stock_impact si
    JOIN news n ON si.news_id = n.id
    WHERE si.base_price > 0
    ORDER BY si.id DESC
    LIMIT 200
""")
rows = c.fetchall()
print(f"Found {len(rows)} records to check.\n")

fixed = 0
skipped = 0
failed = 0

for row in rows:
    sid, ticker, base_price, status, news_time = row
    
    try:
        pub_dt = parsedate_to_datetime(news_time).astimezone(IST)
    except Exception:
        try:
            pub_dt = datetime.fromisoformat(news_time).replace(tzinfo=IST)
        except Exception:
            print(f"  [SKIP] id={sid} {ticker}: cannot parse time '{news_time}'")
            skipped += 1
            continue

    # Only fix trading-hours records (skip off-hours news)
    weekday = pub_dt.weekday()
    t = pub_dt.hour * 60 + pub_dt.minute
    is_market = weekday < 5 and (9*60+15) <= t <= (15*60+30)
    
    if not is_market:
        skipped += 1
        continue
    
    correct_price, method = get_price_at_time(ticker, pub_dt)
    
    if correct_price is None:
        print(f"  [FAIL] id={sid} {ticker}: could not fetch price at {pub_dt.strftime('%H:%M')}")
        failed += 1
        continue
    
    # Only update if there's a meaningful difference (> 0.5%)
    diff_pct = abs(correct_price - base_price) / base_price * 100 if base_price > 0 else 100
    
    if diff_pct > 0.5:
        print(f"  [FIX]  id={sid} {ticker} @ {pub_dt.strftime('%Y-%m-%d %H:%M')} | stored={base_price} correct={correct_price} diff={diff_pct:.1f}% [{method}] status={status}")
        c.execute("UPDATE stock_impact SET base_price = ? WHERE id = ?", 
                  (correct_price, sid))
        fixed += 1
    else:
        skipped += 1
    
    time.sleep(0.3)  # be gentle with yfinance

conn.commit()
conn.close()

print(f"\n{'='*60}")
print(f"DONE: Fixed={fixed} | Skipped/OK={skipped} | Failed={failed}")
