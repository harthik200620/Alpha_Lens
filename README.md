# Alpha Lens 📈

> An AI-powered Indian stock market intelligence platform that analyzes live financial news and provides quantitative trading insights using Google Gemini.

---

## What is Alpha Lens?

Alpha Lens is a real-time quant research web application built for the Indian equity market (NSE/BSE). It fetches live business news, runs it through an AI model trained to think like a Tier-1 quantitative portfolio manager, and delivers structured trade signals — complete with affected tickers, directional bias, and reasoning. It also includes a full backtesting engine to validate how well those AI signals performed historically.

---

## Features

- **Live Market Ticker** — Real-time prices and % changes for NIFTY 50, SENSEX, BANK NIFTY, and MIDCAP NIFTY via yfinance.
- **AI News Analysis** — Fetches top Indian business headlines and sends them through Google Gemini for elite quantitative analysis: affected stocks, impact direction, reasoning, and risk factors.
- **10-Second Price Re-evaluation Loop** — Automatically tracks whether the market has reacted to news in the predicted direction.
- **Backtesting Engine** — Runs bulk historical analysis on a CSV of past news headlines and measures how often the AI signals hit a 1% profit target vs. a 2% stop-loss, with multi-API key rotation for high throughput.
- **OTP Authentication** — Secure email-based passwordless login using SendGrid.
- **Google OAuth Sign-In** — One-click authentication via Google accounts.
- **SQLite User Database** — Lightweight persistent user management with session support.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask |
| AI Model | Google Gemini 1.5 Flash / 2.5 Flash |
| Market Data | yfinance |
| News Feed | NewsAPI |
| Auth & Email | SendGrid, Google OAuth |
| Database | SQLite3 |
| Frontend | HTML, CSS, JavaScript |

---

## Project Structure

```
Alpha_Lens/
├── app.py              # Main Flask server — routes, live data, AI analysis
├── backtest.py         # Bulk backtesting engine with multi-key API rotation
├── database.py         # User auth: OTP login, Google OAuth, session management
├── index.html          # Main frontend dashboard
├── index-2.html        # Secondary frontend page
├── alphalens7.html     # Additional UI page
├── view_users.py       # Utility script to inspect the user database
├── news_dataset.csv    # Historical news dataset used for backtesting
└── users.db            # SQLite user database
```

---

## Getting Started

### Prerequisites

- Python 3.9+
- A [NewsAPI](https://newsapi.org/) key
- A [Google Gemini](https://ai.google.dev/) API key
- A [SendGrid](https://sendgrid.com/) API key with a verified sender identity

### Installation

```bash
git clone https://github.com/KIRITO-899/Alpha_Lens.git
cd Alpha_Lens
pip install flask requests google-generativeai yfinance pytz sendgrid werkzeug
```

### Configuration

Open `app.py` and replace the placeholder API keys with your own:

```python
NEWS_API_KEY = "your_newsapi_key"
GEMINI_API_KEY = "your_gemini_api_key"
```

Open `database.py` and replace:

```python
SENDGRID_API_KEY = 'your_sendgrid_api_key'
# Also update the from_email to your SendGrid verified sender
```

### Running the App

```bash
python app.py
```

Then open your browser and go to `http://127.0.0.1:5000`.

---

## Running the Backtester

The backtesting engine reads from `news_dataset.csv` (columns: `Datetime`, `Headline`) and evaluates AI predictions against real historical price data.

```bash
python backtest.py
```

Sample output:

```
==================================================
 FINAL HIGH-CONVICTION STATISTICS REPORT
==================================================
Total News Articles Processed: 120
Total Predictions Triggered:   47
TARGET HIT (Wins):          31
STOP HIT (Losses):          11
STILL RUNNING:               5
AI STRATEGY WIN RATE: 73.8%
==================================================
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Serves the main dashboard |
| GET | `/api/indices` | Live NIFTY/SENSEX price data |
| GET | `/api/news` | Fetch and analyze latest news with AI |
| GET | `/api/market_update` | Re-evaluate prices for existing analysis |
| POST | `/api/send-otp` | Send OTP email to user |
| POST | `/api/verify-otp` | Verify OTP and create/login user |
| POST | `/api/oauth-signin` | Sign in via Google OAuth |
| GET | `/api/me` | Get current session user |
| POST | `/api/logout` | Log out |

---

## Security Notice

The current codebase contains API keys directly in source files. Before any public deployment, move all secrets to environment variables or a `.env` file and add `.env` to `.gitignore`.

```bash
export NEWS_API_KEY="your_key"
export GEMINI_API_KEY="your_key"
export SENDGRID_API_KEY="your_key"
```

---

## Notes

- **Market Hours**: yfinance returns the last available closing price outside of NSE/BSE trading hours (9:15 AM – 3:30 PM IST).
- **NewsAPI Free Tier**: Free-tier accounts have limited request rates and may return cached or slightly delayed headlines.
- **Backtesting Trade Rules**: 1% profit target, 2% stop-loss, evaluated at T+24h and T+48h after news publication.

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

---

## Contributors

- [KIRITO-899](https://github.com/KIRITO-899) — H Y Yeshwanth Kumar
- [Sumant-varanasi](https://github.com/Sumant-varanasi)
