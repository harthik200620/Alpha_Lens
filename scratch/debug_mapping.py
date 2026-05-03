import json
import sqlite3
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))
from app import get_candidate_stocks, connect_news_db, MIN_CONFIDENCE, client, MODEL_NAME  # type: ignore
from prediction_models import EnsemblePredictor  # type: ignore
from technical_analysis import get_stock_technical_context, get_market_regime  # type: ignore

def debug_news_mapping():
    headlines = [
        "Market Trading Guide: Buy SBI and Bajaj Consumer on Monday for gains up to 6%",
        "Mcap of 8 of top-10 most valued firms jumps Rs 4.13 lakh cr; HDFC, ICICI Bank top gainers",
        "FIIs sell Indian equities worth Rs 1.6 lakh cr since outbreak of Iran-US war.",
        "Gold surges as US-Iran ceasefire weakens US dollar"
    ]
    
    ensemble = EnsemblePredictor()
    market_regime = get_market_regime()
    
    for h in headlines:
        print(f"\nHeadline: {h}")
        candidates = get_candidate_stocks(h, client, MODEL_NAME)
        print(f"Candidates found: {candidates}")
        
        if not candidates:
            print("  -> NO CANDIDATES FOUND")
            continue
            
        for ticker, direction in candidates:
            print(f"  Testing Ticker: {ticker} (Direction: {direction})")
            tech_data = get_stock_technical_context(ticker)
            print(f"    Tech Data fetched: {tech_data is not None}")
            
            result = ensemble.predict(
                headline=h,
                ticker=ticker,
                direction=direction,
                tech_data=tech_data,
                market_regime=market_regime,
                db_connect_fn=connect_news_db,
                api_client=client,
                model_name=MODEL_NAME,
                min_score=MIN_CONFIDENCE
            )
            
            print(f"    Ensemble Result: Approved={result['approved']}, Score={result['final_score']}, Agree={result['models_agreeing']}/5, Veto={result['has_veto']}")
            print(f"    Detail: {result['detail']}")

if __name__ == '__main__':
    debug_news_mapping()
