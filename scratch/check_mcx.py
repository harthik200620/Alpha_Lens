import sqlite3
import collections

conn = sqlite3.connect('../news_cache.db')
c = conn.cursor()

print('--- Active / Recent MCX impact signals ---')
c.execute("SELECT impact, base_price, current_price, status, created_at FROM stock_impact WHERE ticker='MCX.NS' ORDER BY created_at DESC LIMIT 5")
for r in c.fetchall():
    print(f"Impact: {r[0]}, Base: {r[1]}, Current: {r[2]}, Status: {r[3]}, Time: {r[4]}")

print('\n--- Historical MCX pattern results ---')
c.execute("SELECT direction, outcome, change_pct, created_at FROM historical_patterns WHERE ticker='MCX.NS' ORDER BY created_at DESC LIMIT 5")
for r in c.fetchall():
    print(f"Dir: {r[0]}, Outcome: {r[1]}, Change %: {r[2]}, Time: {r[3]}")
conn.close()
