import os
import sys
import time
from dotenv import load_dotenv
from google import genai
import concurrent.futures

load_dotenv()

keys = [os.environ.get(f"GEMINI_API_KEY_{i}") for i in range(1, 10)]
model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

for idx, key in enumerate(keys):
    if not key:
        print(f"Key {idx+1}: Not set")
        continue
    print(f"Testing Key {idx+1} ({key[:6]}...{key[-4:]})")
    client = genai.Client(api_key=key)
    try:
        def make_call():
            return client.models.generate_content(
                model=model_name,
                contents="Reply with: OK"
            )
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(make_call)
            res = future.result(timeout=15)
            print(f"  -> SUCCESS! Response: {res.text.strip()}")
    except Exception as e:
        print(f"  -> FAILED: {type(e).__name__} - {e}")
    print("-" * 50)
