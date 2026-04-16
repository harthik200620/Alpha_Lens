import sqlite3
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from backend import yfinance_twelvedata_shim as yf
from datetime import datetime, timezone
import dateutil.parser
import warnings
import math
warnings.filterwarnings('ignore')

def fix_base_prices():
    print("Fixing base prices for historical stock impacts...")
    conn = sqlite3.connect('news_cache.db')
    c = conn.cursor()
    
    # Get all stock impacts and their news dates
    c.execute('''SELECT s.id, s.ticker, s.current_price, n.created_at, n.news_time 
                 FROM stock_impact s 
                 JOIN news n ON s.news_id = n.id''')
    rows = c.fetchall()
    
    updated_count = 0
    for row in rows:
        impact_id, ticker, current_price, created_at, news_time = row
        
        # Determine the target datetime
        target_dt = None
        if news_time and news_time != "Just Now" and news_time != "System Processing":
            try:
                target_dt = dateutil.parser.parse(news_time)
            except:
                pass
                
        if target_dt is None:
            try:
                # sqlite format: 2026-04-05 07:30:00
                target_dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                target_dt = target_dt.replace(tzinfo=timezone.utc)
            except:
                continue

        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=timezone.utc)

        target_date_str = target_dt.strftime('%Y-%m-%d')
        
        try:
            tick = yf.Ticker(ticker)
            # Fetch last 30 days to ensure we capture the prior trading day if it was a weekend
            hist = tick.history(period="1mo")
            if hist.empty:
                continue
                
            # Find the closest price equal to or before the news time
            hist_filtered = hist[hist.index <= target_dt]
            
            if not hist_filtered.empty:
                # Get the price right before the news
                new_base_price = float(hist_filtered.iloc[-1]['Close'])
            else:
                # If news is older than our history, or on a weekend before any data
                # just get the earliest available open price
                new_base_price = float(hist.iloc[0]['Open'])

            # Sometimes prices are exactly the same by luck, but if they are exactly the same
            # and it's because of the backfill, we definitely fix it.
            if math.isnan(new_base_price) or new_base_price <= 0:
                continue

            # Update the base price
            c.execute("UPDATE stock_impact SET base_price = ? WHERE id = ?", (new_base_price, impact_id))
            
            # Recalculate diff to trigger frontend changes immediately
            if current_price and current_price > 0:
                diff_percent = ((current_price - new_base_price) / new_base_price) * 100
                status = 'Active View'
                
                # Check target/stop loss briefly
                c.execute("SELECT impact FROM stock_impact WHERE id = ?", (impact_id,))
                impact_direction = c.fetchone()[0].lower()
                is_bull = 'bullish' in impact_direction
                
                if is_bull:
                    if diff_percent >= 1.5: status = 'Predicted Target Hit'
                    elif diff_percent <= -3.0: status = 'Reacted Against Prediction'
                else:
                    if diff_percent <= -1.5: status = 'Predicted Target Hit'
                    elif diff_percent >= 3.0: status = 'Reacted Against Prediction'

                c.execute("UPDATE stock_impact SET status = ? WHERE id = ?", (status, impact_id))

            updated_count += 1
            print(f"[{ticker}] Base price updated to ₹{new_base_price:.2f}")

        except Exception as e:
            print(f"Error processing {ticker}: {e}")
            
    conn.commit()
    conn.close()
    print(f"DONE! Fixed {updated_count} base prices.")

if __name__ == "__main__":
    fix_base_prices()
