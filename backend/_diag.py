"""Test the AI screener ThreadPoolExecutor behavior"""
import os, sys, json
sys.path.insert(0, r"C:\Project rohan\Alpha_Lens\backend")
from dotenv import load_dotenv
load_dotenv(r"C:\Project rohan\Alpha_Lens\.env")

from google import genai
from google.genai import types
import concurrent.futures

keys = [v for k, v in sorted(os.environ.items()) if k.startswith("GEMINI_API_KEY") and v]
print(f"Loaded {len(keys)} keys.")

prompt = "Hello, respond with a short sentence."

for _key_idx, key in enumerate(keys):
    print(f"\nTesting key {_key_idx + 1}...")
    _ai_client = genai.Client(api_key=key)
    try:
        def _make_call(_c=_ai_client, _p=prompt):
            return _c.models.generate_content(
                model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=_p,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                ),
            )
        _tex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            _fut = _tex.submit(_make_call)
            resp = _fut.result(timeout=60)
            print(f"Key {_key_idx + 1} succeeded: {resp.text.strip()}")
        finally:
            _tex.shutdown(wait=False, cancel_futures=True)
    except Exception as e:
        print(f"Key {_key_idx + 1} failed: {e}")
