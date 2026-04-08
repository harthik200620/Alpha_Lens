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
from google import genai
from google.genai import types
import yfinance as yf
import logging
yf.set_tz_cache_location("venv/yf_cache")
logger = logging.getLogger('yfinance')
logger.disabled = True
logger.propagate = False

from datetime import datetime, timedelta, timezone
from technical_analysis import (
    get_stock_technical_context,
    format_technical_context_for_prompt,
    get_market_regime
)

app = Flask(__name__, template_folder='.')
app.secret_key = "super_secret_alpha_lens_key"

# Minimum AI confidence to accept a prediction
MIN_CONFIDENCE = 65

import performance_report

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
            status TEXT DEFAULT 'Active View',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(news_id) REFERENCES news(id)
        )
    ''')
    try:
        c.execute("ALTER TABLE stock_impact ADD COLUMN confidence_score INTEGER DEFAULT 80")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE stock_impact ADD COLUMN technical_context TEXT DEFAULT ''")
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
    "AIzaSyABS1FGUxLRNcekIfquMcIKcGVjKd-bGq4",
    "AIzaSyCt_GQ1Z39bpkIZMjRZtjmyx-zjxqiFlUw",
    "AIzaSyCUJbHzWvCYzokef_NyXKNWQ6ywniO-wb4",
    "AIzaSyA6En5i8Bpr6_lPKWSMecchwRfHruHw0tU"
]
current_key_idx = 0
client = genai.Client(api_key=API_KEYS[current_key_idx])
MODEL_NAME = 'gemini-2.5-flash'

# Top Tier Indian Financial RSS Feeds (Targeted for Stock-Specific News)
RSS_SOURCES = [
    "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms",   # ET Stocks in News
    "https://economictimes.indiatimes.com/markets/stocks/earnings/rssfeeds/837588974.cms", # ET Earnings
    "https://www.moneycontrol.com/rss/buzzingstocks.xml", # MC Buzzing Stocks
    "https://www.livemint.com/rss/markets"
]

def clean_json(raw_text):
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
    return json.loads(cleaned.strip())

def ai_news_worker():
    global LIVE_NEWS_CACHE, current_key_idx, client, MODEL_NAME
    print("🚀 Alpha Lens v2.0 Background Engine Started. Fetching LiveMint, ET & MoneyControl...")
    print(f"   Settings: Min Confidence={MIN_CONFIDENCE} | Technical Confirmation ON")
    
    while True:
        raw_articles = []
        for url in RSS_SOURCES:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:15]:
                    raw_articles.append({
                        "headline": entry.title,
                        "time": entry.published if hasattr(entry, 'published') else "Just Now"
                    })
            except Exception as e:
                print(f"RSS Error on {url}: {e}")
        
        if raw_articles:
            print(f"📡 Scraped {len(raw_articles)} headlines. Analyzing with Gemini + Technical Confirmation...")

        # Get overall market regime for context
        market_regime = get_market_regime()
        
        analyzed_news = []
        for article in raw_articles:
            headline = article['headline']
            
            # --- 1. EARLY EXIT: Check if headline is already processed ---
            conn = connect_news_db()
            c = conn.cursor()
            c.execute("SELECT id FROM news WHERE headline = ?", (headline,))
            if c.fetchone():
                conn.close()
                continue
            conn.close()
            
            prompt = f"""
            You are an elite quantitative portfolio manager at a top-tier Indian hedge fund.
            Identify high-impact news and categorize it accurately.
            
            Current Market Regime: {market_regime}

            Headline: '{headline}'

            STRICT CATEGORIZATION RULES (Choose ONE):
            - "Finance": Stock market trends, RBI policy, banking, macroeconomics, Sensex/Nifty, currency.
            - "Business": Company specific news (mergers, earnings, IPOs, management), startup news, industries.
            - "Technology": Tech launches, AI, gadgets, software.
            - "Politics": Government decisions, elections, policy shifts (not purely economic).
            - "World": Global events, international politics, wars.
            - "General": Miscellaneous news ONLY if none of the above fit. DO NOT use this for market/corporate news.

            CRITICAL HIGH-WIN-RATE RULES:
            1. If garbage/ad, set "ignore" to true.
            2. If the news is ambiguous, already priced in, or routine — DO NOT identify stocks. Leave "affected_stocks" as [].
            3. Identify stocks when there is a highly probable directional edge from this news.
            4. Maximum 1-3 stocks per news item — pick the best candidates.
            5. 'impact': BULLISH or BEARISH only. NO SLIGHTLY variants — commit to a clear direction or skip.
            6. 'view': High Conviction (confidence 80+) or Moderate Conviction (confidence 65-79).
            7. 'confidence_score': Be realistic. 85+ for crystal-clear catalysts. 65-84 for strong probable signals. Below 65 means you should NOT be recommending this stock.
            8. Think 2nd order: Crude crash → Short ONGC, Buy Paints. Rate hike → Short realty, Buy banks.
            9. Consider if the move is "buy the rumour, sell the news".
            10. **ONLY IDENTIFY INDIAN STOCKS LISTED ON THE NSE.** You MUST append '.NS' to the ticker symbol. Do NOT recommend foreign stocks like US tech companies (e.g., AVGO, AAPL).

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
                    "impact": "BULLISH",
                    "estimated_change_percent": 2.5,
                    "view": "High Conviction",
                    "confidence_score": 85,
                    "reason": "Clear 1-sentence reason"
                }}
              ]
            }}
            """
            
            success = False
            retries = 0
            while not success and retries < len(API_KEYS):
                try:
                    resp = client.models.generate_content(
                        model=MODEL_NAME, 
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json"
                        )
                    )
                    analysis = clean_json(resp.text)
                    
                    conn = connect_news_db()
                    c = conn.cursor()
                    
                    # Insert headline into DB regardless of result so we don't query Gemini again
                    c.execute('''
                        INSERT INTO news (headline, news_time, aam_janta_translation, macro_pathway, category)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (headline, article['time'], analysis.get('aam_janta_translation', ''), json.dumps(analysis.get('macro_pathway', [])), analysis.get('category', 'General')))
                    news_id = c.lastrowid
                    
                    if analysis.get("ignore", False):
                        print(f"   🚮 Ignored {headline[:40]}... (Classified as garbage/no-edge)")
                    else:
                        filtered_stocks = []
                        for stock in analysis.get('affected_stocks', []):
                            conf = stock.get('confidence_score', 50)
                            ticker_name = stock.get('ticker', 'UNKNOWN')
                            if not ticker_name.endswith('.NS') and not ticker_name.endswith('.BO'):
                                ticker_name += '.NS'
                                stock['ticker'] = ticker_name

                            if conf >= MIN_CONFIDENCE:
                                filtered_stocks.append(stock)
                            else:
                                print(f"   🔴 Filtered out {ticker_name} — confidence {conf} < {MIN_CONFIDENCE}")
                        
                        if len(filtered_stocks) == 0:
                            print(f"   ⏭️ Skipped {headline[:40]}... (No high-conviction Indian stocks found)")
                        else:
                            saved_count = 0
                            for stock in filtered_stocks:
                                ticker = stock.get('ticker')
                                base_price = 0.0
                                tech_context_str = ""
                                passed_filters = True
                                
                                try:
                                    tick_data = yf.Ticker(ticker)
                                    base_price = tick_data.fast_info.last_price
                                    
                                    tech_data = get_stock_technical_context(ticker)
                                    if tech_data:
                                        tech_context_str = json.dumps(tech_data)
                                        impact_lower = stock.get('impact', '').lower()
                                        range_pos = tech_data.get('range_position_52w', 0.5)
                                        above_sma20 = tech_data.get('above_sma20')
                                        
                                        if 'bull' in impact_lower:
                                            if above_sma20 is False:
                                                print(f"   🔴 Trend Blocker rejected {ticker}: BELOW SMA20")
                                                passed_filters = False
                                            elif market_regime == "RISK_OFF":
                                                print(f"   🔴 Regime Blocker rejected {ticker}: RISK_OFF market")
                                                passed_filters = False
                                            elif range_pos > 0.85:
                                                print(f"   🔴 Exhaustion Blocker rejected {ticker}: Range {range_pos}")
                                                passed_filters = False
                                                
                                        if 'bear' in impact_lower:
                                            if above_sma20 is True:
                                                print(f"   🔴 Trend Blocker rejected {ticker}: ABOVE SMA20")
                                                passed_filters = False
                                            elif market_regime == "RISK_ON":
                                                print(f"   🔴 Regime Blocker rejected {ticker}: RISK_ON market")
                                                passed_filters = False
                                            elif range_pos < 0.15:
                                                print(f"   🔴 Exhaustion Blocker rejected {ticker}: Range {range_pos}")
                                                passed_filters = False
                                except:
                                    base_price = 100.0
                                
                                if passed_filters:
                                    c.execute('''
                                        INSERT INTO stock_impact (news_id, ticker, impact, estimated_change_percent, view, reason, base_price, current_price, confidence_score, technical_context)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    ''', (news_id, ticker, stock.get('impact'), stock.get('estimated_change_percent'), stock.get('view'), stock.get('reason'), base_price, base_price, stock.get('confidence_score', 80), tech_context_str))
                                    saved_count += 1
                                    
                            if saved_count > 0:
                                print(f"   ✅ AI Found Alpha & Saved to DB: {headline[:40]}... ({saved_count} stocks passed strict filters)")
                            else:
                                print(f"   🛡️ All {len(filtered_stocks)} candidates for {headline[:30]} blocked by V2 Tech Guards!")
                    
                    conn.commit()
                    conn.close()
                    success = True
                except Exception as e:
                    error_msg = str(e).lower()
                    if "429" in error_msg or "quota" in error_msg:
                        print(f"   ⚠️ API Quota Reached on Key Index {current_key_idx}. Swapping keys...")
                        current_key_idx = (current_key_idx + 1) % len(API_KEYS)
                        client = genai.Client(api_key=API_KEYS[current_key_idx])
                        time.sleep(2)
                        retries += 1
                    else:
                        print(f"   🔴 Unknown API Error: {str(e)[:100]}")
                        break
            if not success:
                print(f"   ❌ Failed to analyze article after {retries} retries: {headline[:40]}...")
            
            time.sleep(3)
            
        # Clean up old news (older than 4 days)
        try:
            conn = connect_news_db()
            c = conn.cursor()
            four_days_ago = (datetime.now(timezone.utc) - timedelta(days=4)).strftime('%Y-%m-%d %H:%M:%S')
            c.execute("DELETE FROM stock_impact WHERE news_id IN (SELECT id FROM news WHERE created_at < ?)", (four_days_ago,))
            c.execute("DELETE FROM news WHERE created_at < ?", (four_days_ago,))
            conn.commit()
            conn.close()
        except Exception as e:
            print("DB Cleanup Error:", e)
            
        # Show latest performance only AFTER the batch of news has been completely analyzed
        try:
            import performance_report
            print("\n" + "="*60)
            print(" 🔄 END OF BATCH ANALYSIS — LATEST RESULTS:")
            print("="*60)
            performance_report.run_performance_check()
        except Exception as e:
            print("Performance Report Error:", e)
            
        time.sleep(600)

def yfinance_worker():
    print("YFinance Live Price Engine v2.1 Started. Asymmetric Thresholds + Time Expiry Active...")
    while True:
        try:
            conn = connect_news_db()
            c = conn.cursor()
            # Fetch active views from last 3 days
            three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')
            c.execute("SELECT id, ticker, base_price, impact, created_at FROM stock_impact WHERE status = 'Active View' AND created_at > ?", (three_days_ago,))
            active_stocks = c.fetchall()
            
            for row in active_stocks:
                stock_id, ticker, base_price, impact, created_at_str = row
                try:
                    tick_data = yf.Ticker(ticker)
                    current_price = tick_data.fast_info.last_price
                    
                    diff_percent = ((current_price - base_price) / base_price) * 100
                    
                    new_status = 'Active View'
                    impact_lower = impact.lower()
                    is_bullish = 'bullish' in impact_lower
                    
                    # ASYMMETRIC thresholds: 1.5% target, 3% stop (wide stop = breathing room)
                    target_pct = 1.5
                    stop_pct = 3.0
                    
                    if is_bullish:
                        if diff_percent >= target_pct:
                            new_status = 'Predicted Target Hit'
                        elif diff_percent <= -stop_pct:
                            new_status = 'Reacted Against Prediction'
                    else: # bearish
                        if diff_percent <= -target_pct:
                            new_status = 'Predicted Target Hit'
                        elif diff_percent >= stop_pct:
                            new_status = 'Reacted Against Prediction'
                    
                    # TIME-BASED EXPIRY: If trade hasn't resolved in 3 days, expire it
                    if new_status == 'Active View':
                        try:
                            created_dt = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S')
                            age_hours = (datetime.now(timezone.utc).replace(tzinfo=None) - created_dt).total_seconds() / 3600
                            if age_hours >= 72:  # 3 days
                                new_status = 'Expired'
                        except:
                            pass
                            
                    c.execute("UPDATE stock_impact SET current_price = ?, status = ? WHERE id = ?", (current_price, new_status, stock_id))
                except Exception as e:
                    pass
                
            conn.commit()
            conn.close()
        except Exception as e:
            print("YFinance Worker Error:", e)
            
        time.sleep(60)

# Threading starts moved to main block to prevent Flask reloader duplicate race conditions.

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
    # Start background threads
    engine_thread = threading.Thread(target=ai_news_worker, daemon=True)
    engine_thread.start()

    yf_thread = threading.Thread(target=yfinance_worker, daemon=True)
    yf_thread.start()

    # Threaded=True allows the background AI loop to run alongside the website
    # use_reloader=False prevents double execution of our background threads on restart
    app.run(debug=True, port=5000, threaded=True, use_reloader=False)