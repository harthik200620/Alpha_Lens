import requests

_h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"}

for ticker in ["TMPV.NS", "TMCV.NS", "TATAMOTORS.NS"]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
    try:
        resp = requests.get(url, headers=_h, timeout=8)
        data = resp.json()
        meta = data.get('chart', {}).get('result', [{}])[0].get('meta', {})
        rmp = meta.get('regularMarketPrice')
        print(f"Ticker {ticker}: regularMarketPrice = {rmp}")
    except Exception as e:
        print(f"Ticker {ticker} failed: {e}")
