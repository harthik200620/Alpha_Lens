import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
print("[DEBUG] App startup beginning...", flush=True)
from flask import Flask, render_template, request, jsonify, session, make_response
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
ARTICLE_TEXT_CACHE = {}

# Global write lock — ensures only one thread writes to SQLite at a time.
# Reads do NOT need this lock (WAL mode allows concurrent reads).
DB_WRITE_LOCK = threading.Lock()

from datetime import datetime, timedelta, timezone
from technical_analysis import (
    get_stock_technical_context,
    format_technical_context_for_prompt,
    get_market_regime
)
from prediction_models import EnsemblePredictor

_TICKER_CACHE = {}
_TICKER_CACHE_TIME = {}

def get_robust_price(ticker, market_open=None):
    """
    Fetches live/closing price with a 30-second in-memory cache.
    Uses Angel One SmartAPI (exchange-sourced LTP) with Yahoo Finance fallback.
    Caches both successes (real price) and failures (0.0 sentinel) for 30s.
    """
    global _TICKER_CACHE, _TICKER_CACHE_TIME
    now = time.time()

    if market_open is None:
        market_open = is_market_open()

    # Return cached value if still fresh (30s window)
    if ticker in _TICKER_CACHE and (now - _TICKER_CACHE_TIME.get(ticker, 0)) < 30:
        return _TICKER_CACHE[ticker]

    lp, _ = yf.get_ltp(ticker)
    price = round(float(lp), 2) if (lp and lp > 0) else 0.0

    _TICKER_CACHE[ticker] = price
    _TICKER_CACHE_TIME[ticker] = now
    return price




# NSE market holidays for 2026
NSE_HOLIDAYS_2026 = {
    (1, 26),   # Republic Day
    (2, 19),   # Chhatrapati Shivaji Maharaj Jayanti
    (3, 25),   # Holi
    (4, 2),    # Ram Navami / Good Friday (tentative)
    (4, 10),   # Good Friday
    (4, 14),   # Dr. Ambedkar Jayanti / Mahavir Jayanti
    (5, 1),    # Maharashtra Day / Labour Day
    (6, 2),    # Eid ul Adha
    (8, 15),   # Independence Day
    (8, 27),   # Ganesh Chaturthi
    (10, 2),   # Gandhi Jayanti
    (10, 21),  # Dussehra
    (10, 22),  # Dussehra (additional)
    (11, 5),   # Diwali - Laxmi Puja
    (11, 6),   # Diwali (Balipratipada)
    (12, 25),  # Christmas
}

def is_market_open():
    """Return True if Indian stock market is currently open (Mon-Fri, 9:15 AM – 3:30 PM IST, non-holiday)."""
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    weekday = now_ist.weekday()  # 0=Mon … 4=Fri
    if weekday >= 5:
        return False
    # Check NSE holiday calendar
    if (now_ist.month, now_ist.day) in NSE_HOLIDAYS_2026:
        return False
    t = now_ist.hour * 60 + now_ist.minute  # minutes since midnight
    return (9 * 60 + 15) <= t <= (15 * 60 + 30)


def published_after_market_hours(dt_str):
    """Return True when the provided news date occurs outside NSE trading hours."""
    if not dt_str:
        return False
    try:
        dt = parsedate_to_datetime(dt_str)
        if dt is None:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ist = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
        weekday = ist.weekday()
        if weekday >= 5:
            return True
        minutes = ist.hour * 60 + ist.minute
        return minutes < (9 * 60 + 15) or minutes > (15 * 60 + 30)
    except Exception:
        return False


def has_market_traded_since(published_dt_str):
    """
    Returns True if at least one market session has occurred or is currently occurring
    since the news was published.
    """
    if not published_dt_str:
        return True
    try:
        if isinstance(published_dt_str, datetime):
            dt = published_dt_str
        elif ',' in published_dt_str:
            dt = parsedate_to_datetime(published_dt_str)
        else:
            # SQL format 'YYYY-MM-DD HH:MM:SS' is UTC
            dt = datetime.strptime(published_dt_str, '%Y-%m-%d %H:%M:%S')
            dt = dt.replace(tzinfo=timezone.utc)
            
        if dt is None:
            return True
            
        ist = timezone(timedelta(hours=5, minutes=30))
        published_ist = dt.astimezone(ist)
        now_ist = datetime.now(ist)
        
        # If published in the future
        if published_ist >= now_ist:
            return False
            
        def is_trading_day(d):
            if d.weekday() >= 5:
                return False
            return (d.month, d.day) not in NSE_HOLIDAYS_2026

        curr_date = published_ist.date()
        end_date = now_ist.date()
        
        while curr_date <= end_date:
            if is_trading_day(curr_date):
                market_start = datetime.combine(curr_date, datetime.min.time()).replace(tzinfo=ist) + timedelta(hours=9, minutes=15)
                market_end = datetime.combine(curr_date, datetime.min.time()).replace(tzinfo=ist) + timedelta(hours=15, minutes=30)
                
                overlap_start = max(published_ist, market_start)
                overlap_end = min(now_ist, market_end)
                if overlap_start < overlap_end:
                    return True
            curr_date += timedelta(days=1)
            
        return False
    except Exception as e:
        print(f"Error in has_market_traded_since: {e}")
        return True


app = Flask(__name__, template_folder='../frontend', static_folder='../frontend', static_url_path='/')
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.jinja_env.auto_reload = True

# Minimum AI confidence to accept a prediction
MIN_CONFIDENCE = 50

# Signal evaluation rules used by startup repair and the live price worker.
TRADE_TARGET_PCT = 2.0
TRADE_STOP_PCT = 1.0

import performance_report

# In-memory store for OTPs
OTP_STORE = {}
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL")
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")

# Use absolute paths so the server works from any working directory
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
def choose_news_db_path():
    candidates = [
        os.path.join(_APP_DIR, 'news_cache.db'),
        os.path.abspath(os.path.join(_APP_DIR, '..', 'news_cache.db')),
    ]
    for path in candidates:
        try:
            conn = sqlite3.connect(path, timeout=5.0)
            conn.execute("SELECT 1")
            conn.close()
            return path
        except Exception as e:
            print(f"   [DB] Cannot open {path}: {e}")
    return candidates[0]

_NEWS_DB  = choose_news_db_path()
_USERS_DB = os.path.join(_APP_DIR, 'users.db')

class CursorWrapper:
    def __init__(self, cursor, is_postgres=False):
        self.cursor = cursor
        self.is_postgres = is_postgres
        self._lastrowid = None

    @property
    def lastrowid(self):
        return self._lastrowid

    def execute(self, sql, params=None):
        if not self.is_postgres:
            self.cursor.execute(sql, params or ())
            self._lastrowid = self.cursor.lastrowid
            return self

        # PostgreSQL Translation Layer
        sql_translated = sql.replace('?', '%s')
        sql_upper = sql_translated.upper()

        # 1. Drop PRAGMA commands
        if sql_upper.strip().startswith('PRAGMA'):
            return self

        # 2. Translate 'INTEGER PRIMARY KEY AUTOINCREMENT' to 'SERIAL PRIMARY KEY'
        if 'INTEGER PRIMARY KEY AUTOINCREMENT' in sql_upper:
            import re
            sql_translated = re.sub(
                r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT',
                'SERIAL PRIMARY KEY',
                sql_translated,
                flags=re.IGNORECASE
            )

        # 3. Translate 'INSERT OR IGNORE INTO' to 'INSERT INTO ... ON CONFLICT DO NOTHING'
        if 'INSERT OR IGNORE INTO' in sql_upper:
            import re
            sql_translated = re.sub(
                r'INSERT\s+OR\s+IGNORE\s+INTO',
                'INSERT INTO',
                sql_translated,
                flags=re.IGNORECASE
            )
            sql_translated = sql_translated.rstrip()
            if sql_translated.endswith(';'):
                sql_translated = sql_translated[:-1] + ' ON CONFLICT DO NOTHING;'
            else:
                sql_translated = sql_translated + ' ON CONFLICT DO NOTHING'

        # 4. Handle lastrowid via RETURNING id for INSERT queries (except stock_universe)
        sql_translated_upper = sql_translated.upper()
        is_insert = sql_translated_upper.strip().startswith('INSERT')
        
        if is_insert and ('RETURNING' not in sql_translated_upper) and ('STOCK_UNIVERSE' not in sql_translated_upper):
            sql_translated = sql_translated.rstrip()
            if sql_translated.endswith(';'):
                sql_translated = sql_translated[:-1] + ' RETURNING id;'
            else:
                sql_translated = sql_translated + ' RETURNING id'
            
            if params:
                self.cursor.execute(sql_translated, params)
            else:
                self.cursor.execute(sql_translated)
            try:
                row = self.cursor.fetchone()
                if row:
                    self._lastrowid = row[0]
            except Exception:
                self._lastrowid = None
        else:
            if params:
                self.cursor.execute(sql_translated, params)
            else:
                self.cursor.execute(sql_translated)
            
        return self

    def executemany(self, sql, seq_of_parameters):
        if not self.is_postgres:
            self.cursor.executemany(sql, seq_of_parameters)
            return self

        sql_translated = sql.replace('?', '%s')
        self.cursor.executemany(sql_translated, seq_of_parameters)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def __iter__(self):
        return iter(self.cursor)

    def close(self):
        self.cursor.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class ConnectionWrapper:
    def __init__(self, conn, is_postgres=False):
        self.conn = conn
        self.is_postgres = is_postgres
        self._row_factory = None

    @property
    def row_factory(self):
        return self._row_factory

    @row_factory.setter
    def row_factory(self, val):
        self._row_factory = val
        if not self.is_postgres:
            self.conn.row_factory = val

    def cursor(self):
        if self.is_postgres:
            if self._row_factory is not None:
                import psycopg2.extras
                cursor = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            else:
                cursor = self.conn.cursor()
        else:
            cursor = self.conn.cursor()
        return CursorWrapper(cursor, is_postgres=self.is_postgres)

    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self.conn.commit()

    def rollback(self):
        try:
            self.conn.rollback()
        except Exception:
            pass

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)


def connect_postgres_db(db_url):
    import psycopg2
    # Ensure connections have a reasonable timeout
    conn = psycopg2.connect(db_url, connect_timeout=10)
    return ConnectionWrapper(conn, is_postgres=True)


def connect_news_db():
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            return connect_postgres_db(db_url)
        except Exception as e:
            print(f"   [DB] Failed to connect to PostgreSQL: {e}. Falling back to SQLite...")
    
    conn = sqlite3.connect(_NEWS_DB, timeout=30.0, check_same_thread=False)
    return ConnectionWrapper(conn, is_postgres=False)


def connect_users_db():
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            return connect_postgres_db(db_url)
        except Exception as e:
            print(f"   [DB] Failed to connect to PostgreSQL for users: {e}. Falling back to SQLite...")
            
    conn = sqlite3.connect(_USERS_DB, timeout=10.0)
    return ConnectionWrapper(conn, is_postgres=False)


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


def db_write(fn, retries=3, delay=1.0):
    """
    Execute a write operation (fn) under DB_WRITE_LOCK with automatic retry.
    fn receives (conn, cursor) and should NOT call commit/close.
    Returns the value returned by fn, or None on failure.
    """
    for attempt in range(retries):
        with DB_WRITE_LOCK:
            try:
                conn = connect_news_db()
                c = conn.cursor()
                result = fn(conn, c)
                conn.commit()
                conn.close()
                return result
            except Exception as e:
                # Check for operational / interface / connection / database lock errors
                exc_name = type(e).__name__
                is_operational = (exc_name in ('OperationalError', 'InterfaceError', 'DatabaseError')) or isinstance(e, sqlite3.OperationalError)
                try:
                    conn.rollback()
                except:
                    pass
                try:
                    conn.close()
                except:
                    pass
                
                if is_operational and attempt < retries - 1:
                    print(f"   [DB] Write locked/operational error ({exc_name}), retry {attempt+1}/{retries}...")
                    time.sleep(delay)
                else:
                    print(f"   [DB] Write failed after {retries} retries: {e}")
                    break
    return None


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

def migrate_local_sqlite_to_postgres():
    import sqlite3
    # Use __file__ to find the correct absolute path regardless of working directory
    _here = os.path.dirname(os.path.abspath(__file__))
    _repo_root = os.path.dirname(_here)
    print(f"   [MIGRATION] Working dir: {os.getcwd()}, app dir: {_here}", flush=True)

    candidates = [
        os.path.join(_here, 'news_cache.db'),           # backend/news_cache.db (app.py sibling)
        os.path.join(_repo_root, 'backend', 'news_cache.db'),  # repo_root/backend/news_cache.db
        os.path.join(_repo_root, 'news_cache.db'),       # repo_root/news_cache.db
        os.path.join(os.getcwd(), 'backend', 'news_cache.db'),
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
        for row in univ_rows:
            try:
                pg_cur.execute("""
                    INSERT INTO stock_universe (ticker, symbol, name, exchange, source, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker) DO NOTHING
                """, row)
            except Exception as e:
                pg_conn.rollback()
                print(f"      [MIGRATION] Error inserting stock_universe {row[0]}: {e}")
        pg_conn.commit()

        # 2. Migrate news (using actual SQLite columns)
        print("   [MIGRATION] Migrating news table...", flush=True)
        sqlite_cur.execute("SELECT id, headline, news_time, aam_janta_translation, macro_pathway, created_at, category FROM news")
        news_rows = sqlite_cur.fetchall()
        inserted_news = 0
        for row in news_rows:
            try:
                pg_cur.execute("""
                    INSERT INTO news (id, headline, news_time, aam_janta_translation, macro_pathway, created_at, category)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, row)
                inserted_news += 1
            except Exception as e:
                pg_conn.rollback()
                print(f"      [MIGRATION] Error inserting news {row[0]}: {e}")
        pg_conn.commit()
        print(f"   [MIGRATION] News: {inserted_news}/{len(news_rows)} rows migrated.", flush=True)

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
        for row in impact_rows:
            try:
                pg_cur.execute("""
                    INSERT INTO stock_impact (id, news_id, ticker, impact, estimated_change_percent,
                        view, reason, base_price, current_price, status, created_at,
                        confidence_score, technical_context, ensemble_detail)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, row)
                inserted_impact += 1
            except Exception as e:
                pg_conn.rollback()
                print(f"      [MIGRATION] Error inserting stock_impact {row[0]}: {e}")
        pg_conn.commit()
        print(f"   [MIGRATION] Stock impact: {inserted_impact}/{len(impact_rows)} rows migrated.", flush=True)

        # 4. Migrate historical_patterns
        print("   [MIGRATION] Migrating historical_patterns table...", flush=True)
        sqlite_cur.execute("SELECT id, headline, ticker, direction, outcome, change_pct, created_at FROM historical_patterns")
        pat_rows = sqlite_cur.fetchall()
        for row in pat_rows:
            try:
                pg_cur.execute("""
                    INSERT INTO historical_patterns (id, headline, ticker, direction, outcome, change_pct, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, row)
            except Exception as e:
                pg_conn.rollback()
                print(f"      [MIGRATION] Error inserting pattern {row[0]}: {e}")
        pg_conn.commit()

        # Adjust primary key sequences
        for seq_table in ['news', 'stock_impact', 'historical_patterns']:
            try:
                pg_cur.execute(f"SELECT setval(pg_get_serial_sequence('{seq_table}', 'id'), COALESCE(MAX(id), 1) + 1) FROM {seq_table}")
                pg_conn.commit()
            except Exception as ex:
                print(f"      [MIGRATION] Error syncing sequence {seq_table}: {ex}")

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
migrate_local_sqlite_to_postgres()
print("[DEBUG] migrate_local_sqlite_to_postgres() completed", flush=True)

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

# Your Gemini API Keys for rotation
API_KEYS = [
    os.environ.get(f"GEMINI_API_KEY_{i}") for i in range(1, 13)
]
# Add direct fallbacks for keys 10, 11, 12
fallback_keys = [
    "AIzaSyBjYaEDhYWQD-muqhmzxn2hpzgXuq4BYCE",
    "AIzaSyBEYTIYiaCNWuCRM19oJ6QKEMSwbfmZIqY",
    "AIzaSyDHc5YyF3QO84EfmrwfqA2DowxqzP5t89Y"
]
for fk in fallback_keys:
    if fk and fk not in API_KEYS:
        API_KEYS.append(fk)

API_KEYS = [key for key in API_KEYS if key] # Filter out missing keys

# Per-key cooldown after 429 (skip dead keys instead of re-trying them every batch)
_KEY_QUOTA_COOLDOWN_UNTIL: dict = {}
_GEMINI_KEY_COOLDOWN_SECS = int(os.environ.get("GEMINI_KEY_COOLDOWN_SECS", "300"))

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
    cooldown = cooldown_secs if cooldown_secs is not None else _GEMINI_KEY_COOLDOWN_SECS
    _KEY_QUOTA_COOLDOWN_UNTIL[key_idx] = time.time() + cooldown

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
    global current_key_idx, client
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

def get_and_rotate_client(last_failed_idx=None, is_timeout=False, is_quota=True, is_transient=False):
    global current_key_idx, client
    if last_failed_idx is not None:
        if is_quota or is_timeout or is_transient:
            cooldown = 15 if (is_transient or is_timeout) else _GEMINI_KEY_COOLDOWN_SECS
            _mark_gemini_key_quota_hit(last_failed_idx, cooldown_secs=cooldown)
            if is_timeout:
                status_str = "timed out"
            elif is_transient:
                status_str = "hit transient error"
            else:
                status_str = "hit quota limit"
            print(f"   [AI Rotation] Key {last_failed_idx + 1} {status_str}. Marked on cooldown for {cooldown}s.")
            sys.stdout.flush()
        else:
            print(f"   [AI Rotation] Key {last_failed_idx + 1} failed due to network/DNS error. Retrying without cooldown.")
            sys.stdout.flush()
    
    idx = _next_available_gemini_key_idx(current_key_idx if last_failed_idx is None else (last_failed_idx + 1) % len(API_KEYS))
    if idx is None:
        print("   [AI Rotation] No available Gemini keys left.")
        sys.stdout.flush()
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
]

# Global state for scraping optimizations
RSS_CACHE = {url: {'etag': None, 'modified': None} for url in RSS_SOURCES}
SEEN_HEADLINES = set()

# ── Duplicate Signal Cooldown Guard ──
# Tracks (ticker, direction) pairs with their last signal timestamp (UTC).
# Prevents the same ticker+direction from generating duplicate signals within 24 hours.
# Format: { "SBIN.NS_BULLISH": datetime_utc }
RECENT_SIGNALS: dict = {}

# NOTE: SM_GEMINI_CLIENT (aimlapi.com) removed — key expired.
# quant_ai_screener now uses the google-genai 'client' (same as Phase 2)
# with pure rule-based fallback when no Gemini keys are available.

def scrape_article_text(url):
    """Fetches the actual article body text (first 3 paragraphs) to give AI better context."""
    if not url or "google.com" in url:
        return ""
    cached = ARTICLE_TEXT_CACHE.get(url)
    if cached is not None:
        return cached

    try:
        resp = HTTP_SESSION.get(url, timeout=5)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            paragraphs = soup.find_all('p')
            text = " ".join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 50])
            result = text[:1500]
            ARTICLE_TEXT_CACHE[url] = result
            return result
    except Exception as e:
        print(f"   [Scrape Error] {url}: {e}")
    ARTICLE_TEXT_CACHE[url] = ""
    return ""

def clean_json(raw_text):
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
    return json.loads(cleaned.strip())

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

# ==========================================
# KEYWORD FILTER — fast relevance check
# ==========================================
FINANCE_KEYWORDS = [
    'stock', 'share', 'shares', 'market', 'nifty', 'sensex', 'bse', 'nse',
    'rally', 'crash', 'bull', 'bear', 'trade', 'trading', 'etf', 'ipo', 'fpo',
    'dividend', 'earnings', 'profit', 'loss', 'revenue', 'quarter',
    'q1', 'q2', 'q3', 'q4', 'rbi', 'sebi', 'inflation', 'rate', 'bond',
    'rupee', 'crude', 'oil', 'gold', 'bank', 'nbfc', 'mutual fund',
    'buy', 'sell', 'target', 'upgrade', 'downgrade', 'fii', 'dii', 'fpi',
    'block deal', 'bulk deal', 'merger', 'acquisition', 'buyback', 'delisting',
    'rebound', 'correction', 'breakout', 'support', 'resistance',
    'sector', 'pharma', 'auto', 'realty', 'infra', 'defence', 'power',
    'cement', 'fmcg', 'telecom', 'midcap', 'smallcap', 'largecap',
    'result', 'growth', 'margin', 'ebitda', 'pat', 'eps',
    'investor', 'portfolio', 'fund', 'index', 'return', 'equity',
    'debt', 'credit', 'loan', 'interest', 'fiscal', 'gdp',
    'export', 'import', 'tariff', 'manufacturing', 'corporate', 'company',
]

def is_finance_relevant(headline):
    h = headline.lower()
    return any(kw in h for kw in FINANCE_KEYWORDS)

# ==========================================
# SENTIMENT KEYWORDS — bullish/bearish rules
# ==========================================
BULLISH_KEYWORDS = [
    'rise', 'rises', 'rising', 'rally', 'rallies', 'surge', 'surges',
    'jump', 'jumps', 'gain', 'gains', 'gained', 'up ', 'high', 'highs',
    'record', 'soar', 'soars', 'zoom', 'zooms', 'profit', 'growth',
    'upgrade', 'outperform', 'buy', 'bullish', 'positive', 'strong',
    'beat', 'beats', 'exceed', 'boost', 'rebound', 'recovery', 'breakout',
    'dividend', 'buyback', 'expansion', 'robust', 'stellar', 'doubles',
    'optimistic', 'upside', 'winner', 'outpace', 'top pick',
]

BEARISH_KEYWORDS = [
    'fall', 'falls', 'falling', 'drop', 'drops', 'crash', 'crashes',
    'plunge', 'plunges', 'decline', 'declines', 'declined', 'down ', 'low',
    'lows', 'sink', 'sinks', 'tumble', 'tumbles', 'loss', 'losses',
    'downgrade', 'underperform', 'sell', 'bearish', 'negative', 'weak',
    'miss', 'misses', 'cut', 'cuts', 'slash', 'concern', 'fear',
    'warning', 'ban', 'penalty', 'fine', 'fraud', 'scam', 'debt',
    'default', 'flee', 'exit', 'outflow', 'worst', 'slump',
]

# ==========================================
# CATEGORY CLASSIFICATION — rule-based
# ==========================================
CATEGORY_KEYWORDS = {
    'Finance': ['stock', 'market', 'nifty', 'sensex', 'rbi', 'sebi', 'fund', 'fii', 'dii', 'bond', 'yield', 'inflation', 'rate', 'rupee', 'forex', 'index', 'rally', 'crash', 'bull', 'bear'],
    'Business': ['company', 'merger', 'acquisition', 'ipo', 'earnings', 'profit', 'revenue', 'ceo', 'board', 'startup', 'valuation', 'q1', 'q2', 'q3', 'q4', 'quarter', 'result', 'dividend', 'buyback'],
    'Technology': ['tech', 'ai ', 'software', 'digital', 'chip', 'semiconductor', 'data', 'cloud', 'cyber', 'app ', 'gadget'],
    'Politics': ['government', 'election', 'minister', 'parliament', 'policy', 'modi', 'bjp', 'congress', 'bill ', 'political'],
    'World': ['global', 'us ', 'china', 'trump', 'fed ', 'european', 'war', 'tariff', 'trade war', 'geopolitical', 'iran', 'russia'],
}

def classify_category(headline):
    h = headline.lower()
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw in h)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else 'General'

# ==========================================
# RULE-BASED STOCK MAPPING — instant, no AI
# ==========================================
import re

# All keywords use plain strings; matching uses regex word-boundaries (see get_candidate_stocks)
STOCK_KEYWORD_MAP = {
    # ── NIFTY 50 ──
    'reliance industries': 'RELIANCE.NS', 'reliance': 'RELIANCE.NS', 'ril': 'RELIANCE.NS',
    'tata consultancy': 'TCS.NS', 'tcs': 'TCS.NS',
    'infosys': 'INFY.NS', 'infy': 'INFY.NS',
    'hdfc bank': 'HDFCBANK.NS', 'hdfcbank': 'HDFCBANK.NS', 'hdfc': 'HDFCBANK.NS',
    'icici bank': 'ICICIBANK.NS', 'icicibank': 'ICICIBANK.NS', 'icici': 'ICICIBANK.NS',
    'state bank of india': 'SBIN.NS', 'state bank': 'SBIN.NS', 'sbi': 'SBIN.NS',
    'bharti airtel': 'BHARTIARTL.NS', 'airtel': 'BHARTIARTL.NS',
    'hindustan unilever': 'HINDUNILVR.NS', 'hul': 'HINDUNILVR.NS',
    'itc': 'ITC.NS',
    'kotak mahindra': 'KOTAKBANK.NS', 'kotak bank': 'KOTAKBANK.NS', 'kotak': 'KOTAKBANK.NS',
    'larsen & toubro': 'LT.NS', 'larsen and toubro': 'LT.NS', 'larsen': 'LT.NS', 'l&t': 'LT.NS', 'l and t': 'LT.NS',
    'axis bank': 'AXISBANK.NS', 'axis': 'AXISBANK.NS',
    'bajaj finance': 'BAJFINANCE.NS',
    'bajaj finserv': 'BAJAJFINSV.NS',
    'maruti suzuki': 'MARUTI.NS', 'maruti': 'MARUTI.NS',
    'asian paints': 'ASIANPAINT.NS',
    'titan company': 'TITAN.NS', 'titan': 'TITAN.NS',
    'sun pharmaceutical': 'SUNPHARMA.NS', 'sun pharma': 'SUNPHARMA.NS',
    'wipro': 'WIPRO.NS',
    'hcl technologies': 'HCLTECH.NS', 'hcl tech': 'HCLTECH.NS', 'hcl': 'HCLTECH.NS',
    'power grid': 'POWERGRID.NS', 'powergrid': 'POWERGRID.NS',
    'ntpc': 'NTPC.NS',
    'tata motors': 'TMPV.NS',
    'tata steel': 'TATASTEEL.NS',
    'mahindra & mahindra': 'M&M.NS', 'mahindra and mahindra': 'M&M.NS', 'mahindra': 'M&M.NS', 'm&m': 'M&M.NS',
    'adani enterprises': 'ADANIENT.NS', 'adani ent': 'ADANIENT.NS',
    'adani ports': 'ADANIPORTS.NS',
    'adani green': 'ADANIGREEN.NS',
    'adani power': 'ADANIPOWER.NS',
    'adani total': 'ADANITOTAL.NS',
    'adani': 'ADANIENT.NS',
    'ultratech cement': 'ULTRACEMCO.NS', 'ultratech': 'ULTRACEMCO.NS',
    'nestle india': 'NESTLEIND.NS', 'nestle': 'NESTLEIND.NS',
    'tech mahindra': 'TECHM.NS',
    'indusind bank': 'INDUSINDBK.NS', 'indusind': 'INDUSINDBK.NS',
    'grasim': 'GRASIM.NS',
    'bajaj auto': 'BAJAJ-AUTO.NS',
    'cipla': 'CIPLA.NS',
    'dr reddy': 'DRREDDY.NS', "dr. reddy's": 'DRREDDY.NS', 'dr reddys': 'DRREDDY.NS',
    'hero motocorp': 'HEROMOTOCO.NS', 'hero moto': 'HEROMOTOCO.NS', 'hero': 'HEROMOTOCO.NS',
    'coal india': 'COALINDIA.NS',
    'ongc': 'ONGC.NS',
    'bharat petroleum': 'BPCL.NS', 'bpcl': 'BPCL.NS',
    "divi's laboratories": 'DIVISLAB.NS', "divi's lab": 'DIVISLAB.NS', 'divis lab': 'DIVISLAB.NS', 'divis': 'DIVISLAB.NS',
    'britannia': 'BRITANNIA.NS',
    'eicher motors': 'EICHERMOT.NS', 'royal enfield': 'EICHERMOT.NS',
    'apollo hospitals': 'APOLLOHOSP.NS', 'apollo hospital': 'APOLLOHOSP.NS', 'apollo': 'APOLLOHOSP.NS',
    'tata consumer': 'TATACONSUM.NS',
    'sbi life': 'SBILIFE.NS',
    'hdfc life': 'HDFCLIFE.NS',
    'shriram finance': 'SHRIRAMFIN.NS',
    'bhel': 'BHEL.NS', 'bharat heavy electricals': 'BHEL.NS',
    'jsw steel': 'JSWSTEEL.NS', 'jsw': 'JSWSTEEL.NS',
    'hindalco': 'HINDALCO.NS',
    # ── Popular Mid/Small Caps ──
    'muthoot finance': 'MUTHOOTFIN.NS', 'muthoot fin': 'MUTHOOTFIN.NS', 'muthoot': 'MUTHOOTFIN.NS',
    'aurobindo pharma': 'AUROPHARMA.NS', 'aurobindo': 'AUROPHARMA.NS',
    'hindustan petroleum': 'HINDPETRO.NS', 'hpcl': 'HINDPETRO.NS',
    'indian oil': 'IOC.NS', 'ioc': 'IOC.NS',
    'bharat electronics': 'BEL.NS', 'bel': 'BEL.NS',
    'hindustan aeronautics': 'HAL.NS', 'hal': 'HAL.NS',
    'solar industries': 'SOLARINDS.NS',
    'vodafone idea': 'IDEA.NS', 'vi ': 'IDEA.NS',
    'godfrey phillips': 'GODFRYPHLP.NS',
    'tejas networks': 'TEJASNET.NS', 'tejas network': 'TEJASNET.NS',
    'bandhan bank': 'BANDHANBNK.NS', 'bandhan': 'BANDHANBNK.NS',
    'manappuram': 'MANAPPURAM.NS',
    'zomato': 'ZOMATO.NS',
    'paytm': 'PAYTM.NS', 'one97': 'PAYTM.NS',
    'nykaa': 'NYKAA.NS',
    'delhivery': 'DELHIVERY.NS',
    'vedanta': 'VEDL.NS',
    'jindal steel': 'JINDALSTEL.NS', 'jindal': 'JINDALSTEL.NS',
    'tata power': 'TATAPOWER.NS',
    'tata elxsi': 'TATAELXSI.NS',
    'ltimindtree': 'LTIM.NS', 'lti mindtree': 'LTIM.NS', 'lti': 'LTIM.NS',
    'punjab national bank': 'PNB.NS', 'punjab national': 'PNB.NS', 'pnb': 'PNB.NS',
    'bank of baroda': 'BANKBARODA.NS', 'bob': 'BANKBARODA.NS',
    'canara bank': 'CANBK.NS', 'canara': 'CANBK.NS',
    'idbi bank': 'IDBI.NS', 'idbi': 'IDBI.NS',
    'federal bank': 'FEDERALBNK.NS',
    'yes bank': 'YESBANK.NS',
    'irctc': 'IRCTC.NS',
    'irfc': 'IRFC.NS',
    'rvnl': 'RVNL.NS', 'rail vikas': 'RVNL.NS',
    'nhpc': 'NHPC.NS',
    'suzlon energy': 'SUZLON.NS', 'suzlon': 'SUZLON.NS',
    'tata chemicals': 'TATACHEM.NS',
    'godrej consumer': 'GODREJCP.NS', 'godrej': 'GODREJCP.NS',
    'pidilite': 'PIDILITIND.NS',
    'havells': 'HAVELLS.NS',
    'siemens': 'SIEMENS.NS',
    'abb india': 'ABB.NS', 'abb': 'ABB.NS',
    'page industries': 'PAGEIND.NS',
    'dmart': 'DMART.NS', 'avenue supermarts': 'DMART.NS',
    'biocon': 'BIOCON.NS',
    'lupin': 'LUPIN.NS',
    'torrent pharma': 'TORNTPHARM.NS', 'torrent': 'TORNTPHARM.NS',
    'jubilant foodworks': 'JUBLFOOD.NS', 'jubilant food': 'JUBLFOOD.NS',
    'indigo airlines': 'INDIGO.NS', 'interglobe aviation': 'INDIGO.NS', 'indigo': 'INDIGO.NS',
    'spicejet': 'SPICEJET.NS',
    'dixon technologies': 'DIXON.NS', 'dixon tech': 'DIXON.NS', 'dixon': 'DIXON.NS',
    'polycab': 'POLYCAB.NS',
    'persistent systems': 'PERSISTENT.NS', 'persistent': 'PERSISTENT.NS',
    'coforge': 'COFORGE.NS',
    'mphasis': 'MPHASIS.NS',
    'max healthcare': 'MAXHEALTH.NS', 'max health': 'MAXHEALTH.NS',
    'motherson sumi': 'MOTHERSON.NS', 'motherson': 'MOTHERSON.NS',
    'srf': 'SRF.NS',
    'pi industries': 'PIIND.NS',
    'cholamandalam investment': 'CHOLAFIN.NS', 'cholamandalam': 'CHOLAFIN.NS', 'chola': 'CHOLAFIN.NS',
    'voltas': 'VOLTAS.NS',
    'bharat forge': 'BHARATFORG.NS',
    'exide industries': 'EXIDEIND.NS', 'exide': 'EXIDEIND.NS',
    'amara raja': 'AMARAJABAT.NS',
    'marico': 'MARICO.NS',
    'dabur': 'DABUR.NS',
    'colgate palmolive': 'COLPAL.NS', 'colgate': 'COLPAL.NS',
    'acc cement': 'ACC.NS', 'acc': 'ACC.NS',
    'ambuja cements': 'AMBUJACEM.NS', 'ambuja cement': 'AMBUJACEM.NS', 'ambuja': 'AMBUJACEM.NS',
    'shree cement': 'SHREECEM.NS', 'shree': 'SHREECEM.NS',
    'dalmia bharat': 'DALBHARAT.NS', 'dalmia': 'DALBHARAT.NS',
    'hatsun agro': 'HATSUN.NS', 'hatsun': 'HATSUN.NS',
    # ── Tata Group (generic "tata" catches news about the whole group) ──
    'tata group': 'TMPV.NS',
    # ── Other large populars ──
    'dlf': 'DLF.NS',
    'lodha': 'LODHA.NS', 'macrotech': 'LODHA.NS',
    'oberoi realty': 'OBEROIRLTY.NS', 'oberoi': 'OBEROIRLTY.NS',
    'lici': 'LICI.NS', 'lic india': 'LICI.NS', 'lic': 'LICI.NS',
    'nuvoco': 'NUVOCO.NS',
    'syngene': 'SYNGENE.NS',
    'laurus labs': 'LAURUSLABS.NS', 'laurus': 'LAURUSLABS.NS',
    'alkem laboratories': 'ALKEM.NS', 'alkem': 'ALKEM.NS',
    'the ramco': 'RAMCOCEM.NS', 'ramco cement': 'RAMCOCEM.NS',
    'emami': 'EMAMILTD.NS',
    'astral': 'ASTRAL.NS',
    'supreme industries': 'SUPREMEIND.NS',
    'kajaria': 'KAJARIACER.NS', 'kajaria ceramics': 'KAJARIACER.NS',
    'relaxo': 'RELAXO.NS',
    'campus activewear': 'CAMPUS.NS',
    'one mobi': 'ONMOBILE.NS',
    'nesco': 'NESCO.NS',
    'gland pharma': 'GLAND.NS',
    'ipca laboratories': 'IPCALAB.NS', 'ipca': 'IPCALAB.NS',
    'navin fluorine': 'NAVINFLUOR.NS',
    'deepak nitrite': 'DEEPAKNTR.NS', 'deepak': 'DEEPAKNTR.NS',
    'clean science': 'CLEANSCI.NS',
    'fine organics': 'FINEORG.NS',
    'aarti industries': 'AARTIIND.NS', 'aarti': 'AARTIIND.NS',
    'nocil': 'NOCIL.NS',
    'bombay burmah': 'BBTC.NS',
    'edelweiss': 'EDELWEISS.NS',
    'angel one': 'ANGELONE.NS', 'angel broking': 'ANGELONE.NS',
    'hdfc amc': 'HDFCAMC.NS',
    'nippon india': 'NAM-INDIA.NS', 'nippon': 'NAM-INDIA.NS',
    'bajaj consumer': 'BAJAJCON.NS',
    'trent': 'TRENT.NS',
    'v-mart': 'VMART.NS', 'v mart': 'VMART.NS',
    'metro brands': 'METROBRAND.NS',
    'bata': 'BATAIND.NS', 'bata india': 'BATAIND.NS',
    'kpit technologies': 'KPITTECH.NS', 'kpit': 'KPITTECH.NS',
    'tata technologies': 'TATATECH.NS', 'tata tech': 'TATATECH.NS',
    'cams': 'CAMS.NS',
    'cdsl': 'CDSL.NS',
    'bse': 'BSE.NS',
    'mcx': 'MCX.NS',
    'nse india': 'NSEI.NS',
    'mamaearth': 'HONASA.NS', 'honasa': 'HONASA.NS',
    'boat': 'IMAGINE.NS',
    'swiggy': 'SWIGGY.NS',
    'ola electric': 'OLAELEC.NS', 'ola': 'OLAELEC.NS',
}

# ==========================================
# MACRO & SECTOR IMPACT MAP — 2nd order effects
# ==========================================
MACRO_IMPACT_MAP = {
    # ── Crude oil ──
    'crude oil rise': [('ONGC.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH'), ('ASIANPAINT.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    'crude oil crash': [('ONGC.NS', 'BEARISH'), ('BPCL.NS', 'BULLISH'), ('ASIANPAINT.NS', 'BULLISH'), ('INDIGO.NS', 'BULLISH')],
    'crude rises': [('ONGC.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    'crude falls': [('ONGC.NS', 'BEARISH'), ('BPCL.NS', 'BULLISH'), ('INDIGO.NS', 'BULLISH')],
    'oil prices rise': [('ONGC.NS', 'BULLISH'), ('HINDPETRO.NS', 'BEARISH'), ('BPCL.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    'oil prices fall': [('ONGC.NS', 'BEARISH'), ('HINDPETRO.NS', 'BULLISH'), ('BPCL.NS', 'BULLISH'), ('INDIGO.NS', 'BULLISH')],
    'opec cut': [('ONGC.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH'), ('IOC.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    'opec increase': [('ONGC.NS', 'BEARISH'), ('BPCL.NS', 'BULLISH'), ('IOC.NS', 'BULLISH'), ('INDIGO.NS', 'BULLISH')],
    # ── FII / FPI ──
    'fii selling': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'fii sell': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'fiis sell': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'fii outflow': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'fpi outflow': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'fii buying': [('HDFCBANK.NS', 'BULLISH'), ('ICICIBANK.NS', 'BULLISH'), ('RELIANCE.NS', 'BULLISH')],
    'fii inflow': [('HDFCBANK.NS', 'BULLISH'), ('ICICIBANK.NS', 'BULLISH'), ('RELIANCE.NS', 'BULLISH')],
    'fpi inflow': [('HDFCBANK.NS', 'BULLISH'), ('ICICIBANK.NS', 'BULLISH'), ('RELIANCE.NS', 'BULLISH')],
    # ── RBI / Rates ──
    'rate hike': [('DLF.NS', 'BEARISH'), ('LODHA.NS', 'BEARISH'), ('SBIN.NS', 'BULLISH'), ('BAJFINANCE.NS', 'BEARISH')],
    'rate cut': [('DLF.NS', 'BULLISH'), ('LODHA.NS', 'BULLISH'), ('SBIN.NS', 'BEARISH'), ('BAJFINANCE.NS', 'BULLISH')],
    'repo rate cut': [('HDFCBANK.NS', 'BULLISH'), ('SBIN.NS', 'BULLISH'), ('DLF.NS', 'BULLISH'), ('BAJFINANCE.NS', 'BULLISH')],
    'repo rate hike': [('HDFCBANK.NS', 'BEARISH'), ('SBIN.NS', 'BEARISH'), ('DLF.NS', 'BEARISH')],
    'rbi policy': [('HDFCBANK.NS', 'BULLISH'), ('SBIN.NS', 'BULLISH'), ('ICICIBANK.NS', 'BULLISH')],
    # ── Semiconductor / Chips (Global → India) ──
    'semiconductor shortage': [('INFY.NS', 'BEARISH'), ('WIPRO.NS', 'BEARISH'), ('TMPV.NS', 'BEARISH'), ('MARUTI.NS', 'BEARISH')],
    'chip shortage': [('TMPV.NS', 'BEARISH'), ('MARUTI.NS', 'BEARISH'), ('HEROMOTOCO.NS', 'BEARISH'), ('EICHERMOT.NS', 'BEARISH')],
    'semiconductor ban': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('WIPRO.NS', 'BEARISH'), ('TMPV.NS', 'BEARISH')],
    'chip export ban': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('TMPV.NS', 'BEARISH')],
    'semiconductor plant india': [('VEDL.NS', 'BULLISH'), ('TATAELXSI.NS', 'BULLISH'), ('DIXON.NS', 'BULLISH')],
    'chip fab india': [('VEDL.NS', 'BULLISH'), ('TATAELXSI.NS', 'BULLISH')],
    # ── Japan / China / US Geopolitical Supply Chain ──
    'japan export control': [('TMPV.NS', 'BEARISH'), ('MARUTI.NS', 'BEARISH'), ('INFY.NS', 'BEARISH')],
    'japan semiconductor': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('WIPRO.NS', 'BEARISH'), ('TMPV.NS', 'BEARISH')],
    'china slowdown': [('TATASTEEL.NS', 'BEARISH'), ('JSWSTEEL.NS', 'BEARISH'), ('HINDALCO.NS', 'BEARISH'), ('COALINDIA.NS', 'BEARISH')],
    'china stimulus': [('TATASTEEL.NS', 'BULLISH'), ('JSWSTEEL.NS', 'BULLISH'), ('HINDALCO.NS', 'BULLISH')],
    'china tariff': [('TATASTEEL.NS', 'BULLISH'), ('JSWSTEEL.NS', 'BULLISH'), ('DIXON.NS', 'BULLISH')],
    'china dumping': [('TATASTEEL.NS', 'BEARISH'), ('JSWSTEEL.NS', 'BEARISH'), ('HINDALCO.NS', 'BEARISH')],
    'us fed rate': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('INFY.NS', 'BEARISH')],
    'fed rate cut': [('INFY.NS', 'BULLISH'), ('TCS.NS', 'BULLISH'), ('HDFCBANK.NS', 'BULLISH')],
    'fed rate hike': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('HDFCBANK.NS', 'BEARISH')],
    'us recession': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('WIPRO.NS', 'BEARISH'), ('HCLTECH.NS', 'BEARISH')],
    'us sanctions': [('RELIANCE.NS', 'BEARISH'), ('ONGC.NS', 'BEARISH')],
    'taiwan tension': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('TMPV.NS', 'BEARISH')],
    'taiwan strait': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH')],
    'russia ukraine': [('ONGC.NS', 'BULLISH'), ('COALINDIA.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH')],
    'middle east tension': [('ONGC.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    'iran conflict': [('ONGC.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    # ── Currency / Trade ──
    'rupee falls': [('INFY.NS', 'BULLISH'), ('TCS.NS', 'BULLISH'), ('WIPRO.NS', 'BULLISH'), ('SUNPHARMA.NS', 'BULLISH')],
    'rupee weakens': [('INFY.NS', 'BULLISH'), ('TCS.NS', 'BULLISH'), ('SUNPHARMA.NS', 'BULLISH')],
    'rupee rises': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH')],
    'rupee strengthens': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH')],
    'dollar surge': [('INFY.NS', 'BULLISH'), ('TCS.NS', 'BULLISH'), ('MARUTI.NS', 'BEARISH')],
    'tariff': [('TMPV.NS', 'BEARISH'), ('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH')],
    'trade war': [('TMPV.NS', 'BEARISH'), ('INFY.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'anti-dumping duty': [('TATASTEEL.NS', 'BULLISH'), ('JSWSTEEL.NS', 'BULLISH')],
    'import duty hike': [('DIXON.NS', 'BULLISH'), ('TATASTEEL.NS', 'BULLISH')],
    'pli scheme': [('DIXON.NS', 'BULLISH'), ('VEDL.NS', 'BULLISH'), ('TATAELXSI.NS', 'BULLISH')],
    # ── Commodities (deep supply chain) ──
    'steel prices rise': [('TATASTEEL.NS', 'BULLISH'), ('JSWSTEEL.NS', 'BULLISH'), ('MARUTI.NS', 'BEARISH'), ('LT.NS', 'BEARISH')],
    'steel prices fall': [('TATASTEEL.NS', 'BEARISH'), ('JSWSTEEL.NS', 'BEARISH'), ('MARUTI.NS', 'BULLISH'), ('LT.NS', 'BULLISH')],
    'aluminium prices': [('HINDALCO.NS', 'BULLISH'), ('VEDL.NS', 'BULLISH')],
    'copper prices rise': [('HINDALCO.NS', 'BULLISH'), ('VEDL.NS', 'BULLISH')],
    'lithium shortage': [('TMPV.NS', 'BEARISH'), ('M&M.NS', 'BEARISH')],
    'lithium prices fall': [('TMPV.NS', 'BULLISH'), ('M&M.NS', 'BULLISH')],
    'coal prices rise': [('COALINDIA.NS', 'BULLISH'), ('NTPC.NS', 'BEARISH'), ('JSWSTEEL.NS', 'BEARISH')],
    'natural gas prices': [('IGL.NS', 'BEARISH'), ('MGL.NS', 'BEARISH'), ('GAIL.NS', 'BULLISH')],
    'gold surges': [('MUTHOOTFIN.NS', 'BULLISH'), ('MANAPPURAM.NS', 'BULLISH'), ('TITAN.NS', 'BEARISH')],
    'gold rises': [('MUTHOOTFIN.NS', 'BULLISH'), ('MANAPPURAM.NS', 'BULLISH')],
    'gold falls': [('MUTHOOTFIN.NS', 'BEARISH'), ('MANAPPURAM.NS', 'BEARISH'), ('TITAN.NS', 'BULLISH')],
    # ── Sector Deep / Government Policy ──
    'defense budget': [('HAL.NS', 'BULLISH'), ('BEL.NS', 'BULLISH'), ('BHARATFORG.NS', 'BULLISH')],
    'defence budget': [('HAL.NS', 'BULLISH'), ('BEL.NS', 'BULLISH'), ('BHARATFORG.NS', 'BULLISH')],
    'defense order': [('HAL.NS', 'BULLISH'), ('BEL.NS', 'BULLISH')],
    'railway budget': [('RVNL.NS', 'BULLISH'), ('IRFC.NS', 'BULLISH'), ('IRCTC.NS', 'BULLISH')],
    'infrastructure spending': [('LT.NS', 'BULLISH'), ('RVNL.NS', 'BULLISH'), ('NTPC.NS', 'BULLISH')],
    'inflation rise': [('HDFCBANK.NS', 'BEARISH'), ('DLF.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'gdp growth': [('HDFCBANK.NS', 'BULLISH'), ('RELIANCE.NS', 'BULLISH'), ('LT.NS', 'BULLISH')],
    'monsoon forecast': [('UPL.NS', 'BULLISH'), ('PIDILITIND.NS', 'BULLISH'), ('DABUR.NS', 'BULLISH')],
    'drought': [('UPL.NS', 'BEARISH'), ('DABUR.NS', 'BEARISH'), ('ITC.NS', 'BEARISH')],
    'ev policy': [('TMPV.NS', 'BULLISH'), ('M&M.NS', 'BULLISH'), ('MARUTI.NS', 'BEARISH')],
    'electric vehicle': [('TMPV.NS', 'BULLISH'), ('M&M.NS', 'BULLISH')],
    'renewable energy': [('ADANIGREEN.NS', 'BULLISH'), ('TATAPOWER.NS', 'BULLISH'), ('NTPC.NS', 'BULLISH')],
    'solar tariff': [('ADANIGREEN.NS', 'BULLISH'), ('TATAPOWER.NS', 'BULLISH')],
    'upi transaction': [('PAYTM.NS', 'BULLISH'), ('SBICARD.NS', 'BULLISH')],
    'digital payment': [('PAYTM.NS', 'BULLISH'), ('SBICARD.NS', 'BULLISH')],
    'pharma sector rally': [('SUNPHARMA.NS', 'BULLISH'), ('CIPLA.NS', 'BULLISH'), ('DRREDDY.NS', 'BULLISH')],
    'fda approval': [('SUNPHARMA.NS', 'BULLISH'), ('CIPLA.NS', 'BULLISH'), ('DRREDDY.NS', 'BULLISH')],
    'fda warning': [('SUNPHARMA.NS', 'BEARISH'), ('CIPLA.NS', 'BEARISH'), ('DRREDDY.NS', 'BEARISH')],
    'it sector rally': [('INFY.NS', 'BULLISH'), ('TCS.NS', 'BULLISH'), ('WIPRO.NS', 'BULLISH')],
    'banking sector': [('HDFCBANK.NS', 'BULLISH'), ('ICICIBANK.NS', 'BULLISH'), ('SBIN.NS', 'BULLISH')],
    'auto sector': [('MARUTI.NS', 'BULLISH'), ('TMPV.NS', 'BULLISH'), ('M&M.NS', 'BULLISH')],
    'realty stocks': [('DLF.NS', 'BULLISH'), ('LODHA.NS', 'BULLISH'), ('OBEROIRLTY.NS', 'BULLISH')],
    'metal stocks': [('TATASTEEL.NS', 'BULLISH'), ('JSWSTEEL.NS', 'BULLISH'), ('HINDALCO.NS', 'BULLISH')],
}

MATERIAL_EVENT_KEYWORDS = [
    'earnings', 'result', 'results', 'profit', 'loss', 'revenue', 'margin',
    'order win', 'wins order', 'contract', 'deal', 'merger', 'acquisition',
    'stake sale', 'block deal', 'bulk deal', 'buyback', 'dividend', 'split',
    'bonus', 'ipo', 'listing', 'approval', 'ban', 'penalty', 'fine', 'fraud',
    'default', 'downgrade', 'upgrade', 'guidance', 'capex', 'expansion',
    'plant', 'shutdown', 'launch', 'tariff', 'rbi', 'repo rate', 'budget',
    'policy', 'export', 'import', 'crude', 'rupee', 'fii', 'fpi',
    # Geopolitical / Supply Chain / Macro (hidden chain triggers)
    'semiconductor', 'chip', 'sanction', 'embargo', 'trade war', 'tariff war',
    'fed rate', 'federal reserve', 'ecb', 'bank of japan', 'boj',
    'china', 'japan', 'taiwan', 'russia', 'ukraine', 'iran', 'middle east',
    'opec', 'recession', 'slowdown', 'stimulus', 'dumping', 'anti-dumping',
    'supply chain', 'shortage', 'disruption', 'blockade', 'strike',
    'inflation', 'deflation', 'gdp', 'current account', 'fiscal deficit',
    'monsoon', 'drought', 'flood', 'climate',
    'lithium', 'cobalt', 'rare earth', 'copper', 'aluminium', 'steel',
    'natural gas', 'lng', 'coal', 'solar', 'renewable', 'ev ', 'electric vehicle',
    'pli', 'subsidy', 'deregulation', 'privatization', 'disinvestment',
    'fda', 'usfda', 'dcgi', 'who', 'pandemic', 'epidemic',
    'defence order', 'defense order', 'arms deal', 'military',
    'digital payment', 'upi', 'fintech', 'cryptocurrency', 'bitcoin',
    'promoter', 'insider', 'pledge', 'rating', 'moody', 'fitch', 's&p',
]

LOW_SIGNAL_PHRASES = [
    'analyst says', 'price target', 'target price', 'stocks to buy',
    'should you buy', 'what should investors do', 'technical breakout',
    'watch today', 'market live', 'sensex today', 'nifty today',
]

INDEX_LIKE_SYMBOLS = {
    'NIFTY', 'NIFTY50', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY',
    'SENSEX', 'NSEI', 'BSESN', 'NSE', 'BSE',
}

COMMON_UPPERCASE_WORDS = {
    'RBI', 'SEBI', 'FII', 'FIIS', 'FPI', 'FPIS', 'DII', 'DIIS', 'IPO',
    'CEO', 'CFO', 'MD', 'QIP', 'GDP', 'GST', 'EV', 'AI', 'IT', 'US',
    'UK', 'EU', 'Q1', 'Q2', 'Q3', 'Q4', 'PAT', 'EBITDA', 'NPA',
}

def normalize_ticker(ticker):
    if not ticker:
        return None
    t = str(ticker).upper().strip()
    t = t.replace("NSE:", "").replace("BSE:", "")
    t = t.replace(".NSE", ".NS").replace(".BSE", ".BO")
    t = re.sub(r'[^A-Z0-9&.\-]', '', t)
    if not t or t.startswith("^"):
        return None
    if not (t.endswith(".NS") or t.endswith(".BO")):
        if re.fullmatch(r'[A-Z0-9&\-]{2,16}', t):
            t = f"{t}.NS"
        else:
            return None
    base = t.rsplit(".", 1)[0]
    suffix = t.rsplit(".", 1)[1]
    
    # Resolve common AI ticker hallucinations / aliases
    _TICKER_ALIASES = {
        'INTERGLOBE': 'INDIGO',
        'INTERGLOBEAVIATION': 'INDIGO',
        'MAHINDRA': 'M&M',
        'MAHINDRA&MAHINDRA': 'M&M',
        'MAHINDRAANDMAHINDRA': 'M&M',
        'MANDM': 'M&M',
        'LARSEN': 'LT',
        'LARSEN&TOUBRO': 'LT',
        'L&T': 'LT',
        'LANDT': 'LT',
        'LARSENANDTOUBRO': 'LT',
        'BAJAJAUTO': 'BAJAJ-AUTO',
        'TATACONSUMER': 'TATACONSUM',
        'HUL': 'HINDUNILVR',
        'KOTAK': 'KOTAKBANK',
        'SBI': 'SBIN',
        'TATAMOTORS': 'TMPV',
        'TATAMOTOR': 'TMPV',
    }
    if base in _TICKER_ALIASES:
        base = _TICKER_ALIASES[base]
        t = f"{base}.{suffix}"

    if base in INDEX_LIKE_SYMBOLS:
        return None
    return t

def ticker_base(ticker):
    t = normalize_ticker(ticker)
    return t.rsplit(".", 1)[0] if t else ""

def is_supported_equity_ticker(ticker):
    t = normalize_ticker(ticker)
    if not t:
        return False
    known = {normalize_ticker(v) for v in STOCK_KEYWORD_MAP.values()}
    if t in known:
        return True
    base = ticker_base(t)
    try:
        # If the Angel One scrip master is already loaded, use it as a guard
        # against obvious AI hallucinations. If it is not loaded, stay permissive
        # so the news worker does not block on a network call.
        if getattr(yf, "_scrip_loaded", False):
            if t.endswith(".NS"):
                return base in getattr(yf, "_scrip_cache", {})
            if t.endswith(".BO"):
                return base in getattr(yf, "_bse_cache", {})
    except Exception:
        pass
    return re.fullmatch(r'[A-Z0-9&\-]{2,16}\.(NS|BO)', t) is not None

def _keyword_mentions_ticker(text, ticker):
    if not text or not ticker:
        return False
    text_l = text.lower()
    ticker_n = normalize_ticker(ticker)
    for keyword, mapped_ticker in STOCK_KEYWORD_MAP.items():
        if normalize_ticker(mapped_ticker) != ticker_n:
            continue
        pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
        if re.search(pattern, text_l):
            return True
    base = ticker_base(ticker_n).lower()
    return bool(base and re.search(r'\b' + re.escape(base) + r'\b', text_l))

def _macro_mentions(text):
    text_l = (text or "").lower()
    return [kw for kw in MACRO_IMPACT_MAP if kw in text_l]

def _headline_direction(headline, context=""):
    h = f"{headline or ''} {context or ''}".lower()
    bull_score = sum(1 for kw in BULLISH_KEYWORDS if kw in h)
    bear_score = sum(1 for kw in BEARISH_KEYWORDS if kw in h)
    return 'BULLISH' if bull_score >= bear_score else 'BEARISH'

def candidate_quality_score(headline, context, ticker, source="rule", materiality_hint=65):
    text = f"{headline or ''} {context or ''}"
    text_l = text.lower()
    try:
        score = int(float(re.sub(r'[^0-9.]', '', str(materiality_hint or 65)) or 65))
    except Exception:
        score = 65
    source_l = (source or "rule").lower()

    if source_l == "llm":
        score += 12
    elif source_l == "macro":
        score += 6
    else:
        score += 4

    if _keyword_mentions_ticker(headline, ticker):
        score += 22
    elif _keyword_mentions_ticker(context, ticker):
        score += 10

    macro_hits = _macro_mentions(text_l)
    if macro_hits:
        score += min(14, len(macro_hits) * 5)

    material_hits = sum(1 for kw in MATERIAL_EVENT_KEYWORDS if kw in text_l)
    score += min(18, material_hits * 4)

    low_signal_hits = sum(1 for phrase in LOW_SIGNAL_PHRASES if phrase in text_l)
    score -= min(18, low_signal_hits * 6)

    if not _keyword_mentions_ticker(text, ticker) and not macro_hits and source_l != "llm":
        score -= 14

    return max(10, min(99, score))

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
    return [(item["ticker"], item["direction"]) for item in ranked]


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


def get_price_with_range(ticker, market_open=None):
    """
    Returns (current_price, eval_high, eval_low) for stop/target evaluation.
    MARKET OPEN  : Angel One LTP + day high/low (live, real-time).
    MARKET CLOSED: Yahoo Finance official 3:30 PM close (authoritative NSE close).
                   Falls back to Angel One LTP if Yahoo fails.
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
        # Market closed: authoritative NSE close from Yahoo
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
    Uses meta.regularMarketPrice from the daily chart endpoint — this is
    Yahoo's authoritative live/last-close price and avoids the old bug
    of scanning 1-min candles and returning stale data from the wrong day.
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
        meta = data.get('chart', {}).get('result', [{}])[0].get('meta', {})

        # regularMarketPrice = Yahoo's authoritative current/last-close price
        rmp = meta.get('regularMarketPrice')
        if rmp and rmp > 0:
            close_price = round(float(rmp), 2)
            _YAHOO_CLOSE_CACHE[ticker] = (close_price, now_ts)
            return close_price

        # Fallback: previousClose from meta
        prev = meta.get('chartPreviousClose') or meta.get('previousClose')
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
    global LIVE_NEWS_CACHE, current_key_idx, client, MODEL_NAME, SEEN_HEADLINES
    print("[SYSTEM] Alpha Lens v6.0 AI ENSEMBLE Engine Started!")
    print(f"   Pipeline: RSS -> AI Gatekeeper (Gemini) -> Duplicate Filter -> 7-Model Ensemble (>= 70 score & 5/7 vote)")
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
        conn.close()
    except Exception as e:
        print(f"   [DB Init Error] {e}")
    
    print("   [DEBUG] Starting main loop...")
    sys.stdout.flush()

    def fetch_feed(url):
        stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        articles = []
        try:
            # Use requests with hard timeout, then feed to feedparser
            try:
                resp = HTTP_SESSION.get(url, timeout=8)
                if resp.status_code != 200:
                    return []
                feed = feedparser.parse(resp.content)
            except Exception:
                return []  # Timeout or network error
            
            for entry in feed.entries[:30]:
                pub_time = entry.published if hasattr(entry, 'published') else "Just Now"
                if pub_time and pub_time != "Just Now":
                    try:
                        pub_dt = parsedate_to_datetime(pub_time)
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                        if pub_dt < stale_cutoff:
                            continue
                    except Exception:
                        pass
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
        except Exception as e:
            print(f"   RSS Error for {url}: {e}")
        return articles

    while True:
      try:
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
        
        print(f"[SCRAPE] Got {len(raw_articles)} headlines from all sources")
        sys.stdout.flush()
        
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

            try_order = []
            if current_key_idx in available_keys:
                try_order.append(current_key_idx)
            try_order.extend(i for i in available_keys if i not in try_order)

            _key_idx = try_order[0]
            _ai_client = _set_active_gemini_client(_key_idx)

            if _ai_client:
                print(f"   [AI] Screening batch of {len(articles_batch)} articles...")
                sys.stdout.flush()
                numbered = "\n".join(
                    [
                        f"{i+1}. Headline: {a['headline']}\n"
                        f"   Context: {(a.get('deep_context') or a.get('summary') or 'Not available')[:700]}"
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

                prompt = f"""You are the Chief Investment Strategist at India's top macro hedge fund, managing ₹50,000 Cr AUM. You are NOT a keyword matcher — you are a MACRO STRATEGIST who sees connections that retail traders completely miss.

Your EDGE: You analyze news through HIDDEN SUPPLY CHAINS, GEOPOLITICAL TRANSMISSION, and SECOND/THIRD-ORDER EFFECTS. When retail traders read "Japan restricts semiconductor exports" they see nothing. YOU see: chip shortage → auto production cuts → MARUTI.NS/TMPV.NS BEARISH, IT hardware delays → INFY.NS BEARISH, but chip design outsourcing opportunity → TATAELXSI.NS BULLISH.

Analyze exactly {len(articles_batch)} news items. For EVERY article, think through these HIDDEN CHAINS:

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
                                _mark_gemini_key_quota_hit(_key_idx, cooldown_secs=15)
                                print(f"   [AI] Transient error on key {_key_idx + 1}/{len(API_KEYS)}: {_api_err} — trying next available key (15s cooldown)")
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
            if h_lower and h_lower not in SEEN_HEADLINES and h_lower not in seen_this_batch:
                new_articles.append(a)
                seen_this_batch.add(h_lower)
        
        print(f"   [FILTER] Screened out {len(raw_articles) - len(new_articles)} duplicates. {len(new_articles)} new articles to review.")
        sys.stdout.flush()

        # Smaller batches reduce per-request token load and 429 risk on free-tier keys
        BATCH_SIZE = 10
        screened_signals = []
        for i in range(0, len(new_articles), BATCH_SIZE):
            batch = new_articles[i:i + BATCH_SIZE]
            batch_results = quant_ai_screener(batch)
            if batch_results and all(r.get("reason") in ("AI_COOLDOWN", "AI_QUOTA_EXHAUSTED", "AI_UNAVAILABLE") for r in batch_results):
                print(f"   [AI Worker] AI keys unavailable/exhausted. Suspending remaining {len(new_articles) - i} articles in this cycle so they can be screened next time.")
                sys.stdout.flush()
                break
            screened_signals.extend(batch_results)
            if i + BATCH_SIZE < len(new_articles):
                print("   [AI Batch Spacer] Sleeping 3 seconds to avoid RPM limit...")
                sys.stdout.flush()
                time.sleep(3)
        
        ai_signal_count = sum(1 for s in screened_signals if s.get("ticker"))
        ai_article_count = len({s.get("headline") for s in screened_signals})
        print(f"AI Screener Total: {ai_signal_count} ticker-signals across {ai_article_count} AI-reviewed articles")
        
        # STEP 2: Duplicate Filter + Instant Save + Stock Mapping
        # NOTE: We open/close the DB connection atomically per article to avoid
        # long-held write locks that cause "database is locked" errors when other
        # threads (yfinance_worker, Flask routes) also need to write.
        new_article_ids = []

        for signal in screened_signals:
            headline = signal['headline']
            article_url = signal.get('url')
            ticker = normalize_ticker(signal.get('ticker')) if signal.get('ticker') else None
            base_direction = (signal.get('direction') or '').upper()

            # ── Fast In-Memory Duplicate Check ──
            h_lower = headline.lower().strip()
            if h_lower in SEEN_HEADLINES:
                # Headline already saved; still need to try this ticker signal
                try:
                    _c = connect_news_db()
                    _cur = _c.cursor()
                    _cur.execute("SELECT id FROM news WHERE headline = ? LIMIT 1", (headline,))
                    _row = _cur.fetchone()
                    _c.close()
                    news_id = _row[0] if _row else None
                except Exception:
                    news_id = None
            else:
                SEEN_HEADLINES.add(h_lower)
                try:
                    _c = connect_news_db()
                    _cur = _c.cursor()
                    _cur.execute("SELECT id FROM news WHERE headline = ? LIMIT 1", (headline,))
                    _row = _cur.fetchone()
                    _c.close()
                    news_id = _row[0] if _row else None
                except Exception:
                    news_id = None

                if news_id is None:
                    category = classify_category(headline)
                    _hl = headline
                    _time = signal['time']
                    _cat = category
                    def _insert_news(conn, c, _hl=_hl, _time=_time, _cat=_cat):
                        c.execute('''INSERT OR IGNORE INTO news (headline, news_time, aam_janta_translation, macro_pathway, category)
                            VALUES (?, ?, ?, ?, ?)''',
                            (_hl, _time, None, '[]', _cat))
                        if c.lastrowid:
                            return c.lastrowid
                        c.execute("SELECT id FROM news WHERE headline = ? LIMIT 1", (_hl,))
                        row = c.fetchone()
                        return row[0] if row else None
                    news_id = db_write(_insert_news)
                    if news_id:
                        new_article_ids.append({'id': news_id, 'headline': headline})

            if news_id is None:
                continue

            if not ticker or base_direction not in ("BULLISH", "BEARISH") or not is_supported_equity_ticker(ticker):
                continue

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
                _pub_dt = parsedate_to_datetime(signal['time']).astimezone(_ist)
                _pub_dt_utc_str = _pub_dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

                # Check if news was published during trading hours
                _news_during_market = True
                if _pub_dt.weekday() >= 5 or (_pub_dt.month, _pub_dt.day) in NSE_HOLIDAYS_2026:
                    _news_during_market = False
                else:
                    _t = _pub_dt.hour * 60 + _pub_dt.minute
                    if not ((9 * 60 + 15) <= _t <= (15 * 60 + 30)):
                        _news_during_market = False

                # Get current LTP + prev_close from Angel One
                _ltp, _prev, _, _ = yf._get_cached_quote(ticker)
                _ltp_val = round(float(_ltp), 2) if (_ltp and _ltp > 0) else 0.0
                _prev_val = round(float(_prev), 2) if (_prev and _prev > 0) else 0.0

                if _news_during_market:
                    # News during market hours: get exact price at publication time
                    base_price = get_base_price_at_time(ticker, _pub_dt)
                    current_price_now = _ltp_val if _ltp_val > 0 else _prev_val
                    # If intraday price lookup failed, use prev_close as baseline
                    if base_price <= 0:
                        base_price = _prev_val if _prev_val > 0 else _ltp_val
                else:
                    # NEWS AFTER MARKET HOURS:
                    # base_price = official NSE closing price of today's session
                    # If Yahoo fails, use Angel One prev_close before falling back to LTP.
                    _official_close = _get_yahoo_official_close(ticker)
                    if _official_close and _official_close > 0:
                        base_price = _official_close
                        current_price_now = _official_close
                        print(f"   [Price] {ticker}: After-hours → base=current={base_price} (Yahoo official close, 0% until market opens)")
                    elif _prev_val > 0:
                        base_price = _prev_val
                        current_price_now = _prev_val
                        print(f"   [Price] {ticker}: After-hours → base=current={base_price} (AO prev_close fallback, 0% until market opens)")
                    elif _ltp_val > 0:
                        base_price = _ltp_val
                        current_price_now = _ltp_val
                        print(f"   [Price] {ticker}: After-hours → base=current={base_price} (AO LTP fallback, 0% until market opens)")


            except Exception as _e:
                if not _pub_dt_utc_str:
                    _pub_dt_utc_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
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

            # ── ATR-BASED DYNAMIC STOP & TARGET ──
            # ATR (Average True Range) measures a stock's typical daily price swing.
            # We set stop = max(1.0, atr_pct * 1.0) and target = max(2.0, atr_pct * 2.0)
            # This gives a consistent 2:1 Reward:Risk ratio regardless of stock volatility.
            _atr_pct = 0.0
            if tech_data and tech_data.get('atr_pct'):
                _atr_pct = float(tech_data['atr_pct'])
            if _atr_pct > 0:
                _dynamic_stop   = round(min(2.5, max(1.0, _atr_pct * 1.0)), 2)  # cap at 2.5%
                _dynamic_target = round(min(5.0, max(2.0, _atr_pct * 2.0)), 2)  # cap at 5% (2:1 Reward:Risk)
            else:
                _dynamic_stop   = TRADE_STOP_PCT    # fallback to config default
                _dynamic_target = TRADE_TARGET_PCT
            print(f"   [ATR] {ticker}: ATR={_atr_pct:.2f}% → stop={_dynamic_stop:.2f}% target={_dynamic_target:.2f}%")

            # Predict using Ensemble
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
                precalculated_score=signal.get("quality_score")
            )

            if result['approved']:
                view = 'High Conviction' if result['final_score'] >= 85 else 'Moderate Conviction'
                quality = signal.get("quality_score")
                quality_note = f" Stock-pick quality: {quality}/100." if quality else ""
                reason = (f"Ensemble Score: {result['final_score']} ({result['models_agreeing']}/5 models approve). "
                          f"ATR stop: {_dynamic_stop:.1f}% | target: {_dynamic_target:.1f}%.{quality_note}")
                approved_signals.append((news_id, ticker, result['direction'], _dynamic_target,
                                         view, reason, base_price, current_price_now,
                                         result['final_score'], tech_context_str, result['detail'], _pub_dt_utc_str))
                # Mark this ticker+direction as recently signalled to prevent duplicates
                RECENT_SIGNALS[_cooldown_key] = _now_utc
                # Prune old entries to keep dict lean (remove entries older than 48h)
                cutoff = _now_utc - timedelta(hours=48)
                RECENT_SIGNALS.update({k: v for k, v in RECENT_SIGNALS.items() if v > cutoff})

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
                                new_dt = datetime.utcnow()
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
                                    boosted_conf = min(99, max(db_conf, conf) + 10)
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
        time.sleep(180)  # Poll every 3 minutes for fresh news

        
        # ============================================================
        # PHASE 2: BACKGROUND — Batch Gemini for explanations only
        # ============================================================
        # Find all articles missing AI explanation
        conn = connect_news_db()
        c = conn.cursor()
        c.execute("SELECT id, headline FROM news WHERE aam_janta_translation IS NULL ORDER BY created_at DESC LIMIT 5")
        pending_articles = [{'id': r[0], 'headline': r[1]} for r in c.fetchall()]
        conn.close()
        
        if pending_articles:
            print(f"[Phase 2] Batch AI explanations for {len(pending_articles)} articles (5 per API call)...")
            
            # Process in batches of 5 headlines per single Gemini call
            for i in range(0, len(pending_articles), 5):
                batch = pending_articles[i:i+5]
                headlines_text = "\n".join([f"{j+1}. {a['headline']}" for j, a in enumerate(batch)])
                
                prompt = f"""You are a financial journalist writing for everyday Indians.
For each headline below, provide:
1. "aam_janta_translation": A 2-sentence explanation in simple language about what this means for common people.
2. "macro_pathway": A 4-step chain showing the macro impact flow.

Headlines:
{headlines_text}

Output STRICT valid JSON array:
[
  {{
    "index": 1,
    "aam_janta_translation": "Simple 2-sentence explanation for common people.",
    "macro_pathway": ["Trigger Event", "Direct Impact", "Ripple Effect", "End Result"]
  }}
]"""
                
                success = False
                explain_keys = _available_gemini_key_indices() or list(range(len(API_KEYS)))
                explain_order = []
                if current_key_idx in explain_keys:
                    explain_order.append(current_key_idx)
                explain_order.extend(k for k in explain_keys if k not in explain_order)

                for _key_idx in explain_order:
                    try:
                        _set_active_gemini_client(_key_idx)
                        resp = client.models.generate_content(
                            model=MODEL_NAME,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json"
                            )
                        )
                        analyses = clean_json(resp.text)
                        if not isinstance(analyses, list):
                            analyses = [analyses]

                        with DB_WRITE_LOCK:
                            conn = connect_news_db()
                            c = conn.cursor()
                            _analyses = analyses
                            _batch = batch
                            for analysis in _analyses:
                                idx = analysis.get('index', 0) - 1
                                if 0 <= idx < len(_batch):
                                    news_id = _batch[idx]['id']
                                    c.execute('''UPDATE news SET aam_janta_translation = ?, macro_pathway = ? WHERE id = ?''',
                                        (analysis.get('aam_janta_translation', 'Analysis complete.'),
                                         json.dumps(analysis.get('macro_pathway', [])),
                                         news_id))
                            conn.commit()
                            conn.close()

                        print(f"   [+] Batch {i//5 + 1}: Explained {len(batch)} articles (key {_key_idx + 1})")
                        success = True
                        break
                    except Exception as e:
                        if _is_gemini_quota_error(e):
                            _mark_gemini_key_quota_hit(_key_idx)
                            print(f"   [!] Quota on key {_key_idx + 1}/{len(API_KEYS)} — rotating...")
                            time.sleep(2)
                            continue
                        print(f"   [-] Batch Gemini Error: {str(e)[:80]}")
                        break
                
                if not success:
                    print(f"   [-] Failed batch {i//5 + 1} after trying all available keys")
                
                time.sleep(2)  # Small delay between batches
        
        # Clean up old news (older than 7 days)
        try:
            seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
            def _cleanup(conn, c):
                c.execute("DELETE FROM stock_impact WHERE news_id IN (SELECT id FROM news WHERE created_at < ?)", (seven_days_ago,))
                c.execute("DELETE FROM news WHERE created_at < ?", (seven_days_ago,))
            db_write(_cleanup)
        except Exception as e:
            print("DB Cleanup Error:", e)
            
        # Performance report
        try:
            import performance_report
            print("\n" + "="*60)
            print(" END OF CYCLE — PERFORMANCE REPORT:")
            print("="*60)
            performance_report.run_performance_check()
        except Exception as e:
            print("Performance Report Error:", e)
        # Note: main loop sleep is 180s at start of cycle (3-minute news polling)
      except Exception as _loop_err:
        print(f"[FATAL LOOP ERROR] {_loop_err}")
        import traceback; traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        time.sleep(30)  # Wait 30s before retrying

def get_price_with_range(ticker, market_open=None):
    """
    Returns (current_price, eval_high, eval_low) for stop/target evaluation.
    Uses the shim's 30-second quote cache — no extra API call needed.
    """
    if market_open is None:
        market_open = is_market_open()

    # Single cached call gets ltp + day_high + day_low
    ltp, prev, dh, dl = yf._get_cached_quote(ticker)
    if not ltp or ltp <= 0:
        return None, None, None

    current = round(float(ltp), 2)

    # Only use intraday high/low when market is OPEN
    if market_open:
        eval_high = round(float(dh), 2) if dh and dh > 0 else current
        eval_low  = round(float(dl), 2) if dl and dl > 0 else current
    else:
        eval_high = current
        eval_low  = current

    return current, eval_high, eval_low


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
                if l_pct <= -stop_pct:  return 'Stop Loss Hit',       round(l_pct, 2)
                if h_pct >= target_pct: return 'Predicted Target Hit', round(h_pct, 2)
            else:
                if h_pct >= stop_pct:    return 'Stop Loss Hit',       round(h_pct, 2)
                if l_pct <= -target_pct: return 'Predicted Target Hit', round(l_pct, 2)
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
    return (9 * 60 + 15) <= minutes <= (15 * 60 + 30)


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
                if abs(base_price - next_open) > 0.01:
                    base_price = next_open
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
            new_status = 'Expired' if age_hours >= 72 else 'Active View'

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

                # ── KEY RULE: After-hours signals → base_price = NEXT OPEN PRICE ──
                # This MUST run before the historical hit check below.
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
                            (9 * 60 + 15) <= _t <= (15 * 60 + 30)
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
                                if abs(base_price - _next_open_price) > 0.01:
                                    base_price = _next_open_price
                                    def _update_base(conn, c, _sid=stock_id, _bp=base_price):
                                        c.execute("UPDATE stock_impact SET base_price=? WHERE id=?", (_bp, _sid))
                                    db_write(_update_base)
                                # Historical check must start from NEXT session, not signal day
                                _hist_start_date = _next_session_date

                            # If next trading session hasn't started yet, keep diff at 0%
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
                            if age_hours >= 72:
                                new_status = 'Expired'
                        except Exception:
                            pass

                    # Only log to patterns if status just changed right now
                    if new_status in ['Predicted Target Hit', 'Stop Loss Hit']:
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

        except Exception as e:
            print("YFinance Worker Error:", e)

        # Poll every 60 seconds always (fast enough to initialize new news prices quickly)
        time.sleep(60)

# Threading starts moved to main block to prevent Flask reloader duplicate race conditions.

# ==========================================
# APP ROUTES
# ==========================================
@app.route('/')
def home():
    resp = make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '-1'
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
    return day.weekday() < 5 and (day.month, day.day) not in NSE_HOLIDAYS_2026


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
    
    # Cache any successfully populated results to prevent API spamming
    if result:
        _INDEX_CACHE = result
        _INDEX_CACHE_TIME = time.time()
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
        except:
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

@app.route('/api/news/all', methods=['GET'])
def get_all_news():
    try:
        conn = connect_news_db()
        c  = conn.cursor()
        c2 = conn.cursor()
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute("SELECT * FROM news WHERE created_at >= ? ORDER BY created_at DESC", (seven_days_ago,))
        news_rows = c.fetchall()

        # Build column name list from cursor description
        news_cols = [desc[0] for desc in c.cursor.description] if hasattr(c, 'cursor') else [desc[0] for desc in c.description]

        all_news = []
        mkt_open = is_market_open()
        quote_cache = {}
        for raw_row in news_rows:
            news_item = dict(zip(news_cols, raw_row))
            try:
                news_item['macro_pathway'] = json.loads(news_item.get('macro_pathway') or '[]')
            except:
                news_item['macro_pathway'] = []
            c2.execute("SELECT * FROM stock_impact WHERE news_id = ?", (news_item['id'],))
            raw_stocks_rows = c2.fetchall()
            impact_cols = [desc[0] for desc in c2.cursor.description] if hasattr(c2, 'cursor') else [desc[0] for desc in c2.description]
            raw_stocks = [dict(zip(impact_cols, r)) for r in raw_stocks_rows]
            # Deduplicate by ticker — keep highest confidence score
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
                if not mkt_open and status == 'Active View' and not has_market_traded_since(news_item.get('news_time') or news_item.get('created_at')):
                    s['current_price'] = bp
                    s['diff_pct'] = 0.0
                    s['market_change_pct'] = 0.0
                    s['_skip_market_quote'] = True
                    cp = bp
                is_closed = s.get('status') in ['Stop Loss Hit', 'Predicted Target Hit', 'Reacted Against Prediction', 'Expired']
                if is_closed and s.get('estimated_change_percent') is not None:
                    s['diff_pct'] = round(s.get('estimated_change_percent'), 2)
                elif bp > 0 and cp > 0:
                    s['diff_pct'] = round((cp - bp) / bp * 100, 2)
                else:
                    s['diff_pct'] = None
            attach_market_change_percentages(stocks, market_open=mkt_open, quote_cache=quote_cache)
            news_item['affected_stocks'] = stocks
            all_news.append(news_item)

        conn.close()
        return jsonify({"market_open": mkt_open, "news": all_news})
    except Exception as e:
        print("Error fetching all news", e)
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

COMMON_EXTERNAL_STOCK_ALIASES = {
    "tesla": "TSLA",
    "tsla": "TSLA",
    "apple": "AAPL",
    "aapl": "AAPL",
    "microsoft": "MSFT",
    "msft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "googl": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "nvidia": "NVDA",
    "nvda": "NVDA",
    "amazon": "AMZN",
    "amzn": "AMZN",
    "bitcoin": "BTC",
    "btc": "BTC",
    "ethereum": "ETH",
    "eth": "ETH",
}

GENERIC_STOCK_NAME_WORDS = {
    "limited", "ltd", "industries", "industry", "india", "indian", "company",
    "corporation", "corp", "bank", "finance", "financial", "services",
    "service", "group", "holdings", "holding", "enterprise", "enterprises",
}

OUT_OF_SCOPE_TOPIC_WORDS = {
    "weather", "rain", "temperature", "cricket", "football", "movie",
    "movies", "song", "songs", "recipe", "travel", "hotel", "politics",
    "election", "celebrity", "astrology", "horoscope",
}

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
    global client, current_key_idx
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
            print(f"Portfolio assistant AI timeout on key {active_idx + 1}; rotating...")
            get_and_rotate_client(active_idx, is_timeout=True)
        except Exception as e:
            print(f"Portfolio assistant AI error on key {active_idx + 1}: {e}; rotating...")
            is_quota = _is_gemini_quota_error(e)
            get_and_rotate_client(active_idx, is_timeout=False, is_quota=is_quota)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    return None

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

    external = detect_external_tickers(question, portfolio_tickers)
    if external:
        return assistant_response(
            "I can only answer from your added portfolio stocks and their saved news.",
            "blocked",
            context_count=0,
            blocked_tickers=external,
        )

    context_items, normalized_tickers = get_portfolio_news_context(portfolio_tickers)

    if not is_portfolio_news_question(question, context_items, normalized_tickers, portfolio_names):
        return assistant_response(
            "I can only answer from your added portfolio stocks and their saved news.",
            "blocked",
            context_count=len(context_items),
            tickers=normalized_tickers,
            skipped_ai=True,
        )

    if not context_items:
        return assistant_response(
            "I do not have saved portfolio-linked news for those added stocks yet.",
            "no_context",
            context_count=0,
            tickers=normalized_tickers,
        )

    requested_bases = mentioned_portfolio_bases(question, normalized_tickers, portfolio_names)
    answer_context_items = rank_portfolio_context_items(question, context_items, requested_bases)
    if requested_bases and not answer_context_items:
        return assistant_response(
            f"I do not have saved portfolio-linked news for {', '.join(sorted(requested_bases))} yet.",
            "no_context",
            context_count=0,
            tickers=normalized_tickers,
            matched_tickers=sorted(requested_bases),
        )

    local_answer = fallback_portfolio_answer(question, answer_context_items, normalized_tickers, portfolio_names)

    if not should_try_portfolio_ai(question):
        return assistant_response(
            local_answer,
            "local",
            context_count=len(answer_context_items),
            tickers=normalized_tickers,
            matched_tickers=sorted(requested_bases),
            skipped_ai=True,
        )

    context_lines = []
    for item in answer_context_items:
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

    prompt = f"""You are Alpha Lens Portfolio Assistant.
You may answer ONLY using the user's added portfolio tickers and the saved news context below.
If the question is outside these portfolio tickers or outside this news context, say exactly:
"I can only answer from your added portfolio stocks and their saved news."
Do not invent facts, prices, targets, or news. Answer only the question asked.

Format the answer in clean Markdown:
**Answer**
1-3 short sentences.

**Portfolio Impact**
- TICKER: direction, confidence, and what the saved news implies.

**News Used**
- The exact saved headline(s) used.

If there is not enough saved context, say that clearly instead of guessing.

Portfolio tickers: {', '.join(normalized_tickers)}

Saved portfolio news context:
{chr(10).join(context_lines)}

Question: {question}
"""
    answer = run_portfolio_ai_with_timeout(prompt)
    if answer and "I can only answer from your added portfolio stocks and their saved news." not in answer:
        return assistant_response(
            answer,
            "ai",
            context_count=len(answer_context_items),
            tickers=normalized_tickers,
            matched_tickers=sorted(requested_bases),
        )

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

    otp = str(random.randint(100000, 999999))
    OTP_STORE[email] = (otp, time.time() + 600)

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

    stored_otp, expiry = OTP_STORE[email]
    if time.time() > expiry or stored_otp != user_otp:
        del OTP_STORE[email]
        return jsonify({"error": "Invalid or expired OTP."}), 401

    del OTP_STORE[email]

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
                symbol=excluded.symbol,
                name=CASE
                    WHEN stock_universe.source = 'curated' THEN stock_universe.name
                    ELSE excluded.name
                END,
                exchange=excluded.exchange,
                source=CASE
                    WHEN stock_universe.source = 'curated' THEN stock_universe.source
                    ELSE excluded.source
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
                     (source = 'curated') DESC,
                     LENGTH(symbol),
                     symbol
            LIMIT ?
        """, (q, q, q, prefix, prefix, prefix, like, like, like, limit))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return [
            {
                "name":     r.get("name") or r.get("symbol") or r.get("ticker"),
                "ticker":   r.get("ticker"),
                "exchange": r.get("exchange"),
                "source":   r.get("source"),
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[Stock Search] DB search failed: {e}")
        return []

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
        c.execute("""
            SELECT current_price, technical_context
            FROM stock_impact
            WHERE UPPER(ticker) = ? AND current_price > 0
            ORDER BY datetime(created_at) DESC, id DESC
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

    lp, prev = yf.get_ltp(ticker)
    price = _positive_float(lp)
    prev_close = _positive_float(prev)
    price_label = "Live" if market_open else "Last Close"

    if market_open:
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
        cutoff = (datetime.now(timezone.utc) - timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute("""
            SELECT si.id, si.ticker, si.impact, si.confidence_score,
                   si.base_price, si.current_price, si.estimated_change_percent,
                   si.status, si.created_at, si.view, si.reason, si.ensemble_detail,
                   n.headline
            FROM stock_impact si
            LEFT JOIN news n ON si.news_id = n.id
            WHERE si.created_at >= ?
            ORDER BY si.confidence_score DESC, si.created_at DESC
            LIMIT 200
        """, (cutoff,))
        rows = c.fetchall()
        conn.close()

        mkt_open = is_market_open()
        signals = []
        for row in rows:
            d = dict(row)
            bp = d.get('base_price') or 0
            cp = d.get('current_price') or bp
            status = d.get('status', 'Active View')
            created_at = d.get('created_at')

            # If market hasn't traded since news publication, show entry price as current price
            if not mkt_open and status == 'Active View' and not has_market_traded_since(created_at):
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
        return jsonify({'signals': signals, 'count': len(signals), 'market_open': is_market_open()})
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
    ("LTI","LTIMindtree","IT"),
]

@app.route('/api/heatmap-data', methods=['GET'])
def get_heatmap_data():
    try:
        conn = connect_news_db()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute("""
            SELECT ticker, impact, confidence_score, status,
                   base_price, current_price, estimated_change_percent, created_at
            FROM stock_impact
            WHERE created_at >= ?
            ORDER BY confidence_score DESC, created_at DESC
        """, (cutoff,))
        db_signals = {}
        for row in c.fetchall():
            t = (row['ticker'] or '').replace('.NS', '').replace('.BO', '')
            if t not in db_signals:
                db_signals[t] = dict(row)
        conn.close()

        result = []
        for (sym, name, sector) in NIFTY50_UNIVERSE:
            sig = db_signals.get(sym, {})
            impact = (sig.get('impact') or '').lower()
            is_bull = 'bullish' in impact
            is_bear = 'bearish' in impact
            has_signal = bool(impact)

            bp = sig.get('base_price') or 0
            cp = sig.get('current_price') or 0
            est = sig.get('estimated_change_percent')
            status = sig.get('status', '')

            if status in ('Predicted Target Hit', 'Stop Loss Hit') and est is not None:
                diff_pct = round(float(est), 2)
            elif bp > 0 and cp > 0:
                diff_pct = round((cp - bp) / bp * 100, 2)
            else:
                diff_pct = 0.0

            result.append({
                'symbol': sym,
                'name': name,
                'sector': sector,
                'has_signal': has_signal,
                'direction': 'BULLISH' if is_bull else ('BEARISH' if is_bear else 'NEUTRAL'),
                'confidence': sig.get('confidence_score') or 0,
                'diff_pct': diff_pct,
                'status': status,
            })

        return jsonify({'stocks': result, 'market_open': is_market_open()})
    except Exception as e:
        print(f"[heatmap-data] Error: {e}")
        return jsonify({'stocks': [], 'market_open': False})


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


@app.route('/api/debug-sql-runner', methods=['POST'])
def debug_sql_runner():
    token = request.headers.get("X-Alpha-Lens-Token")
    if token != "alpha-lens-super-secret":
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
            cols = [desc[0] for desc in c.cursor.description] if hasattr(c, 'cursor') else [desc[0] for desc in c.description]
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


def start_background_workers():
    engine_thread = threading.Thread(target=ai_news_worker, daemon=True)
    engine_thread.start()

    yf_thread = threading.Thread(target=yfinance_worker, daemon=True)
    yf_thread.start()

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

if __name__ == '__main__':
    # Small delay so DB is fully ready before workers start writing
    time.sleep(2)

    parser = argparse.ArgumentParser(description='Alpha Lens backend startup mode')
    parser.add_argument('--workers-only', action='store_true', help='Run background workers without launching the Flask UI')
    parser.add_argument('--skip-workers', action='store_true', help='Do not start background workers')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 5000)), help='Port for the Flask UI')
    args = parser.parse_args()

    run_workers = not args.skip_workers and os.environ.get("ALPHA_LENS_SKIP_WORKERS", "").lower() not in ("1", "true", "yes")
    if args.workers_only:
        run_workers = True

    if os.environ.get("ALPHA_LENS_SKIP_AUTO_REPAIR", "").lower() not in ("1", "true", "yes"):
        repair_existing_signal_statuses(days=14)

    engine_thread = None
    yf_thread = None
    if run_workers:
        engine_thread, yf_thread = start_background_workers()
    else:
        print("[SYSTEM] Background workers skipped for local UI server.")

    if args.workers_only:
        print("[SYSTEM] Worker-only mode active. Flask UI is disabled.")
        engine_thread.join()
        yf_thread.join()
    else:
        # Threaded=True allows the background AI loop to run alongside the website
        # use_reloader=False prevents double execution of our background threads on restart
        debug_mode = os.environ.get("FLASK_ENV") == "development"
        app.run(debug=debug_mode, host='0.0.0.0', port=args.port, threaded=True, use_reloader=False)

