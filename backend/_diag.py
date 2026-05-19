"""Test the AI screener directly with a known-good headline"""
import os, sys, json
sys.path.insert(0, r"C:\Project rohan\Alpha_Lens\backend")
from dotenv import load_dotenv
load_dotenv(r"C:\Project rohan\Alpha_Lens\.env")

from google import genai

keys = [v for k, v in sorted(os.environ.items()) if k.startswith("GEMINI_API_KEY") and v]
client = genai.Client(api_key=keys[0])

# Use one of the actual headlines from the DB
import sqlite3
conn = sqlite3.connect(r"C:\Project rohan\Alpha_Lens\backend\news_cache.db")
c = conn.cursor()
c.execute("SELECT headline FROM news LIMIT 10")
headlines = [r[0] for r in c.fetchall()]
conn.close()

print(f"Testing with {len(headlines)} headlines:\n")
for h in headlines:
    print(f"  - {h[:80]}")

numbered = "\n".join([
    f"{i+1}. Headline: {h}\n   Context: Not available"
    for i, h in enumerate(headlines)
])

schema_example = json.dumps([
    {"index": 1, "material": True, "catalyst_type": "EARNINGS_BEAT", "materiality_score": 87,
     "impacts": [{"ticker": "TCS.NS", "direction": "BULLISH", "confidence": 88, "impact_type": "DIRECT",
                  "reason": "Q4 PAT beat consensus."}]},
    {"index": 2, "material": False, "catalyst_type": "NOISE", "materiality_score": 12, "impacts": []}
], indent=2)

prompt = f"""You are the Chief Investment Strategist at India's top macro hedge fund.
Analyze these {len(headlines)} news items and identify stocks affected through hidden supply chains and macro transmission.

News items:
{numbered}

Return ONLY valid JSON matching this shape:
{schema_example}"""

print(f"\n--- Calling Gemini ---")
try:
    resp = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=prompt,
    )
    raw = resp.text.strip()
    print(f"Raw response length: {len(raw)}")
    print(f"First 500 chars:\n{raw[:500]}")
    
    # Try parse
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
    data = json.loads(cleaned.strip())
    material = [d for d in data if d.get("material")]
    print(f"\nParsed: {len(data)} items, {len(material)} material")
    for d in material:
        for imp in d.get("impacts", []):
            print(f"  -> {imp.get('ticker')} {imp.get('direction')} ({imp.get('confidence')}%) - {imp.get('reason','')[:60]}")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
