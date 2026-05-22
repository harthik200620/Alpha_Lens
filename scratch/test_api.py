import requests
import json

try:
    r = requests.get('http://127.0.0.1:5000/api/news/all')
    data = r.json()
    news = data.get('news', [])
    found = False
    for n in news:
        for s in n.get('affected_stocks', []):
            ticker = s.get('ticker')
            if ticker in ('IOC.NS', 'ONGC.NS'):
                print(f"Ticker: {ticker}")
                print(f"  Base Price:          {s.get('base_price')}")
                print(f"  Current Price:       {s.get('current_price')}")
                print(f"  Diff %:              {s.get('diff_pct')}")
                print(f"  Market Change %:     {s.get('market_change_pct')}")
                print(f"  Status:              {s.get('status')}")
                found = True
    if not found:
        print("IOC.NS and ONGC.NS signals not found in the news list.")
except Exception as e:
    print(f"Error querying API: {e}")
