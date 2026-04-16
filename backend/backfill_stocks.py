"""
Backfill Script — Alpha Lens
Processes ALL existing news headlines in the DB and generates stock_impact
entries using the updated (relaxed) ensemble engine.

Run once with:  .\venv\Scripts\python.exe backfill_stocks.py
"""

import sqlite3
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import yfinance_twelvedata_shim as yf
yf.set_tz_cache_location("venv/yf_cache")

from app import (
    get_candidate_stocks, connect_news_db, MIN_CONFIDENCE,
    BULLISH_KEYWORDS, BEARISH_KEYWORDS
)
from prediction_models import EnsemblePredictor
from technical_analysis import get_stock_technical_context, get_market_regime

def backfill():
    print("=" * 60)
    print(f" ALPHA LENS STOCK BACKFILL  (MIN_CONFIDENCE={MIN_CONFIDENCE})")
    print("=" * 60)

    conn = connect_news_db()
    c = conn.cursor()

    # Fetch all news articles
    c.execute("SELECT id, headline FROM news ORDER BY created_at DESC")
    all_news = c.fetchall()
    conn.close()

    print(f"Found {len(all_news)} news articles in DB. Processing...\n")

    market_regime = get_market_regime()
    ensemble = EnsemblePredictor()

    total_new_signals = 0
    total_processed = 0

    for news_id, headline in all_news:
        candidates = get_candidate_stocks(headline)
        if not candidates:
            continue

        total_processed += 1
        saved_for_this = 0

        conn = connect_news_db()
        c = conn.cursor()

        for ticker, base_direction in candidates:
            # Skip if this news_id + ticker combination already exists
            c.execute(
                "SELECT id FROM stock_impact WHERE news_id = ? AND ticker = ?",
                (news_id, ticker)
            )
            if c.fetchone():
                continue  # Already has an entry, skip

            # Fetch current price
            base_price = 0.0
            try:
                tick_data = yf.Ticker(ticker)
                hist = tick_data.history(period='1d', interval='1m')
                if not hist.empty:
                    base_price = round(float(hist['Close'].iloc[-1]), 2)
                else:
                    hist5 = tick_data.history(period='5d')
                    if not hist5.empty:
                        base_price = round(float(hist5['Close'].iloc[-1]), 2)
            except Exception:
                base_price = 0.0

            if base_price <= 0:
                print(f"   [skip] {ticker} — could not fetch price")
                continue

            # Technical context
            tech_data = get_stock_technical_context(ticker)
            tech_context_str = json.dumps(tech_data) if tech_data else ""

            # Ensemble predict
            result = ensemble.predict(
                headline=headline,
                ticker=ticker,
                direction=base_direction,
                tech_data=tech_data,
                market_regime=market_regime,
                db_connect_fn=connect_news_db,
                min_score=MIN_CONFIDENCE
            )

            if result['approved']:
                view = 'High Conviction' if result['final_score'] >= 85 else 'Moderate Conviction'
                reason = (
                    f"Ensemble Score: {result['final_score']} "
                    f"({result['models_agreeing']}/5 models approve). "
                    f"Expected directional breakout."
                )
                c.execute(
                    '''INSERT INTO stock_impact
                       (news_id, ticker, impact, estimated_change_percent, view, reason,
                        base_price, current_price, confidence_score, technical_context, ensemble_detail)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (
                        news_id, ticker, result['direction'], 2.5,
                        view, reason,
                        base_price, base_price,
                        result['final_score'],
                        tech_context_str,
                        result['detail']
                    )
                )
                saved_for_this += 1
                total_new_signals += 1
                print(f"   [+] {ticker} ({result['direction']}) score={result['final_score']} | {headline[:55]}...")

        conn.commit()
        conn.close()

    print(f"\n{'='*60}")
    print(f" DONE! Processed {total_processed} headlines with candidates.")
    print(f" New stock impact entries created: {total_new_signals}")
    print(f"{'='*60}")

    # Final count
    conn = connect_news_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM stock_impact")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT news_id) FROM stock_impact")
    unique_news = c.fetchone()[0]
    conn.close()
    print(f" Total stock_impact rows now: {total}")
    print(f" News articles with ≥1 stock signal: {unique_news}")


if __name__ == '__main__':
    backfill()
