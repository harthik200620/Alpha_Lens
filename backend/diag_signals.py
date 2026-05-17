import sqlite3, os, sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
c = sqlite3.connect('news_cache.db')

print("=== News last 48h ===")
r = c.execute("SELECT count(*) FROM news WHERE created_at >= datetime('now', '-2 days')").fetchone()
print("Count:", r[0])

print("\n=== Signals last 14 days ===")
r = c.execute("SELECT count(*) FROM stock_impact WHERE created_at >= datetime('now', '-14 days')").fetchone()
print("Count:", r[0])

print("\n=== All signals (most recent first) ===")
rows = c.execute("SELECT ticker, status, created_at FROM stock_impact ORDER BY created_at DESC LIMIT 5").fetchall()
for row in rows:
    print(row)

print("\n=== SEEN_HEADLINES check: news with no signals ===")
rows = c.execute("""
    SELECT n.id, n.headline, n.created_at FROM news n
    WHERE NOT EXISTS (SELECT 1 FROM stock_impact si WHERE si.news_id = n.id)
    ORDER BY n.created_at DESC LIMIT 5
""").fetchall()
for row in rows:
    print(f"  id={row[0]} | {row[1][:65]}")
    print(f"    created_at: {row[2]}")

print("\n=== Latest ENV check ===")
import dotenv, pathlib
dotenv.load_dotenv(str(pathlib.Path(__file__).parent.parent / '.env'))
keys = [os.environ.get(f"GEMINI_API_KEY_{i}") for i in range(1,5)]
keys = [k for k in keys if k]
print(f"  GEMINI keys loaded: {len(keys)}")
sm = os.environ.get("SM_GEMINI_API_KEY", "")
print(f"  SM_GEMINI_KEY configured: {'YES' if sm else 'NO'}")
