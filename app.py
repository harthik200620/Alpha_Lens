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
                for entry in feed.entries[:3]:
                    raw_articles.append({
                        "headline": entry.title,
                        "time": entry.published if hasattr(entry, 'published') else "Just Now"
                    })
            except Exception as e:
                print(f"RSS Error on {url}: {e}")

        analyzed_news = []
        for article in raw_articles:
            headline = article['headline']
            prompt = f"""
            You are a Tier-1 Quantitative Macro Analyst. Analyze this live news headline: '{headline}'
            
            RULES:
            1. If it is a generic/boring news story, return EXACTLY: {{"ignore": true}}
            2. ALL analysis MUST STRICTLY be focused on the INDIAN economy and Indian stocks. If it does not affect India, ignore it.
            3. If it is a MAJOR systemic event, policy change, or highly impactful corporate action affecting India, analyze it.
            4. Append '.NS' or '.BO' to stock tickers to represent NSE or BSE.
            
            Output strictly as JSON:
            {{
              "ignore": false,
              "headline": "{headline}",
              "aam_janta_translation": "Explain the macro impact simply for a retail trader in 2 sentences.",
              "macro_pathway": ["Trigger Event", "Primary Hit", "Ripple Effect", "Macro Outcome"],
              "affected_stocks": [
                {{
                    "ticker": "TICKER.NS",
                    "impact": "BULLISH or BEARISH",
                    "estimated_change_percent": 2.5,
                    "view": "High Conviction",
                    "reason": "Why this specific stock moves."
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
                                INSERT INTO news (headline, news_time, aam_janta_translation, macro_pathway)
                                VALUES (?, ?, ?, ?)
                            ''', (headline, analysis['news_time'], analysis.get('aam_janta_translation', ''), json.dumps(analysis.get('macro_pathway', []))))
                            
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
                                    INSERT INTO stock_impact (news_id, ticker, impact, estimated_change_percent, view, reason, base_price, current_price)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ''', (news_id, ticker, stock.get('impact'), stock.get('estimated_change_percent'), stock.get('view'), stock.get('reason'), base_price, base_price))
                            
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
                    is_bullish = 'bullish' in impact.lower()
                    
                    if is_bullish:
                        if diff_percent >= 2.0:
                            new_status = 'Profit Target 2% Hit'
                        elif diff_percent <= -2.0:
                            new_status = 'Stop Loss 2% Hit'
                    else: # bearish
                        if diff_percent <= -2.0: # stock dropped 2%, meaning bearish call gained 2%
                            new_status = 'Profit Target 2% Hit'
                        elif diff_percent >= 2.0:
                            new_status = 'Stop Loss 2% Hit'
                            
                    c.execute("UPDATE stock_impact SET current_price = ?, status = ? WHERE id = ?", (current_price, new_status, stock_id))
                except Exception as e:
                    pass # ignore yfinance errors for individual tickers
                
            conn.commit()
            conn.close()
        except Exception as e:
            print("YFinance Worker Error:", e)
            
        time.sleep(120) # Update prices every 2 minutes

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