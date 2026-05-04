import requests, json

r = requests.get('http://127.0.0.1:5000/api/news/all', timeout=10).json()
stocks = [s for n in r.get('news', []) for s in n.get('affected_stocks', [])]

print(f"Total signals: {len(stocks)}\n")
for s in stocks:
    print(f"  {s['ticker']:20s} | {s['status']:25s} | base={s.get('base_price')} | diff_pct={s.get('diff_pct')}")
