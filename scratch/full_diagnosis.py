"""
Full Alpha Lens Diagnosis Script
Checks: DB state, API responses, env keys, RSS feeds, Gemini API
"""
import sqlite3, os, sys, json
sys.stdout.reconfigure(encoding='utf-8')
from datetime import datetime, timedelta, timezone

BASE = os.path.join(os.path.dirname(__file__), '..')
DB1 = os.path.join(BASE, 'backend', 'news_cache.db')
DB2 = os.path.join(BASE, 'backend', 'users.db')

sys.path.insert(0, os.path.join(BASE, 'backend'))
from dotenv import load_dotenv
load_dotenv(os.path.join(BASE, '.env'))

print("=" * 65)
print("  ALPHA LENS — FULL SYSTEM DIAGNOSIS")
print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 65)

# ── 1. ENV KEYS ──────────────────────────────────────────────────
print("\n[1] ENVIRONMENT KEYS")
keys = {
    "GEMINI_API_KEY_1": os.environ.get("GEMINI_API_KEY_1"),
    "GEMINI_API_KEY_2": os.environ.get("GEMINI_API_KEY_2"),
    "GEMINI_API_KEY_3": os.environ.get("GEMINI_API_KEY_3"),
    "GEMINI_API_KEY_4": os.environ.get("GEMINI_API_KEY_4"),
    "SENDGRID_API_KEY": os.environ.get("SENDGRID_API_KEY"),
    "FLASK_SECRET_KEY": os.environ.get("FLASK_SECRET_KEY"),
    "TWELVEDATA_API_KEY": os.environ.get("TWELVEDATA_API_KEY"),
}
for k, v in keys.items():
    if v:
        print(f"   [OK] {k}: {'*' * 8}{v[-4:]}")
    else:
        print(f"   [MISSING] {k}: NOT SET")

# ── 2. NEWS DATABASE ─────────────────────────────────────────────
print("\n[2] NEWS DATABASE (news_cache.db)")
try:
    conn = sqlite3.connect(DB1)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM news")
    total = c.fetchone()[0]
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    c.execute("SELECT COUNT(*) FROM news WHERE created_at >= ?", (seven_days_ago,))
    recent = c.fetchone()[0]
    c.execute("SELECT MIN(created_at), MAX(created_at) FROM news")
    drange = c.fetchone()
    c.execute("SELECT COUNT(*) FROM stock_impact")
    impacts = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM stock_impact WHERE status = 'Active View'")
    active_impacts = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM news WHERE aam_janta_translation IS NOT NULL")
    with_ai = c.fetchone()[0]

    status_news = 'OK' if recent > 0 else 'PROBLEM - 0 news shown on frontend!'
    print(f"   Total news:           {total}")
    print(f"   News in last 7 days:  {recent}  [{status_news}]")
    print(f"   Date range:           {drange[0]} -> {drange[1]}")
    print(f"   Stock impact rows:    {impacts}")
    print(f"   Active signals:       {active_impacts}")
    print(f"   With AI explanation:  {with_ai}/{total}")

    print("\n   Latest 5 headlines:")
    c.execute("SELECT headline, created_at, category FROM news ORDER BY created_at DESC LIMIT 5")
    for row in c.fetchall():
        print(f"     [{row[1]}] [{row[2]}] {row[0][:70]}")

    conn.close()
except Exception as e:
    print(f"   [ERROR] DB Error: {e}")

# ── 3. RSS FEEDS ──────────────────────────────────────────────────
print("\n[3] RSS FEED CONNECTIVITY")
import feedparser
RSS = [
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms"),
    ("MoneyControl Buzzing",   "https://www.moneycontrol.com/rss/buzzingstocks.xml"),
    ("LiveMint Markets",       "https://www.livemint.com/rss/markets"),
    ("Google News NSE/BSE",    "https://news.google.com/rss/search?q=NSE+BSE+Nifty+Sensex+when:7d&hl=en-IN&gl=IN&ceid=IN:en"),
]
for name, url in RSS:
    try:
        feed = feedparser.parse(url)
        count = len(feed.entries)
        if count > 0:
            print(f"   [OK] {name}: {count} entries")
        else:
            print(f"   [EMPTY] {name}: 0 entries (feed empty or blocked)")
    except Exception as e:
        print(f"   [ERROR] {name}: {e}")

# ── 4. GEMINI API ─────────────────────────────────────────────────
print("\n[4] GEMINI API TEST")
try:
    from google import genai
    key = os.environ.get("GEMINI_API_KEY_1")
    if key:
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model='gemini-2.5-flash',
            contents='Reply with just: OK'
        )
        if resp.text:
            print(f"   [OK] Gemini API working. Response: {resp.text.strip()[:30]}")
        else:
            print("   [ERROR] Gemini returned empty response")
    else:
        print("   [MISSING] No GEMINI_API_KEY_1 to test")
except Exception as e:
    print(f"   [ERROR] Gemini: {e}")

# ── 5. YFINANCE / INDEX DATA ──────────────────────────────────────
print("\n[5] INDEX PRICE DATA (Nifty/Sensex)")
try:
    import yfinance_twelvedata_shim as yf  # type: ignore
    for sym, name in [('^NSEI','NIFTY 50'), ('^BSESN','SENSEX')]:
        try:
            t = yf.Ticker(sym)
            fi = t.fast_info
            lp = fi.last_price
            pc = fi.previous_close
            print(f"   [OK] {name}: Last={lp}, PrevClose={pc}")
        except Exception as e:
            print(f"   [ERROR] {name}: {e}")
except Exception as e:
    print(f"   [ERROR] yfinance import error: {e}")

# ── 6. LIVE API ENDPOINT TEST ─────────────────────────────────────
print("\n[6] LIVE API ENDPOINT TEST")
try:
    import requests
    r = requests.get("http://127.0.0.1:5000/api/news/all", timeout=5)
    data = r.json()
    news_count = len(data.get('news', []))
    market_open = data.get('market_open')
    print(f"   /api/news/all -> {news_count} articles returned, market_open={market_open}")
    if news_count == 0:
        print("   [PROBLEM] Frontend gets ZERO news - this is why nothing shows!")
    else:
        print(f"   [OK] News API working. First: {data['news'][0]['headline'][:60]}")
except Exception as e:
    print(f"   [ERROR] API not reachable (is server running?): {e}")

try:
    r2 = requests.get("http://127.0.0.1:5000/api/indices", timeout=8)
    idx = r2.json()
    for i in idx:
        price = i.get('price')
        chg = i.get('change_pct')
        status = '[OK]' if price else '[NO DATA]'
        print(f"   {status} {i['name']}: Rs{price} ({chg}%)")
except Exception as e:
    print(f"   [ERROR] /api/indices: {e}")

print("\n" + "=" * 65)
print("  DIAGNOSIS COMPLETE")
print("=" * 65)
