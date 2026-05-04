import requests

h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

tickers_to_test = ["TATAMOTORS.NS", "TATAMOTORS.BO", "500570.BO"]
endpoints = [
    "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=5d&interval=1d",
    "https://query2.finance.yahoo.com/v8/finance/chart/{}?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v7/finance/quote?symbols={}",
]

for ticker in tickers_to_test:
    for ep in endpoints:
        url = ep.format(ticker)
        try:
            r = requests.get(url, headers=h, timeout=8)
            data = r.json()
            # check v8 chart
            if 'chart' in data:
                result = data['chart'].get('result')
                if result:
                    meta = result[0]['meta']
                    print(f"OK   {ticker:20s} via {url[:50]}  price={meta.get('regularMarketPrice')}")
                else:
                    print(f"ERR  {ticker:20s} via {url[:50]}  {data['chart']['error']}")
            # check v7 quote
            elif 'quoteResponse' in data:
                res = data['quoteResponse'].get('result', [])
                if res:
                    print(f"OK   {ticker:20s} via {url[:50]}  price={res[0].get('regularMarketPrice')}")
                else:
                    print(f"ERR  {ticker:20s} via {url[:50]}  no results")
        except Exception as e:
            print(f"EXC  {ticker:20s} via {url[:50]}  {e}")
