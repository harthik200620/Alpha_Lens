"""Verify API response shows correct prices."""
import requests, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

r = requests.get('http://127.0.0.1:5000/api/news/all')
data = r.json()
news_list = [n for n in data.get('news', []) if n.get('affected_stocks')]
print(f"{len(news_list)} news items with stocks\n")

for n in news_list[:5]:
    print(f"NEWS: {n['headline'][:60]}...")
    for s in n.get('affected_stocks', []):
        bp = s.get('base_price', 0)
        cp = s.get('current_price', 0)
        dp = s.get('diff_pct')
        st = s.get('status', '')
        print(f"  {s['ticker']} | base={bp} cur={cp} diff_pct={dp}% | {st}")
    print()
