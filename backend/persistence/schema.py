"""
Database schema builders, extracted verbatim from app.py:
  * init_db()      — users table
  * init_news_db() — news / stock_impact / archives / calendar / macro tables
                     plus idempotent ALTER/CREATE INDEX migrations
                     (its nested run_query_safe swallows already-exists errors)

Depends only on db.py's connection helpers — no app import, no cycle. app.py
imports both back and calls them at startup.
"""
from persistence.db import connect_news_db, connect_users_db


def init_db():
    conn = connect_users_db()
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




def init_news_db():
    def run_query_safe(sql):
        try:
            conn = connect_news_db()
            c = conn.cursor()
            c.execute(sql)
            conn.commit()
            conn.close()
        except Exception:
            pass

    run_query_safe('''
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            headline TEXT NOT NULL,
            news_time TEXT,
            aam_janta_translation TEXT,
            macro_pathway TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    run_query_safe("ALTER TABLE news ADD COLUMN category TEXT DEFAULT 'General'")
    # Full article snippet (RSS summary / scraped body). UI shows this so the
    # user gets the whole article, while the AI screener only consumes a
    # tiny slice of it — decouples user-facing detail from token cost.
    run_query_safe("ALTER TABLE news ADD COLUMN body TEXT DEFAULT ''")
    # AI screening status — 'screened' (default, AI processed the headline)
    # vs 'pending' (saved during AI downtime; the rescreen pass picks these
    # up at the top of every cycle once a Gemini key is available again).
    # Default 'screened' so any pre-existing rows are not treated as pending.
    run_query_safe("ALTER TABLE news ADD COLUMN ai_status TEXT DEFAULT 'screened'")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_news_ai_status ON news(ai_status)")

    # ── "The Ripple" — propagation graphs for macro-grade big news ──
    # Stored separately so we can drop/regenerate without touching the
    # news table, and so the body row doesn't bloat with rare-but-large
    # JSON blobs. ripple_score 0-100; is_big_news triggers UI badge.
    run_query_safe('''
        CREATE TABLE IF NOT EXISTS news_ripple (
            news_id      INTEGER PRIMARY KEY,
            ripple_score INTEGER DEFAULT 0,
            is_big_news  INTEGER DEFAULT 0,
            ripple_json  TEXT,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_news_ripple_big ON news_ripple(is_big_news)")
    # Quick flag on the news row itself — avoids a JOIN on the hot listing
    # endpoint just to know whether to show the "View Ripple" badge.
    run_query_safe("ALTER TABLE news ADD COLUMN has_ripple INTEGER DEFAULT 0")

    # ── Macro Events: purely quantitative shock detection ──
    # Every time MacroDataTracker detects a 1d move past an instrument's
    # threshold, the macro_shock_worker writes a row here and triggers
    # a ripple-graph generation. No news involved.
    run_query_safe('''
        CREATE TABLE IF NOT EXISTS macro_event (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            instrument_key  TEXT NOT NULL,
            instrument_label TEXT,
            symbol          TEXT,
            shock_level     TEXT,
            change_pct_1d   REAL,
            last_price      REAL,
            prev_close      REAL,
            ripple_json     TEXT,
            detected_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at      TIMESTAMP
        )
    ''')
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_macro_event_detected ON macro_event(detected_at)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_macro_event_instrument ON macro_event(instrument_key)")
    # The /api/macro/events endpoint filters every request on `expires_at >= now`
    # so the active-window scan should hit an index. Defensive — the table is
    # tiny today, but every macro-pulse page-load goes through this WHERE.
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_macro_event_expires_at ON macro_event(expires_at)")
    # Flag the shock with whether NSE was open when detected. UI uses this
    # to badge events as actionable (NSE closed → positioning window) vs
    # informational (NSE open → already in price).
    run_query_safe("ALTER TABLE macro_event ADD COLUMN during_nse_hours INTEGER DEFAULT 0")

    # ── Economic Calendar: forward-looking scheduled events ──
    # Manually-curated table of upcoming macro events (RBI/Fed/MPC/PMIs/etc.)
    # with AI scenario analysis. Updated weekly via /api/admin/calendar/upsert
    # or by editing CALENDAR_EVENTS_SEED + restarting.
    run_query_safe('''
        CREATE TABLE IF NOT EXISTS economic_calendar (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date                TEXT NOT NULL,
            event_time_ist            TEXT,
            title                     TEXT NOT NULL,
            country                   TEXT,
            category                  TEXT,
            importance                TEXT,
            description               TEXT,
            prior_value               TEXT,
            consensus_estimate        TEXT,
            actual_value              TEXT,
            scenarios_json            TEXT,
            historical_analogues_json TEXT,
            related_sectors_json      TEXT,
            related_tickers_json      TEXT,
            status                    TEXT DEFAULT 'upcoming',
            created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_calendar_date ON economic_calendar(event_date)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_calendar_importance ON economic_calendar(importance)")
    # Unique-ish dedupe key so re-seeding doesn't duplicate. Combination of
    # date + title + country.
    run_query_safe("CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_key ON economic_calendar(event_date, country, title)")
    
    run_query_safe('''
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
    
    run_query_safe("ALTER TABLE stock_impact ADD COLUMN confidence_score INTEGER DEFAULT 80")
    run_query_safe("ALTER TABLE stock_impact ADD COLUMN technical_context TEXT DEFAULT ''")
    run_query_safe("ALTER TABLE stock_impact ADD COLUMN ensemble_detail TEXT DEFAULT ''")
    
    run_query_safe('''
        CREATE TABLE IF NOT EXISTS historical_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            headline TEXT,
            ticker TEXT,
            direction TEXT,
            outcome TEXT,
            change_pct REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    run_query_safe('''
        CREATE TABLE IF NOT EXISTS stock_universe (
            ticker TEXT PRIMARY KEY,
            symbol TEXT,
            name TEXT,
            exchange TEXT,
            source TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    run_query_safe("CREATE UNIQUE INDEX IF NOT EXISTS idx_news_headline ON news(headline)")
    run_query_safe("CREATE UNIQUE INDEX IF NOT EXISTS idx_stockimpact_news_ticker ON stock_impact(news_id, ticker)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_news_created_at ON news(created_at)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_stockimpact_news_id ON stock_impact(news_id)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_stock_universe_symbol ON stock_universe(symbol)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_stock_universe_name ON stock_universe(name)")

    # ── Tier-1 performance indexes (T1.1) ──
    # Backs the hottest filter/sort columns. Each one shaves 10-50ms off the
    # query that uses it; cumulative impact across worker + UI requests is large.
    # CREATE IF NOT EXISTS is idempotent, safe to run every startup.
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_stockimpact_ticker      ON stock_impact(ticker)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_stockimpact_status      ON stock_impact(status)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_stockimpact_created_at  ON stock_impact(created_at)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_stockimpact_status_created ON stock_impact(status, created_at)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_histpatterns_ticker     ON historical_patterns(ticker)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_histpatterns_ticker_dir ON historical_patterns(ticker, direction)")

    # ── T3.16: Archive tables for periodic data archival ──
    # Same schema as their hot counterparts. The archival worker MOVES rows
    # older than 90 days here, keeping the active tables small + fast forever.
    # Critical: historical_patterns is NOT archived — that's the long-term
    # training memory the HistoricalSimilarityModel reads from.
    run_query_safe('''
        CREATE TABLE IF NOT EXISTS news_archive (
            id INTEGER PRIMARY KEY,
            headline TEXT NOT NULL,
            news_time TEXT,
            aam_janta_translation TEXT,
            macro_pathway TEXT,
            category TEXT,
            created_at TIMESTAMP,
            archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    run_query_safe('''
        CREATE TABLE IF NOT EXISTS stock_impact_archive (
            id INTEGER PRIMARY KEY,
            news_id INTEGER,
            ticker TEXT,
            impact TEXT,
            estimated_change_percent REAL,
            view TEXT,
            reason TEXT,
            base_price REAL,
            current_price REAL,
            status TEXT,
            created_at TIMESTAMP,
            confidence_score INTEGER,
            technical_context TEXT,
            ensemble_detail TEXT,
            archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Idempotent ALTER so existing news_archive tables pick up the body column
    # without losing data. Matches the live `news` table schema.
    run_query_safe("ALTER TABLE news_archive ADD COLUMN body TEXT DEFAULT ''")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_news_archive_created_at  ON news_archive(created_at)")
    run_query_safe("CREATE INDEX IF NOT EXISTS idx_stockimpact_archive_news ON stock_impact_archive(news_id)")
