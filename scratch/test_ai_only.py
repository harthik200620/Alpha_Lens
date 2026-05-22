import os
import sys
from dotenv import load_dotenv

# Add backend directory to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))
from prediction_models import EnsemblePredictor

load_dotenv()

def mock_db_connect():
    class DummyCursor:
        def execute(self, *args, **kwargs):
            pass
        def fetchall(self):
            return []
    class DummyConn:
        def cursor(self):
            return DummyCursor()
        def close(self):
            pass
    return DummyConn()

def test_ensemble():
    predictor = EnsemblePredictor()
    
    # 1. Mock AILogicModel.score to return 85 (AI model is working)
    predictor.m7.score = lambda *args, **kwargs: 85
    
    # Run prediction
    result_ok = predictor.predict(
        headline="TCS wins huge 2B USD contract",
        ticker="TCS.NS",
        direction="BULLISH",
        tech_data={"rsi_14": 55, "ema_alignment": "BULLISH"},
        market_regime="NEUTRAL",
        db_connect_fn=mock_db_connect
    )
    print("Test 1 (AI Logic Active):")
    print(f"  Approved: {result_ok['approved']}")
    print(f"  Score: {result_ok['final_score']}")
    print(f"  Detail: {result_ok['detail']}")
    
    # 2. Mock AILogicModel.score to return None (AI model fails/unavailable)
    predictor.m7.score = lambda *args, **kwargs: None
    
    # Run prediction
    result_fail = predictor.predict(
        headline="TCS wins huge 2B USD contract",
        ticker="TCS.NS",
        direction="BULLISH",
        tech_data={"rsi_14": 55, "ema_alignment": "BULLISH"},
        market_regime="NEUTRAL",
        db_connect_fn=mock_db_connect
    )
    print("Test 2 (AI Logic Unavailable):")
    print(f"  Approved: {result_fail['approved']}")
    print(f"  Score: {result_fail['final_score']}")
    print(f"  Detail: {result_fail['detail']}")
    
    # Asserts
    assert result_fail['approved'] is False, "Error: Ensemble approved prediction without AI model!"
    print("\nSUCCESS: AI-only verification checks passed!")

if __name__ == "__main__":
    test_ensemble()
