import urllib.request
import json

def verify():
    url = "http://127.0.0.1:5000/api/signal-terminal"
    try:
        response = urllib.request.urlopen(url)
        data = json.loads(response.read().decode('utf-8'))
        signals = data.get('signals', [])
        print(f"Total signals returned: {len(signals)}")
        
        ongc_signals = [s for s in signals if "ONGC" in s['ticker']]
        print(f"ONGC signals found: {len(ongc_signals)}")
        for idx, s in enumerate(ongc_signals):
            print(f"[{idx+1}] ID: {s['id']}, Ticker: {s['ticker']}, Dir: {s['direction']}, Status: {s['status']}, Conf: {s['confidence']}, Entry: {s['entry']}, Current: {s['current']}, Progress: {s['progress_pct']}%, Headline: {s['headline']}")
            
        print("\nAll active/stopped signals:")
        for idx, s in enumerate(signals[:10]):
            print(f" - {s['ticker']} ({s['direction']}): {s['status']} | Conf: {s['confidence']} | {s['headline']}")
            
    except Exception as e:
        print("Verification failed:", e)

if __name__ == "__main__":
    verify()
