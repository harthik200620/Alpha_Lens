import sqlite3

conn = sqlite3.connect('news_cache.db')
c = conn.cursor()

c.execute("""
    SELECT s.id, s.ticker, s.base_price, s.current_price, s.status, n.news_time, n.headline 
    FROM stock_impact s 
    JOIN news n ON s.news_id = n.id 
    WHERE s.ticker IN ('ICICIBANK.NS', 'BSE.NS') 
    ORDER BY s.id DESC 
    LIMIT 10
""")

print("ID | Ticker | Base | Current | Status | News Time | Headline")
for r in c.fetchall():
    sid, tk, bp, cp, st, nt, hl = r
    diff = ((cp - bp) / bp * 100) if bp > 0 else 0
    print(f"{sid} | {tk} | {bp:.2f} | {cp:.2f} | {diff:+.2f}% | {st} | {nt} | {hl[:40]}")

conn.close()
