# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Maintenance Policy

**CRITICAL: Update this file whenever features are added.** This file is the primary knowledge source for future Claude instances. Keep it synchronized with the codebase.

### What to Document

After adding or modifying features, update CLAUDE.md if the change affects:

- **New commands or workflows** — Common tasks or commands users should know
- **Architecture or system design** — Changes to how systems interact (backend threads, databases, APIs)
- **New backend modules or APIs** — New Python files, Flask endpoints, or utility modules
- **Configuration or setup steps** — New environment variables, setup requirements, build steps
- **Dependencies** — New packages in requirements.txt, version changes that affect usage
- **Project structure** — New directories, file organization changes, or critical file locations

### Automatic Reminder Hook

A hook in `.claude/settings.json` (PostToolUse on Write|Edit) will remind you to update this file whenever you modify files in the project. **Heed the reminder** — it catches cases where documentation gets out of sync.

### How to Update

1. **Be specific** — Don't just list changes; explain the "why" and "how"
2. **Keep it concise** — Use tables, bullet points, and clear sections
3. **Stay accurate** — Stale documentation is worse than no documentation
4. **Cross-reference** — Link to critical files or commands mentioned
5. **Test your changes** — Verify instructions work before documenting them

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
├── .mcp.json                    # Context7 MCP server + inline key (gitignored, local-only)
├── .claude/
│   ├── settings.json            # Shared Claude Code config (hooks, team permissions)
│   └── settings.local.json      # Personal config + CONTEXT7_API_KEY (gitignored)
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

## Development Workflow

When you add or modify features in Alpha_Lens, follow this workflow:

### 1. Implement the Feature
- Write code, add files, modify backend/frontend
- Test locally to ensure it works

### 2. Update CLAUDE.md (Before Committing)
Claude Code will remind you after file modifications. **Do not skip this step.**

Update one of these sections based on what changed:

| Section | When to Update |
|---------|---|
| **Quick start** | Changed how to run the app or added new startup requirements |
| **Common commands** | Added new utility scripts or management commands |
| **Project structure** | Reorganized directories or added new modules |
| **Architecture** | Changed how components interact (backend threads, data flow) |
| **Key modules** | Added new `.py` files in backend, changed existing module responsibilities |
| **Environment variables** | Added new required `.env` variables |

Example update for a new backend module:
```markdown
| `new_feature.py` | New module for X functionality — imported by `app.py` |
```

### 3. Commit Together
```bash
git add CLAUDE.md <your-changed-files>
git commit -m "Add feature X and document in CLAUDE.md"
```

### Development Notes

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

### How it's wired

The MCP server is registered in **`.mcp.json`** — a remote HTTP server pointing at
`https://mcp.context7.com/mcp`, with the `CONTEXT7_API_KEY` **inline** in the auth
header. `.mcp.json` is **gitignored** so the key never reaches git.

| File | Role | Committed? |
|------|------|-----------|
| `.mcp.json` | Server registration with the real key inline in the header | ❌ No (gitignored — holds the secret) |
| `.claude/settings.local.json` | `enabledMcpjsonServers: ["context7"]` to trust the server (also keeps a copy of the key in `env`) | ❌ No (gitignored) |

> **Why inline instead of `${CONTEXT7_API_KEY}` expansion?** Claude Code did not
> reliably expand the `${...}` placeholder from the settings `env` block into the
> `.mcp.json` header, so the handshake sent an empty key and failed. Hardcoding the
> key in the gitignored `.mcp.json` removes that failure point entirely.

### Setup (one-time, per machine)

1. **Get a free API key** at [context7.com/dashboard](https://context7.com/dashboard)
   (format: `ctx7sk-…`).
2. **Put it inline** in `.mcp.json` → `mcpServers.context7.headers.CONTEXT7_API_KEY`.
   The file is gitignored, so the key stays out of git.
3. **Fully quit and reopen Claude Code** so it loads `.mcp.json`. Verify with `/mcp` —
   `context7` should show **connected**, exposing `resolve-library-id` and `query-docs`.

> Note: `CONTEXT7_API_KEY` in the project `.env` or on Render only powers the Flask
> app — it does **not** feed Claude Code's MCP connection. The key must be in
> `.mcp.json` for the MCP to authenticate.

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
