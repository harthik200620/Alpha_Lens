import requests
r = requests.get('http://127.0.0.1:5000/api/indices', timeout=15).json()
for i in r:
    print(f"{i['name']:15s}  price={i['price']}  change={i['change_pct']}%  label={i['price_label']}")
