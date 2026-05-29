import sqlite3
import sys
p='backend/news_cache.db'
try:
    conn=sqlite3.connect(p)
    c=conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print('tables:', c.fetchall())
    c.execute('SELECT count(*) FROM news')
    print('news_count:', c.fetchone()[0])
    conn.close()
except Exception as e:
    print('ERR',e)
    sys.exit(1)
