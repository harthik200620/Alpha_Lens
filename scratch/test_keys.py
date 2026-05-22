import os
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

keys = [os.environ.get(f"GEMINI_API_KEY_{i}") for i in range(1, 10)]
print(f"Loaded {len([k for k in keys if k])} keys from env.")

for idx, key in enumerate(keys):
    if not key:
        print(f"Key {idx+1}: Empty")
        continue
    try:
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="say hello"
        )
        print(f"Key {idx+1}: SUCCESS - Response: {resp.text.strip()}")
    except Exception as e:
        print(f"Key {idx+1}: FAILED - Type: {type(e).__name__} - Message: {e}")
