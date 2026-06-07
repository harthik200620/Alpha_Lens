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

**Frontend:** Single-file HTML (`frontend/index.html`) + vanilla JS. No build step. The old monolithic `app.js` was split into **9 ordered chunks** (`app-core.js` → `app-calendar.js`, see below) plus `frontend/stocks.js`. Flask serves these from `static_folder='../frontend'`.

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
`ticker_utils`, `news_rules`, `news_data`. `tests/__init__.py` puts `backend/`
on `sys.path` so the sibling modules import regardless of CWD. Tests are pure
(no network/DB/threads), so they run in well under a second.

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
│   │   ├── macro_tracker.py     #   MacroDataTracker — commodity/FX/rates snapshot + shock detection
│   │   ├── ticker_utils.py      #   Ticker normalization + news-candidate screening helpers
│   │   └── oi_data.py           #   Open-interest data fetch (lazy-imported by signals/technical_analysis)
│   ├── newsproc/                # ── Subpackage: news processing (pure) ──
│   │   ├── news_rules.py        #   Rule-based news classification + STOCK_KEYWORD_MAP
│   │   ├── news_data.py         #   Static data tables (MACRO_IMPACT_MAP, keyword lists, ticker sets)
│   │   ├── calendar_seed.py     #   Macro/economic-events calendar seed (CALENDAR_EVENTS_SEED)
│   │   └── portfolio_data.py    #   Portfolio-assistant ticker-detection lookup tables
│   ├── signals/                 # ── Subpackage: signal generation ──
│   │   ├── prediction_models.py #   Multi-model ensemble (Sentiment, Historical, Sector, Event)
│   │   ├── technical_analysis.py#   RSI, SMA, Bollinger Bands, market regime detection
│   │   ├── calibration.py       #   Score→P(win) calibration map + meta-label gate (levers #1/#4)
│   │   ├── calibration_map.json #   Isotonic score→P(win) map (refreshable; built by scratch/ pipeline)
│   │   └── ripple_engine.py     #   Ripple 2.0 — pure deterministic 5-dimension macro cascade (beta-based)
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
│   ├── app-core.js              # Globals, Google/OTP auth, tab shell, date utils (chunk 1/9)
│   ├── app-news.js              # fetchLiveNews, dashboard render, badges, hero, archive, Command Center (2/9)
│   ├── app-stocks.js            # Watchlist search, portfolio assistant (3/9)
│   ├── app-market.js            # Major stocks, indices, smart polling (4/9)
│   ├── app-premium.js           # Animations, cursor trail, parallax, flip, ticker hover (5/9)
│   ├── app-terminal.js          # Stock drawer, signal terminal, backtest, notifications (6/9)
│   ├── app-ripple.js            # Ripple graph render (7/9)
│   ├── app-macro.js             # Macro Pulse view (8/9)
│   ├── app-calendar.js          # Economic-events calendar (9/9)
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
| `marketdata/macro_tracker.py` | `MacroDataTracker` — live commodity/FX/rates snapshot + quantitative shock detection |
| `marketdata/ticker_utils.py` | Ticker normalization + news-candidate screening — `normalize_ticker`, `candidate_quality_score`, etc. Imports `newsproc.news_rules`/`newsproc.news_data` |
| `marketdata/oi_data.py` | Open-interest fetch; lazy-imported by `signals/technical_analysis.py` |
| `newsproc/news_rules.py` | Pure rule-based classification — keyword filter, sentiment lists, `classify_category`, `STOCK_KEYWORD_MAP` |
| `newsproc/news_data.py` | Pure static data tables — `MACRO_IMPACT_MAP`, materiality/noise keyword lists, ticker-parsing sets |
| `newsproc/calendar_seed.py` | Pure static seed for the macro/economic-events calendar (`CALENDAR_EVENTS_SEED`) |
| `newsproc/portfolio_data.py` | Pure lookup tables for the portfolio assistant's ticker detection |
| `signals/prediction_models.py` | 5-model ensemble predictor — sentiment, historical, sector, event, aggregation |
| `signals/technical_analysis.py` | RSI, SMA, Bollinger Bands, volume analysis, market regime detection. Now also returns `avg_volume_20d` (for the liquidity filter) |
| `signals/calibration.py` | Maps ensemble score → empirical P(target before stop); meta-label gate (levers #1/#4). Loads `calibration_map.json`; gate OFF by default (`CALIBRATION_GATE_ENABLED`) |
| `signals/ripple_engine.py` | **Ripple 2.0** — pure, deterministic 5-dimension macro cascade (direct/second-order/sector/portfolio/action-window) via signed betas. No LLM. `compute_ripple()`; served by `/api/macro/events/<id>/ripple2` |
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
| `ATR_STOP_MULT` / `ATR_TARGET_MULT` (+ `ATR_STOP_CAP_PCT` / `ATR_TARGET_CAP_PCT`) | 1.0 / 2.0 (2.5 / 5.0) | ATR stop & target width (2:1 R:R by default). Raise `ATR_STOP_MULT` to stop noise-whipsaw. |
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

## Health & worker liveness

Two endpoints expose background-worker state:

| Endpoint | Use it for |
|----------|-----------|
| `GET /api/health` | One-glance "is anything broken right now?". Returns `overall: "ok"\|"degraded"\|"down"` + a per-worker state (`ok`/`not_started`/`running`/`silent`/`stalled`) judged against a per-worker stall budget, plus Gemini-key counts and a DB probe. HTTP **503** when `overall=down` so uptime monitors can latch on the status. Use this for cron monitors and quick eyeball checks. |
| `GET /api/debug-worker-status` | Full per-worker dump — raw heartbeat fields, last cycle metrics (`last_scrape_count`, `last_save_count`, `last_news_moved`, `last_pruned_count`, etc.), last error + age. Use this when `/api/health` says something's wrong and you need the detail. |

Both read from the in-process `WORKER_HEARTBEAT` dict in `app.py`, populated by each worker per cycle (`_heartbeat(name, **fields)`). All seven workers — `ai_news`, `yfinance`, `macro_shock`, `archival`, `news_prune`, `eval_labeler`, `calendar` — write their start/finish/error timestamps. Per-worker stall budgets live in `_WORKER_STALL_BUDGET_SECS` and are tuned to each worker's natural cadence (e.g. archival's budget is 36h because it runs every 24h; calendar's is 3h for its 30m cadence).

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
  `0.55·avg_stock + 0.15·max_stock + 0.18·macro + 0.12·sector`. Route count is now **44** (Ripple 2.0 added one).
- **Frontend:** `loadRiskRadar()` / `renderRiskRadar()` + helpers (`_rrDimTile`,
  `_rrStockRow`, `_rrMeter`, `_rrSkeleton`, `_rrErrorState`) in `app-stocks.js`. Renders a
  hero (big score + level + summary + a LOW→HIGH meter), 6 dimension tiles (each with a
  bar + top contributing stocks/reasons), and a **Top risks by stock** ranking.
  **Lifecycle:** called from `switchTab('portfolio')` (lazy-load) and on every watchlist
  change (`saveWatchlist`, force-refresh); a 60s client throttle sits over the 30m server
  cache. **Degradation:** hidden until there's a watchlist AND a real score — a cold-start
  / zero-data fetch shows nothing rather than a broken shell; fetch errors show a retrying
  message. Styles: `.rr-*` block in `styles.css` (token-based, level-colored
  green/amber/red, responsive 2-col→1-col < 600px). **No holdings sizes** exist (the
  watchlist is `{ticker, name}` only), so the model is **equal-weight** — a quantity-aware
  weighting would be a follow-up. Env knobs: `RISK_RADAR_TTL_SECS`, `RISK_RADAR_MAX_TICKERS`.

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
  **Route count is now 44.**
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

- **Frontend**: No build step. Edit `frontend/index.html`, the `frontend/app-*.js` chunks, `frontend/styles.css` directly. Flask serves via `static_folder`. Browser refresh fetches latest.
  - **Design system**: tokens live in `styles.css :root` — surfaces, borders, text-opacity steps, market semantics (`--green/--red/--amber`), a champagne brand accent (`--accent`), a radius scale (`--radius-sm…pill`), a **spacing scale** (`--space-1…8`, 8pt grid), a **type scale** (`--text-2xs…3xl`), motion (`--ease-out/--ease-spring/--duration-*`), and a **shadow elevation scale** (`--shadow-sm…xl`). **Prefer these tokens over raw px/hex.** `border-radius` was migrated onto the radius scale wherever a raw value matched a token exactly (6/8/12/16px → `--radius-sm/md/lg/xl`, 99px → `--radius-pill`); a few intentional one-off radii (10/14/22px) remain by design. The `--text-*` scale is the canonical set to adopt for **new** UI — existing font-sizes were **not** force-migrated (that changes visuals and there's no local preview to verify against). Keyboard focus uses one global `:focus-visible` ring (`--focus-ring`); don't reintroduce per-element `outline` hacks. Status dots use `.pill-dot` (inherits `currentColor`) instead of 🟢/🔴 emoji — keep iconography as **SVG/CSS, never emoji** (visible UI is emoji-free; only standard close glyphs / a hidden connection-error icon remain). `<head>` has favicon + apple-touch-icon + manifest + theme-color + **Open Graph/Twitter** link-preview meta. ⚠️ The ~67 `!important` rules were left intentionally — most override the Tailwind CDN utilities, and blind removal risks cascade regressions that can't be verified without a working preview (the in-repo Chrome MCP is a *remote* browser and can't reach `localhost`).
  - **Macro Pulse theming**: the Macro Pulse view (`#view-macro-pulse`, the `.mp-*` block in `styles.css`, rendered by `app-macro.js`) was rebranded off a legacy **violet/purple** palette onto the champagne `--accent` + market-semantic/text tokens, and its emoji (methodology bar, table legend, `⚠️`/`●`/`ⓘ` glyphs) replaced with inline SVG — so it now matches the rest of the app. The premium **Ripple 2.0** modal (`.r2-*`) is the deterministic cascade opened from its alert cards. ✅ A **local visual preview IS possible** (despite the remote-Chrome caveat above): render a static harness — real `styles.css` + the target `app-*.js` chunk + a stubbed `window.fetch` returning mock API JSON — served via `python -m http.server` and screenshotted through the **Claude Preview** MCP (add a `?v=` cache-bust on the asset links and `location.reload()` after editing). This is how the rebrand + Ripple 2.0 UI were verified.
  - **Empty / error states**: render an intentional state, never leave skeleton rows or a misleading message. The `.term-empty` pattern (centered icon + `.term-empty-title` + `.term-empty-sub`, token-styled) is the template — see `renderTerminal()` / the `fetchTerminalData()` catch in `app-terminal.js`, which distinguish **truly-empty** ("No active signals right now…") from **filtered-empty** ("No signals match this filter") from **fetch error** ("Couldn't reach the signal engine — retrying"). Perpetual skeletons on a failed/zero fetch read as *broken*; this is the biggest perceived-professionalism lever given free-tier sleep makes "empty" the common state.
  - **Numbers**: use `font-variant-numeric: tabular-nums` for any changing figure so columns/prices don't jitter — applied to `.font-mono` and `.terminal-table` cells. Prefer the `.font-mono` data utility for prices, %, P&L, confidence.
  - **Removed gimmick motion** (read as "vibe-coded", not premium): the cursor-glow trail and scroll-linked KPI parallax were deleted from `app-premium.js`, and the full-card 3D tilt + magnetic-button pull were removed from `initPremiumInteractions()`. The subtle per-panel glass spotlight, digit-flip, skeleton-swap, stagger, and ticker-hover preview were **kept** (purposeful micro-interactions). Don't re-add cursor trails / parallax.
  - **app.js chunk split**: `app.js` was split into 9 ordered `app-*.js` chunks (see structure tree). They are **classic scripts sharing one global scope**; `index.html` loads them with `defer` in document order, so concatenating them top-to-bottom reproduces the original `app.js` byte-for-byte. Functions may call across chunks (resolved at runtime), but **module-level state must stay in original load order** — don't reorder the `<script>` tags. When adding a chunk or renaming, update three places: `index.html` script tags, `sw.js` `isStaticAsset` regex, and the `/app-` rule in `app.py` `_CACHE_RULES`. Bump the `?v=` query + `sw.js CACHE_VERSION` on any chunk change so caches purge.
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
  This catches circular imports / `NameError`s / bad subpackage paths that `py_compile` misses. `ALPHA_LENS_SKIP_AUTO_BOOTSTRAP=1` skips `_bootstrap_workers()` (the import-time thread launcher). Expect **44 routes**. Then run the test suite (`python -m unittest discover -s tests`).

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
