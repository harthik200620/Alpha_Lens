import sqlite3
import os
from datetime import datetime

db_path = r"c:\Project rohan\Alpha_Lens\backend\news_cache.db"

def merge_duplicates():
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Get all active signals
    c.execute("""
        SELECT id, news_id, ticker, impact, base_price, current_price, confidence_score, view, reason, created_at
        FROM stock_impact
        WHERE status = 'Active View'
        ORDER BY ticker, created_at ASC
    """)
    rows = c.fetchall()
    
    # Group by ticker
    by_group = {}
    for r in rows:
        db_id, news_id, ticker, impact, bp, cp, conf, view, reason, created_at = r
        key = ticker
        if key not in by_group:
            by_group[key] = []
        by_group[key].append({
            'id': db_id,
            'news_id': news_id,
            'ticker': ticker,
            'impact': impact,
            'base_price': bp or 0.0,
            'current_price': cp or 0.0,
            'confidence': conf or 80,
            'view': view or '',
            'reason': reason or '',
            'created_at': created_at
        })
        
    merged_count = 0
    deleted_ids = []
    
    for key, items in by_group.items():
        if len(items) <= 1:
            continue
            
        # We have multiple active signals for the same ticker
        # We will iterate through them and merge items that are close in time and price
        primary = items[0]
        for dup in items[1:]:
            # Calculate time difference
            try:
                p_dt = datetime.strptime(primary['created_at'], '%Y-%m-%d %H:%M:%S')
            except Exception:
                p_dt = datetime.utcnow()
            try:
                d_dt = datetime.strptime(dup['created_at'], '%Y-%m-%d %H:%M:%S')
            except Exception:
                d_dt = p_dt
                
            time_diff = abs((d_dt - p_dt).total_seconds())
            if time_diff > 86400:
                # Too far apart in time, skip merging this one, make it the new primary for subsequent checks
                primary = dup
                continue
                
            # Calculate price difference
            p_bp = primary['base_price']
            d_bp = dup['base_price']
            is_similar = False
            if p_bp == 0.0 or d_bp == 0.0:
                is_similar = True
            else:
                pct_diff = abs(p_bp - d_bp) / p_bp
                if pct_diff <= 0.025:
                    is_similar = True
                    
            if not is_similar:
                # Price is too different, skip merging
                primary = dup
                continue
                
            # Perform Merge!
            boosted_conf = min(99, max(primary['confidence'], dup['confidence']) + 10)
            new_view = 'High Conviction' if boosted_conf >= 85 else 'Moderate Conviction'
            
            # Compare confidence scores to decide final direction
            if dup['confidence'] > primary['confidence']:
                final_impact = dup['impact']
            else:
                final_impact = primary['impact']
            
            # Fetch headline of the duplicate news
            c.execute("SELECT headline FROM news WHERE id = ?", (dup['news_id'],))
            hl_row = c.fetchone()
            dup_hl = hl_row[0] if hl_row else "Consensus News"
            
            # Merge reasons
            if dup['impact'] != primary['impact']:
                merged_reason = f"{primary['reason']} | [Consensus Boost ({dup['impact']}): '{dup_hl}'] {dup['reason']}"
            else:
                merged_reason = f"{primary['reason']} | [Consensus Boost: '{dup_hl}'] {dup['reason']}"
            
            # Update primary in DB
            c.execute("""
                UPDATE stock_impact
                SET confidence_score = ?, view = ?, reason = ?, impact = ?
                WHERE id = ?
            """, (boosted_conf, new_view, merged_reason, final_impact, primary['id']))
            
            # Delete duplicate row
            c.execute("DELETE FROM stock_impact WHERE id = ?", (dup['id'],))
            
            print(f"Merged duplicate signal for {key}:")
            print(f"  Primary ID: {primary['id']} ({primary['impact']} Confidence {primary['confidence']} -> {final_impact} {boosted_conf})")
            print(f"  Deleted ID: {dup['id']} ({dup['impact']} News: '{dup_hl[:50]}...')")
            
            # Update local primary state in case there is a third duplicate
            primary['confidence'] = boosted_conf
            primary['view'] = new_view
            primary['reason'] = merged_reason
            primary['impact'] = final_impact
            
            merged_count += 1
            deleted_ids.append(dup['id'])
            
    conn.commit()
    conn.close()
    
    print(f"\nRetrospective merge complete. Merged and deleted {merged_count} duplicate rows.")

if __name__ == "__main__":
    merge_duplicates()
