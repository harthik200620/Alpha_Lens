from flask import Flask, render_template, jsonify
import requests
import google.generativeai as genai
import json

app = Flask(__name__, template_folder='.')

# --- API CONFIGURATION ---
NEWS_API_KEY = "86e94c83a01c4953bc6b9cccb33f1154"
GEMINI_API_KEY = "AIzaSyBvP8naGuO9R4FTJ0WPuodWoBs-dFF7XM0"

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

def fetch_and_analyze():
    # 1. Fetch Live Indian News (With dynamic fallback to ensure data)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
    primary_url = f"https://newsapi.org/v2/top-headlines?country=in&category=business&apiKey={NEWS_API_KEY}"
    fallback_url = f"https://newsapi.org/v2/everything?q=(India AND (business OR NSE OR BSE))&sortBy=publishedAt&language=en&apiKey={NEWS_API_KEY}"
    
    try:
        response = requests.get(primary_url, headers=headers)
        articles = response.json().get('articles', [])
        
        if not articles or response.status_code != 200:
            print("Fallback triggered: Fetching latest India business news...")
            response = requests.get(fallback_url, headers=headers)
            articles = response.json().get('articles', [])

        if not articles:
            return [{"error": "No news found currently."}]
            
        results = []
        for article in articles[:3]: # Process top 3 news items
            news_text = f"{article.get('title')}. {article.get('description', '')}"
            
            # 2. Force the AI to output exact JSON for our UI
            prompt = f"""
            Analyze this Indian market news: '{news_text}'
            Output STRICTLY as JSON:
            {{
              "headline": "Short punchy summary of the event",
              "aam_janta_translation": "1 sentence explaining everyday life impact",
              "macro_pathway": ["Trigger", "Immediate Hit", "Ripple", "Macro Result"],
              "affected_stocks": [
                {{
                    "ticker": "TICKER.NS",
                    "impact": "bullish" | "slightly bullish" | "bearish" | "slightly bearish",
                    "view": "short-term now" | "long-term" | "already reacted",
                    "estimated_change_percent": 1.5,
                    "reason": "Brief reason why"
                }}
              ]
            }}
            """
            try:
                ai_resp = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
                analysis = json.loads(ai_resp.text)
                results.append(analysis)
            except Exception as e:
                print("AI Parse Error:", e)
                
        if not results:
            return [{"error": "AI error or rate limit exceeded. Please wait a minute and reload."}]
            
        return results
    except Exception as e:
        return [{"error": str(e)}]

# Serve the HTML frontend
@app.route('/')
def home():
    return render_template('index.html')

# API endpoint for the frontend JavaScript to call
@app.route('/api/news')
def get_news():
    data = fetch_and_analyze()
    return jsonify(data)

if __name__ == '__main__':
    print("🚀 Starting IN-SIGHT Local Server on http://127.0.0.1:5000")
    app.run(debug=True)