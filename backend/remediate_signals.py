"""
Evaluate all Active View signals and update their status in the database.
Uses the same logic as the yfinance_worker but applies it retroactively to all signals.
"""
import sqlite3
import sys
import os
from datetime import datetime, timedelta, timezone

# Add backend to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)

import yfinance_twelvedata_shim as yf
import yfinance

DB_PATH = os.path.join(os.path.dirname(__file__), 'news_cache.db')
TARGET_PCT = 2.0
STOP_PCT = 1.0

def evaluate_signal(ticker, base_price, impact_direction, created_at_str):
    """
    Evaluate a single signal against current price data.
    Returns: ('Stop Loss Hit' | 'Predicted Target Hit' | 'Active View', diff_pct)
    """
    try:
        is_bullish = 'bullish' in impact_direction.lower()
        
        # Parse signal timestamp
        created_dt = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600
        
        # Get current price using yfinance
        stock = yfinance.Ticker(ticker)
        info = stock.info
        current_price = info.get('currentPrice') or info.get('regularMarketPrice')
        
        if current_price is None or base_price <= 0:
            return 'Active View', 0.0
        
        current_price = float(current_price)
        
        # Calculate diff percentage
        diff_pct = round(((current_price - base_price) / base_price) * 100, 2)
        
        # Check historical hits (if old enough)
        if age_hours >= 12:
            hist = stock.history(period='30d', interval='1d')
            if not hist.empty:
                IST = timezone(timedelta(hours=5, minutes=30))
                created_ist = created_dt.astimezone(IST)
                
                for idx, row in hist.iterrows():
                    bar_ist = idx.tz_convert(IST) if idx.tzinfo else idx.replace(tzinfo=timezone.utc).astimezone(IST)
                    
                    # Skip candles before/at signal time
                    if bar_ist <= created_ist:
                        continue
                    
                    h = float(row['High'])
                    l = float(row['Low'])
                    h_pct = ((h - base_price) / base_price) * 100
                    l_pct = ((l - base_price) / base_price) * 100
                    
                    if is_bullish:
                        if l_pct <= -STOP_PCT:
                            return 'Stop Loss Hit', round(l_pct, 2)
                        if h_pct >= TARGET_PCT:
                            return 'Predicted Target Hit', round(h_pct, 2)
                    else:
                        if h_pct >= STOP_PCT:
                            return 'Stop Loss Hit', round(h_pct, 2)
                        if l_pct <= -TARGET_PCT:
                            return 'Predicted Target Hit', round(l_pct, 2)
        
        # Check current price trigger
        if is_bullish:
            if diff_pct >= TARGET_PCT:
                return 'Predicted Target Hit', diff_pct
            elif diff_pct <= -STOP_PCT:
                return 'Stop Loss Hit', diff_pct
        else:
            if diff_pct <= -TARGET_PCT:
                return 'Predicted Target Hit', diff_pct
            elif diff_pct >= STOP_PCT:
                return 'Stop Loss Hit', diff_pct
        
        # Check expiry
        if age_hours >= 72:
            return 'Expired', diff_pct
        
        return 'Active View', diff_pct
        
    except Exception as e:
        print(f"Error evaluating {ticker}: {e}")
        return 'Active View', 0.0

def main():
    print("Evaluating all Active View signals and updating database...")
    conn = sqlite3.connect(DB_PATH, timeout=15)
    c = conn.cursor()
    
    # Fetch all Active View signals
    c.execute("""SELECT id, ticker, impact, base_price, created_at 
                 FROM stock_impact WHERE status = 'Active View' AND base_price > 0""")
    signals = c.fetchall()
    
    print(f"Found {len(signals)} Active View signals to evaluate.\n")
    
    updated = 0
    status_counts = {}
    
    for sig_id, ticker, impact, base_price, created_at in signals:
        new_status, diff_pct = evaluate_signal(ticker, base_price, impact, created_at)
        
        if new_status != 'Active View':
            c.execute("UPDATE stock_impact SET status = ?, estimated_change_percent = ? WHERE id = ?",
                     (new_status, diff_pct, sig_id))
            updated += 1
            print(f"[{sig_id}] {ticker} {impact}: {new_status} ({diff_pct}%)")
        
        status_counts[new_status] = status_counts.get(new_status, 0) + 1
    
    conn.commit()
    conn.close()
    
    print(f"\n=== SUMMARY ===")
    print(f"Total signals evaluated: {len(signals)}")
    print(f"Updated: {updated}")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

if __name__ == "__main__":
    main()
