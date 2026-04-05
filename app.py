from flask import Flask, render_template, request, jsonify, session
import sqlite3
import secrets
import random
import threading
import time
import json
from werkzeug.security import generate_password_hash
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import feedparser
import google.generativeai as genai
import yfinance as yf
import logging
yf.set_tz_cache_location("venv/yf_cache") # Optional, but helps cleanly separate cache
logger = logging.getLogger('yfinance')
logger.disabled = True
logger.propagate = False

from datetime import datetime, timedelta

app = Flask(__name__, template_folder='.')
app.secret_key = "super_secret_alpha_lens_key"

# In-memory store for OTPs
OTP_STORE = {}
SENDGRID_API_KEY = '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'

def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def connect_news_db():
    conn = sqlite3.connect('news_cache.db', timeout=20.0)
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

def init_news_db():
    conn = connect_news_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            headline TEXT NOT NULL,
            news_time TEXT,
            aam_janta_translation TEXT,
            macro_pathway TEXT, -- Stored as JSON string
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    try:
        c.execute("ALTER TABLE news ADD COLUMN category TEXT DEFAULT 'General'")
    except sqlite3.OperationalError:
        pass
    c.execute('''
        CREATE TABLE IF NOT EXISTS stock_impact (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER,
            ticker TEXT,
            impact TEXT,
            estimated_change_percent REAL,
            view TEXT,
            reason TEXT,
            base_price REAL,
            current_price REAL,
            status TEXT DEFAULT 'Active View', -- 'Active View', 'Profit Target Hit', 'Stop Loss Hit'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(news_id) REFERENCES news(id)
        )
    ''')
    try:
        c.execute("ALTER TABLE stock_impact ADD COLUMN confidence_score INTEGER DEFAULT 80")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_db()
init_news_db()

# ==========================================
# LIVE AI NEWS ENGINE (LiveMint, ET, MoneyControl)
# ==========================================
# We no longer use in-memory cache for news, but we keep it here just in case.
LIVE_NEWS_CACHE = []

# Your Gemini API Keys for rotation
API_KEYS = [
    "AIzaSyBpbzop1zP_7fLml_09Oo7aFk8W1jWF9SQ",
    "AIzaSyABS1FGUxLRNcekIfquMcIKcGVjKd-bGq4",
    "AIzaSyDkS2vjNmGCQXwqUjhYx5dMdP_qwwQlqTU"
]
current_key_idx = 0
genai.configure(api_key=API_KEYS[current_key_idx])
model = genai.GenerativeModel('gemini-2.5-flash')

# Top Tier Indian Financial RSS Feeds
RSS_SOURCES = [
    "https://www.livemint.com/rss/markets", 
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/MCtopnews.xml"
]

def clean_json(raw_text):
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
    return json.loads(cleaned.strip())

def ai_news_worker():
    global LIVE_NEWS_CACHE, current_key_idx, model
    print("🚀 Alpha Lens Background Engine Started. Fetching LiveMint, ET & MoneyControl...")
    
    while True:
        raw_articles = []
        for url in RSS_SOURCES:
            try:
                feed = feedparser.parse(url)
                # Get top 3 latest news from each source
                for entry in feed.entries[:15]:  # Deeper scrape to avoid missing updates
                    raw_articles.append({
                        "headline": entry.title,
                        "time": entry.published if hasattr(entry, 'published') else "Just Now"
                    })
            except Exception as e:
                print(f"RSS Error on {url}: {e}")
        
        if raw_articles:
            print(f"📡 Scraped {len(raw_articles)} headlines. Analyzing with Gemini...")
        
        analyzed_news = []
        for article in raw_articles:
            headline = article['headline']
            prompt = f"""
            Identify high-impact news and categorize it accurately.

            Headline: '{headline}'

            STRICT CATEGORIZATION RULES (Choose ONE):
            - "Finance": Stock market trends, RBI policy, banking, macroeconomics, Sensex/Nifty, currency.
            - "Business": Company specific news (mergers, earnings, IPOs, management), startup news, industries.
            - "Technology": Tech launches, AI, gadgets, software.
            - "Politics": Government decisions, elections, policy shifts (not purely economic).
            - "World": Global events, international politics, wars.
            - "General": Miscellaneous news ONLY if none of the above fit. DO NOT use this for market/corporate news.

            OTHER RULES:
            1. If garbage/ad, set "ignore" to true.
            2. If it directly impacts the Indian stock market, identify 1-4 NSE/BSE tickers (ticker.NS or ticker.BO). 
            3. If no specific stock impact, leave "affected_stocks" as [].
            4. Realistic 'estimated_change_percent' only (0.5 for small move, 2-4 for major).
            5. 'impact': BULLISH | SLIGHTLY BULLISH | BEARISH | SLIGHTLY BEARISH.
            6. 'view': High Conviction | Moderate Conviction.
            7. 'confidence_score': An integer (0-100) reflecting the clarity of the news impact.

            Output STRICT valid JSON:
            {{
              "ignore": false,
              "category": "Finance",
              "headline": "{headline}",
              "aam_janta_translation": "Summary in 2 simple sentences.",
              "macro_pathway": ["Trigger", "Direct Impact", "Ripple", "Result"],
              "affected_stocks": [
                {{
                    "ticker": "TICKER.NS",
                    "impact": "BULLISH | SLIGHTLY BULLISH | BEARISH | SLIGHTLY BEARISH",
                    "estimated_change_percent": 2.5,
                    "view": "High Conviction | Moderate Conviction",
                    "confidence_score": 92,
                    "reason": "Why?"
                }}
              ]
            }}
            """
            
            success = False
            retries = 0
            while not success and retries < 2:
                try:
                    resp = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
                    analysis = clean_json(resp.text)
                    if not analysis.get("ignore", False):
                        analysis['news_time'] = article['time']
                        analyzed_news.append(analysis)
                        
                        # --- INSERT INTO DB ---
                        conn = connect_news_db()
                        c = conn.cursor()
                        # Check if headline already exists to avoid duplicates
                        c.execute("SELECT id FROM news WHERE headline = ?", (headline,))
                        if not c.fetchone():
                            c.execute('''
                                INSERT INTO news (headline, news_time, aam_janta_translation, macro_pathway, category)
                                VALUES (?, ?, ?, ?, ?)
                            ''', (headline, analysis['news_time'], analysis.get('aam_janta_translation', ''), json.dumps(analysis.get('macro_pathway', [])), analysis.get('category', 'General')))
                            
                            news_id = c.lastrowid
                            
                            # Add stocks with base prices
                            for stock in analysis.get('affected_stocks', []):
                                ticker = stock.get('ticker')
                                base_price = 0.0
                                try:
                                    tick_data = yf.Ticker(ticker)
                                    base_price = tick_data.fast_info.last_price
                                except:
                                    base_price = 100.0 # fallback
                                
                                c.execute('''
                                    INSERT INTO stock_impact (news_id, ticker, impact, estimated_change_percent, view, reason, base_price, current_price, confidence_score)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ''', (news_id, ticker, stock.get('impact'), stock.get('estimated_change_percent'), stock.get('view'), stock.get('reason'), base_price, base_price, stock.get('confidence_score', 80)))
                            
                            conn.commit()
                            print(f"✅ AI Found Alpha & Saved to DB: {headline[:40]}...")
                        conn.close()
                        
                    success = True
                except Exception as e:
                    error_msg = str(e).lower()
                    if "429" in error_msg or "quota" in error_msg:
                        current_key_idx = (current_key_idx + 1) % len(API_KEYS)
                        genai.configure(api_key=API_KEYS[current_key_idx])
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        time.sleep(2)
                        retries += 1
                    else:
                        break
            time.sleep(3) # Prevent rate limiting
            
        # Clean up old news (older than 4 days)
        try:
            conn = connect_news_db()
            c = conn.cursor()
            four_days_ago = (datetime.utcnow() - timedelta(days=4)).strftime('%Y-%m-%d %H:%M:%S')
            c.execute("DELETE FROM stock_impact WHERE news_id IN (SELECT id FROM news WHERE created_at < ?)", (four_days_ago,))
            c.execute("DELETE FROM news WHERE created_at < ?", (four_days_ago,))
            conn.commit()
            conn.close()
        except Exception as e:
            print("Cleanup error:", e)
            
        time.sleep(600) # Wait 10 minutes before scraping again

def yfinance_worker():
    print("📈 YFinance Live Price Engine Started. Tracking Active Views...")
    while True:
        try:
            conn = connect_news_db()
            c = conn.cursor()
            # Fetch active views from last 2 days
            two_days_ago = (datetime.utcnow() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
            c.execute("SELECT id, ticker, base_price, impact FROM stock_impact WHERE status = 'Active View' AND created_at > ?", (two_days_ago,))
            active_stocks = c.fetchall()
            
            for row in active_stocks:
                stock_id, ticker, base_price, impact = row
                try:
                    tick_data = yf.Ticker(ticker)
                    current_price = tick_data.fast_info.last_price
                    
                    diff_percent = ((current_price - base_price) / base_price) * 100
                    
                    new_status = 'Active View'
                    impact_lower = impact.lower()
                    is_bullish = 'bullish' in impact_lower
                    is_slightly = 'slightly' in impact_lower
                    
                    # For slightly bullish/bearish, use 1% target/loss thresholds
                    target_pct = 1.0 if is_slightly else 2.0
                    
                    if is_bullish:
                        if diff_percent >= target_pct:
                            new_status = 'Predicted Target Hit'
                        elif diff_percent <= -target_pct:
                            new_status = 'Reacted Against Prediction'
                    else: # bearish
                        if diff_percent <= -target_pct: # stock dropped, bearish call correct
                            new_status = 'Predicted Target Hit'
                        elif diff_percent >= target_pct:
                            new_status = 'Reacted Against Prediction'
                            
                    c.execute("UPDATE stock_impact SET current_price = ?, status = ? WHERE id = ?", (current_price, new_status, stock_id))
                except Exception as e:
                    pass # ignore yfinance errors for individual tickers
                
            conn.commit()
            conn.close()
        except Exception as e:
            print("YFinance Worker Error:", e)
            
        time.sleep(60) # Update prices every 1 minute

# Start background threads
engine_thread = threading.Thread(target=ai_news_worker, daemon=True)
engine_thread.start()

yf_thread = threading.Thread(target=yfinance_worker, daemon=True)
yf_thread.start()

# ==========================================
# APP ROUTES
# ==========================================
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/indices', methods=['GET'])
def get_indices():
    from datetime import timezone, timedelta as td
    ist = timezone(td(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    weekday = now_ist.weekday()  # 0=Mon … 4=Fri, 5=Sat, 6=Sun
    hour, minute = now_ist.hour, now_ist.minute

    market_open = (
        weekday < 5 and
        ((hour == 9 and minute >= 15) or (10 <= hour <= 14) or
         (hour == 15 and minute <= 30))
    )

    if market_open:
        price_label = "Live"
        market_status = "Market Open"
    else:
        price_label = "Prev. Close"
        if weekday >= 5:
            market_status = "Market Closed · Opens Mon 9:15 AM IST"
        elif hour < 9 or (hour == 9 and minute < 15):
            market_status = "Market Closed · Opens at 9:15 AM IST"
        else:
            market_status = "Market Closed · Closed at 3:30 PM IST"

    indices = [
        {"symbol": "^NSEI",    "name": "NIFTY 50"},
        {"symbol": "^BSESN",   "name": "SENSEX"},
        {"symbol": "^NSEBANK", "name": "BANK NIFTY"},
        {"symbol": "^NSMIDCP", "name": "MIDCAP NIFTY"},
    ]
    result = []
    for idx in indices:
        try:
            t = yf.Ticker(idx["symbol"])
            info = t.fast_info
            price = info.last_price
            prev_close = info.previous_close
            # When market is closed show 0% change — price shown is last recorded price
            if market_open and prev_close and prev_close > 0:
                change_pct = ((price - prev_close) / prev_close) * 100
            else:
                change_pct = 0.0
            result.append({
                "name": idx["name"],
                "price": round(price, 2),
                "change_pct": round(change_pct, 2),
                "is_live": market_open,
                "price_label": price_label,
                "market_status": market_status
            })
        except Exception as e:
            result.append({"name": idx["name"], "price": None, "change_pct": 0.0,
                           "is_live": market_open, "price_label": price_label,
                           "market_status": market_status})
    return jsonify(result)

@app.route('/api/news/top', methods=['GET'])
def get_top_news():
    try:
        conn = connect_news_db()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM news ORDER BY created_at DESC LIMIT 1")
        news_row = c.fetchone()
        
        if not news_row:
            conn.close()
            return jsonify([{
                "headline": "AI Engine is analyzing LiveMint, ET, and MoneyControl...",
                "news_time": "System Processing",
                "aam_janta_translation": "The background engine is downloading and filtering live market data. Please wait.",
                "macro_pathway": ["Scrape", "Filter", "Analyze", "Deploy"],
                "affected_stocks": []
            }])
        
        news_item = dict(news_row)
        try:
            news_item['macro_pathway'] = json.loads(news_item['macro_pathway'])
        except:
            news_item['macro_pathway'] = []
            
        c.execute("SELECT * FROM stock_impact WHERE news_id = ?", (news_item['id'],))
        stocks = [dict(s) for s in c.fetchall()]
        news_item['affected_stocks'] = stocks
        conn.close()
        return jsonify([news_item])
    except Exception as e:
        print("Error fetching top news", e)
        return jsonify([])

@app.route('/api/news/all', methods=['GET'])
def get_all_news():
    try:
        conn = connect_news_db()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM news ORDER BY created_at DESC")
        news_rows = c.fetchall()
        
        all_news = []
        for row in news_rows:
            news_item = dict(row)
            try:
                news_item['macro_pathway'] = json.loads(news_item['macro_pathway'])
            except:
                news_item['macro_pathway'] = []
            c.execute("SELECT * FROM stock_impact WHERE news_id = ?", (news_item['id'],))
            stocks = [dict(s) for s in c.fetchall()]
            news_item['affected_stocks'] = stocks
            all_news.append(news_item)
            
        conn.close()
        return jsonify(all_news)
    except Exception as e:
        print("Error fetching all news", e)
        return jsonify([])

@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    data = request.json
    email = data.get('email')

    if not email:
        return jsonify({"error": "Email is required"}), 400

    otp = str(random.randint(100000, 999999))
    OTP_STORE[email] = otp

    message = Mail(
        from_email='verified_sender@yourdomain.com',  # <--- CHANGE THIS TO YOUR VERIFIED SENDGRID EMAIL
        to_emails=email,
        subject='Alpha Lens - Your Authentication Code',
        html_content=f'''
            <div style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
                <h2>Welcome to Alpha Lens</h2>
                <p>Your secure, one-time login code is:</p>
                <h1 style="color: #06b6d4; font-size: 32px; letter-spacing: 5px;">{otp}</h1>
                <p>This code will expire in 10 minutes.</p>
            </div>
        '''
    )

    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        sg.send(message)
        return jsonify({"message": "OTP sent successfully!"}), 200
    except Exception as e:
        print(f"SendGrid Error: {e}")
        return jsonify({"error": "Failed to send email via SendGrid. Check your Verified Sender Identity."}), 500

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    data = request.json
    email = data.get('email')
    user_otp = data.get('otp')

    if not email or email not in OTP_STORE or OTP_STORE[email] != user_otp:
        return jsonify({"error": "Invalid or expired OTP."}), 401

    del OTP_STORE[email]

    try:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("SELECT email FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        
        if not user:
            dummy_password = generate_password_hash(secrets.token_hex(16))
            c.execute("INSERT INTO users (email, password) VALUES (?, ?)", (email, dummy_password))
            conn.commit()
        
        conn.close()
        session['user'] = email
        return jsonify({"message": "Authentication successful", "user": email}), 200
    except Exception as e:
        return jsonify({"error": "Database error occurred."}), 500

@app.route('/api/oauth-signin', methods=['POST'])
def oauth_signin():
    data = request.json
    account_id = data.get('account_id') 

    if not account_id:
        return jsonify({"error": "Account ID required"}), 400

    try:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("SELECT email FROM users WHERE email = ?", (account_id,))
        user = c.fetchone()
        
        if not user:
            dummy_password = generate_password_hash(secrets.token_hex(16))
            c.execute("INSERT INTO users (email, password) VALUES (?, ?)", (account_id, dummy_password))
            conn.commit()
        
        conn.close()
        session['user'] = account_id
        return jsonify({"message": "Authentication successful", "user": account_id}), 200
    except Exception as e:
        return jsonify({"error": "Database error occurred."}), 500

@app.route('/api/me', methods=['GET'])
def get_current_user():
    if 'user' in session:
        return jsonify({"user": session['user']}), 200
    return jsonify({"user": None}), 200

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    return jsonify({"message": "Logged out"}), 200

if __name__ == '__main__':
    # Threaded=True allows the background AI loop to run alongside the website
    app.run(debug=True, port=5000, threaded=True)