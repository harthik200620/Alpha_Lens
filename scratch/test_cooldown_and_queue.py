import os
import sys
import time
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath('backend'))
import app

# Set dummy/mock API keys to control behavior
app.API_KEYS = ["MOCK_KEY_1", "MOCK_KEY_2"]
app._KEY_QUOTA_COOLDOWN_UNTIL = {}

def test_transient_error_cooldown():
    print("--- Test 1: Transient Error Cooldown ---")
    exc_transient = Exception("503 Service Unavailable / Model overloaded")
    is_trans = app._is_gemini_transient_error(exc_transient)
    is_q = app._is_gemini_quota_error(exc_transient)
    print(f"Is transient: {is_trans} (Expected: True)")
    print(f"Is quota: {is_q} (Expected: False)")

    # Simulate get_and_rotate_client with transient error
    app.current_key_idx = 0
    # Clear client so it bootstrap/rotates
    app.client = None
    
    # Run rotation with transient error
    _, idx = app.get_and_rotate_client(last_failed_idx=0, is_timeout=False, is_quota=False, is_transient=True)
    
    # Check cooldown duration
    cooldown_until = app._KEY_QUOTA_COOLDOWN_UNTIL.get(0, 0)
    now = time.time()
    diff = cooldown_until - now
    print(f"Key 1 cooldown: {diff:.2f} seconds remaining (Expected: ~15 seconds)")
    print(f"Rotated to key: {idx + 1} (Expected: 2)")

def test_quota_error_cooldown():
    print("\n--- Test 2: Quota Error Cooldown ---")
    exc_quota = Exception("429 RESOURCE_EXHAUSTED: Quota exceeded")
    is_trans = app._is_gemini_transient_error(exc_quota)
    is_q = app._is_gemini_quota_error(exc_quota)
    print(f"Is transient: {is_trans} (Expected: False)")
    print(f"Is quota: {is_q} (Expected: True)")

    # Run rotation with quota error on Key 2
    _, idx = app.get_and_rotate_client(last_failed_idx=1, is_timeout=False, is_quota=True, is_transient=False)
    
    cooldown_until = app._KEY_QUOTA_COOLDOWN_UNTIL.get(1, 0)
    now = time.time()
    diff = cooldown_until - now
    print(f"Key 2 cooldown: {diff:.2f} seconds remaining (Expected: ~300 seconds)")

def test_worker_suspension():
    print("\n--- Test 3: Worker Batch Suspension ---")
    
    # Create articles batch
    test_articles = [
        {"headline": "Article 1", "summary": "Text 1", "time": "Just Now"},
        {"headline": "Article 2", "summary": "Text 2", "time": "Just Now"},
    ]
    
    # Local mock of quant_ai_screener return value
    def mock_screener(batch):
        return [
            {"headline": a["headline"], "ticker": None, "direction": None, "reason": "AI_COOLDOWN"}
            for a in batch
        ]
    
    # Simulate news duplicate filter + loop logic from main worker
    new_articles = test_articles.copy()
    app.SEEN_HEADLINES.clear()
    
    BATCH_SIZE = 10
    screened_signals = []
    
    # Run worker batch loop logic
    for i in range(0, len(new_articles), BATCH_SIZE):
        batch = new_articles[i:i + BATCH_SIZE]
        batch_results = mock_screener(batch)
        if batch_results and all(r.get("reason") in ("AI_COOLDOWN", "AI_QUOTA_EXHAUSTED", "AI_UNAVAILABLE") for r in batch_results):
            print("Worker detected AI failure! Breaking loop to suspend screening.")
            break
        screened_signals.extend(batch_results)
        
    print(f"Signals collected: {len(screened_signals)} (Expected: 0)")
    print(f"Seen Headlines list size: {len(app.SEEN_HEADLINES)} (Expected: 0)")

if __name__ == "__main__":
    test_transient_error_cooldown()
    test_quota_error_cooldown()
    test_worker_suspension()
