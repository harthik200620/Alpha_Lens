import sqlite3
conn = sqlite3.connect('news_cache.db')
c = conn.cursor()

# Delete news items that have no stock impacts.
# This forces the RSS poller to re-evaluate them and generate pristine stock_impact rows.
c.execute("DELETE FROM news WHERE id NOT IN (SELECT news_id FROM stock_impact)")
conn.commit()

print("Deleted orphan news:", c.rowcount)
conn.close()
