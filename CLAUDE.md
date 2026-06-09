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

A hook in `.claude/settings.json` (PostToolUse on `Bash`) emits a CLAUDE.md-update reminder **after every `git commit`** (filtered by `.claude/hooks/post_commit_reminder.py` — silent on `--dry-run` and on every non-commit Bash call). It used to fire on every Write/Edit, which got noisy during multi-file changes; the post-commit timing means you're reminded once, when a commit has actually landed and the change is concrete enough to document. **Heed the reminder** if the commit affected commands, architecture, backend modules/APIs, configuration, dependencies, or project structure.

### How to Update

1. **Be specific** — Don't just list changes; explain the "why" and "how"
2. **Keep it concise** — Use tables, bullet points, and clear sections
3. **Stay accurate** — Stale documentation is worse than no documentation
4. **Cross-reference** — Link to critical files or commands mentioned
5. **Test your changes** — Verify instructions work before documenting them

## Git push target

Always push to the `harthik` remote (`github.com/harthik200620/Alpha_Lens.git`), NOT `origin` (KIRITO-899). The `main` branch is already configured to track `harthik/main`, so a plain `git push` will go to the right place — do not pass `origin` explicitly.

## ⚠️ Do NOT start the Flask server without asking

**Never run `python backend/app.py` (or otherwise boot the server) without explicit
user confirmation first.** The user runs it themselves and starting it can consume
Gemini API keys. Background workers are deliberately **paused** for key-saving via
`.env` (`ALPHA_LENS_SKIP_WORKERS=1`, `ALPHA_LENS_SKIP_AUTO_REPAIR=1`) — do not start
workers or remove those flags either. To verify code changes without booting the
app/workers, use the import check (`ALPHA_LENS_SKIP_AUTO_BOOTSTRAP=1 python -c "import app; ..."`)
and the unit tests — see the verification one-liner under Development Notes. If a
running server is genuinely needed, **ask the user to start it** (or ask permission
first), and stop any server you were told to start when done.

## Quick start

**Backend (Flask):** `C:/Project rohan/Alpha_Lens/.alpha-venv/Scripts/python.exe backend/app.py` — serves on port 5000

**Frontend:** Single-file HTML (`frontend/index.html`) + vanilla JS. No deploy build step (Tailwind is precompiled offline to `frontend/tailwind.built.css` — see the ⚠️ note under Development Notes; the old `cdn.tailwindcss.com` Play-CDN was removed for first-paint speed). The old monolithic `app.js` was split into **12 ordered chunks** (`app-core.js` → `app-filings.js`, see below — `app-fno.js` is the F&O Smart-Money view, `app-earnings.js` is the Earnings & Results Intelligence view, `app-filings.js` is the Exchange Filing Alerts view) plus `frontend/stocks.js`. Flask serves these from `static_folder='../frontend'`.

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

### Backfill pending headlines through the ensemble
Backfill is exposed as a **one-time, manual admin API endpoint** (not a standalone
script — the old `backfill_stocks.py` no longer exists). It runs the same prediction
pipeline over headlines with `ai_status='pending'`:
```bash
curl -X POST "http://127.0.0.1:5000/api/admin/backfill-pending-predictions" \
  -H "X-Alpha-Lens-Token: <admin_token>" -H "Content-Type: application/json" \
  -d '{"limit": 64}'
```
Implemented by `_run_backfill_pending()` / `backfill_pending_predictions()` in `app.py`.
Poll the same endpoint with `GET` (and the admin token) to watch progress.

### Performance reporting
```bash
python backend/performance_report.py
```
Reads `news_cache.db` and generates terminal-based stats: total articles, unique signals, breakdown by trade status (Active/Hit Target/Stopped Out/Expired), win rate, avg confidence.

### Running the test suite
```bash
cd backend && "../.alpha-venv/Scripts/python.exe" -m unittest discover -s tests
```
Stdlib `unittest` (pytest is **not** in the venv). `backend/tests/` covers the
pure modules extracted from `app.py` during decomposition — `market_calendar`,
`ticker_utils`, `news_rules`, `news_data`, and `earnings_data` (the earnings
math). `tests/__init__.py` puts `backend/` on `sys.path` so the sibling modules
import regardless of CWD. Tests are pure (no network/DB/threads), so they run in
well under a second. **73 tests** as of the Earnings Intelligence feature.

### Installing dependencies
```bash
pip install -r requirements.txt
```
Installs Flask, google-genai, yfinance, sendgrid, feedparser, etc.

## Project structure

```
Alpha_Lens/
├── backend/
│   ├── app.py                   # Flask server + AI news engine + yfinance worker (entrypoint: app:app)
│   ├── persistence/             # ── Subpackage: DB layer ──
│   │   ├── db.py                #   connect/db_write + SQLite↔Postgres wrappers + PG pool
│   │   └── schema.py            #   Schema builders — init_db/init_news_db (depends on db.py)
│   ├── marketdata/              # ── Subpackage: market data ──
│   │   ├── market_calendar.py   #   Pure NSE calendar/market-hours helpers
│   │   ├── macro_tracker.py     #   MacroDataTracker — commodity/FX/rates snapshot + σ (vol-normalized) shock detection
│   │   ├── ticker_utils.py      #   Ticker normalization + news-candidate screening helpers
│   │   ├── oi_data.py           #   F&O bhavcopy fetch+parse (futures+options) + delivery%/bulk-block deals + snapshot persistence (lazy-imported)
│   │   ├── angel_fno.py         #   Angel One INTRADAY F&O source (#5) — live opnInterest → same snapshot shape, EOD fallback (SWR)
│   │   ├── earnings_data.py     #   Pure earnings math — quarter labels, YoY/QoQ, margins, verdict, scorecard
│   │   └── price_resolver.py    #   Pure price rules — select_fresh_close (kills stale-close bug) + atr_stop_target (ATR/2, ATR)
│   ├── newsproc/                # ── Subpackage: news processing (pure) ──
│   │   ├── news_rules.py        #   Rule-based news classification + STOCK_KEYWORD_MAP
│   │   ├── news_data.py         #   Static data tables (MACRO_IMPACT_MAP, keyword lists, ticker sets)
│   │   ├── calendar_seed.py     #   Macro/economic-events calendar seed (CALENDAR_EVENTS_SEED)
│   │   ├── portfolio_data.py    #   Portfolio-assistant ticker-detection lookup tables
│   │   └── filing_classifier.py #   Pure exchange-filing → plain-English alert classifier (9 event types)
│   ├── signals/                 # ── Subpackage: signal generation ──
│   │   ├── prediction_models.py #   Multi-model ensemble (Sentiment, Historical, Sector, Event)
│   │   ├── technical_analysis.py#   RSI, SMA, Bollinger Bands, market regime detection
│   │   ├── calibration.py       #   Score→P(win) calibration map + meta-label gate (levers #1/#4)
│   │   ├── calibration_map.json #   Isotonic score→P(win) map (refreshable; built by scratch/ pipeline)
│   │   ├── ripple_engine.py     #   Ripple 2.0 — pure deterministic 5-dimension macro cascade (beta-based)
│   │   ├── fno_engine.py        #   F&O Smart-Money board — pure OI-buildup/PCR/max-pain/IV/FII/sector analytics
│   │   ├── options_math.py      #   Black-76 implied vol + Greeks (pure; feeds the option chain)
│   │   └── nifty_outlook.py     #   Nifty Next-Session Outlook — pure macro-cue → NIFTY bias model
│   ├── tests/                   # stdlib unittest suite for the pure subpackage modules
│   ├── backtest.py              # Historical backtesting harness (⚠ stale: uses .history(start=) the shim dropped)
│   ├── eval_loop.py             # Forward shadow-ledger — logs every signal decision + ATR outcomes (append-only)
│   ├── performance_report.py    # Win rate, confidence stats, trade status breakdown
│   ├── database.py              # OTP auth, OAuth, session management (SQLite; currently unimported)
│   ├── news_cache.db            # SQLite: headlines, AI analysis, stock impacts
│   ├── users.db                 # SQLite: user accounts, sessions
│   ├── angelone_shim.py         # yfinance-compatible shim (Angel One data, imported as `yf`)
│   ├── yfinance_twelvedata_shim.py  # Alt yfinance-compatible shim (Twelve Data)
│   ├── whatsapp_sender.py       # WhatsApp alert sender (lazy-imported by app.py)
│   └── [serve_app.py, _diag.py, win_rate_check.py — dev/utility scripts]
├── frontend/
│   ├── index.html               # Main dashboard (stocks ticker, news cards, signals)
│   ├── app-core.js              # Globals, Google/OTP auth, tab shell, date utils (chunk 1/12)
│   ├── app-news.js              # fetchLiveNews, dashboard render, badges, hero, archive, Command Center (2/12)
│   ├── app-stocks.js            # Watchlist search, portfolio assistant, Risk Radar (3/12)
│   ├── app-market.js            # Major stocks, indices, smart polling (4/12)
│   ├── app-premium.js           # Animations, cursor trail, parallax, flip, ticker hover (5/12)
│   ├── app-terminal.js          # Stock drawer, signal terminal, backtest, notifications (6/12)
│   ├── app-earnings.js          # Earnings & Results Intelligence tab (7/12)
│   ├── app-ripple.js            # Ripple graph render (8/12)
│   ├── app-macro.js             # Macro Pulse view (9/12)
│   ├── app-fno.js               # F&O Smart-Money board + option-chain modal (10/12)
│   ├── app-calendar.js          # Economic-events calendar (11/12)
│   ├── app-filings.js           # Exchange Filing Alerts feed (12/13)
│   ├── app-glossary.js          # Beginner explain-layer — JARGON map + delegated tooltip for [data-term] (13/13)
│   ├── stocks.js                # NSE/BSE ticker lookup (~2150 entries, lazy-loaded)
│   ├── sw.js                    # PWA service worker (cache-first static, network-first HTML/API)
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
2. **AI News Engine** (background thread): Fetches RSS (68 sources — Economic Times, MoneyControl, LiveMint, Business Standard, CNBC + Google-News **sector / catalyst / regulatory / landmine** queries + **direct RBI/SEBI RSS**) plus **BSE corporate-filing announcements**, **GDELT global news**, and **NewsAPI.ai / Event Registry finance-only news** (`fetch_bse_announcements` / `fetch_gdelt_news` / `fetch_eventregistry_finance_news`) → fuzzy-dedups → analyzes with Gemini → runs through 5-model ensemble → applies technical filters → stores in DB
3. **Multi-model Ensemble** (`signals/prediction_models.py`): 
   - SentimentDepthModel — keyword strength, negation, sentiment intensity
   - HistoricalSimilarityModel — pattern matching against past headlines
   - SectorMomentumModel — sector-level momentum eval
   - EventPatternModel — earnings, mergers, regulatory events
   - EnsemblePredictor — weighted aggregate (AI vote **down-weighted to 0.30**, env-tunable `W_*`), then gate: **score ≥ `MIN_CONFIDENCE` (50) AND ≥3 of 5 models agree AND no technical veto AND (default) the technical model actively confirms the direction** (`REQUIRE_TECH_CONFIRM`). Optional meta-label + regime-hard-block gates (off by default). ⚠️ The old "≥70 AND 3+" is stale.
4. **Technical Confirmation** (`signals/technical_analysis.py`): RSI (14-period), SMA (20/50), Bollinger Bands, volume trends, market regime
5. **yfinance Worker** (background thread): Monitors open positions, resolves trades vs target/stop-loss every 10s
6. **Archival Worker** (`archival_worker`, every 24h): the **sole retention authority** — MOVES news + signals older than `ARCHIVE_AFTER_DAYS` (90) into `*_archive` tables (reversible insert+delete). Nothing is hard-deleted on the hot path.
7. **News Prune Worker** (`news_prune_worker` → `prune_low_value_news`, hourly): bounds the "All News" feed to `NEWS_MAX_ROWS` (800) / `NEWS_MAX_AGE_DAYS` (5) by deleting **signal-less** news. News referenced by a signal is exempt (kept 90 days with the signal).
8. **Calendar Maintenance Worker** (`calendar_worker` → `_calendar_maintenance`, every `CALENDAR_RUN_EVERY_MIN`=30m): keeps the forward economic calendar clean — flips concluded events to `status='released'` and **purges** events older than `CALENDAR_PURGE_AFTER_DAYS` (2). The `/api/calendar` endpoint also hides any event whose IST time has passed on read, so an event drops off the calendar **the moment it's done** regardless of worker timing.
9. **SQLite DBs**: `news_cache.db` (headlines, signals), `users.db` (accounts, sessions). Production uses PostgreSQL via `DATABASE_URL`.

## Key modules

| File | Purpose |
|------|---------|
| `app.py` | Flask routes, API endpoints, RSS fetch loop, AI analysis dispatch, background threads. Imports the subpackages back (e.g. `from persistence.db import …`) so call sites are unchanged |
| `persistence/db.py` | Database layer — `connect_news_db`/`connect_users_db`, `db_write`, the SQLite↔Postgres wrappers + PG pool. **`_APP_DIR` = parent of this file's dir** so DBs resolve to `backend/`, not `backend/persistence/` |
| `persistence/schema.py` | Schema builders — `init_db`/`init_news_db` (table creation + idempotent migrations); imports `from persistence.db import …` |
| `marketdata/market_calendar.py` | Pure NSE calendar helpers — holidays, `is_market_open`, `has_market_traded_since` |
| `marketdata/macro_tracker.py` | `MacroDataTracker` — live commodity/FX/rates snapshot + **volatility-normalized (σ/z-score) shock detection**. Pulls 6mo daily closes → realized vol → `sigma = move/vol`; pure helpers `daily_returns()`/`compute_vol_stats()`/`latest_daily_change()` (unit-tested). ⚠️ `latest_daily_change` computes the **TRUE 1-day change** from the last two daily closes — NOT Yahoo's `chartPreviousClose`, which on the 6mo chart is the close ~6 months ago (that bug made every "1d" move a 6-MONTH move → Nifty -10.77%, USD/INR +5.68%, and inflated every σ so the whole board flagged MAJOR) |
| `marketdata/ticker_utils.py` | Ticker normalization + news-candidate screening — `normalize_ticker`, `candidate_quality_score`, etc. Imports `newsproc.news_rules`/`newsproc.news_data` |
| `marketdata/oi_data.py` | NSE F&O bhavcopy fetch+parse — **futures (STF) + options (STO/IDO)** from one ZIP → `get_oi_buildup_for_ticker` (technical model) + `get_fno_raw_snapshot`/`get_option_chain_raw` (Smart-Money board). Also defensive `get_delivery_map` (cash delivery%) + `get_bulk_block_deals`. **Persists each snapshot to `fno_snapshot`** (cold-start restore + `get_prev_snapshot` diff baseline) and overlays the Angel intraday source when enabled. Lazy-imported |
| `marketdata/angel_fno.py` | **Angel One intraday F&O OI (#5)** — builds the EOD snapshot shape from live FULL-mode `opnInterest` (futures for all underlyings + index option chains), OI-change vs the persisted EOD baseline. Pure `assemble_futures`/`assemble_index_chain` + stale-while-revalidate background build. OFF unless `ANGEL_FNO_ENABLED=1` + creds; auto-falls back to EOD (Angel blocks datacenter IPs) |
| `marketdata/earnings_data.py` | **Pure** earnings math (no I/O) — Indian fiscal-quarter labels, YoY/QoQ growth, margins (bps), EPS-surprise classification, rule-based quarter verdict, and `build_scorecard()`. Backs `/api/earnings/intelligence`; unit-tested in `tests/test_earnings_data.py` |
| `marketdata/price_resolver.py` | **Pure** price rules (no I/O) — `select_fresh_close(daily, reg_price, reg_time_ist)` reconciles the daily-close series vs Yahoo `regularMarketPrice`/`regularMarketTime` to **kill the "stale close" bug** (daily bar is null right after the bell → the close lagged a session; e.g. IOC showed 138.26 when the real close was 135.6). `atr_stop_target(atr_pct)` = **stop ATR/2, target ATR**, flat 1%/2% fallback. Unit-tested in `tests/test_price_resolver.py` |
| `newsproc/news_rules.py` | Pure rule-based classification — keyword filter, sentiment lists, `classify_category`, `STOCK_KEYWORD_MAP` |
| `newsproc/news_data.py` | Pure static data tables — `MACRO_IMPACT_MAP`, materiality/noise keyword lists, ticker-parsing sets |
| `newsproc/calendar_seed.py` | Pure static seed for the macro/economic-events calendar (`CALENDAR_EVENTS_SEED`) |
| `newsproc/portfolio_data.py` | Pure lookup tables for the portfolio assistant's ticker detection |
| `newsproc/filing_classifier.py` | **Exchange Filing Alerts** classifier — pure (stdlib `re`). `classify_filing(text, category, subcategory)` buckets a BSE announcement / catalyst headline into one of 9 material event types (promoter pledge / insider buy-sell / rating change / acquisition / resignation / order win / bonus / split / dividend) with `impact` (positive/negative/neutral), `severity`, a plain-English `explanation`, and extracted `detail` (₹/%/ratio/agency). No LLM. Feeds `/api/filings` |
| `signals/prediction_models.py` | 5-model ensemble predictor — sentiment, historical, sector, event, aggregation |
| `signals/technical_analysis.py` | RSI, SMA, Bollinger Bands, volume analysis, market regime detection. Now also returns `avg_volume_20d` (for the liquidity filter) |
| `signals/calibration.py` | Maps ensemble score → empirical P(target before stop); meta-label gate (levers #1/#4). Loads `calibration_map.json`; gate OFF by default (`CALIBRATION_GATE_ENABLED`) |
| `signals/ripple_engine.py` | **Ripple 2.0** — pure, deterministic 5-dimension macro cascade (direct/second-order/sector/portfolio/action-window) via signed betas. No LLM. `compute_ripple()`; served by `/api/macro/events/<id>/ripple2` |
| `signals/fno_engine.py` | **F&O Smart-Money** — pure board builder. `build_smart_money_board()`: OI×price buildup quadrants + conviction (directional), unusual-OI, PCR/max-pain/ranked-walls + **per-strike IV/Greeks/skew** (`option_chain_view`), index matrix, futures **basis** + **rollover**, **FII/DII participant positioning**, sector clustering, market bias, deterministic **setups** + narrative. No LLM. Served by `/api/fno/*` |
| `signals/options_math.py` | **Black-76 IV + Greeks** — pure. `implied_vol_black76` (Newton+bisection, intrinsic-floor), `black76_greeks` (Δ/Γ/Θ-day/Vega-1%), `iv_and_greeks`, `years_to_expiry`. Priced off the futures forward → no dividend-yield guess. Env `IV_RISK_FREE_RATE` (0.065) |
| `signals/nifty_outlook.py` | **Nifty Next-Session Outlook** — pure pre-open bias model. `compute_nifty_outlook(snapshot, during_nse_hours)`: aggregates the live macro board (US VIX, DXY, US10Y, Brent, USD/INR, Gold, Copper, India VIX) via signed NIFTY betas → expected next-session move + vol-band range + honest (capped) confidence + transparent per-driver breakdown. No LLM. Served by `/api/macro/nifty-outlook` |
| `eval_loop.py` | Forward shadow-ledger — logs EVERY signal decision (approved + rejected, with config) into the append-only `signal_eval_log` table, then labels ATR outcomes for all so each filter is measurable. Surfaced by `/api/eval-report` |
| `backtest.py` | Bulk historical replay — news vs candle data, win/loss stats. ⚠ **Stale**: calls `.history(start=…)` which the current shim no longer supports |
| `performance_report.py` | Terminal-based performance stats |
| `database.py` | SQLite user auth, OTP, OAuth 2.0, session management (currently unimported — at `backend/` root) |

## Win-rate levers & the eval loop

A calibration study (see `scratch/` + `signals/calibration.py`) found the raw ensemble
score **non-predictive** (high-confidence signals did not win more) on the available
data, so win-rate work shifted to **selection** — env-tunable levers on the signal path,
all reversible:

| Knob | Default | Effect |
|------|---------|--------|
| `MIN_SIGNAL_PRICE` / `MIN_TURNOVER_CR` | 20 / 1.0 | **Liquidity filter** — skip penny (<₹20) & illiquid (<₹1cr/day turnover) names before the ensemble. Uses `tech_data['avg_volume_20d']`. |
| `ATR_STOP_MULT` / `ATR_TARGET_MULT` (+ `ATR_STOP_CAP_PCT` / `ATR_TARGET_CAP_PCT`) | 0.5 / 1.0 (10 / 20) | **Stop = ATR/2, Target = ATR** (2:1 R:R). No-ATR → flat **1%/2%** fallback (`REQUIRE_ATR=0` now — it no longer *skips*). Wide caps only guard a corrupt ATR. Math lives in the pure, unit-tested `marketdata/price_resolver.atr_stop_target()`. |
| `REQUIRE_TECH_CONFIRM` / `TECH_CONFIRM_MIN` | 1 / 50 | Require the technical model (s3) to **actively confirm** the direction, not just "not veto". |
| `W_AI` `W_TECHNICAL` `W_HISTORICAL` `W_SECTOR` `W_INDIAN` | 0.30 / 0.30 / 0.20 / 0.05 / 0.15 | Ensemble weights (AI **down-weighted** from 0.40; final score normalized by total weight). |
| `REGIME_HARD_BLOCK` | 0 | Hard-reject counter-regime trades (vs the soft `REGIME_PENALTY`). |
| `CALIBRATION_GATE_ENABLED` (+ `RR_BREAKEVEN`) | 0 | Meta-label gate: reject signals whose calibrated `p_win` < breakeven. Needs a trustworthy `signals/calibration_map.json` first. |

The selection funnel (`SELECTION_FUNNEL`: `liquidity_skip` / `atr_skip` / `ensemble_rejected`
/ `ensemble_approved`) is surfaced in **`/api/debug-worker-status`** so each filter's drop
rate is visible.

### The eval loop (the scoreboard)

`eval_loop.py` + the **append-only `signal_eval_log` table** log *every* decision the
worker makes — approved AND rejected, with the active config snapshot. The
`eval_labeler_worker` (every `EVAL_LABEL_EVERY_HOURS`=6h) then computes the ATR
triple-barrier outcome for **all** of them once older than `EVAL_HORIZON_DAYS` (4).

- **`GET /api/eval-report`** → approved vs **rejected** win rate (the counterfactual: are the
  filters dropping losers or winners?) + per-disposition breakdown.
- **`POST /api/admin/label-eval`** (token: `X-Alpha-Lens-Token`) → trigger labelling on demand.

> ⚠️ **`signal_eval_log` is APPEND-ONLY by design.** No prune/archival worker touches it and
> the reset-all-news endpoint does **not** wipe it — only `INSERT` (log) and `UPDATE` (fill
> outcome) ever run against it, so the measurement record is permanent.

The calibration map is built offline by the `scratch/` pipeline (`relabel_signals.py` →
`plot_compare.py`); refresh `signals/calibration_map.json` as real closed trades accumulate,
then enable the gate. New env knobs: `EVAL_HORIZON_DAYS`, `EVAL_LABEL_EVERY_HOURS`,
`EVAL_LOG_DISABLED`, `EVAL_LABELER_DISABLED`.

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

> **`GOOGLE_OAUTH_CLIENT_ID` is the single source of truth for Google sign-in.**
> It's used both server-side (token verification in `oauth-signin`) **and**
> client-side: the frontend fetches it from **`GET /api/public-config`**
> (`{"google_client_id": …}`) in `initializeGoogleAuth()` rather than hardcoding
> it in `app-core.js`. The ID is public (it ships in the sign-in button anyway), so
> serving it to the client is fine — this just keeps the server and button in sync
> from one env var. Set it in the Render dashboard env for production.

### Signal lifecycle / retention env vars

| Var | Default | Meaning |
|-----|---------|---------|
| `SIGNAL_EXPIRY_HOURS` | `96` | A signal not hitting target/stop within this window is marked **Expired** (excluded from hit-rate). |
| `SIGNAL_RETENTION_DAYS` | `90` | Signals + their news stay in the **hot tables** at least this long. Keep aligned with `ARCHIVE_AFTER_DAYS`. |
| `ARCHIVE_AFTER_DAYS` | `90` | `archival_worker` MOVES rows older than this into `*_archive` tables (reversible) every `ARCHIVE_RUN_EVERY_HOURS`. |
| `SIGNAL_TERMINAL_MAX` | `1500` | Max rows `/api/signal-terminal` returns over the 90-day window (~6 signals/day in practice). |
| `NEWS_MAX_AGE_DAYS` | `5` | **News feed** window — "All News" shows the last N days; the prune deletes signal-less news older than this. |
| `NEWS_MAX_ROWS` | `800` | **News feed** row cap — `prune_low_value_news` deletes signal-less news beyond the newest N. |

> **News feed vs signals are two different retention windows.** The *news feed*
> is bounded to 800 rows / 5 days. *Signals* persist 90 days. News that a signal
> references is **exempt** from the news prune — it's kept with the signal (so the
> signal terminal can show its headline) and archived alongside it at 90 days.

## Signal retention & lifecycle

Signals live in `stock_impact` (hot table) and are **retained for at least 90 days**:

1. **Created** by the AI news engine → `stock_impact` with `status='Active View'`.
2. **Monitored** by the yfinance worker → status resolves to `Predicted Target Hit` / `Stop Loss Hit` / `Reacted Against Prediction`, or **Expired** after `SIGNAL_EXPIRY_HOURS`.
3. **Retained** in the hot tables for `SIGNAL_RETENTION_DAYS` (90). The **only** thing that removes them is `archival_worker`, which **moves** rows older than `ARCHIVE_AFTER_DAYS` into `stock_impact_archive` / `news_archive` (reversible insert+delete) — nothing is hard-deleted on the hot path.
   - ⚠️ There used to be a per-cycle `DELETE ... older than 7 days` in `ai_news_worker` that destroyed signals early. It was **removed** — `archival_worker` is now the sole retention authority.
4. **Surfaced** by `/api/signal-terminal` (90-day window; live re-pricing only for `Active View` signals, closed ones use stored price) and the track record via `/api/backtest-stats?range=90d|all`.

### Reset (start tracking from zero)

To wipe **all** signals + news and begin counting from 0 (e.g. after a model/prompt change):
```bash
curl -X POST "http://127.0.0.1:5000/api/admin/reset-all-news?confirm=YES_WIPE_EVERYTHING" \
  -H "X-Alpha-Lens-Token: <SQL_RUNNER_SECRET>"
```
Wipes `stock_impact`, `news`, both `*_archive` tables, and `historical_patterns`, and clears the in-memory dedup/bias caches so the worker restarts blank. Requires the `?confirm=YES_WIPE_EVERYTHING` guard.

## Price correctness & Track-Record recompute

Two defects made stock prices (and therefore the Track Record) wrong; both are fixed
and the historical data was recomputed in place.

1. **The "stale close" bug (root cause).** Yahoo's daily candle series (`interval=1d`)
   does NOT finalize *today's* bar until ~15–20 min after the 15:30 IST close — right
   after the bell the last daily bar has `close=None`. So any "last close" taken purely
   from the daily series **lagged a full session** (e.g. IOC showed **138.26**, Friday's
   close, when the real Monday close was **135.6**). Meanwhile Yahoo's `regularMarketPrice`
   (stamped `regularMarketTime` ~15:30:01) already carries the genuine latest close.
   Different code paths picked different sources → the **same stock showed different
   prices across the UI**. Fixed by the pure `marketdata/price_resolver.select_fresh_close`
   (uses `regularMarketPrice` only when its timestamp is a completed NSE session ≥ 15:30
   IST AND not older than the newest daily bar; else the daily bar). It is now the single
   source of truth wired into `get_last_closed_session_quote` (via a new
   `_yahoo_daily_and_meta` one-call+120s-cache helper) **and** `_get_yahoo_official_close`,
   so every market-closed display path (`get_price_with_range`, `get_stock_market_change_quote`,
   `/api/stock-price`, Signal Terminal re-price, watchlist, Command Center) agrees.
   ⚠️ This is the **stock-price analogue of the `macro_tracker` `chartPreviousClose` fix** —
   never trust the daily series' last bar blindly; reconcile against `regularMarketTime`.
2. **ATR rule:** stop = **ATR/2**, target = **ATR** (was 1×/2× with 1%/2% floors). No-ATR
   now falls back to a flat **1% stop / 2% target** instead of *skipping* the signal
   (`REQUIRE_ATR` default flipped 1→0). Math is the pure `atr_stop_target()`; mirrored in
   `eval_loop.py`.
3. **Realistic bracket fills (honest P&L):** `check_historical_hits` (the shared resolver
   used by the yfinance worker, the repair pass, and the recompute) now records the **fill
   at the target/stop level**, or the **gap-open** when a bar gapped *through* a level —
   NOT the candle extreme. Recording the raw high/low overstated both wins and losses
   (e.g. a −5.23% candle low booked against a 1% stop). The worker's live-intraday section
   was clamped to match.

4. **⚠️ Postgres `created_at` datetime-vs-string bug (prod-only, was silent).** `stock_impact.created_at`
   is `TIMESTAMP` → Postgres returns a **`datetime` object**, SQLite returns the stored **string**.
   `_parse_created_at` and the yfinance worker's inline parsers assumed a string (`'GMT' in s` /
   `strptime`), so on **production** every parse silently failed → the worker's multi-day historical
   OHLC catch-up + expiry never ran (aged signals stuck "Active View"; you only ever saw the
   live-intraday stops, never the multi-day **target** hits → "stops but no targets, 0 win rate"),
   and `recompute_all_signals` updated **0 rows**. Fixed by the pure `price_resolver.parse_timestamp()`
   (handles datetime *and* string → tz-aware UTC); `_parse_created_at` delegates to it and the worker's
   three parse sites (after-hours start, §1 catch-up, expiry) route through it. Unit-tested. This is a
   **local SQLite vs prod Postgres divergence** — it cannot reproduce locally, so it stayed hidden.

**Recompute the whole Track Record in place** (after the fixes, or any future ATR-rule
change) — re-derives each signal's stop/target (ATR recovered from the stored
`technical_context.atr_pct`), re-resolves status from real OHLC, recomputes `current_price`
(fresh-close resolver) + `estimated_change_percent`, and rewrites the `reason`'s
`ATR stop: X% | target: Y%` marker. **No LLM; only UPDATEs (never deletes); idempotent.**
`/api/backtest-stats` reads straight off these rows, so it auto-corrects.
```bash
curl -X POST "http://127.0.0.1:5000/api/admin/recompute-signals" \
  -H "X-Alpha-Lens-Token: <SQL_RUNNER_SECRET>"   # optional ?days=N&limit=N
```
Implemented by `recompute_all_signals()` / `admin_recompute_signals()` in `app.py`
(**route #51**). Local one-shot: `ALPHA_LENS_SKIP_AUTO_BOOTSTRAP=1 python -c "import app; print(app.recompute_all_signals())"`.

## Health & worker liveness

Two endpoints expose background-worker state:

| Endpoint | Use it for |
|----------|-----------|
| `GET /api/health` | One-glance "is anything broken right now?". Returns `overall: "ok"\|"degraded"\|"down"` + a per-worker state (`ok`/`not_started`/`running`/`silent`/`stalled`) judged against a per-worker stall budget, plus Gemini-key counts and a DB probe. HTTP **503** when `overall=down` so uptime monitors can latch on the status. Use this for cron monitors and quick eyeball checks. |
| `GET /api/debug-worker-status` | Full per-worker dump — raw heartbeat fields, last cycle metrics (`last_scrape_count`, `last_save_count`, `last_news_moved`, `last_pruned_count`, etc.), last error + age. Use this when `/api/health` says something's wrong and you need the detail. |

Both read from the in-process `WORKER_HEARTBEAT` dict in `app.py`, populated by each worker per cycle (`_heartbeat(name, **fields)`). All eight workers — `ai_news`, `yfinance`, `macro_shock`, `archival`, `news_prune`, `eval_labeler`, `calendar`, `filings` — write their start/finish/error timestamps. Per-worker stall budgets live in `_WORKER_STALL_BUDGET_SECS` and are tuned to each worker's natural cadence (e.g. archival's budget is 36h because it runs every 24h; calendar's is 3h for its 30m cadence).

## The Economic Calendar (forward catalysts)

A forward-looking schedule of macro events (RBI/Fed/MPC, CPI/IIP/WPI, PMIs, OPEC, China, monsoon, FOMC…), each carrying **AI-style scenario analysis** (upside/expected/downside with probabilities that sum to 1.0), historical analogues, and related sectors/tickers. Frontend: `app-calendar.js` (`/api/calendar`). Backend: `economic_calendar` table seeded from `newsproc/calendar_seed.py` (`CALENDAR_EVENTS_SEED`).

**Auto-removal — an event drops off the moment it's done.** This is enforced at two layers:
- **On read** (`/api/calendar`): events whose IST datetime (`_calendar_event_is_done`) has passed are filtered out by default. Non-clock times (`Morning`/`TBD`/`All Day`/`""`) resolve to end-of-day so they survive their whole day. Pass `?include_done=1` for an admin/history view that still shows concluded events.
- **By worker** (`calendar_worker` → `_calendar_maintenance`, every 30m): flips concluded events to `status='released'`, then **hard-purges** events older than `CALENDAR_PURGE_AFTER_DAYS` (2) so the table self-cleans. `seed_calendar_events()` also **skips already-concluded** seed entries (unless `force=True`), so the weekly slate rotates naturally and restarts don't resurrect past events.

**Refreshing the week.** Two paths: (a) edit `CALENDAR_EVENTS_SEED` + restart (startup runs `seed_calendar_events(force=False)`, INSERT-OR-IGNORE keyed on `(event_date, country, title)`), or (b) `POST /api/admin/calendar/upsert` (token: `X-Alpha-Lens-Token`) for a live delete-then-insert over a window. The current seed (`2026-06-08 → 2026-06-17`) was produced by the `Workflow` pipeline (draft → adversarial verify), web-grounded in real prints, then harmonized to one macro backdrop (USD/INR ~95, Brent ~$95-97). Regenerate with `scratch/gen_calendar_seed.py` (gitignored) if you re-run that workflow.

Env knobs: `CALENDAR_RUN_EVERY_MIN` (30), `CALENDAR_PURGE_AFTER_DAYS` (2), `CALENDAR_DONE_GRACE_MIN` (0), `CALENDAR_WORKER_DISABLED`.

## The Command Center (dashboard "live edge" bar)

The dashboard (`view-top-news`) leads with the product's **actual value** — live
signals + track record — instead of burying it behind a tab. The `#command-center`
`<section>` sits at the **top of the main column, above "Latest Headlines"**, and is
rendered entirely by `loadCommandBar()` / `renderCommandBar()` in `app-news.js`.

- **Data:** reuses the exact same endpoints as the Signal Terminal and Track Record —
  `GET /api/signal-terminal` (signals) + `GET /api/backtest-stats?range=all` (summary)
  — so it can never disagree with them. No new backend.
- **Shows:** 4 stat tiles (Live Signals · Today's Bias · Avg Conviction w/ color-coded
  meter · Hit Rate **only once trades close**, else "Signals Tracked · grading in
  progress"), a **bull/bear bias distribution bar** (`#cc-bias`), and the **top 5
  highest-conviction live signal cards** (`#cc-signals`) — each card links to the
  Signal Terminal.
- **Lifecycle:** `loadCommandBar()` is called from `startSmartPolling()` on boot and
  on every news-poll tick (`app-market.js`). The heavier `backtest-stats` call is
  **throttled to ≤ once / 5 min** (cached in `_ccSummary`); signals refetch every tick.
- **Degradation:** the section is `hidden` by default and only revealed once there's
  something real to show. On a cold-start fetch failure or zero signals + zero track
  record, it **stays hidden** and the dashboard simply shows the news feed — never a
  broken skeleton.
- **Does NOT touch** the per-article Signal Desk, Plain English Decode, Full Article,
  or the "Stocks Affected" table (all intentional, left as-is).
- Styles: `.cc-*` block in `styles.css` (token-based, responsive: stats 2-col→4-col,
  header stacks < 480px). A **compliance disclaimer footer** (`.app-footer`) was added
  site-wide for the finance-product trust layer.

### Sparklines (Command Center cards)

Each top-conviction card in the Command Center paints a tiny inline **SVG sparkline**
of the ticker's recent close trend (green if up over the window, red if down).
- **Backend:** `GET /api/sparklines?tickers=A,B,C` (in `app.py`) returns
  `{ticker: [close, …]}` (last ~20 daily closes). It fetches via
  **`yf.Ticker(t).history(period='1mo', interval='1d')`** — NOT `get_ohlc()`. ⚠️ This
  matters: `get_ohlc()` is **Angel-One-only with no fallback** and returns `[]` on the
  Render datacenter IP (the static symbol→token map makes it *enter* the Angel One branch,
  but the authenticated candle call fails there) — so the original `get_ohlc()` version
  rendered **empty** in production. `Ticker.history()` falls back to **Yahoo's chart API**
  (reachable from Render — live quotes already use it), which populates the series.
  **Server-cached** `_SPARKLINE_CACHE` for `SPARKLINE_TTL_SECS` (900s) and capped at
  `SPARKLINE_MAX_TICKERS` (10) so the 30s dashboard poll never hammers the data API.
  Defensive — `[]`/`{}` on any failure.
- **Frontend:** `enhanceCommandBarSparklines()` / `_sparkSVG()` / `_paintSparks()` in
  `app-news.js`. Cards render first; sparklines are an **async, additive** enhancement
  (frontend-cached 10 min in `_ccSparks`, fetches only uncached tickers). A slow/failed
  fetch never blocks the cards. Pure hand-rolled SVG (no chart lib). Env knobs:
  `SPARKLINE_TTL_SECS`, `SPARKLINE_DAYS`, `SPARKLINE_MAX_TICKERS`.

## The Portfolio Risk Radar (daily risk score)

The **Portfolio tab** leads with a **Risk Radar** — a daily **LOW / MEDIUM / HIGH**
risk score (0–100) for the user's watchlist, broken down across seven dimensions:
per-stock, **sector concentration**, **news flow**, **macro**, **valuation**,
**technical weakness**, and **F&O pressure**. The `#risk-radar` `<section>` sits at the
**top of the Portfolio tab's right column, above "News Affecting My Portfolio"** —
mirroring the dashboard's Command Center ("lead with value").

- **Backend:** `GET /api/portfolio/risk-radar?tickers=A,B,C` (in `app.py`).
  **Purely quantitative / rule-based — NO Gemini/LLM call** (zero keys, deterministic,
  cacheable). Implemented by `_compute_portfolio_risk()` + per-dimension scorers
  (`_score_technical` / `_score_valuation` / `_score_fno` / `_score_news_for_ticker` /
  `_score_macro` / `_score_sector_concentration`) and `_risk_level()` banding
  (LOW <34, MEDIUM 34–61, HIGH ≥62). Inputs are all **already-cached** helpers:
  `get_stock_technical_context()` (technicals **+ `oi_buildup`** for F&O — one call covers
  both), `get_stock_fundamentals()` (sector + P/E + P/B + 52w for valuation/concentration),
  the `stock_impact` table (recent bearish signals → news), and `MacroDataTracker`
  (India VIX + shocks, portfolio-wide). **Server-cached** `_RISK_RADAR_CACHE` per
  sorted-ticker key for `RISK_RADAR_TTL_SECS` (1800s) and capped at
  `RISK_RADAR_MAX_TICKERS` (15). **Defensive** — any single ticker that fails to resolve
  is skipped and flagged in `degraded`; the route never 500s (returns a safe empty shell).
  Per-stock composite = weighted blend (technical .42 / news .26 / F&O .18 / valuation .14)
  renormalized over whichever dims a name has; overall =
  `0.55·avg_stock + 0.15·max_stock + 0.18·macro + 0.12·sector`. Route count is now **50**.
- **Frontend:** `loadRiskRadar()` / `renderRiskRadar()` + helpers (`_rrDimTile`,
  `_rrStockRow`, `_rrMeter`, `_rrSkeleton`, `_rrErrorState`) in `app-stocks.js`. Renders a
  hero (big score + level + summary + a LOW→HIGH meter), 6 dimension tiles (each with a
  bar + top contributing stocks/reasons), and a **Top risks by stock** ranking.
  **Lifecycle:** called from `switchTab('portfolio')` (lazy-load) and on every watchlist
  change (`saveWatchlist`, force-refresh); a 60s client throttle sits over the 30m server
  cache. **Empty state:** with **no watchlist** the section is NOT hidden — it shows a
  professional **teaser** (`_rrEmpty()`): the 7 dimensions + an **"Add stocks"** CTA
  (`focusWatchlistSearch()` → focuses `#stock-search-input`) so the radar is discoverable,
  and a note that **the same watchlist powers both the radar and the portfolio news** (the
  news empty state references it too). **Degradation:** a zero-data/error fetch with a
  populated watchlist shows a retrying message (never a broken shell). Styles: `.rr-*` block
  in `styles.css` (token-based, level-colored
  green/amber/red, responsive 2-col→1-col < 600px). Env knobs: `RISK_RADAR_TTL_SECS`,
  `RISK_RADAR_MAX_TICKERS`.

  **Holdings + live P&L + value-weighted radar (added).** The watchlist entry now carries
  optional **`qty` + `avgPrice`** (client-side localStorage, same key, with a load-time
  normalize so old `{ticker,name}` rows default to `null`). `renderWatchlistPanel`
  (`app-stocks.js`) renders inline Qty / Avg ₹ inputs + a per-card **unrealized P&L** badge
  + a **portfolio total tile** (mark-to-market, avg-cost, unrealized — labelled; `price==0`
  → "price unavailable"; implausible avg vs live → "check avg vs price"). Prices reuse
  `/api/stock-price/<t>` → `watchlistPrices` (no new endpoint). The **Risk Radar is now
  value-weighted**: `loadRiskRadar` **POSTs** `{holdings:[{ticker,qty,avgPrice}]}` (was
  GET `?tickers=`); `/api/portfolio/risk-radar` accepts **GET (equal-weight, back-compat) OR
  POST (value-weighted)**, folds the per-ticker weight into the `_RISK_RADAR_CACHE` key (so
  a 10-share vs 1000-share portfolio of the same names doesn't collide), and
  `_compute_portfolio_risk(tickers, weights=)` uses `_rr_weighted_mean` (pure, unit-smoked)
  for `avg_stock` + each dimension (weight = `qty×avgPrice`; unsized names default to the
  mean position size; no sizes → equal-weight). `max_stock` + per-stock composite unchanged.
  ⚠️ **Sector concentration is still count-based** (not value-weighted) — a noted follow-up.
  Route count stays **50** (method added to the existing route). Still deterministic / no-LLM.

### Signal Terminal — mobile card view

The 10-column Signal Terminal table is unreadable on phones. Each `<td>` in
`renderTerminal()` now carries a `data-label`, and a `@media (max-width:767px)` rule in
`styles.css` transforms rows into **stacked cards** (thead hidden, each cell a
label→value flex line, headline wraps full-width). The empty/error `colspan` row is
excepted so it stays centered. Desktop is untouched (the transform is mobile-only).

### Mobile navigation (critical fix)

The desktop nav menu is `hidden md:flex`, so **below 768px it disappeared with no
replacement** — phones had no way to switch tabs. Fixed with a **horizontally-scrollable
mobile tab bar** (`#mobile-tabbar` / `.mtab` in `index.html`, shown only `< 768px` via a
self-contained `@media (max-width:767px)` rule — NOT Tailwind's `md:hidden`, to avoid
CDN source-order ambiguity). Each pill calls the same `switchTab(...)`; `switchTab` now
also syncs the active `.mtab`. The stock-only tabs carry `stock-mode-element` so they
hide in non-stock mode exactly like the desktop nav. Other mobile touches: `<main>` is
`p-4 md:p-6` (more content width on phones); heroes already use `clamp()`; data tables
keep their `overflow-x-auto` horizontal scroll (a full mobile card-view is a noted
follow-up). Viewport meta is present.

## The Earnings & Results Intelligence (quarterly results, decoded)

A dedicated tab **between the Signal Terminal and Track Record** (nav order:
Signal Terminal → **Earnings** → Track Record) that auto-summarizes the latest
quarterly results for the user's watchlist holdings — or, when the watchlist is
empty, the names the engine is currently tracking (distinct recent `stock_impact`
tickers). Per holding it shows **revenue, net profit, operating & net margin
(with YoY bps change), EPS surprise vs estimates (Beat/Miss/In-line), a
transparent rule-based quarter verdict (Strong/Mixed/Weak), affected holdings,
and the next earnings date** — plus an optional, grounded AI brief covering
**management tone / guidance / order book**. Mirrors the "lead with value"
pattern of Command Center and Risk Radar.

- **Backend:** `GET /api/earnings/intelligence?tickers=A,B,C` (in `app.py`,
  route #44). Two layers:
  - **Quantitative core (precise, deterministic, ZERO Gemini keys):** real
    yfinance — `_extract_quarterly_financials()` (quarterly income statement →
    revenue / net income / operating income, last ~6 quarters) and
    `_extract_earnings_dates()` (EPS estimate vs reported → surprise %, plus the
    next earnings date). ⚠️ Like `get_stock_fundamentals()`, this uses the **real
    yfinance library** (a local `import yfinance`), NOT the `angelone_shim` `yf`
    alias — the shim's `Ticker` only exposes `.fast_info`/`.history()`. Yahoo
    carries quarterly financials for `.NS` names; INR is shown in **₹ crore**. All
    the arithmetic (fiscal-quarter labels, YoY/QoQ, margins, verdict, plain-English
    summary) lives in the **pure** `marketdata/earnings_data.py`
    (`build_scorecard()`), unit-tested in `tests/test_earnings_data.py`.
  - **Qualitative AI brief (optional, env-gated, key-frugal):**
    `_earnings_ai_brief()` makes ONE grounded Gemini call per name — but **only**
    for holdings that reported within `EARNINGS_AI_FRESH_DAYS` (30) AND have recent
    related headlines in the `news`/`stock_impact` tables (those headlines are the
    *only* source it may quote). Per-ticker cached 24h (`_EARNINGS_BRIEF_CACHE`).
    The prompt forbids inventing numbers and forces "Not disclosed in available
    sources" when headlines don't cover guidance/order-book. Off-season → zero
    calls. Toggle with `EARNINGS_AI_BRIEF_ENABLED` (**default 1/on**; set `0` to
    make the tab fully deterministic / no-LLM like the Risk Radar).
  - **Performance (it must never be a "slow runner"):** the up-to-8 names × 3
    yfinance round-trips are fetched **concurrently** via a thread pool, but with a
    twist — yfinance negotiates a cookie/crumb on its first call under a
    process-global lock, so N cold threads contending on it balloon a ~5s batch to
    ~20s. We therefore **prime the session on ONE ticker first** (`primer`), then
    fan out the rest over the now-warm session (`_build_one_earnings_card` per
    ticker). Measured: ~4.5s for 5 names warm, ~5.6s for 1 cold. The whole fetch is
    bounded by `EARNINGS_FETCH_TIMEOUT_SECS` (15) — a hung/throttled Yahoo can never
    stall the response; it returns whatever resolved, flagged `degraded`. The tab
    is **lazy-loaded** (only on `switchTab('earnings')`, NOT in the 30s dashboard
    poll) with a skeleton, so it can't block the rest of the site.
  - **Caching & resilience:** `_EARNINGS_CACHE` per sorted-ticker key — **6h** TTL
    for a clean result, **`EARNINGS_DEGRADED_TTL_SECS` (10 min)** for a
    partial/degraded one so it retries soon. **Stale-while-revalidate:** if a
    recompute comes back degraded (e.g. Yahoo throttling) but a prior clean payload
    exists, the route keeps serving the good one — a transient hiccup never blanks
    out a watchlist that already worked. Capped at `EARNINGS_MAX_TICKERS` (8).
    **Defensive** — any ticker that fails to resolve is skipped and flagged in
    `degraded`; the route never 500s (returns a safe empty shell). ⚠️ Yahoo's
    fundamentals endpoints **rate-limit bursty/datacenter IPs** (per the Render
    note) — that's contained by the budget + degraded-TTL + SWR, but it's why a
    burst of cold fetches can briefly show partial data.
- **Frontend:** `loadEarningsIntel()` / `renderEarningsIntel()` + helpers
  (`_eiCard`, `_eiMetricTile`, `_eiSurpriseTile`, `_eiBrief`, `_eiStatsRow`,
  `_eiUpcoming`, `_eiSkeleton`, `_eiEmpty`, `_eiError`) in **`app-earnings.js`**
  (chunk 7/10). Renders a 4-tile stat row (Reported / Beats / Misses / Upcoming),
  a headline, per-holding result cards (verdict-colored left accent bar, summary
  line, 4 metric tiles, verdict drivers, optional AI brief), and an upcoming-
  results strip. Static hero in `index.html` (`#view-earnings`) + dynamic
  `#earnings-body`. **Lifecycle:** lazy-loaded from `switchTab('earnings')`; 60s
  client throttle over the 6h server cache; watchlist-keyed (refetches when
  holdings change). **Degradation:** skeleton on first paint, `.term-empty`
  empty/error states (never a perpetual skeleton). Styles: `.ei-*` block in
  `styles.css` (token-based, Strong/Mixed/Weak = green/amber/red, responsive
  4-col→2-col→1-col).
- **Wiring (new chunk → 3 places + version):** registered in `app-core.js`
  (`tabs` + `STOCK_NAV_IDS` + the `switchTab` lazy-load hook), added to
  `index.html` (desktop nav, mobile tabbar, `#view-earnings`, script tag), and the
  `sw.js` `isStaticAsset` regex (`earnings` added). The `/app-` prefix in `app.py`
  `_CACHE_RULES` already covers caching. Cache version bumped to
  `al-v19-2026-06-08-earnings` (index.html `?v=` + `sw.js CACHE_VERSION`).
- **Known data limit:** Yahoo's EPS estimates/surprise are **sparse for many NSE
  names** — when absent the surprise shows "Awaited" (honest, not fabricated) and
  Beats/Misses count it as neither. Verified live against `RELIANCE.NS` / `TCS.NS`
  (real revenue/profit/margin/next-date resolve; surprise was "Awaited"). Env
  knobs: `EARNINGS_TTL_SECS`, `EARNINGS_DEGRADED_TTL_SECS`, `EARNINGS_MAX_TICKERS`,
  `EARNINGS_FETCH_TIMEOUT_SECS`, `EARNINGS_FRESH_DAYS`, `EARNINGS_AI_BRIEF_ENABLED`,
  `EARNINGS_AI_FRESH_DAYS`, `EARNINGS_BRIEF_TTL_SECS`.

## The Ripple (macro propagation graph)

"The Ripple" expands a systemic event into a 3-tier cascade of NSE stocks
(Direct Impact → Supply Chain → Macro Transmission), each node carrying a
direction + **confidence %** + one-line causal reason. Two entry points share
the same shape and renderer (`_renderRippleGraph` in `app-ripple.js`):
- **News ripple** (`generate_ripple_graph` → `/api/news/<id>/ripple`) — auto-built for big news.
- **Macro ripple** (`generate_macro_ripple_graph` → `/api/macro/events/<id>/ripple`) — built from a quantitative price shock (the Copper/Brent/etc. cards in Macro Pulse).

**Selectivity / honest confidence.** The LLM tends to pad every tier to the
requested count and inflate confidence, which made graphs look like *everything*
reacts. Two layers fix this:
1. **Prompt** — both generators now ask for *fewer, materially-impacted* names
   (tier 1: 2-5, tier 2: 1-4, tier 3: 0-3, "never pad to a count"), with
   confidence that **decays across tiers** and scales to the move's size.
2. **`_postprocess_ripple_graph(data, shock_level=None)`** — a deterministic
   backstop applied on **both generate AND read** (so graphs cached before this
   existed also tighten, no Gemini re-call). It: normalizes confidence, enforces
   **decay** (no hop can be more certain than its strongest cause — each tier is
   capped at the best confidence of the tier above), drops nodes below a floor,
   then sorts by confidence and caps each tier's size. A borderline `SIGNIFICANT`
   (not `MAJOR`) shock tightens the caps further.

Env knobs (all reversible): `RIPPLE_MIN_CONFIDENCE` (55), `RIPPLE_TIER1_MAX` (5),
`RIPPLE_TIER2_MAX` (4), `RIPPLE_TIER3_MAX` (3). Frontend renders an animated
flowing arrow between tiers (`.rfl-arrow-flow`) so the cascade direction reads at
a glance.

## The Ripple 2.0 (quantitative five-dimension cascade)

**Ripple 2.0 is the accurate, deterministic successor to the LLM macro ripple.**
For a macro shock it produces five dimensions a sell-side quant desk would frame:
**Direct Impact · Second-Order Impact · Sector Impact · Portfolio Impact ·
Action Window**. It replaces the Gemini-generated 3-tier graph **on the Macro
Pulse alert cards** (the per-news 3-tier `openRipple`/`/api/news/<id>/ripple`
path is **left intact**).

**Why deterministic (no LLM).** The old macro ripple called Gemini, which padded
tiers and inflated confidence (and burned keys — see the key-saving policy). The
engine instead models transmission with **signed betas**: each stock carries an
expected %-move per +1% move of the instrument, grounded in the mechanism
(margin / input-cost / duration / risk-on-off). `expected_move = clamp(beta ×
shock%, ±cap)`; **direction is the sign**, so a down-move flips every node
automatically. This is reproducible, unit-tested, instant, burns **zero** API
keys, and never hallucinates a ticker. Betas are seeded from the institutional
correlations already in `compute_macro_effects()` and refined per name.

- **Backend engine:** `signals/ripple_engine.py` — **pure** (stdlib only, no
  app/network/DB import → no cycle). `compute_ripple(instrument_key, pct,
  shock_level, during_nse_hours, watchlist, instrument_label)` returns
  `{instrument, pct, shock_level, summary, direct[], second_order[], sector[],
  portfolio{}, action_window{}}`. 13 tracked instruments map onto **8
  transmission graphs** (`KEY_TO_GROUP`): oil (brent/wti), gas, precious
  (gold/silver), metals (copper), usd (dxy/usdinr), vol (vix_us/vix_in), index
  (nifty/banknifty), rates (us10y). Each node: ticker/name/sector/direction/
  expected_move_pct/confidence/beta/lag/mechanism. **Sector** rolls the nodes up
  to a per-sector net bias; **portfolio** filters nodes to the user's watchlist
  (equal-weight net impact + exposure count) — `applicable:false` when no
  watchlist; **action_window** reads `during_nse_hours` → `ACTIONABLE` (NSE shut,
  position before open) / `LIVE` (repricing now) / `INFO`, with a horizon
  (immediate vs lagged majority) and urgency (MAJOR→HIGH / SIGNIFICANT→MEDIUM).
- **Route:** `GET /api/macro/events/<id>/ripple2?tickers=A,B,C` (in `app.py`,
  imports `compute_ripple as compute_ripple2`). Computed on the fly — **no DB
  cache** (it's cheap and the portfolio dimension is per-watchlist). Defensive:
  404 on unknown event, safe shell on bad input, never 500s on known inputs.
  **Route count is now 50.**
- **Frontend:** `openRipple2()` / `_renderRipple2()` + `_r2*` helpers in
  `app-ripple.js` render a dedicated `#ripple2-modal` (separate from the legacy
  `#ripple-modal`). The Macro Pulse alert cards (`app-macro.js`) call
  `openRipple2(id)` (falling back to `openMacroRipple` only if the new renderer
  is absent) and pass the watchlist via `_r2WatchlistTickers()` (reads the
  `alpha_lens_watchlist` global/localStorage). Styles: `.r2-*` block in
  `styles.css` (token-based, green/red semantics, diverging sector bars,
  responsive 2-col→1-col < 768px).
- **Tunables** (module constants in `ripple_engine.py`, kept there to keep the
  module pure): `MAX_EXPECTED_MOVE` (6.0), `MAX_DIRECT_NODES` (6),
  `MAX_SECOND_NODES` (6), `CONF_DIRECT_BASE` (82), `CONF_SECOND_BASE` (66),
  `CONF_FLOOR`/`CONF_CEIL` (50/95).
- **Tests:** `tests/test_ripple_engine.py` (18 cases — sign-flip, beta scaling +
  cap, confidence decay, sector rollup, watchlist matching, action-window states,
  unknown-instrument/zero/bad-input safety). To extend the model, edit the
  `_GROUPS` betas/mechanisms — pure data, no wiring changes.

## The Nifty Next-Session Outlook (Macro Pulse pre-open bias)

The **Macro Pulse tab** leads with a **Nifty Next-Session Outlook** — a deterministic,
transparent pre-open bias that aggregates the **live macro board** (overnight global cues
already tracked by `MacroDataTracker`) into an expected NIFTY next-session **directional
bias + expected % range + honest confidence**, with a full **per-driver contribution
breakdown**. Like Risk Radar / Ripple 2.0 it is **purely quantitative — NO LLM** (zero
keys, reproducible, instant).

> ⚠️ **Honesty contract.** It is a *bias estimate* (the framework a macro desk uses
> pre-open), **NOT a market-prediction guarantee** — markets gap on news/earnings/flows the
> model cannot see. The engine therefore **caps its own confidence at 80**, labels itself a
> bias, and shows every input that drove the read. Don't re-market it as a forecast of fact.

- **Engine:** `signals/nifty_outlook.py` — **pure** (stdlib only). `compute_nifty_outlook(
  snapshot, during_nse_hours)` sums `driver_change × signed_beta` (each term and the total
  capped) across 8 curated, **non-redundant** drivers (US VIX, DXY, US 10Y, Brent, USD/INR,
  Gold, Copper, India VIX — brent not wti, gold not silver; banknifty/nifty aren't drivers of
  themselves). Betas are grounded in India macro structure (net oil importer, EM beta to the
  dollar/US rates, risk-on/off via vol). The **range** is probability-banded around the bias
  using NIFTY's own realized daily vol: a **~68% (±1σ)** most-likely band + a **~95% (±2σ)**
  outer "all-possibilities" bound (`range_*` / `wide_*` + projected levels). **Confidence** is
  built from driver *agreement* + breadth + magnitude, floored 25 / **ceiled 80**.
- **Route:** `GET /api/macro/nifty-outlook` (in `app.py`) — `MacroDataTracker.get_snapshot()`
  + `is_market_open()` → `compute_nifty_outlook`. Pure compute, no DB cache, safe shell on
  failure. **Route count is now 50.**
- **⚠️ 1-day-change fix (foundational):** `macro_tracker` previously used Yahoo's
  `chartPreviousClose` from a **6mo-range** chart as "prev" → every "1-day" move was actually
  a **6-MONTH** move (Nifty −10.77%, USD/INR +5.68%, Copper +17%…), which also inflated every
  σ so the whole board flagged MAJOR/ACTIONABLE and poisoned the outlook range. Now
  `latest_daily_change()` derives prev from the **prior session close** (`series[-2]`, then
  `previousClose`) — never `chartPreviousClose`. This corrects the change %, the σ shock
  detection, and the outlook for the *entire* Macro Pulse. Regression-tested.
- **Stored alert-card self-heal:** the **Active Shock Alert cards** render from the
  `macro_event` table, so rows *detected by the old buggy code* still carried a stale
  6-month change/prev. `list_macro_events` (and `/ripple2`) now **reconcile each stored
  event against the live snapshot** — overriding `change_pct_1d`/`last_price`/`prev_close`,
  re-running `classify_shock`, and **dropping events that are no longer a real shock** under
  corrected data. So the board self-heals on read (false 6-month "shocks" vanish, real ones
  show the true prior-session close + 1-day move) without a DB wipe.
- **NIFTY price read hardened:** `latest_daily_change` also falls back to the latest valid
  close when Yahoo omits `regularMarketPrice` for `^NSEI`. The live `^NSEI` feed is still
  Yahoo's free, ~15-min-delayed quote (last close after hours) — a true real-time level needs
  a paid feed (GIFT Nifty is the best next-session proxy); the tile labels the value `LAST`
  and frames the horizon (`NSE is shut` / `NSE is open`) so it reads honestly.
- **Frontend:** `loadNiftyOutlook()` / `_mpRenderNiftyOutlook()` in `app-macro.js`, tile
  `#mp-nifty-outlook` at the top of `#view-macro-pulse` (called from `fetchMacroPulse()`).
  Renders NIFTY last + projected level range, stance + expected move ± band, a confidence
  meter, the transparent driver-contribution bars, summary + disclaimer. `.mp-out-*` CSS
  (champagne/green/red tokens). Hides itself on fetch error (never a broken tile).
- **Tests:** `tests/test_nifty_outlook.py` (13 cases — direction per driver, aggregation +
  caps, range-from-vol, confidence ceiling/agreement, horizon framing, empty-safety,
  driver-set dedup). Tunables are module constants in `nifty_outlook.py` (betas, caps,
  `CONF_FLOOR`/`CONF_CEIL`) — pure data, no wiring.

## The F&O Smart-Money Layer (institutional positioning radar)

The **F&O tab** is a dedicated derivatives desk that decodes **what institutions are
doing** from the daily NSE F&O tape. The nav was reorganized for it — **Top News + All
News now live under a "News" dropdown** (desktop) / pills (mobile), freeing a top-level
**F&O** item. Frontend: `app-fno.js` (chunk 9/10), view `#view-fno`, `.fno-*` +
`.nav-dropdown` CSS. Like the Risk Radar and Ripple 2.0 it is **purely quantitative /
deterministic — NO Gemini/LLM call** (zero keys), so it is reproducible, instant, and
never hallucinates a ticker. (An on-demand Gemini "deep brief" was deliberately left out
to respect the key-saving policy — the deterministic narrative already synthesizes 6+
signals; it's a clean one-route follow-up if ever wanted.)

**Data — one file, minimal API-block risk.** Everything is built from the **daily NSE
F&O bhavcopy** (a static ZIP on `archives.nseindia.com` — the CDN, NOT the
datacenter-blocked `api.nseindia.com`). `marketdata/oi_data.py` was extended to parse the
**full** bhavcopy (futures `STF` + options `STO`/`IDO`), so futures OI-buildup AND the
option chain (CE/PE OI by strike, ΔOI, spot via `UndrlygPric`) come from ONE download —
zero extra calls. Two **optional secondary sources** are fetched defensively (each
isolated → `{}`/`[]` + a `degraded` flag on failure, never breaks the board): **delivery
%** (cash `sec_bhavdata_full`) and **bulk/block deals** (`bulk.csv`/`block.csv`). ⚠️ The
secondary equity-archive endpoints could **not be exercised against live data in the build
env** — validate in production via the `[FNO]` logs (the FO bhavcopy itself is the proven
path that already feeds the technical model's `oi_buildup`).

**Engine:** `signals/fno_engine.py` — **pure** (stdlib only, no app/DB/network import).
`build_smart_money_board(snapshot, watchlist, delivery, deals)` returns:
- **Buildup quadrants** (OI×price): Long Buildup / Short Buildup / Short Covering / Long
  Unwinding, each ranked by a **conviction score** (ΔOI magnitude × price-confirm ×
  liquidity × delivery).
- **Unusual OI surges** + **delivery-conviction** spikes.
- **Option analytics** per symbol: PCR(OI), **max-pain**, call/put **OI walls**,
  option-sentiment from fresh writing (ΔOI). `option_chain_view()` powers the per-stock
  drill-down.
- **Index option matrix** (NIFTY / BANKNIFTY / FINNIFTY …) — the headline numbers.
- **Sector clustering** (static ~190-name F&O sector map → no fundamentals calls),
  **market-wide bias** (conviction-weighted long vs short pressure + a NIFTY-PCR overlay),
  and a **deterministic English institutional narrative**.

**Routes** (in `app.py`, both lazy-import `marketdata.oi_data`):
- `GET /api/fno/smart-money?tickers=A,B,C` — the assembled board (watchlist personalises
  the slice + delivery-boosted conviction). The bhavcopy is cached 4h in `oi_data`; the
  board is cached briefly here (`FNO_BOARD_TTL_SECS`, 600s) keyed by watchlist. Defensive:
  returns a **safe shell (HTTP 200)** rather than 500, so the UI degrades to an honest
  empty state.
- `GET /api/fno/option-chain/<symbol>` — per-name strike ladder + PCR/max-pain/walls; 404
  when the symbol has no F&O options. **Route count is now 50.**

**Frontend:** `fetchFnoSmartMoney()` + `_fno*` render helpers + `openFnoOptionChain()`
modal in `app-fno.js`. Lazy-loaded by `switchTab('fno')`, 60s client throttle. Hero with a
**bias gauge**, the index matrix, the 2×2 quadrant tables (click a row → the broker-style
option-chain modal: diverging CE/PE OI ladder with ATM + wall tags), unusual-OI + delivery
lists, diverging **sector bars**, and a bulk/block deals table. Watchlist names get a ★.
Degrades to an honest empty/error state (never a broken skeleton) when the bhavcopy is
unreachable — common outside the ~7-8 PM IST publish. The view + nav-dropdown UI were
verified via the static-harness + Claude Preview workflow (see Development Notes).

**Tests:** `tests/test_fno_engine.py` (27 cases — buildup routing, PCR/max-pain/walls math,
conviction, sector map, board assembly, safety, + the bhavcopy parser vs a synthetic UDiFF
sample). Env knob: `FNO_BOARD_TTL_SECS` (600); the engine's caps are module constants in
`fno_engine.py` (kept there to keep it import-pure).

### F&O freshness layer — persistence + day-over-day diff + Angel One intraday OI

The base board is **end-of-day** (the bhavcopy changes once/day, ~7-8 PM IST). Five
features make it feel live **honestly** (no fake intraday jitter). All reversible/defensive.

1. **Snapshot persistence (`#2`).** After a successful parse, `oi_data` upserts the parsed
   `{futures, options}` JSON into the **`fno_snapshot`** table (keyed by `bhavcopy_date`,
   last `FNO_SNAPSHOT_KEEP`=3 kept). On a cold start / NSE-unreachable window,
   `_load_latest_persisted()` restores the last good snapshot so the board renders instantly
   (source flips to `eod_restored`, NSE retried after `FNO_PERSIST_RETRY_SECS`=1800 instead
   of the full 4h). `get_prev_snapshot()` exposes the **previous trading day** as the diff
   baseline. Disable with `FNO_PERSIST_DISABLED=1`.
2. **Day-over-day diff (`#4`).** `fno_engine.diff_snapshots(curr, prev)` (pure) tags each
   futures name `vs_prev = {flipped, is_new, buildup_prev, oi_delta_pct}` and a top-level
   `changes` summary. `build_smart_money_board(..., prev_snapshot=)` attaches these to rows
   (the route passes `oi_data.get_prev_snapshot(before_date=...)`). Frontend shows **NEW /
   FLIPPED** chips + a "N flipped · M new vs <date>" pill. Tested in `tests/test_fno_diff.py`.
3. **Honest labeling + countdown + auto-poll (`#1`/`#3`).** `app-fno.js` `_fnoRenderMeta`
   shows an **END-OF-DAY** pill + "As of <date> close" + a **live countdown** to tonight's
   ~19:30 IST publish (or a green **LIVE · HH:MM IST** pill when intraday is on). The F&O tab
   **auto-polls** every 3 min while visible (paused when hidden; `switchTab` wrapper, like
   `app-calendar.js`). The board also carries **`served_at`** → a **"Refreshed HH:MM:SS IST"**
   stamp (when the data was last pulled — survives the cache, so it's honest on a cache hit),
   and **`intraday_status`** (`angel_fno.status()` → `off`/`closed`/`building`/`live`/
   `unavailable`) → a status pill: a spinning **"Building live data…"** while the first
   intraday snapshot is assembling, **"Live data unavailable here · showing end-of-day"** when
   builds keep failing (e.g. Angel blocked from a datacenter IP — 2+ consecutive fails), or
   **"Live OI resumes at market open"** off-hours. The route **does NOT cache a `building`
   board** and the frontend retries every **15 s** while building, so it flips to LIVE within
   seconds of the background build finishing.
4. **Angel One intraday OI (`#5`) — `marketdata/angel_fno.py`.** When **enabled**, replaces
   the EOD futures with **live `opnInterest`** from Angel One SmartAPI FULL-mode quotes and
   overlays live **index** option chains (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY); stock option
   chains stay EOD. Intraday **OI change** is measured vs the persisted EOD baseline (#2), so
   buildups/bias/unusual-OI move through the session. **Stale-while-revalidate**: a request
   never blocks on Angel — it serves the cached intraday snapshot (`ANGEL_FNO_TTL_SECS`=180)
   and triggers ONE background refresh; the first poll shows EOD, the next shows LIVE. The
   board cache shortens to `FNO_BOARD_TTL_LIVE_SECS`=60 when live so updates flow.
   - **Primitives** live in `angelone_shim.py`: `load_fno_scrip()` indexes the **NFO** segment
     of the scrip master (futures/options tokens, **strike is ÷100 paise**, expiry `DDMMMYYYY`),
     `nfo_front_future_tokens()` / `nfo_front_option_tokens()`, and `get_full_quotes()` (batch
     FULL quote, **≤50 tokens/request**). The pure `assemble_futures` / `assemble_index_chain`
     are unit-tested (`tests/test_angel_fno.py`).
   - **OFF by default.** Needs the four `ANGELONE_*` creds **and** `ANGEL_FNO_ENABLED=1`.
     ⚠️ **Angel One blocks datacenter IPs** (same reason the shim falls back to Yahoo), so
     intraday works on a **local/residential IP** but **auto-falls back to EOD on Render**.
     `get_fno_raw_snapshot()` returns `source: 'intraday'|'eod'|'eod_restored'` + `as_of`
     so the UI (and a curl) can tell which is live.

**New env knobs:** `ANGELONE_API_KEY/CLIENT_ID/PIN/TOTP_SECRET` (Angel creds),
`ANGEL_FNO_ENABLED` (0), `ANGEL_FNO_TTL_SECS` (180), `FNO_BOARD_TTL_LIVE_SECS` (60),
`FNO_PERSIST_DISABLED` (0), `FNO_SNAPSHOT_KEEP` (3), `FNO_PERSIST_RETRY_SECS` (1800).

### F&O v2 — institutional upgrades (best-in-class pass)

A research + adversarial-review workflow (benchmarking Sensibull / Opstra / QuantsApp) drove
a major upgrade — all still **deterministic, EOD, zero keys**:

- **Implied Volatility + Greeks** — new pure module `signals/options_math.py`: per-strike IV
  via a **Black-76** solver (Newton + bisection, intrinsic-floor gate) priced off the
  **futures forward** (so no dividend-yield guess), using the option **settlement price**
  (`SttlmPric`, fallback `ClsPric`). Adds Delta / Gamma / Theta(per-day) / Vega(per-1%),
  **ATM IV**, and **IV skew** (OTM put − call). The option-chain modal now shows the IV smile
  + Delta (hover) beside OI. Env knob `IV_RISK_FREE_RATE` (0.065). Round-trip + Greeks-identity
  unit-tested (`tests/test_options_math.py`). ⚠️ r is NOT in Black-76 d1 (it only discounts).
- **FII/DII/Pro/Client participant positioning (the literal smart money)** — new
  `oi_data.get_participant_oi()` fetches `nsccl/fao_participant_oi_<DDMMYYYY>.csv` (⚠️ date is
  `%d%m%Y`, NOT the bhavcopy's `%Y%m%d`; tab-stripped headers). `_participant_positioning()`
  derives FII net index-futures (the headline directional gauge) + per-cohort long-share +
  option call/put writing read → the lead **FII / DII Positioning** panel.
- **Futures basis** (futures − spot) + **rollover %** (next/(front+next) OI) — `IDF` index
  futures are now parsed; surfaced per name and on the index cards.
- **Deterministic setups** (`suggest_setup()`): regime → named bias + key levels
  (support = put wall, resistance = call wall, magnet = max-pain) + IV-rich/cheap overlay.
  Framed as bias/levels, NOT advice (honesty contract).
- **Ranked OI walls** (top-3/side + fresh-writing flag), an **India VIX** context tile (from
  `MacroDataTracker`), a **data-age + END-OF-DAY** trust label, and a **mobile card-view** for
  the quadrant tables (`.fno-table` `@media`).
- **Two correctness fixes:** conviction price-confirmation is now **directional** (a move that
  contradicts the buildup adds nothing); a brand-new/all-fresh futures contract no longer reads
  `ΔOI 0%` (flagged + surfaced instead of vanishing). Sector map de-duped (ABFRL) and the inert
  BSE `SENSEX`/`BANKEX` dropped from `INDEX_SYMBOLS`.
- ⚠️ **Deliberate non-goals** (need a paid/real-time feed; conflict with the IP block + EOD
  ethos): live intraday option chain / real-time Greeks. **IV Rank/Percentile** + PCR-trend
  need a stored ~1yr ATM-IV history — a clean follow-up (log daily, then rank).
- **Route count unchanged (47)** — the FII panel + IV all ride the existing board/chain
  routes; participant OI is fetched server-side inside `/api/fno/smart-money`. Tests:
  `tests/test_fno_advanced.py` (directional conviction, max-pain tie, basis/rollover, IV
  attach, FII net, setups, ranked walls).

## The Exchange Filing Alerts (corporate-actions radar)

A dedicated **Filings** tab — sitting in the nav **between Signal Terminal and Track
Record** — that surfaces the market-moving corporate filings companies report to the
exchange and **decodes each into plain English a normal investor understands**. Nine
material event types are detected: **promoter pledge · insider buy/sell · resignation ·
acquisition / M&A · order win · rating change · dividend · split · bonus**. Like Risk
Radar / Ripple 2.0 / F&O it is **purely deterministic — NO Gemini/LLM call** (zero keys,
reproducible, cacheable, never hallucinates a ticker).

- **Classifier engine:** `newsproc/filing_classifier.py` — **pure** (stdlib `re` only).
  `classify_filing(text, category, subcategory)` runs ordered, priority-ranked detectors
  (most-specific first, so "board to consider dividend **and bonus**" buckets as the more
  material *bonus*) and returns `{type, type_label, impact, severity, severity_rank,
  explanation, detail, headline}` — or `None` if the line isn't one of the nine types.
  **`impact`** (positive/negative/neutral) is the *typical* reaction to that **kind** of
  event (pledge-created → negative, pledge-released → positive, insider-buy → positive,
  rating-downgrade → negative-high, order-win → positive, etc.), framed in the UI as
  "what this usually means", NOT advice/prediction; unknown directions resolve to neutral.
  Best-effort `detail` extraction (₹ dividend per share, pledge %, order/deal value with
  cr/lakh scaling, split/bonus ratio, rating agency + direction). Guards: a **SEBI/court/
  NCLT order** is never mis-read as an "order win".
- **Data sources (defensive, two-tier):**
  1. **Primary — BSE structured corporate filings** (canonical). `fetch_bse_filings()` in
     `app.py` pulls the same `api.bseindia.com` announcements API as
     `fetch_bse_announcements` but returns the **structured** fields (company, scrip,
     category, subcategory, datetime, PDF link) and does **not** pre-filter — the
     classifier decides materiality. Links to the actual BSE filing PDF.
  2. **Secondary — already-scraped catalyst news** (resilience). When BSE is flaky the feed
     still populates from the `news` table (the regulatory / landmine RSS queries), each
     headline run through the same classifier; **requires a mapped ticker** (precision over
     recall) and links to a Google-News search. Marked `source:"News"` vs `"BSE Filing"`.
  - Merge + **dedup** (same stock + type + day → keep the canonical filing over news, then
    higher severity, then newer). Newest-first feed. Cross-source timestamps normalized to
    absolute epoch (BSE NEWS_DT is **IST**, news `created_at` is **UTC**).
- **Route:** `GET /api/filings?type=<key>&limit=N` (in `app.py`). Full classified set
  cached `FILINGS_TTL_SECS` (600); filtering by type happens on read. Defensive — each
  source isolated, returns a **safe shell (HTTP 200)** rather than 500, with a `degraded`
  `{bse, news}` flag. Response also carries `types[]` (label + per-type count) for the
  filter pills. **Route count is now 50.**
- **Frontend:** `app-filings.js` (chunk 11/11) — `fetchFilings()` + render helpers +
  `setFilingFilter()`. View `#view-filings`, lazy-loaded by `switchTab('filings')`, 60s
  client throttle over the 10-min server cache. Hero (live-alert count + freshness),
  type-filter pills with icons + counts, and a card grid (1-col → 2-col ≥980px): each card
  has a left accent bar + impact chip colored by direction (green/red/amber), a type badge
  with SVG icon, ticker chip, the raw filing headline, the plain-English explanation, an
  extracted-detail chip, source label, and a "View filing"/"Read more" link. Honest
  empty/error/filtered states (never a broken skeleton) + a "how to read this" disclaimer.
  `.fil-*` CSS block (token-based, mobile card stack). Verified via the static-harness +
  Claude Preview workflow.
- **⚠️ Same BSE caveat as the news pipeline:** the live BSE announcements API could not be
  exercised against live data in the build env — validate in production via the `[FILINGS]`
  logs + `feed_stats['bse_filings']`; the news-table fallback guarantees the feed is never
  empty when workers are running. Env knobs: `FILINGS_TTL_SECS` (600), `FILINGS_MAX` (90),
  `FILINGS_NEWS_LOOKBACK_DAYS` (5), `BSE_ANNOUNCEMENTS_ENABLED` (shared with the news path).
- **Tests:** `tests/test_filing_classifier.py` (23 cases — every type, buy-vs-sell &
  upgrade-vs-downgrade direction, pledge release/invocation, priority/overlap, the
  SEBI-order guard, category-hint use, figure extraction, empty/None safety).

### Filings: 24/7 pull + click-to-explain (added)

- **24/7 background pull.** `filings_worker()` (in `app.py`, registered in
  `start_background_workers`, heartbeat key `filings`, stall budget 2h) re-runs
  `_collect_exchange_filings()` every `FILINGS_REFRESH_MIN` (15m) and stores it in
  `_FILINGS_CACHE`, so the feed stays warm and refreshes **without** a tab open (instead of
  pulling only on-demand). No Gemini keys. ⚠️ On the Render free tier the process sleeps when
  idle, so "24/7" = "whenever the instance is awake" (the market-hours keep-alive covers the
  session); a truly always-on feed needs the paid plan. Disable with `FILINGS_WORKER_DISABLED=1`.
  The frontend also **auto-polls** every 3 min while the Filings tab is visible (paused when
  hidden) — `switchTab` wrapper like F&O/Calendar.
- **Click-to-explain ("why this matters").** Each alert is now clickable → a **detail modal**
  (`openFilingDetail` / `_filEnsureModal` in `app-filings.js`, `.fil-modal`/`.film-*` CSS).
  It shows the plain-English meaning, the **cause→effect mechanism** (how that event type
  actually moves the stock), a **"what to watch next"** checklist, a key-figure chip, the
  source-filing link, and an honesty disclaimer. Content is **deterministic, no LLM**:
  `filing_classifier.explain_filing(type)` returns `{mechanism, watch[], caveat}` from the
  module-level `FILING_MECHANISMS` table (one entry per type) + a shared `FILING_DISCLAIMER`.
  `/api/filings` now also returns `explainers` (per-type deep map) + `disclaimer`, so the
  modal needs no extra request. Tested in `tests/test_filing_explain.py`. New env knobs:
  `FILINGS_WORKER_DISABLED`, `FILINGS_REFRESH_MIN` (15).

## The Beginner Explain-Layer (glossary tooltips)

A site-wide **deterministic glossary** (no LLM) so a normal investor can learn the jargon
in place. New chunk **`app-glossary.js`** (13/13) holds a `JARGON` map (`key → {term, short,
long}`) + **one delegated tooltip** that fires for any element with class `gloss`
(`data-term="<key>"`) — hover on desktop, tap on mobile. Render a term with the global
helper **`glossTerm('PCR', 'pcr')`** → a faint dotted label + a `?` chip; unknown keys fall
back to the plain label, so it's safe to sprinkle anywhere.

- **Covered so far (highest-jargon surface first):** the F&O **option-chain modal** stats
  (PCR / ATM IV / IV skew / max pain / spot-vs-pain / call & put walls), the F&O **index
  matrix** (PCR / max pain / walls / ATM IV / basis), and the F&O hero (**India VIX**, long/
  short buildup, **bias score**). Extending to the Signal Terminal (conviction / ATR / hit
  rate), Macro Pulse, and Risk Radar dims is now trivial — just wrap the label in
  `glossTerm()`; the infra (map + tooltip + `.gloss`/`.gloss-tip` CSS) is shared.
- **Honesty fix (overlapping quick win):** the **Macro Impact Flow** panel's pulsing emerald
  `live-dot` + **"AI mapped"** badge (misleading — the content is rule-based now that AI is
  paused) was replaced with a plain **"Likely transmission path"** label. The "Plain English
  Decode" panel was left (its header is honest; it already falls back to `explanation` /
  deterministic text). The map/pathway code (`app-news.js`) already builds real `TICKER
  (Direction)` steps from `affected_stocks` when present.
- **Registering a new chunk** (done for `app-glossary.js`): `index.html` script tag +
  `sw.js isStaticAsset` regex + the `/app-` prefix in `app.py _CACHE_RULES` already covers
  it; bump `?v=` / `CACHE_VERSION`. No backend, no keys.

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

- **Frontend**: No deploy build step. Edit `frontend/index.html`, the `frontend/app-*.js` chunks, `frontend/styles.css` directly. Flask serves via `static_folder`. Browser refresh fetches latest.
  - ⚠️ **Tailwind is PRECOMPILED, not the Play CDN.** The render-blocking `cdn.tailwindcss.com` script (~115KB gzip of JS + a runtime DOM-scan/compile pass on every load) was replaced — commented out in `index.html` — by a committed static stylesheet **`frontend/tailwind.built.css`** (linked right AFTER `styles.css` to preserve the original cascade, where Tailwind utilities sit after styles.css and styles.css `!important` rules still win). **FOOTGUN:** if you add a NEW Tailwind utility class anywhere (HTML or a chunk), you MUST regenerate or it silently renders unstyled. Regenerate from the project root:
    ```
    npx tailwindcss@3 -c scratch/tailwind/tailwind.config.js -i scratch/tailwind/input.css -o frontend/tailwind.built.css --minify
    ```
    The generation config (`scratch/tailwind/`, gitignored) mirrors the old inline `tailwind.config` (cyan/blue/indigo/emerald/green/slate→champagne remap + Space Grotesk). Bump the `?v=` query + `sw.js` `CACHE_VERSION` after regenerating, and register the file in `sw.js` `isStaticAsset` (already done) + an `app.py` `_CACHE_RULES` entry (already `immutable`). **Rollback:** delete the `tailwind.built.css` `<link>` and un-comment the CDN block in `index.html`.
  - **Loading-performance pass (`al-v37`).** Static delivery was already strong (Brotli on Render: `styles.css` 201→40KB, `index.html` 119→21KB wire; assets `immutable` 1yr-cached; preconnect + font-preload; Tailwind CDN removed). The slow paths were **cold-load API latency** and **wasted work on every page load**, fixed by: (1) **`app-macro.js` no longer auto-fetches on page load** — it used to fire `fetchMacroPulse()` at module-exec (defer→`readyState==interactive`) even with the macro tab hidden, burning 2 API calls + a perpetual 90s poll on *every* load; now the poll is **visible-only** (`_mpTabVisible()` via `offsetParent`), and `switchTab('macro-pulse')` still loads it on tab-open. (2) **`/api/indices` parallelized** — the 4 indices resolve concurrently in a 4-thread pool (`_resolve_one_index` closure + `executor.map`, order preserved) instead of up to ~4 serial 8s-timeout fallbacks → ~1.4s→~0.4s cold; `@route_cache` raised 25s→60s to match `_INDEX_CACHE`. (3) **`get_top_news` quote pre-warm** — `attach_market_change_percentages(..., prewarm=True)` fetches the affected-stock quotes concurrently (workers fetch-and-return, main thread assigns; dedup preserved) so the serial assignment loop hits cache; **only `get_top_news` opts in** (the `/api/news/all` path stays serial to reuse its cross-article cache). (4) **SW app-shell** — `sw.js` `STATIC_PRECACHE` now includes `'/'` so a repeat visit on a slow/just-woken instance paints the nav+skeleton instantly from cache while `htmlNetworkFirst()` still revalidates (network-first semantics unchanged). ⚠️ The **dominant** real-world delay remains the **free-tier cold start** (instance sleeps after ~15min) — that's infra; the market-hours keep-alive covers the NSE session, and a 24/7 keep-alive would fix it but runs workers (Gemini keys) 24/7. Verified via the audit workflow (14 agents, adversarially verified — it corrected several "high" impact claims to medium and rejected dynamic-injection chunk lazy-loading as conflicting with the byte-identical-concat constraint for low real gain).
  - **Design system**: tokens live in `styles.css :root` — surfaces, borders, text-opacity steps, market semantics (`--green/--red/--amber`), a champagne brand accent (`--accent`), a radius scale (`--radius-sm…pill`), a **spacing scale** (`--space-1…8`, 8pt grid), a **type scale** (`--text-2xs…3xl`), motion (`--ease-out/--ease-spring/--duration-*`), and a **shadow elevation scale** (`--shadow-sm…xl`). **Prefer these tokens over raw px/hex.** `border-radius` was migrated onto the radius scale wherever a raw value matched a token exactly (6/8/12/16px → `--radius-sm/md/lg/xl`, 99px → `--radius-pill`); a few intentional one-off radii (10/14/22px) remain by design. The `--text-*` scale is the canonical set to adopt for **new** UI — existing font-sizes were **not** force-migrated (that changes visuals and there's no local preview to verify against). Keyboard focus uses one global `:focus-visible` ring (`--focus-ring`); don't reintroduce per-element `outline` hacks. Status dots use `.pill-dot` (inherits `currentColor`) instead of 🟢/🔴 emoji — keep iconography as **SVG/CSS, never emoji** (visible UI is emoji-free; only standard close glyphs / a hidden connection-error icon remain). `<head>` has favicon + apple-touch-icon + manifest + theme-color + **Open Graph/Twitter** link-preview meta. ⚠️ The ~67 `!important` rules were left intentionally — most override the Tailwind CDN utilities, and blind removal risks cascade regressions that can't be verified without a working preview (the in-repo Chrome MCP is a *remote* browser and can't reach `localhost`).
  - **Macro Pulse theming**: the Macro Pulse view (`#view-macro-pulse`, the `.mp-*` block in `styles.css`, rendered by `app-macro.js`) was rebranded off a legacy **violet/purple** palette onto the champagne `--accent` + market-semantic/text tokens, and its emoji (methodology bar, table legend, `⚠️`/`●`/`ⓘ` glyphs) replaced with inline SVG — so it now matches the rest of the app. The **alert cards** (`_mpRenderAlertCard`) were then rebuilt for balance: a left-aligned header (the `<button>`'s default `text-align:center` is reset on `.mp-alert-card`) with the % move as a tinted pill, a structured `LAST`/`PREV` stat strip (`.mp-alert-quote`, replacing the inline "Last: x | Prev: y" text), and a footer pinned via `margin-top:auto` so every card in a row is **equal-height and aligned** regardless of predictor count. `_mpFmtDetected()` fixes the old **"Detected Invalid Date"** (caused by appending `Z` to a space-separated datetime) — it normalizes the space→`T`, treats naive times as UTC, and renders true IST (`timeZone: 'Asia/Kolkata'`). **Shock detection is volatility-normalized (σ), not fixed-%:** `MacroDataTracker` computes each instrument's realized daily vol from 6mo of closes and a z-score `sigma = move/vol`, classified at **≥2.5σ (Significant) / ≥3.5σ (Major)** — one threshold correct across all asset classes (the old fixed-% `SHOCK_THRESHOLDS` is the automatic fallback when history is thin). Cards/table show the σ chip (red ≥3.5σ) + percentile-vs-history; `_mpFmtDetected()` fixes the old "Detected Invalid Date" (true IST). Env knobs (all revertable): `MACRO_SHOCK_MODE` (`sigma`|`pct`), `MACRO_SIGMA_SIGNIFICANT` (2.5), `MACRO_SIGMA_MAJOR` (3.5), `MACRO_VOL_WINDOW` (60), `MACRO_ABS_FLOOR_PCT` (0.1), `MACRO_HISTORY_RANGE` (6mo). ⚠️ This changes **live shock detection on Render** (workers run there) — set `MACRO_SHOCK_MODE=pct` to revert. The premium **Ripple 2.0** modal (`.r2-*`) is the deterministic cascade opened from its alert cards. ✅ A **local visual preview IS possible** (despite the remote-Chrome caveat above): render a static harness — real `styles.css` + the target `app-*.js` chunk + a stubbed `window.fetch` returning mock API JSON — served via `python -m http.server` and screenshotted through the **Claude Preview** MCP (add a `?v=` cache-bust on the asset links and `location.reload()` after editing). This is how the rebrand + Ripple 2.0 UI were verified.
  - **Empty / error states**: render an intentional state, never leave skeleton rows or a misleading message. The `.term-empty` pattern (centered icon + `.term-empty-title` + `.term-empty-sub`, token-styled) is the template — see `renderTerminal()` / the `fetchTerminalData()` catch in `app-terminal.js`, which distinguish **truly-empty** ("No active signals right now…") from **filtered-empty** ("No signals match this filter") from **fetch error** ("Couldn't reach the signal engine — retrying"). Perpetual skeletons on a failed/zero fetch read as *broken*; this is the biggest perceived-professionalism lever given free-tier sleep makes "empty" the common state.
  - **Numbers**: use `font-variant-numeric: tabular-nums` for any changing figure so columns/prices don't jitter — applied to `.font-mono` and `.terminal-table` cells. Prefer the `.font-mono` data utility for prices, %, P&L, confidence.
  - **Removed gimmick motion** (read as "vibe-coded", not premium): the cursor-glow trail and scroll-linked KPI parallax were deleted from `app-premium.js`, and the full-card 3D tilt + magnetic-button pull were removed from `initPremiumInteractions()`. The subtle per-panel glass spotlight, digit-flip, skeleton-swap, stagger, and ticker-hover preview were **kept** (purposeful micro-interactions). Don't re-add cursor trails / parallax.
  - **app.js chunk split**: `app.js` was split into 12 ordered `app-*.js` chunks (see structure tree; `app-fno.js` is the F&O view, loaded between `app-macro.js` and `app-calendar.js`). They are **classic scripts sharing one global scope**; `index.html` loads them with `defer` in document order, so concatenating them top-to-bottom reproduces the original `app.js` byte-for-byte. Functions may call across chunks (resolved at runtime), but **module-level state must stay in original load order** — don't reorder the `<script>` tags. When adding a chunk or renaming, update three places: `index.html` script tags, the `sw.js` `isStaticAsset` regex (which **enumerates each chunk name** — add the new one), and the `/app-` rule in `app.py` `_CACHE_RULES` (a **prefix**, so it auto-covers new chunks). Bump the `?v=` query + `sw.js CACHE_VERSION` on any chunk change so caches purge.
- **Backend**: Reload Flask dev server to pick up Python changes (`CTRL+C`, restart `python backend/app.py`).
- **`print()` is globally `safe_print`** (top of `app.py`): `_real_print = builtins.print` is captured first, then `builtins.print = safe_print` shadows it process-wide. So **every bare `print()` in any module** (workers, `performance_report`, etc.) is automatically guarded against I/O errors on a closed stdout (e.g. the Flask reloader / gunicorn worker recycle) — no need to hunt down call-sites. `safe_print` calls `_real_print` directly to avoid infinite recursion once `print` points back at itself.
- **Database**: SQLite files (`news_cache.db`, `users.db`) are created on first run. Delete to reset.
- **API keys**: Always use environment variables (`.env`). Never hardcode in source.
- **Background threads** (all started by `start_background_workers`, unless `--workers-only` mode): AI news engine, yfinance price worker, `archival_worker` (90-day reversible archive), `news_prune_worker` (800/5-day feed prune), `calendar_worker` (every 30m — releases concluded calendar events + purges them after 2 days), plus macro warmer/shock workers, and `eval_labeler_worker` (every 6h — fills ATR outcomes for the append-only `signal_eval_log` eval ledger). Retention is owned by these workers — there is **no** per-cycle hard-delete anymore.
- **Market hours**: yfinance returns last available price outside NSE/BSE hours (9:15 AM – 3:30 PM IST). Live signals are most accurate during market hours.
- **Dedup (two layers)**: exact lowercase match (`SEEN_HEADLINES`) for identical headlines, PLUS a **fuzzy near-duplicate guard** — the incoming headline (punctuation/whitespace-normalized) is compared via `SequenceMatcher` against the last `DEDUP_WINDOW` (300) headlines and dropped if similarity ≥ `DEDUP_THRESHOLD` (0.85). Catches the same story reworded by another source ("Reliance surges 5%" vs "Reliance rises 5%"). Set `DEDUP_THRESHOLD=1.0` to disable fuzzy. ⚠️ Earlier docs claimed "75% vs 50 recent" but the code was exact-match-only until this was implemented (`_norm_headline` / `_is_near_dup_headline` in `app.py`).
- **News scraping robustness**: 68 sources — mainstream (ET, Moneycontrol, LiveMint, Business Standard, CNBC) + Google-News-scoped **sector** (banks, IT, pharma, auto, metals, power, infra, defence), **catalyst** (order wins, capex, QIP, buyback/dividend, broker target changes), and **regulatory/landmine** queries (promoter pledge, SEBI orders, auditor resignations, ASM/GSM, block deals, rating downgrades). ⚠️ Direct publisher RSS (Business Standard, Financial Express, Moneycontrol) **403/503s from datacenter IPs** (e.g. Render) — the Google-News-scoped queries are the reliable bulk on the server; prefer adding those, not more direct feeds. `HTTP_SESSION` has bounded status-only retry/backoff. `scrape_article_text` is thread-safe (`_ARTICLE_TEXT_CACHE_LOCK`), caches only success + permanent 4xx (transient 429/5xx retry next cycle), and falls back from `<p>` to `<div>/<article>` bodies. Unparseable RSS pub-times are now **skipped** (they no longer bypass the `NEWS_MAX_AGE_HOURS` staleness gate). 0-article cycles set `feed_health=zero_articles` in `/api/debug-worker-status`. `RECENT_SIGNALS` is capped (`RECENT_SIGNALS_CAP`, 10000).
- **News scraping (further hardening)**: RSS fetch uses **conditional-GET** (etag/Last-Modified via `RSS_CACHE`; toggle `RSS_CONDITIONAL_GET`) to skip unchanged feeds (HTTP 304), with **rotated User-Agents** (`_USER_AGENTS` / `_ua()`). **Per-feed health** (`FEED_STATS`: fetches / articles / not_modified / failures / last_error) is exposed at `/api/debug-worker-status` → `feed_stats`. Naive (tz-less) pub-times are assumed **IST**, not UTC (`_assume_tz`, env `NAIVE_PUBTIME_TZ` default `IST`) — so a `10:00` IST article resolves to `04:30 UTC` instead of looking ~5.5h fresher. Articles stuck `ai_status='pending'` past `PENDING_TIMEOUT_HOURS` (24) are aged to `stale_pending` so a Gemini outage can't grow the backlog forever. HTML is capped at 3 MB before BeautifulSoup parsing.
- **Direct regulatory sources** (source-of-truth, no aggregation lag): the feed list now includes **direct RBI RSS** (press releases + notifications) and **SEBI RSS** (`sebirss.xml`) — both probed reachable from the server. **BSE corporate-filing announcements** are pulled via `fetch_bse_announcements()` (JSON API at `api.bseindia.com`, keyword-filtered to pledge / rating / board-outcome / auditor / M&A catalysts; defensive, returns `[]` on any failure; toggle `BSE_ANNOUNCEMENTS_ENABLED`). ⚠️ **NSE's own API blocks datacenter IPs** (timed out from the server) — NSE filings / ASM-GSM real-time need a paid data feed or residential proxy, not a server-side scrape. ⚠️ The BSE fetcher's live-record parsing could **not be verified in the build environment** (its network returned no BSE records for any date); validate in production via `feed_stats['bse_announcements']` and the `[BSE]` worker logs. **GDELT** (`fetch_gdelt_news`) adds free near-real-time global news (~15-min index), called once per cycle with **auto-backoff on HTTP 429** (`GDELT_BACKOFF_SECS`); toggle `GDELT_ENABLED`, tune `GDELT_QUERY` / `GDELT_TIMESPAN`. artlist mode returns title+url only — the existing scraper fetches the body downstream.
- **NewsAPI.ai / Event Registry — finance-only** (`fetch_eventregistry_finance_news`): POSTs to `eventregistry.org/api/v1/article/getArticles`, **hard-filtered to the Business/Finance category** (`categoryUri="news/Business"`) + India-market keywords, so only finance news enters the pipeline. The free plan has a **limited monthly token quota**, so calls are **throttled to one per `EVENTREGISTRY_MIN_INTERVAL_SECS`** (default 30m) — NOT every cycle — with the next-call gate armed *before* the request so a hang can't burn tokens. Heavy syndication in results is collapsed by the fuzzy-dedup guard. Defensive: returns `[]` on any failure (incl. missing key). **The API key lives ONLY in the `EVENTREGISTRY_API_KEY` env var — never in source or `.env` in git.** Set it in the **Render dashboard env** for production; if unset the fetcher silently no-ops. Env knobs: `EVENTREGISTRY_API_KEY` (required), `EVENTREGISTRY_ENABLED` (1), `EVENTREGISTRY_MIN_INTERVAL_SECS` (1800), `EVENTREGISTRY_COUNT` (50, capped 100), `EVENTREGISTRY_CATEGORY` (`news/Business`), `EVENTREGISTRY_KEYWORDS` (comma list, OR'd), `EVENTREGISTRY_LANG` (`eng`). Surfaced at `/api/debug-worker-status → feed_stats['eventregistry']`.
- **Backend subpackages**: the modules extracted from `app.py` now live in four topical subpackages under `backend/` — `persistence/` (db, schema), `marketdata/` (market_calendar, macro_tracker, ticker_utils, oi_data), `newsproc/` (news_rules, news_data, calendar_seed, portfolio_data), `signals/` (prediction_models, technical_analysis). `app.py`, the shims, `whatsapp_sender.py`, and the dev/utility scripts stay at `backend/` root. **The Render entrypoint is unchanged** (`gunicorn --chdir backend … app:app`) — `--chdir backend` puts `backend/` on `sys.path`, so subpackages import as top-level packages (`from persistence.db import …`) and root shims (`import angelone_shim`) still resolve. Imports use **absolute** dotted paths (`from marketdata.ticker_utils import …`), never relative. ⚠️ When moving a module that resolves paths from `__file__` (only `persistence/db.py` does), adjust `_APP_DIR` so DB files still resolve to `backend/`.
- **Verifying any app.py / import change** (without spawning workers/network):
  ```bash
  cd backend && ALPHA_LENS_SKIP_AUTO_BOOTSTRAP=1 \
    "../.alpha-venv/Scripts/python.exe" -c "import app; print(len(list(app.app.url_map.iter_rules())), 'routes')"
  ```
  This catches circular imports / `NameError`s / bad subpackage paths that `py_compile` misses. `ALPHA_LENS_SKIP_AUTO_BOOTSTRAP=1` skips `_bootstrap_workers()` (the import-time thread launcher). Expect **51 routes**. Then run the test suite (`python -m unittest discover -s tests`) — **227 tests**.

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

## Project skills (`.claude/skills/`)

Project-local Claude Code skills live here and ship with the repo.

| Skill | Purpose |
|-------|---------|
| `honest-review` | An honest, anti-sycophantic reviewer for **code AND decisions**. Gives a blunt verdict (right / wrong / risky), backs every finding with evidence (`file:line`, a repro, a doc, or the project harness), calibrates **wrong vs risky vs taste vs right**, and — critically — **argues its case instead of caving**: it holds its ground under evidence-free pushback but concedes fast when genuinely refuted. Auto-triggers on "am I doing this right?", "is this a good approach?", "be honest", "poke holes in this", "push back on me", "should I do X or Y?", or a gut-check before committing. It defers to `code-review` (mechanical defect sweep / inline PR comments) and `security-review` (vuln audit). Grounded in the real project checks (37-route harness, the unit tests, retention/byte-identity rules). |

The skill's prompts, assertions, and a validation benchmark live under
`.claude/skills/honest-review/{SKILL.md, references/, evals/}`. The bulky
generated eval workspace (`*-workspace/`, incl. the static viewer HTML) is
gitignored — regenerate it with the skill-creator if you want to re-run the
benchmark. To tune the reviewer's bluntness, edit the "Why this exists" /
"Holding your ground" sections of `SKILL.md` — stance is a one-paragraph change.

## Deployment (Render)

The `render.yaml` file configures a Render web service:
- **Runtime**: Python
- **Build**: `pip install -r requirements.txt`
- **Start**: Gunicorn (1 worker, 4 threads) on port $PORT
- **Region**: Singapore
- **Plan**: Free tier (512 MB RAM)

Database: PostgreSQL (optional, configured via DATABASE_URL env var).

**Live URL:** `https://alpha-lens-qvxw.onrender.com` (Render appends a random
suffix to the service name).

### ⚠️ Free-tier spin-down throttles signal generation

The free web plan **sleeps the instance after ~15 min with no inbound HTTP
traffic**, and the AI-news/signal workers live *inside* that web process — so
while it's asleep, **no signals are generated**, and each wake-up is a cold start
that **wipes all in-memory state** (dedup cache, `SELECTION_FUNNEL` counters,
`RECENT_SIGNALS`). Symptom: very few signals over a day + `ai_news.cycles_completed`
stuck low in `/api/debug-worker-status` (a continuously-up instance would show
hundreds). This is the dominant cause of "barely any signals on production", NOT
the selection filters.

**Mitigation in use — market-hours keep-alive (free).** An external cron
(cron-job.org) GETs **`/api/health`** (lightweight, spends no Gemini keys) every
10 min, **only Mon–Fri 09:00–15:50 IST** (`*/10 9-15 * * 1-5`, timezone
`Asia/Kolkata`). This keeps the dyno awake across the NSE session (warm before the
9:15 open, alive through the 15:30 close) so workers run when signals matter, and
lets it sleep off-hours so Gemini keys aren't burned 24/7. The first ~09:00 ping
each day returns 503 during cold start (expected — disable that job's failure
alerts). **Do not delete this pinger** without a replacement, or production goes
back to near-zero signals. Durable alternatives (cost money): run workers as a
dedicated Render **Background Worker** (`app.py --workers-only`), or upgrade the
web service off free.

> Note: production env vars are set in the Render dashboard, NOT from the local
> `.env`. The local key-saving flags (`ALPHA_LENS_SKIP_WORKERS`,
> `ALPHA_LENS_SKIP_AUTO_REPAIR`) do **not** apply on Render — workers run there.
