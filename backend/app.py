import sys, io, gc
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def safe_print(*args, **kwargs):
    """Thread-safe print that silently ignores I/O errors on closed stdout (e.g. Flask reloader)."""
    try:
        print(*args, **kwargs)
        try:
            sys.stdout.flush()
        except Exception:
            pass
    except (ValueError, OSError):
        pass  # stdout was closed — ignore silently

print("[DEBUG] App startup beginning...", flush=True)
from flask import Flask, render_template, request, jsonify, session, make_response, send_from_directory
import sqlite3
import secrets
import random
import threading
import time
import json
from werkzeug.security import generate_password_hash
import os
import argparse
from dotenv import load_dotenv

# Load environment variables from parent directory
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import feedparser
from google import genai
from google.genai import types
from difflib import SequenceMatcher
import requests
from bs4 import BeautifulSoup
import concurrent.futures
from collections import deque
from openai import OpenAI as OpenAIClient
import angelone_shim as yf
import logging
from email.utils import parsedate_to_datetime
print("[DEBUG] All imports complete", flush=True)
yf.set_tz_cache_location("yf_cache")  # no-op in Angel One shim

# Shared HTTP session for network calls to reduce connection overhead.
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
# Bounded retry/backoff on transient HTTP STATUS errors only (not connect/read
# timeouts, so a slow feed can't blow its per-cycle budget). Fail-safe if the
# urllib3 Retry signature differs across versions.
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    _http_retry = Retry(total=2, connect=0, read=0, status=2, backoff_factor=0.5,
                        status_forcelist=[429, 500, 502, 503, 504])
    _http_adapter = HTTPAdapter(max_retries=_http_retry)
    HTTP_SESSION.mount("http://", _http_adapter)
    HTTP_SESSION.mount("https://", _http_adapter)
except Exception as _http_e:
    print(f"[HTTP] retry adapter unavailable: {_http_e}")
ARTICLE_TEXT_CACHE = {}
_ARTICLE_TEXT_CACHE_LOCK = threading.Lock()
# Was 500 — trimmed to 200 to stay under Render's 512MB free-tier ceiling.
# Each entry holds up to 1500 chars of body text → 200 × 1.5KB ≈ 300KB total.
_ARTICLE_TEXT_CACHE_MAX = 200
# URL → canonical published-time string (RFC 2822). Empty string = scraped
# successfully but no meta-tag found (so we don't retry). LRU-evicted.
# Was 5000 — trimmed to 1000 for the same memory reason. Each entry is small
# (~50 chars of timestamp + URL key), but at 5000 entries the total adds up.
PUBLISHED_TIME_CACHE = {}
_PUBLISHED_TIME_CACHE_MAX = 1000


from datetime import datetime, timedelta, timezone
from signals.technical_analysis import (
    get_stock_technical_context,
    format_technical_context_for_prompt,
    get_market_regime
)
from signals.prediction_models import EnsemblePredictor




# NSE market-calendar helpers (holidays, market-hours, session checks) were
# extracted to market_calendar.py. Imported back so every existing reference
# (is_market_open / is_market_holiday / has_market_traded_since / ...) keeps
# resolving exactly as before.
from marketdata.market_calendar import (
    NSE_HOLIDAYS_2026, NSE_HOLIDAYS_2027, _NSE_HOLIDAYS_BY_YEAR,
    is_market_holiday, is_market_open, published_after_market_hours,
    has_market_traded_since,
)


_TICKER_CACHE = {}
_TICKER_CACHE_TIME = {}
_TICKER_CACHE_LOCK = threading.Lock()  # Bug #17: protect concurrent cache access

def get_robust_price(ticker, market_open=None):
    """
    Fetches live/closing price with a 30-second in-memory cache.
    Uses Angel One SmartAPI (exchange-sourced LTP) with Yahoo Finance fallback.
    Caches both successes (real price) and failures (0.0 sentinel) for 30s.
    Thread-safe via _TICKER_CACHE_LOCK (Bug #17 fix).
    """
    now = time.time()

    if market_open is None:
        market_open = is_market_open()  # is_market_open is defined above — no forward reference

    # Return cached value if still fresh (30s window) — read under lock
    with _TICKER_CACHE_LOCK:
        if ticker in _TICKER_CACHE and (now - _TICKER_CACHE_TIME.get(ticker, 0)) < 30:
            return _TICKER_CACHE[ticker]

    lp, _ = yf.get_ltp(ticker)
    price = round(float(lp), 2) if (lp and lp > 0) else 0.0

    with _TICKER_CACHE_LOCK:
        _TICKER_CACHE[ticker] = price
        _TICKER_CACHE_TIME[ticker] = now
    return price


app = Flask(__name__, template_folder='../frontend', static_folder='../frontend', static_url_path='/')
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["TEMPLATES_AUTO_RELOAD"] = True
# T1.3: Static files (stocks.js, index.html) — short Flask default cache.
# The after_request hook below then overrides Cache-Control per URL pattern
# (1d on stocks.js, etc.) for stronger control.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60
app.jinja_env.auto_reload = True

# ── T1.2 gzip compression ──
# Compresses every text response (HTML/JSON/JS/CSS) over the wire.
# 2.5 MB news payload typically gzips to ~250 KB. Roughly 5-10× smaller on
# every page load. Wrapped in try/except so a missing flask-compress dep
# doesn't crash startup — gzip is a perf win, not a correctness requirement.
try:
    from flask_compress import Compress
    app.config["COMPRESS_MIMETYPES"] = [
        "text/html", "text/css", "text/javascript",
        "application/json", "application/javascript",
        "image/svg+xml",
    ]
    app.config["COMPRESS_LEVEL"]     = 6      # default; good speed/size tradeoff
    app.config["COMPRESS_MIN_SIZE"]  = 500    # don't bother below 500B
    Compress(app)
    print("[PERF] gzip compression enabled (flask-compress)")
except Exception as _e:
    print(f"[PERF] gzip compression NOT enabled: {_e}")


# ── T1.3 Cache-Control headers ──
# Maps endpoint URL patterns to "max-age" durations. A single after_request
# hook sets Cache-Control on every response so the browser (and any CDN in
# front) can serve repeat visits from cache. Mutable signal data uses short
# TTLs so users still see fresh content; truly immutable assets get 1y.
_CACHE_RULES = (
    # ── Long-lived static assets ──
    # The frontend JS catalog is a static file; we version it implicitly via
    # the index.html cache. Safe to cache aggressively.
    ("/stocks.js",                   "public, max-age=86400, stale-while-revalidate=86400"),
    # ── Newly-extracted frontend chunks (was inline in index.html) ──
    # Use no-cache so browsers ALWAYS revalidate — avoids stale JS/CSS after
    # deploys. The server returns 304 Not Modified quickly when unchanged.
    ("/app.js",                      "no-cache, must-revalidate"),
    # app.js was split into ordered app-*.js chunks; same revalidate policy.
    ("/app-",                        "no-cache, must-revalidate"),
    ("/styles.css",                  "no-cache, must-revalidate"),
    # ── Macro Pulse — refresh moderately fast for live shock view ──
    ("/api/macro/events",            "public, max-age=60, stale-while-revalidate=120"),
    # ── Calendar — events change weekly; cache aggressively ──
    ("/api/calendar",                "public, max-age=600, stale-while-revalidate=1800"),
    # ── Short-lived data — refresh frequently ──
    ("/api/indices",                 "public, max-age=30, stale-while-revalidate=30"),
    ("/api/news/top",                "public, max-age=30, stale-while-revalidate=60"),
    ("/api/news/all",                "public, max-age=60, stale-while-revalidate=120"),
    ("/api/signal-terminal",         "public, max-age=5, stale-while-revalidate=10"),
    ("/api/backtest-stats",          "public, max-age=10, stale-while-revalidate=30"),
    ("/api/stock-price/",            "public, max-age=20, stale-while-revalidate=60"),
    ("/api/stock-search",            "public, max-age=120"),
    # ── Never cache: live status & user-specific endpoints ──
    ("/api/debug-",                  "no-store"),
    ("/api/whatsapp/",               "no-store"),
    ("/api/portfolio-assistant",     "no-store"),
    ("/api/me",                      "no-store"),
    ("/api/send-otp",                "no-store"),
    ("/api/verify-otp",              "no-store"),
    ("/api/logout",                  "no-store"),
)

@app.after_request
def _apply_cache_headers(resp):
    try:
        path = (request.path or "")
        # Patterns in _CACHE_RULES are authoritative — they override any
        # Cache-Control set by Flask's static handler or individual routes.
        for prefix, value in _CACHE_RULES:
            if path.startswith(prefix):
                resp.headers["Cache-Control"] = value
                # Drop the legacy "no-cache" sister headers if anything set them
                for k in ("Pragma", "Expires"):
                    resp.headers.pop(k, None)
                return resp
        # No explicit rule — leave whatever the route already set; otherwise
        # apply a sensible default for HTML responses.
        if not resp.headers.get("Cache-Control") and resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "public, max-age=60, must-revalidate"
    except Exception:
        pass
    return resp


# ── T2.11: Server-side route cache ──
# Memoizes the JSON response of expensive read-only endpoints for `ttl` seconds.
# Cache key = (path, querystring). Returns the cached Flask response when a
# request arrives within the window; computes fresh otherwise. Thread-safe.
#
# Browsers already cache via Cache-Control headers (T1.3), but THAT cache is
# per-browser. This cache is shared across ALL visitors hitting the same Render
# instance — so when 10 users land at once, only the first triggers the DB
# query; the next 9 get the cached response in <1 ms.
import functools as _ft
import threading as _th

_ROUTE_CACHE: dict = {}
_ROUTE_CACHE_LOCK = _th.Lock()

def route_cache(ttl_seconds: int):
    """Memoize a Flask route's return value for `ttl_seconds`. Key = path + query."""
    def deco(fn):
        @_ft.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (request.path or '', request.query_string or b'')
            now = time.time()
            with _ROUTE_CACHE_LOCK:
                entry = _ROUTE_CACHE.get(key)
                if entry and (now - entry['ts']) < ttl_seconds:
                    # Return a NEW Flask Response so any after_request hooks
                    # (Cache-Control, gzip via flask-compress) still apply.
                    body, mimetype, status = entry['body'], entry['mimetype'], entry['status']
                    return app.response_class(body, status=status, mimetype=mimetype)
            # Cache miss — compute, memoize, return.
            result = fn(*args, **kwargs)
            try:
                # Normalize result to a Flask Response so we can cache its bytes.
                # Routes typically return a Response (jsonify) or a (resp, status) tuple.
                if isinstance(result, tuple):
                    body_obj, status = result[0], result[1] if len(result) > 1 else 200
                else:
                    body_obj, status = result, 200
                if hasattr(body_obj, 'get_data'):
                    body, mimetype = body_obj.get_data(), body_obj.mimetype
                else:
                    # Fallback — shouldn't normally happen
                    body, mimetype = str(body_obj).encode('utf-8'), 'application/json'
                with _ROUTE_CACHE_LOCK:
                    _ROUTE_CACHE[key] = {
                        'ts': now,
                        'body': body,
                        'mimetype': mimetype,
                        'status': status if isinstance(status, int) else 200,
                    }
            except Exception:
                pass
            return result
        return wrapper
    return deco

# Minimum AI confidence to accept a prediction.
# Was 50 — let too much noise through. Live 30d stats showed 0% hit rate
# across all confidence bands (90+ included), so the score wasn't predictive
# at low cutoffs. 75 cuts approved-signal volume ~half, but the survivors
# carry stronger model agreement. Env-tunable so we can dial it back if the
# market regime flips and signal count crashes.
MIN_CONFIDENCE = int(os.environ.get("MIN_CONFIDENCE", "50"))

# Signal evaluation rules used by startup repair and the live price worker.
TRADE_TARGET_PCT = 2.0
TRADE_STOP_PCT = 1.0
# A signal that hasn't hit target or stop within this window is marked Expired
# and excluded from hit-rate stats. Tunable via env var for ops without redeploy.
SIGNAL_EXPIRY_HOURS = int(os.environ.get("SIGNAL_EXPIRY_HOURS", "96"))

# Signals (and the news they reference) are kept in the hot tables for at least
# this many days so the track record + signal terminal show a full 90-day
# history. After this window archival_worker MOVES rows to the *_archive tables
# (reversible) — nothing is hard-deleted. Keep aligned with ARCHIVE_AFTER_DAYS.
SIGNAL_RETENTION_DAYS = int(os.environ.get("SIGNAL_RETENTION_DAYS", "90"))

# News FEED retention — separate from signal retention. The "All News" tab is
# kept bounded to the newest NEWS_FEED_MAX_ROWS rows AND the last
# NEWS_FEED_RETENTION_DAYS days so the browser never chokes. IMPORTANT: news that
# a signal references is EXEMPT from this prune — it is retained alongside the
# signal and archival_worker moves both into *_archive together at 90 days, so
# the signal terminal can still resolve the headline for the full 90-day window.
NEWS_FEED_RETENTION_DAYS = int(os.environ.get("NEWS_MAX_AGE_DAYS", "5"))
NEWS_FEED_MAX_ROWS       = int(os.environ.get("NEWS_MAX_ROWS", "800"))

import performance_report

# In-memory store for OTPs
OTP_STORE = {}
# Bug #5 fix: track failed OTP attempts per email to prevent brute-force.
# Cleared on success, on expiry, or when a new OTP is issued.
OTP_ATTEMPTS: dict = {}
_OTP_MAX_ATTEMPTS = 5  # block after 5 wrong guesses per OTP window
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL")
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")

# The database layer (connection wrappers, Postgres pool, connect_news_db /
# connect_users_db / connect_postgres_db / db_write, and the SQLite paths) was
# extracted to db.py. Imported back so all call sites resolve unchanged; db.py
# is self-contained (stdlib + psycopg2) so there is no import cycle.
from persistence.db import (
    _NEWS_DB, _USERS_DB,
    CursorWrapper, ConnectionWrapper,
    connect_news_db, connect_users_db, connect_postgres_db,
    db_write,
)


# Schema builders (init_db / init_news_db) were extracted to schema.py.
# Imported back; they are invoked at startup just below.
from persistence.schema import init_db, init_news_db

def migrate_local_sqlite_to_postgres():
    import sqlite3
    # Use __file__ to find the correct absolute path regardless of working directory
    _here = os.path.dirname(os.path.abspath(__file__))
    _repo_root = os.path.dirname(_here)
    print(f"   [MIGRATION] Working dir: {os.getcwd()}, app dir: {_here}", flush=True)

    # Also check .done files to re-run a partial migration
    candidates = [
        os.path.join(_here, 'news_cache.db'),
        os.path.join(_here, 'news_cache.db.done'),
        os.path.join(_repo_root, 'backend', 'news_cache.db'),
        os.path.join(_repo_root, 'backend', 'news_cache.db.done'),
        os.path.join(_repo_root, 'news_cache.db'),
        os.path.join(os.getcwd(), 'backend', 'news_cache.db'),
        os.path.join(os.getcwd(), 'backend', 'news_cache.db.done'),
        os.path.join(os.getcwd(), 'news_cache.db'),
    ]
    db_path = None
    for c in candidates:
        print(f"   [MIGRATION] Checking: {c} -> exists={os.path.exists(c)}", flush=True)
        if os.path.exists(c):
            db_path = c
            break
    if not db_path:
        print("   [MIGRATION] No local SQLite database found. Skipping migration.", flush=True)
        return

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("   [MIGRATION] SQLite is active locally. No PostgreSQL migration needed.")
        return

    print(f"   [MIGRATION] Found local SQLite database at {db_path}. Starting cloud migration...", flush=True)
    try:
        sqlite_conn = sqlite3.connect(db_path)
        sqlite_cur = sqlite_conn.cursor()

        pg_conn = connect_news_db()
        pg_cur = pg_conn.cursor()

        # 1. Migrate stock_universe
        print("   [MIGRATION] Migrating stock_universe table...", flush=True)
        sqlite_cur.execute("SELECT ticker, symbol, name, exchange, source, updated_at FROM stock_universe")
        univ_rows = sqlite_cur.fetchall()
        if univ_rows:
            try:
                pg_cur.executemany("""
                    INSERT INTO stock_universe (ticker, symbol, name, exchange, source, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker) DO NOTHING
                """, univ_rows)
                pg_conn.commit()
            except Exception as e:
                pg_conn.rollback()
                print(f"   [MIGRATION] Batch stock_universe failed ({e}). Falling back to row-by-row...", flush=True)
                for row in univ_rows:
                    try:
                        pg_cur.execute("""
                            INSERT INTO stock_universe (ticker, symbol, name, exchange, source, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (ticker) DO NOTHING
                        """, row)
                        pg_conn.commit()
                    except Exception as ex:
                        pg_conn.rollback()
                        print(f"      [MIGRATION] Error inserting stock_universe {row[0]}: {ex}", flush=True)

        # 2. Migrate news (using actual SQLite columns)
        print("   [MIGRATION] Migrating news table...", flush=True)
        sqlite_cur.execute("SELECT id, headline, news_time, aam_janta_translation, macro_pathway, created_at, category FROM news")
        news_rows = sqlite_cur.fetchall()
        inserted_news = 0
        skipped_news = 0
        if news_rows:
            try:
                pg_cur.executemany("""
                    INSERT INTO news (id, headline, news_time, aam_janta_translation, macro_pathway, created_at, category)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, news_rows)
                pg_conn.commit()
                inserted_news = len(news_rows)
            except Exception as e:
                pg_conn.rollback()
                print(f"   [MIGRATION] Batch news migration failed ({e}). Falling back to row-by-row...", flush=True)
                for row in news_rows:
                    try:
                        pg_cur.execute("""
                            INSERT INTO news (id, headline, news_time, aam_janta_translation, macro_pathway, created_at, category)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT DO NOTHING
                        """, row)
                        pg_conn.commit()
                        inserted_news += 1
                    except Exception as ex:
                        pg_conn.rollback()
                        skipped_news += 1
                        if skipped_news <= 5:
                            print(f"      [MIGRATION] Error inserting news id={row[0]}: {ex}", flush=True)
        print(f"   [MIGRATION] News: {inserted_news} inserted, {skipped_news} skipped out of {len(news_rows)}.", flush=True)

        # 3. Migrate stock_impact (using actual SQLite columns)
        print("   [MIGRATION] Migrating stock_impact table...", flush=True)
        sqlite_cur.execute("""
            SELECT id, news_id, ticker, impact, estimated_change_percent, view, reason,
                   base_price, current_price, status, created_at, confidence_score,
                   technical_context, ensemble_detail
            FROM stock_impact
        """)
        impact_rows = sqlite_cur.fetchall()
        inserted_impact = 0
        if impact_rows:
            try:
                pg_cur.executemany("""
                    INSERT INTO stock_impact (id, news_id, ticker, impact, estimated_change_percent,
                        view, reason, base_price, current_price, status, created_at,
                        confidence_score, technical_context, ensemble_detail)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, impact_rows)
                pg_conn.commit()
                inserted_impact = len(impact_rows)
            except Exception as e:
                pg_conn.rollback()
                print(f"   [MIGRATION] Batch stock_impact failed ({e}). Falling back to row-by-row...", flush=True)
                inserted_impact = 0
                for row in impact_rows:
                    try:
                        pg_cur.execute("""
                            INSERT INTO stock_impact (id, news_id, ticker, impact, estimated_change_percent,
                                view, reason, base_price, current_price, status, created_at,
                                confidence_score, technical_context, ensemble_detail)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                        """, row)
                        pg_conn.commit()
                        inserted_impact += 1
                    except Exception as ex:
                        pg_conn.rollback()
                        print(f"      [MIGRATION] Error inserting stock_impact {row[0]}: {ex}", flush=True)
        print(f"   [MIGRATION] Stock impact: {inserted_impact}/{len(impact_rows)} rows migrated.", flush=True)

        # 4. Migrate historical_patterns
        print("   [MIGRATION] Migrating historical_patterns table...", flush=True)
        sqlite_cur.execute("SELECT id, headline, ticker, direction, outcome, change_pct, created_at FROM historical_patterns")
        pat_rows = sqlite_cur.fetchall()
        if pat_rows:
            try:
                pg_cur.executemany("""
                    INSERT INTO historical_patterns (id, headline, ticker, direction, outcome, change_pct, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, pat_rows)
                pg_conn.commit()
            except Exception as e:
                pg_conn.rollback()
                print(f"   [MIGRATION] Batch historical_patterns failed ({e}). Falling back to row-by-row...", flush=True)
                for row in pat_rows:
                    try:
                        pg_cur.execute("""
                            INSERT INTO historical_patterns (id, headline, ticker, direction, outcome, change_pct, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                        """, row)
                        pg_conn.commit()
                    except Exception as ex:
                        pg_conn.rollback()
                        print(f"      [MIGRATION] Error inserting pattern {row[0]}: {ex}", flush=True)

        # Adjust primary key sequences
        for seq_table in ['news', 'stock_impact', 'historical_patterns']:
            try:
                pg_cur.execute(f"SELECT setval(pg_get_serial_sequence('{seq_table}', 'id'), COALESCE(MAX(id), 1) + 1) FROM {seq_table}")
                pg_conn.commit()
            except Exception as ex:
                print(f"      [MIGRATION] Error syncing sequence {seq_table}: {ex}", flush=True)

        sqlite_conn.close()
        pg_conn.close()

        print("   [MIGRATION] SUCCESS! All data migrated to cloud database.", flush=True)

        # Rename file so it does not attempt migration again
        try:
            os.rename(db_path, db_path + ".done")
            print(f"   [MIGRATION] Renamed local {db_path} to prevent re-migration.", flush=True)
        except Exception as e:
            print(f"      [MIGRATION] Warning: Could not rename SQLite file: {e}")

    except Exception as e:
        print(f"   [MIGRATION] FAILED: {e}", flush=True)

init_db()
print("[DEBUG] init_db() completed", flush=True)
init_news_db()
print("[DEBUG] init_news_db() completed", flush=True)
import threading
migration_thread = threading.Thread(
    target=migrate_local_sqlite_to_postgres,
    name="CloudMigrationThread",
    daemon=True
)
migration_thread.start()
print("[DEBUG] Started migrate_local_sqlite_to_postgres() in background thread", flush=True)

# Checkpoint any stale WAL from a previous crashed run so we start clean
try:
    _chk = connect_news_db()
    _chk.execute('PRAGMA wal_checkpoint(TRUNCATE);')
    _chk.close()
except Exception:
    pass
print("[DEBUG] WAL checkpoint completed", flush=True)

# ==========================================
# LIVE AI NEWS ENGINE (LiveMint, ET, MoneyControl)
# ==========================================
# We no longer use in-memory cache for news, but we keep it here just in case.
LIVE_NEWS_CACHE = []

# ── Gemini API Key Pools ──
# LIST 1: keys 1–23 — primary rotation pool (used first).
# LIST 2: keys 24–35 — fallback pool, engaged automatically when EVERY key
#          in List 1 is on quota cooldown simultaneously.
#
# All keys are read from environment variables — no hardcoding. Deploy-time
# changes (adding/removing keys) are purely env changes; no code edit needed.
#
# Set on Render dashboard → Environment → Add Environment Variable:
#   GEMINI_API_KEY_1  ... GEMINI_API_KEY_23   ← List 1
#   GEMINI_API_KEY_24 ... GEMINI_API_KEY_35   ← List 2

API_KEYS_LIST1 = [
    k for k in (os.environ.get(f"GEMINI_API_KEY_{i}") for i in range(1, 24)) if k
]
API_KEYS_LIST2 = [
    k for k in (os.environ.get(f"GEMINI_API_KEY_{i}") for i in range(24, 36)) if k
]

# API_KEYS is the active pool used by all rotation helpers.
# It starts as List 1; the fallback logic in get_and_rotate_client() switches
# it to List 2 when all List 1 keys are exhausted, and logs the transition.
API_KEYS = list(API_KEYS_LIST1)  # starts with List 1

# Track which list is currently active so the debug endpoint can report it.
_ACTIVE_KEY_LIST = 1  # 1 or 2

# ── Per-key cooldown after 429/503 ──
# 429 on the Gemini free tier almost always means the key's RPD (daily) limit
# is gone, not a per-minute throttle. The previous flat 5-min cooldown re-
# probed a dead key 12 times an hour for no reason. New: gentle exponential
# backoff per key — dead keys get pushed out gradually without locking up
# the rotation when a healthy key has a transient blip.
#   Strike 1:  5 min
#   Strike 2: 10 min
#   Strike 3: 20 min
#   Strike 4+: 30 min  (cap — still re-probes ~every half hour)
# Successful call resets the counter. Tunable via env vars below.
_KEY_QUOTA_COOLDOWN_UNTIL: dict = {}
_KEY_QUOTA_STRIKE_COUNT: dict = {}   # idx -> consecutive 429 count
_KEY_QUOTA_LAST_HIT: dict = {}       # idx -> unix time of last 429 (for stale-strike decay)
_GEMINI_KEY_COOLDOWN_SECS = int(os.environ.get("GEMINI_KEY_COOLDOWN_SECS", "300"))
# Cap on escalated cooldown so even a permanently-dead key gets re-probed
# at least once every CAP seconds (giving daily reset a chance to recover it).
_GEMINI_KEY_COOLDOWN_MAX_SECS = int(os.environ.get("GEMINI_KEY_COOLDOWN_MAX_SECS", "1800"))  # 30min
# Multiplier between strikes. 2x means strikes go 5/10/20/30(cap).
_GEMINI_KEY_COOLDOWN_MULT = float(os.environ.get("GEMINI_KEY_COOLDOWN_MULT", "2"))
# Strikes decay if a key goes this long without another 429 (1 hour by default).
_GEMINI_STRIKE_DECAY_SECS = int(os.environ.get("GEMINI_STRIKE_DECAY_SECS", "3600"))
# Transient (503) cooldown. When Gemini's servers report "high demand" we wait
# 30s before retrying that key — long enough to not feed the storm, short
# enough to come back quickly once the spike passes.
_GEMINI_TRANSIENT_COOLDOWN_SECS = int(os.environ.get("GEMINI_TRANSIENT_COOLDOWN_SECS", "30"))

# Worker heartbeat — updated by ai_news_worker / yfinance_worker so /api/debug-worker-status
# can tell whether the background threads are alive and when they last did anything.
WORKER_HEARTBEAT = {
    "ai_news": {
        "last_cycle_started_at": None,
        "last_scrape_count": None,
        "last_save_count": None,
        "last_cycle_finished_at": None,
        "last_error": None,
        "last_error_at": None,
        "cycles_completed": 0,
    },
    "yfinance": {
        "last_cycle_started_at": None,
        "last_cycle_finished_at": None,
        "last_error": None,
        "last_error_at": None,
        "cycles_completed": 0,
    },
    # Wired by macro_shock_worker / archival_worker / news_prune_worker so
    # /api/health can detect a silently-dead background loop.
    "macro_shock": {
        "last_cycle_started_at": None,
        "last_cycle_finished_at": None,
        "last_shocks_detected": None,
        "last_error": None,
        "last_error_at": None,
        "cycles_completed": 0,
    },
    "archival": {
        "last_cycle_started_at": None,
        "last_cycle_finished_at": None,
        "last_news_moved": None,
        "last_impact_moved": None,
        "last_error": None,
        "last_error_at": None,
        "cycles_completed": 0,
    },
    "news_prune": {
        "last_cycle_started_at": None,
        "last_cycle_finished_at": None,
        "last_pruned_count": None,
        "last_error": None,
        "last_error_at": None,
        "cycles_completed": 0,
    },
}


def _heartbeat(worker_name, **fields):
    try:
        bucket = WORKER_HEARTBEAT.setdefault(worker_name, {})
        bucket.update(fields)
    except Exception:
        pass

def _is_gemini_quota_error(exc: Exception) -> bool:
    err = str(exc).lower()
    # True quota limit hits
    return (
        "429" in err or 
        "resource_exhausted" in err or 
        "quota" in err or 
        "rate limit" in err or
        "limit exceeded" in err
    )

def _is_gemini_transient_error(exc: Exception) -> bool:
    err = str(exc).lower()
    return (
        "503" in err or 
        "unavailable" in err or 
        "overloaded" in err or
        "timeout" in err or
        "timed out" in err or
        isinstance(exc, TimeoutError)
    )

def _gemini_key_available(key_idx: int) -> bool:
    return _KEY_QUOTA_COOLDOWN_UNTIL.get(key_idx, 0) <= time.time()

def _available_gemini_key_indices():
    return [i for i in range(len(API_KEYS)) if _gemini_key_available(i)]

def _mark_gemini_key_quota_hit(key_idx: int, cooldown_secs: int = None):
    """
    Put a key on cooldown. If `cooldown_secs` is given, use it verbatim — that
    path is for non-429 cases (transient 503, unexpected errors) where the
    caller already decided the duration.

    Default behaviour (no `cooldown_secs`) treats this as a 429/quota hit and
    applies the escalating per-key backoff described above.
    """
    now = time.time()
    if cooldown_secs is not None:
        # Caller-supplied duration (typically transient/503) — no strike bump.
        _KEY_QUOTA_COOLDOWN_UNTIL[key_idx] = now + cooldown_secs
        return

    # Decay stale strikes — if this key hasn't been quota-hit in a while,
    # treat the upcoming hit as a fresh first strike. This prevents a key
    # that hit once yesterday and recovered from getting a 6h cooldown
    # today on its very first 429.
    last_hit = _KEY_QUOTA_LAST_HIT.get(key_idx, 0)
    if last_hit and (now - last_hit) > _GEMINI_STRIKE_DECAY_SECS:
        _KEY_QUOTA_STRIKE_COUNT[key_idx] = 0

    strikes = _KEY_QUOTA_STRIKE_COUNT.get(key_idx, 0) + 1
    _KEY_QUOTA_STRIKE_COUNT[key_idx] = strikes
    _KEY_QUOTA_LAST_HIT[key_idx] = now

    # Gentle exponential: 5min, 10min, 20min, 30min(cap)…
    base = _GEMINI_KEY_COOLDOWN_SECS
    cooldown = min(base * (_GEMINI_KEY_COOLDOWN_MULT ** (strikes - 1)), _GEMINI_KEY_COOLDOWN_MAX_SECS)
    _KEY_QUOTA_COOLDOWN_UNTIL[key_idx] = now + cooldown
    safe_print(
        f"   [AI] Key {key_idx + 1} quota strike #{strikes} — cooldown {int(cooldown)}s "
        f"({'capped' if cooldown >= _GEMINI_KEY_COOLDOWN_MAX_SECS else 'escalating'})."
    )

def _reset_gemini_key_strikes(key_idx: int):
    """Call on a successful Gemini response so the key's quota strike count
    decays back to zero. Without this, a key that succeeded after a brief
    cooldown would still escalate on its next 429."""
    if key_idx in _KEY_QUOTA_STRIKE_COUNT:
        _KEY_QUOTA_STRIKE_COUNT.pop(key_idx, None)
        _KEY_QUOTA_LAST_HIT.pop(key_idx, None)

def _seconds_until_any_gemini_key():
    now = time.time()
    waits = [_KEY_QUOTA_COOLDOWN_UNTIL[i] - now for i in range(len(API_KEYS))
             if _KEY_QUOTA_COOLDOWN_UNTIL.get(i, 0) > now]
    return max(0, int(min(waits))) if waits else 0

def _next_available_gemini_key_idx(start_from: int = 0):
    if not API_KEYS:
        return None
    for step in range(len(API_KEYS)):
        idx = (start_from + step) % len(API_KEYS)
        if _gemini_key_available(idx):
            return idx
    return None

def _set_active_gemini_client(key_idx: int):
    global current_key_idx, client
    current_key_idx = key_idx
    client = genai.Client(api_key=API_KEYS[key_idx])
    return client

def _bootstrap_gemini_client():
    global client
    if not API_KEYS:
        client = None
        return None
    idx = _next_available_gemini_key_idx(0)
    if idx is None:
        idx = 0
        print(f"   [AI] All Gemini keys on quota cooldown — starting on key 1 until one frees up.")
    return _set_active_gemini_client(idx)

current_key_idx = 0
client = _bootstrap_gemini_client()


def _try_switch_to_list2():
    """
    Called when every key in the current active pool is on cooldown.
    If List 2 has keys and we are currently on List 1, switch the active
    pool to List 2 (and vice versa when List 2 is also exhausted).
    Returns True if the pool was switched, False otherwise.
    """
    global API_KEYS, _ACTIVE_KEY_LIST, _KEY_QUOTA_COOLDOWN_UNTIL, _KEY_QUOTA_STRIKE_COUNT, _KEY_QUOTA_LAST_HIT

    if _ACTIVE_KEY_LIST == 1 and API_KEYS_LIST2:
        safe_print(
            f"   [AI Rotation] All {len(API_KEYS_LIST1)} List-1 keys exhausted — "
            f"switching to List 2 ({len(API_KEYS_LIST2)} keys)."
        )
        API_KEYS = list(API_KEYS_LIST2)
        _ACTIVE_KEY_LIST = 2
        # Reset cooldown state for the new pool.
        _KEY_QUOTA_COOLDOWN_UNTIL.clear()
        _KEY_QUOTA_STRIKE_COUNT.clear()
        _KEY_QUOTA_LAST_HIT.clear()
        return True

    if _ACTIVE_KEY_LIST == 2 and API_KEYS_LIST1:
        # List 2 is also exhausted — roll back to List 1 (its daily quota may
        # have reset by now; keys will be re-probed on their normal cooldown).
        safe_print(
            f"   [AI Rotation] All List-2 keys also exhausted — "
            f"falling back to List 1 ({len(API_KEYS_LIST1)} keys) for re-probe."
        )
        API_KEYS = list(API_KEYS_LIST1)
        _ACTIVE_KEY_LIST = 1
        _KEY_QUOTA_COOLDOWN_UNTIL.clear()
        _KEY_QUOTA_STRIKE_COUNT.clear()
        _KEY_QUOTA_LAST_HIT.clear()
        return True

    return False


def get_and_rotate_client(last_failed_idx=None, is_timeout=False, is_quota=True, is_transient=False):
    if last_failed_idx is not None:
        if is_quota or is_timeout or is_transient:
            if is_quota and not (is_transient or is_timeout):
                # Quota → let _mark_gemini_key_quota_hit apply the escalating
                # per-key backoff (5min → 30min → 2h → 6h cap).
                _mark_gemini_key_quota_hit(last_failed_idx)
                cooldown_label = f"escalating (strike {_KEY_QUOTA_STRIKE_COUNT.get(last_failed_idx, 1)})"
            else:
                # Transient/timeout → fixed short cooldown, no strike bump.
                cooldown = _GEMINI_TRANSIENT_COOLDOWN_SECS
                _mark_gemini_key_quota_hit(last_failed_idx, cooldown_secs=cooldown)
                cooldown_label = f"{cooldown}s"
            if is_timeout:
                status_str = f"timed out (cooldown {cooldown_label})"
            elif is_transient:
                status_str = f"hit transient error (cooldown {cooldown_label})"
            else:
                status_str = f"hit quota limit (cooldown {cooldown_label})"
            safe_print(f"   [AI Rotation] Key {last_failed_idx + 1} (List {_ACTIVE_KEY_LIST}) {status_str}.")
        else:
            safe_print(f"   [AI Rotation] Key {last_failed_idx + 1} (List {_ACTIVE_KEY_LIST}) failed due to network/DNS error. Retrying without cooldown.")

    idx = _next_available_gemini_key_idx(current_key_idx if last_failed_idx is None else (last_failed_idx + 1) % len(API_KEYS))

    # If no key is available in the current pool, try switching to the other list.
    if idx is None:
        if _try_switch_to_list2():
            idx = _next_available_gemini_key_idx(0)

    if idx is None:
        safe_print(f"   [AI Rotation] No available Gemini keys left in either list.")
        return None, None

    _set_active_gemini_client(idx)
    return client, idx
# Keep the live signal engine on a quota-friendly model by default. The previous
# hard-coded Pro model exhausted free-tier quota quickly, which disabled the AI
# confirmation layer and left the system leaning on bearish risk-off rules.
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Comprehensive RSS: Indian Financial + Global Macro/Geopolitical
RSS_SOURCES = [
    # ── Economic Times (multiple desks) ──
    "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms",
    "https://economictimes.indiatimes.com/markets/stocks/earnings/rssfeeds/837588974.cms",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms",
    "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
    "https://economictimes.indiatimes.com/news/international/rssfeeds/1373381519.cms",
    # ── MoneyControl ──
    "https://www.moneycontrol.com/rss/buzzingstocks.xml",
    "https://www.moneycontrol.com/rss/marketsindia.xml",
    "https://www.moneycontrol.com/rss/topstory.xml",
    "https://www.moneycontrol.com/rss/economy.xml",
    "https://www.moneycontrol.com/rss/worldnews.xml",
    # ── LiveMint ──
    "https://www.livemint.com/rss/markets",
    "https://www.livemint.com/rss/companies",
    "https://www.livemint.com/rss/industry",
    "https://www.livemint.com/rss/economy",
    "https://www.livemint.com/rss/politics",
    # ── Business Standard ──
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.business-standard.com/rss/companies-101.rss",
    "https://www.business-standard.com/rss/finance-103.rss",
    "https://www.business-standard.com/rss/economy-102.rss",
    "https://www.business-standard.com/rss/international-104.rss",
    # ── NDTV Profit ──
    "https://feeds.feedburner.com/ndtvprofit-latest",
    # ── Financial Express ──
    "https://www.financialexpress.com/market/feed/",
    "https://www.financialexpress.com/economy/feed/",
    # ── Mint (CNBC-TV18 / Bloomberg) ──
    "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market-109.xml",
    "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/economy-108.xml",
    "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/world-111.xml",
    # ── Reuters India / Global ──
    "https://news.google.com/rss/search?q=site:reuters.com+india+economy+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    # ── Google News: Indian Markets ──
    "https://news.google.com/rss/search?q=indian+stock+market+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=NSE+BSE+Nifty+Sensex+stocks+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+stocks+earnings+results+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=indian+economy+RBI+market+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=SEBI+RBI+NSE+BSE+order+fine+approval+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+mergers+acquisitions+IPO+results+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=quarterly+results+earnings+profit+loss+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    # ── Google News: GLOBAL MACRO / GEOPOLITICAL (hidden chain triggers) ──
    "https://news.google.com/rss/search?q=semiconductor+chip+shortage+ban+export+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=crude+oil+OPEC+prices+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=US+Fed+rate+decision+inflation+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=China+economy+trade+war+tariff+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=Japan+trade+export+restriction+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=rupee+dollar+FII+FPI+flow+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=geopolitical+tension+war+sanctions+india+impact+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=steel+copper+aluminium+commodity+prices+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=PLI+subsidy+government+policy+india+industry+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    # ── Google News: REGULATORY / LANDMINE catalysts (India-specific, high-value) ──
    "https://news.google.com/rss/search?q=promoter+pledge+shares+india+when:2d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=SEBI+ban+order+penalty+investigation+india+when:2d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=auditor+resignation+resigns+india+company+when:2d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=ASM+GSM+surveillance+measure+stock+NSE+BSE+when:2d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=block+deal+bulk+deal+stake+sale+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=credit+rating+downgrade+india+company+when:2d&hl=en-IN&gl=IN&ceid=IN:en",
    # ── DIRECT regulator RSS (source-of-truth, no Google aggregation lag — probed
    # reachable from the server; NSE's own API blocks datacenter IPs so it's not
    # here. BSE announcements (pledge/ratings) need a custom JSON fetcher). ──
    "https://www.rbi.org.in/pressreleases_rss.xml",
    "https://www.rbi.org.in/notifications_rss.xml",
    "https://www.sebi.gov.in/sebirss.xml",
]

# Global state for scraping optimizations
RSS_CACHE = {url: {'etag': None, 'modified': None} for url in RSS_SOURCES}
SEEN_HEADLINES = set()

# ── Duplicate Signal Cooldown Guard ──
# Tracks (ticker, direction) pairs with their last signal timestamp (UTC).
# Prevents the same ticker+direction from generating duplicate signals within 24 hours.
# Format: { "SBIN.NS_BULLISH": datetime_utc }
RECENT_SIGNALS: dict = {}

# ── Selection funnel (lever #6 observability) ──
# Cumulative counters (since process start) of how many signal candidates each
# stage drops, surfaced via /api/debug-worker-status so the forward effect of
# the liquidity / ensemble filters is visible without needing a backtest.
SELECTION_FUNNEL: dict = {
    "liquidity_skip": 0,
    "atr_skip": 0,
    "ensemble_rejected": 0,
    "ensemble_approved": 0,
}

# Forward shadow-ledger ("eval loop") — logs every signal decision for
# measurement. Append-only; see backend/eval_loop.py.
import eval_loop

# ── Directional Bias Circuit-Breaker ──
# Tracks directions of recently APPROVED signals (rolling window). When the
# stream is dominated by one direction (e.g. 5/5 bearish like our live data
# showed), demand extra confidence on the dominant side. This prevents the
# model from going 100% one-way during a bull/bear news cycle.
#
# Tunables:
#   BIAS_WINDOW         — how many recent signals to consider (default 20)
#   BIAS_THRESHOLD_PCT  — bias trigger threshold (default 70)
#   BIAS_CONF_BOOST     — extra confidence required on the biased side (default 10)
import collections as _collections
RECENT_DIRECTIONS: _collections.deque = _collections.deque(
    maxlen=int(os.environ.get("BIAS_WINDOW", "20"))
)

# ── Fuzzy near-duplicate headline guard ──
# SEEN_HEADLINES (exact lowercase match) catches identical headlines; this
# catches the SAME story REWORDED by a different source ("Reliance surges 5%"
# vs "Reliance rises 5%") — which exact-match misses and which would otherwise
# spawn duplicate / correlated signals. The normalized incoming headline is
# compared against a bounded window of recent headlines via SequenceMatcher.
# Env-tunable; set DEDUP_THRESHOLD=1.0 to effectively disable fuzzy matching.
DEDUP_THRESHOLD = float(os.environ.get("DEDUP_THRESHOLD", "0.85"))
RECENT_HEADLINES: _collections.deque = _collections.deque(
    maxlen=int(os.environ.get("DEDUP_WINDOW", "300"))
)

def _norm_headline(h):
    """Lowercase, collapse whitespace, drop trailing punctuation so
    'SBIN gains!' and 'sbin  gains' normalize to the same string."""
    if not h:
        return ""
    s = re.sub(r"\s+", " ", h.lower().strip())
    return re.sub(r"[\s!?.,;:…—–\-]+$", "", s)

def _is_near_dup_headline(h_norm):
    """True if h_norm is >= DEDUP_THRESHOLD similar to any recent headline."""
    if not h_norm or DEDUP_THRESHOLD >= 1.0:
        return False
    for prev in RECENT_HEADLINES:
        if SequenceMatcher(None, h_norm, prev).ratio() >= DEDUP_THRESHOLD:
            return True
    return False

import random

# Rotated User-Agents reduce the chance a single static UA gets bot-blocked.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

def _ua():
    try:
        return random.choice(_USER_AGENTS)
    except Exception:
        return _USER_AGENTS[0]

# Per-feed fetch health (exposed via /api/debug-worker-status -> feed_stats) so
# a quietly-degrading source can be spotted and replaced. Each thread updates
# its own url key, so distinct-key dict writes are GIL-safe without a lock.
FEED_STATS = {}

def _feed_stat(url, outcome, n_articles=0, err=None):
    """outcome in {'ok','not_modified','fail'}. Never raises."""
    try:
        s = FEED_STATS.setdefault(url, {"fetches": 0, "articles": 0,
                                        "not_modified": 0, "failures": 0, "last_error": None})
        s["fetches"] += 1
        if outcome == "ok":
            s["articles"] += n_articles
        elif outcome == "not_modified":
            s["not_modified"] += 1
        else:
            s["failures"] += 1
            if err:
                s["last_error"] = str(err)[:200]
    except Exception:
        pass

# Naive timestamps (RSS/meta with no timezone) are assumed to be in this tz
# before converting to UTC. Indian publishers usually emit IST when they omit
# the tz; assuming UTC made those articles look ~5.5h fresher than reality.
# Set NAIVE_PUBTIME_TZ=UTC to revert to the old behaviour.
_NAIVE_TZ = timezone(timedelta(hours=5, minutes=30)) \
    if os.environ.get("NAIVE_PUBTIME_TZ", "IST").upper() == "IST" else timezone.utc

def _assume_tz(dt):
    """Make a naive datetime tz-aware using the configured default tz."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=_NAIVE_TZ)
    return dt

# ── BSE corporate-announcements fetcher (pledge / rating / board outcomes) ──
# Direct JSON API (not RSS). NSE blocks datacenter IPs, but BSE's announcement
# API is reachable; this pulls the source-of-truth filings the Google-News
# queries only catch with lag. Filtered to high-signal "landmine"/corporate-
# action catalysts so the screener isn't flooded with routine filings.
# DEFENSIVE: any failure returns [] and never breaks the scrape cycle.
# Toggle with BSE_ANNOUNCEMENTS_ENABLED (default on).
# ⚠️ Could NOT be verified against live data in the build environment (its
# network returned no BSE records for any date); validate in production via
# FEED_STATS['bse_announcements'] and the [BSE] logs.
_BSE_KEYWORDS = (
    "pledge", "encumbr", "rating", "downgrade", "upgrade", "resign", "auditor",
    "board meeting", "outcome of board", "fund rais", "fund-rais", "acqui",
    "amalgamat", "merger", "default", "insolvency", "nclt", "sebi", "fraud",
    "investigat", "open offer", "buyback", "buy-back", "bonus", "stock split",
    "stake sale", "preferential", "qip", "order", "contract", "demerger",
)
_BSE_IST = timezone(timedelta(hours=5, minutes=30))

def fetch_bse_announcements(lookback_days=1, cap=60):
    """Pull recent BSE filings, keyword-filtered to material catalysts, mapped to
    the standard article dict. Never raises."""
    if os.environ.get("BSE_ANNOUNCEMENTS_ENABLED", "1").lower() not in ("1", "true", "yes"):
        return []
    try:
        _now_ist = datetime.now(_BSE_IST)
        d_to = _now_ist.strftime("%Y%m%d")
        d_from = (_now_ist - timedelta(days=lookback_days)).strftime("%Y%m%d")
        url = ("https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
               f"?pageno=1&strCat=-1&strPrevDate={d_from}&strScrip=&strSearch=P"
               f"&strToDate={d_to}&strType=C&subcategory=-1")
        hdrs = {"User-Agent": _ua(), "Accept": "application/json",
                "Referer": "https://www.bseindia.com/corporates/ann.html",
                "Origin": "https://www.bseindia.com"}
        resp = HTTP_SESSION.get(url, headers=hdrs, timeout=12)
        if resp.status_code != 200:
            _feed_stat("bse_announcements", "fail", err=f"HTTP {resp.status_code}")
            return []
        try:
            data = resp.json()
        except Exception:
            _feed_stat("bse_announcements", "fail", err="non-json")
            return []
        rows = data.get("Table", []) if isinstance(data, dict) else []
        out = []
        for r in rows:
            company = str(r.get("SLONGNAME") or "").strip()
            subj = str(r.get("NEWSSUB") or r.get("HEADLINE") or "").strip()
            cat = str(r.get("CATEGORYNAME") or "").strip()
            if not company or not subj:
                continue
            if not any(k in f"{subj} {cat}".lower() for k in _BSE_KEYWORDS):
                continue
            _att = str(r.get("ATTACHMENTNAME") or "").strip()
            _url = (f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{_att}"
                    if _att else "https://www.bseindia.com/corporates/ann.html")
            out.append({
                "headline": f"{company}: {subj}"[:300],
                "time": r.get("NEWS_DT") or r.get("DT_TM") or "",
                "url": _url,
                "summary": str(r.get("MORE") or subj)[:900],
                "source": "BSE Filings",
            })
            if len(out) >= cap:
                break
        _feed_stat("bse_announcements", "ok", n_articles=len(out))
        return out
    except Exception as e:
        _feed_stat("bse_announcements", "fail", err=e)
        return []

# NOTE: SM_GEMINI_CLIENT (aimlapi.com) removed — key expired.
# quant_ai_screener now uses the google-genai 'client' (same as Phase 2)
# with pure rule-based fallback when no Gemini keys are available.

def scrape_article_text(url):
    """Fetches the article body text to give the AI better context. Thread-safe
    cache. Transient failures (timeout / 5xx / 429) are NOT cached so they retry
    next cycle; only success and permanent 4xx (403 paywall / 404 gone) cache."""
    if not url or "google.com" in url:
        return ""
    with _ARTICLE_TEXT_CACHE_LOCK:
        cached = ARTICLE_TEXT_CACHE.get(url)
    if cached is not None:
        return cached

    def _cache(val):
        with _ARTICLE_TEXT_CACHE_LOCK:
            if len(ARTICLE_TEXT_CACHE) >= _ARTICLE_TEXT_CACHE_MAX:
                try:
                    del ARTICLE_TEXT_CACHE[next(iter(ARTICLE_TEXT_CACHE))]
                except (StopIteration, KeyError):
                    pass
            ARTICLE_TEXT_CACHE[url] = val

    try:
        resp = HTTP_SESSION.get(url, timeout=5, headers={"User-Agent": _ua()})
        if resp.status_code == 200:
            # resp.content (bytes) lets BeautifulSoup detect encoding from the
            # HTML meta charset — avoids mojibake on mislabelled sites. Cap input
            # size to avoid pathological-HTML parse blowups.
            soup = BeautifulSoup(resp.content[:3_000_000], 'html.parser')
            paragraphs = soup.find_all('p')
            text = " ".join(p.get_text().strip() for p in paragraphs
                            if len(p.get_text().strip()) > 50)
            if len(text) < 80:
                # Site uses <div>/<article>/<section> for body, not <p> tags.
                blocks = soup.find_all(['article', 'section', 'div'])
                text = " ".join(b.get_text(" ", strip=True) for b in blocks
                                if len(b.get_text(strip=True)) > 200)
            result = text[:1500]
            _cache(result)
            return result
        # Permanent client errors (403 paywall, 404 gone) → cache empty so we
        # don't re-hit them. Transient (408/429/5xx) → return empty WITHOUT
        # caching, so the next cycle can retry.
        if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
            _cache("")
        return ""
    except Exception as e:
        print(f"   [Scrape Error] {url}: {e}")
        return ""  # transient (timeout/network) — do not cache, allow retry

def _format_to_rfc2822(time_str):
    """Normalize any common datetime string (ISO 8601, +0530 offset, naive)
    to RFC 2822 with explicit +0000 (matches what RSS feeds emit). Returns
    None if the string can't be parsed."""
    if not time_str:
        return None
    s = str(time_str).strip()
    # Try ISO-style first (most meta tags use this — "2026-05-26T03:42:11+05:30")
    try:
        s_iso = s.replace('Z', '+00:00')
        dt = _assume_tz(datetime.fromisoformat(s_iso))
        return dt.astimezone(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')
    except Exception:
        pass
    # Fall back to RFC 2822-style parser
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt:
            dt = _assume_tz(dt)
            return dt.astimezone(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')
    except Exception:
        pass
    return None


def fetch_published_time(url):
    """Fetch the canonical 'published time' from an article page by scraping
    standard publisher meta tags. Order of precedence:
        1. <meta property="article:published_time" content="...">  (OG / Facebook)
        2. <meta itemprop="datePublished" content="...">           (Schema.org)
        3. <meta name="pubdate" / "publishdate" / "DC.date.issued"> (legacy)
        4. <meta property="og:published_time" content="...">
        5. <time datetime="..." pubdate>                            (HTML5)
        6. JSON-LD "datePublished" field                            (Schema.org JSON)

    Returns an RFC-2822-formatted UTC string (matches RSS time format) or None.
    Results are cached per URL — including misses ('') — to avoid re-scraping.
    """
    if not url or "google.com" in url:
        return None
    cached = PUBLISHED_TIME_CACHE.get(url)
    if cached is not None:
        return cached or None  # '' = cached miss, treat as None

    raw = None
    try:
        resp = HTTP_SESSION.get(url, timeout=5)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            # 1-4: meta-tag patterns
            meta_patterns = [
                {'property': 'article:published_time'},
                {'itemprop': 'datePublished'},
                {'name': 'pubdate'},
                {'name': 'publishdate'},
                {'name': 'DC.date.issued'},
                {'property': 'og:published_time'},
                {'name': 'article:published_time'},
            ]
            for attrs in meta_patterns:
                el = soup.find('meta', attrs=attrs)
                if el and el.get('content'):
                    raw = el.get('content')
                    break
            # 5: HTML5 <time pubdate datetime="...">
            if not raw:
                t = soup.find('time', attrs={'pubdate': True}) or \
                    soup.find('time', attrs={'itemprop': 'datePublished'})
                if t and t.get('datetime'):
                    raw = t.get('datetime')
            # 6: JSON-LD
            if not raw:
                for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
                    try:
                        data = json.loads(script.string or '{}')
                    except Exception:
                        continue
                    candidates = data if isinstance(data, list) else [data]
                    for item in candidates:
                        if isinstance(item, dict) and item.get('datePublished'):
                            raw = item.get('datePublished')
                            break
                    if raw:
                        break
    except Exception as e:
        # Don't spam logs for every transient fail — debug-level only
        pass

    formatted = _format_to_rfc2822(raw) if raw else None

    # LRU evict + cache (empty string = miss marker so we don't re-scrape)
    if len(PUBLISHED_TIME_CACHE) >= _PUBLISHED_TIME_CACHE_MAX:
        try:
            oldest_key = next(iter(PUBLISHED_TIME_CACHE))
            del PUBLISHED_TIME_CACHE[oldest_key]
        except Exception:
            pass
    PUBLISHED_TIME_CACHE[url] = formatted or ''
    return formatted


def clean_json(raw_text):
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
    return json.loads(cleaned.strip())


# ──────────────────────────────────────────────────────────────────────────
# "THE RIPPLE" — propagation graphs for macro-grade big news
# Most news is a single-stock story. A small subset is *systemic* — crude
# crashing 5%, RBI cutting rates, a war breaking out, a major policy shift —
# and these events ripple across many stocks in predictable cascades.
# Show those (and only those) as a force-directed propagation graph.
# ──────────────────────────────────────────────────────────────────────────

# Keywords that hint at macro/systemic news (one of many factors in the score).
# ──────────────────────────────────────────────────────────────────────────
# MacroDataTracker (live commodity/FX/rates snapshot via Yahoo's free chart
# endpoint) was extracted to macro_tracker.py. Imported back; used class-level
# as MacroDataTracker.get_snapshot()/detect_shocks() throughout.
from marketdata.macro_tracker import MacroDataTracker


# Background warmer — refresh the snapshot every 5 minutes so the first
# news event that asks for it gets an instant answer instead of waiting
# on 13 HTTP calls.
def _macro_data_warmer():
    while True:
        try:
            MacroDataTracker.get_snapshot()
        except Exception:
            pass
        time.sleep(300)


# ──────────────────────────────────────────────────────────────────────────
# THE CALENDAR — forward-looking macro event schedule
#
# Manually curated; updated weekly. The seed below covers the week of
# June 1-7, 2026. To refresh next week:
#   • Edit CALENDAR_EVENTS_SEED (preferred), OR
#   • POST to /api/admin/calendar/upsert with new payloads.
#
# Each event carries AI-style scenario analysis (upside / expected /
# downside) so traders can pre-position. Historical analogues add the
# statistical grounding.
# ──────────────────────────────────────────────────────────────────────────
# The macro/economic calendar seed (a large static list of event dicts) was
# extracted to calendar_seed.py. seed_calendar_events() below imports it back.
from newsproc.calendar_seed import CALENDAR_EVENTS_SEED


def seed_calendar_events(force=False):
    """
    Populate economic_calendar with the curated weekly schedule.
    UNIQUE INDEX on (event_date, country, title) prevents duplicates so
    this is safe to call on every startup. Pass force=True to overwrite
    existing rows with the latest seed payload (useful when you edit
    descriptions/scenarios mid-week).
    """
    try:
        for ev in CALENDAR_EVENTS_SEED:
            scenarios_json = json.dumps(ev.get("scenarios") or {})
            analogues_json = json.dumps(ev.get("historical_analogues") or [])
            sectors_json   = json.dumps(ev.get("related_sectors") or [])
            tickers_json   = json.dumps(ev.get("related_tickers") or [])
            _ev = ev
            def _ins(conn, c, _e=_ev, _sj=scenarios_json, _aj=analogues_json,
                     _secj=sectors_json, _tj=tickers_json, _force=force):
                if _force:
                    c.execute("""DELETE FROM economic_calendar
                                 WHERE event_date = ? AND country = ? AND title = ?""",
                              (_e["event_date"], _e.get("country", ""), _e["title"]))
                c.execute("""
                    INSERT OR IGNORE INTO economic_calendar
                      (event_date, event_time_ist, title, country, category, importance,
                       description, prior_value, consensus_estimate,
                       scenarios_json, historical_analogues_json,
                       related_sectors_json, related_tickers_json,
                       status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    _e["event_date"], _e.get("event_time_ist", ""), _e["title"],
                    _e.get("country", ""), _e.get("category", ""), _e.get("importance", "MEDIUM"),
                    _e.get("description", ""), _e.get("prior_value", ""), _e.get("consensus_estimate", ""),
                    _sj, _aj, _secj, _tj, "upcoming",
                ))
            db_write(_ins)
        print(f"[CALENDAR] Seeded {len(CALENDAR_EVENTS_SEED)} events.", flush=True)
    except Exception as e:
        print(f"[CALENDAR] Seed error: {e}", flush=True)


# Catalyst types from the screener that hint at systemic impact.
_MACRO_CATALYST_TYPES = (
    "RBI", "FED", "FOMC", "MONETARY",
    "RATE", "POLICY", "BUDGET", "FISCAL",
    "COMMODITY", "CRUDE", "CURRENCY",
    "GEOPOLITICAL", "WAR", "TARIFF", "SANCTIONS",
    "ELECTION", "MACRO", "INFLATION",
)


def _ripple_score_for_signal(signal, impact_count=1):
    """
    Per-news ripple score. Intentionally NO headline-keyword scoring —
    the "is this macro?" decision is now made purely by MacroDataTracker
    looking at real instrument prices (not what the article says).
    News ripples are still allowed but only via screener-grade materiality
    + multi-stock breadth + after-hours timing — all quantitative inputs.
    """
    materiality = 0
    try:
        materiality = int(float(signal.get("quality_score") or 0))
    except Exception:
        materiality = 0
    score = max(0, min(100, materiality))
    catalyst = (signal.get("catalyst_type") or "").upper()
    if any(m in catalyst for m in _MACRO_CATALYST_TYPES):
        score += 10  # smaller bonus now that real macro detection runs separately
    if impact_count > 1:
        score += min(15, (impact_count - 1) * 5)
    try:
        _ist = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(_ist)
        if now_ist.weekday() >= 5:
            score += 10
        else:
            _t = now_ist.hour * 60 + now_ist.minute
            if _t < (9 * 60 + 15) or _t >= (15 * 60 + 30):
                score += 10
    except Exception:
        pass
    return max(0, min(100, score))


def is_big_news(signal, impact_count=1):
    """Convenience wrapper — returns (is_big, score)."""
    threshold = int(os.environ.get("BIG_NEWS_THRESHOLD", "85"))  # raised: news no longer benefits from keyword bonus
    score = _ripple_score_for_signal(signal, impact_count=impact_count)
    return (score >= threshold, score)


def generate_ripple_graph(headline, body, catalyst_type, primary_tickers):
    """
    Single Gemini call that expands a macro news event into a 3-tier
    propagation graph.

    Returns a dict with the shape:
        {
          "summary": "1-sentence framing of why this is systemic",
          "tiers": [
            {"tier": 1, "label": "Direct Impact", "nodes": [
                {"ticker": "ONGC.NS", "direction": "BEARISH",
                 "confidence": 88, "reason": "..."}
            ]},
            {"tier": 2, "label": "Second-Order (Supply Chain)", "nodes": [...]},
            {"tier": 3, "label": "Macro Transmission", "nodes": [...]}
          ]
        }
    or None if generation failed.

    Designed to be called from a background thread or the API endpoint —
    never inline in the worker save loop because it costs ~3-4s of latency.
    """
    available = _available_gemini_key_indices()
    if not available:
        return None

    primary_tickers_str = ", ".join(primary_tickers) if primary_tickers else "none yet identified"
    prompt = f"""You are the Chief Macro Strategist at a top Indian hedge fund.
DOMAIN GUIDANCE FOR ACCURACY:
To help you make extremely accurate, institutional-grade predictions, here is the official mapping of news categories and systemic triggers to Indian equities:
- Energy/Oil news (crude, gas):
  * Rising crude price is BULLISH for upstream (ONGC.NS, OIL.NS, RELIANCE.NS) but BEARISH for downstream/OMCs (BPCL.NS, IOC.NS, HPCL.NS), paints (ASIANPAINT.NS, BERGEPAINT.NS), aviation (INDIGO.NS), and tyres (APOLLOTYRE.NS, MRF.NS).
  * Falling crude price is BEARISH for upstream (ONGC.NS) but BULLISH for downstream/OMCs (BPCL.NS, HPCL.NS, IOC.NS), paints (ASIANPAINT.NS), aviation (INDIGO.NS), and tyres (APOLLOTYRE.NS).
- Inflation/Rates/Monetary Policy (RBI/Fed):
  * Rate cuts / dovish sentiment is BULLISH for interest-rate sensitives: Banks/NBFCs (HDFCBANK.NS, ICICIBANK.NS, SBIN.NS, BAJFINANCE.NS), Real Estate (DLF.NS, OBEROIRLTY.NS), and Autos (MARUTI.NS, TATAMOTORS.NS).
  * Rate hikes / hawkish sentiment is BEARISH for Banks, Real Estate, and Autos.
- IT demand / US Economic data / US slow downs:
  * Positive demand / US growth is BULLISH for IT services exporters (TCS.NS, INFY.NS, HCLTECH.NS, WIPRO.NS, TECHM.NS).
  * Negative demand / US recession fears is BEARISH for IT services.
- Geopolitical tensions / safe-havens:
  * Volatility / rising tension is BULLISH for Defensives: Gold (TITAN.NS), FMCG (ITC.NS, HINDUNILVR.NS), and Pharma (SUNPHARMA.NS, CIPLA.NS).
- Capital expenditures/Infrastructure/Budget:
  * Increased public spending is BULLISH for Capital Goods & Infrastructure: L&T (LT.NS), SIEMENS.NS, ABB.NS, BHEL.NS.
- Rural economy/Monsoon:
  * Good monsoon / rural stimulus is BULLISH for FMCG (HINDUNILVR.NS, DABUR.NS) and Auto/Tractors (M&M.NS, MARUTI.NS, ESCORTS.NS).

A SYSTEMIC news event just hit. Your job: build the propagation graph showing
HOW this event will ripple across the Indian equity market in 3 tiers.

EVENT
  Headline:   {headline}
  Catalyst:   {catalyst_type or "macro event"}
  Direct hits identified so far: {primary_tickers_str}
  Context:    {(body or "")[:600]}

OUTPUT — three tiers of impact, NSE-listed tickers only (.NS suffix):

TIER 1 — DIRECT IMPACT (3-6 tickers)
  Companies directly named or whose P&L is hit in the next session.
  Example: crude crashes → ONGC, RELIANCE, Cairn

TIER 2 — SECOND-ORDER / SUPPLY CHAIN (5-10 tickers)
  Companies one step removed: customers / suppliers / competitors.
  Example: crude down → BPCL/IOC margin EXPANSION (refining win),
           airlines (INDIGO) fuel cost win,
           tyres (APOLLOTYRE) input cost win

TIER 3 — MACRO TRANSMISSION (5-10 tickers)
  Companies hit by the broader macro consequence — inflation, rates,
  FII flow, currency, sector rotation.
  Example: crude down → RBI dovish path → rate-sensitives (HDFCBANK,
           DLF, BAJFINANCE) → AUTO via rural demand (MARUTI)

For each ticker also state:
  • direction:   BULLISH or BEARISH
  • confidence:  0-100, honest probability the predicted move plays out
                  in next 1-3 sessions
  • reason:      one-sentence specific causal chain (NOT generic)

Return STRICT valid JSON, no markdown fences:

{{
  "summary": "1-sentence framing of why this is systemic",
  "tiers": [
    {{ "tier": 1, "label": "Direct Impact",
       "nodes": [
         {{ "ticker": "X.NS", "direction": "BEARISH", "confidence": 85,
            "reason": "specific reason" }}
       ]
    }},
    {{ "tier": 2, "label": "Second-Order (Supply Chain)",
       "nodes": [ ... ]
    }},
    {{ "tier": 3, "label": "Macro Transmission",
       "nodes": [ ... ]
    }}
  ]
}}"""

    # Randomize starting key so concurrent ripples spread across the rotation.
    import random as _rnd
    try_order = list(available)
    _rnd.shuffle(try_order)

    raw = None
    for _key_idx in try_order:
        try:
            _set_active_gemini_client(_key_idx)
            resp = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            raw = resp.text
            _reset_gemini_key_strikes(_key_idx)
            break
        except Exception as _e:
            if _is_gemini_quota_error(_e):
                _mark_gemini_key_quota_hit(_key_idx)
                continue
            if _is_gemini_transient_error(_e):
                _mark_gemini_key_quota_hit(_key_idx, cooldown_secs=_GEMINI_TRANSIENT_COOLDOWN_SECS)
                continue
            continue

    if not raw:
        return None
    try:
        data = clean_json(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or "tiers" not in data:
        return None
    return data


def save_ripple_to_db(news_id, ripple_score, is_big, ripple_data):
    """Persist a ripple graph against a news_id; flip news.has_ripple."""
    payload = json.dumps(ripple_data) if ripple_data else None
    def _write(conn, c, _nid=news_id, _sc=ripple_score, _big=int(bool(is_big)), _pl=payload):
        c.execute(
            "INSERT OR REPLACE INTO news_ripple (news_id, ripple_score, is_big_news, ripple_json, generated_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (_nid, _sc, _big, _pl)
        )
        c.execute("UPDATE news SET has_ripple = ? WHERE id = ?", (1 if _pl else 0, _nid))
    db_write(_write)


# ──────────────────────────────────────────────────────────────────────────
# Macro-driven ripple generator + shock detector
# Triggered purely by quantitative price moves — never by news headlines.
# ──────────────────────────────────────────────────────────────────────────
def generate_macro_ripple_graph(instrument):
    """
    One Gemini call. Builds a 3-tier propagation graph for a macro shock.
    Input: instrument dict from MacroDataTracker.detect_shocks() —
        { key, symbol, label, last, prev_close, change_pct_1d,
          shock_level, threshold_pct }
    Returns the same shape as the news ripple generator, plus a 'trigger'
    field that names the macro instrument and its move.
    """
    available = _available_gemini_key_indices()
    if not available:
        return None

    label = instrument.get('label') or instrument.get('symbol')
    pct = instrument.get('change_pct_1d', 0)
    last = instrument.get('last')
    prev = instrument.get('prev_close')
    shock_level = instrument.get('shock_level') or 'SHOCK'
    direction_word = "up" if pct > 0 else "down"

    prompt = f"""You are the Chief Macro Strategist at a top Indian hedge fund.
DOMAIN GUIDANCE FOR ACCURACY:
To help you make extremely accurate, institutional-grade predictions, here is the official mapping of systemic shocks to Indian equities:
- BRENT CRUDE / WTI CRUDE:
  * If UP (BULLISH for upstream oil & gas: ONGC.NS, OIL.NS, RELIANCE.NS. BEARISH for paint: ASIANPAINT.NS, BERGEPAINT.NS; aviation: INDIGO.NS; OMCs: BPCL.NS, IOC.NS, HPCL.NS; tyre: APOLLOTYRE.NS, MRF.NS).
  * If DOWN (BEARISH for upstream oil & gas: ONGC.NS, OIL.NS. BULLISH for paint: ASIANPAINT.NS, BERGEPAINT.NS; aviation: INDIGO.NS; OMCs: BPCL.NS, IOC.NS, HPCL.NS; tyre: APOLLOTYRE.NS, MRF.NS).
- GOLD / SILVER:
  * If UP (BULLISH for jewellery: TITAN.NS, KALYANKJIL.NS; gold financiers: MUTHOOTFIN.NS, MANAPPURAM.NS).
  * If DOWN (BEARISH for jewellery: TITAN.NS; gold financiers: MUTHOOTFIN.NS).
- DXY (Dollar Index) / USD/INR:
  * If UP/Strong Dollar (BULLISH for export-heavy sectors: IT: TCS.NS, INFY.NS, HCLTECH.NS, WIPRO.NS, TECHM.NS; Pharma: SUNPHARMA.NS, CIPLA.NS, DRREDDY.NS).
  * If DOWN/Weak Dollar (BEARISH/neutral for IT & Pharma; BULLISH for domestic cyclical/consumers).
- INDIA VIX / US VIX (Volatility Spike):
  * If UP (BEARISH for high-beta and financials: HDFCBANK.NS, ICICIBANK.NS, SBIN.NS, DLF.NS. BULLISH/Defensive for IT: TCS.NS, INFY.NS; Pharma: SUNPHARMA.NS; FMCG: ITC.NS, HINDUNILVR.NS).
- NIFTY 50 / BANK NIFTY:
  * If UP (BULLISH for heavyweight financials: HDFCBANK.NS, ICICIBANK.NS, SBIN.NS, AXISBANK.NS, and conglomerates: RELIANCE.NS).
  * If DOWN (BEARISH for high-beta financials and momentum stocks).
- US 10Y YIELD:
  * If UP (BEARISH for growth sectors like IT: TCS.NS, INFY.NS and highly-leveraged real estate / infrastructure: DLF.NS, LT.NS).
  * If DOWN (BULLISH for growth sectors like IT: TCS.NS, INFY.NS and interest-rate sensitives).

A SYSTEMIC market shock just printed in real prices. Build the propagation
graph showing exactly how this move will cascade across Indian equities in
3 tiers.

EVENT (purely quantitative — no news interpretation)
  Instrument:    {label} ({instrument.get('symbol')})
  Move (1-day):  {pct:+.2f}% (closed {direction_word})
  Last price:    {last}
  Prev close:    {prev}
  Shock level:   {shock_level}

OUTPUT — three tiers of impact, NSE-listed tickers only (.NS suffix):

TIER 1 — DIRECT IMPACT (3-6 tickers)
  Companies whose Q-on-Q P&L is directly affected by THIS instrument's move.
  Example: crude {direction_word} 5%+ → ONGC, RELIANCE, OIL upstream margin shift.

TIER 2 — SECOND-ORDER / SUPPLY CHAIN (5-10 tickers)
  Companies one link removed: customers / suppliers / direct beneficiaries
  or losers of the input-cost change.
  Example: crude down → BPCL/IOC refining margin EXPANSION, INDIGO/SPICEJET
           fuel-cost relief, APOLLOTYRE input-cost relief.

TIER 3 — MACRO TRANSMISSION (5-10 tickers)
  Companies hit by the broader macro consequence — inflation path, rates,
  FII flow, currency, sector rotation.
  Example: crude down → softer CPI → RBI dovish path → rate-sensitives
           (HDFCBANK, DLF, BAJFINANCE) and AUTO via rural demand (MARUTI).

For each ticker:
  • direction:   BULLISH or BEARISH
  • confidence:  0-100, honest probability of the predicted next-session move
  • reason:      one-sentence specific causal chain (NOT generic)

Return STRICT valid JSON, no markdown fences:

{{
  "summary": "1-sentence framing of why this {pct:+.2f}% move in {label} is systemic for NSE",
  "trigger": {{
    "instrument": "{label}",
    "symbol": "{instrument.get('symbol')}",
    "change_pct_1d": {pct},
    "last": {last},
    "prev_close": {prev},
    "shock_level": "{shock_level}"
  }},
  "tiers": [
    {{ "tier": 1, "label": "Direct Impact",
       "nodes": [
         {{ "ticker": "X.NS", "direction": "BEARISH", "confidence": 85,
            "reason": "specific reason" }}
       ]
    }},
    {{ "tier": 2, "label": "Second-Order (Supply Chain)",
       "nodes": [ ... ]
    }},
    {{ "tier": 3, "label": "Macro Transmission",
       "nodes": [ ... ]
    }}
  ]
}}"""

    import random as _rnd
    try_order = list(available)
    _rnd.shuffle(try_order)
    raw = None
    for _key_idx in try_order:
        try:
            _set_active_gemini_client(_key_idx)
            resp = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            raw = resp.text
            _reset_gemini_key_strikes(_key_idx)
            break
        except Exception as _e:
            if _is_gemini_quota_error(_e):
                _mark_gemini_key_quota_hit(_key_idx)
                continue
            if _is_gemini_transient_error(_e):
                _mark_gemini_key_quota_hit(_key_idx, cooldown_secs=_GEMINI_TRANSIENT_COOLDOWN_SECS)
                continue
            continue
    if not raw:
        return None
    try:
        data = clean_json(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or "tiers" not in data:
        return None
    # Always overwrite trigger with the actual numbers so we don't trust AI
    # echo of them.
    data['trigger'] = {
        'instrument':     label,
        'symbol':         instrument.get('symbol'),
        'change_pct_1d':  pct,
        'last':           last,
        'prev_close':     prev,
        'shock_level':    shock_level,
    }
    return data


def macro_shock_worker():
    """
    Background worker that polls MacroDataTracker every N minutes,
    detects shocks (purely on % move thresholds), and triggers ripple
    generation — but ONLY when NSE is closed.

    Why: shocks that print during NSE hours have already been (partially)
    absorbed by ONGC/RELIANCE/etc. in real-time. The ripple becomes
    hindsight. We only want to fire when NSE is CLOSED so the user has a
    real positioning window before the next open.

    Modes (env: MACRO_NSE_HOURS_MODE):
      • skip   (default)  — no detection during NSE hours. Cleanest.
      • flag              — detect during NSE hours but mark each event
                            with during_nse_hours=1 so UI can de-emphasize.
      • always            — detect everything (original behaviour).
    """
    poll_secs = int(os.environ.get("MACRO_POLL_SECS", "600"))
    dedupe_hours = int(os.environ.get("MACRO_DEDUPE_HOURS", "6"))
    expire_hours = int(os.environ.get("MACRO_EXPIRE_HOURS", "24"))
    nse_hours_mode = os.environ.get("MACRO_NSE_HOURS_MODE", "skip").lower().strip()
    if nse_hours_mode not in ("skip", "flag", "always"):
        nse_hours_mode = "skip"
    # Warm-up: give the rest of startup ~90s before first poll
    time.sleep(90)
    print(f"[MACRO] Shock worker started — poll {poll_secs}s, dedupe {dedupe_hours}h, NSE-hours mode={nse_hours_mode}.", flush=True)
    while True:
        try:
            _heartbeat("macro_shock", last_cycle_started_at=time.time())
            # ── NSE-hours gate ──
            # During the 9:15-15:30 IST trading window, any move large enough
            # to qualify as a "shock" has already been (at least partially)
            # absorbed by Indian stocks in real-time. We default to skipping
            # detection until NSE closes so every ripple we surface gives the
            # trader a real positioning window before the next open.
            nse_open_now = False
            try:
                nse_open_now = bool(is_market_open())
            except Exception:
                pass
            if nse_open_now and nse_hours_mode == "skip":
                # Sleep until next poll, but log on the cycle so we know
                # the worker is alive.
                print(f"[MACRO] NSE open — detection paused (mode=skip).", flush=True)
                # Still a "completed" cycle for liveness purposes — it ran,
                # decided to skip, will run again. Without this the health
                # check would think the worker is hung whenever NSE is open.
                _heartbeat("macro_shock",
                           last_cycle_finished_at=time.time(),
                           last_shocks_detected=0,
                           cycles_completed=WORKER_HEARTBEAT["macro_shock"].get("cycles_completed", 0) + 1)
                time.sleep(poll_secs)
                continue

            shocks = MacroDataTracker.detect_shocks()
            if shocks:
                _wnd = "AFTER-HOURS" if not nse_open_now else "NSE-OPEN"
                print(f"[MACRO] {len(shocks)} live shock(s) [{_wnd}]: " +
                      ", ".join(f"{s['key']} {s['change_pct_1d']:+.2f}%" for s in shocks),
                      flush=True)
            for s in shocks:
                # Dedup: skip if we already have a recent event for this instrument.
                try:
                    conn = connect_news_db()
                    c = conn.cursor()
                    cutoff = (datetime.now(timezone.utc) - timedelta(hours=dedupe_hours)).strftime('%Y-%m-%d %H:%M:%S')
                    c.execute(
                        "SELECT id FROM macro_event WHERE instrument_key = ? AND detected_at >= ? "
                        "ORDER BY detected_at DESC LIMIT 1",
                        (s['key'], cutoff)
                    )
                    row = c.fetchone()
                    conn.close()
                    if row:
                        continue
                except Exception as _e:
                    print(f"[MACRO] dedup query error: {_e}", flush=True)
                    continue
                # Generate the ripple graph
                graph = generate_macro_ripple_graph(s)
                if not graph:
                    # Save with no ripple — UI can show the shock alone
                    print(f"[MACRO] ripple generation failed for {s['key']} (saved with no graph)", flush=True)
                ripple_payload = json.dumps(graph) if graph else None
                _exp = (datetime.now(timezone.utc) + timedelta(hours=expire_hours)).strftime('%Y-%m-%d %H:%M:%S')
                _s_snap = dict(s)
                _during_nse = int(bool(nse_open_now))
                def _insert_macro(conn, c, _s=_s_snap, _g=ripple_payload, _exp=_exp, _dn=_during_nse):
                    c.execute(
                        """INSERT INTO macro_event
                            (instrument_key, instrument_label, symbol, shock_level,
                             change_pct_1d, last_price, prev_close, ripple_json,
                             expires_at, during_nse_hours)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (_s['key'], _s.get('label'), _s.get('symbol'), _s.get('shock_level'),
                         _s.get('change_pct_1d'), _s.get('last'), _s.get('prev_close'), _g, _exp, _dn)
                    )
                db_write(_insert_macro)
                _action = "ACTIONABLE (NSE closed)" if not nse_open_now else "INFO (NSE open)"
                print(f"[MACRO] saved event: {s['key']} {s['change_pct_1d']:+.2f}% ({s['shock_level']}) — {_action}", flush=True)
            _heartbeat("macro_shock",
                       last_cycle_finished_at=time.time(),
                       last_shocks_detected=len(shocks),
                       cycles_completed=WORKER_HEARTBEAT["macro_shock"].get("cycles_completed", 0) + 1)
        except Exception as e:
            print(f"[MACRO] worker error: {e}", flush=True)
            _heartbeat("macro_shock", last_error=str(e)[:200], last_error_at=time.time())
        time.sleep(poll_secs)


def strip_html(value):
    if not value:
        return ""
    try:
        return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    except Exception:
        return str(value)

def extract_json_from_text(raw_text):
    """Best-effort JSON extraction for model responses wrapped in markdown/text."""
    if not raw_text:
        raise ValueError("empty model response")
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Prefer arrays for batch screeners, then objects for single-response prompts.
    for open_char, close_char in (("[", "]"), ("{", "}")):
        start = cleaned.find(open_char)
        end = cleaned.rfind(close_char)
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
    raise ValueError("no JSON payload found")

import re  # app.py module-level re (the rules block that declared it moved to news_rules.py)

# Rule-based news classification (keyword filter, sentiment & category lists,
# instant ticker mapping) was extracted to news_rules.py. Imported back so every
# call site (is_finance_relevant / classify_category / STOCK_KEYWORD_MAP / ...) resolves.
from newsproc.news_rules import (
    FINANCE_KEYWORDS, is_finance_relevant,
    BULLISH_KEYWORDS, BEARISH_KEYWORDS,
    CATEGORY_KEYWORDS, classify_category,
    STOCK_KEYWORD_MAP,
)

# Static news-engine data tables (macro impact map, materiality / noise keyword
# lists, ticker-parsing sets) were extracted to news_data.py. Imported back so
# every reference resolves exactly as before.
from newsproc.news_data import (
    MACRO_IMPACT_MAP, MATERIAL_EVENT_KEYWORDS, LOW_SIGNAL_PHRASES,
    INDEX_LIKE_SYMBOLS, COMMON_UPPERCASE_WORDS,
)

# Ticker normalization + news-candidate screening helpers were extracted to
# ticker_utils.py (pure: stdlib + angelone_shim + news_rules/news_data). Imported
# back so every call site (normalize_ticker / candidate_quality_score / ...) resolves.
from marketdata.ticker_utils import (
    normalize_ticker, ticker_base, is_supported_equity_ticker,
    _keyword_mentions_ticker, _macro_mentions, _headline_direction,
    candidate_quality_score,
)

def rank_signal_candidates(article, candidates, max_results=5):
    headline = article.get("headline", "") if isinstance(article, dict) else ""
    context = article.get("deep_context", "") or article.get("summary", "") if isinstance(article, dict) else ""
    merged = {}

    for item in candidates:
        if isinstance(item, dict):
            ticker = normalize_ticker(item.get("ticker"))
            direction = (item.get("direction") or item.get("bias") or "").upper()
            source = item.get("source", "llm")
            materiality = item.get("materiality_score") or item.get("confidence") or 70
            reason = item.get("reason", "")
        else:
            ticker = normalize_ticker(item[0] if len(item) > 0 else None)
            direction = (item[1] if len(item) > 1 else _headline_direction(headline, context)).upper()
            source = item[2] if len(item) > 2 else "rule"
            materiality = 62
            reason = ""

        if not ticker or direction not in ("BULLISH", "BEARISH"):
            continue
        if not is_supported_equity_ticker(ticker):
            continue

        score = candidate_quality_score(headline, context, ticker, source, materiality)
        existing = merged.get(ticker)
        if not existing or score > existing["quality_score"]:
            merged[ticker] = {
                "ticker": ticker,
                "direction": direction,
                "quality_score": score,
                "source": source,
                "reason": reason,
            }

    ranked = sorted(merged.values(), key=lambda x: x["quality_score"], reverse=True)
    return ranked[:max_results]

def article_screening_context(article, max_chars=900):
    """Use RSS summary first, then scrape article text for richer stock picking."""
    if not isinstance(article, dict):
        return ""
    cached = article.get("deep_context")
    if cached:
        return cached

    summary = strip_html(article.get("summary", ""))
    context = summary[:max_chars]
    headline = article.get("headline", "")
    url = article.get("url")

    should_scrape = (
        url
        and "google.com" not in url
        and len(context) < 240
        and is_finance_relevant(f"{headline} {context}")
    )
    if should_scrape:
        scraped = scrape_article_text(url)
        if scraped and len(scraped) > len(context):
            context = scraped[:max_chars]

    article["deep_context"] = context
    return context

def _fallback_get_candidate_stocks(headline, context=""):
    """Fallback method using static dictionaries if API fails."""
    h = f"{headline or ''} {context or ''}".lower()
    candidates = {}

    headline_sentiment = _headline_direction(headline, context)

    for keyword, ticker in sorted(STOCK_KEYWORD_MAP.items(), key=lambda x: -len(x[0])):
        ticker = normalize_ticker(ticker)
        if not ticker or ticker in candidates:
            continue
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, h):
            candidates[ticker] = headline_sentiment

    # Catch explicit exchange symbols already present in uppercase headlines.
    original_text = f"{headline or ''} {context or ''}"
    for token in re.findall(r'\b[A-Z][A-Z0-9&\-]{2,15}\b', original_text):
        if token in COMMON_UPPERCASE_WORDS:
            continue
        ticker = normalize_ticker(token)
        if ticker and ticker not in candidates and is_supported_equity_ticker(ticker):
            candidates[ticker] = headline_sentiment

    for macro_kw, effects in MACRO_IMPACT_MAP.items():
        if macro_kw in h:
            for ticker, impact in effects:
                ticker = normalize_ticker(ticker)
                if ticker and ticker not in candidates:
                    candidates[ticker] = impact

    article = {"headline": headline or "", "summary": context or "", "deep_context": context or ""}
    ranked = rank_signal_candidates(
        article,
        [{"ticker": t, "direction": d, "source": "macro" if _macro_mentions(h) else "rule"} for t, d in candidates.items()],
        max_results=10,
    )

    # Bug #11 fix: apply the same 24-hour RECENT_SIGNALS cooldown guard that the
    # main LLM path uses.  Without this, rule-based fallback bypasses deduplication
    # when AI quota is exhausted, allowing duplicate signals within the cooldown window.
    _now = datetime.now(timezone.utc)
    _cooldown_window = timedelta(hours=24)
    filtered = []
    for ticker, direction in [(item["ticker"], item["direction"]) for item in ranked]:
        _key = f"{ticker}:{direction}"
        _last = RECENT_SIGNALS.get(_key)
        if _last and (_now - _last) < _cooldown_window:
            continue  # still within 24-hour cooldown — skip duplicate
        filtered.append((ticker, direction))
    return filtered


def get_candidate_stocks(headline, api_client, model_name, context=""):
    """
    HYBRID approach: Runs BOTH Gemini LLM AND rule-based keyword/macro map,
    then unions the results. This ensures:
    - Macro news (RBI rates, FII flows, crude oil) always generates signals via MACRO_IMPACT_MAP
    - Stock-specific news gets precise tickers from the LLM
    - Never returns empty for finance-relevant headlines
    Returns list of tuples: [(ticker, impact_direction)]
    """
    # Step 1: Only run LLM (keywords removed)
    llm_results = []
    if api_client:
        prompt = (
            "As a quantitative researcher in the Indian equities market, evaluate this news.\n"
            f"Headline: {headline}\n"
            f"Article context: {context[:1000] if context else 'Not available'}\n\n"
            "1) Identify only NSE/BSE listed equities with a direct or clearly traceable second-order impact. Append .NS or .BO.\n"
            "2) Prefer the company named in the news. Use sector beneficiaries only for macro/policy/commodity news with a clear pathway.\n"
            "3) Limit to the 1-3 highest quality tickers. Do not invent tickers. Ignore generic analyst target lists and broad market commentary.\n"
            "4) Determine the forward-looking bias for each (BULLISH/BEARISH).\n"
            "5) Classify materiality as MATERIAL only if the move could plausibly matter over 1-5 sessions.\n"
            "Return exactly formatted JSON like this:\n"
            "{\n"
            '  "materiality": "MATERIAL",\n'
            '  "impacts": [\n'
            '    {"ticker": "TCS.NS", "bias": "BULLISH", "confidence": 82, "reason": "earnings beat"}\n'
            "  ]\n"
            "}\n"
        )
        try:
            response = api_client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            data = extract_json_from_text(response.text)
            if isinstance(data, dict):
                if data.get("materiality") != "IGNORE":
                    for item in data.get("impacts", []):
                        ticker = normalize_ticker(item.get("ticker", ""))
                        bias = item.get("bias", "NEUTRAL").upper()
                        if ticker and bias in ("BULLISH", "BEARISH"):
                            llm_results.append({
                                "ticker": ticker,
                                "direction": bias,
                                "source": "llm",
                                "confidence": item.get("confidence", 75),
                                "reason": item.get("reason", ""),
                            })
        except Exception as e:
            print(f"   [!] LLM Target Extraction Error: {e}")

    # Return LLM results directly (no rule-based additions)
    article = {"headline": headline, "summary": context, "deep_context": context}
    ranked = rank_signal_candidates(
        article,
        llm_results,
        max_results=5,
    )
    candidates = [(item["ticker"], item["direction"]) for item in ranked]
    return candidates



# ==========================================
# BULLETPROOF BASE PRICE FETCHER
# ==========================================
def _extract_scalar(val):
    """Safely extract a scalar float from a yfinance cell (handles Series/DataFrame multi-index)."""
    if val is None:
        return None
    if hasattr(val, 'iloc'):
        # It's a Series or DataFrame — dig out the first numeric value
        val = val.iloc[0]
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def get_base_price_at_time(ticker, pub_dt_ist):
    """
    Returns the stock price at the moment of news publication.
    Uses Angel One 1-min candles (primary) -> Yahoo Finance 5d/1m (fallback).
    pub_dt_ist must be an IST-aware datetime.
    """
    IST = timezone(timedelta(hours=5, minutes=30))

    # -- PRIMARY: Angel One 1-min candles --
    try:
        exchange, token = yf._get_exchange_token(ticker)
        if exchange and token:
            from_dt = pub_dt_ist - timedelta(hours=1)
            to_dt   = pub_dt_ist + timedelta(minutes=5)
            df = yf._ao_get_candle(exchange, token, 'ONE_MINUTE', from_dt, to_dt)
            if not df.empty:
                df.index = df.index.tz_convert(IST)
                past = df[df.index <= pub_dt_ist]
                if not past.empty:
                    price = float(past.iloc[-1]['Close'])
                    if price > 0:
                        print(f"   [Price] {ticker} @ {pub_dt_ist.strftime('%H:%M IST')}: {price:.2f} (AngelOne 1m)")
                        return round(price, 2)
    except Exception as e:
        print(f"   [Price] AngelOne 1m error for {ticker}: {e}")

    # -- FALLBACK: Yahoo Finance 5d/1m chart --
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1m"
        resp = HTTP_SESSION.get(url, timeout=10)
        data = resp.json()
        result   = data['chart']['result'][0]
        tss      = result['timestamp']
        closes   = result['indicators']['quote'][0]['close']
        best_p, best_t = None, None
        for ts, cl in zip(tss, closes):
            if cl is None:
                continue
            bar_dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST)
            if bar_dt <= pub_dt_ist:
                best_p = cl
                best_t = bar_dt
        if best_p and best_p > 0:
            print(f"   [Price] {ticker} @ {best_t.strftime('%H:%M IST')}: {best_p:.2f} (Yahoo 1m)")
            return round(best_p, 2)
    except Exception as e:
        print(f"   [Price] Yahoo 1m error for {ticker}: {e}")

    # -- LAST RESORT: Angel One prev_close --
    try:
        ltp, prev, _, _ = yf._get_cached_quote(ticker)
        if prev and prev > 0:
            print(f"   [Price] {ticker}: {prev:.2f} (prev_close last resort)")
            return round(prev, 2)
    except Exception:
        pass

    return 0.0


def get_close_on_or_before_publication(ticker, pub_dt_ist, lookback_days=10):
    """
    Return the last completed daily close at or before the publication time.
    Used for news that lands outside market hours, including delayed AI
    analysis after Gemini keys recover.
    """
    try:
        hist = yf.Ticker(ticker).history(period=f'{lookback_days}d', interval='1d')
        if hist is None or hist.empty or 'Close' not in hist:
            return 0.0

        if getattr(hist.index, 'tz', None) is None:
            idx = hist.index.tz_localize(timezone.utc)
        else:
            idx = hist.index.tz_convert(timezone(timedelta(hours=5, minutes=30)))

        closes = []
        pub_date = pub_dt_ist.date()
        for dt_idx, close in zip(idx, hist['Close'].tolist()):
            try:
                bar_date = dt_idx.date()
                close_price = float(close)
            except Exception:
                continue
            if close_price > 0 and bar_date <= pub_date:
                closes.append((bar_date, close_price))

        if closes:
            return round(closes[-1][1], 2)
    except Exception as e:
        print(f"   [Price] Historical daily close error for {ticker}: {e}")

    return 0.0


def get_signal_prices_for_publication(ticker, publication_time):
    """
    Resolve signal entry/current prices from the original news publication
    timestamp, not the time the AI analysis happens.
    Returns (base_price, current_price_now, publication_utc_str).
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    pub_dt = None
    if isinstance(publication_time, datetime):
        pub_dt = publication_time
    elif publication_time:
        try:
            pub_dt = parsedate_to_datetime(str(publication_time))
        except Exception:
            try:
                pub_dt = datetime.strptime(str(publication_time), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            except Exception:
                pub_dt = None

    if pub_dt is None:
        pub_dt = datetime.now(timezone.utc)
    if pub_dt.tzinfo is None:
        pub_dt = pub_dt.replace(tzinfo=timezone.utc)

    pub_dt_ist = pub_dt.astimezone(ist)
    pub_dt_utc_str = pub_dt_ist.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    news_during_market = (
        pub_dt_ist.weekday() < 5 and
        not is_market_holiday(pub_dt_ist.month, pub_dt_ist.day, pub_dt_ist.year) and
        (9 * 60 + 15) <= (pub_dt_ist.hour * 60 + pub_dt_ist.minute) < (15 * 60 + 30)
    )

    ltp_val = prev_val = 0.0
    try:
        ltp, prev, _, _ = yf._get_cached_quote(ticker)
        ltp_val = round(float(ltp), 2) if (ltp and ltp > 0) else 0.0
        prev_val = round(float(prev), 2) if (prev and prev > 0) else 0.0
    except Exception:
        pass

    if news_during_market:
        base_price = get_base_price_at_time(ticker, pub_dt_ist)
        if base_price <= 0:
            base_price = get_close_on_or_before_publication(ticker, pub_dt_ist)
        if base_price <= 0:
            base_price = prev_val if prev_val > 0 else ltp_val
    else:
        base_price = get_close_on_or_before_publication(ticker, pub_dt_ist)
        if base_price <= 0:
            base_price = prev_val if prev_val > 0 else ltp_val

    if has_market_traded_since(pub_dt_utc_str):
        current_price_now = ltp_val if ltp_val > 0 else base_price
        if not is_market_open():
            try:
                quote_fn = globals().get('get_stock_market_change_quote')
                if quote_fn:
                    quote_price = _positive_float(quote_fn(ticker, market_open=False).get('price'))
                    if quote_price:
                        current_price_now = quote_price
            except Exception:
                pass
    else:
        current_price_now = base_price

    return round(base_price, 2) if base_price else 0.0, round(current_price_now, 2) if current_price_now else 0.0, pub_dt_utc_str


def get_price_with_range(ticker, market_open=None):
    """
    Returns (current_price, eval_high, eval_low) for stop/target evaluation.
    MARKET OPEN  : Angel One LTP + day high/low (live, real-time).
    MARKET CLOSED: Completed daily-session close, matching portfolio pricing.
                   Falls back to Yahoo/meta and then Angel One quote data.
    """
    if market_open is None:
        market_open = is_market_open()

    if market_open:
        ltp, prev, dh, dl = yf._get_cached_quote(ticker)
        if not ltp or ltp <= 0:
            return None, None, None
        current   = round(float(ltp), 2)
        eval_high = round(float(dh), 2) if dh and dh > 0 else current
        eval_low  = round(float(dl), 2) if dl and dl > 0 else current
        return current, eval_high, eval_low
    else:
        # Market closed: use the completed daily-session close used by the
        # portfolio cards. Yahoo regularMarketPrice can drift from the actual
        # official close for some NSE symbols after hours.
        last_close, _ = get_last_closed_session_quote(ticker)
        if last_close and last_close > 0:
            return last_close, last_close, last_close

        official_close = _get_yahoo_official_close(ticker)
        if official_close and official_close > 0:
            return official_close, official_close, official_close
        # Fallback: use last known prev_close when available to avoid post-close LTP drift.
        ltp, prev, dh, dl = yf._get_cached_quote(ticker)
        prev_close = _positive_float(prev)
        if prev_close and prev_close > 0:
            return prev_close, prev_close, prev_close
        if not ltp or ltp <= 0:
            return None, None, None
        current = round(float(ltp), 2)
        return current, current, current


# Cache: ticker -> (close_price, fetched_at_timestamp)
_YAHOO_CLOSE_CACHE = {}
_YAHOO_CLOSE_CACHE_TTL = 300  # 5 minutes


def _get_yahoo_official_close(ticker):
    """
    Fetch the most recent official closing price from Yahoo Finance.
    Uses the last non-null daily close from the chart endpoint. Do not prefer
    meta.regularMarketPrice here; for NSE symbols it can diverge from the
    completed-session close after hours.
    Results cached for 5 minutes.
    """
    import time as _time
    now_ts = _time.time()

    cached = _YAHOO_CLOSE_CACHE.get(ticker)
    if cached and (now_ts - cached[1]) < _YAHOO_CLOSE_CACHE_TTL:
        return cached[0]

    try:
        _h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
        resp = requests.get(url, headers=_h, timeout=8)
        data = resp.json()
        result = data.get('chart', {}).get('result', [{}])[0]
        meta = result.get('meta', {})
        quote = result.get('indicators', {}).get('quote', [{}])[0]
        closes = quote.get('close', [])

        for close in reversed(closes):
            try:
                close_price = round(float(close), 2)
            except Exception:
                continue
            if close_price > 0:
                _YAHOO_CLOSE_CACHE[ticker] = (close_price, now_ts)
                return close_price

        # Fallback: meta close fields, then regularMarketPrice as a last resort.
        prev = meta.get('chartPreviousClose') or meta.get('previousClose') or meta.get('regularMarketPrice')
        if prev and prev > 0:
            close_price = round(float(prev), 2)
            _YAHOO_CLOSE_CACHE[ticker] = (close_price, now_ts)
            return close_price

    except Exception:
        pass  # Silent fail — caller will use Angel One fallback

    return None


# ==========================================
# V3 INSTANT NEWS ENGINE — Two-Phase Pipeline

def ai_news_worker():
    print("[SYSTEM] Alpha Lens v6.0 AI ENSEMBLE Engine Started!")
    _min_agree = int(os.environ.get("ENSEMBLE_MIN_AGREE", "3"))
    print(f"   Pipeline: RSS -> AI Gatekeeper (Gemini) -> Duplicate Filter -> 5-Model Ensemble (>= {MIN_CONFIDENCE} score & {_min_agree}/5 vote, no technical-model veto)")
    print(f"   Background: Batch Gemini for Aam Janta explanations only")
    print(f"   Settings: Min Confidence={MIN_CONFIDENCE} | R:R = {TRADE_STOP_PCT}% stop : {TRADE_TARGET_PCT}% target")
    import sys as _sys
    sys.stdout.flush()
    try:
        ensemble = EnsemblePredictor()
        print("   [OK] EnsemblePredictor initialized")
    except Exception as _e:
        print(f"   [FATAL] EnsemblePredictor failed: {_e}")
        import traceback; traceback.print_exc()
        return
    
    # Initialize SEEN_HEADLINES from DB on first run.
    print("   [DEBUG] About to load SEEN_HEADLINES from DB...")
    sys.stdout.flush()
    try:
        conn = connect_news_db()
        print("   [DEBUG] DB connected for SEEN_HEADLINES")
        sys.stdout.flush()
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT headline
            FROM news
        """)
        for row in c.fetchall():
            if row[0]:
                SEEN_HEADLINES.add(row[0].lower().strip())
        print(f"   [SEEN_HEADLINES] Loaded {len(SEEN_HEADLINES)} headlines that already exist in database.")

        # Seed the fuzzy-dedup window with the most recent headlines so near-dup
        # detection works immediately after a restart (not just exact match).
        try:
            c.execute("SELECT headline FROM news ORDER BY id DESC LIMIT ?", (RECENT_HEADLINES.maxlen,))
            for _r in reversed(c.fetchall()):
                if _r and _r[0]:
                    RECENT_HEADLINES.append(_norm_headline(_r[0]))
        except Exception:
            pass

        # Pre-seed the directional bias window from the most-recent N approved
        # signals. Without this, every redeploy resets the rolling window and
        # the circuit-breaker is blind until 10 new signals accumulate. Loaded
        # newest-first then reversed so the deque order matches chronological.
        try:
            _bias_win = int(os.environ.get("BIAS_WINDOW", "20"))
            c.execute(f"""
                SELECT impact FROM stock_impact
                ORDER BY id DESC LIMIT {_bias_win}
            """)
            _recent_impacts = [r[0] for r in c.fetchall() if r and r[0]]
            for _imp in reversed(_recent_impacts):
                _dir = 'BULLISH' if 'bull' in _imp.lower() else ('BEARISH' if 'bear' in _imp.lower() else None)
                if _dir:
                    RECENT_DIRECTIONS.append(_dir)
            print(f"   [BIAS] Pre-seeded {len(RECENT_DIRECTIONS)} recent directions from DB.")
        except Exception as _be:
            print(f"   [BIAS] Pre-seed failed (non-fatal): {_be}")

        conn.close()
    except Exception as e:
        print(f"   [DB Init Error] {e}")
    
    print("   [DEBUG] Starting main loop...")
    sys.stdout.flush()

    def fetch_feed(url):
        # ── Two-layer freshness gate ──
        # 1) Rolling stale cutoff (default 6h, was 24h). RSS feeds routinely
        #    carry old articles; without a tight gate, day-old news slipped
        #    through into the pipeline. Tunable via NEWS_MAX_AGE_HOURS.
        # 2) Absolute floor: NEWS_MIN_TIMESTAMP_UTC (e.g. "2026-05-25T22:30:00Z").
        #    Anything older than this is rejected forever, regardless of how
        #    fresh it looks relative to "now". Used after a DB wipe to make
        #    sure stale articles can never re-enter via late RSS republishes.
        try:
            _max_age_h = float(os.environ.get("NEWS_MAX_AGE_HOURS", "6"))
        except Exception:
            _max_age_h = 6.0
        stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=_max_age_h)
        abs_min_cutoff = None
        _min_ts_env = os.environ.get("NEWS_MIN_TIMESTAMP_UTC", "").strip()
        if _min_ts_env:
            try:
                # Accept "2026-05-25T22:30:00Z" or "2026-05-25T22:30:00+00:00"
                _s = _min_ts_env.replace("Z", "+00:00")
                abs_min_cutoff = datetime.fromisoformat(_s)
                if abs_min_cutoff.tzinfo is None:
                    abs_min_cutoff = abs_min_cutoff.replace(tzinfo=timezone.utc)
            except Exception:
                abs_min_cutoff = None
        articles = []
        try:
            # Conditional-GET (etag/last-modified) skips unchanged feeds; rotated
            # UA reduces bot-blocking. Any failure degrades to a normal fetch.
            try:
                _hdrs = {"User-Agent": _ua()}
                if os.environ.get("RSS_CONDITIONAL_GET", "1").lower() in ("1", "true", "yes"):
                    _ce = RSS_CACHE.get(url) or {}
                    if _ce.get("etag"):
                        _hdrs["If-None-Match"] = _ce["etag"]
                    if _ce.get("modified"):
                        _hdrs["If-Modified-Since"] = _ce["modified"]
                resp = HTTP_SESSION.get(url, timeout=8, headers=_hdrs)
                if resp.status_code == 304:
                    _feed_stat(url, "not_modified")
                    return []  # unchanged since last fetch — nothing new
                if resp.status_code != 200:
                    _feed_stat(url, "fail", err=f"HTTP {resp.status_code}")
                    return []
                try:  # remember validators for next cycle's conditional GET
                    if url in RSS_CACHE:
                        if resp.headers.get("ETag"):
                            RSS_CACHE[url]["etag"] = resp.headers.get("ETag")
                        if resp.headers.get("Last-Modified"):
                            RSS_CACHE[url]["modified"] = resp.headers.get("Last-Modified")
                except Exception:
                    pass
                feed = feedparser.parse(resp.content)
            except Exception as _ge:
                _feed_stat(url, "fail", err=_ge)
                return []  # Timeout or network error

            for entry in feed.entries[:30]:
                pub_time = entry.published if hasattr(entry, 'published') else "Just Now"
                if pub_time and pub_time != "Just Now":
                    pub_dt = None
                    try:
                        pub_dt = parsedate_to_datetime(pub_time)
                    except Exception:
                        pub_dt = None
                    if pub_dt is None:
                        try:  # some feeds emit ISO-8601 ("2026-05-25T22:30:00Z")
                            pub_dt = datetime.fromisoformat(pub_time.strip().replace("Z", "+00:00"))
                        except Exception:
                            pub_dt = None
                    if pub_dt is None:
                        # Unparseable timestamp — can't verify freshness, so SKIP
                        # rather than let stale news bypass the age gate.
                        continue
                    pub_dt = _assume_tz(pub_dt)
                    if pub_dt < stale_cutoff:
                        continue
                    if abs_min_cutoff is not None and pub_dt < abs_min_cutoff:
                        continue
                link = entry.link if hasattr(entry, 'link') else None
                summary = ""
                if hasattr(entry, 'summary'):
                    summary = strip_html(entry.summary)
                elif hasattr(entry, 'description'):
                    summary = strip_html(entry.description)
                articles.append({
                    "headline": entry.title,
                    "time": pub_time,
                    "url": link,
                    "summary": summary[:900],
                    "source": getattr(feed.feed, "title", "")
                })
            _feed_stat(url, "ok", n_articles=len(articles))
        except Exception as e:
            print(f"   RSS Error for {url}: {e}")
            _feed_stat(url, "fail", err=e)
        return articles

    while True:
      try:
        _heartbeat("ai_news", last_cycle_started_at=time.time())
        # ============================================================
        # PHASE 1: INSTANT — Scrape, Filter, Save, Map (no API calls)
        # ============================================================
        raw_articles = []
        print(f"[SCRAPE] Fetching from {len(RSS_SOURCES)} RSS sources...")
        sys.stdout.flush()
        try:
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
            try:
                future_map = {executor.submit(fetch_feed, url): url for url in RSS_SOURCES}
                try:
                    for future in concurrent.futures.as_completed(future_map, timeout=60):
                        try:
                            res = future.result(timeout=10)
                            raw_articles.extend(res)
                        except Exception:
                            pass
                except concurrent.futures.TimeoutError:
                    # Some feeds didn't finish in time — collect what we have
                    for future in future_map:
                        if future.done():
                            try:
                                raw_articles.extend(future.result(timeout=0))
                            except Exception:
                                pass
                    print(f"   [SCRAPE] Timeout reached, collected {len(raw_articles)} articles from fast feeds")
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        except Exception as scrape_err:
            print(f"   [SCRAPE ERROR] {scrape_err}")

        # ── Direct BSE corporate-filing announcements (pledge/ratings/board) ──
        try:
            _bse = fetch_bse_announcements()
            if _bse:
                raw_articles.extend(_bse)
                print(f"   [BSE] +{len(_bse)} filing announcements (pledge/ratings/board).")
        except Exception as _be:
            print(f"   [BSE] fetch failed (non-fatal): {_be}")
        
        print(f"[SCRAPE] Got {len(raw_articles)} headlines from all sources")
        sys.stdout.flush()
        _heartbeat("ai_news", last_scrape_count=len(raw_articles))
        if len(raw_articles) == 0:
            # All feeds returned nothing — almost always a provider/network
            # outage, not a quiet news day. Surface it (feed_health shows in
            # /api/debug-worker-status) instead of silently running an empty cycle.
            print("[SCRAPE][ALERT] 0 articles from all RSS sources — likely provider/network outage.")
            _heartbeat("ai_news", feed_health="zero_articles")
        else:
            _heartbeat("ai_news", feed_health="ok")
        
        # Get market regime for technical filters
        market_regime = get_market_regime()
        
        # STEP 1: Quant AI Screener — replaces blunt keyword filter
        # Sends all headlines at once to a dedicated Gemini quant model.
        # It returns material stock mappings and no-impact rows for unchanged articles.
        def quant_ai_screener(articles_batch):
            """
            Screens a batch of headlines for material stock-level impacts.
            Primary: google-genai Gemini SDK (uses same rotating API keys as Phase 2).
            No keyword stock fallback: if AI is unavailable, no stock signals are created.
            Returns rows shaped as {headline, time, url, ticker, direction}; ticker is None for no-impact news.
            """
            if not articles_batch:
                return []
            # Skip slow per-article body scraping — use headline + RSS summary only
            # deep_context scraping for 500+ articles would take 30+ minutes

            def _no_impact_row(article, materiality_score=0, catalyst_type="NO_DIRECT_EQUITY_IMPACT"):
                try:
                    materiality_score = int(float(materiality_score or 0))
                except Exception:
                    materiality_score = 0
                return {
                    "headline": article["headline"],
                    "time": article["time"],
                    "url": article.get("url"),
                    "summary": article.get("summary", ""),
                    "deep_context": article.get("deep_context", ""),
                    "ticker": None,
                    "direction": None,
                    "quality_score": materiality_score,
                    "reason": catalyst_type,
                    "material": False,
                }

            # ── Try AI screening first (only if Gemini client is available) ──
            available_keys = _available_gemini_key_indices()
            if not available_keys:
                _wait = _seconds_until_any_gemini_key()
                print(f"   [AI] All keys on quota cooldown — skipping batch ({_wait}s until a key retries).")
                sys.stdout.flush()
                return [_no_impact_row(a, 0, "AI_COOLDOWN") for a in articles_batch]

            # Pick a random starting key for THIS batch. The old behaviour was
            # "try current_key_idx first, then iterate" — which meant every
            # batch in a cycle hammered the same key first, burning its TPM
            # while other keys sat idle. Random start spreads the load so a
            # quota spike on one key doesn't snowball into all 12 on cooldown.
            import random as _rnd
            shuffled = list(available_keys)
            _rnd.shuffle(shuffled)
            try_order = shuffled

            _key_idx = try_order[0]
            _ai_client = _set_active_gemini_client(_key_idx)

            if _ai_client:
                print(f"   [AI] Screening batch of {len(articles_batch)} articles...")
                sys.stdout.flush()
                # Slashed to 200 chars (was 700). The headline carries 80% of the
                # materiality signal anyway — the snippet is just disambiguation.
                # With BATCH_SIZE=8 and ~200-char contexts the per-call prompt
                # stays well under Gemini's free-tier TPM ceiling, which is what
                # was repeatedly blowing every key to 5-min cooldown. Override
                # via SCRAPER_CONTEXT_CHARS if you ever want richer context.
                _ctx_chars = int(os.environ.get("SCRAPER_CONTEXT_CHARS", "200"))
                numbered = "\n".join(
                    [
                        f"{i+1}. Headline: {a['headline']}\n"
                        f"   Context: {(a.get('deep_context') or a.get('summary') or 'Not available')[:_ctx_chars]}"
                        for i, a in enumerate(articles_batch)
                    ]
                )
                prompt = f"""You are the Chief Quantitative Portfolio Manager at a top-1% Indian hedge fund managing a multi-billion dollar long-short equity book on NSE/BSE. You think like Renaissance Technologies or Two Sigma — but specialized for Indian markets.

Your ONLY job: analyze these {len(articles_batch)} news items and identify the HIGHEST CONVICTION stock-level trade opportunities that will produce an asymmetric 3%+ move within 1-5 trading sessions.

For EVERY headline, think through this decision tree:

STEP 1 — DIRECT IMPACT: Which specific NSE/BSE-listed company is named or directly affected by this news? (e.g., "TCS wins $2B deal" → TCS.NS BULLISH)
STEP 2 — SECOND-ORDER SUPPLY CHAIN: What companies are suppliers, customers, or competitors? (e.g., "Steel prices surge" → TATASTEEL.NS BULLISH, MARUTI.NS BEARISH due to input cost)
STEP 3 — MACRO TRANSMISSION: How does this flow through the economy? (e.g., "RBI cuts repo rate" → HDFCBANK.NS BULLISH, DLF.NS BULLISH via cheaper mortgages)
STEP 4 — FLOW ANALYSIS: Will FIIs/DIIs/MFs be forced to rebalance? Will options market reprice?
STEP 5 — SIGNAL vs NOISE: Is this a durable 1-5 session catalyst, or a one-day noise event that reverts?

STRICT INCLUSION (High Conviction Only):
✅ Q1/Q2/Q3/Q4 earnings beats/misses vs street estimates
✅ M&A, acquisitions, mergers, stake sales, delistings
✅ Regulatory approvals/bans (SEBI, FDA, DCGI, RBI, Government orders)
✅ Major order wins/cancellations (>5% of annual revenue)
✅ CEO/CFO/MD changes or board-level shakeups
✅ Commodity/currency shocks with clear P&L transmission (crude, rupee, gold, metals)
✅ RBI rate decisions, CRR/SLR changes, liquidity operations
✅ Large FII/DII block deals revealing institutional conviction
✅ Credit rating upgrades/downgrades, debt defaults, NCLT filings
✅ Large capex announcements, plant shutdowns, capacity expansions
✅ Government policy with sector-specific revenue impact (PLI, tariffs, subsidies)
✅ Promoter buying/selling (insider activity)

STRICT EXCLUSION (Zero Signal Noise):
❌ "Nifty/Sensex may rise/fall today" generic commentary
❌ Analyst price target reiterations without NEW catalyst
❌ Broad monthly FII/DII flow data (not stock-specific)
❌ Technical analysis summaries ("stock at support/resistance")
❌ "Stocks to watch today" listicles without specific catalysts
❌ Repeat coverage of events already priced in by the market
❌ General economic outlook pieces without actionable stock impact

TICKER FORMAT: Use exact NSE symbol + .NS suffix (e.g., RELIANCE.NS, HDFCBANK.NS, TCS.NS, INFY.NS, SBIN.NS, TMPV.NS). For BSE-only stocks use .BO suffix.

News items to analyze:
{numbered}

Return a COMPLETE JSON array covering ALL {len(articles_batch)} headlines — no exceptions.
For non-material news: set material=false with empty impacts.
For material news: provide ONLY the 1-3 HIGHEST CONVICTION tickers. Quality over quantity — do NOT dilute with weak signals.

[
  {{{{"index": 1, "material": true, "catalyst_type": "EARNINGS_BEAT", "materiality_score": 87, "impacts": [{{{{"ticker": "TCS.NS", "direction": "BULLISH", "confidence": 88, "impact_type": "DIRECT", "reason": "Q4 PAT beat consensus by 12%, deal pipeline at all-time high — historically similar beats drove 4-7% moves within 2 sessions"}}}}]}}}},
  {{{{"index": 2, "material": false, "catalyst_type": "NOISE", "materiality_score": 12, "impacts": []}}}}
]"""

                schema_example = json.dumps([
                    {
                        "index": 1,
                        "material": True,
                        "catalyst_type": "EARNINGS_BEAT",
                        "materiality_score": 87,
                        "impacts": [
                            {
                                "ticker": "TCS.NS",
                                "direction": "BULLISH",
                                "confidence": 88,
                                "impact_type": "DIRECT",
                                "reason": "Q4 PAT beat consensus and deal pipeline improved; similar beats can drive a 3-7% move in 1-5 sessions."
                            }
                        ]
                    },
                    {
                        "index": 2,
                        "material": False,
                        "catalyst_type": "NOISE",
                        "materiality_score": 12,
                        "impacts": []
                    }
                ], indent=2)

                prompt = f"""You are an elite QUANTITATIVE portfolio manager on a top-decile Indian long/short equity desk. You are NOT a headline classifier. You take a position ONLY when there is a genuine, FORWARD-LOOKING edge with asymmetric risk/reward over the next 1–5 trading sessions. Your reputation rests on PRECISION: a wrong signal costs far more than a missed one. When in doubt, pass.

══════════════════════════════════════════════════════════════════
PRIME DIRECTIVE — TRADE THE NEXT MOVE, NEVER CHASE THE LAST ONE
══════════════════════════════════════════════════════════════════
A signal requires a catalyst that predicts a FUTURE move the market has NOT yet digested.
If the headline merely REPORTS a move that ALREADY HAPPENED, the reaction is ALREADY PRICED IN — issuing a same-direction signal is CHASING and is FORBIDDEN.
  • Past-move language ("fell / falls / slumps / slips / drops / tanks / declines / down X%", "rose / rises / surges / jumps / gains / soars / up X%", "hits 52-week high/low after…", "ends lower/higher") = the move is DONE.
  • Default for such headlines: material=false.
  • Only override if there is a CLEAR mean-reversion mechanism for the OTHER side — e.g. an OFS / block-deal / stake-sale dip is TEMPORARY supply pressure that typically RECOVERS once absorbed (so it is NOT a fresh short; if anything a tactical bounce). State the mechanism explicitly.
  • Example of the mistake to avoid: "Coal India falls 5% as OFS opens" → do NOT go BEARISH (the drop already happened and OFS dips recover). "ONGC slumps 3% after Q4" → do NOT go BEARISH on the slump itself.

══════════════════════════════════════════════════════════════════
WHAT QUALIFIES AS MATERIAL (a fresh, forward catalyst with a quantifiable P&L path to a LIQUID NSE name)
══════════════════════════════════════════════════════════════════
• Earnings SURPRISE vs street estimates (a beat/miss with magnitude — not a bare "profit rose 13%" with no comparison)
• Order win/loss, M&A, stake/strategic deal, fund-raise, demerger
• Regulatory approval/ban, rating upgrade/downgrade, guidance change
• Capacity/capex with revenue visibility, promoter buy/sell, insider action
• Hard commodity / currency / policy / rate shock with a CLEAR, specific transmission to a named stock

══════════════════════════════════════════════════════════════════
YOUR EDGE — HIDDEN CHAINS (this is where you beat the street)
══════════════════════════════════════════════════════════════════
LAYER 1 directly-named company. LAYER 2 supply chain (suppliers/customers/competitors/input costs). LAYER 3 macro transmission. LAYER 4 flow/index repositioning.
  • Commodities → Indian users & producers (crude→airlines/OMCs/paint vs ONGC; steel→auto/infra vs TATASTEEL; copper→power).
  • Geopolitics → Indian supply-chain dependencies (China/Taiwan/Japan → IT/auto/electronics/pharma APIs).
  • Central banks (Fed/RBI) → FII-flow impact; FX → exporters/importers.
The reason MUST trace the chain AND state why it is not yet priced in.

══════════════════════════════════════════════════════════════════
HARD REJECT (material=false) — be ruthless; MOST news is noise
══════════════════════════════════════════════════════════════════
• Already-moved / priced-in reactive headlines (see PRIME DIRECTIVE)
• "Stocks to watch / in focus / on brokerage radar", "5 stocks to buy", technical/RSI/200-DMA/breakout scans
• Analyst target reiterations with NO new catalyst; "Buy/Sell/Hold" opinion lists
• Generic index/market commentary (Sensex/Nifty up-down, GIFT Nifty, pre-open), holidays, gold/silver/FX price ticks
• Foreign-listed names with no concrete Indian transmission
• Vague sector sympathy with no quantifiable P&L path; repeat coverage of an already-traded event
• Obscure illiquid micro-caps / index pages / IPO grey-market chatter

══════════════════════════════════════════════════════════════════
DIRECTION & CONFIDENCE
══════════════════════════════════════════════════════════════════
• direction = the FUTURE move the catalyst implies (may differ from the headline's tone).
• confidence (0–100) reflects YOUR EDGE, not how loud the news is:
   80+  direct, unambiguous, large fresh surprise, clean setup (RARE)
   65–79 solid fresh catalyst with a clear mechanism
   50–64 real but second-order / partial / some ambiguity
   <50 → do not emit; set material=false
• Prefer FEWER, higher-quality calls: 1–3 highest-conviction tickers max. Returning ZERO material signals for a noisy batch is correct and expected — do not manufacture signals.

Analyze exactly {len(articles_batch)} news items.

Rules:
- Return a complete JSON array with one object for EVERY input index.
- material=false → impacts=[].
- reason MUST name the FORWARD catalyst, the transmission chain, and why it is NOT already priced in.
- Use exact NSE ticker.NS format. Do NOT invent tickers. confidence and materiality_score: 0–100.

News items to analyze:
{numbered}

Return ONLY valid JSON matching this shape:
{schema_example}"""

                try:
                    resp = None
                    for _key_idx in try_order:
                        _ai_client = genai.Client(api_key=API_KEYS[_key_idx])
                        try:
                            import concurrent.futures as _cf2
                            def _make_call(_c=_ai_client, _p=prompt):
                                return _c.models.generate_content(
                                    model=MODEL_NAME,
                                    contents=_p,
                                    config=types.GenerateContentConfig(
                                        response_mime_type="application/json"
                                    ),
                                )
                            _tex = _cf2.ThreadPoolExecutor(max_workers=1)
                            try:
                                _fut = _tex.submit(_make_call)
                                resp = _fut.result(timeout=60)
                            finally:
                                _tex.shutdown(wait=False, cancel_futures=True)
                            _set_active_gemini_client(_key_idx)
                            _reset_gemini_key_strikes(_key_idx)
                            print(f"   [AI] Screener OK on key {_key_idx + 1}/{len(API_KEYS)}")
                            sys.stdout.flush()
                            break
                        except Exception as _api_err:
                            if _is_gemini_quota_error(_api_err):
                                _mark_gemini_key_quota_hit(_key_idx)
                                print(f"   [AI] 429 on key {_key_idx + 1}/{len(API_KEYS)} — trying next available key")
                                sys.stdout.flush()
                                time.sleep(2)
                                continue
                            elif _is_gemini_transient_error(_api_err):
                                _mark_gemini_key_quota_hit(_key_idx, cooldown_secs=_GEMINI_TRANSIENT_COOLDOWN_SECS)
                                print(f"   [AI] Transient error on key {_key_idx + 1}/{len(API_KEYS)}: {_api_err} — trying next available key ({_GEMINI_TRANSIENT_COOLDOWN_SECS}s cooldown)")
                                sys.stdout.flush()
                                time.sleep(2)
                                continue
                            else:
                                _mark_gemini_key_quota_hit(_key_idx, cooldown_secs=30)
                                print(f"   [AI] Unexpected error on key {_key_idx + 1}/{len(API_KEYS)}: {_api_err} — marked on 30s cooldown")
                                sys.stdout.flush()
                                time.sleep(2)
                                continue
                    if resp is None:
                        _wait = _seconds_until_any_gemini_key()
                        print(f"   [AI] No available keys left for this batch ({_wait}s until retry).")
                        sys.stdout.flush()
                        return [_no_impact_row(a, 0, "AI_QUOTA_EXHAUSTED") for a in articles_batch]
                    parsed = extract_json_from_text(resp.text)
                    if isinstance(parsed, dict):
                        for v in parsed.values():
                            if isinstance(v, list):
                                parsed = v
                                break
                    if not isinstance(parsed, list):
                        raise ValueError("AI screener response was not a JSON array")

                    results = []
                    seen_indexes = set()
                    for item in parsed:
                        try:
                            idx = int(item.get("index", 0)) - 1
                        except Exception:
                            continue
                        if not item.get("material", False):
                            if 0 <= idx < len(articles_batch):
                                seen_indexes.add(idx)
                                article = articles_batch[idx]
                                results.append(_no_impact_row(
                                    article,
                                    item.get("materiality_score", 0),
                                    item.get("catalyst_type", "NOISE")
                                ))
                            continue
                        if 0 <= idx < len(articles_batch):
                            seen_indexes.add(idx)
                            article = articles_batch[idx]
                            impact_candidates = []
                            for impact in item.get("impacts", []):
                                ticker = normalize_ticker(impact.get("ticker", ""))
                                direction = impact.get("direction", "").upper().strip()
                                if ticker and direction in ("BULLISH", "BEARISH") and is_supported_equity_ticker(ticker):
                                    # Adjust confidence based on impact type
                                    try:
                                        conf = int(float(impact.get("confidence", item.get("materiality_score", 75))))
                                    except Exception:
                                        conf = 75
                                    impact_type = str(impact.get("impact_type", "DIRECT")).upper()
                                    # No penalty for deep analysis — 2nd-order and macro
                                    # signals are the EDGE that retail traders miss
                                    if impact_type == "SECOND_ORDER":
                                        conf = max(10, conf - 2)  # Minimal penalty
                                    elif impact_type == "MACRO_TRANSMISSION":
                                        conf = max(10, conf - 3)  # Minimal penalty
                                    impact_candidates.append({
                                        "ticker": ticker,
                                        "direction": direction,
                                        "source": "llm",
                                        "confidence": conf,
                                        "reason": impact.get("reason", ""),
                                    })
                            # Pure AI — no keyword fallback blending when AI is available
                            ranked_by_ticker = {}
                            for candidate in impact_candidates:
                                try:
                                    quality = int(float(candidate.get("confidence", 70)))
                                except Exception:
                                    quality = 70
                                quality = max(10, min(99, quality))
                                ranked_item = {
                                    "ticker": candidate["ticker"],
                                    "direction": candidate["direction"],
                                    "quality_score": quality,
                                    "reason": candidate.get("reason", ""),
                                }
                                current = ranked_by_ticker.get(candidate["ticker"])
                                if not current or ranked_item["quality_score"] > current["quality_score"]:
                                    ranked_by_ticker[candidate["ticker"]] = ranked_item

                            ranked_ai_impacts = sorted(
                                ranked_by_ticker.values(),
                                key=lambda item: item["quality_score"],
                                reverse=True,
                            )[:3]
                            if not ranked_ai_impacts:
                                results.append(_no_impact_row(
                                    article,
                                    item.get("materiality_score", 0),
                                    "AI_NO_VALID_TICKER"
                                ))
                                continue
                            # Capture catalyst_type once for this material item.
                            # The screener returns it at the item level; without
                            # carrying it onto each impact row, downstream
                            # credibility filters lose it.
                            _catalyst_type = (item.get("catalyst_type") or "").upper().replace(" ", "_")
                            for ranked in ranked_ai_impacts:
                                results.append({
                                        "headline": article["headline"],
                                        "time": article["time"],
                                        "url": article.get("url"),
                                        "summary": article.get("summary", ""),
                                        "deep_context": article.get("deep_context", ""),
                                        "ticker": ranked["ticker"],
                                        "direction": ranked["direction"],
                                        "quality_score": ranked["quality_score"],
                                        "reason": ranked.get("reason", ""),
                                        "catalyst_type": _catalyst_type,
                                })
                    for idx, article in enumerate(articles_batch):
                        if idx not in seen_indexes:
                            results.append(_no_impact_row(article, 0, "AI_MISSING_DECISION"))

                    signal_count = sum(1 for r in results if r.get("ticker"))
                    print(f"   [AI Screener] {signal_count} ticker-signals from {len(articles_batch)} AI-reviewed headlines")
                    return results
                except Exception as e:
                    print(f"   [AI Screener Error] {e} -- falling back to rule-based mapping")
                    # ── FALLBACK: Rule-based mapping when AI is unavailable ──
                    return _rule_based_fallback(articles_batch, _no_impact_row)

            # AI client not available — use rule-based fallback
            print("   [AI Screener] Gemini client unavailable; using rule-based mapping")
            return _rule_based_fallback(articles_batch, _no_impact_row)

        def _rule_based_fallback(articles_batch, _no_impact_row):
            """AI-only mode: skip all articles when AI is unavailable. No keyword/macro signals."""
            print(f"   [AI] Skipping {len(articles_batch)} articles — AI-only mode, no fallback.")
            return [_no_impact_row(a, 0, "AI_UNAVAILABLE") for a in articles_batch]

        # Filter out already seen headlines in memory before AI screening
        new_articles = []
        seen_this_batch = set()
        for a in raw_articles:
            h_lower = a["headline"].lower().strip() if a.get("headline") else ""
            if not h_lower or h_lower in SEEN_HEADLINES or h_lower in seen_this_batch:
                continue
            h_norm = _norm_headline(a.get("headline"))
            if _is_near_dup_headline(h_norm):
                continue  # same story reworded by another source — drop it
            new_articles.append(a)
            seen_this_batch.add(h_lower)
            RECENT_HEADLINES.append(h_norm)

        print(f"   [FILTER] Screened out {len(raw_articles) - len(new_articles)} duplicates. {len(new_articles)} new articles to review.")
        sys.stdout.flush()

        # ── RESCREEN: pick up news rows saved during AI downtime ──
        # When Gemini keys were on cooldown earlier, headlines were saved with
        # ai_status='pending' so the user could still see them. Now we fetch
        # those rows, shape them like RSS articles, and pipe them through the
        # same quant_ai_screener as fresh articles. They bypass the
        # SEEN_HEADLINES pre-filter intentionally (the news row already exists;
        # we just need to attach the AI analysis). pending_news_ids_by_headline
        # maps each pending headline to its news_id so the save loop knows to
        # flip its ai_status to 'screened' on a successful response.
        pending_news_ids_by_headline = {}
        try:
            # RESCREEN_LIMIT was 40 — far too small. With a backlog of ~1000
            # pending rows, only the newest 40 were ever eligible, so the
            # older ~960 sat in the DB forever and were never part of the
            # stack. Raised to 2000 (well above the 7-day-capped news table's
            # realistic size) so the ENTIRE pending backlog — old news
            # included — is pulled into the queue every cycle. The LIFO sort
            # below then orders it newest-first, and the screener works down
            # the whole stack as key headroom allows. Still env-tunable for a
            # smaller memory footprint if ever needed.
            _rescreen_limit = int(os.environ.get('RESCREEN_LIMIT', '2000'))
            _pending_timeout_h = float(os.environ.get('PENDING_TIMEOUT_HOURS', '24'))
            _rs_conn = connect_news_db()
            _rs_c    = _rs_conn.cursor()
            _rs_c.execute("""
                SELECT id, headline, news_time, body, created_at
                FROM news
                WHERE ai_status = 'pending'
                ORDER BY created_at DESC
                LIMIT ?
            """, (_rescreen_limit,))
            _rs_rows = _rs_c.fetchall()
            _rs_conn.close()
            if _rs_rows:
                added = 0
                _stale_pending_ids = []
                _now_utc_rs = datetime.now(timezone.utc)
                for _nid, _hl, _ntime, _body, _created in _rs_rows:
                    # Age out rows stuck pending past the timeout (e.g. a long
                    # Gemini outage) so the backlog can't grow forever.
                    if _pending_timeout_h > 0 and _created:
                        try:
                            _cdt = _assume_tz(parsedate_to_datetime(_created))
                            if _cdt and (_now_utc_rs - _cdt).total_seconds() > _pending_timeout_h * 3600:
                                _stale_pending_ids.append(_nid)
                                continue
                        except Exception:
                            pass
                    _hl_norm = (_hl or '').lower().strip()
                    if not _hl_norm or _hl_norm in seen_this_batch:
                        continue
                    new_articles.append({
                        'headline': _hl,
                        'time':     _ntime or '',
                        'url':      None,
                        'summary':  _body or '',
                        'deep_context': _body or '',
                        'source':   'rescreen',
                    })
                    seen_this_batch.add(_hl_norm)
                    pending_news_ids_by_headline[_hl_norm] = _nid
                    added += 1
                print(f"   [RESCREEN] Queued {added} pending news rows for AI re-screening.")
                sys.stdout.flush()
                if _stale_pending_ids:
                    try:
                        _ph = ",".join("?" for _ in _stale_pending_ids)
                        db_write(lambda conn, c: c.execute(
                            f"UPDATE news SET ai_status='stale_pending' WHERE id IN ({_ph})",
                            tuple(_stale_pending_ids)))
                        print(f"   [RESCREEN] Aged out {len(_stale_pending_ids)} rows stuck pending > {_pending_timeout_h}h -> stale_pending.")
                    except Exception as _sp_err:
                        print(f"   [RESCREEN] stale-pending update failed: {_sp_err}")
        except Exception as _rs_err:
            print(f"   [RESCREEN] Fetch error (non-fatal): {_rs_err}")

        # ── LIFO / stack ordering: newest news evaluated FIRST ──
        # When Gemini keys are exhausted mid-cycle, the screener bails out and
        # leaves the rest of the queue as ai_status='pending' for a later
        # rescreen. We want that "leftover" to be the OLDER, less-actionable
        # headlines — never the freshest market-movers. So sort the combined
        # queue (fresh RSS + pending rescreen rows) by publication time
        # descending before batching. The newest items hit the model while we
        # still have key headroom; anything we run out of quota for is stale by
        # definition and waits at the bottom of the stack until keys recover.
        def _article_recency_key(_art):
            try:
                _dt = parsedate_to_datetime(_art.get('time')) if _art.get('time') else None
                if _dt is not None:
                    if _dt.tzinfo is None:
                        _dt = _dt.replace(tzinfo=timezone.utc)
                    return _dt
            except Exception:
                pass
            # Unknown/unparseable time → sink to the bottom (evaluated last).
            return datetime.min.replace(tzinfo=timezone.utc)
        new_articles.sort(key=_article_recency_key, reverse=True)

        # Batch tunables — sized to fit comfortably under Gemini free-tier TPM.
        # Bigger batches were running keys into 5-min cooldowns because each
        # call's prompt blew past 1M TPM with the long per-article context.
        # 8 articles × ~200-char context keeps the prompt ~3-4K tokens, so
        # 12 rotating keys can absorb a full cycle without any 429s.
        BATCH_SIZE = int(os.environ.get("SCRAPER_BATCH_SIZE", "8"))
        BATCH_SLEEP = float(os.environ.get("SCRAPER_BATCH_SLEEP", "1"))
        screened_signals = []
        for i in range(0, len(new_articles), BATCH_SIZE):
            batch = new_articles[i:i + BATCH_SIZE]
            batch_results = quant_ai_screener(batch)
            # IMPORTANT: We no longer break on AI failure. The no-impact rows
            # carry reason="AI_COOLDOWN" through to the save loop, where the
            # news row is INSERTed with ai_status='pending'. A rescreen pass
            # at the top of every cycle re-attempts these rows the moment a
            # key frees up — so the user sees the headline immediately and
            # the stock-impact analysis fills in as soon as the AI can answer.
            screened_signals.extend(batch_results)
            # Skip the inter-batch sleep when this batch made no real API call
            # (every row came back quota-failed). With the queue now up to
            # ~2000 rows, sleeping 1s between dozens of instant quota-failed
            # batches would otherwise add minutes of dead time per cycle. We
            # still iterate so fresh articles flow to the save loop and get
            # inserted as 'pending' — we just don't pause when there's nothing
            # to throttle.
            _batch_quota_dead = batch_results and all(
                r.get("reason") in ("AI_COOLDOWN", "AI_QUOTA_EXHAUSTED") for r in batch_results
            )
            if not _batch_quota_dead and i + BATCH_SIZE < len(new_articles) and BATCH_SLEEP > 0:
                time.sleep(BATCH_SLEEP)
        
        ai_signal_count = sum(1 for s in screened_signals if s.get("ticker"))
        ai_article_count = len({s.get("headline") for s in screened_signals})
        print(f"AI Screener Total: {ai_signal_count} ticker-signals across {ai_article_count} AI-reviewed articles")
        
        # STEP 2: Duplicate Filter + Instant Save + Stock Mapping
        # NOTE: We open/close the DB connection atomically per article to avoid
        # long-held write locks that cause "database is locked" errors when other
        # threads (yfinance_worker, Flask routes) also need to write.
        #
        # headline_id_cache — populated lazily as we walk screened_signals. The
        # AI screener typically emits 1-3 ticker-signals per headline, so without
        # this cache every additional ticker for the same headline would trigger
        # a fresh SELECT. Reduces per-cycle DB round-trips by ~3x on the hot
        # path. Lifetime: this loop only.
        new_article_ids = []
        headline_id_cache = {}

        # ── Pre-fetch canonical published times in parallel ──
        # The RSS pubDate is often a scheduled-publish placeholder ("Stocks to
        # Watch Today" wires routinely tag 07:00 IST hours before they actually
        # release). To avoid showing the user a future time OR our ingestion
        # time, we scrape the article URL for the canonical timestamp from
        # standard publisher meta tags (OG, Schema.org, JSON-LD). One HTTP call
        # per unique URL, all in parallel, cached forever per URL.
        canonical_pub_times = {}
        _unique_url_by_headline = {}
        for _s in screened_signals:
            _hl = _s.get('headline')
            _url = _s.get('url')
            if _hl and _url and _hl not in _unique_url_by_headline:
                _unique_url_by_headline[_hl] = _url
        if _unique_url_by_headline:
            # Default 4 (was 10) — each parallel BeautifulSoup parse holds a
            # full HTML tree in memory until GC'd. 4 keeps peak transient
            # memory ~2-3x lower with minimal latency cost.
            _pt_workers = int(os.environ.get('PUB_TIME_WORKERS', '4'))
            def _pt_fetch(item):
                hl, url = item
                try:
                    return (hl, fetch_published_time(url))
                except Exception:
                    return (hl, None)
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=_pt_workers) as _pt_pool:
                    for hl, real_t in _pt_pool.map(_pt_fetch, _unique_url_by_headline.items()):
                        if real_t:
                            canonical_pub_times[hl] = real_t
                print(f"   [PUB-TIME] Resolved canonical published time for {len(canonical_pub_times)}/{len(_unique_url_by_headline)} articles.")
                sys.stdout.flush()
            except Exception as _pt_err:
                print(f"   [PUB-TIME] Prefetch error (non-fatal): {_pt_err}")

        for signal in screened_signals:
            headline = signal['headline']
            article_url = signal.get('url')
            ticker = normalize_ticker(signal.get('ticker')) if signal.get('ticker') else None
            base_direction = (signal.get('direction') or '').upper()

            # ── Fast-skip: AI-failed signal for a headline already in the DB ──
            # When a big rescreen batch (now up to ~2000 rows) runs out of key
            # headroom, every leftover row comes back AI_QUOTA_EXHAUSTED. Such a
            # row is already in the DB as 'pending' — there's nothing to do (no
            # flip, no new insert). Skipping it here avoids a SELECT-per-row
            # storm against the (already strained) Postgres pool. New headlines
            # that failed are NOT in SEEN_HEADLINES yet, so they still fall
            # through to the INSERT-as-pending path below.
            if signal.get('reason') in ('AI_COOLDOWN', 'AI_QUOTA_EXHAUSTED', 'AI_UNAVAILABLE'):
                if headline not in headline_id_cache and headline.lower().strip() in SEEN_HEADLINES:
                    continue

            # ── Cache lookup first — zero DB round-trips on repeat headlines ──
            if headline in headline_id_cache:
                news_id = headline_id_cache[headline]
            else:
                h_lower = headline.lower().strip()
                if h_lower in SEEN_HEADLINES:
                    # Already in DB from a prior cycle — one SELECT to grab the id
                    news_id = None
                    try:
                        _c = connect_news_db()
                        _cur = _c.cursor()
                        _cur.execute("SELECT id FROM news WHERE headline = ? LIMIT 1", (headline,))
                        _row = _cur.fetchone()
                        _c.close()
                        news_id = _row[0] if _row else None
                    except Exception:
                        pass
                else:
                    # New headline — go straight to INSERT (the _insert_news fn
                    # has its own SELECT fallback if INSERT OR IGNORE no-ops on
                    # a UNIQUE collision). Dropped the redundant pre-SELECT.
                    SEEN_HEADLINES.add(h_lower)
                    category = classify_category(headline)
                    _hl = headline
                    # Time-of-publication precedence:
                    #   1. canonical_pub_times[headline] — meta tag from article
                    #      page (the publisher's own truth). Most accurate.
                    #   2. signal['time'] — RSS pubDate (publisher's scheduled
                    #      slot; sometimes lies about being in the future).
                    # Either way we then apply the 5-min future-clamp as a
                    # safety net so a publisher mistake doesn't reach the UI.
                    _time = canonical_pub_times.get(headline) or signal['time']
                    try:
                        from email.utils import parsedate_to_datetime as _ptd
                        _parsed_dt = _ptd(_time) if _time else None
                        if _parsed_dt is not None:
                            if _parsed_dt.tzinfo is None:
                                _parsed_dt = _parsed_dt.replace(tzinfo=timezone.utc)
                            _now_utc = datetime.now(timezone.utc)
                            if _parsed_dt > _now_utc + timedelta(minutes=5):
                                _time = _now_utc.strftime('%a, %d %b %Y %H:%M:%S +0000')
                                print(f"   [TIME] Clamped future news_time to now for: {_hl[:60]}...")
                    except Exception:
                        pass
                    _cat = category
                    # Preserve the full RSS snippet (deep_context wins if the
                    # full-text scraper already populated it). UI renders this
                    # verbatim; the AI screener only ever saw a 200-char slice.
                    _body = (signal.get("deep_context") or signal.get("summary") or "")[:5000]
                    # ai_status='pending' if Gemini was unreachable for this
                    # article; the rescreen pass at the top of each cycle
                    # picks pending rows back up. Default 'screened' for any
                    # article AI actually evaluated (material or not).
                    _ai_fail = signal.get('reason') in ('AI_COOLDOWN', 'AI_QUOTA_EXHAUSTED', 'AI_UNAVAILABLE')
                    _ai_status = 'pending' if _ai_fail else 'screened'
                    def _insert_news(conn, c, _hl=_hl, _time=_time, _cat=_cat, _body=_body, _st=_ai_status):
                        c.execute('''INSERT OR IGNORE INTO news (headline, news_time, aam_janta_translation, macro_pathway, category, body, ai_status)
                            VALUES (?, ?, ?, ?, ?, ?, ?)''',
                            (_hl, _time, None, '[]', _cat, _body, _st))
                        if c.lastrowid:
                            return c.lastrowid
                        c.execute("SELECT id FROM news WHERE headline = ? LIMIT 1", (_hl,))
                        row = c.fetchone()
                        return row[0] if row else None
                    news_id = db_write(_insert_news)
                    if news_id:
                        new_article_ids.append({'id': news_id, 'headline': headline})

                headline_id_cache[headline] = news_id

            if news_id is None:
                continue

            # ── Rescreen completion — flip ai_status to 'screened' ──
            # This signal came from a row we re-queued at the top of the cycle.
            # If the screener actually responded (not another AI_COOLDOWN), we
            # mark the row as screened so the rescreen loop doesn't pick it up
            # again. We pop from the dict so only one UPDATE fires per news_id
            # (the same headline may appear multiple times if AI emitted
            # multiple tickers for it — only the first occurrence needs to run
            # the UPDATE).
            _hl_norm = headline.lower().strip()
            if _hl_norm in pending_news_ids_by_headline and signal.get('reason') not in ('AI_COOLDOWN', 'AI_QUOTA_EXHAUSTED', 'AI_UNAVAILABLE'):
                _pending_id = pending_news_ids_by_headline.pop(_hl_norm)
                def _mark_screened(conn, c, _id=_pending_id):
                    c.execute("UPDATE news SET ai_status='screened' WHERE id=?", (_id,))
                db_write(_mark_screened)

            if not ticker or base_direction not in ("BULLISH", "BEARISH") or not is_supported_equity_ticker(ticker):
                continue

            # News-quality assessment is no longer a mechanical filter here —
            # it now happens INSIDE the AILogicModel prompt. The AI sees the
            # catalyst_type, the news age (hours since canonical publication),
            # and the headline itself, and is asked to penalize soft catalysts,
            # stale news, and already-viral coverage. One model with full
            # context beats three mechanical gates with no nuance.

            # ── Full Text Scraping (Context Boost) ──
            body_text = signal.get("deep_context") or signal.get("summary") or ""
            if not body_text:
                body_text = scrape_article_text(article_url)
            ai_input = headline
            if body_text:
                ai_input = f"{headline}\nContext: {body_text}"

            approved_signals = []
            market_currently_open = is_market_open()

            # ── Get base_price = ACTUAL PRICE at news publication time ──
            # KEY RULE: When news arrives AFTER market hours, base_price AND
            # current_price MUST be the SAME (today's closing price / LTP).
            # The percentage change should be 0% until the market opens and
            # actual price movement occurs.
            _ist = timezone(timedelta(hours=5, minutes=30))
            base_price = 0.0
            current_price_now = 0.0
            _pub_dt_utc_str = ""
            try:
                base_price, current_price_now, _pub_dt_utc_str = get_signal_prices_for_publication(
                    ticker,
                    signal.get('time')
                )
            except Exception as _e:
                if not _pub_dt_utc_str:
                    _pub_dt_utc_str = signal.get('time') or datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                print(f"   [!] Price fetch error for {ticker}: {_e}")

            # ── DUPLICATE SIGNAL GUARD / MERGE CHECK ──
            _cooldown_key = f"{ticker}_{base_direction}"
            _last_signal_time = RECENT_SIGNALS.get(_cooldown_key)
            _now_utc = datetime.now(timezone.utc)
            
            _can_merge = False
            _active_id = None
            try:
                _c = connect_news_db()
                _cur = _c.cursor()
                _cur.execute("""
                    SELECT id, base_price, created_at, confidence_score, reason
                    FROM stock_impact
                    WHERE ticker = ? AND impact = ? AND status = 'Active View'
                    ORDER BY created_at DESC LIMIT 1
                """, (ticker, base_direction))
                _row = _cur.fetchone()
                _c.close()
                if _row:
                    _db_id, _db_bp, _db_created, _db_conf, _db_reason = _row
                    try:
                        _new_dt = datetime.strptime(_pub_dt_utc_str, '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        _new_dt = _now_utc
                    try:
                        _db_dt = datetime.strptime(_db_created, '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        _db_dt = _new_dt
                    
                    _time_diff = abs((_new_dt - _db_dt).total_seconds())
                    if _time_diff <= 86400:  # 24 hours
                        if _db_bp == 0.0 or base_price == 0.0:
                            _can_merge = True
                            _active_id = _db_id
                        else:
                            _pct_diff = abs(_db_bp - base_price) / _db_bp
                            if _pct_diff <= 0.025:  # within 2.5%
                                _can_merge = True
                                _active_id = _db_id
            except Exception as _db_err:
                print(f"   [DB Merge Guard Error] {_db_err}")

            if _last_signal_time and (_now_utc - _last_signal_time).total_seconds() < 86400:  # 24 hours
                if not _can_merge:
                    _hours_ago = round((_now_utc - _last_signal_time).total_seconds() / 3600, 1)
                    print(f"   [SKIP] {ticker} {base_direction}: duplicate signal — last seen {_hours_ago}h ago (24h cooldown)")
                    continue
                else:
                    print(f"   [MERGE DETECTED] {ticker} {base_direction} matches active signal ID {_active_id}. Bypassing 24h cooldown to run AI ensemble for merge approval.")

            # ── Get tech context BEFORE ensemble (needed for ATR-based stop/target) ──
            tech_data = get_stock_technical_context(ticker)
            tech_context_str = json.dumps(tech_data) if tech_data else ""

            # ── LIQUIDITY / QUALITY GATE (lever #1) — skip penny & illiquid names ──
            # Sub-Rs price and thin-turnover stocks have unreliable candle data
            # and erratic, un-tradeable moves; they were the noisiest losers in
            # the calibration study (e.g. AURIGROW at Rs0.32). Require a minimum
            # price AND a minimum ~20-day rupee turnover. Both env-tunable;
            # missing volume/price data is allowed through (the ATR gate below
            # already drops rows with no usable data). Runs before the ensemble
            # so an illiquid name never costs a Gemini AI-vote call.
            if tech_data:
                _px = tech_data.get('current_price') or 0
                _avgvol = tech_data.get('avg_volume_20d') or 0
                _min_price = float(os.environ.get('MIN_SIGNAL_PRICE', '20'))
                _min_turnover_cr = float(os.environ.get('MIN_TURNOVER_CR', '1.0'))
                if _px and _px < _min_price:
                    print(f"   [LIQUIDITY] {ticker}: price Rs{_px} < Rs{_min_price} -> penny stock, SKIP")
                    SELECTION_FUNNEL['liquidity_skip'] += 1
                    eval_loop.log_decision('rejected_liquidity', ticker, base_direction,
                                           news_id=news_id, headline=headline,
                                           base_price=_px, news_time=_pub_dt_utc_str)
                    continue
                _turnover_cr = (_px * _avgvol) / 1e7  # rupee crore/day
                if _avgvol and _turnover_cr < _min_turnover_cr:
                    print(f"   [LIQUIDITY] {ticker}: turnover Rs{_turnover_cr:.2f}cr/day < Rs{_min_turnover_cr}cr -> illiquid, SKIP")
                    SELECTION_FUNNEL['liquidity_skip'] += 1
                    eval_loop.log_decision('rejected_liquidity', ticker, base_direction,
                                           news_id=news_id, headline=headline,
                                           base_price=_px, news_time=_pub_dt_utc_str)
                    continue

            # ── ATR-BASED DYNAMIC STOP & TARGET ──
            # ATR (Average True Range) measures a stock's typical daily price swing.
            # Stop = atr_pct * 1.0 (capped 1.0-2.5%), Target = atr_pct * 2.0 (capped 2-5%).
            # Locks the R:R at ~2:1 while scaling with each stock's actual volatility.
            #
            # No-ATR policy: REQUIRE_ATR=1 (default) skips the signal entirely if
            # ATR data isn't available. Previously we fell back to the static
            # 1%/2% defaults — but on a high-vol stock that means we were
            # setting a stop INSIDE intraday noise, which explains a chunk of
            # the 100% stop-hit rate in the live data. Set REQUIRE_ATR=0 to
            # restore the old fallback behaviour.
            _atr_pct = 0.0
            if tech_data and tech_data.get('atr_pct'):
                _atr_pct = float(tech_data['atr_pct'])
            if _atr_pct > 0:
                # Lever #2: ATR multipliers + caps are env-tunable so the stop
                # can be widened (e.g. ATR_STOP_MULT=1.5) to stop getting
                # whipsawed out by intraday noise, without a code change.
                # Defaults reproduce the original 1x/2x, 1-2.5%/2-5% behaviour.
                _stop_mult = float(os.environ.get('ATR_STOP_MULT', '1.0'))
                _tgt_mult  = float(os.environ.get('ATR_TARGET_MULT', '2.0'))
                _stop_cap  = float(os.environ.get('ATR_STOP_CAP_PCT', '2.5'))
                _tgt_cap   = float(os.environ.get('ATR_TARGET_CAP_PCT', '5.0'))
                _dynamic_stop   = round(min(_stop_cap, max(1.0, _atr_pct * _stop_mult)), 2)
                _dynamic_target = round(min(_tgt_cap, max(2.0, _atr_pct * _tgt_mult)), 2)
                print(f"   [ATR] {ticker}: ATR={_atr_pct:.2f}% → stop={_dynamic_stop:.2f}% target={_dynamic_target:.2f}%")
            else:
                if os.environ.get("REQUIRE_ATR", "1").lower() in ("1", "true", "yes"):
                    print(f"   [ATR] {ticker}: no ATR data — SKIPPING signal (REQUIRE_ATR=1).")
                    SELECTION_FUNNEL['atr_skip'] += 1
                    eval_loop.log_decision('rejected_atr', ticker, base_direction,
                                           news_id=news_id, headline=headline,
                                           base_price=(tech_data.get('current_price') if tech_data else None),
                                           news_time=_pub_dt_utc_str)
                    continue
                _dynamic_stop   = TRADE_STOP_PCT
                _dynamic_target = TRADE_TARGET_PCT
                print(f"   [ATR] {ticker}: no ATR — falling back to static {_dynamic_stop:.1f}%/{_dynamic_target:.1f}%")

            # ── News-quality context for AI prompt ──
            # Pass the catalyst classification and a calculated news-age so
            # the AI can penalize soft / stale / over-saturated coverage in
            # its own reasoning (instead of mechanical pre-filters we just
            # removed). canonical_pub_times is from the meta-tag scraper.
            _signal_catalyst = signal.get("catalyst_type") or ""
            _news_age_h = None
            try:
                _pub_t = canonical_pub_times.get(headline) or signal.get("time", "")
                if _pub_t:
                    from email.utils import parsedate_to_datetime as _ptd_age
                    _pdt = _ptd_age(_pub_t)
                    if _pdt:
                        _pdt = _assume_tz(_pdt)
                        _news_age_h = round(max(0.0, (datetime.now(timezone.utc) - _pdt).total_seconds() / 3600.0), 1)
            except Exception:
                _news_age_h = None

            # Predict using Ensemble.
            # ── Adaptive quota saver ──
            # The ensemble's AI model normally makes a fresh per-ticker Gemini
            # call (best quality). But that's ~1 call per stock — under a quota
            # crunch it drains the daily free-tier budget mid-day and then NO
            # signals get produced at all. So when usable keys are scarce we
            # switch that AI vote to the screener's already-computed score (0
            # extra calls), so predictions keep flowing on the quota that's
            # left. When keys are plentiful we use the fresh vote as before, so
            # there's no quality hit in the normal case. Threshold env-tunable;
            # set AI_SCARCE_KEY_THRESHOLD=0 to always use the fresh vote.
            _scarce_thr = int(os.environ.get("AI_SCARCE_KEY_THRESHOLD", "4"))
            _keys_scarce = len(_available_gemini_key_indices()) < _scarce_thr
            result = ensemble.predict(
                headline=ai_input,
                ticker=ticker,
                direction=base_direction,
                tech_data=tech_data,
                market_regime=market_regime,
                db_connect_fn=connect_news_db,
                api_client=client,
                model_name=MODEL_NAME,
                min_score=MIN_CONFIDENCE,
                get_client_fn=get_and_rotate_client,
                precalculated_score=signal.get("quality_score"),
                catalyst_type=_signal_catalyst,
                news_age_hours=_news_age_h,
                force_precalculated=_keys_scarce,
            )

            SELECTION_FUNNEL['ensemble_approved' if result['approved'] else 'ensemble_rejected'] += 1
            eval_loop.log_decision(
                'approved' if result['approved'] else 'rejected_ensemble',
                ticker, base_direction, news_id=news_id, headline=headline,
                final_score=result.get('final_score'),
                calibrated_p_win=result.get('calibrated_p_win'),
                base_price=base_price, atr_pct=_atr_pct,
                stop_pct=_dynamic_stop, target_pct=_dynamic_target,
                news_time=_pub_dt_utc_str)
            if result['approved']:
                # ── Directional bias circuit-breaker ──
                # If recent approved signals are dominated by one direction, demand
                # extra confidence on that side. Live 30d data showed 5/5 bearish
                # — that pattern would now require +10 confidence to push another
                # bearish through, naturally rebalancing the stream over time.
                if len(RECENT_DIRECTIONS) >= 10:
                    _same = sum(1 for d in RECENT_DIRECTIONS if d == result['direction'])
                    _bias_pct = (_same / len(RECENT_DIRECTIONS)) * 100.0
                    _bias_threshold = float(os.environ.get("BIAS_THRESHOLD_PCT", "70"))
                    if _bias_pct >= _bias_threshold:
                        _bias_boost = int(os.environ.get("BIAS_CONF_BOOST", "10"))
                        _required = MIN_CONFIDENCE + _bias_boost
                        if result['final_score'] < _required:
                            print(f"   [BIAS] {ticker} {result['direction']} REJECTED — last {len(RECENT_DIRECTIONS)} signals are {_bias_pct:.0f}% {result['direction']}; need score >= {_required} (got {result['final_score']})")
                            continue

                view = 'High Conviction' if result['final_score'] >= 85 else 'Moderate Conviction'
                quality = signal.get("quality_score")
                quality_note = f" Stock-pick quality: {quality}/100." if quality else ""
                reason = (f"Ensemble Score: {result['final_score']} ({result['models_agreeing']}/5 models approve). "
                          f"ATR stop: {_dynamic_stop:.1f}% | target: {_dynamic_target:.1f}%.{quality_note}")
                approved_signals.append((news_id, ticker, result['direction'], _dynamic_target,
                                         view, reason, base_price, current_price_now,
                                         result['final_score'], tech_context_str, result['detail'], _pub_dt_utc_str))
                # Record this direction in the rolling bias window so the
                # circuit-breaker above can see the trend on subsequent signals.
                RECENT_DIRECTIONS.append(result['direction'])

                # ── "The Ripple" — auto-generate propagation graph for BIG news ──
                # Only fires for events that clear the macro-grade threshold
                # (commodity shocks, central-bank decisions, geopolitical,
                # election, policy, currency). Backgrounded so the save loop
                # never blocks on the extra Gemini call.
                try:
                    _ripple_impact_count = len([s for s in screened_signals if s.get('headline') == headline])
                    _is_big, _ripple_sc = is_big_news(signal, impact_count=_ripple_impact_count)
                    if _is_big and news_id:
                        # Avoid re-generating if we already did one for this news_id this session.
                        # Cheap memo via module-level set.
                        try:
                            _ripple_inflight = globals().setdefault('_RIPPLE_INFLIGHT', set())
                        except Exception:
                            _ripple_inflight = set()
                        if news_id not in _ripple_inflight:
                            _ripple_inflight.add(news_id)
                            _ripple_payload = {
                                'news_id':        news_id,
                                'headline':       headline,
                                'body':           body_text,
                                'catalyst_type':  signal.get('catalyst_type', ''),
                                'primary':        [ticker],
                                'score':          _ripple_sc,
                            }
                            def _gen_ripple(p=_ripple_payload):
                                try:
                                    print(f"   [RIPPLE] Building propagation graph for news_id={p['news_id']} (score={p['score']})...", flush=True)
                                    graph = generate_ripple_graph(
                                        p['headline'], p['body'], p['catalyst_type'], p['primary']
                                    )
                                    if graph:
                                        save_ripple_to_db(p['news_id'], p['score'], True, graph)
                                        print(f"   [RIPPLE] Saved ripple for news_id={p['news_id']}", flush=True)
                                    else:
                                        # Save the score even if generation failed so the badge
                                        # still shows; the endpoint can retry on click.
                                        save_ripple_to_db(p['news_id'], p['score'], True, None)
                                        print(f"   [RIPPLE] Generation failed for news_id={p['news_id']} (will retry on click)", flush=True)
                                except Exception as _re:
                                    print(f"   [RIPPLE] error: {_re}", flush=True)
                            threading.Thread(target=_gen_ripple, daemon=True, name=f"Ripple-{news_id}").start()
                except Exception as _ripple_err:
                    print(f"   [RIPPLE] dispatch error (non-fatal): {_ripple_err}", flush=True)

                # ── WhatsApp alert dispatch (high-conviction only) ──
                # Spawned on a daemon thread so Meta Cloud API latency NEVER
                # blocks the signal-save loop. Previously a slow API call
                # could hold the loop for seconds per signal; now it returns
                # in microseconds. Sender's own cooldowns + daily caps still
                # apply. Confidence floor: only signals >=80 get pushed.
                try:
                    if result['final_score'] >= int(os.environ.get('WHATSAPP_MIN_CONFIDENCE', '80')):
                        _wa_payload = {
                            'ticker':     ticker,
                            'direction':  result['direction'],
                            'confidence': result['final_score'],
                            'target_pct': _dynamic_target,
                            'stop_pct':   _dynamic_stop,
                            'headline':   headline,
                        }
                        def _wa_fire(payload=_wa_payload):
                            try:
                                import whatsapp_sender as _wa
                                _wa.send_signal_alert(payload)
                            except Exception as _wa_err:
                                print(f"   [WA] async dispatch error (non-fatal): {_wa_err}", flush=True)
                        threading.Thread(target=_wa_fire, daemon=True, name=f"WA-{ticker}").start()
                except Exception as _wa_err:
                    print(f"   [WA] queue error (non-fatal): {_wa_err}", flush=True)

                # Mark this ticker+direction as recently signalled to prevent duplicates
                RECENT_SIGNALS[_cooldown_key] = _now_utc
                # Bug #2 fix: dict.update() only adds/overwrites — it never removes keys.
                # Prune expired entries IN-PLACE — a full reassignment (`RECENT_SIGNALS = {...}`)
                # makes Python treat the name as a function-local, which breaks the read at
                # line ~4209 with UnboundLocalError on the next iteration (Bug #29).
                cutoff = _now_utc - timedelta(hours=48)
                _expired_keys = [_k for _k, _v in RECENT_SIGNALS.items() if _v <= cutoff]
                for _k in _expired_keys:
                    RECENT_SIGNALS.pop(_k, None)

            # ── Save approved signals in one short atomic write ──
            if approved_signals:
                _sigs = approved_signals
                def _insert_signals(conn, c, _s=_sigs):
                    for sig in _s:
                        news_id, ticker, impact, est_change, view_str, reason_str, bp, cp, conf, tech_ctx, ens_det, created_at_str = sig
                        
                        # Check for existing mergeable active signal
                        c.execute("""
                            SELECT id, confidence_score, reason, base_price, created_at, impact
                            FROM stock_impact
                            WHERE ticker = ? AND status = 'Active View'
                            ORDER BY created_at DESC LIMIT 1
                        """, (ticker,))
                        row = c.fetchone()
                        
                        merged = False
                        if row:
                            db_id, db_conf, db_reason, db_bp, db_created, db_impact = row
                            try:
                                new_dt = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S')
                            except Exception:
                                new_dt = datetime.now(timezone.utc).replace(tzinfo=None)  # Bug #22 fix: utcnow() deprecated
                            try:
                                db_dt = datetime.strptime(db_created, '%Y-%m-%d %H:%M:%S')
                            except Exception:
                                db_dt = new_dt
                                
                            time_diff = abs((new_dt - db_dt).total_seconds())
                            if time_diff <= 86400: # 24 hours
                                is_similar = False
                                if db_bp == 0.0 or bp == 0.0:
                                    is_similar = True
                                else:
                                    pct_diff = abs(db_bp - bp) / db_bp
                                    if pct_diff <= 0.025: # within 2.5%
                                        is_similar = True
                                        
                                if is_similar:
                                    # ── Decaying consensus boost (Option 1) ──
                                    # A flat +10 per merge let 3 weak (~70) signals
                                    # stack straight to 93 "High Conviction". Each
                                    # extra corroboration is now worth progressively
                                    # less, so a pile of marginal signals can't fake
                                    # conviction. Count prior merges from the reason
                                    # trail (each merge appended a "[Consensus Boost"
                                    # marker). Schedule: 1st +5, 2nd +3, 3rd +2, 4th+ +1.
                                    _prior_merges = (db_reason or "").count("[Consensus Boost")
                                    try:
                                        _boost_schedule = [int(x) for x in os.environ.get("MERGE_BOOST_SCHEDULE", "5,3,2,1").split(",")]
                                    except Exception:
                                        _boost_schedule = [5, 3, 2, 1]
                                    _this_boost = _boost_schedule[min(_prior_merges, len(_boost_schedule) - 1)]
                                    _raw_boosted = max(db_conf, conf) + _this_boost

                                    # ── Cap unless a single signal was strong (Option 3) ──
                                    # Merge-stacking alone must not reach the High
                                    # Conviction band (>=85). Unless ONE underlying
                                    # signal independently scored >= MERGE_HIGH_BASE,
                                    # clamp the merged confidence to MERGE_CONF_CAP so
                                    # it stays Moderate. A genuinely strong single
                                    # signal (e.g. a clean 88) keeps its high label.
                                    _merge_cap = int(os.environ.get("MERGE_CONF_CAP", "80"))
                                    _high_base = int(os.environ.get("MERGE_HIGH_BASE", "85"))
                                    _strong_single = (db_conf >= _high_base) or (conf >= _high_base)
                                    if not _strong_single:
                                        _raw_boosted = min(_raw_boosted, _merge_cap)
                                    boosted_conf = min(99, _raw_boosted)
                                    new_view = 'High Conviction' if boosted_conf >= 85 else 'Moderate Conviction'
                                    
                                    c.execute("SELECT headline FROM news WHERE id = ?", (news_id,))
                                    hl_row = c.fetchone()
                                    new_hl = hl_row[0] if hl_row else "Consensus News"
                                    
                                    if impact != db_impact:
                                        if conf > db_conf:
                                            final_impact = impact
                                        else:
                                            final_impact = db_impact
                                        merged_reason = f"{db_reason} | [Consensus Boost ({impact}): '{new_hl}'] {reason_str}"
                                    else:
                                        final_impact = db_impact
                                        merged_reason = f"{db_reason} | [Consensus Boost: '{new_hl}'] {reason_str}"
                                    
                                    c.execute("""
                                        UPDATE stock_impact
                                        SET confidence_score = ?, view = ?, reason = ?, impact = ?
                                        WHERE id = ?
                                    """, (boosted_conf, new_view, merged_reason, final_impact, db_id))
                                    print(f"   [MERGE] Merged new signal for {ticker} into existing active signal ID {db_id}. Confidence boosted to {boosted_conf}. Impact set to {final_impact}.")
                                    merged = True
                        
                        if not merged:
                            c.execute('''INSERT OR IGNORE INTO stock_impact
                                (news_id, ticker, impact, estimated_change_percent, view, reason, base_price, current_price, confidence_score, technical_context, ensemble_detail, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', sig)
                db_write(_insert_signals)
                print(f"   [+] ENSEMBLE APPROVED: {headline[:45]}... ({len(approved_signals)} alpha signals)")
        print(f"PHASE 1 DONE: {len(new_article_ids)} new headlines saved INSTANTLY to database!")
        # Heartbeat: record actual save count so /api/debug-worker-status can
        # show "scraped N, saved M" instead of "scraped N, ???".  M=0 with
        # large N means the AI screener is bailing out (cooldown or filter).
        _heartbeat("ai_news", last_save_count=len(new_article_ids))
        # Bug #10 fix: sleep moved to END of the full loop (after Phase 2) so that
        # Phase 2 is not squeezed into the previous cycle's idle window, and the next
        # Phase 1 always has a full 3-minute gap after Phase 2 completes.

        
        # ============================================================
        # PHASE 2 disabled — explanations are now generated ON-DEMAND
        # ============================================================
        # Previously: every new headline auto-got an Aam Janta translation +
        # 4-step Macro Pathway via a separate Gemini call. That burned ~700
        # calls/day on articles users may never open. Replaced by the
        # /api/news/<id>/explain endpoint, which generates and caches the
        # explanation only when a user actually clicks into the article.
        # Set PHASE2_ENABLED=1 to restore the old background behaviour.
        if os.environ.get("PHASE2_ENABLED", "0").lower() in ("1", "true", "yes"):
            print("[Phase 2] (legacy) PHASE2_ENABLED=1 — background explanations re-enabled.")
            # (Legacy code path intentionally removed. To restore, see git history
            #  prior to the 'remove Phase 2 background' commit, or set the env var
            #  and re-enable in a future commit.)

        # NOTE: the old per-cycle "DELETE news + stock_impact older than 7 days"
        # was REMOVED — it was destroying signals long before the intended
        # 90-day window (this is why the track record / signal terminal kept
        # losing history). Retention is now owned exclusively by archival_worker,
        # which every 24h MOVES rows older than SIGNAL_RETENTION_DAYS (90) into
        # the *_archive tables (reversible insert+delete), so signals persist for
        # a full 90 days and are never hard-deleted on the hot path.
            
        # Performance report
        try:
            import performance_report
            print("\n" + "="*60)
            print(" END OF CYCLE — PERFORMANCE REPORT:")
            print("="*60)
            performance_report.run_performance_check()
        except Exception as e:
            print("Performance Report Error:", e)

        # Bug #10 fix: 3-minute inter-cycle sleep moved here so it applies to the
        # FULL loop body (Phase 1 + Phase 2 + cleanup) not just Phase 1.
        _heartbeat(
            "ai_news",
            last_cycle_finished_at=time.time(),
            cycles_completed=WORKER_HEARTBEAT["ai_news"].get("cycles_completed", 0) + 1,
        )

        # ── End-of-cycle memory hygiene ──
        # 1. Cap SEEN_HEADLINES so it doesn't grow unbounded over weeks of
        #    uptime (was a 'set', could otherwise reach 100K+ entries).
        #    Keep the most recent N — anything older is fine to re-screen
        #    since the AI is fast and DB INSERT OR IGNORE handles dupes.
        try:
            _seen_cap = int(os.environ.get("SEEN_HEADLINES_CAP", "5000"))
            if len(SEEN_HEADLINES) > _seen_cap:
                # Sets don't preserve order; we just drop arbitrary entries.
                # This is acceptable because the DB's UNIQUE INDEX on
                # news.headline is the source of truth for dedup.
                _excess = len(SEEN_HEADLINES) - _seen_cap
                for _ in range(_excess):
                    SEEN_HEADLINES.pop()
                print(f"   [MEM] Capped SEEN_HEADLINES from {_seen_cap + _excess} to {_seen_cap}.")
            # RECENT_SIGNALS (cooldown guard) had no eviction and grew forever;
            # old entries are already past the 24h cooldown, so dropping the
            # oldest-inserted is safe.
            _sig_cap = int(os.environ.get("RECENT_SIGNALS_CAP", "10000"))
            if len(RECENT_SIGNALS) > _sig_cap:
                for _k in list(RECENT_SIGNALS.keys())[:len(RECENT_SIGNALS) - _sig_cap]:
                    RECENT_SIGNALS.pop(_k, None)
                print(f"   [MEM] Capped RECENT_SIGNALS to {_sig_cap}.")
        except Exception:
            pass
        # 2. Force GC to reclaim per-cycle transients (BeautifulSoup trees,
        #    feedparser parses, AI screener prompts, etc.). On Render's 512MB
        #    free tier this is the difference between stable and OOM-killed.
        try:
            _freed = gc.collect()
            if _freed:
                print(f"   [MEM] gc.collect freed {_freed} objects this cycle.")
        except Exception:
            pass

        # Cycle cadence — 300s → 600s (10 min). With Phase 2 removed from the
        # background pipeline, AILogicModel is the dominant Gemini consumer and
        # doubling the interval halves daily call volume essentially for free.
        # 144 cycles/day at 10-min is still well above the news refresh need
        # for end-of-day-trading horizon. Override via SCRAPER_CYCLE_SLEEP_SECS
        # if RPD usage drops low enough to tighten cycles again.
        time.sleep(int(os.environ.get("SCRAPER_CYCLE_SLEEP_SECS", "600")))
      except Exception as _loop_err:
        print(f"[FATAL LOOP ERROR] {_loop_err}")
        import traceback; traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        _heartbeat("ai_news", last_error=str(_loop_err)[:300], last_error_at=time.time())
        time.sleep(30)  # Wait 30s before retrying

# Bug #1 fix: The second (simpler) definition of get_price_with_range that was here
# has been removed.  The authoritative definition at the top of this file (see
# _get_yahoo_official_close) correctly handles market-closed pricing via Yahoo Finance.


def _fetch_ohlc_direct(ticker, days=14):
    """
    Fetch daily OHLC from Angel One SmartAPI (with Yahoo Finance fallback).
    Returns list of (datetime_utc, high, low, close) tuples.
    """
    # Yahoo Finance chart API is reliable for daily historical data.
    # (Angel One's historical OHLC API is currently broken and only returns today's data,
    # which breaks historical target/stop hit detection).
    # Fallback: Yahoo Finance chart API
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={days}d&interval=1d"
        resp = HTTP_SESSION.get(url, timeout=8)
        result = resp.json()['chart']['result'][0]
        timestamps = result.get('timestamp', [])
        quote  = result['indicators']['quote'][0]
        opens  = quote.get('open',  [None] * len(timestamps))
        highs  = quote.get('high',  [None] * len(timestamps))
        lows   = quote.get('low',   [None] * len(timestamps))
        closes = quote.get('close', [None] * len(timestamps))
        rows = []
        for ts, o, h, l, c in zip(timestamps, opens, highs, lows, closes):
            if o is not None and h is not None and l is not None:
                rows.append((
                    datetime.fromtimestamp(ts, tz=timezone.utc),
                    float(o), float(h), float(l),
                    float(c) if c else 0.0
                ))
        return rows
    except Exception:
        return []


def check_historical_hits(ticker, since_dt, base_price, target_pct, stop_pct, is_bullish,
                           ohlc_rows=None, start_date=None):
    """
    Checks chronological daily OHLC data starting from start_date (or since_dt if not given).
    start_date allows after-hours signals to skip the signal day entirely and only
    check from the NEXT trading session onward — preventing false Stop Loss Hits on
    the opening gap of the next day.
    Returns (hit_status, diff_percent) or (None, None).
    """
    try:
        if ohlc_rows is None:
            ohlc_rows = _fetch_ohlc_direct(ticker)
        if not ohlc_rows:
            return None, None

        IST = timezone(timedelta(hours=5, minutes=30))
        today_ist = datetime.now(IST).date()

        # Use explicitly passed start_date for after-hours signals, otherwise use the exact signal timestamp.
        if start_date is not None:
            check_from_date = start_date
            check_from_dt = None
        else:
            since_utc = since_dt.replace(tzinfo=timezone.utc) if not since_dt.tzinfo else since_dt.astimezone(timezone.utc)
            check_from_dt = since_utc.astimezone(IST)
            check_from_date = check_from_dt.date()

        for (bar_dt, o, h, l, _c) in ohlc_rows:
            bar_dt_ist = bar_dt.astimezone(IST)
            bar_date_ist = bar_dt_ist.date()
            if bar_date_ist < check_from_date or bar_date_ist > today_ist:
                continue

            if check_from_dt is not None and bar_dt_ist <= check_from_dt:
                continue

            h_pct = ((h - base_price) / base_price) * 100
            l_pct = ((l - base_price) / base_price) * 100
            if is_bullish:
                # Bug #18 fix: Check target before stop for bullish signals.
                # If both levels are breached in the same candle (gap day), assume
                # the target was hit first (benefit of the doubt for long positions).
                if h_pct >= target_pct: return 'Predicted Target Hit', round(h_pct, 2)
                if l_pct <= -stop_pct:  return 'Stop Loss Hit',       round(l_pct, 2)
            else:
                # For bearish signals, check target first (downside).
                if l_pct <= -target_pct: return 'Predicted Target Hit', round(l_pct, 2)
                if h_pct >= stop_pct:    return 'Stop Loss Hit',       round(h_pct, 2)
        return None, None
    except Exception:
        return None, None


def _parse_created_at(created_at_str):
    try:
        if '+' in created_at_str or 'GMT' in created_at_str:
            dt = parsedate_to_datetime(created_at_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        return datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(created_at_str)
        except Exception:
            return None


def _published_during_nse_hours(created_dt_utc):
    IST = timezone(timedelta(hours=5, minutes=30))
    dt_ist = created_dt_utc.astimezone(IST)
    if dt_ist.weekday() >= 5:
        return False
    minutes = dt_ist.hour * 60 + dt_ist.minute
    return (9 * 60 + 15) <= minutes < (15 * 60 + 30)  # Bug #23 fix: strict < at close


def _next_session_open_price(ticker, signal_dt_utc, ohlc_rows):
    if not ohlc_rows:
        return None, None
    IST = timezone(timedelta(hours=5, minutes=30))
    signal_date_ist = signal_dt_utc.astimezone(IST).date()
    for (bar_dt, o, h, l, c) in ohlc_rows:
        bar_date_ist = bar_dt.astimezone(IST).date()
        if bar_date_ist > signal_date_ist:
            return o, bar_date_ist
    return None, None


def repair_existing_signal_statuses(days=14):
    print("[REPAIR] Reconciling recent signals against dynamic or 2%/1% rules...")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    conn = connect_news_db()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT id, ticker, impact, base_price, current_price, status, created_at, estimated_change_percent, reason
        FROM stock_impact
        WHERE created_at > ?
    """, (cutoff,))
    rows = c.fetchall()
    if not rows:
        conn.close()
        print("[REPAIR] No recent signals found to reconcile.")
        return

    ohlc_cache = {}
    updates = []
    fixed = 0

    for row in rows:
        stock_id = row['id']
        ticker = row['ticker']
        impact = row['impact'] or ''
        base_price = row['base_price'] or 0.0
        current_price = row['current_price'] or 0.0
        status = row['status'] or 'Active View'
        created_at_str = row['created_at']

        created_dt = _parse_created_at(created_at_str)
        if not created_dt:
            continue

        if current_price <= 0:
            current_price = get_robust_price(ticker)
            if current_price <= 0:
                continue

        if base_price <= 0:
            base_price = current_price

        signal_market_hours = _published_during_nse_hours(created_dt)
        start_date = None

        if not signal_market_hours:
            if ticker not in ohlc_cache:
                ohlc_cache[ticker] = _fetch_ohlc_direct(ticker, days=14)
            next_open, next_session_date = _next_session_open_price(ticker, created_dt, ohlc_cache[ticker])
            if next_open and next_open > 0:
                start_date = next_session_date

        if start_date is None:
            start_date = created_dt.astimezone(timezone(timedelta(hours=5, minutes=30))).date()

        is_bullish = 'bullish' in impact.lower()
        target_pct = TRADE_TARGET_PCT
        stop_pct = TRADE_STOP_PCT
        
        reason_str = row['reason'] or ''
        import re as _re
        m_tgt = _re.search(r'target[:\s]+([0-9.]+)%', reason_str, _re.I)
        m_stp = _re.search(r'stop[:\s]+([0-9.]+)%', reason_str, _re.I)
        if m_tgt:
            try: target_pct = float(m_tgt.group(1))
            except: pass
        if m_stp:
            try: stop_pct = float(m_stp.group(1))
            except: pass

        if ticker not in ohlc_cache:
            ohlc_cache[ticker] = _fetch_ohlc_direct(ticker, days=14)

        hist_status, hist_diff = check_historical_hits(
            ticker, created_dt, base_price, target_pct, stop_pct, is_bullish,
            ohlc_rows=ohlc_cache.get(ticker), start_date=start_date
        )

        if hist_status:
            new_status = hist_status
            diff_percent = hist_diff
        else:
            age_hours = (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600
            diff_percent = round(((current_price - base_price) / base_price) * 100, 2) if base_price > 0 else 0.0
            new_status = 'Expired' if age_hours >= SIGNAL_EXPIRY_HOURS else 'Active View'

        if abs(base_price - (row['base_price'] or 0.0)) > 0.01 or new_status != status or abs(diff_percent - (row['estimated_change_percent'] or 0.0)) > 0.1:
            updates.append((current_price, base_price, new_status, diff_percent, stock_id))
            fixed += 1

    if updates:
        def _apply_repair(conn_inner, c_inner):
            c_inner.executemany(
                """UPDATE stock_impact
                   SET current_price = ?, base_price = ?, status = ?, estimated_change_percent = ?
                   WHERE id = ?""",
                updates
            )
        db_write(_apply_repair)

    conn.close()
    print(f"[REPAIR] Completed. Fixed {fixed} signal rows out of {len(rows)} reviewed.")


def yfinance_worker():
    print("YFinance Live Price Engine v2.4 Started. Always-Update + Market-Aware Evaluation...")

    while True:
        try:
            _heartbeat("yfinance", last_cycle_started_at=time.time())
            market_currently_open = is_market_open()

            # ── PHASE A: Read active stocks ──
            conn = connect_news_db()
            c = conn.cursor()
            # ALL non-expired signals within 14 days — for status evaluation
            fourteen_days_ago = (datetime.now(timezone.utc) - timedelta(days=14)).strftime('%Y-%m-%d %H:%M:%S')
            c.execute("SELECT id, news_id, ticker, base_price, impact, created_at, status, reason FROM stock_impact WHERE status != 'Expired' AND created_at > ?", (fourteen_days_ago,))
            active_stocks = c.fetchall()

            # ALSO fetch resolved rows where current_price still equals base_price
            # These never got a live price update — refresh them now
            c.execute("""
                SELECT id, news_id, ticker, base_price, impact, created_at, status, reason
                FROM stock_impact
                WHERE ABS(current_price - base_price) < 0.01
                AND created_at > ?
            """, (fourteen_days_ago,))
            stale_price_rows = c.fetchall()
            # Merge, deduplicating by id
            seen_ids = {r[0] for r in active_stocks}
            for r in stale_price_rows:
                if r[0] not in seen_ids:
                    active_stocks.append(r)
                    seen_ids.add(r[0])
            conn.close()

            if not active_stocks:
                print(f"   [YF] No active stocks to update. Market {'Open' if market_currently_open else 'Closed'}.")
                time.sleep(60)
                continue

            updates = []       # ('price_only'|'full', ...)
            patterns = []      # for historical_patterns logging
            _ohlc_cache = {}   # ticker -> ohlc_rows, fetched once per cycle
            print(f"   [YF] Processing {len(active_stocks)} signals...")

            # ── Pre-warm price cache in parallel ──
            # Previously each active row triggered a sequential network call
            # via get_price_with_range(). With 50+ active signals that's 50
            # serial round-trips per cycle. Pre-fetching every unique ticker
            # concurrently warms the 30-second _QUOTE_CACHE so the serial
            # loop below hits cache for ~every call. Net result: cycle wall-
            # clock drops from ~25s to ~3s on the typical 50-signal day.
            unique_tickers = list({row[2] for row in active_stocks if row[2]})
            if unique_tickers:
                # Default 4 (was 10) — keeps peak memory low on free-tier Render.
                # Each concurrent yfinance fetch holds pandas DataFrame state.
                _yf_workers = int(os.environ.get("YF_PREFETCH_WORKERS", "4"))
                def _prewarm(tk, _mo=market_currently_open):
                    try:
                        get_price_with_range(tk, market_open=_mo)
                    except Exception:
                        pass
                with concurrent.futures.ThreadPoolExecutor(max_workers=_yf_workers) as _pool:
                    list(_pool.map(_prewarm, unique_tickers))

            for row in active_stocks:
                stock_id, news_id, ticker, base_price, impact, created_at_str, status, reason = row

                # ── Fetch current price (uses 30s _TICKER_CACHE — deduplicates same-ticker rows) ──
                current_price, today_high, today_low = get_price_with_range(
                    ticker, market_open=market_currently_open
                )

                if current_price is None or current_price <= 0:
                    continue

                current_price = round(float(current_price), 2)

                # ── If base_price is 0, initialize it to current_price ──
                if base_price == 0.0 or base_price is None:
                    base_price = current_price
                    _sid_init = stock_id
                    _bp_init = base_price
                    def _init_base(conn, c, _sid=_sid_init, _cp=_bp_init):
                        c.execute("UPDATE stock_impact SET base_price=?, current_price=? WHERE id=?", (_cp, _cp, _sid))
                    db_write(_init_base)
                    print(f"   [YF] Initialized base_price=current_price={base_price} for {ticker} (ID={_sid_init})")

                # For outside-market-hours news, keep base_price anchored to
                # the news-time close, but start target/stop evaluation from
                # the next tradable session.
                _IST = timezone(timedelta(hours=5, minutes=30))
                _hist_start_date = None  # Will be set to next session date for after-hours signals

                if status == 'Active View':
                    try:
                        _pub_ist = parsedate_to_datetime(created_at_str).astimezone(_IST) \
                            if ('+' in created_at_str or 'GMT' in created_at_str) else \
                            datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S').replace(
                                tzinfo=timezone.utc).astimezone(_IST)

                        _now_ist = datetime.now(_IST)
                        _last_close = _now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
                        _signal_ist_date = _pub_ist.date()
                        _today_ist = _now_ist.date()
                        _t = _pub_ist.hour * 60 + _pub_ist.minute
                        _news_was_market_hours = (
                            _pub_ist.weekday() < 5 and
                            (9 * 60 + 15) <= _t < (15 * 60 + 30)  # Bug #23 fix: strict < at close
                        )

                        if not _news_was_market_hours:
                            if ticker not in _ohlc_cache:
                                _ohlc_cache[ticker] = _fetch_ohlc_direct(ticker, days=14)

                            _next_open_price = None
                            _next_session_date = None
                            for (bar_dt, o, h, l, _bc) in _ohlc_cache[ticker]:
                                bar_date_ist = bar_dt.astimezone(_IST).date()
                                if bar_date_ist > _signal_ist_date or (
                                        bar_date_ist == _signal_ist_date and _t < 9 * 60 + 15):
                                    _next_open_price = o
                                    _next_session_date = bar_date_ist
                                    break

                            if _next_open_price and _next_open_price > 0:
                                # Historical check must start from NEXT session, not signal day
                                _hist_start_date = _next_session_date

                            # If next trading session hasn't started yet, keep
                            # diff at 0% using the original news-time base.
                            if not has_market_traded_since(created_at_str):
                                current_price = base_price
                    except Exception:
                        pass

                # Always compute diff from the authoritative base_price
                diff_percent = round(((current_price - base_price) / base_price) * 100, 2) if base_price and base_price > 0 else 0.0
                new_status = status  # Keep the old status by default

                # Evaluate target hit / stop loss ONLY IF it hasn't triggered yet
                if status == 'Active View':
                    impact_lower = impact.lower()
                    is_bullish = 'bullish' in impact_lower
                    target_pct = TRADE_TARGET_PCT
                    stop_pct   = TRADE_STOP_PCT

                    reason_str = reason or ''
                    import re as _re
                    m_tgt = _re.search(r'target[:\s]+([0-9.]+)%', reason_str, _re.I)
                    m_stp = _re.search(r'stop[:\s]+([0-9.]+)%', reason_str, _re.I)
                    if m_tgt:
                        try: target_pct = float(m_tgt.group(1))
                        except: pass
                    if m_stp:
                        try: stop_pct = float(m_stp.group(1))
                        except: pass

                    # ── 1. Multi-day catch-up (History from NEXT SESSION for after-hours signals) ──
                    try:
                        created_dt = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                        age_hours = (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600
                        if age_hours >= 12:  # old enough to have "yesterday"
                            if ticker not in _ohlc_cache:
                                _ohlc_cache[ticker] = _fetch_ohlc_direct(ticker, days=14)
                            hist_status, hist_diff = check_historical_hits(
                                ticker, created_dt, base_price, target_pct, stop_pct, is_bullish,
                                ohlc_rows=_ohlc_cache[ticker],
                                start_date=_hist_start_date  # None for intraday; next session date for after-hours
                            )
                            if hist_status:
                                new_status = hist_status
                                diff_percent = hist_diff
                    except Exception:
                        pass

                    # ── 2. Intraday high/low evaluation (Only during MARKET HOURS) ──
                    if new_status == 'Active View' and market_currently_open:
                        eval_high = today_high if today_high else current_price
                        eval_low  = today_low  if today_low  else current_price

                        high_pct = ((eval_high - base_price) / base_price) * 100
                        low_pct  = ((eval_low  - base_price) / base_price) * 100

                        if is_bullish:
                            if low_pct <= -stop_pct:
                                new_status = 'Stop Loss Hit'
                                diff_percent = low_pct
                            elif high_pct >= target_pct:
                                new_status = 'Predicted Target Hit'
                                diff_percent = high_pct
                        else:  # BEARISH
                            if high_pct >= stop_pct:
                                new_status = 'Stop Loss Hit'
                                diff_percent = high_pct
                            elif low_pct <= -target_pct:
                                new_status = 'Predicted Target Hit'
                                diff_percent = low_pct

                    # ── 3. Current price evaluation (MARKET HOURS ONLY) ──
                    # Only evaluate SL/TP from current price during trading hours.
                    # After hours, diff_percent is 0 for same-day signals, so this
                    # would never trigger anyway. For older signals, historical
                    # OHLC catch-up (section 1) handles it.
                    if new_status == 'Active View' and market_currently_open and base_price > 0:
                        if is_bullish:
                            if diff_percent >= target_pct:
                                new_status = 'Predicted Target Hit'
                            elif diff_percent <= -stop_pct:
                                new_status = 'Stop Loss Hit'
                        else:  # BEARISH
                            if diff_percent <= -target_pct:
                                new_status = 'Predicted Target Hit'
                            elif diff_percent >= stop_pct:
                                new_status = 'Stop Loss Hit'

                    # Check expiry (always, regardless of market hours)
                    if new_status == 'Active View':
                        try:
                            created_dt = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S')
                            age_hours = (datetime.now(timezone.utc).replace(tzinfo=None) - created_dt).total_seconds() / 3600
                            if age_hours >= SIGNAL_EXPIRY_HOURS:
                                new_status = 'Expired'
                        except Exception:
                            pass

                    # Only log to patterns if status just changed right now
                    if new_status in ['Predicted Target Hit', 'Stop Loss Hit', 'Reacted Against Prediction']:
                        is_bullish_flag = 'bullish' in impact.lower()
                        patterns.append((news_id, ticker, is_bullish_flag, diff_percent, new_status))

                # ── CRITICAL: Split updates by signal type ──
                # Resolved signals: Only update current_price, NEVER touch estimated_change_percent.
                # Active signals: Full update: current_price + status + estimated_change_percent
                if status in ('Stop Loss Hit', 'Predicted Target Hit', 'Reacted Against Prediction', 'Expired'):
                    updates.append(('price_only', current_price, stock_id))
                else:
                    updates.append(('full', current_price, new_status, round(diff_percent, 2), stock_id))

            # ── PHASE C: Write all updates ──
            if updates:
                _updates = updates
                _patterns = patterns
                def _write_prices(conn, c):
                    price_only = [(u[1], u[2]) for u in _updates if u[0] == 'price_only']
                    full_upd   = [(u[1], u[2], u[3], u[4]) for u in _updates if u[0] == 'full']
                    if price_only:
                        c.executemany(
                            "UPDATE stock_impact SET current_price = ? WHERE id = ?",
                            price_only
                        )
                    if full_upd:
                        c.executemany(
                            """UPDATE stock_impact
                               SET current_price = ?, status = ?, estimated_change_percent = ?
                               WHERE id = ?""",
                            full_upd
                        )
                    for news_id, ticker, is_bullish, diff_percent, new_status in _patterns:
                        c.execute("SELECT headline FROM news WHERE id = ?", (news_id,))
                        row = c.fetchone()
                        if row:
                            outcome = 'HIT' if new_status == 'Predicted Target Hit' else 'STOP'
                            direction = 'BULLISH' if is_bullish else 'BEARISH'
                            c.execute(
                                '''INSERT INTO historical_patterns (headline, ticker, direction, outcome, change_pct)
                                   VALUES (?, ?, ?, ?, ?)''',
                                (row[0], ticker, direction, outcome, diff_percent)
                            )
                db_write(_write_prices)
                n_po = sum(1 for u in updates if u[0] == 'price_only')
                n_fl = sum(1 for u in updates if u[0] == 'full')
                print(f"   [YF] Updated {n_fl} active + {n_po} resolved prices. Market {'Open' if market_currently_open else 'Closed'}.")

            _heartbeat(
                "yfinance",
                last_cycle_finished_at=time.time(),
                cycles_completed=WORKER_HEARTBEAT["yfinance"].get("cycles_completed", 0) + 1,
            )

            # End-of-cycle memory hygiene — pandas DataFrames from yfinance are
            # the heaviest transient objects we allocate. Explicitly drop the
            # local OHLC cache reference and force gc so they actually free.
            try:
                _ohlc_cache.clear()
            except Exception:
                pass
            try:
                gc.collect()
            except Exception:
                pass

        except Exception as e:
            print("YFinance Worker Error:", e)
            _heartbeat("yfinance", last_error=str(e)[:300], last_error_at=time.time())

        # Poll every 60 seconds always (fast enough to initialize new news prices quickly)
        time.sleep(60)

# Threading starts moved to main block to prevent Flask reloader duplicate race conditions.

# ==========================================
# APP ROUTES
# ==========================================
@app.route('/')
def home():
    # T1.3: short cache with must-revalidate so users get fresh UI within 1 minute
    # but repeat visits inside that window are instant from the browser cache.
    resp = make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'public, max-age=60, must-revalidate'
    return resp


# ── T3.13 Service Worker ──
# Served from the site root so its scope is the whole origin.
# Cache-Control no-cache forces every browser to revalidate /sw.js on each load
# so deploys that bump the SW's CACHE_VERSION actually ship to users promptly.
@app.route('/sw.js')
def service_worker():
    resp = send_from_directory(app.static_folder, 'sw.js')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp


# ── T3.14 PWA manifest ──
@app.route('/manifest.json')
def pwa_manifest():
    resp = send_from_directory(app.static_folder, 'manifest.json')
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp

def _fetch_index_from_yahoo_chart(symbol, market_open=None, now_ist=None):
    """
    Fallback: fetch index price from Yahoo Finance free chart API.
    No API key needed. Returns (last_price, prev_close) or (None, None).
    """
    try:
        yf_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
        resp = requests.get(url, headers=yf_headers, timeout=8)
        data = resp.json()
        result = data.get('chart', {}).get('result', [{}])[0]
        meta = result.get('meta', {})
        if market_open is None:
            market_open = is_market_open()
        if now_ist is None:
            now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        expected_close_date = _expected_index_close_date(now_ist, market_open)

        last_price = meta.get('regularMarketPrice')
        quotes = result.get('indicators', {}).get('quote', [{}])[0]
        timestamps = result.get('timestamp', [])
        close_pairs = []
        for ts, close in zip(timestamps, quotes.get('close', [])):
            close_value = _market_quote_float(close)
            if close_value is not None:
                close_pairs.append((ts, close_value))
        closes = [close for _, close in close_pairs]

        latest_close_is_current_session = None
        if close_pairs:
            latest_close_date = datetime.fromtimestamp(
                close_pairs[-1][0], tz=timezone.utc
            ).astimezone(now_ist.tzinfo).date()
            latest_close_is_current_session = latest_close_date == expected_close_date

        if not market_open and closes and latest_close_is_current_session:
            last_price = closes[-1]
        elif not last_price or last_price <= 0:
            if closes:
                last_price = closes[-1]

        prev_close = _derive_previous_close(
            closes, last_price, market_open, latest_close_is_current_session
        )
        if not prev_close:
            prev_close = (
                meta.get('previousClose')
                or meta.get('regularMarketPreviousClose')
                or meta.get('chartPreviousClose')
            )
        if last_price and last_price > 0:
            return float(last_price), float(prev_close) if prev_close else None
    except Exception as e:
        print(f"   [Index Fallback] Yahoo error for {symbol}: {e}")
    return None, None


def _fetch_nse_index_quotes():
    """
    Fetch official NSE index snapshots. Yahoo can lag by one trading day for
    Indian indices after close, so use NSE first for NSE-managed indices.
    """
    try:
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-IN,en;q=0.9",
            "Referer": "https://www.nseindia.com/market-data/live-equity-market",
        }
        session.get("https://www.nseindia.com", headers=headers, timeout=8)
        resp = session.get("https://www.nseindia.com/api/allIndices", headers=headers, timeout=8)
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("data", [])
        wanted = {
            "NIFTY 50": "NIFTY 50",
            "NIFTY BANK": "BANK NIFTY",
            "NIFTY MIDCAP SELECT": "MIDCAP NIFTY",
            "NIFTY MIDCAP 50": "MIDCAP NIFTY",
        }
        quotes = {}
        for row in items:
            index_name = str(row.get("index") or row.get("indexSymbol") or "").upper().strip()
            display_name = wanted.get(index_name)
            if not display_name:
                continue
            price = _market_quote_float(row.get("last") or row.get("lastPrice"))
            prev_close = _market_quote_float(row.get("previousClose") or row.get("previousClosePrice"))
            change_pct = row.get("percentChange")
            if price is None:
                continue
            if change_pct is None and prev_close:
                change_pct = calculate_market_change_pct(price, prev_close)
            quotes[display_name] = {
                "price": price,
                "previous_close": prev_close,
                "change_pct": round(float(change_pct), 2) if change_pct is not None else None,
            }
        return quotes
    except Exception as e:
        print(f"   [IDX] NSE index snapshot failed: {e}")
        return {}


# In-memory cache for index data (60-second TTL)
_INDEX_CACHE = []
_INDEX_CACHE_TIME = 0

_STOCK_MARKET_CHANGE_CACHE = {}


def _market_quote_float(value):
    try:
        number = float(value)
        return number if number > 0 else None
    except Exception:
        return None


def calculate_market_change_pct(current_value, previous_close):
    current = _market_quote_float(current_value)
    previous = _market_quote_float(previous_close)
    if current is None or previous is None:
        return 0.0
    return round(((current - previous) / previous) * 100, 2)


def _is_nse_trading_day(day):
    return day.weekday() < 5 and not is_market_holiday(day.month, day.day, day.year)  # Bug #12 fix


def _previous_nse_trading_day(day):
    candidate = day - timedelta(days=1)
    while not _is_nse_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def _expected_index_close_date(now_ist, market_open):
    """
    Latest completed NSE session for index close display.
    After midnight and before market open, this is still the previous trading day.
    During market hours it is today.
    """
    today = now_ist.date()
    minutes = now_ist.hour * 60 + now_ist.minute
    if market_open:
        return today
    if _is_nse_trading_day(today) and minutes >= (15 * 60 + 30):
        return today
    return _previous_nse_trading_day(today)


def _derive_previous_close(closes, current_value, market_open, latest_close_is_current_session=None):
    clean_closes = []
    for close in closes or []:
        close_value = _market_quote_float(close)
        if close_value is not None:
            clean_closes.append(close_value)

    if not clean_closes:
        return None
    if len(clean_closes) == 1:
        return clean_closes[0] if market_open else None

    current = _market_quote_float(current_value)
    latest_close = clean_closes[-1]

    if market_open and latest_close_is_current_session is False:
        return latest_close
    if latest_close_is_current_session is True:
        return clean_closes[-2]

    # During live sessions some feeds expose yesterday's close as the final
    # daily candle while regularMarketPrice carries the live value.
    if market_open and current is not None:
        tolerance = max(0.01, current * 0.0001)
        if abs(latest_close - current) > tolerance:
            return latest_close

    return clean_closes[-2]


def _index_result_has_quotes(items):
    return bool(items) and all(item.get("price") is not None for item in items)

@app.route('/api/indices', methods=['GET'])
@route_cache(ttl_seconds=25)
def get_indices():
    global _INDEX_CACHE, _INDEX_CACHE_TIME
    
    market_open = is_market_open()
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    weekday = now_ist.weekday()
    hour, minute = now_ist.hour, now_ist.minute

    if market_open:
        price_label = "Live"
        market_status = "Market Open"
    else:
        price_label = "Last Close"
        if weekday >= 5:
            market_status = "Market Closed · Opens Mon 9:15 AM IST"
        elif hour < 9 or (hour == 9 and minute < 15):
            market_status = "Market Closed · Opens at 9:15 AM IST"
        else:
            market_status = "Market Closed · Closed at 3:30 PM IST"

    # Return cached data if fresh (60s during market hours, 5min when closed)
    cache_ttl = 60 if market_open else 300
    if _INDEX_CACHE and (time.time() - _INDEX_CACHE_TIME) < cache_ttl:
        for item in _INDEX_CACHE:
            item['is_live'] = market_open
            item['price_label'] = price_label
            item['market_status'] = market_status
        return jsonify(_INDEX_CACHE)

    indices = [
        {"symbol": "^NSEI",    "name": "NIFTY 50"},
        {"symbol": "^BSESN",   "name": "SENSEX"},
        {"symbol": "^NSEBANK", "name": "BANK NIFTY"},
        {"symbol": "^NSMIDCP", "name": "MIDCAP NIFTY"},
    ]
    nse_quotes = _fetch_nse_index_quotes()
    result = []
    for idx in indices:
        last_price = None
        prev_close = None
        change_pct = None

        nse_quote = nse_quotes.get(idx["name"])
        if nse_quote:
            last_price = nse_quote.get("price")
            prev_close = nse_quote.get("previous_close")
            change_pct = nse_quote.get("change_pct")
            print(f"   [IDX] {idx['name']}: ₹{last_price:.2f} (NSE official ✓)")

        # ── PRIMARY: Yahoo Finance Chart API (most reliable for prev_close) ──
        try:
            if nse_quote:
                raise RuntimeError("NSE quote already resolved")
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{idx['symbol']}?range=5d&interval=1d"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            resp = requests.get(url, headers=headers, timeout=8)
            data = resp.json()
            chart_result = data.get('chart', {}).get('result', [{}])[0]
            meta = chart_result.get('meta', {})

            # regularMarketPrice = current live/last price.
            _lp = meta.get('regularMarketPrice')

            quotes = chart_result.get('indicators', {}).get('quote', [{}])[0]
            timestamps = chart_result.get('timestamp', [])
            close_pairs = []
            for ts, close in zip(timestamps, quotes.get('close', [])):
                close_value = _market_quote_float(close)
                if close_value is not None:
                    close_pairs.append((ts, close_value))
            closes = [close for _, close in close_pairs]
            latest_close_is_current_session = None
            if close_pairs:
                try:
                    latest_close_date = datetime.fromtimestamp(
                        close_pairs[-1][0], tz=timezone.utc
                    ).astimezone(ist).date()
                    latest_close_is_current_session = latest_close_date == now_ist.date()
                except Exception:
                    latest_close_is_current_session = None

            if not market_open and closes:
                _lp = closes[-1]
            elif not _lp or _lp <= 0:
                if closes:
                    _lp = closes[-1]

            _pc = _derive_previous_close(
                closes, _lp, market_open, latest_close_is_current_session
            )
            if not _pc:
                _pc = (
                    meta.get('previousClose')
                    or meta.get('regularMarketPreviousClose')
                    or meta.get('chartPreviousClose')
                )

            if _lp and _lp > 0:
                last_price = float(_lp)
                print(f"   [IDX] {idx['name']}: ₹{last_price:.2f} (Yahoo Chart ✓)")
            if _pc and _pc > 0:
                prev_close = float(_pc)
        except Exception as e:
            if not nse_quote:
                print(f"   [IDX] Yahoo Chart failed for {idx['name']}: {e}")

        # ── FALLBACK: TwelveData shim ──
        if last_price is None or last_price <= 0 or prev_close is None or prev_close <= 0:
            try:
                t = yf.Ticker(idx["symbol"])
                try:
                    fi = t.fast_info
                    _lp_shim = fi.last_price
                    _pc_shim = fi.previous_close
                    if _lp_shim and float(_lp_shim) > 0:
                        last_price = float(_lp_shim)
                    if _pc_shim and float(_pc_shim) > 0 and not prev_close:
                        prev_close = float(_pc_shim)
                except Exception:
                    pass

                if last_price is None or last_price <= 0:
                    hist = t.history(period='5d', interval='1d')
                    if len(hist) >= 2:
                        last_price = last_price or float(hist['Close'].iloc[-1])
                        if not prev_close:
                            prev_close = float(hist['Close'].iloc[-2])
                    elif len(hist) == 1:
                        last_price = last_price or float(hist['Close'].iloc[-1])
            except Exception as e:
                print(f"   [IDX] TwelveData fallback failed for {idx['name']}: {e}")

        # ── Compute % change ──
        if last_price is None or last_price <= 0 or prev_close is None or prev_close <= 0:
            fallback_price, fallback_prev = _fetch_index_from_yahoo_chart(
                idx["symbol"], market_open=market_open, now_ist=now_ist
            )
            if fallback_price and fallback_price > 0:
                last_price = fallback_price
            if fallback_prev and fallback_prev > 0:
                prev_close = fallback_prev

        display_price = last_price
        has_index_quote = bool(last_price and last_price > 0 and prev_close and prev_close > 0)
        if change_pct is None:
            change_pct = calculate_market_change_pct(last_price, prev_close) if has_index_quote else None

        # When market closed: show the last available price (which IS the day's close)
        if not market_open and not display_price:
            display_price = prev_close

        display_price = round(display_price, 2) if display_price else None
        result.append({
            "name": idx["name"],
            "price": display_price,
            "last_price": display_price,
            "change_pct": change_pct,
            "is_live": market_open,
            "price_label": price_label,
            "market_status": market_status
        })
    
    # Only update the cache when at least one index has a valid price.
    # This prevents a transient NSE/Yahoo failure from wiping out good
    # cached data and showing blank Nifty values on the frontend.
    has_valid_price = any(item.get("price") is not None for item in result)
    if result and has_valid_price:
        _INDEX_CACHE = result
        _INDEX_CACHE_TIME = time.time()
    elif _INDEX_CACHE:
        # Return the last good cache rather than an empty/null result
        for item in _INDEX_CACHE:
            item['is_live'] = market_open
            item['price_label'] = price_label
            item['market_status'] = market_status
        return jsonify(_INDEX_CACHE)
    return jsonify(result)

# Debug route removed for security

def attach_market_change_percentages(stocks, market_open=None, quote_cache=None):
    if market_open is None:
        market_open = is_market_open()
    if quote_cache is None:
        quote_cache = {}

    for stock in stocks:
        if stock.get('_skip_market_quote'):
            stock['previous_close'] = stock.get('current_price')
            stock['market_change_pct'] = stock.get('market_change_pct', 0.0)
            stock.pop('_skip_market_quote', None)
            continue
        ticker = normalize_ticker(stock.get('ticker')) or stock.get('ticker')
        if not ticker:
            stock['market_change_pct'] = None
            continue
        try:
            if ticker not in quote_cache:
                quote_cache[ticker] = get_stock_market_change_quote(ticker, market_open=market_open)
            quote = quote_cache.get(ticker) or {}
            price = _positive_float(quote.get("price"))
            previous_close = _positive_float(quote.get("previous_close"))
            stock['previous_close'] = previous_close
            stock['market_change_pct'] = quote.get("change_pct")
            # Preserve the signal's stored current_price when one already exists.
            # Only fill in a missing price from the market quote.
            if price and not stock.get('current_price'):
                stock['current_price'] = price
        except Exception:
            stock['market_change_pct'] = None
    return stocks

@app.route('/api/news/top', methods=['GET'])
def get_top_news():
    try:
        conn = connect_news_db()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c2 = conn.cursor()  # separate cursor for inner queries
        c.execute("SELECT * FROM news ORDER BY created_at DESC LIMIT 1")
        news_row = c.fetchone()

        if not news_row:
            conn.close()
            return jsonify({"market_open": is_market_open(), "news": [{
                "headline": "AI Engine is analyzing LiveMint, ET, and MoneyControl...",
                "news_time": "System Processing",
                "aam_janta_translation": "The background engine is downloading and filtering live market data. Please wait.",
                "macro_pathway": ["Scrape", "Filter", "Analyze", "Deploy"],
                "affected_stocks": []
            }]})

        news_item = dict(news_row)
        try:
            news_item['macro_pathway'] = json.loads(news_item['macro_pathway'])
        except (ValueError, TypeError):  # json.loads() only raises these; bare except swallows KeyboardInterrupt
            news_item['macro_pathway'] = []

        c2.execute("SELECT * FROM stock_impact WHERE news_id = ?", (news_item['id'],))
        raw_stocks = [dict(s) for s in c2.fetchall()]
        # Deduplicate by ticker — keep highest confidence score for each ticker
        seen_tickers = {}
        for s in raw_stocks:
            t = s.get('ticker', '')
            if t not in seen_tickers or (s.get('confidence_score') or 0) > (seen_tickers[t].get('confidence_score') or 0):
                seen_tickers[t] = s
        stocks = list(seen_tickers.values())
        for s in stocks:
            bp     = s.get('base_price') or 0
            cp     = s.get('current_price') or 0
            status = s.get('status', '')
            if not is_market_open() and status == 'Active View' and not has_market_traded_since(news_item.get('news_time') or news_item.get('created_at')):
                s['current_price'] = bp
                s['diff_pct'] = 0.0
                s['market_change_pct'] = 0.0
                s['_skip_market_quote'] = True
                cp = bp
            resolved = status in ('Stop Loss Hit', 'Predicted Target Hit', 'Reacted Against Prediction')
            if resolved and s.get('estimated_change_percent') is not None:
                s['diff_pct'] = round(float(s['estimated_change_percent']), 2)
            elif bp > 0 and cp > 0:
                s['diff_pct'] = round((cp - bp) / bp * 100, 2)
            else:
                s['diff_pct'] = None
        mkt_open = is_market_open()
        attach_market_change_percentages(stocks, market_open=mkt_open)
        news_item['affected_stocks'] = stocks
        conn.close()
        return jsonify({"market_open": mkt_open, "news": [news_item]})

    except Exception as e:
        print("Error fetching top news", e)
        return jsonify({"market_open": is_market_open(), "news": []})


@app.route('/api/news/<int:news_id>/explain', methods=['GET', 'POST'])
def get_news_explanation(news_id):
    """
    On-demand explanation generator — currently PAUSED to save Gemini keys.

    The frontend no longer calls this, and the endpoint itself short-circuits
    with a paused message so any stray caller burns zero Gemini quota. Remove
    the early-return below to re-enable.
    """
    return jsonify({
        "id": news_id,
        "aam_janta_translation": "Paused for saving keys for now",
        "macro_pathway": [
            "Paused for saving keys for now",
            "Paused for saving keys for now",
            "Paused for saving keys for now",
            "Paused for saving keys for now",
        ],
        "cached": False,
        "paused": True,
    })
    # ── Original AI-backed implementation (kept for easy re-enable) ──
    # Behaviour: cache-hit returns existing row; cache-miss calls Gemini once,
    # UPDATEs the row, returns result. Restored by deleting the early-return
    # above.
    try:
        conn = connect_news_db()
        c = conn.cursor()
        c.execute("SELECT id, headline, aam_janta_translation, macro_pathway FROM news WHERE id = ? LIMIT 1", (news_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "News not found", "id": news_id}), 404

        _, headline, existing_translation, existing_pathway = row
        # Cache hit — return what we already have.
        if existing_translation:
            try:
                _pw = json.loads(existing_pathway or '[]')
                if not isinstance(_pw, list):
                    _pw = []
            except Exception:
                _pw = []
            return jsonify({
                "id": news_id,
                "aam_janta_translation": existing_translation,
                "macro_pathway": _pw,
                "cached": True,
            })

        # Cache miss — call Gemini once for this single headline.
        available = _available_gemini_key_indices()
        if not available:
            return jsonify({
                "id": news_id,
                "aam_janta_translation": None,
                "macro_pathway": [],
                "cached": False,
                "error": "AI keys all on cooldown — try again in a few minutes.",
            }), 503

        prompt = f"""You are a financial journalist writing for everyday Indians.
For the headline below, return STRICT valid JSON with two fields:
  • aam_janta_translation: a 2-sentence plain-English explanation of what
    this means for common people.
  • macro_pathway: a 4-step causal chain ["Trigger Event", "Direct Impact",
    "Ripple Effect", "End Result"].

Headline: {headline}

Return ONLY:
{{
  "aam_janta_translation": "Simple 2-sentence explanation for common people.",
  "macro_pathway": ["Trigger Event", "Direct Impact", "Ripple Effect", "End Result"]
}}"""

        raw_text = None
        try_order = list(available)
        import random as _rnd
        _rnd.shuffle(try_order)
        for _key_idx in try_order:
            try:
                _set_active_gemini_client(_key_idx)
                resp = client.models.generate_content(
                    model=MODEL_NAME,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                raw_text = resp.text
                break
            except Exception as _e:
                if _is_gemini_quota_error(_e):
                    _mark_gemini_key_quota_hit(_key_idx)
                    continue
                # transient — try another key
                continue

        if not raw_text:
            return jsonify({
                "id": news_id,
                "aam_janta_translation": None,
                "macro_pathway": [],
                "cached": False,
                "error": "AI generation failed — all keys errored or returned nothing.",
            }), 503

        try:
            data = clean_json(raw_text)
            if isinstance(data, list) and data:
                data = data[0]
        except Exception:
            data = {}

        translation = (data.get("aam_janta_translation") or "").strip()
        pathway = data.get("macro_pathway") or []
        if not isinstance(pathway, list):
            pathway = []
        if not translation:
            return jsonify({
                "id": news_id,
                "aam_janta_translation": None,
                "macro_pathway": [],
                "cached": False,
                "error": "AI returned no translation.",
            }), 503

        # Persist so subsequent clicks are instant.
        _nid = news_id
        _tr  = translation
        _pw  = json.dumps(pathway)
        def _save_explanation(conn, c, _nid=_nid, _tr=_tr, _pw=_pw):
            c.execute(
                "UPDATE news SET aam_janta_translation = ?, macro_pathway = ? WHERE id = ?",
                (_tr, _pw, _nid)
            )
        db_write(_save_explanation)

        return jsonify({
            "id": news_id,
            "aam_janta_translation": translation,
            "macro_pathway": pathway,
            "cached": False,
        })
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc(),
        }), 500


@app.route('/api/calendar', methods=['GET'])
def get_calendar():
    """
    Forward-looking macro event schedule. By default returns the next 14
    days (covering "this week" + "next week"). Past events still appear
    if within `past_days` (default 1).

    Response: events grouped per day, plus a flat list.
    """
    try:
        days_ahead = min(int(request.args.get('days', 14)), 30)
        past_days  = min(int(request.args.get('past', 1)),   7)
    except Exception:
        days_ahead, past_days = 14, 1
    try:
        today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
        start = (today - timedelta(days=past_days)).strftime('%Y-%m-%d')
        end   = (today + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
        conn = connect_news_db()
        c = conn.cursor()
        c.execute("""
            SELECT id, event_date, event_time_ist, title, country, category,
                   importance, description, prior_value, consensus_estimate,
                   actual_value, scenarios_json, historical_analogues_json,
                   related_sectors_json, related_tickers_json, status,
                   created_at, updated_at
            FROM economic_calendar
            WHERE event_date BETWEEN ? AND ?
            ORDER BY event_date ASC, event_time_ist ASC, importance DESC
        """, (start, end))
        cols = [d[0] for d in c.cursor.description]
        rows = [dict(zip(cols, r)) for r in c.fetchall()]
        conn.close()
        # Parse JSON fields
        for r in rows:
            for k in ('scenarios_json', 'historical_analogues_json',
                      'related_sectors_json', 'related_tickers_json'):
                raw = r.pop(k, None)
                clean_key = k.replace('_json', '')
                try:
                    r[clean_key] = json.loads(raw) if raw else None
                except Exception:
                    r[clean_key] = None
        # Group by day
        by_day = {}
        for r in rows:
            by_day.setdefault(r['event_date'], []).append(r)
        return jsonify({
            "today_ist": today.strftime('%Y-%m-%d'),
            "events": rows,
            "by_day": by_day,
            "count": len(rows),
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route('/api/calendar/<int:event_id>', methods=['GET'])
def get_calendar_event(event_id):
    """Single-event detail (used when user clicks a card)."""
    try:
        conn = connect_news_db()
        c = conn.cursor()
        c.execute("""SELECT id, event_date, event_time_ist, title, country, category,
                            importance, description, prior_value, consensus_estimate,
                            actual_value, scenarios_json, historical_analogues_json,
                            related_sectors_json, related_tickers_json, status,
                            created_at, updated_at
                     FROM economic_calendar WHERE id = ? LIMIT 1""", (event_id,))
        cols = [d[0] for d in c.description]
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Event not found"}), 404
        r = dict(zip(cols, row))
        for k in ('scenarios_json', 'historical_analogues_json',
                  'related_sectors_json', 'related_tickers_json'):
            raw = r.pop(k, None)
            clean_key = k.replace('_json', '')
            try:
                r[clean_key] = json.loads(raw) if raw else None
            except Exception:
                r[clean_key] = None
        return jsonify(r)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route('/api/admin/calendar/upsert', methods=['POST'])
def admin_calendar_upsert():
    """
    Weekly maintenance endpoint — replace next-week's events with new payload.
    Auth: X-Alpha-Lens-Token: <SQL_RUNNER_SECRET>
    Body: { "events": [ { event_date, event_time_ist, title, country, category,
                          importance, description, prior_value, consensus_estimate,
                          scenarios, historical_analogues, related_sectors,
                          related_tickers }, ... ],
            "replace_window_days": 7   # delete-then-insert window (default 7)
          }
    """
    secret = os.environ.get("SQL_RUNNER_SECRET")
    token = request.headers.get("X-Alpha-Lens-Token") or request.args.get("token")
    if not secret or token != secret:
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    events = body.get("events") or []
    if not events:
        return jsonify({"error": "No events provided in payload"}), 400
    replace_days = int(body.get("replace_window_days", 7))
    try:
        # If a window is specified, delete existing rows in that window first
        # so the user can re-seed cleanly.
        today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
        end = (today + timedelta(days=replace_days)).strftime('%Y-%m-%d')
        start = today.strftime('%Y-%m-%d')
        def _wipe(conn, c, _s=start, _e=end):
            c.execute("DELETE FROM economic_calendar WHERE event_date BETWEEN ? AND ?",
                      (_s, _e))
        db_write(_wipe)

        inserted = 0
        for ev in events:
            scenarios_json = json.dumps(ev.get("scenarios") or {})
            analogues_json = json.dumps(ev.get("historical_analogues") or [])
            sectors_json   = json.dumps(ev.get("related_sectors") or [])
            tickers_json   = json.dumps(ev.get("related_tickers") or [])
            _ev = ev
            def _ins(conn, c, _e=_ev, _sj=scenarios_json, _aj=analogues_json,
                     _secj=sectors_json, _tj=tickers_json):
                c.execute("""
                    INSERT OR REPLACE INTO economic_calendar
                      (event_date, event_time_ist, title, country, category, importance,
                       description, prior_value, consensus_estimate,
                       scenarios_json, historical_analogues_json,
                       related_sectors_json, related_tickers_json,
                       status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    _e["event_date"], _e.get("event_time_ist", ""), _e["title"],
                    _e.get("country", ""), _e.get("category", ""), _e.get("importance", "MEDIUM"),
                    _e.get("description", ""), _e.get("prior_value", ""), _e.get("consensus_estimate", ""),
                    _sj, _aj, _secj, _tj, ev.get("status", "upcoming"),
                ))
            db_write(_ins)
            inserted += 1
        return jsonify({"status": "success", "inserted": inserted,
                        "wiped_window": f"{start} → {end}"})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


def compute_macro_effects(key, pct):
    if not key or pct is None:
        return []
    try:
        pct = float(pct)
    except ValueError:
        return []
    is_up = pct > 0
    k = key.lower()
    
    effects = []
    
    if k in ('brent', 'wti'):
        if is_up:
            effects = [
                {"ticker": "ONGC.NS", "name": "ONGC", "direction": "BULLISH", "expected_move_pct": 2.5, "reason": "Direct Upstream margin expansion"},
                {"ticker": "OIL.NS", "name": "Oil India", "direction": "BULLISH", "expected_move_pct": 2.8, "reason": "Direct Upstream margin expansion"},
                {"ticker": "INDIGO.NS", "name": "InterGlobe Aviation", "direction": "BEARISH", "expected_move_pct": -3.5, "reason": "Aviation fuel cost inflation"},
                {"ticker": "ASIANPAINT.NS", "name": "Asian Paints", "direction": "BEARISH", "expected_move_pct": -2.2, "reason": "Crude derivative input cost inflation"}
            ]
        else:
            effects = [
                {"ticker": "BPCL.NS", "name": "BPCL", "direction": "BULLISH", "expected_move_pct": 3.2, "reason": "Marketing margin expansion"},
                {"ticker": "INDIGO.NS", "name": "InterGlobe Aviation", "direction": "BULLISH", "expected_move_pct": 3.8, "reason": "Lower ATF fuel expense"},
                {"ticker": "ASIANPAINT.NS", "name": "Asian Paints", "direction": "BULLISH", "expected_move_pct": 2.2, "reason": "Raw material input cost relief"},
                {"ticker": "ONGC.NS", "name": "ONGC", "direction": "BEARISH", "expected_move_pct": -2.2, "reason": "Realization prices compression"}
            ]
    elif k in ('gold', 'silver'):
        if is_up:
            effects = [
                {"ticker": "TITAN.NS", "name": "Titan Company", "direction": "BULLISH", "expected_move_pct": 1.8, "reason": "Inventory gains on jewellery stock"},
                {"ticker": "MUTHOOTFIN.NS", "name": "Muthoot Finance", "direction": "BULLISH", "expected_move_pct": 2.0, "reason": "Higher LTV collateral value"},
                {"ticker": "KALYANKJIL.NS", "name": "Kalyan Jewellers", "direction": "BULLISH", "expected_move_pct": 2.5, "reason": "Jewellery inventory value gains"}
            ]
        else:
            effects = [
                {"ticker": "TITAN.NS", "name": "Titan Company", "direction": "BEARISH", "expected_move_pct": -1.5, "reason": "Inventory valuation markdowns"},
                {"ticker": "MUTHOOTFIN.NS", "name": "Muthoot Finance", "direction": "BEARISH", "expected_move_pct": -1.8, "reason": "Risk of collateral value drop"}
            ]
    elif k == 'natgas':
        if is_up:
            effects = [
                {"ticker": "ONGC.NS", "name": "ONGC", "direction": "BULLISH", "expected_move_pct": 1.5, "reason": "Realized price increase on gas sales"},
                {"ticker": "IGL.NS", "name": "Indraprastha Gas", "direction": "BEARISH", "expected_move_pct": -2.0, "reason": "Compressed margins on sourcing"},
                {"ticker": "MGL.NS", "name": "Mahanagar Gas", "direction": "BEARISH", "expected_move_pct": -2.2, "reason": "High input LNG costs"}
            ]
        else:
            effects = [
                {"ticker": "IGL.NS", "name": "Indraprastha Gas", "direction": "BULLISH", "expected_move_pct": 2.5, "reason": "Sourcing gas cost relief"},
                {"ticker": "MGL.NS", "name": "Mahanagar Gas", "direction": "BULLISH", "expected_move_pct": 2.8, "reason": "Margins expansion"}
            ]
    elif k in ('dxy', 'usdinr'):
        if is_up:
            effects = [
                {"ticker": "INFY.NS", "name": "Infosys", "direction": "BULLISH", "expected_move_pct": 1.8, "reason": "Export revenue translation gains"},
                {"ticker": "TCS.NS", "name": "TCS", "direction": "BULLISH", "expected_move_pct": 1.5, "reason": "Stronger USD billing translations"},
                {"ticker": "SUNPHARMA.NS", "name": "Sun Pharma", "direction": "BULLISH", "expected_move_pct": 1.2, "reason": "US generic revenue benefit"},
                {"ticker": "BPCL.NS", "name": "BPCL", "direction": "BEARISH", "expected_move_pct": -1.5, "reason": "Rising crude import cost in INR"}
            ]
        else:
            effects = [
                {"ticker": "TCS.NS", "name": "TCS", "direction": "BEARISH", "expected_move_pct": -1.2, "reason": "Translation margin compression"},
                {"ticker": "L&T.NS", "name": "L&T", "direction": "BULLISH", "expected_move_pct": 1.2, "reason": "Domestic infrastructure cycle boost"},
                {"ticker": "ICICIBANK.NS", "name": "ICICI Bank", "direction": "BULLISH", "expected_move_pct": 1.0, "reason": "Rupee strength improves capital flows"}
            ]
    elif k in ('vix_us', 'vix_in'):
        if is_up:
            effects = [
                {"ticker": "ITC.NS", "name": "ITC Limited", "direction": "BULLISH", "expected_move_pct": 1.2, "reason": "Defensive low-beta rotation"},
                {"ticker": "TCS.NS", "name": "TCS", "direction": "BULLISH", "expected_move_pct": 0.8, "reason": "Defensive earnings profile"},
                {"ticker": "HDFCBANK.NS", "name": "HDFC Bank", "direction": "BEARISH", "expected_move_pct": -1.8, "reason": "Risk-off equity liquidation"},
                {"ticker": "ICICIBANK.NS", "name": "ICICI Bank", "direction": "BEARISH", "expected_move_pct": -2.0, "reason": "FII volatility outflow risk"}
            ]
        else:
            effects = [
                {"ticker": "ICICIBANK.NS", "name": "ICICI Bank", "direction": "BULLISH", "expected_move_pct": 1.5, "reason": "Risk-on credit buying"},
                {"ticker": "DLF.NS", "name": "DLF Limited", "direction": "BULLISH", "expected_move_pct": 2.2, "reason": "High beta recovery rally"}
            ]
    elif k in ('nifty', 'banknifty'):
        if is_up:
            effects = [
                {"ticker": "HDFCBANK.NS", "name": "HDFC Bank", "direction": "BULLISH", "expected_move_pct": 1.5, "reason": "Heavyweight index tracker"},
                {"ticker": "ICICIBANK.NS", "name": "ICICI Bank", "direction": "BULLISH", "expected_move_pct": 1.8, "reason": "Momentum credit buying"},
                {"ticker": "RELIANCE.NS", "name": "Reliance Industries", "direction": "BULLISH", "expected_move_pct": 1.2, "reason": "Index-weight matching rally"}
            ]
        else:
            effects = [
                {"ticker": "HDFCBANK.NS", "name": "HDFC Bank", "direction": "BEARISH", "expected_move_pct": -1.5, "reason": "Index heavyweight drag"},
                {"ticker": "ICICIBANK.NS", "name": "ICICI Bank", "direction": "BEARISH", "expected_move_pct": -1.8, "reason": "Financial sector selloff"}
            ]
    elif k == 'us10y':
        if is_up:
            effects = [
                {"ticker": "TCS.NS", "name": "TCS", "direction": "BEARISH", "expected_move_pct": -1.5, "reason": "DCF discount rate re-rating pressure"},
                {"ticker": "DLF.NS", "name": "DLF Limited", "direction": "BEARISH", "expected_move_pct": -2.0, "reason": "High interest cost real estate pressure"},
                {"ticker": "L&T.NS", "name": "L&T", "direction": "BEARISH", "expected_move_pct": -1.2, "reason": "Leveraged infra capex headwind"}
            ]
        else:
            effects = [
                {"ticker": "TCS.NS", "name": "TCS", "direction": "BULLISH", "expected_move_pct": 1.2, "reason": "DCF multiple re-rating support"},
                {"ticker": "DLF.NS", "name": "DLF Limited", "direction": "BULLISH", "expected_move_pct": 1.8, "reason": "Borrowing cost relief expectation"},
                {"ticker": "HDFCBANK.NS", "name": "HDFC Bank", "direction": "BULLISH", "expected_move_pct": 1.4, "reason": "Yield decline supportive of credit growth"}
            ]
    elif k == 'copper':
        if is_up:
            effects = [
                {"ticker": "HINDALCO.NS", "name": "Hindalco", "direction": "BULLISH", "expected_move_pct": 2.0, "reason": "Base metal LME price tailwind"},
                {"ticker": "VEDL.NS", "name": "Vedanta", "direction": "BULLISH", "expected_move_pct": 2.2, "reason": "Base metal LME price tailwind"},
                {"ticker": "HAVELLS.NS", "name": "Havells", "direction": "BEARISH", "expected_move_pct": -1.5, "reason": "Rising input wire rod costs"}
            ]
        else:
            effects = [
                {"ticker": "HINDALCO.NS", "name": "Hindalco", "direction": "BEARISH", "expected_move_pct": -1.8, "reason": "Metal realization price drop"},
                {"ticker": "HAVELLS.NS", "name": "Havells", "direction": "BULLISH", "expected_move_pct": 1.2, "reason": "Input wiring cost relief"}
            ]

    scale_mult = 1.0
    if k in ('brent', 'wti', 'natgas', 'silver', 'copper'):
        scale_mult = min(max(abs(pct) / 3.0, 0.5), 2.5)
    elif k in ('gold',):
        scale_mult = min(max(abs(pct) / 1.5, 0.5), 2.5)
    elif k in ('dxy', 'usdinr'):
        scale_mult = min(max(abs(pct) / 0.8, 0.5), 2.5)
    elif k in ('vix_us', 'vix_in'):
        scale_mult = min(max(abs(pct) / 10.0, 0.5), 2.5)
    elif k in ('nifty', 'banknifty'):
        scale_mult = min(max(abs(pct) / 1.5, 0.5), 2.5)
    elif k == 'us10y':
        scale_mult = min(max(abs(pct) / 5.0, 0.5), 2.5)

    scaled_effects = []
    for eff in effects:
        item = dict(eff)
        base_move = item["expected_move_pct"]
        item["expected_move_pct"] = round(base_move * scale_mult, 2)
        scaled_effects.append(item)
    return scaled_effects


@app.route('/api/macro/events', methods=['GET'])
def list_macro_events():
    """
    Active macro shocks detected by MacroDataTracker in the last
    `expires_at` window. Frontend uses this to render the "Macro Pulse" strip.
    """
    try:
        conn = connect_news_db()
        c = conn.cursor()
        c.execute("""
            SELECT id, instrument_key, instrument_label, symbol, shock_level,
                   change_pct_1d, last_price, prev_close, detected_at, expires_at,
                   during_nse_hours,
                   (CASE WHEN ripple_json IS NOT NULL AND ripple_json != '' THEN 1 ELSE 0 END) AS has_ripple
            FROM macro_event
            WHERE expires_at >= ?
            ORDER BY detected_at DESC
            LIMIT 50
        """, (datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),))
        cols = [d[0] for d in c.description]
        rows = [dict(zip(cols, r)) for r in c.fetchall()]
        conn.close()

        # Also include the LIVE snapshot so the UI can show all instruments
        # (not just shocked ones) with their current 1d move — useful as a
        # mini-dashboard.
        try:
            snap = list(MacroDataTracker.get_snapshot().values())
        except Exception:
            snap = []

        seen_keys = set()
        deduped_rows = []
        for r in rows:
            key = r.get('instrument_key')
            if key not in seen_keys:
                seen_keys.add(key)
                deduped_rows.append(r)

        # Enrich with systemic causal effects
        for r in deduped_rows:
            r['effects'] = compute_macro_effects(r.get('instrument_key'), r.get('change_pct_1d'))

        for s in snap:
            s['effects'] = compute_macro_effects(s.get('key'), s.get('change_pct_1d'))

        return jsonify({"events": deduped_rows, "snapshot": snap})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route('/api/macro/events/<int:event_id>/ripple', methods=['GET'])
def get_macro_event_ripple(event_id):
    """
    Return the ripple graph for a specific macro event. Generates one
    inline if it was missing (e.g., previous attempt failed due to quota).
    """
    try:
        conn = connect_news_db()
        c = conn.cursor()
        c.execute("""
            SELECT id, instrument_key, instrument_label, symbol, shock_level,
                   change_pct_1d, last_price, prev_close, ripple_json, detected_at
            FROM macro_event WHERE id = ?
        """, (event_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Macro event not found"}), 404

        (mid, key, label, symbol, shock_level,
         pct, last, prev, ripple_json, detected_at) = row

        if ripple_json:
            try:
                graph = json.loads(ripple_json)
            except Exception:
                graph = None
            if graph:
                return jsonify({
                    "event_id": mid,
                    "instrument": label,
                    "symbol": symbol,
                    "shock_level": shock_level,
                    "change_pct_1d": pct,
                    "last_price": last,
                    "prev_close": prev,
                    "detected_at": str(detected_at),
                    "summary": graph.get("summary", ""),
                    "tiers": graph.get("tiers", []),
                    "trigger": graph.get("trigger", {}),
                    "cached": True,
                })

        # No JSON yet — regenerate inline.
        inst = {
            'key': key, 'label': label, 'symbol': symbol,
            'change_pct_1d': pct, 'last': last, 'prev_close': prev,
            'shock_level': shock_level,
        }
        graph = generate_macro_ripple_graph(inst)
        if not graph:
            return jsonify({"error": "Ripple generation failed."}), 503
        payload = json.dumps(graph)
        def _u(conn, c, _id=mid, _p=payload):
            c.execute("UPDATE macro_event SET ripple_json = ? WHERE id = ?", (_p, _id))
        db_write(_u)
        return jsonify({
            "event_id": mid,
            "instrument": label,
            "symbol": symbol,
            "shock_level": shock_level,
            "change_pct_1d": pct,
            "last_price": last,
            "prev_close": prev,
            "detected_at": str(detected_at),
            "summary": graph.get("summary", ""),
            "tiers": graph.get("tiers", []),
            "trigger": graph.get("trigger", {}),
            "cached": False,
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route('/api/news/<int:news_id>/ripple', methods=['GET'])
def get_news_ripple(news_id):
    """
    "The Ripple" — returns the 3-tier propagation graph for a big news event.

    Behaviour:
      • Cached graph exists → return it instantly.
      • Marked is_big_news but no JSON yet (background gen failed) →
        regenerate inline as a single Gemini call.
      • Not a big-news event → 404 (frontend should never link to it
        because the news.has_ripple flag tells it whether to show the
        "View Ripple" badge).

    Response shape:
      {
        "news_id": 123,
        "headline": "...",
        "ripple_score": 92,
        "summary": "1-sentence framing",
        "tiers": [
          {"tier": 1, "label": "Direct Impact", "nodes": [...]},
          {"tier": 2, "label": "Second-Order (Supply Chain)", "nodes": [...]},
          {"tier": 3, "label": "Macro Transmission", "nodes": [...]}
        ],
        "generated_at": "..."
      }
    """
    try:
        conn = connect_news_db()
        c = conn.cursor()
        c.execute("""
            SELECT n.id, n.headline, n.body, n.category,
                   r.ripple_score, r.is_big_news, r.ripple_json, r.generated_at
            FROM news n
            LEFT JOIN news_ripple r ON r.news_id = n.id
            WHERE n.id = ?
            LIMIT 1
        """, (news_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "News not found", "id": news_id}), 404

        _, headline, body, _cat, ripple_score, is_big, ripple_json, generated_at = row

        # If we have cached JSON, return it directly.
        if ripple_json:
            try:
                graph = json.loads(ripple_json)
            except Exception:
                graph = None
            if graph:
                return jsonify({
                    "news_id":      news_id,
                    "headline":     headline,
                    "ripple_score": ripple_score or 0,
                    "summary":      graph.get("summary", ""),
                    "tiers":        graph.get("tiers", []),
                    "generated_at": str(generated_at) if generated_at else None,
                    "cached":       True,
                })

        # No JSON yet — generate inline. (Only proceed if this was already
        # tagged big-news, OR if the user explicitly forces regeneration.)
        force = request.args.get('force', '').lower() in ('1', 'true', 'yes')
        if not is_big and not force:
            return jsonify({
                "error": "This news event isn't tagged as big-enough for a ripple graph.",
                "hint": "Pass ?force=1 to override (admin/debug).",
            }), 404

        available = _available_gemini_key_indices()
        if not available:
            return jsonify({
                "error": "All AI keys on cooldown — try again in a few minutes.",
            }), 503

        graph = generate_ripple_graph(headline, body or '', '', [])
        if not graph:
            return jsonify({"error": "Ripple generation failed."}), 503

        save_ripple_to_db(news_id, ripple_score or 75, True, graph)

        return jsonify({
            "news_id":      news_id,
            "headline":     headline,
            "ripple_score": ripple_score or 75,
            "summary":      graph.get("summary", ""),
            "tiers":        graph.get("tiers", []),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cached":       False,
        })
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc(),
        }), 500


@app.route('/api/news/all', methods=['GET'])
def get_all_news():
    """
    Returns recent (last 7 days) news with their affected stock impacts.

    Performance note: This used to do 1 + N queries (one big news query + one
    stock_impact query per news row). At limit=7500 that was 7501 round-trips
    against Render's free-tier Postgres, which routinely exceeded the 60s
    Cloudflare edge timeout. Refactored to **2 total queries** (news, then a
    single IN(...) batch for impacts) and one Python-side group-by — drops
    the dominant cost from O(N) round-trips to O(1).
    """
    try:
        # Bug #15 fix: pagination to prevent unbounded response sizes
        try:
            limit = min(int(request.args.get('limit', 50)), 7500)
            offset = max(int(request.args.get('offset', 0)), 0)
        except (ValueError, TypeError):
            limit, offset = 50, 0
        # ?lite=1 trims body to a snippet so the list response stays small.
        # The All News card grid uses lite mode (200-char snippet is plenty
        # for a preview); the main article viewer can hit /api/news/all
        # without the flag if it ever needs the full body.
        lite = request.args.get('lite', '').lower() in ('1', 'true', 'yes')
        body_max = 250 if lite else 5000

        conn = connect_news_db()
        c    = conn.cursor()
        feed_cutoff = (datetime.now(timezone.utc) - timedelta(days=NEWS_FEED_RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')

        # ── Query 1: news rows (feed shows the last NEWS_FEED_RETENTION_DAYS days) ──
        c.execute(
            "SELECT * FROM news WHERE created_at >= ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (feed_cutoff, limit, offset)
        )
        news_rows = c.fetchall()
        news_cols = [desc[0] for desc in c.cursor.description]

        if not news_rows:
            conn.close()
            return jsonify({"market_open": is_market_open(), "news": [], "limit": limit, "offset": offset})

        # Build the news_items list and collect IDs for the impact batch query
        news_items = []
        news_ids   = []
        for raw_row in news_rows:
            ni = dict(zip(news_cols, raw_row))
            try:
                ni['macro_pathway'] = json.loads(ni.get('macro_pathway') or '[]')
            except (ValueError, TypeError):  # json.loads only raises these
                ni['macro_pathway'] = []
            # body trim — lite mode caps at 250 chars so the JSON payload stays
            # small (200 articles * 5KB body each = 1MB+; trimmed = ~50KB total).
            if ni.get('body'):
                _b = ni['body']
                if len(_b) > body_max:
                    ni['body'] = _b[:body_max].rstrip() + '…'
            news_items.append(ni)
            news_ids.append(ni['id'])

        # ── Query 2: ALL stock_impact rows for the news set, in ONE round-trip ──
        # SQLite + Postgres both happily handle a few thousand IN-list params;
        # the cursor wrapper translates ? → %s automatically.
        impacts_by_news_id = {}
        if news_ids:
            placeholders = ','.join(['?'] * len(news_ids))

            # ── Bonus query 2b: ripple_score for cards that have one ──
            # Tiny — only big-news rows exist here. Stitched onto news_items
            # so the UI can render "⚡ Ripple · 92" on the badge.
            try:
                c.execute(
                    f"SELECT news_id, ripple_score FROM news_ripple WHERE news_id IN ({placeholders})",
                    tuple(news_ids)
                )
                _ripple_scores = {r[0]: r[1] for r in c.fetchall()}
                for ni in news_items:
                    if ni['id'] in _ripple_scores:
                        ni['ripple_score'] = _ripple_scores[ni['id']]
            except Exception:
                pass

            c.execute(
                f"SELECT * FROM stock_impact WHERE news_id IN ({placeholders})",
                tuple(news_ids)
            )
            impact_rows = c.fetchall()
            impact_cols = [desc[0] for desc in c.cursor.description]
            for raw in impact_rows:
                row = dict(zip(impact_cols, raw))
                impacts_by_news_id.setdefault(row.get('news_id'), []).append(row)

        conn.close()

        # ── Stitch impacts back onto news items + apply the same business
        #    logic (dedup-by-ticker / diff_pct / market-closed gating) ──
        mkt_open = is_market_open()
        quote_cache = {}

        for ni in news_items:
            raw_stocks = impacts_by_news_id.get(ni['id'], [])

            # Dedup by ticker — keep the row with the highest confidence_score
            seen_tickers = {}
            for s in raw_stocks:
                t = s.get('ticker', '')
                if t not in seen_tickers or (s.get('confidence_score') or 0) > (seen_tickers[t].get('confidence_score') or 0):
                    seen_tickers[t] = s
            stocks = list(seen_tickers.values())

            for s in stocks:
                bp = s.get('base_price') or 0
                cp = s.get('current_price') or 0
                status = s.get('status', '')
                if not mkt_open and status == 'Active View' and not has_market_traded_since(
                    ni.get('news_time') or ni.get('created_at')
                ):
                    s['current_price'] = bp
                    s['diff_pct'] = 0.0
                    s['market_change_pct'] = 0.0
                    s['_skip_market_quote'] = True
                    cp = bp
                is_closed = s.get('status') in (
                    'Stop Loss Hit', 'Predicted Target Hit', 'Reacted Against Prediction', 'Expired'
                )
                if is_closed and s.get('estimated_change_percent') is not None:
                    s['diff_pct'] = round(s.get('estimated_change_percent'), 2)
                elif bp > 0 and cp > 0:
                    s['diff_pct'] = round((cp - bp) / bp * 100, 2)
                else:
                    s['diff_pct'] = None

            attach_market_change_percentages(stocks, market_open=mkt_open, quote_cache=quote_cache)
            ni['affected_stocks'] = stocks

        return jsonify({"market_open": mkt_open, "news": news_items, "limit": limit, "offset": offset})
    except Exception as e:
        print("Error fetching all news", e)
        import traceback; traceback.print_exc()
        return jsonify({"market_open": is_market_open(), "news": []})

def parse_macro_pathway_value(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []

def get_portfolio_news_context(portfolio_tickers, limit=18):
    normalized = [normalize_ticker(t) for t in portfolio_tickers]
    normalized = [t for t in normalized if t]
    portfolio_bases = {ticker_base(t) for t in normalized}
    if not portfolio_bases:
        return [], []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    conn = connect_news_db()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT
            n.id AS news_id, n.headline, n.news_time, n.created_at,
            n.aam_janta_translation, n.macro_pathway, n.category,
            si.ticker, si.impact, si.estimated_change_percent, si.view,
            si.reason, si.base_price, si.current_price, si.status,
            si.confidence_score, si.ensemble_detail
        FROM news n
        JOIN stock_impact si ON n.id = si.news_id
        WHERE n.created_at >= ?
        ORDER BY n.created_at DESC
        LIMIT 500
    """, (cutoff,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    grouped = {}
    market_open = is_market_open()
    quote_cache = {}
    for row in rows:
        if ticker_base(row.get("ticker")) not in portfolio_bases:
            continue
        news_id = row["news_id"]
        item = grouped.setdefault(news_id, {
            "id": news_id,
            "headline": row.get("headline", ""),
            "news_time": row.get("news_time", ""),
            "created_at": row.get("created_at", ""),
            "explanation": row.get("aam_janta_translation") or "",
            "macro_pathway": parse_macro_pathway_value(row.get("macro_pathway")),
            "category": row.get("category") or "General",
            "stocks": [],
        })
        bp = row.get("base_price") or 0
        cp = row.get("current_price") or 0
        signal_diff_pct = None
        try:
            if bp and cp:
                signal_diff_pct = round((float(cp) - float(bp)) / float(bp) * 100, 2)
        except Exception:
            signal_diff_pct = None
        market_change_pct = None
        try:
            ticker = normalize_ticker(row.get("ticker"))
            if ticker:
                if ticker not in quote_cache:
                    quote_cache[ticker] = get_stock_market_change_quote(ticker, market_open=market_open)
                market_change_pct = quote_cache[ticker].get("change_pct")
        except Exception:
            market_change_pct = None
        item["stocks"].append({
            "ticker": normalize_ticker(row.get("ticker")),
            "impact": row.get("impact"),
            "status": row.get("status"),
            "confidence_score": row.get("confidence_score"),
            "view": row.get("view"),
            "reason": row.get("reason"),
            # Prefer the signal’s actual base/current diff when a signal exists.
            # Only fall back to market change when there is no signal diff available.
            "diff_pct": signal_diff_pct if signal_diff_pct is not None else market_change_pct,
            "signal_diff_pct": signal_diff_pct,
            "market_change_pct": market_change_pct,
        })

    items = list(grouped.values())[:limit]
    return items, sorted(normalized)

def known_ticker_bases():
    bases = {ticker_base(t) for t in STOCK_KEYWORD_MAP.values() if ticker_base(t)}
    try:
        if getattr(yf, "_scrip_loaded", False):
            bases.update(getattr(yf, "_scrip_cache", {}).keys())
            bases.update(getattr(yf, "_bse_cache", {}).keys())
    except Exception:
        pass
    return bases

# Portfolio-assistant ticker-detection lookup tables were extracted to
# portfolio_data.py. Imported back so clean_stock_name() etc. resolve them.
from newsproc.portfolio_data import (
    COMMON_EXTERNAL_STOCK_ALIASES, GENERIC_STOCK_NAME_WORDS,
    OUT_OF_SCOPE_TOPIC_WORDS,
)

def clean_stock_name(value):
    return re.sub(r'[^a-z0-9&\-\s]+', ' ', str(value or '').lower()).strip()

def detect_external_tickers(question, portfolio_tickers):
    portfolio_bases = {ticker_base(t) for t in portfolio_tickers if ticker_base(t)}
    known_bases = known_ticker_bases()
    mentioned = set()
    for token in re.findall(r'\b[A-Z][A-Z0-9&\-]{2,15}(?:\.(?:NS|BO))?\b', question.upper()):
        base = token.split(".", 1)[0]
        if base in COMMON_UPPERCASE_WORDS or base in INDEX_LIKE_SYMBOLS:
            continue
        if base in known_bases:
            mentioned.add(base)

    q_words = re.sub(r'[^a-z0-9&\-\s]+', ' ', question.lower())
    for name, ticker in STOCK_KEYWORD_MAP.items():
        base = ticker_base(ticker)
        clean_name = re.sub(r'[^a-z0-9&\-\s]+', ' ', str(name or '').lower()).strip()
        if base and clean_name and re.search(r'\b' + re.escape(clean_name) + r'\b', q_words):
            mentioned.add(base)
    for alias, base in COMMON_EXTERNAL_STOCK_ALIASES.items():
        if re.search(r'\b' + re.escape(alias) + r'\b', q_words):
            mentioned.add(base)
    return sorted(mentioned - portfolio_bases)

def portfolio_alias_map(portfolio_tickers, portfolio_names=None):
    alias_map = {}
    normalized = []
    for ticker in portfolio_tickers:
        t = normalize_ticker(ticker)
        if t and t not in normalized:
            normalized.append(t)

    for idx, ticker in enumerate(normalized):
        base = ticker_base(ticker)
        if not base:
            continue
        aliases = alias_map.setdefault(base, set())
        aliases.add(base.lower())
        aliases.add(ticker.lower())
        if portfolio_names and idx < len(portfolio_names):
            clean_name = clean_stock_name(portfolio_names[idx])
            if clean_name:
                aliases.add(clean_name)
                for token in re.findall(r'[a-z0-9&\-]{3,}', clean_name):
                    if token not in GENERIC_STOCK_NAME_WORDS:
                        aliases.add(token)

    normalized_set = set(normalized)
    for name, ticker in STOCK_KEYWORD_MAP.items():
        normalized_ticker = normalize_ticker(ticker)
        base = ticker_base(normalized_ticker)
        if normalized_ticker in normalized_set and base:
            alias_map.setdefault(base, set()).add(str(name).lower())
    return alias_map

def mentioned_portfolio_bases(question, portfolio_tickers, portfolio_names=None):
    q = clean_stock_name(question)
    mentioned = set()
    for base, aliases in portfolio_alias_map(portfolio_tickers, portfolio_names).items():
        if any(alias and re.search(r'\b' + re.escape(alias) + r'\b', q) for alias in aliases):
            mentioned.add(base)
    return mentioned

def portfolio_aliases(portfolio_tickers, portfolio_names=None):
    aliases = set()
    for values in portfolio_alias_map(portfolio_tickers, portfolio_names).values():
        aliases.update(values)
    return aliases

def is_portfolio_news_question(question, context_items, portfolio_tickers, portfolio_names=None):
    return True

def should_try_portfolio_ai(question):
    return True

def portfolio_item_bases(item):
    return {ticker_base(s.get("ticker")) for s in item.get("stocks", []) if ticker_base(s.get("ticker"))}

def portfolio_item_timestamp(item):
    for key in ("created_at", "news_time"):
        value = item.get(key)
        if not value:
            continue
        try:
            if isinstance(value, str) and "," in value:
                return parsedate_to_datetime(value).timestamp()
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
    return 0

def item_stock_score(item, wanted_bases=None, intent="latest"):
    scores = []
    for stock in item.get("stocks", []):
        base = ticker_base(stock.get("ticker"))
        if wanted_bases and base not in wanted_bases:
            continue
        confidence = float(stock.get("confidence_score") or 0)
        move = stock.get("diff_pct")
        try:
            move = float(move) if move is not None else 0.0
        except Exception:
            move = 0.0
        impact = str(stock.get("impact") or "").lower()
        score = confidence
        if intent == "risk":
            score += 40 if "bearish" in impact else 0
            score += abs(min(move, 0)) * 10
        elif intent == "bullish":
            score += 40 if "bullish" in impact and "bearish" not in impact else 0
            score += max(move, 0) * 10
        elif intent == "move":
            score += abs(move) * 15
        scores.append(score)
    return max(scores) if scores else 0

def rank_portfolio_context_items(question, context_items, wanted_bases=None):
    q = (question or "").lower()
    if wanted_bases:
        context_items = [item for item in context_items if portfolio_item_bases(item) & wanted_bases]

    if not context_items:
        return []

    if any(term in q for term in ["risk", "worry", "concern", "bearish", "negative", "down", "loss"]):
        return sorted(context_items, key=lambda item: (item_stock_score(item, wanted_bases, "risk"), portfolio_item_timestamp(item)), reverse=True)
    if any(term in q for term in ["bullish", "positive", "up", "gain"]):
        return sorted(context_items, key=lambda item: (item_stock_score(item, wanted_bases, "bullish"), portfolio_item_timestamp(item)), reverse=True)
    if any(term in q for term in ["move", "reaction", "changed", "change", "percent"]):
        return sorted(context_items, key=lambda item: (item_stock_score(item, wanted_bases, "move"), portfolio_item_timestamp(item)), reverse=True)
    if any(term in q for term in ["latest", "recent", "newest", "today", "news", "what happened"]):
        return sorted(context_items, key=portfolio_item_timestamp, reverse=True)

    return context_items

def portfolio_question_intent(question):
    q = (question or "").lower()
    if any(term in q for term in ["bullish", "bearish", "bull/bear", "positive", "negative"]):
        return "bull_bear"
    if any(term in q for term in ["risk", "worry", "concern", "downside", "loss"]):
        return "risk"
    if any(term in q for term in ["explain", "why", "reason", "impact on my portfolio"]):
        return "explain"
    if any(term in q for term in ["latest", "recent", "newest", "today", "news", "what happened"]):
        return "latest"
    return "default"

def format_move_text(stock):
    move = stock.get("diff_pct")
    if move is None:
        return ""
    try:
        return f", move {float(move):+.2f}%"
    except Exception:
        return ""

def format_stock_line(stock):
    confidence = stock.get("confidence_score", "NA")
    return (
        f"- **{stock.get('ticker')}**: {stock.get('impact') or 'Impact pending'} "
        f"({confidence}% confidence{format_move_text(stock)})"
    )

def relevant_item_stocks(item, answer_bases):
    return [
        stock for stock in item.get("stocks", [])
        if ticker_base(stock.get("ticker")) in answer_bases
    ]

def build_latest_portfolio_answer(context_items, answer_bases):
    lines = []
    for item in context_items[:4]:
        stocks = relevant_item_stocks(item, answer_bases)
        if not stocks:
            continue
        ticker_bits = ", ".join(
            f"{s.get('ticker')} {s.get('impact') or 'pending'}"
            for s in stocks[:3]
        )
        lines.append(f"- **{ticker_bits}**: {item.get('headline')}")
    if not lines:
        return None
    return "\n\n".join([
        "**Answer**\nHere are the latest saved news items linked to your portfolio.",
        "**Latest News**\n" + "\n".join(lines),
        "**News Used**\n" + "\n".join(f"- {item.get('headline')}" for item in context_items[:3] if item.get("headline")),
    ])

def build_risk_portfolio_answer(context_items, answer_bases):
    risk_lines = []
    supportive_lines = []
    for item in context_items[:6]:
        for stock in relevant_item_stocks(item, answer_bases):
            impact = str(stock.get("impact") or "").lower()
            line = f"{format_stock_line(stock)} from \"{item.get('headline')}\""
            if "bearish" in impact:
                risk_lines.append(line)
            else:
                supportive_lines.append(line)
    if risk_lines:
        answer = "The main saved-news risk is concentrated in bearish signals."
    else:
        answer = "I do not see a bearish saved-news signal in the current portfolio context."
    sections = [f"**Answer**\n{answer}"]
    sections.append("**Risk Signals**\n" + "\n".join(risk_lines[:5] or ["- No bearish saved-news signals found."]))
    if supportive_lines:
        sections.append("**Offsetting/Supportive Signals**\n" + "\n".join(supportive_lines[:3]))
    return "\n\n".join(sections)

def build_bull_bear_portfolio_answer(context_items, answer_bases):
    bullish = []
    bearish = []
    neutral = []
    for item in context_items[:6]:
        for stock in relevant_item_stocks(item, answer_bases):
            impact = str(stock.get("impact") or "").lower()
            line = f"{format_stock_line(stock)} from \"{item.get('headline')}\""
            if "bearish" in impact:
                bearish.append(line)
            elif "bullish" in impact:
                bullish.append(line)
            else:
                neutral.append(line)
    sections = ["**Answer**\nHere is the saved-news bull/bear split for your portfolio."]
    sections.append("**Bullish**\n" + "\n".join(bullish[:5] or ["- No bullish saved-news signals found."]))
    sections.append("**Bearish**\n" + "\n".join(bearish[:5] or ["- No bearish saved-news signals found."]))
    if neutral:
        sections.append("**Other**\n" + "\n".join(neutral[:3]))
    return "\n\n".join(sections)

def build_explain_portfolio_answer(context_items, answer_bases):
    item = context_items[0] if context_items else None
    if not item:
        return None
    explanation = item.get("explanation") or "This saved news item does not have a plain-English explanation yet."
    sections = [f"**Answer**\n{explanation}"]
    stocks = [format_stock_line(s) for s in relevant_item_stocks(item, answer_bases)]
    if stocks:
        sections.append("**Portfolio Impact**\n" + "\n".join(stocks[:5]))
    pathway = item.get("macro_pathway") or []
    if pathway:
        sections.append("**Why It Matters**\n" + "\n".join(f"- {step}" for step in pathway[:4]))
    sections.append(f"**News Used**\n- {item.get('headline')}")
    return "\n\n".join(sections)

_UNHELPFUL_FALLBACK_FRAGMENTS = (
    "do not have saved portfolio-linked news",
    "not enough detail to answer",
)


def _is_unhelpful_fallback(answer):
    """True when fallback_portfolio_answer punted because no matching news exists."""
    if not answer:
        return True
    low = answer.lower()
    return any(frag in low for frag in _UNHELPFUL_FALLBACK_FRAGMENTS)


_DATA_DRIVEN_QUESTION_PATTERNS = (
    r'\b(long[\- ]?term|long term|short[\- ]?term|short term)\b',
    r'\b(hold|holding)\b.*\b(year|years|month|months|long)\b',
    r'\b(\d+\s*(year|years|month|months))\b',
    r'\b(horizon|future|forever)\b',
    r'\b(sell|exit|trim|reduce|offload|dump|book\s*profit)\b',
    r'\b(expensive|cheap|overvalu|undervalu|valuation|fairly\s*valued|worth\s*buying)\b',
    r'\b(p\s*/\s*e|pe\s*ratio|pb\s*ratio|p\s*/\s*b)\b',
    r'\b(risk|risky|safe|downside|drawdown|volatil)\b',
    r'\b(diversif|allocation|rebalanc|portfolio\s*mix)\b',
    r'\b(best|better|worst|stronger|weaker)\b',
)


def _question_needs_data_driven_answer(question):
    """
    True when the question is about portfolio decisions (horizon, hold/sell, valuation,
    risk, allocation) rather than a specific saved news event. The news-based fallback
    can't meaningfully answer these, so we route to the data-driven summary instead.
    """
    if not question:
        return False
    q = question.lower()
    return any(re.search(pat, q) for pat in _DATA_DRIVEN_QUESTION_PATTERNS)


def build_data_driven_fallback(question, portfolio_details, portfolio_tickers):
    """
    Produce a useful answer for general portfolio questions when AI is unavailable
    and no matching saved news exists. Uses the live quotes + fundamentals already
    gathered in `portfolio_details` (one preformatted string per holding).

    Tries to detect intent (long-term hold, sell/exit, valuation, sector) and
    tailors the framing accordingly. Always lists each holding with the live data
    so the user gets something concrete instead of "I can only answer from news."
    """
    q = (question or "").lower()

    horizon_terms = ("long term", "long-term", "longterm", "hold", "holding", "year", "years",
                     "month", "months", "horizon", "future")
    sell_terms = ("sell", "exit", "book profit", "trim", "reduce", "offload", "dump")
    value_terms = ("expensive", "cheap", "overvalu", "undervalu", "valuation", "p/e", "pe ratio",
                   "fairly", "worth")
    risk_terms = ("risk", "risky", "safe", "downside", "drawdown", "volatile", "volatility")

    horizon = any(t in q for t in horizon_terms)
    sell = any(t in q for t in sell_terms)
    valuation = any(t in q for t in value_terms)
    risk = any(t in q for t in risk_terms)

    intent_lines = []
    if horizon and sell:
        intent_lines.append(
            "For a multi-year hold, prefer names with a profitable franchise, a P/E close to or below "
            "their sector median, and price comfortably below the 52-week high. Stocks trading near "
            "their 52-week high on stretched P/E are typical trim candidates if you need to free capital."
        )
    elif horizon:
        intent_lines.append(
            "Long-term holds work best when the business has a durable moat and the entry valuation isn't "
            "stretched. Use P/E vs sector median and distance from 52-week high as quick sanity checks."
        )
    elif sell:
        intent_lines.append(
            "Common reasons to trim: P/E meaningfully above sector median, price near 52-week high with "
            "weakening fundamentals, or sector headwinds (rate cycle, regulation, demand slowdown)."
        )
    elif valuation:
        intent_lines.append(
            "Quick valuation read: compare each holding's P/E against its sector median. Above median = "
            "expensive (needs growth to justify); below = potentially cheap if earnings hold up."
        )
    elif risk:
        intent_lines.append(
            "Risk view: stocks far above their 52-week low and trading on high P/E carry more downside in "
            "a correction. Diversification across sectors lowers single-name shock risk."
        )
    else:
        intent_lines.append(
            "Live snapshot of your holdings below. The AI advisor is briefly unavailable, but you can use "
            "P/E vs sector median and distance from 52-week high/low as quick decision anchors."
        )

    sections = ["**Answer**\n" + " ".join(intent_lines)]

    if portfolio_details:
        sections.append("**Your Holdings — Live Data**\n" + "\n".join(portfolio_details))

    sections.append(
        "**Note**\nThe AI advisor timed out, so this is a data-only summary. "
        "Ask again in a moment for a full analysis."
    )
    return "\n\n".join(sections)


def fallback_portfolio_answer(question, context_items, portfolio_tickers, portfolio_names=None):
    if not context_items:
        return "I do not have saved portfolio-linked news for those added stocks yet."

    q = (question or "").lower()
    q_tokens = {tok for tok in re.findall(r'[a-z]{4,}', q) if tok not in {
        'what', 'when', 'where', 'which', 'about', 'from', 'that', 'this',
        'will', 'with', 'have', 'does', 'there', 'their', 'your', 'explain',
    }}
    portfolio_bases = {ticker_base(t) for t in portfolio_tickers}
    wanted_bases = mentioned_portfolio_bases(question, portfolio_tickers, portfolio_names)
    answer_bases = wanted_bases or portfolio_bases
    ranked_context = rank_portfolio_context_items(question, context_items, wanted_bases)
    if wanted_bases and not ranked_context:
        return f"I do not have saved portfolio-linked news for {', '.join(sorted(wanted_bases))} yet."

    intent = portfolio_question_intent(question)
    if intent == "latest":
        answer = build_latest_portfolio_answer(ranked_context, answer_bases)
        if answer:
            return answer
    if intent == "risk":
        return build_risk_portfolio_answer(ranked_context, answer_bases)
    if intent == "bull_bear":
        return build_bull_bear_portfolio_answer(ranked_context, answer_bases)
    if intent == "explain":
        answer = build_explain_portfolio_answer(ranked_context, answer_bases)
        if answer:
            return answer

    compact_q = re.sub(r'[^a-z0-9]+', ' ', q).strip()
    scored_matches = []
    ticker_matches = []
    for item in ranked_context:
        headline = (item.get('headline', '') or '').lower()
        explanation = (item.get('explanation', '') or '').lower()
        compact_headline = re.sub(r'[^a-z0-9]+', ' ', headline).strip()
        headline_hits = sum(1 for tok in q_tokens if tok in headline)
        explanation_hits = sum(1 for tok in q_tokens if tok in explanation)
        score = (headline_hits * 3) + explanation_hits
        if compact_headline and compact_headline in compact_q:
            score += 100
        if score >= 2:
            scored_matches.append((score, item))
            continue

        item_bases = {ticker_base(s.get("ticker")) for s in item.get("stocks", [])}
        if wanted_bases and item_bases & wanted_bases:
            ticker_matches.append(item)
        elif any(base and base.lower() in q for base in item_bases):
            ticker_matches.append(item)

    if scored_matches:
        focused = [item for _, item in sorted(scored_matches, key=lambda pair: pair[0], reverse=True)]
    elif ticker_matches:
        focused = ticker_matches
    else:
        focused = ranked_context[:4]

    item = focused[0] if focused else None
    if not item:
        return "I found portfolio-linked news, but not enough detail to answer that specific question."

    explanation = item.get("explanation") or "This saved news item does not have a plain-English explanation yet."
    sections = [f"**Answer**\n{explanation}"]

    pathway = item.get("macro_pathway") or []
    if pathway:
        sections.append("**Why It Matters**\n" + "\n".join(f"- {step}" for step in pathway[:4]))

    stock_bits = []
    for s in item.get("stocks", []):
        if ticker_base(s.get("ticker")) not in answer_bases:
            continue
        stock_bits.append(format_stock_line(s))
    if stock_bits:
        sections.append("**Portfolio Impact**\n" + "\n".join(stock_bits[:5]))

    news_lines = [f"- {n.get('headline')}" for n in focused[:3] if n.get("headline")]
    sections.append("**News Used**\n" + "\n".join(news_lines or [f"- {item.get('headline')}"]))
    return "\n\n".join(sections)

def run_portfolio_ai_with_timeout(prompt, timeout_seconds=6.5):
    if not API_KEYS:
        return None
    max_attempts = len(API_KEYS)
    for attempt in range(max_attempts):
        active_client = client
        active_idx = current_key_idx
        if not active_client:
            active_client, active_idx = get_and_rotate_client()
            if not active_client:
                return None
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            active_client.models.generate_content,
            model=MODEL_NAME,
            contents=prompt,
        )
        try:
            response = future.result(timeout=timeout_seconds)
            return (getattr(response, "text", "") or "").strip()
        except concurrent.futures.TimeoutError:
            future.cancel()
            safe_print(f"Portfolio assistant AI timeout on key {active_idx + 1}; rotating...")
            get_and_rotate_client(active_idx, is_timeout=True)
        except Exception as e:
            safe_print(f"Portfolio assistant AI error on key {active_idx + 1}: {e}; rotating...")
            is_quota = _is_gemini_quota_error(e)
            get_and_rotate_client(active_idx, is_timeout=False, is_quota=is_quota)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    return None

FUNDAMENTALS_CACHE = {}

def get_stock_fundamentals(ticker):
    """Retrieve trailing P/E, P/B, 52-week High/Low and Sector dynamically from yfinance, caching in-memory for 1 hour."""
    normalized = normalize_ticker(ticker)
    if not normalized:
        return {}
    
    now = time.time()
    cached = FUNDAMENTALS_CACHE.get(normalized)
    if cached and (now - cached["ts"]) < 3600: # 1 hour cache TTL
        return cached["data"]
    
    try:
        import yfinance as yf
        t = yf.Ticker(normalized)
        info = t.info
        
        # Extract fields
        sector = info.get("sector") or "Unknown"
        pe = info.get("trailingPE") or info.get("forwardPE")
        pb = info.get("priceToBook")
        high = info.get("fiftyTwoWeekHigh")
        low = info.get("fiftyTwoWeekLow")
        long_name = info.get("longName") or info.get("shortName") or ticker_base(normalized)
        
        # Float conversions
        pe_val = round(float(pe), 2) if pe is not None else None
        pb_val = round(float(pb), 2) if pb is not None else None
        high_val = round(float(high), 2) if high is not None else None
        low_val = round(float(low), 2) if low is not None else None
        
        data = {
            "name": long_name,
            "sector": sector,
            "pe_ratio": pe_val,
            "pb_ratio": pb_val,
            "high_52w": high_val,
            "low_52w": low_val
        }
        FUNDAMENTALS_CACHE[normalized] = {"data": data, "ts": now}
        return data
    except Exception as e:
        print(f"[Fundamentals] Error fetching for {ticker}: {e}")
        # Return empty structure or stale data if present
        if cached:
            return cached["data"]
        return {}

def is_related_to_stocks_portfolio_news(question, portfolio_tickers, portfolio_names=None):
    """
    Returns True if the question is related to portfolios, stocks, market, financial news, or economy.
    Returns False otherwise.
    Uses robust keyword matching.
    """
    q = (question or "").lower().strip()
    if not q:
        return False
        
    # Check out-of-scope topics first using existing dictionary
    for word in OUT_OF_SCOPE_TOPIC_WORDS:
        if re.search(r'\b' + re.escape(word) + r'\b', q):
            return False

    # Words that strongly indicate relatedness to portfolio, stocks, market, news, or economy
    allowed_keywords = {
        # Portfolio / Holdings
        "portfolio", "holding", "holdings", "watchlist", "added", "investment", "investments", "buy", "sell", "own",
        # Stocks / Assets
        "stock", "stocks", "share", "shares", "equity", "equities", "ticker", "tickers", "symbol", "symbols", "asset", "assets",
        # Market / Price
        "market", "markets", "price", "prices", "valuation", "valuations", "expensive", "cheap", "pe", "p/e", "pb", "p/b",
        "pe ratio", "p/e ratio", "sector", "sectors", "industry", "industries", "index", "indices", "nifty", "sensex", "bse", "nse",
        # Economy / Macro
        "economy", "economic", "gdp", "inflation", "decline", "declining", "grow", "growth", "recession", "slump", "rbi", "fed",
        "interest", "rates", "tariff", "policy", "monsoon", "tax", "budget", "deficit", "rupee", "dollar", "currency",
        # Company / Financials
        "earnings", "profit", "loss", "revenue", "sales", "dividend", "dividends", "ceo", "board", "quarter", "results",
        # News / Impact
        "news", "headline", "headlines", "impact", "affect", "reaction", "signal", "signals", "bullish", "bearish", "trend",
        "rsi", "ema", "macd", "technical", "technicals", "chart", "charts", "trendline", "breakout", "breakdown"
    }
    
    # Check if any allowed keyword is present
    for word in allowed_keywords:
        if re.search(r'\b' + re.escape(word) + r'\b', q):
            return True
            
    # Check if any portfolio ticker is mentioned
    for ticker in portfolio_tickers:
        base = ticker_base(ticker).lower()
        if base and re.search(r'\b' + re.escape(base) + r'\b', q):
            return True
            
    # Check if any portfolio name is mentioned
    if portfolio_names:
        for name in portfolio_names:
            clean = clean_stock_name(name)
            if clean and len(clean) > 2 and clean in q:
                return True
            
    return False

@app.route('/api/portfolio-assistant', methods=['POST'])
def portfolio_assistant():
    started_at = time.time()

    def assistant_response(answer, source, status=200, **extra):
        payload = {
            "answer": answer,
            "source": source,
            "elapsed_ms": int((time.time() - started_at) * 1000),
            **extra,
        }
        return jsonify(payload), status

    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    raw_holdings = data.get("holdings") or data.get("tickers") or []
    if isinstance(raw_holdings, (str, dict)):
        raw_holdings = [raw_holdings]

    portfolio_tickers = []
    portfolio_names = []
    for holding in raw_holdings[:50]:
        if isinstance(holding, dict):
            ticker = holding.get("ticker") or holding.get("symbol") or ""
            name = holding.get("name") or ""
        else:
            ticker = holding
            name = ""
        normalized = normalize_ticker(ticker)
        if normalized and normalized not in portfolio_tickers:
            portfolio_tickers.append(normalized)
        if name:
            portfolio_names.append(str(name))

    if not question:
        return assistant_response(
            "Ask a question about your added portfolio stocks or their saved news.",
            "blocked",
            context_count=0,
        )
    if not portfolio_tickers:
        return assistant_response(
            "Add stocks to your portfolio first, then I can answer from their saved news.",
            "blocked",
            context_count=0,
        )

    # 1. SCOPE GUARD
    if not is_related_to_stocks_portfolio_news(question, portfolio_tickers, portfolio_names):
        return assistant_response(
            "Error: I can only answer questions related to your portfolio, stocks, and the news related to them.",
            "blocked",
            context_count=0,
        )

    # 2. FETCH PORTFOLIO LIVE QUOTES & FUNDAMENTAL DETAILS
    portfolio_details = []
    for ticker in portfolio_tickers:
        # Get live price
        quote = get_stock_market_change_quote(ticker)
        price = quote.get("price", 0.0)
        change_pct = quote.get("change_pct", 0.0)
        price_str = f"₹{price:.2f}" if price else "N/A"
        change_str = f"{change_pct:+.2f}%" if change_pct else "0.00%"
        
        # Get fundamentals
        fund = get_stock_fundamentals(ticker)
        name = fund.get("name") or ticker_base(ticker)
        sector = fund.get("sector") or "Unknown"
        pe = fund.get("pe_ratio")
        pb = fund.get("pb_ratio")
        high = fund.get("high_52w")
        low = fund.get("low_52w")
        
        pe_str = f"{pe:.2f}" if pe else "N/A"
        pb_str = f"{pb:.2f}" if pb else "N/A"
        high_str = f"₹{high:.2f}" if high else "N/A"
        low_str = f"₹{low:.2f}" if low else "N/A"
        
        portfolio_details.append(
            f"- **{ticker}** ({name}):\n"
            f"  * Current Price: {price_str} ({change_str})\n"
            f"  * Sector: {sector}\n"
            f"  * P/E Ratio: {pe_str}\n"
            f"  * P/B Ratio: {pb_str}\n"
            f"  * 52-Week Range: High {high_str} | Low {low_str}"
        )
        
    portfolio_context = "\n".join(portfolio_details)

    # 3. GET NEWS CONTEXT
    context_items, normalized_tickers = get_portfolio_news_context(portfolio_tickers)
    requested_bases = mentioned_portfolio_bases(question, normalized_tickers, portfolio_names)
    answer_context_items = rank_portfolio_context_items(question, context_items, requested_bases)
    
    context_lines = []
    for item in answer_context_items[:5]:
        stock_lines = []
        for stock in item.get("stocks", []):
            if requested_bases and ticker_base(stock.get("ticker")) not in requested_bases:
                continue
            move = ""
            if stock.get("diff_pct") is not None:
                move = f", move={stock['diff_pct']:+.2f}%"
            stock_lines.append(
                f"{stock.get('ticker')} {stock.get('impact')} confidence={stock.get('confidence_score')} "
                f"status={stock.get('status')}{move}; reason={stock.get('reason') or 'NA'}"
            )
        context_lines.append(
            f"News ID {item['id']} | {item.get('news_time') or item.get('created_at')} | "
            f"{item.get('headline')} | Portfolio stocks: {' || '.join(stock_lines)} | "
            f"Explanation: {(item.get('explanation') or '')[:500]}"
        )
        
    news_context = "\n".join(context_lines) if context_lines else "No specific database news matching these stocks is currently logged."

    # 4. CONSTRUCT RICH AI PROMPT
    prompt = f"""You are Alpha Lens Portfolio Assistant.
You are a highly sophisticated financial advisor and quantitative researcher specializing in the Indian equities market.
You have full access to the user's portfolio stocks, live prices, valuation metrics, and their saved news context.

Your task is to answer the user's question in the best possible, professional, and detailed way by researching all possibilities (macroeconomics, stock fundamentals, and sector valuations).

CRITICAL RULE 1 (SCOPE GUARD):
You must STRICTLY only answer questions that are related to the user's portfolio, stocks (in general or in their portfolio), stock market, financial news, economy, and company details.
If the question is NOT related to portfolios, stocks, stock market, companies, financial news, or economic topics (for example: if it asks about weather, cooking, recipes, general coding/programming, general science, non-financial history, sports, astrology, celebrities, or random trivia), you MUST respond exactly with the following error message and nothing else:
"Error: I can only answer questions related to your portfolio, stocks, and the news related to them."

CRITICAL RULE 2 (PORTFOLIO ANALYTICS):
When answering questions about the portfolio or specific stocks, incorporate the provided stock data (Price, P/E ratio, P/B ratio, 52-week High/Low, and Sector) to give a thorough, data-backed analysis.
Estimate and compare their P/E ratios to standard sector/industry benchmarks in the Indian market to assess if they are expensive, cheap, or fairly valued.
Address relevant macroeconomic factors (e.g. declining Indian economy, interest rates, RBI policy) and explain how they impact the specific sectors and stocks in their portfolio.

Format the answer in clean Markdown:
**Answer**
A clear, detailed, and professional explanation (3-6 sentences or bullet points) answering the user's question, integrating macro factors, fundamentals (P/E ratio), and sector positioning.

**Portfolio Valuation & Metrics**
Provide a clean summary table or list of the portfolio stocks mentioned/analyzed, displaying their current Price, P/E, Sector, and Valuation Status (e.g., undervalued, expensive, fairly valued) based on your expert sector knowledge.

**News & Signals Used**
- Mention any headlines from the saved news used, or state "General market analysis and fundamentals used" if no saved database news is directly applicable.

---
USER PORTFOLIO DETAILS:
{portfolio_context}

SAVED NEWS & AI SIGNALS CONTEXT:
{news_context}

USER QUESTION:
{question}
"""

    answer = run_portfolio_ai_with_timeout(prompt)
    if answer:
        return assistant_response(
            answer,
            "ai",
            context_count=len(answer_context_items),
            tickers=normalized_tickers,
            matched_tickers=sorted(requested_bases),
        )

    # Fallback when Gemini is unavailable.
    # For general portfolio questions (horizon, hold/sell, valuation, sector, risk),
    # the news-based fallback can't really help — it would just paraphrase an unrelated
    # headline. Prefer the data-driven summary built from the live prices and
    # fundamentals we already fetched.
    if _question_needs_data_driven_answer(question):
        local_answer = build_data_driven_fallback(question, portfolio_details, portfolio_tickers)
    else:
        local_answer = fallback_portfolio_answer(question, answer_context_items, normalized_tickers, portfolio_names)
        if _is_unhelpful_fallback(local_answer):
            local_answer = build_data_driven_fallback(question, portfolio_details, portfolio_tickers)
    return assistant_response(
        local_answer,
        "local",
        context_count=len(answer_context_items),
        tickers=normalized_tickers,
        matched_tickers=sorted(requested_bases),
        ai_fallback=True,
    )

def _verify_google_id_token(credential_jwt):
    """Returns dict with at least 'email' on success, or raises ValueError."""
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_auth_requests
    except ImportError as e:
        raise ValueError(
            "Install google-auth: pip install google-auth"
        ) from e
    if not GOOGLE_OAUTH_CLIENT_ID:
        raise ValueError("GOOGLE_OAUTH_CLIENT_ID is not configured")
    return id_token.verify_oauth2_token(
        credential_jwt,
        google_auth_requests.Request(),
        GOOGLE_OAUTH_CLIENT_ID,
    )


@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    data = request.get_json(silent=True) or {}
    email = data.get('email')

    if not email:
        return jsonify({"error": "Email is required"}), 400

    if not SENDGRID_API_KEY:
        return jsonify({"error": "Email service is not configured (SENDGRID_API_KEY)."}), 503
    if not SENDGRID_FROM_EMAIL:
        return jsonify({"error": "Email sender is not configured (SENDGRID_FROM_EMAIL)."}), 503

    # Bug #16 fix: prune any expired OTPs before adding a new one
    now_ts = time.time()
    expired = [k for k, (_, exp) in OTP_STORE.items() if now_ts > exp]
    for k in expired:
        OTP_STORE.pop(k, None)

    # Bug #14 fix: use cryptographically secure random for OTP
    otp = str(secrets.randbelow(900000) + 100000)
    OTP_STORE[email] = (otp, now_ts + 600)
    # Bug #5 fix: reset attempt counter whenever a fresh OTP is issued so the
    # user gets a full 5-attempt window for the new code.
    OTP_ATTEMPTS.pop(email, None)

    message = Mail(
        from_email=SENDGRID_FROM_EMAIL,
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
    data = request.get_json(silent=True) or {}
    email = data.get('email')
    user_otp = data.get('otp')

    if not email or email not in OTP_STORE:
        return jsonify({"error": "Invalid or expired OTP."}), 401

    # Bug #5 fix: block brute-force attempts by checking the per-email attempt counter.
    attempts_so_far = OTP_ATTEMPTS.get(email, 0)
    if attempts_so_far >= _OTP_MAX_ATTEMPTS:
        return jsonify({"error": "Too many incorrect attempts. Please request a new OTP."}), 429

    stored_otp, expiry = OTP_STORE[email]
    if time.time() > expiry:
        # OTP expired — remove it, clear attempt counter, and return error
        del OTP_STORE[email]
        OTP_ATTEMPTS.pop(email, None)
        return jsonify({"error": "Invalid or expired OTP."}), 401

    if stored_otp != user_otp:
        # Wrong code — increment attempt counter; delete OTP once limit is reached
        OTP_ATTEMPTS[email] = attempts_so_far + 1
        if OTP_ATTEMPTS[email] >= _OTP_MAX_ATTEMPTS:
            del OTP_STORE[email]
            OTP_ATTEMPTS.pop(email, None)
            return jsonify({"error": "Too many incorrect attempts. Please request a new OTP."}), 429
        remaining = _OTP_MAX_ATTEMPTS - OTP_ATTEMPTS[email]
        return jsonify({"error": f"Invalid OTP. {remaining} attempt(s) remaining."}), 401

    # Correct OTP — clear stores
    del OTP_STORE[email]
    OTP_ATTEMPTS.pop(email, None)

    try:
        conn = connect_users_db()
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
    data = request.get_json(silent=True) or {}
    credential = data.get('credential')

    if not credential:
        return jsonify({"error": "Google credential token is required."}), 400
    if not GOOGLE_OAUTH_CLIENT_ID:
        return jsonify({"error": "Google sign-in is not configured (GOOGLE_OAUTH_CLIENT_ID)."}), 503

    try:
        idinfo = _verify_google_id_token(credential)
    except ValueError as e:
        return jsonify({"error": f"Invalid Google token: {e!s}"}), 401
    except Exception as e:
        print(f"Google OAuth verify error: {e}")
        return jsonify({"error": "Could not verify Google sign-in."}), 401

    email = idinfo.get('email')
    if not email or not idinfo.get('email_verified', True):
        return jsonify({"error": "Google account email is missing or unverified."}), 400

    try:
        conn = connect_users_db()
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
    except Exception:
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

_STOCK_UNIVERSE_SEEDED = False
_STOCK_UNIVERSE_REFRESHING = False
_STOCK_UNIVERSE_LAST_REFRESH = 0
_STOCK_UNIVERSE_LOCK = threading.Lock()

def upsert_stock_universe_rows(rows):
    if not rows:
        return 0

    def _write(conn, c):
        c.executemany("""
            INSERT INTO stock_universe (ticker, symbol, name, exchange, source, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(ticker) DO UPDATE SET
                symbol=EXCLUDED.symbol,
                name=CASE
                    WHEN stock_universe.source = 'curated' THEN stock_universe.name
                    ELSE EXCLUDED.name
                END,
                exchange=EXCLUDED.exchange,
                source=CASE
                    WHEN stock_universe.source = 'curated' THEN stock_universe.source
                    ELSE EXCLUDED.source
                END,
                updated_at=CURRENT_TIMESTAMP
        """, rows)
        return len(rows)

    return db_write(_write) or 0

def is_valid_stock_universe_symbol(symbol):
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    # Angel One's master can include exchange test instruments; hide them from user search.
    if "TEST" in sym or "DUMMY" in sym:
        return False
    return True

def ensure_stock_universe_seeded():
    global _STOCK_UNIVERSE_SEEDED
    if _STOCK_UNIVERSE_SEEDED:
        return
    with _STOCK_UNIVERSE_LOCK:
        if _STOCK_UNIVERSE_SEEDED:
            return

        curated = {}
        for name, ticker in STOCK_KEYWORD_MAP.items():
            normalized = normalize_ticker(ticker)
            if not normalized:
                continue
            display_name = str(name).title()
            current = curated.get(normalized)
            if not current or len(display_name) > len(current):
                curated[normalized] = display_name

        rows = []
        for ticker, name in curated.items():
            base = ticker_base(ticker)
            if not is_valid_stock_universe_symbol(base):
                continue
            exchange = "BSE" if ticker.endswith(".BO") else "NSE"
            rows.append((ticker, base, name, exchange, "curated"))

        try:
            conn = connect_news_db()
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT DISTINCT ticker
                FROM stock_impact
                WHERE ticker IS NOT NULL AND TRIM(ticker) <> ''
            """)
            for row in c.fetchall():
                ticker = normalize_ticker(row["ticker"])
                if not ticker:
                    continue
                base = ticker_base(ticker)
                if not is_valid_stock_universe_symbol(base):
                    continue
                exchange = "BSE" if ticker.endswith(".BO") else "NSE"
                rows.append((ticker, base, base, exchange, "news"))
            conn.close()
        except Exception as e:
            print(f"[Stock Search] Could not seed from stock_impact: {e}")

        upsert_stock_universe_rows(rows)
        _STOCK_UNIVERSE_SEEDED = True
    # Lock released — safe to start Angel One background refresh now
    refresh_stock_universe_from_angelone(force=True)

def refresh_stock_universe_from_angelone(force=False):
    global _STOCK_UNIVERSE_REFRESHING, _STOCK_UNIVERSE_LAST_REFRESH
    now = time.time()
    if not force and (now - _STOCK_UNIVERSE_LAST_REFRESH) < 1800:
        return
    with _STOCK_UNIVERSE_LOCK:
        if _STOCK_UNIVERSE_REFRESHING:
            return
        _STOCK_UNIVERSE_REFRESHING = True
        _STOCK_UNIVERSE_LAST_REFRESH = now

    def _run():
        global _STOCK_UNIVERSE_REFRESHING
        try:
            yf._load_scrip_master()
            rows = []
            nse_names = getattr(yf, "_scrip_name_cache", {})
            bse_names = getattr(yf, "_bse_name_cache", {})
            # Prefer name caches; fall back to symbol keys from token caches
            if nse_names:
                for sym, name in nse_names.items():
                    if not is_valid_stock_universe_symbol(sym):
                        continue
                    rows.append((f"{sym}.NS", sym, name or sym, "NSE", "angelone"))
            else:
                for sym in getattr(yf, "_scrip_cache", {}).keys():
                    if not is_valid_stock_universe_symbol(sym):
                        continue
                    rows.append((f"{sym}.NS", sym, sym, "NSE", "angelone"))
            if bse_names:
                for sym, name in bse_names.items():
                    if not is_valid_stock_universe_symbol(sym):
                        continue
                    rows.append((f"{sym}.BO", sym, name or sym, "BSE", "angelone"))
            else:
                for sym in getattr(yf, "_bse_cache", {}).keys():
                    if not is_valid_stock_universe_symbol(sym):
                        continue
                    rows.append((f"{sym}.BO", sym, sym, "BSE", "angelone"))
            count = upsert_stock_universe_rows(rows)
            print(f"[Stock Search] Stock universe refreshed: {count} Angel One symbols upserted")
        except Exception as e:
            print(f"[Stock Search] Stock universe refresh failed: {e}")
        finally:
            _STOCK_UNIVERSE_REFRESHING = False

    threading.Thread(target=_run, daemon=True).start()

def search_stock_universe(query, limit=20):
    q = (query or "").strip().lower()
    if not q:
        return []
    like   = f"%{q}%"
    prefix = f"{q}%"
    db_results = []
    try:
        conn = connect_news_db()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT ticker, symbol, name, exchange, source,
                CASE
                    WHEN LOWER(symbol) = ?              THEN 0
                    WHEN LOWER(ticker) = ?              THEN 1
                    WHEN LOWER(name)   = ?              THEN 2
                    WHEN LOWER(symbol) LIKE ?           THEN 3
                    WHEN LOWER(name)   LIKE ?           THEN 4
                    WHEN LOWER(ticker) LIKE ?           THEN 5
                    ELSE 6
                END AS rank
            FROM stock_universe
            WHERE (
                   LOWER(symbol) LIKE ?
                OR LOWER(ticker) LIKE ?
                OR LOWER(name)   LIKE ?
            )
              AND UPPER(symbol) NOT LIKE '%TEST%'
              AND UPPER(symbol) NOT LIKE '%DUMMY%'
            ORDER BY rank,
                     CASE WHEN source = 'curated' THEN 1 ELSE 0 END DESC,
                     LENGTH(symbol),
                     symbol
            LIMIT ?
        """, (q, q, q, prefix, prefix, prefix, like, like, like, limit))
        raw_rows = c.fetchall()
        conn.close()
        for r in raw_rows:
            try:
                row = dict(r)
            except Exception:
                row = {"ticker": r[0], "symbol": r[1], "name": r[2], "exchange": r[3], "source": r[4]}
            db_results.append({
                "name":     row.get("name") or row.get("symbol") or row.get("ticker"),
                "ticker":   row.get("ticker"),
                "exchange": row.get("exchange"),
                "source":   row.get("source"),
            })
    except Exception as e:
        print(f"[Stock Search] DB search failed: {e}", flush=True)
        db_results = []

    # ALWAYS also search STOCK_KEYWORD_MAP in-memory and merge.
    # This guarantees results even when DB is empty, seeding is in-flight, or DB query fails.
    seen_tickers = {r["ticker"] for r in db_results}
    curated_matches = []
    for kw_name, ticker in STOCK_KEYWORD_MAP.items():
        if ticker in seen_tickers:
            continue
        base = ticker_base(ticker)
        if not is_valid_stock_universe_symbol(base):
            continue
        name_lower   = kw_name.lower()
        ticker_lower = ticker.lower()
        base_lower   = base.lower()

        if name_lower == q or ticker_lower == q or base_lower == q:
            rank = 0
        elif base_lower.startswith(q) or ticker_lower.startswith(q) or name_lower.startswith(q):
            rank = 1
        elif q in base_lower or q in ticker_lower or q in name_lower:
            rank = 2
        else:
            continue

        curated_matches.append({
            "rank":     rank,
            "name":     kw_name.title(),
            "ticker":   ticker,
            "exchange": "BSE" if ticker.endswith(".BO") else "NSE",
            "source":   "curated",
        })

    curated_matches.sort(key=lambda x: (x["rank"], len(x["ticker"]), x["ticker"]))
    merged = db_results + [
        {"name": m["name"], "ticker": m["ticker"], "exchange": m["exchange"], "source": m["source"]}
        for m in curated_matches
    ]
    return merged[:limit]

@app.route('/api/stock-search', methods=['GET'])
def search_stocks():
    query = request.args.get('q', '').lower().strip()
    if not query:
        return jsonify([])

    ensure_stock_universe_seeded()
    refresh_stock_universe_from_angelone(force=request.args.get("refresh") == "1")
    return jsonify(search_stock_universe(query, limit=20))

def _positive_float(value):
    return _market_quote_float(value)

def get_last_closed_session_quote(ticker):
    """Return (last_close, previous_close) from daily candles."""
    try:
        hist = yf.Ticker(ticker).history(period='10d', interval='1d')
        if hist is None or hist.empty or 'Close' not in hist:
            return None, None
        closes = []
        for value in hist['Close'].dropna().tolist():
            close = _positive_float(value)
            if close:
                closes.append(close)
        if len(closes) >= 2:
            return closes[-1], closes[-2]
        if len(closes) == 1:
            return closes[-1], None
    except Exception as e:
        print(f"[Stock Price] Daily close fallback failed for {ticker}: {e}")
    return None, None

def get_cached_stock_close_from_db(ticker):
    """Fallback to the most recent saved signal when network quotes are unavailable."""
    normalized = normalize_ticker(ticker)
    if not normalized:
        return None, None
    try:
        conn = connect_news_db()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # T2.10: dropped SQLite-only datetime() wrapper — both SQLite and
        # Postgres sort created_at correctly as-is, and datetime() was the
        # source of "function datetime(timestamp without time zone) does
        # not exist" errors visible in earlier Render logs.
        c.execute("""
            SELECT current_price, technical_context
            FROM stock_impact
            WHERE UPPER(ticker) = ? AND current_price > 0
            ORDER BY created_at DESC, id DESC
            LIMIT 20
        """, (normalized.upper(),))
        rows = c.fetchall()
        conn.close()
        for row in rows:
            price = _positive_float(row["current_price"])
            if not price:
                continue
            change_pct = None
            try:
                context = json.loads(row["technical_context"] or "{}")
                change_pct = context.get("return_1d_pct")
            except Exception:
                change_pct = None
            if change_pct is not None:
                try:
                    change_pct = float(change_pct)
                    if change_pct != -100:
                        previous_close = price / (1 + (change_pct / 100))
                        previous_close = _positive_float(previous_close)
                        if previous_close:
                            return price, previous_close
                except Exception:
                    pass
        if rows:
            return _positive_float(rows[0]["current_price"]), None
    except Exception as e:
        print(f"[Stock Price] Saved close fallback failed for {ticker}: {e}")
    return None, None

def get_stock_market_change_quote(ticker, market_open=None):
    if market_open is None:
        market_open = is_market_open()

    normalized_ticker = normalize_ticker(ticker) or str(ticker).upper().strip()
    cache_key = (normalized_ticker, bool(market_open))
    now_ts = time.time()
    cache_ttl = 30 if market_open else 300
    cached = _STOCK_MARKET_CHANGE_CACHE.get(cache_key)
    if cached and (now_ts - cached.get("ts", 0)) < cache_ttl:
        return dict(cached["quote"])

    price_label = "Live" if market_open else "Last Close"
    price = None
    prev_close = None

    if market_open:
        lp, prev = yf.get_ltp(ticker)
        price = _positive_float(lp)
        prev_close = _positive_float(prev)
        if not prev_close:
            last_close, _ = get_last_closed_session_quote(ticker)
            prev_close = _positive_float(last_close)
    else:
        last_close, prior_close = get_last_closed_session_quote(ticker)
        if last_close:
            price = last_close
        if prior_close:
            prev_close = prior_close

        if (not price) or (not prev_close):
            lp, prev = yf.get_ltp(ticker)
            if not price:
                price = _positive_float(lp)
            if not prev_close:
                prev_close = _positive_float(prev)

    if (not price) or (not prev_close):
        cached_price, cached_prev_close = get_cached_stock_close_from_db(ticker)
        if not price and cached_price:
            price = cached_price
            price_label = "Saved Close"
        if not prev_close and cached_prev_close:
            prev_close = cached_prev_close

    if not price:
        price = prev_close or 0.0
    if not prev_close:
        prev_close = price

    quote = {
        "ticker": ticker,
        "price": round(price, 2) if price else 0.0,
        "previous_close": round(prev_close, 2) if prev_close else 0.0,
        "change_pct": calculate_market_change_pct(price, prev_close),
        "market_open": market_open,
        "price_label": price_label,
    }
    _STOCK_MARKET_CHANGE_CACHE[cache_key] = {"quote": quote, "ts": now_ts}
    return dict(quote)

@app.route('/api/stock-price/<ticker>', methods=['GET'])
def get_stock_price(ticker):
    return jsonify(get_stock_market_change_quote(ticker))


# ══════════════════════════════════════════════════════════════
# PREMIUM API: ALPHA SIGNAL SCREENER TERMINAL
# Returns all active/recent signals formatted for the terminal
# ══════════════════════════════════════════════════════════════
@app.route('/api/signal-terminal', methods=['GET'])
def get_signal_terminal():
    try:
        conn = connect_news_db()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # 90-day window (was 5 days) so the terminal keeps a full quarter of
        # signal history. The live re-pricing loop below only fires for Active
        # signals, so returning more rows stays cheap — closed/expired signals
        # use their stored final price.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=SIGNAL_RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
        # ~6 signals/day in practice, so 90 days ≈ ~550 rows; 1500 gives ~3x
        # headroom without truncating, and the table renders once on tab-open
        # (not polled). Raise SIGNAL_TERMINAL_MAX if signal volume grows.
        term_limit = int(os.environ.get("SIGNAL_TERMINAL_MAX", "1500"))
        c.execute("""
            SELECT si.id, si.ticker, si.impact, si.confidence_score,
                   si.base_price, si.current_price, si.estimated_change_percent,
                   si.status, si.created_at, si.view, si.reason, si.ensemble_detail,
                   n.headline
            FROM stock_impact si
            LEFT JOIN news n ON si.news_id = n.id
            WHERE si.created_at >= ?
            ORDER BY si.confidence_score DESC, si.created_at DESC
            LIMIT ?
        """, (cutoff, term_limit))
        rows = c.fetchall()
        conn.close()

        mkt_open = is_market_open()
        market_quote_cache = {}
        signals = []
        for row in rows:
            d = dict(row)
            bp = d.get('base_price') or 0
            cp = d.get('current_price') or bp
            status = d.get('status', 'Active View')
            created_at = d.get('created_at')
            stored_cp = d.get('current_price') or 0

            quote_price = None
            quote_ticker = normalize_ticker(d.get('ticker')) or d.get('ticker')
            # Only re-price signals that are still Active — closed/expired signals
            # (Hit/Stop/Reacted/Expired) have a frozen outcome, so we reuse their
            # stored current_price. This keeps the 90-day terminal cheap by
            # limiting live quote fetches to the handful of still-open signals.
            if quote_ticker and status == 'Active View':
                try:
                    if quote_ticker not in market_quote_cache:
                        market_quote_cache[quote_ticker] = get_stock_market_change_quote(
                            quote_ticker,
                            market_open=mkt_open
                        )
                    quote_price = _positive_float(market_quote_cache[quote_ticker].get('price'))
                    if quote_price:
                        cp = quote_price
                except Exception:
                    quote_price = None

            # If market hasn't traded since news publication, show entry price as current price
            if not mkt_open and status == 'Active View' and not has_market_traded_since(created_at):
                if quote_price and (not bp or not stored_cp or abs(float(stored_cp) - float(bp)) < 0.01):
                    bp = quote_price
                cp = bp
                diff = 0.0
                progress_pct = 0.0
                target_pct = 2.0
                stop_pct = 1.0
                is_bullish = 'bullish' in (d.get('impact') or '').lower()
            else:
                target_pct = 2.0
                stop_pct = 1.0
                reason_str = d.get('reason', '') or ''
                import re as _re
                m_tgt = _re.search(r'target[:\s]+([0-9.]+)%', reason_str, _re.I)
                m_stp = _re.search(r'stop[:\s]+([0-9.]+)%', reason_str, _re.I)
                if m_tgt:
                    try: target_pct = float(m_tgt.group(1))
                    except: pass
                if m_stp:
                    try: stop_pct = float(m_stp.group(1))
                    except: pass

                is_bullish = 'bullish' in (d.get('impact') or '').lower()
                if bp > 0 and cp > 0:
                    diff = ((cp - bp) / bp) * 100
                else:
                    diff = d.get('estimated_change_percent') or 0.0

                if status == 'Predicted Target Hit':
                    progress_pct = 100.0
                elif status in ('Stop Loss Hit', 'Reacted Against Prediction'):
                    progress_pct = -100.0
                elif bp > 0 and cp > 0:
                    towards_prediction = (is_bullish and diff >= 0) or (not is_bullish and diff <= 0)
                    if towards_prediction:
                        divisor = target_pct if target_pct > 0 else 2.0
                        progress_pct = round(min(100.0, (abs(diff) / divisor) * 100), 1)
                    else:
                        divisor = stop_pct if stop_pct > 0 else 1.0
                        progress_pct = -round(min(100.0, (abs(diff) / divisor) * 100), 1)
                else:
                    progress_pct = 0.0

            signals.append({
                'id': d['id'],
                'ticker': d['ticker'],
                'direction': 'BULLISH' if is_bullish else 'BEARISH',
                'confidence': d.get('confidence_score') or 0,
                'entry': round(bp, 2),
                'current': round(cp, 2),
                'target_pct': target_pct,
                'stop_pct': stop_pct,
                'diff_pct': round(diff, 2) if diff else 0,
                'progress_pct': progress_pct,
                'status': status,
                'view': d.get('view', ''),
                'headline': d.get('headline') or '',
                'detail': d.get('ensemble_detail') or '',
                'created_at': d.get('created_at', ''),
            })
        return jsonify({'signals': signals, 'count': len(signals), 'market_open': mkt_open})
    except Exception as e:
        print(f"[signal-terminal] Error: {e}")
        return jsonify({'signals': [], 'count': 0, 'market_open': False})


# ══════════════════════════════════════════════════════════════
# PREMIUM API: NIFTY 50 HEATMAP DATA
# Returns all Nifty 50 stocks with signal + price overlay
# ══════════════════════════════════════════════════════════════
NIFTY50_UNIVERSE = [
    ("RELIANCE","Reliance","Energy"),("TCS","TCS","IT"),("HDFCBANK","HDFC Bank","Banking"),
    ("ICICIBANK","ICICI Bank","Banking"),("INFY","Infosys","IT"),("SBIN","SBI","Banking"),
    ("BHARTIARTL","Airtel","Telecom"),("KOTAKBANK","Kotak Bank","Banking"),
    ("ITC","ITC","FMCG"),("LT","L&T","Infra"),("HINDUNILVR","HUL","FMCG"),
    ("AXISBANK","Axis Bank","Banking"),("BAJFINANCE","Bajaj Fin","Finance"),
    ("MARUTI","Maruti","Auto"),("ASIANPAINT","Asian Paints","FMCG"),
    ("TITAN","Titan","Consumer"),("SUNPHARMA","Sun Pharma","Pharma"),
    ("WIPRO","Wipro","IT"),("HCLTECH","HCL Tech","IT"),("POWERGRID","Power Grid","Power"),
    ("NTPC","NTPC","Power"),("TMPV","Tata Motors","Auto"),
    ("TATASTEEL","Tata Steel","Metal"),("M&M","M&M","Auto"),
    ("ADANIENT","Adani Ent","Conglomerate"),("ADANIPORTS","Adani Ports","Infra"),
    ("ULTRACEMCO","UltraTech","Cement"),("NESTLEIND","Nestle","FMCG"),
    ("TECHM","Tech M","IT"),("INDUSINDBK","IndusInd","Banking"),
    ("GRASIM","Grasim","Cement"),("BAJAJ-AUTO","Bajaj Auto","Auto"),
    ("CIPLA","Cipla","Pharma"),("DRREDDY","Dr Reddy","Pharma"),
    ("HEROMOTOCO","Hero Moto","Auto"),("COALINDIA","Coal India","Energy"),
    ("ONGC","ONGC","Energy"),("BPCL","BPCL","Energy"),("DIVISLAB","Divis Lab","Pharma"),
    ("BRITANNIA","Britannia","FMCG"),("EICHERMOT","Eicher","Auto"),
    ("APOLLOHOSP","Apollo Hosp","Health"),("TATACONSUM","Tata Consumer","FMCG"),
    ("SBILIFE","SBI Life","Insurance"),("HDFCLIFE","HDFC Life","Insurance"),
    ("SHRIRAMFIN","Shriram Fin","Finance"),("JSWSTEEL","JSW Steel","Metal"),
    ("HINDALCO","Hindalco","Metal"),("BAJAJFINSV","Bajaj Finserv","Finance"),
    ("LTIM","LTIMindtree","IT"),  # Bug #19 fix: was LTI, correct NSE ticker is LTIM
]

@app.route('/api/backtest-stats', methods=['GET'])
@route_cache(ttl_seconds=10)
def get_backtest_stats():
    """
    Aggregate signal performance for the Track Record page.

    Query params:
      range = "7d" | "30d" | "90d" | "all" (default 30d)

    A signal is "closed" when status is one of:
      Predicted Target Hit, Stop Loss Hit, Reacted Against Prediction, Expired

    Hit rate counts (Predicted Target Hit) / (closed minus Expired). Expired
    signals are excluded from the denominator because they never resolved — we
    don't pretend they were misses.

    Returns:
      summary: total / closed / hits / stops / hit_rate / avg_win / avg_pnl
      by_confidence: list of {band, signals, hits, stops, hit_rate, avg_pnl}
      by_direction: {bullish: {...}, bearish: {...}}
      recent_closed: last 30 closed signals with details
      range, generated_at
    """
    try:
        range_param = (request.args.get('range') or '30d').lower()
        days_map = {'7d': 7, '30d': 30, '90d': 90, 'all': 365 * 10}
        days = days_map.get(range_param, 30)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

        conn = connect_news_db()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT si.id, si.ticker, si.impact, si.confidence_score, si.status,
                   si.base_price, si.current_price, si.estimated_change_percent,
                   si.created_at, n.headline
            FROM stock_impact si
            LEFT JOIN news n ON si.news_id = n.id
            WHERE si.created_at >= ?
            ORDER BY si.created_at DESC
        """, (cutoff,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()

        CLOSED_STATUSES = {'Predicted Target Hit', 'Stop Loss Hit', 'Reacted Against Prediction', 'Expired'}
        HIT_STATUSES = {'Predicted Target Hit'}
        STOP_STATUSES = {'Stop Loss Hit', 'Reacted Against Prediction'}

        def signed_pnl(row):
            """Return signed P&L % for a signal — positive when the AI was right.

            For a BULLISH call: gain on price up, loss on price down (use raw move).
            For a BEARISH call: gain on price down, loss on price up (invert sign).
            Falls back to estimated_change_percent if computable; otherwise None.
            """
            impact = (row.get('impact') or '').lower()
            est = row.get('estimated_change_percent')
            bp = row.get('base_price') or 0
            cp = row.get('current_price') or 0
            raw = None
            if est is not None:
                raw = float(est)
            elif bp and cp:
                raw = (cp - bp) / bp * 100
            if raw is None:
                return None
            return raw if 'bull' in impact else -raw

        # Hero summary
        total = len(rows)
        closed_rows = [r for r in rows if r['status'] in CLOSED_STATUSES]
        ruled_rows = [r for r in closed_rows if r['status'] != 'Expired']  # judgement denominator
        hits = [r for r in ruled_rows if r['status'] in HIT_STATUSES]
        stops = [r for r in ruled_rows if r['status'] in STOP_STATUSES]

        hit_rate = round(100.0 * len(hits) / len(ruled_rows), 1) if ruled_rows else None

        hit_pnls = [p for r in hits if (p := signed_pnl(r)) is not None]
        all_pnls = [p for r in ruled_rows if (p := signed_pnl(r)) is not None]
        avg_win = round(sum(hit_pnls) / len(hit_pnls), 2) if hit_pnls else None
        avg_pnl = round(sum(all_pnls) / len(all_pnls), 2) if all_pnls else None

        # Confidence bands
        bands_def = [
            ('50-59', 50, 60),
            ('60-69', 60, 70),
            ('70-79', 70, 80),
            ('80-89', 80, 90),
            ('90+',   90, 101),
        ]
        by_confidence = []
        for label, lo, hi in bands_def:
            band_rows = [r for r in ruled_rows if lo <= (r.get('confidence_score') or 0) < hi]
            band_hits = [r for r in band_rows if r['status'] in HIT_STATUSES]
            band_pnls = [p for r in band_rows if (p := signed_pnl(r)) is not None]
            by_confidence.append({
                'band': label,
                'signals': len(band_rows),
                'hits': len(band_hits),
                'stops': len([r for r in band_rows if r['status'] in STOP_STATUSES]),
                'hit_rate': round(100.0 * len(band_hits) / len(band_rows), 1) if band_rows else None,
                'avg_pnl': round(sum(band_pnls) / len(band_pnls), 2) if band_pnls else None,
            })

        # Direction split
        def dir_block(direction_keyword):
            d_rows = [r for r in ruled_rows if direction_keyword in (r.get('impact') or '').lower()]
            d_hits = [r for r in d_rows if r['status'] in HIT_STATUSES]
            d_pnls = [p for r in d_rows if (p := signed_pnl(r)) is not None]
            return {
                'signals': len(d_rows),
                'hits': len(d_hits),
                'stops': len([r for r in d_rows if r['status'] in STOP_STATUSES]),
                'hit_rate': round(100.0 * len(d_hits) / len(d_rows), 1) if d_rows else None,
                'avg_pnl': round(sum(d_pnls) / len(d_pnls), 2) if d_pnls else None,
            }
        by_direction = {
            'bullish': dir_block('bull'),
            'bearish': dir_block('bear'),
        }

        # Recent closed (latest 30)
        recent_closed = []
        for r in closed_rows[:30]:
            pnl = signed_pnl(r)
            recent_closed.append({
                'id': r['id'],
                'ticker': r['ticker'],
                'direction': 'BULLISH' if 'bull' in (r.get('impact') or '').lower() else (
                    'BEARISH' if 'bear' in (r.get('impact') or '').lower() else 'NEUTRAL'
                ),
                'confidence': r.get('confidence_score') or 0,
                'base_price': r.get('base_price'),
                'current_price': r.get('current_price'),
                'pnl_pct': round(pnl, 2) if pnl is not None else None,
                'status': r['status'],
                'created_at': r['created_at'],
                'headline': (r.get('headline') or '')[:140],
            })

        return jsonify({
            'range': range_param,
            'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            'summary': {
                'total_signals': total,
                'closed_signals': len(closed_rows),
                'ruled_signals': len(ruled_rows),
                'hits': len(hits),
                'stops': len(stops),
                'expired': len(closed_rows) - len(ruled_rows),
                'active_or_pending': total - len(closed_rows),
                'hit_rate': hit_rate,
                'avg_win': avg_win,
                'avg_pnl': avg_pnl,
            },
            'by_confidence': by_confidence,
            'by_direction': by_direction,
            'recent_closed': recent_closed,
        })
    except Exception as e:
        print(f"[backtest-stats] Error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════
# PREMIUM API: LATEST SIGNAL ID for push notification polling
# ══════════════════════════════════════════════════════════════
@app.route('/api/signals/latest', methods=['GET'])
def get_latest_signal():
    try:
        conn = connect_news_db()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT si.id, si.ticker, si.impact, si.confidence_score,
                   si.base_price, si.view, si.created_at, n.headline
            FROM stock_impact si
            LEFT JOIN news n ON si.news_id = n.id
            ORDER BY si.id DESC LIMIT 1
        """)
        row = c.fetchone()
        conn.close()
        if row:
            d = dict(row)
            return jsonify({
                'id': d['id'],
                'ticker': d['ticker'],
                'direction': 'BULLISH' if 'bullish' in (d.get('impact') or '').lower() else 'BEARISH',
                'confidence': d.get('confidence_score') or 0,
                'entry': d.get('base_price') or 0,
                'view': d.get('view') or '',
                'headline': (d.get('headline') or '')[:70],
                'created_at': d.get('created_at', ''),
            })
        return jsonify({'id': 0})
    except Exception as e:
        return jsonify({'id': 0})


@app.route('/api/debug-db', methods=['GET'])
def debug_db():
    pg_error = None
    db_url = os.environ.get("DATABASE_URL")
    
    if db_url:
        try:
            # Try to connect directly
            import psycopg2
            pg_conn = psycopg2.connect(db_url, connect_timeout=5)
            pg_conn.close()
        except Exception as e:
            import traceback
            pg_error = {
                'error': str(e),
                'traceback': traceback.format_exc()
            }
    else:
        pg_error = "DATABASE_URL is not set in environment"

    # Check database files on disk
    _here = os.path.dirname(os.path.abspath(__file__))
    _repo_root = os.path.dirname(_here)
    files_status = {
        'here_db': os.path.exists(os.path.join(_here, 'news_cache.db')),
        'here_db_done': os.path.exists(os.path.join(_here, 'news_cache.db.done')),
        'repo_db': os.path.exists(os.path.join(_repo_root, 'backend', 'news_cache.db')),
        'repo_db_done': os.path.exists(os.path.join(_repo_root, 'backend', 'news_cache.db.done')),
        'cwd_db': os.path.exists(os.path.join(os.getcwd(), 'news_cache.db')),
        'cwd_db_done': os.path.exists(os.path.join(os.getcwd(), 'news_cache.db.done')),
    }

    try:
        conn = connect_news_db()
        is_pg = conn.is_postgres
        c = conn.cursor()
        
        # Get count of news, min and max ID
        c.execute("SELECT COUNT(*), MIN(id), MAX(id) FROM news")
        news_stats = c.fetchone()
        news_count = news_stats[0] if news_stats else 0
        min_news_id = news_stats[1] if news_stats else None
        max_news_id = news_stats[2] if news_stats else None
        
        # Get count of stock impact, min and max ID
        c.execute("SELECT COUNT(*), MIN(id), MAX(id) FROM stock_impact")
        impact_stats = c.fetchone()
        impact_count = impact_stats[0] if impact_stats else 0
        min_impact_id = impact_stats[1] if impact_stats else None
        max_impact_id = impact_stats[2] if impact_stats else None

        # Get breakdown of stock impact by status
        c.execute("SELECT status, COUNT(*) FROM stock_impact GROUP BY status")
        status_breakdown = {}
        for row in c.fetchall():
            status_breakdown[str(row[0])] = row[1]
        
        # Fetch latest 5 news headlines
        c.execute("SELECT id, headline, created_at FROM news ORDER BY created_at DESC LIMIT 5")
        latest_news = []
        for row in c.fetchall():
            if hasattr(row, 'keys') or isinstance(row, dict):
                latest_news.append(dict(row))
            else:
                latest_news.append({'id': row[0], 'headline': row[1], 'created_at': str(row[2])})
        
        conn.close()
        return jsonify({
            'status': 'success',
            'is_postgres': is_pg,
            'news_count': news_count,
            'min_news_id': min_news_id,
            'max_news_id': max_news_id,
            'impact_count': impact_count,
            'min_impact_id': min_impact_id,
            'max_impact_id': max_impact_id,
            'status_breakdown': status_breakdown,
            'files_status': files_status,
            'latest_news': latest_news,
            'pg_connection_error': pg_error,
            'has_database_url_env': db_url is not None
        })
    except Exception as e:
        import traceback
        return jsonify({
            'status': 'error',
            'error': str(e),
            'pg_connection_error': pg_error,
            'has_database_url_env': db_url is not None,
            'traceback': traceback.format_exc()
        }), 500


@app.route('/api/debug-worker-status', methods=['GET'])
def debug_worker_status():
    """
    Returns the last-known heartbeat from each background worker plus the latest
    news-table row, so you can diagnose "scraping stopped" issues remotely.

    Key fields:
      - ai_news.last_cycle_finished_at: ago>180s + still running = stalled, not paused
      - ai_news.last_scrape_count: 0 across many cycles = RSS sources down
      - ai_news.last_save_count: 0 with non-zero scrape = AI screener filtering all
      - latest_news.minutes_ago: how stale the DB is, regardless of worker state
    """
    now = time.time()

    def _age(ts):
        return None if ts is None else round(now - ts, 1)

    workers = {}
    for name, bucket in WORKER_HEARTBEAT.items():
        workers[name] = {
            **{k: v for k, v in bucket.items() if not k.endswith("_at")},
            "last_cycle_started_age_s": _age(bucket.get("last_cycle_started_at")),
            "last_cycle_finished_age_s": _age(bucket.get("last_cycle_finished_at")),
            "last_error_age_s": _age(bucket.get("last_error_at")),
        }

    latest_news = None
    try:
        conn = connect_news_db()
        c = conn.cursor()
        c.execute("SELECT id, headline, created_at FROM news ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        if row:
            news_id, headline, created_at = row[0], row[1], row[2]
            minutes_ago = None
            try:
                # created_at is "YYYY-MM-DD HH:MM:SS" UTC
                ts = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                minutes_ago = round((datetime.now(timezone.utc) - ts).total_seconds() / 60, 1)
            except Exception:
                pass
            latest_news = {
                "id": news_id,
                "headline": (headline or "")[:120],
                "created_at": created_at,
                "minutes_ago": minutes_ago,
            }
        conn.close()
    except Exception as e:
        latest_news = {"error": str(e)}

    workers_skipped = os.environ.get("ALPHA_LENS_SKIP_WORKERS", "").lower() in ("1", "true", "yes")

    return jsonify({
        "now_unix": int(now),
        "workers_skipped_via_env": workers_skipped,
        "workers": workers,
        "latest_news": latest_news,
        "available_gemini_keys": len(_available_gemini_key_indices()),
        "total_gemini_keys": len(API_KEYS),
        "selection_funnel": SELECTION_FUNNEL,
        "feed_stats": FEED_STATS,
    })


# Per-worker maximum acceptable gap (seconds) between cycle starts before
# /api/health flags the worker as 'stalled'. Tuned to the worker's own cadence
# (poll interval) plus a generous slack so a slow Gemini call or RSS hiccup
# doesn't flip a healthy worker to degraded.
_WORKER_STALL_BUDGET_SECS = {
    "ai_news":     8 * 60,     # cycles ~every 2-3 min; allow up to 8m
    "yfinance":    3 * 60,     # cycles ~every 10s; 3m is generous
    "macro_shock": 30 * 60,    # MACRO_POLL_SECS default 600s (10m); 3x slack
    "archival":    36 * 3600,  # runs every 24h; 36h budget
    "news_prune":  3 * 3600,   # runs hourly; 3h budget
    "eval_labeler": 9 * 3600,  # runs every 6h; 9h budget
}


@app.route('/api/health', methods=['GET'])
def api_health():
    """
    Friendly health summary for monitoring / quick eyeball checks.

    Returns one of:
      overall = "ok"       → DB reachable, every worker beat within budget
                "degraded" → DB ok but >=1 worker is silent past its budget,
                             or all Gemini keys exhausted
                "down"     → DB unreachable

    Use /api/debug-worker-status for the full per-worker dump; this endpoint
    is the one-liner that tells you "is anything broken right now?".
    """
    now = time.time()
    issues = []

    # ── 1) Database probe ──
    db_ok = False
    db_error = None
    try:
        conn = connect_news_db()
        conn.cursor().execute("SELECT 1").fetchone()
        conn.close()
        db_ok = True
    except Exception as e:
        db_error = str(e)[:200]
        issues.append(f"db: {db_error}")

    # ── 2) Worker liveness ──
    workers_skipped = os.environ.get("ALPHA_LENS_SKIP_WORKERS", "").lower() in ("1", "true", "yes")
    workers_out = {}
    for name, bucket in WORKER_HEARTBEAT.items():
        last_started = bucket.get("last_cycle_started_at")
        last_finished = bucket.get("last_cycle_finished_at")
        ago_started = None if last_started is None else round(now - last_started, 1)
        ago_finished = None if last_finished is None else round(now - last_finished, 1)
        budget = _WORKER_STALL_BUDGET_SECS.get(name, 30 * 60)

        # Worker state machine:
        #   not_started — never beat (process just booted, or worker disabled via env)
        #   ok          — last cycle finished within budget
        #   stalled     — started but no recent finish (loop hung / wedged)
        #   silent      — no beat at all within budget
        if last_started is None and last_finished is None:
            state = "not_started"
        elif ago_started is not None and ago_started > budget:
            state = "silent"
            if not workers_skipped:
                issues.append(f"{name}: silent for {int(ago_started)}s (budget {budget}s)")
        elif last_started and (last_finished is None or last_started > last_finished):
            # Mid-cycle: only count as stalled if started longer than budget ago
            if ago_started is not None and ago_started > budget:
                state = "stalled"
                issues.append(f"{name}: cycle running for {int(ago_started)}s")
            else:
                state = "running"
        else:
            state = "ok"

        workers_out[name] = {
            "state": state,
            "cycles_completed": bucket.get("cycles_completed", 0),
            "last_cycle_started_age_s": ago_started,
            "last_cycle_finished_age_s": ago_finished,
            "stall_budget_s": budget,
            "last_error": bucket.get("last_error"),
            "last_error_age_s": None if bucket.get("last_error_at") is None
                                 else round(now - bucket["last_error_at"], 1),
        }

    # ── 3) Gemini key state ──
    try:
        active = len(_available_gemini_key_indices())
        total = len(API_KEYS)
    except Exception:
        active, total = 0, 0
    gemini = {"active": active, "total": total, "rate_limited": max(0, total - active)}
    if total > 0 and active == 0:
        issues.append("gemini: all keys exhausted")

    # ── 4) Overall verdict ──
    if not db_ok:
        overall = "down"
    elif issues:
        overall = "degraded"
    else:
        overall = "ok"

    return jsonify({
        "overall": overall,
        "issues": issues,
        "db_ok": db_ok,
        "db_error": db_error,
        "workers_skipped_via_env": workers_skipped,
        "workers": workers_out,
        "gemini_keys": gemini,
        "now_unix": int(now),
    }), (200 if overall != "down" else 503)


@app.route('/api/whatsapp/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    """
    Meta WhatsApp Cloud API webhook endpoint.

    GET  — Meta's verification challenge. Returns hub.challenge in plaintext
           when hub.verify_token matches WHATSAPP_VERIFY_TOKEN env var.
    POST — actual webhook events (incoming messages + delivery statuses).
           Returns 200 immediately; payload processing happens async if needed.

    Set in Render env:  WHATSAPP_VERIFY_TOKEN=<the token you typed in Meta>
    Optional:           WHATSAPP_APP_SECRET=<for X-Hub-Signature-256 verify>
    """
    if request.method == 'GET':
        mode      = request.args.get('hub.mode')
        token     = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        expected  = os.environ.get('WHATSAPP_VERIFY_TOKEN', '')

        if not expected:
            print("[WA] Verify GET hit but WHATSAPP_VERIFY_TOKEN env var is not set", flush=True)
            return ('WHATSAPP_VERIFY_TOKEN env var not set on server', 500)

        if mode == 'subscribe' and token == expected and challenge:
            print(f"[WA] Webhook verification OK", flush=True)
            return (challenge, 200, {'Content-Type': 'text/plain'})

        print(f"[WA] Verify rejected — mode={mode!r}, token_match={token == expected}", flush=True)
        return ('Verification failed', 403)

    # POST — actual event delivery
    try:
        payload = request.get_json(silent=True) or {}
        # Meta payload shape:
        # { "object": "whatsapp_business_account",
        #   "entry": [ { "changes": [ { "value": { "messages":[...], "statuses":[...] } } ] } ] }
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {}) or {}

                # Inbound messages (STOP / START / free-text)
                for msg in value.get('messages', []) or []:
                    sender = msg.get('from', '')
                    text   = ((msg.get('text') or {}).get('body') or '').strip().upper()
                    print(f"[WA] inbound msg from {sender}: {text!r}", flush=True)
                    # TODO: handle STOP/START → mark whatsapp_opt_in on user row

                # Delivery / read statuses
                for st in value.get('statuses', []) or []:
                    sid    = st.get('id')
                    status = st.get('status')
                    print(f"[WA] status {sid}: {status}", flush=True)
                    # TODO: update whatsapp_alert_log
    except Exception as e:
        print(f"[WA] Webhook POST handler error: {e}", flush=True)
    # ALWAYS return 200 to Meta — otherwise they retry and eventually disable the subscription
    return ('', 200)


@app.route('/api/debug-whatsapp', methods=['GET'])
def debug_whatsapp():
    """Introspect WhatsApp Cloud API configuration without exposing secrets."""
    try:
        import whatsapp_sender
        return jsonify(whatsapp_sender.configuration_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/debug-whatsapp-broadcast', methods=['POST'])
def debug_whatsapp_broadcast():
    """
    Fire a hello_world template to EVERY recipient in WHATSAPP_RECIPIENTS.

    Auth: provide WHATSAPP_VERIFY_TOKEN value as ?token=... query param OR as
    X-WA-Verify-Token header. This is the webhook verify token (a low-security
    handshake identifier, NOT the access token), so it's safe to use as a
    one-shot test trigger.

    Use this exactly once after deploy to confirm end-to-end WhatsApp
    delivery before relying on the production signal-driven path.
    """
    expected = (os.environ.get("WHATSAPP_VERIFY_TOKEN") or "").strip()
    if not expected:
        return jsonify({"error": "WHATSAPP_VERIFY_TOKEN env var not set"}), 500

    supplied = (
        request.headers.get("X-WA-Verify-Token")
        or request.args.get("token")
        or ""
    ).strip()
    if supplied != expected:
        return jsonify({"error": "Bad token"}), 401

    try:
        import whatsapp_sender as wa
        recipients = wa._get_recipients()
        if not recipients:
            return jsonify({"error": "WHATSAPP_RECIPIENTS empty"}), 400
        results = []
        for r in recipients:
            res = wa.send_test_message(r)
            results.append({"to_suffix": r[-4:], **res})
        return jsonify({"recipients": len(recipients), "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/debug-whatsapp-send-test', methods=['POST'])
def debug_whatsapp_send_test():
    """
    Manually fire a hello_world template message to one phone number.

    Protected by SQL_RUNNER_SECRET (same secret used by debug-sql-runner)
    so you can test sending without exposing this route to the public.

    Body: {"phone": "917799499857"}  (E.164 without '+')
    """
    secret = os.environ.get("SQL_RUNNER_SECRET")
    token  = request.headers.get("X-Alpha-Lens-Token")
    if not secret or token != secret:
        return jsonify({"error": "Unauthorized"}), 401

    data  = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").lstrip("+").strip()
    if not phone:
        return jsonify({"error": "phone required (E.164, no '+')"}), 400

    try:
        import whatsapp_sender
        return jsonify(whatsapp_sender.send_test_message(phone))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/debug-gemini-keys', methods=['GET'])
def debug_gemini_keys():
    """
    Returns the number of Gemini API keys actually loaded into rotation and the
    cooldown status of each. Use this to verify that newly-added keys (e.g.
    GEMINI_API_KEY_10/11/12) made it into the process.

    Does not return key material — only counts, indices, and cooldown timing.
    """
    now = time.time()

    def _pool_status(pool_keys, pool_label, slot_range):
        per_key = []
        for i, _ in enumerate(pool_keys):
            slot_num = slot_range[i]
            until = _KEY_QUOTA_COOLDOWN_UNTIL.get(i, 0) if _ACTIVE_KEY_LIST == (1 if pool_label == "List 1" else 2) else 0
            per_key.append({
                "index": i + 1,
                "env_var": f"GEMINI_API_KEY_{slot_num}",
                "available": until <= now,
                "cooldown_secs_remaining": max(0, int(until - now)),
            })
        return per_key

    list1_slots = list(range(1, 24))
    list2_slots = list(range(24, 36))

    list1_status = _pool_status(API_KEYS_LIST1, "List 1", list1_slots)
    list2_status = _pool_status(API_KEYS_LIST2, "List 2", list2_slots)

    # currently active pool keys with live cooldown info
    active_per_key = []
    for i in range(len(API_KEYS)):
        until = _KEY_QUOTA_COOLDOWN_UNTIL.get(i, 0)
        active_per_key.append({
            "index": i + 1,
            "available": until <= now,
            "cooldown_secs_remaining": max(0, int(until - now)),
        })

    declared_slots = list(range(1, 36))
    declared_present = {n: bool(os.environ.get(f"GEMINI_API_KEY_{n}")) for n in declared_slots}
    return jsonify({
        "active_list": _ACTIVE_KEY_LIST,
        "list1_key_count": len(API_KEYS_LIST1),
        "list2_key_count": len(API_KEYS_LIST2),
        "total_key_count": len(API_KEYS_LIST1) + len(API_KEYS_LIST2),
        "currently_active_index": (current_key_idx + 1) if current_key_idx is not None else None,
        "active_pool_keys": active_per_key,
        "list1_keys": list1_status,
        "list2_keys": list2_status,
        "env_slots_present": declared_present,
    })


# ── ONE-TIME pending-backlog backfill state ──
# Progress is tracked in-process so the slow work can run in a daemon thread
# (it can take minutes under Gemini quota pressure) while the HTTP request
# returns immediately — otherwise Render/Cloudflare's ~60s gateway would kill
# a synchronous call. GET the endpoint (with token) to read this.
_BACKFILL_STATE = {"running": False, "started_at": None, "finished_at": None,
                   "limit": None, "summary": None}


def _run_backfill_pending(limit):
    """
    The actual one-time backlog backfill. Runs the SAME prediction pipeline
    ai_news_worker uses — Gemini screener -> EnsemblePredictor -> ATR gate ->
    save/merge — over news rows still at ai_status='pending', then flips them
    to 'screened'. Mirrors the worker's screener prompt + ensemble + ATR +
    save/merge verbatim so predictions (and win rate) stay consistent. SKIPS
    the ripple-graph and WhatsApp side effects on purpose (no point firing
    hundreds of alerts / extra Gemini calls for old news).

    Runs inside a daemon thread (see route below). Returns a summary dict.
    Batches with no usable key are left 'pending' (quota_skipped) for re-run.
    """
    try:
        _c = connect_news_db(); _cur = _c.cursor()
        _cur.execute("""SELECT id, headline, news_time, body FROM news
                        WHERE ai_status = 'pending'
                        ORDER BY created_at DESC LIMIT ?""", (limit,))
        rows = _cur.fetchall(); _c.close()
    except Exception as e:
        return {"error": f"pending fetch failed: {e}"}

    if not rows:
        return {"status": "done", "processed": 0, "message": "No pending articles."}

    articles = [{"headline": h, "time": (t or ""), "url": None,
                 "summary": (b or ""), "deep_context": (b or ""), "_nid": nid}
                for (nid, h, t, b) in rows]

    ensemble = EnsemblePredictor()
    market_regime = get_market_regime()
    _ctx_chars = int(os.environ.get("SCRAPER_CONTEXT_CHARS", "200"))
    BATCH_SIZE = int(os.environ.get("SCRAPER_BATCH_SIZE", "8"))
    _require_atr = os.environ.get("REQUIRE_ATR", "1").lower() in ("1", "true", "yes")

    summary = {"pending_pulled": len(articles), "screened": 0,
               "material_articles": 0, "signals_saved": 0, "quota_skipped": 0}
    screened_ids = []

    import random as _rnd
    import concurrent.futures as _cf2
    import time as _time

    for _bi in range(0, len(articles), BATCH_SIZE):
        batch = articles[_bi:_bi + BATCH_SIZE]
        avail = _available_gemini_key_indices()
        if not avail:
            summary["quota_skipped"] += len(batch)
            continue
        try_order = list(avail); _rnd.shuffle(try_order)

        numbered = "\n".join(
            f"{i+1}. Headline: {a['headline']}\n   Context: {(a.get('deep_context') or a.get('summary') or 'Not available')[:_ctx_chars]}"
            for i, a in enumerate(batch))
        schema_example = json.dumps([
            {"index": 1, "material": True, "catalyst_type": "EARNINGS_BEAT", "materiality_score": 87,
             "impacts": [{"ticker": "TCS.NS", "direction": "BULLISH", "confidence": 88, "impact_type": "DIRECT",
                          "reason": "Q4 PAT beat consensus and deal pipeline improved; similar beats can drive a 3-7% move in 1-5 sessions."}]},
            {"index": 2, "material": False, "catalyst_type": "NOISE", "materiality_score": 12, "impacts": []}
        ], indent=2)
        # NOTE: prompt copied verbatim from quant_ai_screener (the active one
        # at ~line 3766). Keep these in sync if the worker prompt ever changes.
        prompt = f"""You are the Chief Investment Strategist at India's top macro hedge fund, managing ₹50,000 Cr AUM. You are NOT a keyword matcher — you are a MACRO STRATEGIST who sees connections that retail traders completely miss.

Your EDGE: You analyze news through HIDDEN SUPPLY CHAINS, GEOPOLITICAL TRANSMISSION, and SECOND/THIRD-ORDER EFFECTS. When retail traders read "Japan restricts semiconductor exports" they see nothing. YOU see: chip shortage → auto production cuts → MARUTI.NS/TMPV.NS BEARISH, IT hardware delays → INFY.NS BEARISH, but chip design outsourcing opportunity → TATAELXSI.NS BULLISH.

Analyze exactly {len(batch)} news items. For EVERY article, think through these HIDDEN CHAINS:

LAYER 1 — OBVIOUS (what retail sees): Which company is directly named?
LAYER 2 — SUPPLY CHAIN (what smart money sees): Who supplies to or buys from the affected company? Who competes? What raw materials flow into their products?
  Examples: Steel price surge → input cost for MARUTI (BEARISH), revenue for TATASTEEL (BULLISH)
           China slowdown → metal demand drop → HINDALCO/JSWSTEEL BEARISH
           US visa restrictions → onsite revenue pressure → INFY/TCS BEARISH
LAYER 3 — MACRO TRANSMISSION (what only PMs see): How does this ripple through the Indian economy?
  Examples: Japan chip export ban → global auto production cut → Indian auto parts exporters BEARISH → BUT import substitution plays BULLISH
           Middle East conflict → crude spike → OMC margin compression BEARISH → but ONGC windfall BULLISH → RBI inflation worry → rate hike fear → real estate BEARISH
           US Fed pause → dollar weakening → rupee strengthens → IT exporters BEARISH → FII inflows → banking BULLISH
LAYER 4 — FLOW POSITIONING: Will FIIs/DIIs be forced to rebalance? Will index weights shift? Will options market reprice?

CRITICAL RULES FOR HIDDEN CHAINS:
✅ ALWAYS map global commodity news to Indian users/producers (crude→airlines/OMCs/paint, steel→auto/infra, copper→power)
✅ ALWAYS map geopolitical tension to Indian supply chain dependencies (China/Taiwan/Japan → IT/auto/electronics/pharma APIs)
✅ ALWAYS map central bank decisions (Fed/ECB/BOJ/RBI) to FII flow impact on Indian equities
✅ ALWAYS map currency moves to export/import-heavy sectors
✅ MAP government policy/regulation to specific sector P&L impact (PLI, tariffs, subsidies, environmental norms)
✅ MAP monsoon/weather to agri-dependent sectors (FMCG, fertilizers, rural consumption)

REJECT zero-signal noise:
- Generic "Nifty may rise/fall" commentary without a causal mechanism
- Analyst target reiterations without NEW catalyst
- Technical chart analysis, "stocks to watch" listicles
- Repeat coverage of events already priced in
- Weak sector sympathy without a clear P&L transmission path

Rules:
- Return a complete JSON array with one object for every input index
- For no stock impact: set material=false, impacts=[]
- For material articles: return 1-3 HIGHEST-CONVICTION stocks with DEEP reasoning
- reason MUST explain the HIDDEN CHAIN (e.g., "Japan chip curbs → auto production delays → Maruti depends on Denso/Aisin for ECUs → 15% of components are chip-dependent → production cut risk")
- Use exact NSE ticker.NS format. Do NOT invent tickers.
- confidence and materiality_score: 0-100

News items to analyze:
{numbered}

Return ONLY valid JSON matching this shape:
{schema_example}"""

        resp = None
        for _key_idx in try_order:
            _ai_client = genai.Client(api_key=API_KEYS[_key_idx])
            try:
                _tex = _cf2.ThreadPoolExecutor(max_workers=1)
                try:
                    _fut = _tex.submit(lambda _cl=_ai_client, _p=prompt: _cl.models.generate_content(
                        model=MODEL_NAME, contents=_p,
                        config=types.GenerateContentConfig(response_mime_type="application/json")))
                    resp = _fut.result(timeout=60)
                finally:
                    _tex.shutdown(wait=False, cancel_futures=True)
                _set_active_gemini_client(_key_idx)
                _reset_gemini_key_strikes(_key_idx)
                break
            except Exception as _e:
                if _is_gemini_quota_error(_e):
                    _mark_gemini_key_quota_hit(_key_idx); _time.sleep(1); continue
                if _is_gemini_transient_error(_e):
                    _mark_gemini_key_quota_hit(_key_idx, cooldown_secs=_GEMINI_TRANSIENT_COOLDOWN_SECS); _time.sleep(1); continue
                _mark_gemini_key_quota_hit(_key_idx, cooldown_secs=30); _time.sleep(1); continue
        if resp is None:
            summary["quota_skipped"] += len(batch); continue

        parsed = extract_json_from_text(resp.text)
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    parsed = v; break
        if not isinstance(parsed, list):
            summary["quota_skipped"] += len(batch); continue

        approved_signals = []
        for item in parsed:
            try:
                idx = int(item.get("index", 0)) - 1
            except Exception:
                continue
            if not (0 <= idx < len(batch)):
                continue
            art = batch[idx]
            if not item.get("material", False):
                continue
            cand = []
            for impact in item.get("impacts", []):
                tk = normalize_ticker(impact.get("ticker", ""))
                dr = (impact.get("direction", "") or "").upper().strip()
                if tk and dr in ("BULLISH", "BEARISH") and is_supported_equity_ticker(tk):
                    try:
                        cf = int(float(impact.get("confidence", item.get("materiality_score", 75))))
                    except Exception:
                        cf = 75
                    it = str(impact.get("impact_type", "DIRECT")).upper()
                    if it == "SECOND_ORDER":
                        cf = max(10, cf - 2)
                    elif it == "MACRO_TRANSMISSION":
                        cf = max(10, cf - 3)
                    cand.append({"ticker": tk, "direction": dr, "confidence": cf, "reason": impact.get("reason", "")})
            ranked = {}
            for ci in cand:
                q = max(10, min(99, int(float(ci.get("confidence", 70)))))
                cur = ranked.get(ci["ticker"])
                if not cur or q > cur["quality_score"]:
                    ranked[ci["ticker"]] = {"ticker": ci["ticker"], "direction": ci["direction"], "quality_score": q, "reason": ci.get("reason", "")}
            top = sorted(ranked.values(), key=lambda x: x["quality_score"], reverse=True)[:3]
            if not top:
                continue
            _catalyst = (item.get("catalyst_type") or "").upper().replace(" ", "_")
            summary["material_articles"] += 1
            for r in top:
                ticker = r["ticker"]; base_direction = r["direction"]
                tech_data = get_stock_technical_context(ticker)
                tech_context_str = json.dumps(tech_data) if tech_data else ""
                _atr = float(tech_data['atr_pct']) if (tech_data and tech_data.get('atr_pct')) else 0.0
                if _atr > 0:
                    _stop = round(min(2.5, max(1.0, _atr * 1.0)), 2)
                    _tgt = round(min(5.0, max(2.0, _atr * 2.0)), 2)
                elif _require_atr:
                    continue
                else:
                    _stop = TRADE_STOP_PCT; _tgt = TRADE_TARGET_PCT
                base_price, current_price_now, _pub = get_signal_prices_for_publication(
                    ticker,
                    art.get("time")
                )
                _ai_input = art["headline"]
                _ctx = art.get("deep_context") or art.get("summary") or ""
                if _ctx:
                    _ai_input = f"{art['headline']}\nContext: {_ctx}"
                # force_precalculated=True: the ensemble's AI model reuses the
                # screener's quality_score instead of a fresh per-ticker Gemini
                # call, so the whole backfill costs ~1 Gemini call per batch
                # (the screener) instead of ~10. Stretches scarce quota ~10x.
                result = ensemble.predict(
                    headline=_ai_input, ticker=ticker, direction=base_direction,
                    tech_data=tech_data, market_regime=market_regime, db_connect_fn=connect_news_db,
                    api_client=client, model_name=MODEL_NAME, min_score=MIN_CONFIDENCE,
                    get_client_fn=get_and_rotate_client, precalculated_score=r.get("quality_score"),
                    catalyst_type=_catalyst, news_age_hours=None, force_precalculated=True)
                if not result['approved']:
                    continue
                view = 'High Conviction' if result['final_score'] >= 85 else 'Moderate Conviction'
                quality = r.get("quality_score")
                qnote = f" Stock-pick quality: {quality}/100." if quality else ""
                reason = (f"Ensemble Score: {result['final_score']} ({result['models_agreeing']}/5 models approve). "
                          f"ATR stop: {_stop:.1f}% | target: {_tgt:.1f}%.{qnote}")
                approved_signals.append((art["_nid"], ticker, result['direction'], _tgt, view, reason,
                                         base_price, current_price_now, result['final_score'],
                                         tech_context_str, result['detail'], _pub))
                RECENT_DIRECTIONS.append(result['direction'])

        if approved_signals:
            _sigs = approved_signals
            def _insert_signals(conn, c, _s=_sigs):
                for sig in _s:
                    news_id, ticker, impact, est_change, view_str, reason_str, bp, cp, conf, tech_ctx, ens_det, created_at_str = sig
                    c.execute("""SELECT id, confidence_score, reason, base_price, created_at, impact
                                 FROM stock_impact WHERE ticker = ? AND status = 'Active View'
                                 ORDER BY created_at DESC LIMIT 1""", (ticker,))
                    row = c.fetchone()
                    merged = False
                    if row:
                        db_id, db_conf, db_reason, db_bp, db_created, db_impact = row
                        try:
                            new_dt = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S')
                        except Exception:
                            new_dt = datetime.now(timezone.utc).replace(tzinfo=None)
                        try:
                            db_dt = datetime.strptime(db_created, '%Y-%m-%d %H:%M:%S')
                        except Exception:
                            db_dt = new_dt
                        if abs((new_dt - db_dt).total_seconds()) <= 86400:
                            is_similar = False
                            if db_bp == 0.0 or bp == 0.0:
                                is_similar = True
                            else:
                                if abs(db_bp - bp) / db_bp <= 0.025:
                                    is_similar = True
                            if is_similar:
                                _prior_merges = (db_reason or "").count("[Consensus Boost")
                                try:
                                    _boost_schedule = [int(x) for x in os.environ.get("MERGE_BOOST_SCHEDULE", "5,3,2,1").split(",")]
                                except Exception:
                                    _boost_schedule = [5, 3, 2, 1]
                                _this_boost = _boost_schedule[min(_prior_merges, len(_boost_schedule) - 1)]
                                _raw_boosted = max(db_conf, conf) + _this_boost
                                _merge_cap = int(os.environ.get("MERGE_CONF_CAP", "80"))
                                _high_base = int(os.environ.get("MERGE_HIGH_BASE", "85"))
                                if not ((db_conf >= _high_base) or (conf >= _high_base)):
                                    _raw_boosted = min(_raw_boosted, _merge_cap)
                                boosted_conf = min(99, _raw_boosted)
                                new_view = 'High Conviction' if boosted_conf >= 85 else 'Moderate Conviction'
                                c.execute("SELECT headline FROM news WHERE id = ?", (news_id,))
                                hl_row = c.fetchone()
                                new_hl = hl_row[0] if hl_row else "Consensus News"
                                if impact != db_impact:
                                    final_impact = impact if conf > db_conf else db_impact
                                    merged_reason = f"{db_reason} | [Consensus Boost ({impact}): '{new_hl}'] {reason_str}"
                                else:
                                    final_impact = db_impact
                                    merged_reason = f"{db_reason} | [Consensus Boost: '{new_hl}'] {reason_str}"
                                c.execute("""UPDATE stock_impact SET confidence_score = ?, view = ?, reason = ?, impact = ?
                                             WHERE id = ?""", (boosted_conf, new_view, merged_reason, final_impact, db_id))
                                merged = True
                    if not merged:
                        c.execute('''INSERT OR IGNORE INTO stock_impact
                            (news_id, ticker, impact, estimated_change_percent, view, reason, base_price, current_price, confidence_score, technical_context, ensemble_detail, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', sig)
            db_write(_insert_signals)
            summary["signals_saved"] += len(approved_signals)

        # Every article in a successfully-screened batch (material or not) is
        # marked screened — same semantics as the worker's rescreen completion.
        screened_ids.extend([a["_nid"] for a in batch])

    if screened_ids:
        def _mark(conn, c, _ids=screened_ids):
            ph = ",".join(["?"] * len(_ids))
            c.execute(f"UPDATE news SET ai_status='screened' WHERE id IN ({ph}) AND ai_status='pending'", tuple(_ids))
        db_write(_mark)
        summary["screened"] = len(screened_ids)

    return {"status": "ok", "summary": summary}


@app.route('/api/admin/backfill-pending-predictions', methods=['POST', 'GET'])
def backfill_pending_predictions():
    """
    ONE-TIME, MANUAL backlog backfill (NOT automated, NOT wired into any
    worker). POST starts a background pass over ai_status='pending' news,
    runs the faithful prediction pipeline (see _run_backfill_pending), and
    flips processed rows to 'screened'. ADDITIVE ONLY — the worker and every
    existing code path are untouched, so fresh news keeps working identically.

    Auth:  X-Alpha-Lens-Token: <SQL_RUNNER_SECRET>
    POST body: { "limit": <int> }  newest-N pending to process (default 5).
               Run a small N first, inspect, then raise it to drain all 420.
    GET: returns current/last run progress (poll this instead of waiting on
         the POST, which returns immediately).

    The work runs in a daemon thread because under Gemini quota pressure it
    can take minutes — a synchronous response would exceed the gateway timeout.
    """
    secret = os.environ.get("SQL_RUNNER_SECRET")
    if not secret or request.headers.get("X-Alpha-Lens-Token") != secret:
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == 'GET':
        return jsonify(_BACKFILL_STATE)

    if _BACKFILL_STATE.get("running"):
        return jsonify({"status": "already_running", "state": _BACKFILL_STATE}), 409

    data = request.get_json(silent=True) or {}
    try:
        limit = max(1, min(2000, int(data.get("limit", 5))))
    except Exception:
        limit = 5

    def _job(_limit=limit):
        _BACKFILL_STATE.update({"running": True,
                                "started_at": datetime.now(timezone.utc).isoformat(),
                                "finished_at": None, "limit": _limit, "summary": None})
        try:
            _BACKFILL_STATE["summary"] = _run_backfill_pending(_limit)
        except Exception as _e:
            import traceback
            _BACKFILL_STATE["summary"] = {"error": str(_e), "traceback": traceback.format_exc()[:2000]}
        finally:
            _BACKFILL_STATE["running"] = False
            _BACKFILL_STATE["finished_at"] = datetime.now(timezone.utc).isoformat()

    threading.Thread(target=_job, daemon=True, name="BackfillPending").start()
    return jsonify({"status": "started", "limit": limit,
                    "note": "Running in background. Poll GET /api/admin/backfill-pending-predictions (with token) or watch the pending count for progress."})


@app.route('/api/admin/apply-claude-predictions', methods=['POST'])
def apply_claude_predictions():
    """
    ONE-TIME, MANUAL. Accepts Claude-generated screener results for pending
    news (the AI step that Gemini normally does — but Gemini's daily quota is
    spent, so Claude did it instead) and runs the SAME ensemble the worker
    uses: the rule models (historical / technical / sector / market) PLUS the
    AI vote supplied by Claude, then the ATR gate + save/merge, and flips the
    rows to 'screened'. Makes ZERO Gemini calls (the AI vote is injected via
    force_precalculated, which short-circuits before any Gemini client call).

    Win rate stays consistent: only the AI *provider* changed (Gemini→Claude);
    the ensemble math, the 60/3 gate, ATR sizing, and save/merge are identical
    to normally-generated signals. ADDITIVE ONLY — no existing code path or the
    worker is touched.

    Auth: X-Alpha-Lens-Token: <SQL_RUNNER_SECRET>
    Body: {
      "results": [
        {"news_id": 123, "headline": "...", "material": true,
         "impacts": [{"ticker":"TCS.NS","direction":"BULLISH","confidence":82,
                      "impact_type":"DIRECT","reason":"..."}]},
        {"news_id": 124, "headline": "...", "material": false, "impacts": []}
      ]
    }
    A result with material=false (or no valid impacts) is still flipped to
    'screened' (analysed, no equity impact) — clearing its "AI Analysis
    Pending" badge in the UI.
    """
    secret = os.environ.get("SQL_RUNNER_SECRET")
    if not secret or request.headers.get("X-Alpha-Lens-Token") != secret:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    results = data.get("results") or []
    if not results:
        return jsonify({"error": "no results provided"}), 400

    ensemble = EnsemblePredictor()
    market_regime = get_market_regime()
    _require_atr = os.environ.get("REQUIRE_ATR", "1").lower() in ("1", "true", "yes")
    summary = {"received": len(results), "screened": 0, "material": 0,
               "signals_saved": 0, "errors": 0,
               "atr_skipped": 0, "ensemble_rejected": 0, "evaluated": 0,
               "sample_detail": None}
    screened_ids = []
    approved_signals = []

    for res in results:
        try:
            nid = res.get("news_id")
            if nid is None:
                summary["errors"] += 1
                continue
            screened_ids.append(nid)
            if not res.get("material"):
                continue
            headline = res.get("headline") or ""
            news_publication_time = res.get("news_time") or res.get("created_at") or ""
            if not news_publication_time:
                try:
                    _news_conn = connect_news_db()
                    _news_cur = _news_conn.cursor()
                    _news_cur.execute("SELECT news_time, created_at FROM news WHERE id = ? LIMIT 1", (nid,))
                    _news_row = _news_cur.fetchone()
                    _news_conn.close()
                    if _news_row:
                        news_publication_time = (_news_row[0] or _news_row[1] or "")
                except Exception:
                    news_publication_time = ""
            impacts = res.get("impacts") or []
            summary["material"] += 1
            # Rank exactly like the Gemini screener does: dedupe by ticker,
            # apply the impact-type confidence haircut, keep top 3.
            ranked = {}
            for imp in impacts:
                tk = normalize_ticker(imp.get("ticker", ""))
                dr = (imp.get("direction", "") or "").upper().strip()
                if not (tk and dr in ("BULLISH", "BEARISH") and is_supported_equity_ticker(tk)):
                    continue
                try:
                    cf = int(float(imp.get("confidence", 75)))
                except Exception:
                    cf = 75
                it = str(imp.get("impact_type", "DIRECT")).upper()
                if it == "SECOND_ORDER":
                    cf = max(10, cf - 2)
                elif it == "MACRO_TRANSMISSION":
                    cf = max(10, cf - 3)
                cf = max(10, min(99, cf))
                cur = ranked.get(tk)
                if not cur or cf > cur["quality_score"]:
                    ranked[tk] = {"ticker": tk, "direction": dr, "quality_score": cf, "reason": imp.get("reason", "")}
            top = sorted(ranked.values(), key=lambda x: x["quality_score"], reverse=True)[:3]
            for r in top:
                ticker = r["ticker"]; base_direction = r["direction"]
                tech_data = get_stock_technical_context(ticker)
                tech_context_str = json.dumps(tech_data) if tech_data else ""
                _atr = float(tech_data['atr_pct']) if (tech_data and tech_data.get('atr_pct')) else 0.0
                if _atr > 0:
                    _stop = round(min(2.5, max(1.0, _atr * 1.0)), 2)
                    _tgt = round(min(5.0, max(2.0, _atr * 2.0)), 2)
                elif _require_atr:
                    summary["atr_skipped"] += 1
                    continue
                else:
                    _stop = TRADE_STOP_PCT; _tgt = TRADE_TARGET_PCT
                base_price, current_price_now, _pub = get_signal_prices_for_publication(
                    ticker,
                    news_publication_time
                )
                # Claude's per-ticker confidence is injected as the ensemble's
                # AI vote (m7) via force_precalculated — so NO Gemini call, but
                # the rule models + gate run exactly as for a live signal.
                result = ensemble.predict(
                    headline=headline, ticker=ticker, direction=base_direction,
                    tech_data=tech_data, market_regime=market_regime, db_connect_fn=connect_news_db,
                    api_client=None, model_name=MODEL_NAME, min_score=MIN_CONFIDENCE,
                    get_client_fn=None, precalculated_score=r["quality_score"],
                    catalyst_type=None, news_age_hours=None, force_precalculated=True)
                summary["evaluated"] += 1
                if summary["sample_detail"] is None:
                    summary["sample_detail"] = f"{ticker}: {result.get('detail')} approved={result.get('approved')}"
                if not result['approved']:
                    summary["ensemble_rejected"] += 1
                    continue
                view = 'High Conviction' if result['final_score'] >= 85 else 'Moderate Conviction'
                reason = (f"Ensemble Score: {result['final_score']} ({result['models_agreeing']}/5 models approve). "
                          f"ATR stop: {_stop:.1f}% | target: {_tgt:.1f}%. Stock-pick quality: {r['quality_score']}/100. [Claude-analysed]")
                approved_signals.append((nid, ticker, result['direction'], _tgt, view, reason,
                                         base_price, current_price_now, result['final_score'],
                                         tech_context_str, result['detail'], _pub))
                RECENT_DIRECTIONS.append(result['direction'])
        except Exception:
            summary["errors"] += 1
            continue

    if approved_signals:
        _sigs = approved_signals
        def _insert_signals(conn, c, _s=_sigs):
            for sig in _s:
                news_id, ticker, impact, est_change, view_str, reason_str, bp, cp, conf, tech_ctx, ens_det, created_at_str = sig
                c.execute("""SELECT id, confidence_score, reason, base_price, created_at, impact
                             FROM stock_impact WHERE ticker = ? AND status = 'Active View'
                             ORDER BY created_at DESC LIMIT 1""", (ticker,))
                row = c.fetchone()
                merged = False
                if row:
                    db_id, db_conf, db_reason, db_bp, db_created, db_impact = row
                    try:
                        new_dt = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        new_dt = datetime.now(timezone.utc).replace(tzinfo=None)
                    try:
                        db_dt = datetime.strptime(db_created, '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        db_dt = new_dt
                    if abs((new_dt - db_dt).total_seconds()) <= 86400:
                        is_similar = False
                        if db_bp == 0.0 or bp == 0.0:
                            is_similar = True
                        else:
                            if abs(db_bp - bp) / db_bp <= 0.025:
                                is_similar = True
                        if is_similar:
                            _prior_merges = (db_reason or "").count("[Consensus Boost")
                            try:
                                _boost_schedule = [int(x) for x in os.environ.get("MERGE_BOOST_SCHEDULE", "5,3,2,1").split(",")]
                            except Exception:
                                _boost_schedule = [5, 3, 2, 1]
                            _this_boost = _boost_schedule[min(_prior_merges, len(_boost_schedule) - 1)]
                            _raw_boosted = max(db_conf, conf) + _this_boost
                            _merge_cap = int(os.environ.get("MERGE_CONF_CAP", "80"))
                            _high_base = int(os.environ.get("MERGE_HIGH_BASE", "85"))
                            if not ((db_conf >= _high_base) or (conf >= _high_base)):
                                _raw_boosted = min(_raw_boosted, _merge_cap)
                            boosted_conf = min(99, _raw_boosted)
                            new_view = 'High Conviction' if boosted_conf >= 85 else 'Moderate Conviction'
                            c.execute("SELECT headline FROM news WHERE id = ?", (news_id,))
                            hl_row = c.fetchone()
                            new_hl = hl_row[0] if hl_row else "Consensus News"
                            if impact != db_impact:
                                final_impact = impact if conf > db_conf else db_impact
                                merged_reason = f"{db_reason} | [Consensus Boost ({impact}): '{new_hl}'] {reason_str}"
                            else:
                                final_impact = db_impact
                                merged_reason = f"{db_reason} | [Consensus Boost: '{new_hl}'] {reason_str}"
                            c.execute("""UPDATE stock_impact SET confidence_score = ?, view = ?, reason = ?, impact = ?
                                         WHERE id = ?""", (boosted_conf, new_view, merged_reason, final_impact, db_id))
                            merged = True
                if not merged:
                    c.execute('''INSERT OR IGNORE INTO stock_impact
                        (news_id, ticker, impact, estimated_change_percent, view, reason, base_price, current_price, confidence_score, technical_context, ensemble_detail, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', sig)
        db_write(_insert_signals)
        summary["signals_saved"] = len(approved_signals)

    if screened_ids:
        def _mark(conn, c, _ids=screened_ids):
            ph = ",".join(["?"] * len(_ids))
            c.execute(f"UPDATE news SET ai_status='screened' WHERE id IN ({ph}) AND ai_status='pending'", tuple(_ids))
        db_write(_mark)
        summary["screened"] = len(screened_ids)

    return jsonify({"status": "ok", "summary": summary})


@app.route('/api/debug-sql-runner', methods=['POST'])
def debug_sql_runner():
    # Bug #6 fix: do NOT compare against a hardcoded literal token.
    # Set SQL_RUNNER_SECRET in your .env / Render environment variables.
    secret = os.environ.get("SQL_RUNNER_SECRET")
    token = request.headers.get("X-Alpha-Lens-Token")
    if not secret or token != secret:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.get_json() or {}
    sql = data.get("sql")
    params = data.get("params", ())
    if not sql:
        return jsonify({"error": "No SQL provided"}), 400
        
    try:
        conn = connect_news_db()
        c = conn.cursor()
        c.execute(sql, params)
        
        is_select = sql.strip().upper().startswith("SELECT")
        if is_select:
            rows = c.fetchall()
            # Bug #7 fix: CursorWrapper always has `cursor` as an *attribute* (not just
            # a method), so hasattr(c, 'cursor') is always True and the else branch was
            # dead code.  Access c.cursor.description directly.
            cols = [desc[0] for desc in c.cursor.description]
            result = [dict(zip(cols, r)) for r in rows]
            conn.close()
            return jsonify({"status": "success", "rows": result})
        else:
            conn.commit()
            rowcount = c.rowcount
            conn.close()
            return jsonify({"status": "success", "rowcount": rowcount})
    except Exception as e:
        import traceback
        return jsonify({"status": "error", "error": str(e), "traceback": traceback.format_exc()}), 500


@app.route('/api/admin/prune-news', methods=['POST', 'GET'])
def admin_prune_news():
    """
    On-demand trigger for the aggressive news prune. Uses the same
    SQL_RUNNER_SECRET token as /api/debug-sql-runner.

    POST or GET — header `X-Alpha-Lens-Token: <secret>` OR `?token=<secret>`.
    Returns the row counts removed.
    """
    secret = os.environ.get("SQL_RUNNER_SECRET")
    token = request.headers.get("X-Alpha-Lens-Token") or request.args.get("token")
    if not secret or token != secret:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        result = prune_low_value_news()
        if result is None:
            return jsonify({"status": "error", "error": "prune returned None (db_write likely failed — check server logs)"}), 500
        dn, di = result
        return jsonify({"status": "success", "deleted_news": dn, "deleted_impacts": di})
    except Exception as e:
        import traceback
        return jsonify({"status": "error", "error": str(e), "traceback": traceback.format_exc()}), 500


@app.route('/api/admin/reset-all-news', methods=['POST', 'GET'])
def admin_reset_all_news():
    """
    NUCLEAR — wipes the live news + signal tables completely so we can start
    fresh after a major model/prompt change. Use when you specifically want
    the live data + win-rate stats to begin counting from zero, not to be
    polluted by predictions made under the old (buggy) ensemble.

    What gets wiped:
      • news                  (hot table)
      • stock_impact          (hot signal table)
      • news_archive          (>90d archive)
      • stock_impact_archive  (>90d archive)
      • historical_patterns   (training memory for HistoricalSimilarityModel —
                               wipe so it doesn't keep biasing toward the bad
                               outcomes from the broken ensemble)

    Also clears in-memory caches so the worker restarts with a blank slate
    on its next cycle (SEEN_HEADLINES, RECENT_DIRECTIONS, RECENT_SIGNALS,
    PUBLISHED_TIME_CACHE, ARTICLE_TEXT_CACHE).

    Auth: X-Alpha-Lens-Token: <SQL_RUNNER_SECRET> OR ?token=<secret>
    Safety: also requires ?confirm=YES_WIPE_EVERYTHING — without it returns 400.

    POST is preferred but GET works too (curl convenience).
    """
    secret = os.environ.get("SQL_RUNNER_SECRET")
    token = request.headers.get("X-Alpha-Lens-Token") or request.args.get("token")
    if not secret or token != secret:
        return jsonify({"error": "Unauthorized"}), 401

    confirm = request.args.get("confirm") or (request.get_json(silent=True) or {}).get("confirm")
    if confirm != "YES_WIPE_EVERYTHING":
        return jsonify({
            "error": "Confirmation required",
            "hint": "Pass ?confirm=YES_WIPE_EVERYTHING (or include it in the JSON body) to actually delete.",
        }), 400

    counts = {}
    try:
        def _wipe(conn, c):
            wiped = {}
            for tbl in ("stock_impact", "news_archive", "stock_impact_archive",
                        "historical_patterns", "news"):
                # Get a count first so we can report it back accurately.
                try:
                    c.execute(f"SELECT COUNT(*) FROM {tbl}")
                    row = c.fetchone()
                    wiped[tbl] = int(row[0]) if row else 0
                except Exception:
                    wiped[tbl] = 0
                # Then delete — order matters: stock_impact before news to
                # respect any FK relationship (sqlite uses FK only if
                # enabled, but Postgres enforces if it's wired).
                try:
                    c.execute(f"DELETE FROM {tbl}")
                except Exception as _de:
                    wiped[tbl] = f"error: {_de}"
            return wiped

        counts = db_write(_wipe) or {}

        # Reset in-memory state so the live worker doesn't keep stale
        # headlines / signals / directions in its dedup + bias structures.
        try:
            SEEN_HEADLINES.clear()
        except Exception:
            pass
        try:
            RECENT_DIRECTIONS.clear()
        except Exception:
            pass
        try:
            RECENT_SIGNALS.clear()
        except Exception:
            pass
        try:
            PUBLISHED_TIME_CACHE.clear()
        except Exception:
            pass
        try:
            ARTICLE_TEXT_CACHE.clear()
        except Exception:
            pass

        return jsonify({
            "status": "success",
            "wiped_rows": counts,
            "in_memory_reset": [
                "SEEN_HEADLINES", "RECENT_DIRECTIONS", "RECENT_SIGNALS",
                "PUBLISHED_TIME_CACHE", "ARTICLE_TEXT_CACHE",
            ],
            "next_cycle_starts_at": "within ~3-5 minutes (next ai_news_worker tick)",
        })
    except Exception as e:
        import traceback
        return jsonify({
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc(),
            "wiped_so_far": counts,
        }), 500


def db_sync_worker():
    """
    Background worker that runs periodically to sync all data from cloud PostgreSQL
    to local SQLite databases. This ensures a 100% complete and up-to-date local
    copy of all data is preserved.

    T2.10: When running on Render itself, the local SQLite file lives on
    ephemeral disk and gets wiped on every redeploy — so syncing Postgres
    *back* into it is pointless and produces minute-by-minute "no such
    table: news" errors in the logs. Skip the worker on Render. Devs running
    locally with DATABASE_URL still get the cloud->local sync.
    """
    import sqlite3
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("   [DB SYNC] No DATABASE_URL found. Running in local SQLite mode; sync worker exiting.", flush=True)
        return

    # Render auto-sets RENDER_EXTERNAL_URL / RENDER_EXTERNAL_HOSTNAME / RENDER
    # on every service. Any of them indicates we're running on Render itself.
    if (os.environ.get("RENDER")
            or os.environ.get("RENDER_EXTERNAL_URL")
            or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
            or os.environ.get("DB_SYNC_DISABLED", "").lower() in ("1", "true", "yes")):
        print("   [DB SYNC] Running on Render (ephemeral disk) — cloud->local sync not useful here. Worker exiting.", flush=True)
        return

    print("   [DB SYNC] Synchronizer thread started.", flush=True)
    
    while True:
        try:
            # Sync every 60 seconds
            time.sleep(60)
            
            # Establish connections
            # 1. Cloud PostgreSQL
            try:
                pg_conn = connect_postgres_db(db_url)
                pg_cur = pg_conn.cursor()
            except Exception as e:
                print(f"   [DB SYNC] Failed to connect to cloud PostgreSQL: {e}", flush=True)
                continue
                
            # 2. Local SQLite News DB
            try:
                lite_news_conn = sqlite3.connect(_NEWS_DB, timeout=30.0)
                lite_news_cur = lite_news_conn.cursor()
            except Exception as e:
                print(f"   [DB SYNC] Failed to open local news SQLite: {e}", flush=True)
                pg_conn.close()
                continue
                
            # 3. Local SQLite Users DB
            try:
                lite_users_conn = sqlite3.connect(_USERS_DB, timeout=10.0)
                lite_users_cur = lite_users_conn.cursor()
            except Exception as e:
                print(f"   [DB SYNC] Failed to open local users SQLite: {e}", flush=True)
                lite_news_conn.close()
                pg_conn.close()
                continue

            # ----------------------------------------------------
            # 1. Sync Table: news
            # ----------------------------------------------------
            lite_news_cur.execute("SELECT COALESCE(MAX(id), 0) FROM news")
            max_news_id = lite_news_cur.fetchone()[0]
            
            pg_cur.execute("""
                SELECT id, headline, news_time, aam_janta_translation, macro_pathway, created_at, category, body
                FROM news WHERE id > %s ORDER BY id ASC
            """, (max_news_id,))
            new_news_rows = pg_cur.fetchall()

            if new_news_rows:
                lite_news_cur.executemany("""
                    INSERT OR IGNORE INTO news (id, headline, news_time, aam_janta_translation, macro_pathway, created_at, category, body)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, new_news_rows)
                lite_news_conn.commit()
                print(f"   [DB SYNC] Synced {len(new_news_rows)} new news records.", flush=True)
                
            # Also sync news translation updates (Phase 2 updates)
            lite_news_cur.execute("SELECT id FROM news WHERE aam_janta_translation IS NULL")
            null_trans_ids = [r[0] for r in lite_news_cur.fetchall()]
            if null_trans_ids:
                chunk_size = 100
                for idx in range(0, len(null_trans_ids), chunk_size):
                    chunk = null_trans_ids[idx:idx+chunk_size]
                    pg_cur.execute("""
                        SELECT id, aam_janta_translation, macro_pathway 
                        FROM news WHERE id = ANY(%s) AND aam_janta_translation IS NOT NULL
                    """, (chunk,))
                    updated_trans_rows = pg_cur.fetchall()
                    if updated_trans_rows:
                        for row in updated_trans_rows:
                            lite_news_cur.execute("""
                                UPDATE news SET aam_janta_translation = ?, macro_pathway = ?
                                WHERE id = ?
                            """, (row[1], row[2], row[0]))
                        lite_news_conn.commit()
                        print(f"   [DB SYNC] Updated {len(updated_trans_rows)} news translations/pathways.", flush=True)

            # ----------------------------------------------------
            # 2. Sync Table: stock_universe
            # ----------------------------------------------------
            pg_cur.execute("SELECT ticker, symbol, name, exchange, source, updated_at FROM stock_universe")
            univ_rows = pg_cur.fetchall()
            if univ_rows:
                lite_news_cur.executemany("""
                    INSERT OR REPLACE INTO stock_universe (ticker, symbol, name, exchange, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, univ_rows)
                lite_news_conn.commit()

            # ----------------------------------------------------
            # 3. Sync Table: stock_impact
            # ----------------------------------------------------
            lite_news_cur.execute("SELECT COALESCE(MAX(id), 0) FROM stock_impact")
            max_impact_id = lite_news_cur.fetchone()[0]
            
            pg_cur.execute("""
                SELECT id, news_id, ticker, impact, estimated_change_percent, view, reason, 
                       base_price, current_price, status, created_at, confidence_score, 
                       technical_context, ensemble_detail
                FROM stock_impact WHERE id > %s ORDER BY id ASC
            """, (max_impact_id,))
            new_impact_rows = pg_cur.fetchall()
            if new_impact_rows:
                lite_news_cur.executemany("""
                    INSERT OR IGNORE INTO stock_impact (
                        id, news_id, ticker, impact, estimated_change_percent, view, reason, 
                        base_price, current_price, status, created_at, confidence_score, 
                        technical_context, ensemble_detail
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, new_impact_rows)
                lite_news_conn.commit()
                print(f"   [DB SYNC] Synced {len(new_impact_rows)} new stock impact signals.", flush=True)

            # Also update existing active views from PostgreSQL
            lite_news_cur.execute("SELECT id FROM stock_impact WHERE status = 'Active View'")
            active_impact_ids = [r[0] for r in lite_news_cur.fetchall()]
            if active_impact_ids:
                chunk_size = 100
                for idx in range(0, len(active_impact_ids), chunk_size):
                    chunk = active_impact_ids[idx:idx+chunk_size]
                    pg_cur.execute("""
                        SELECT id, current_price, status, base_price, estimated_change_percent, view, reason, confidence_score
                        FROM stock_impact WHERE id = ANY(%s)
                    """, (chunk,))
                    updated_impacts = pg_cur.fetchall()
                    if updated_impacts:
                        for row in updated_impacts:
                            lite_news_cur.execute("""
                                UPDATE stock_impact SET current_price = ?, status = ?, base_price = ?, 
                                                        estimated_change_percent = ?, view = ?, reason = ?, 
                                                        confidence_score = ?
                                WHERE id = ?
                            """, (row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[0]))
                        lite_news_conn.commit()
                        print(f"   [DB SYNC] Refreshed status of {len(updated_impacts)} active stock signals.", flush=True)

            # ----------------------------------------------------
            # 4. Sync Table: historical_patterns
            # ----------------------------------------------------
            lite_news_cur.execute("SELECT COALESCE(MAX(id), 0) FROM historical_patterns")
            max_pattern_id = lite_news_cur.fetchone()[0]
            
            pg_cur.execute("""
                SELECT id, headline, ticker, direction, outcome, change_pct, created_at 
                FROM historical_patterns WHERE id > %s ORDER BY id ASC
            """, (max_pattern_id,))
            new_pattern_rows = pg_cur.fetchall()
            if new_pattern_rows:
                lite_news_cur.executemany("""
                    INSERT OR IGNORE INTO historical_patterns (id, headline, ticker, direction, outcome, change_pct, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, new_pattern_rows)
                lite_news_conn.commit()
                print(f"   [DB SYNC] Synced {len(new_pattern_rows)} new historical pattern records.", flush=True)

            # ----------------------------------------------------
            # 5. Sync Table: users
            # ----------------------------------------------------
            lite_users_cur.execute("SELECT COALESCE(MAX(id), 0) FROM users")
            max_user_id = lite_users_cur.fetchone()[0]
            
            pg_cur.execute("""
                SELECT id, email, password FROM users WHERE id > %s ORDER BY id ASC
            """, (max_user_id,))
            new_user_rows = pg_cur.fetchall()
            if new_user_rows:
                lite_users_cur.executemany("""
                    INSERT OR IGNORE INTO users (id, email, password) VALUES (?, ?, ?)
                """, new_user_rows)
                lite_users_conn.commit()
                print(f"   [DB SYNC] Synced {len(new_user_rows)} new users.", flush=True)

            # Clean close
            lite_users_conn.close()
            lite_news_conn.close()
            pg_conn.close()
            
        except Exception as ex:
            print(f"   [DB SYNC ERROR] An error occurred in database synchronization loop: {ex}", flush=True)
            import traceback
            traceback.print_exc()


def archival_worker():
    """
    T3.16: Move news + stock_impact rows older than ARCHIVE_AFTER_DAYS into
    the *_archive tables. Keeps the hot tables small so all the indexes stay
    cache-friendly even years from now.

    Reversible — rows are MOVED (insert+delete in a transaction), not
    destroyed. To restore a date range, just INSERT INTO news SELECT * FROM
    news_archive WHERE created_at BETWEEN ...

    The HistoricalSimilarityModel reads from historical_patterns which is
    deliberately untouched — that's the AI's distilled long-term memory.
    """
    ARCHIVE_AFTER_DAYS = int(os.environ.get("ARCHIVE_AFTER_DAYS", "90"))
    RUN_EVERY_HOURS    = int(os.environ.get("ARCHIVE_RUN_EVERY_HOURS", "24"))

    if os.environ.get("ARCHIVE_DISABLED", "").lower() in ("1", "true", "yes"):
        print("[ARCHIVE] Worker disabled via ARCHIVE_DISABLED env var.", flush=True)
        return

    print(f"[ARCHIVE] Worker started — moves rows >{ARCHIVE_AFTER_DAYS} days every {RUN_EVERY_HOURS}h.", flush=True)

    # Small initial delay so the rest of startup finishes first
    time.sleep(120)

    while True:
        try:
            _heartbeat("archival", last_cycle_started_at=time.time())
            cutoff = (datetime.now(timezone.utc) - timedelta(days=ARCHIVE_AFTER_DAYS)) \
                        .strftime('%Y-%m-%d %H:%M:%S')

            conn = connect_news_db()
            c = conn.cursor()

            # ── Move stock_impact rows first (referential safety: they reference news) ──
            c.execute("""
                INSERT INTO stock_impact_archive
                    (id, news_id, ticker, impact, estimated_change_percent, view, reason,
                     base_price, current_price, status, created_at, confidence_score,
                     technical_context, ensemble_detail)
                SELECT id, news_id, ticker, impact, estimated_change_percent, view, reason,
                       base_price, current_price, status, created_at, confidence_score,
                       technical_context, ensemble_detail
                FROM stock_impact
                WHERE created_at < ?
            """, (cutoff,))
            impact_moved = c.cursor.rowcount if hasattr(c, 'cursor') else 0

            c.execute("DELETE FROM stock_impact WHERE created_at < ?", (cutoff,))

            # ── Now news rows ──
            c.execute("""
                INSERT INTO news_archive
                    (id, headline, news_time, aam_janta_translation, macro_pathway,
                     category, created_at, body)
                SELECT id, headline, news_time, aam_janta_translation, macro_pathway,
                       category, created_at, body
                FROM news
                WHERE created_at < ?
            """, (cutoff,))
            news_moved = c.cursor.rowcount if hasattr(c, 'cursor') else 0

            c.execute("DELETE FROM news WHERE created_at < ?", (cutoff,))

            conn.commit()
            conn.close()

            if news_moved or impact_moved:
                print(f"[ARCHIVE] Moved {news_moved} news + {impact_moved} stock_impact rows to archive (older than {cutoff}).", flush=True)
            else:
                print(f"[ARCHIVE] No rows older than {ARCHIVE_AFTER_DAYS}d to archive.", flush=True)

            _heartbeat("archival",
                       last_cycle_finished_at=time.time(),
                       last_news_moved=int(news_moved or 0),
                       last_impact_moved=int(impact_moved or 0),
                       cycles_completed=WORKER_HEARTBEAT["archival"].get("cycles_completed", 0) + 1)
        except Exception as e:
            print(f"[ARCHIVE] Error in archival pass: {e}", flush=True)
            _heartbeat("archival", last_error=str(e)[:200], last_error_at=time.time())

        # Sleep until next run
        time.sleep(RUN_EVERY_HOURS * 3600)


def prune_low_value_news():
    """
    Keeps the hot `news` table bounded so /api/news/all stays snappy and the
    "All News" tab doesn't choke the browser.

    Deletes a news row when it has NO stock_impact (signal-less) AND is not
    pending AND is EITHER older than NEWS_FEED_RETENTION_DAYS (5d) OR beyond the
    newest NEWS_FEED_MAX_ROWS (800) signal-less rows. So the feed is capped on
    both age and count.

    News referenced by a signal is EXEMPT and never deleted here — it is kept
    with the signal so the signal terminal can show its headline for the full
    90-day window, and archival_worker later MOVES both into *_archive together.

    NOT REVERSIBLE for the signal-less rows it drops (pure noise the AI never
    turned into a signal). The reversible 90-day move to *_archive is handled by
    archival_worker.

    Returns (deleted_news_count, deleted_impact_count).
    """
    # Per-pass safety cap so a large backlog clears over a few hourly passes
    # rather than one giant transaction.
    PRUNE_BATCH_LIMIT = int(os.environ.get("PRUNE_BATCH_LIMIT", "5000"))
    age_cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=NEWS_FEED_RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')

    def _prune(conn, c):
        deleted_news = 0
        deleted_impacts = 0

        # Prune signal-less, non-pending news that is EITHER older than
        # NEWS_FEED_RETENTION_DAYS (5d) *or* beyond the newest NEWS_FEED_MAX_ROWS
        # (800) rows — so the "All News" feed stays bounded on both age and count.
        # News referenced by a stock_impact (signal) is EXEMPT (outer NOT EXISTS),
        # so we never blank out a signal's headline; archival_worker later moves
        # those news+signal rows to *_archive together at 90 days.
        # Pending news (saved during AI downtime, awaiting rescreen) is also exempt.
        c.execute("""
            SELECT n.id FROM news n
            WHERE NOT EXISTS (
                SELECT 1 FROM stock_impact si WHERE si.news_id = n.id
            )
              AND (n.ai_status IS NULL OR n.ai_status != 'pending')
              AND (
                    n.created_at < ?
                    OR n.id NOT IN (
                        SELECT id FROM news
                        WHERE NOT EXISTS (
                            SELECT 1 FROM stock_impact si WHERE si.news_id = news.id
                        )
                          AND (ai_status IS NULL OR ai_status != 'pending')
                        ORDER BY created_at DESC
                        LIMIT ?
                    )
              )
            ORDER BY n.created_at ASC
            LIMIT ?
        """, (age_cutoff, NEWS_FEED_MAX_ROWS, PRUNE_BATCH_LIMIT))
        ids_p1 = [r[0] for r in c.fetchall()]
        if ids_p1:
            placeholders = ','.join(['?'] * len(ids_p1))
            c.execute(f"DELETE FROM news WHERE id IN ({placeholders})", tuple(ids_p1))
            deleted_news += len(ids_p1)



        return deleted_news, deleted_impacts

    result = db_write(_prune)
    if result:
        dn, di = result
        if dn or di:
            print(f"[PRUNE] Removed {dn} news + {di} stock_impact rows (oldest dead/stale signals).", flush=True)
        else:
            print("[PRUNE] No prunable rows found this pass.", flush=True)
    return result


def news_prune_worker():
    """
    Background worker that runs prune_low_value_news() on a fixed cadence so
    the "All News" view stays bounded between archival passes.

    Defaults: prune once on startup (after a short warm-up), then every hour.
    Tunable via PRUNE_RUN_EVERY_MIN.
    """
    if os.environ.get("PRUNE_DISABLED", "").lower() in ("1", "true", "yes"):
        print("[PRUNE] Worker disabled via PRUNE_DISABLED env var.", flush=True)
        return

    RUN_EVERY_MIN = int(os.environ.get("PRUNE_RUN_EVERY_MIN", "60"))
    print(f"[PRUNE] Worker started — runs every {RUN_EVERY_MIN}m.", flush=True)

    # Initial warm-up delay so the rest of startup finishes first; then a single
    # eager pass to clear any existing backlog from a fresh deploy.
    time.sleep(45)
    try:
        _heartbeat("news_prune", last_cycle_started_at=time.time())
        _r = prune_low_value_news()
        _heartbeat("news_prune",
                   last_cycle_finished_at=time.time(),
                   last_pruned_count=int((_r or {}).get("deleted", 0)),
                   cycles_completed=WORKER_HEARTBEAT["news_prune"].get("cycles_completed", 0) + 1)
    except Exception as e:
        print(f"[PRUNE] Startup pass failed: {e}", flush=True)
        _heartbeat("news_prune", last_error=str(e)[:200], last_error_at=time.time())

    while True:
        time.sleep(RUN_EVERY_MIN * 60)
        try:
            _heartbeat("news_prune", last_cycle_started_at=time.time())
            _r = prune_low_value_news() or (0, 0)
            _dn, _di = (_r if isinstance(_r, tuple) else (0, 0))
            _heartbeat("news_prune",
                       last_cycle_finished_at=time.time(),
                       last_pruned_count=int(_dn) + int(_di),
                       cycles_completed=WORKER_HEARTBEAT["news_prune"].get("cycles_completed", 0) + 1)
        except Exception as e:
            print(f"[PRUNE] Pass failed: {e}", flush=True)
            _heartbeat("news_prune", last_error=str(e)[:200], last_error_at=time.time())


def eval_labeler_worker():
    """Background labeler for the eval loop — fills outcomes for logged
    decisions (kept AND rejected) once the horizon has elapsed. Runs every
    EVAL_LABEL_EVERY_HOURS (default 6h). Append-only: only UPDATEs the outcome
    columns of signal_eval_log, never deletes a row."""
    if os.environ.get("EVAL_LABELER_DISABLED", "").lower() in ("1", "true", "yes"):
        print("[EVAL] Labeler disabled via EVAL_LABELER_DISABLED.", flush=True)
        return
    every_h = int(os.environ.get("EVAL_LABEL_EVERY_HOURS", "6"))
    print(f"[EVAL] Labeler started — runs every {every_h}h.", flush=True)
    time.sleep(90)  # warm-up so the rest of startup finishes first
    while True:
        try:
            _heartbeat("eval_labeler", last_cycle_started_at=time.time())
            n = eval_loop.label_pending()
            _heartbeat("eval_labeler", last_cycle_finished_at=time.time(),
                       last_labeled_count=n,
                       cycles_completed=WORKER_HEARTBEAT.get("eval_labeler", {}).get("cycles_completed", 0) + 1)
            print(f"[EVAL] Labeled {n} pending rows.", flush=True)
        except Exception as e:
            print(f"[EVAL] Labeler pass failed: {e}", flush=True)
            _heartbeat("eval_labeler", last_error=str(e)[:200], last_error_at=time.time())
        time.sleep(every_h * 3600)


@app.route('/api/eval-report', methods=['GET'])
def api_eval_report():
    """Forward shadow-ledger report: win rate + expectancy for approved vs
    rejected signals (the counterfactual) and per-disposition. Append-only data."""
    try:
        return jsonify(eval_loop.report())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/label-eval', methods=['POST'])
def api_label_eval():
    """Manually trigger outcome labelling for the eval loop (the background
    worker also does this every few hours). Auth: X-Alpha-Lens-Token."""
    secret = os.environ.get("SQL_RUNNER_SECRET")
    token = request.headers.get("X-Alpha-Lens-Token") or request.args.get("token")
    if not secret or token != secret:
        return jsonify({"error": "unauthorized"}), 401
    try:
        limit = int(request.args.get("limit", "500"))
        n = eval_loop.label_pending(limit=limit)
        return jsonify({"labeled": n})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def start_background_workers():
    engine_thread = threading.Thread(target=ai_news_worker, daemon=True)
    engine_thread.start()

    yf_thread = threading.Thread(target=yfinance_worker, daemon=True)
    yf_thread.start()

    # T3.16: archival worker
    arch_thread = threading.Thread(target=archival_worker, name="ArchivalWorker", daemon=True)
    arch_thread.start()

    # Aggressive prune worker — keeps /api/news/all bounded so the "All News"
    # tab doesn't choke the browser on thousands of low-value cards.
    prune_thread = threading.Thread(target=news_prune_worker, name="NewsPruneWorker", daemon=True)
    prune_thread.start()

    # Eval-loop labeler — fills outcomes for the forward shadow-ledger so each
    # filter/weight becomes measurable. Append-only (never deletes).
    eval_thread = threading.Thread(target=eval_labeler_worker, name="EvalLabeler", daemon=True)
    eval_thread.start()

    # MacroDataTracker warm-up + quantitative shock detector.
    macro_warm = threading.Thread(target=_macro_data_warmer, name="MacroWarmer", daemon=True)
    macro_warm.start()
    macro_shock = threading.Thread(target=macro_shock_worker, name="MacroShockWorker", daemon=True)
    macro_shock.start()

    # One-shot seed of the curated calendar. INSERT OR IGNORE keys means it's
    # safe to call on every startup — only new rows get added.
    try:
        seed_calendar_events(force=False)
    except Exception as _ce:
        print(f"[CALENDAR] Startup seed failed (non-fatal): {_ce}", flush=True)

    # Start the cloud-to-local database synchronizer if pointing to cloud
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        sync_thread = threading.Thread(target=db_sync_worker, daemon=True)
        sync_thread.start()
        print("   [DB SYNC] Launched background cloud-to-local database synchronizer", flush=True)

    # Keep-alive ping — only runs on Render (when RENDER env var is set)
    # Pings the app itself every 10 minutes to prevent free tier spin-down
    render_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if render_url:
        if not render_url.startswith("http"):
            render_url = "https://" + render_url
        def _keep_alive():
            import urllib.request
            while True:
                try:
                    time.sleep(600)  # 10 minutes
                    urllib.request.urlopen(f"{render_url}/api/me", timeout=10)
                    print(f"   [KEEP-ALIVE] Pinged {render_url} to prevent spin-down", flush=True)
                except Exception as e:
                    print(f"   [KEEP-ALIVE] Ping failed (non-critical): {e}", flush=True)
        ka_thread = threading.Thread(target=_keep_alive, daemon=True)
        ka_thread.start()
        print(f"   [KEEP-ALIVE] Started — pinging {render_url} every 10 min", flush=True)

    return engine_thread, yf_thread

# ── T2.9: Bootstrap background workers ──
# Extracted from `if __name__ == '__main__':` so both startup paths work:
#   (a) Flask dev server: `python backend/app.py` (local dev)
#   (b) Gunicorn import: `gunicorn ... backend.app:app` (production on Render)
# In (b), the __main__ block never runs because the module is imported, not
# executed — so we run the bootstrap at module level with an idempotency guard.
_alpha_bootstrapped = False
_alpha_bootstrap_lock = threading.Lock()

def _bootstrap_workers():
    """Start the repair + ai_news + yfinance + db_sync threads exactly once."""
    global _alpha_bootstrapped
    with _alpha_bootstrap_lock:
        if _alpha_bootstrapped:
            return
        _alpha_bootstrapped = True

    # Warn if DATABASE_URL is unset in production
    if not os.environ.get("DATABASE_URL"):
        is_prod = os.environ.get("FLASK_ENV", "production").lower() not in ("development", "dev", "test")
        if is_prod:
            print("\n" + "="*80)
            print("   [WARNING] DATABASE_URL is not set in production environment!")
            print("   The system will silently fall back to local SQLite: news_cache.db")
            print("="*80 + "\n", flush=True)

    if os.environ.get("ALPHA_LENS_SKIP_WORKERS", "").lower() in ("1", "true", "yes"):
        print("[SYSTEM] Background workers skipped (ALPHA_LENS_SKIP_WORKERS set).", flush=True)
        return

    if os.environ.get("ALPHA_LENS_SKIP_AUTO_REPAIR", "").lower() not in ("1", "true", "yes"):
        def _run_repair():
            try:
                repair_existing_signal_statuses(days=14)
            except Exception as e:
                print(f"[REPAIR ERROR] Failed to run repair: {e}", flush=True)
        threading.Thread(target=_run_repair, name="SignalRepairThread", daemon=True).start()

    start_background_workers()


# Auto-bootstrap on module import so Gunicorn / WSGI servers fire the workers.
# Skipped if explicitly disabled via env (e.g., during tests / pytest collection
# or when running --workers-only via the CLI which handles bootstrap itself).
if os.environ.get("ALPHA_LENS_SKIP_AUTO_BOOTSTRAP", "").lower() not in ("1", "true", "yes"):
    _bootstrap_workers()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Alpha Lens backend startup mode')
    parser.add_argument('--workers-only', action='store_true', help='Run background workers without launching the Flask UI')
    parser.add_argument('--skip-workers', action='store_true', help='Do not start background workers')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 5000)), help='Port for the Flask UI')
    args = parser.parse_args()

    if args.workers_only:
        print("[SYSTEM] Worker-only mode active. Flask UI is disabled.")
        # Workers already started by _bootstrap_workers() at import time.
        # Just keep the process alive.
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass
    else:
        # Dev-server path. Workers already booted at import time above.
        debug_mode = os.environ.get("FLASK_ENV") == "development"
        app.run(debug=debug_mode, host='0.0.0.0', port=args.port, threaded=True, use_reloader=False)

