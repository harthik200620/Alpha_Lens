import requests
import json

_h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"}
url = "https://query1.finance.yahoo.com/v8/finance/chart/TATAMOTORS.NS?range=5d&interval=1d"

try:
    resp = requests.get(url, headers=_h, timeout=8)
    print("Status code:", resp.status_code)
    print("Response text:", resp.text[:1000])
except Exception as e:
    print("Error:", e)
