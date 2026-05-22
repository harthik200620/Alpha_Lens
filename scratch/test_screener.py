import os
import sys
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

key = os.environ.get("GEMINI_API_KEY_1")
model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

print(f"Testing key 1: {key[:10]}... with model {model}")

articles_batch = [
    {"headline": "TCS wins $2B deal", "summary": "Tata Consultancy Services has bagged a mega $2 billion IT contract from a US retail giant.", "time": "2026-05-22 10:00:00"},
    {"headline": "Reliance Q4 PAT rises 10%", "summary": "Reliance Industries reports 10% increase in net profit for the fourth quarter.", "time": "2026-05-22 10:15:00"},
]

numbered = "\n".join(
    [
        f"{i+1}. Headline: {a['headline']}\n"
        f"   Context: {a['summary']}"
        for i, a in enumerate(articles_batch)
    ]
)

schema_example = json.dumps([
    {
        "index": 1,
        "material": True,
        "catalyst_type": "EARNINGS_BEAT",
        "materiality_score": 87,
        "impacts": [
            {
                "ticker": "TCS.NS",
                "direction": "BULLISH",
                "confidence": 88,
                "impact_type": "DIRECT",
                "reason": "Q4 PAT beat consensus and deal pipeline improved."
            }
        ]
    }
], indent=2)

prompt = f"""You are the Chief Investment Strategist at India's top macro hedge fund.
Analyze exactly {len(articles_batch)} news items. For EVERY article, find stock impact.
Return ONLY valid JSON matching this shape:
{schema_example}

News items to analyze:
{numbered}"""

try:
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        ),
    )
    print("SUCCESS!")
    print(resp.text)
except Exception as e:
    print(f"FAILED - Type: {type(e).__name__} - Message: {e}")
