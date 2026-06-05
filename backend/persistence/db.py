"""
Database layer — connection wrappers (SQLite/Postgres placeholder bridging),
the Postgres connection pool, connect_news_db / connect_users_db, and the
db_write() write-with-retry helper. Extracted verbatim from app.py.

Self-contained: stdlib + (lazy) psycopg2 only — it imports nothing from app,
so there is no import cycle. _APP_DIR is recomputed here from __file__ as the
PARENT of this file's dir (this module lives in backend/persistence/, the DBs
live in backend/). app.py imports the public names back, so all 60+ call sites
resolve unchanged. The schema builders (init_db / init_news_db) live in
persistence/schema.py.
"""
import os
import time
import sqlite3
import threading


# Global write lock — ensures only one thread writes to SQLite at a time.
# Reads do NOT need this lock (WAL mode allows concurrent reads).
DB_WRITE_LOCK = threading.Lock()

# Use absolute paths so the server works from any working directory.
# This module lives in backend/persistence/, but the SQLite DBs live in
# backend/ — so _APP_DIR is the PARENT of this file's dir (one level up from
# the persistence package). Getting this wrong would make sqlite3.connect()
# create an empty news_cache.db inside persistence/ and silently run on it.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
        try:
            import psycopg2.extras
            psycopg2.extras.execute_batch(self.cursor, sql_translated, seq_of_parameters)
        except Exception as e:
            print(f"   [DB] execute_batch failed: {e}. Falling back to standard executemany.", flush=True)
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
        # T2.8: If this wrapper owns a pooled Postgres connection, return it
        # to the pool instead of physically closing the socket. That's the
        # whole point of the pool — avoid the TCP+TLS handshake on every req.
        if self.is_postgres and getattr(self, '_pooled', False):
            try:
                # Rollback any uncommitted state so the next checkout starts clean
                try: self.conn.rollback()
                except Exception: pass
                _PG_POOL.putconn(self.conn)
            except Exception:
                # If pool return fails for any reason, just close directly
                try: self.conn.close()
                except Exception: pass
            return
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)


# ── T2.8: Postgres connection pool ──
# One ThreadedConnectionPool per process, lazily created on first checkout.
# Render free tier Postgres has ~10 max connections, so we cap at 8 to leave
# room for the worker thread + any out-of-band tools. Each checkout-then-
# return is microseconds vs ~50-200ms for a fresh psycopg2.connect().
_PG_POOL = None
_PG_POOL_LOCK = __import__('threading').Lock()

def _get_pg_pool(db_url):
    global _PG_POOL
    if _PG_POOL is not None:
        return _PG_POOL
    with _PG_POOL_LOCK:
        if _PG_POOL is not None:
            return _PG_POOL
        try:
            from psycopg2.pool import ThreadedConnectionPool
            _PG_POOL = ThreadedConnectionPool(
                minconn=1,
                maxconn=int(os.environ.get('PG_POOL_MAX', '8')),
                dsn=db_url,
                connect_timeout=10,
            )
            print(f"[PERF] Postgres connection pool initialized (max={int(os.environ.get('PG_POOL_MAX', '8'))})")
        except Exception as e:
            print(f"[PERF] Postgres pool init FAILED: {e} — falling back to per-request connect")
            _PG_POOL = False  # sentinel: pool unavailable, never retry
    return _PG_POOL


def connect_postgres_db(db_url):
    import psycopg2
    # Try pool first; fall back to direct connect if pool is unavailable.
    pool = _get_pg_pool(db_url)
    if pool and pool is not False:
        try:
            conn = pool.getconn()
            # Quick liveness check — pooled connections sometimes go stale.
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
                cur.close()
            except Exception:
                # Stale — discard and grab another (or fresh-connect)
                try: pool.putconn(conn, close=True)
                except Exception:
                    try: conn.close()
                    except Exception: pass
                conn = psycopg2.connect(db_url, connect_timeout=10)
                wrapper = ConnectionWrapper(conn, is_postgres=True)
                wrapper._pooled = False
                return wrapper
            wrapper = ConnectionWrapper(conn, is_postgres=True)
            wrapper._pooled = True  # close() returns to pool
            return wrapper
        except Exception as e:
            print(f"[PERF] Pool checkout failed ({e}), using direct connect")
    # Fallback: original behavior
    conn = psycopg2.connect(db_url, connect_timeout=10)
    wrapper = ConnectionWrapper(conn, is_postgres=True)
    wrapper._pooled = False
    return wrapper


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

def db_write(fn, retries=3, delay=1.0):
    """
    Execute a write operation (fn) under DB_WRITE_LOCK with automatic retry.
    fn receives (conn, cursor) and should NOT call commit/close.
    Returns the value returned by fn, or None on failure.
    """
    for attempt in range(retries):
        with DB_WRITE_LOCK:
            conn = None  # Bug #9 fix: initialise before try so except handler never hits NameError
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
                except Exception:  # bare 'except:' would swallow KeyboardInterrupt/SystemExit
                    pass
                try:
                    conn.close()
                except Exception:  # same reason
                    pass
                
                if is_operational and attempt < retries - 1:
                    print(f"   [DB] Write locked/operational error ({exc_name}), retry {attempt+1}/{retries}...")
                    time.sleep(delay)
                else:
                    print(f"   [DB] Write failed after {retries} retries: {e}")
                    break
    return None
