import sys, json, re
from google import genai
API_KEYS = [
    'AIzaSyABS1FGUxLRNcekIfquMcIKcGVjKd-bGq4',
    'AIzaSyCt_GQ1Z39bpkIZMjRZtjmyx-zjxqiFlUw',
    'AIzaSyCUJbHzWvCYzokef_NyXKNWQ6ywniO-wb4',
    'AIzaSyA6En5i8Bpr6_lPKWSMecchwRfHruHw0tU'
]
client = genai.Client(api_key=API_KEYS[0])
prompt = '''As a top-tier quantitative researcher in the Indian equities market, evaluate this headline: 'Reliance announces 1000 crore profit'. Look for deep secondary-order effects, hidden supply/demand constraints, unspoken regulatory impacts, or institutional positioning triggers. 
1) Identify ALL primary or secondary NSE/BSE stock tickers implicitly impacted by this news to ensure no opportunities are missed (append .NS to the ticker, e.g., RELIANCE.NS).
2) Determine the actionable forward-looking bias for each (BULLISH/BEARISH/NEUTRAL). 
3) Classify the overall materiality (MATERIAL/IGNORE) — drop retail fluff, flag news capable of causing structural repricing.
Return exactly formatted JSON like this:
{
  "materiality": "MATERIAL",
  "impacts": [
    {"ticker": "TCS.NS", "bias": "BULLISH"}
  ]
}
'''
try:
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    print(response.text)
except Exception as e:
    print('Error:', e)
