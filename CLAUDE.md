# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git push target

Always push to the `harthik` remote (`github.com/harthik200620/Alpha_Lens.git`), NOT `origin` (KIRITO-899). The `main` branch is already configured to track `harthik/main`, so a plain `git push` will go to the right place — do not pass `origin` explicitly.

## Quick start

**Backend (Flask):** `C:/Project rohan/Alpha_Lens/.alpha-venv/Scripts/python.exe backend/app.py` — serves on port 5000

**Frontend:** Single-file HTML (`frontend/index.html`) + vanilla JS (`frontend/app.js`, `frontend/stocks.js`). No build step. Flask serves these from `static_folder='../frontend'`.

Open `http://127.0.0.1:5000` in your browser.

## Common commands

### Running the main app
```bash
python backend/app.py
```
Starts Flask server (port 5000) + two background worker threads:
- **AI News Engine**: Continuously scrapes RSS feeds, analyzes headlines via Gemini, runs multi-model ensemble, applies technical confirmation, stores in `news_cache.db`
- **yfinance Price Worker**: Monitors active positions every 10s, checks if targets/stop-losses are hit

### Running workers only (no web UI)
```bash
python backend/app.py --workers-only
```
Useful for background data collection on a headless machine.

### Backtesting historical signals
```bash
python backend/backtest.py
```
Replays headlines from `news_dataset.csv` against historical candle data. Evaluates at T+24h and T+48h with +1.5% target, -3.0% stop-loss. Outputs win/loss stats.

### Backfill existing headlines through ensemble
```bash
python backend/backfill_stocks.py
```
Reprocesses all headlines already in `news_cache.db` through the updated ensemble engine, regenerating `stock_impact` entries. Run once after upgrades to v4.0+.

### Performance reporting
```bash
python backend/performance_report.py
```
Reads `news_cache.db` and generates terminal-based stats: total articles, unique signals, breakdown by trade status (Active/Hit Target/Stopped Out/Expired), win rate, avg confidence.

### Installing dependencies
```bash
pip install -r requirements.txt
```
Installs Flask, google-genai, yfinance, sendgrid, feedparser, etc.

## Project structure

```
Alpha_Lens/
├── backend/
│   ├── app.py                   # Flask server + AI news engine + yfinance worker
│   ├── prediction_models.py     # Multi-model ensemble (Sentiment, Historical Similarity, Sector Momentum, Event Pattern)
│   ├── technical_analysis.py    # RSI, SMA, Bollinger Bands, market regime detection
│   ├── backtest.py              # Historical backtesting harness
│   ├── backfill_stocks.py       # Regenerates stock_impact via ensemble for existing headlines
│   ├── performance_report.py    # Win rate, confidence stats, trade status breakdown
│   ├── database.py              # OTP auth, OAuth, session management (SQLite)
│   ├── news_cache.db            # SQLite: headlines, AI analysis, stock impacts
│   ├── users.db                 # SQLite: user accounts, sessions
│   ├── angelone_shim.py         # yfinance wrapper (Angel One integration)
│   └── [other utility modules]
├── frontend/
│   ├── index.html               # Main dashboard (stocks ticker, news cards, signals)
│   ├── app.js                   # Frontend logic, API calls
│   ├── stocks.js                # NSE/BSE ticker lookup (~2150 entries)
│   └── styles.css               # Dashboard styling
├── scratch/                     # Dev utilities (diagnostics, one-off scripts)
├── .env                         # API keys (Gemini, SendGrid, Google OAuth)
├── requirements.txt             # Python dependencies
└── README.md                    # Full feature docs
```

## Architecture

**Frontend → Flask backend → AI engine + Workers → SQLite + yfinance**

1. **Flask server** (`app.py`): Routes, static file serving, REST APIs
2. **AI News Engine** (background thread): Fetches RSS (Economic Times, MoneyControl, LiveMint) → analyzes with Gemini → runs through 5-model ensemble → applies technical filters → stores in DB
3. **Multi-model Ensemble** (`prediction_models.py`): 
   - SentimentDepthModel — keyword strength, negation, sentiment intensity
   - HistoricalSimilarityModel — pattern matching against past headlines
   - SectorMomentumModel — sector-level momentum eval
   - EventPatternModel — earnings, mergers, regulatory events
   - EnsemblePredictor — aggregates scores, applies dual gate: **score ≥70 AND 3+ models agree**
4. **Technical Confirmation** (`technical_analysis.py`): RSI (14-period), SMA (20/50), Bollinger Bands, volume trends, market regime
5. **yfinance Worker** (background thread): Monitors open positions, resolves trades vs target/stop-loss every 10s
6. **SQLite DBs**: `news_cache.db` (headlines, signals), `users.db` (accounts, sessions)

## Key modules

| File | Purpose |
|------|---------|
| `app.py` | Flask routes, API endpoints, RSS fetch loop, AI analysis dispatch, background threads |
| `prediction_models.py` | 5-model ensemble predictor — sentiment, historical, sector, event, aggregation |
| `technical_analysis.py` | RSI, SMA, Bollinger Bands, volume analysis, market regime detection |
| `backtest.py` | Bulk historical replay — news vs candle data, win/loss stats |
| `backfill_stocks.py` | Regenerate stock_impact for all existing headlines via ensemble |
| `performance_report.py` | Terminal-based performance stats |
| `database.py` | SQLite user auth, OTP, OAuth 2.0, session management |

## Environment variables

Create a `.env` file in the project root:

```bash
GEMINI_API_KEY_1=<your_key>
GEMINI_API_KEY_2=<your_key>
...
SENDGRID_API_KEY=<your_key>
SENDGRID_FROM_EMAIL=<verified_sender>
GOOGLE_OAUTH_CLIENT_ID=<your_client_id>
FLASK_SECRET_KEY=<random_secret>
GEMINI_MODEL=gemini-2.5-flash
```

The backend rotates through multiple Gemini keys to avoid rate limits.

## Development notes

- **Frontend**: No build step. Edit `frontend/index.html`, `frontend/app.js`, `frontend/styles.css` directly. Flask serves via `static_folder`. Browser refresh fetches latest.
- **Backend**: Reload Flask dev server to pick up Python changes (`CTRL+C`, restart `python backend/app.py`).
- **Database**: SQLite files (`news_cache.db`, `users.db`) are created on first run. Delete to reset.
- **API keys**: Always use environment variables (`.env`). Never hardcode in source.
- **Background threads**: Both the AI news engine and yfinance worker start automatically with the Flask app (unless `--workers-only` mode).
- **Market hours**: yfinance returns last available price outside NSE/BSE hours (9:15 AM – 3:30 PM IST). Live signals are most accurate during market hours.
- **Fuzzy dedup**: Incoming headlines are compared (75% similarity threshold) against the 50 most recent entries to prevent near-duplicate signals.

## Context7 MCP — Library Documentation

Alpha_Lens now includes **Context7 MCP**, which provides real-time, version-specific documentation for all project dependencies. This extends Claude Code with up-to-date docs for:

- Flask, Flask-Compress, Werkzeug
- Google Gemini API (google-genai)
- yfinance (NSE/BSE data)
- SendGrid (email/OTP)
- feedparser (RSS feeds)
- BeautifulSoup4 (HTML parsing)
- pandas, numpy
- And all other Python dependencies

### Setup

1. **Get a free API key** at [context7.com/dashboard](https://context7.com/dashboard)
2. **Set environment variable** in your Claude Code environment:
   ```bash
   CONTEXT7_API_KEY=your_api_key_here
   ```
   Or add to `.env`:
   ```
   CONTEXT7_API_KEY=your_api_key
   ```
3. **Tools are automatically available** — Context7 is configured in `.claude/settings.local.json`

### Usage in Claude Code

When asking about library usage, say:
- "Look up Flask request documentation" → Context7 resolves to Flask library, returns docs
- "Query yfinance API for historical data" → Context7 provides version-specific yfinance docs
- "Show me Gemini API documentation" → Context7 returns google-genai docs with examples

The MCP provides:
- **resolve-library-id**: Convert library names (e.g., "Flask") to Context7 IDs
- **query-docs**: Get specific documentation by library ID and query term

### Example Workflow

```
You: "How do I send an email with SendGrid?"
Claude: [Uses Context7 to fetch SendGrid docs]
Claude: "Here's the SendGrid API for sending emails..."
```

Docs are always up-to-date with the latest library versions — no hallucinated APIs or deprecated functions.

## Deployment (Render)

The `render.yaml` file configures a Render web service:
- **Runtime**: Python
- **Build**: `pip install -r requirements.txt`
- **Start**: Gunicorn (1 worker, 4 threads) on port $PORT
- **Region**: Singapore
- **Plan**: Free tier (512 MB RAM)

Database: PostgreSQL (optional, configured via DATABASE_URL env var).
