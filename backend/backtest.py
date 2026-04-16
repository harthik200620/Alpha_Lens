from google import genai
from google.genai import types
import json
import yfinance_twelvedata_shim as yf
from datetime import datetime, timedelta
import pytz
import time
import csv
import os
import logging

from technical_analysis import (
    get_stock_technical_context,
    format_technical_context_for_prompt,
    get_market_regime,
    compute_rsi,
    compute_sma
)

logger = logging.getLogger('yfinance')
logger.disabled = True
logger.propagate = False

# --- API KEY ROTATOR SETUP ---
API_KEYS = [
    "AIzaSyAX3Tj_yErU_aP19kXlmGDa-URAYGEYojc",
    "AIzaSyAL2fWNHmQTZvAQcG3DAWUkr_vecC5pCaM",
    "AIzaSyCUJbHzWvCYzokef_NyXKNWQ6ywniO-wb4",
    "AIzaSyA6En5i8Bpr6_lPKWSMecchwRfHruHw0tU"
]
current_key_idx = 0

client = genai.Client(api_key=API_KEYS[current_key_idx])
MODEL_NAME = 'gemini-2.5-flash'

# --- ASYMMETRIC RISK/REWARD — wide stop gives trades breathing room ---
TARGET_PCT = 1.5
STOP_PCT = -3.0

# Minimum confidence to accept a trade
MIN_CONFIDENCE = 65

def clean_json(raw_text):
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return json.loads(cleaned.strip())


def get_historical_technical_context(ticker, news_time_str):
    """
    Fetch technical context for a stock at a specific historical point in time.
    Returns technical indicators and the formatted prompt context.
    """
    try:
        if not ticker.endswith('.NS') and not ticker.endswith('.BO'):
            ticker += '.NS'

        ist = pytz.timezone('Asia/Kolkata')
        news_dt = ist.localize(datetime.strptime(news_time_str, "%Y-%m-%d %H:%M"))

        stock = yf.Ticker(ticker)
        # Fetch enough history to compute indicators BEFORE the news date
        start_date = (news_dt - timedelta(days=90)).strftime('%Y-%m-%d')
        end_date = (news_dt + timedelta(days=1)).strftime('%Y-%m-%d')

        hist = stock.history(start=start_date, end=end_date)

        if hist.empty or len(hist) < 20:
            return None, None

        hist.index = hist.index.tz_localize('Asia/Kolkata') if hist.index.tz is None else hist.index.tz_convert('Asia/Kolkata')

        # Only use data up to/on the news date
        hist_before = hist[hist.index.date <= news_dt.date()]
        if hist_before.empty or len(hist_before) < 15:
            return None, None

        closes = hist_before['Close'].tolist()
        volumes = hist_before['Volume'].tolist()
        current_price = round(closes[-1], 2)

        # Returns
        ret_1d = round(((closes[-1] - closes[-2]) / closes[-2]) * 100, 2) if len(closes) >= 2 else 0
        ret_5d = round(((closes[-1] - closes[-6]) / closes[-6]) * 100, 2) if len(closes) >= 6 else 0
        ret_20d = round(((closes[-1] - closes[-21]) / closes[-21]) * 100, 2) if len(closes) >= 21 else 0

        # RSI
        rsi = compute_rsi(closes, 14)

        # SMAs
        sma_20 = compute_sma(closes, 20)
        sma_50 = compute_sma(closes, min(50, len(closes)))

        # 52-week range (use available data)
        highs = hist_before['High'].tolist()
        lows = hist_before['Low'].tolist()
        high_period = round(max(highs), 2)
        low_period = round(min(lows), 2)
        range_width = high_period - low_period
        range_pos = round((current_price - low_period) / range_width, 2) if range_width > 0 else 0.5

        # Pct from highs and lows
        pct_from_high = round(((current_price - high_period) / high_period) * 100, 2) if high_period > 0 else 0
        pct_from_low = round(((current_price - low_period) / low_period) * 100, 2) if low_period > 0 else 0

        # Volume ratio
        avg_vol = round(sum(volumes[-20:]) / min(20, len(volumes[-20:]))) if volumes else 1
        vol_ratio = round(volumes[-1] / avg_vol, 2) if avg_vol > 0 else 1.0

        # Momentum signal
        if rsi is not None:
            if rsi > 75:
                momentum = "OVERBOUGHT"
            elif rsi > 60:
                momentum = "BULLISH_MOMENTUM"
            elif rsi < 25:
                momentum = "OVERSOLD"
            elif rsi < 40:
                momentum = "BEARISH_MOMENTUM"
            else:
                momentum = "NEUTRAL"
        else:
            momentum = "UNKNOWN"

        # Trend
        if sma_20 and sma_50:
            if sma_20 > sma_50 and current_price > sma_20:
                trend = "STRONG_UPTREND"
            elif sma_20 > sma_50:
                trend = "UPTREND"
            elif sma_20 < sma_50 and current_price < sma_20:
                trend = "STRONG_DOWNTREND"
            elif sma_20 < sma_50:
                trend = "DOWNTREND"
            else:
                trend = "SIDEWAYS"
        else:
            trend = "UNKNOWN"

        above_sma20 = current_price > sma_20 if sma_20 else None
        above_sma50 = current_price > sma_50 if sma_50 else None

        context_str = (
            f"Ticker: {ticker}\n"
            f"Price at News Time: ₹{current_price}\n"
            f"1-Day Return: {ret_1d}% | 5-Day Return: {ret_5d}% | 20-Day Return: {ret_20d}%\n"
            f"RSI(14): {rsi} ({momentum})\n"
            f"SMA20: ₹{sma_20} (Price {'above' if above_sma20 else 'below'}) | SMA50: ₹{sma_50}\n"
            f"Period Range: ₹{low_period} - ₹{high_period} | Position: {range_pos} (0=low, 1=high)\n"
            f"From Period High: {pct_from_high}% | From Period Low: {pct_from_low}%\n"
            f"Volume vs 20D Avg: {vol_ratio}x\n"
            f"Overall Trend: {trend}"
        )

        return {
            "price": current_price,
            "rsi": rsi,
            "momentum": momentum,
            "trend": trend,
            "ret_5d": ret_5d,
            "range_pos": range_pos,
            "vol_ratio": vol_ratio,
            "above_sma20": above_sma20
        }, context_str

    except Exception as e:
        return None, None


def get_historical_market_regime(news_time_str):
    """
    Fetch historical Nifty 50 regime exactly at the time of the news.
    """
    try:
        ist = pytz.timezone('Asia/Kolkata')
        news_dt = ist.localize(datetime.strptime(news_time_str, "%Y-%m-%d %H:%M"))
        
        nifty = yf.Ticker("^NSEI")
        start_date = (news_dt - timedelta(days=40)).strftime('%Y-%m-%d')
        end_date = (news_dt + timedelta(days=1)).strftime('%Y-%m-%d')
        
        hist = nifty.history(start=start_date, end=end_date)
        if hist.empty or len(hist) < 10:
            return "UNKNOWN"
            
        hist.index = hist.index.tz_localize('Asia/Kolkata') if hist.index.tz is None else hist.index.tz_convert('Asia/Kolkata')
        hist_before = hist[hist.index.date <= news_dt.date()]
        if hist_before.empty or len(hist_before) < 10:
            return "UNKNOWN"
            
        closes = hist_before['Close'].tolist()
        ret_5d = ((closes[-1] - closes[-6]) / closes[-6]) * 100 if len(closes) >= 6 else 0
        rsi = compute_rsi(closes, 14)

        if ret_5d > 2 and rsi and rsi > 55:
            return "RISK_ON"
        elif ret_5d < -2 and rsi and rsi < 45:
            return "RISK_OFF"
        else:
            return "NEUTRAL"
    except Exception as e:
        return "UNKNOWN"


def scan_candles_for_result(ticker, news_time_str, impact):
    """
    Scans every 15-min candle from the news time over 3 trading days.
    Uses High/Low prices to detect if target or stop was hit at ANY point,
    not just at a single snapshot. This is critical — the old approach missed
    intraday target hits where the stock moved up then pulled back.
    
    Returns: (result, pct_at_resolution, day_number)
    result: 'TARGET_HIT', 'STOP_HIT', 'EXPIRED', 'NO_DATA'
    """
    try:
        if not ticker.endswith('.NS') and not ticker.endswith('.BO'):
            ticker += '.NS'

        ist = pytz.timezone('Asia/Kolkata')
        base_time = ist.localize(datetime.strptime(news_time_str, "%Y-%m-%d %H:%M"))

        stock = yf.Ticker(ticker)
        hist = stock.history(period="60d", interval="15m")

        if hist.empty:
            return "NO_DATA", 0, 0

        hist.index = hist.index.tz_convert('Asia/Kolkata')

        # Get base price
        base_data = hist[hist.index >= base_time]
        if base_data.empty or len(base_data) < 2:
            return "NO_DATA", 0, 0
        base_price = base_data['Close'].iloc[0]
        actual_base_time = base_data.index[0]

        # Get all candles from base_time to base_time + ~3 trading days
        end_scan = actual_base_time + timedelta(days=5)  # 5 calendar days to cover 3 trading days
        scan_data = hist[(hist.index > actual_base_time) & (hist.index <= end_scan)]

        if scan_data.empty:
            return "NO_DATA", 0, 0

        is_bullish = 'bull' in impact.lower()

        # Calculate target and stop prices
        if is_bullish:
            target_price = base_price * (1 + TARGET_PCT / 100)
            stop_price = base_price * (1 + STOP_PCT / 100)  # STOP_PCT is negative
        else:  # bearish
            target_price = base_price * (1 - TARGET_PCT / 100)  # target is price going DOWN
            stop_price = base_price * (1 - STOP_PCT / 100)     # stop is price going UP (STOP_PCT negative, so this adds)

        # Scan every candle's High and Low
        for i, (idx, candle) in enumerate(scan_data.iterrows()):
            high = candle['High']
            low = candle['Low']

            # Determine which trading day this candle falls on
            hours_elapsed = (idx - actual_base_time).total_seconds() / 3600
            if hours_elapsed <= 24:
                day = 1
            elif hours_elapsed <= 48:
                day = 2
            else:
                day = 3

            if is_bullish:
                # For bullish: target hit if High >= target_price
                #              stop hit if Low <= stop_price
                target_hit = high >= target_price
                stop_hit = low <= stop_price
            else:
                # For bearish: target hit if Low <= target_price (price dropped enough)
                #              stop hit if High >= stop_price (price rose too much)
                target_hit = low <= target_price
                stop_hit = high >= stop_price

            # If both target and stop hit in same candle, check close to decide
            if target_hit and stop_hit:
                close_pct = ((candle['Close'] - base_price) / base_price) * 100
                if is_bullish:
                    if close_pct >= 0:
                        return "TARGET_HIT", round(close_pct, 2), day
                    else:
                        return "STOP_HIT", round(close_pct, 2), day
                else:
                    if close_pct <= 0:
                        return "TARGET_HIT", round(close_pct, 2), day
                    else:
                        return "STOP_HIT", round(close_pct, 2), day

            if target_hit:
                pct = round(((candle['Close'] - base_price) / base_price) * 100, 2)
                return "TARGET_HIT", pct, day

            if stop_hit:
                pct = round(((candle['Close'] - base_price) / base_price) * 100, 2)
                return "STOP_HIT", pct, day

        # If we scanned all candles and nothing hit — expired
        last_close = scan_data['Close'].iloc[-1]
        final_pct = round(((last_close - base_price) / base_price) * 100, 2)
        return "EXPIRED", final_pct, 3

    except Exception as e:
        return "NO_DATA", 0, 0


def should_take_trade(ai_data, tech_data, market_regime):
    """
    V2.2 Strict Quantitative Guardrails:
    Forbid counter-trend and counter-regime trades.
    """
    impact = ai_data.get('predicted_impact', '').lower()
    confidence = ai_data.get('confidence', 80)

    # Filter 1: Reject low confidence
    if confidence < MIN_CONFIDENCE:
        return False, f"Confidence {confidence} < {MIN_CONFIDENCE} threshold"

    if tech_data is None:
        return True, "Trade passes (No TA data)"

    rsi = tech_data.get('rsi')
    range_pos = tech_data.get('range_pos', 0.5)
    above_sma20 = tech_data.get('above_sma20')

    # --- THE TREND ALIGNMENT MANDATE ---
    if 'bull' in impact:
        # Prevent buying falling knives: stock MUST be above SMA20 to go long
        if above_sma20 is False:
            return False, "Trend Blocker: Stock is BELOW 20-Day SMA. Bullish call rejected."
            
        # Market Regime Override: Never buy when the broader market is crashing
        if market_regime == "RISK_OFF":
            return False, "Regime Blocker: Market is RISK_OFF. Bullish call rejected."
            
        # Volatility/Exhaustion Filter: Don't buy at the very top of the channel
        if range_pos > 0.85:
            return False, f"Exhaustion Blocker: Price at top {range_pos*100}% of range. Bullish call rejected."

    if 'bear' in impact:
        # Prevent shorting strong rallies: stock MUST be below SMA20 to go short
        if above_sma20 is True:
            return False, "Trend Blocker: Stock is ABOVE 20-Day SMA. Bearish call rejected."
            
        # Market Regime Override: Never short when the broader market is ripping
        if market_regime == "RISK_ON":
            return False, "Regime Blocker: Market is RISK_ON. Bearish call rejected."
            
        # Volatility/Exhaustion Filter: Don't short at the very bottom of the channel
        if range_pos < 0.15:
            return False, f"Exhaustion Blocker: Price at bottom {range_pos*100}% of range. Bearish call rejected."

    return True, "Trade passes all strict V2.2 filters (Trend & Regime Aligned)"


def run_bulk_backtest(csv_filename):
    global current_key_idx, client

    print("\n" + "=" * 60)
    print(" 🚀 ALPHA LENS v2.2 — ENHANCED QUANT BACKTESTER")
    print("    Strict Trend & Regime Alignment | Asymmetric Thresholds")
    print("=" * 60)

    if not os.path.exists(csv_filename):
        print(f"ERROR: Could not find '{csv_filename}'.")
        return

    stats = {
        "total_news_processed": 0,
        "total_predictions_made": 0,
        "predictions_filtered_out": 0,
        "target_hit": 0,
        "stop_hit": 0,
        "still_running": 0,
        "expired": 0,
        "api_errors": 0,
        "data_errors": 0
    }

    trade_log = []

    with open(csv_filename, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        rows = list(reader)

        print(f"Found {len(rows)} articles to process.")
        print(f"Settings: Target={TARGET_PCT}% | Stop={STOP_PCT}% | Min Confidence={MIN_CONFIDENCE}")
        print(f"Multi-Key Rotation Active | Technical Confirmation ON\n")

        for index, row in enumerate(rows):
            news_time = row['Datetime'].strip()
            news_text = row['Headline'].strip()

            stats["total_news_processed"] += 1
            print(f"\n[{index + 1}/{len(rows)}] 📰 {news_text[:60]}...")

            # --- PASS 1: Initial AI analysis to identify affected stocks ---
            prompt_pass1 = f"""
            You are an elite quantitative portfolio manager at a top-tier Indian hedge fund.
            Analyze this historical Indian market news from exactly {news_time}:
            '{news_text}'

            CRITICAL RULES FOR HIGH WIN RATE:
            1. If the news is ambiguous, already priced in (e.g., expected results, routine announcements), or has low direct impact, return empty array.
            2. Identify stocks when there is a highly probable directional edge from this news.
            3. Maximum 1-3 stocks — pick the best candidates.
            4. Think about 2nd/3rd order effects (e.g. Crude crash -> Buy Asian Paints).
            5. Consider if a stock might have already MOVED on the news.
            6. NEVER recommend a trade just because a company is mentioned — the news must create a TRADEABLE EDGE.
            7. 'confidence': integer 0-100. 85+ for crystal-clear setups. 65-84 for strong signals. Below 65 is filtered out.
            8. **ONLY IDENTIFY INDIAN STOCKS LISTED ON THE NSE.** You MUST append '.NS' to the ticker symbol. Do NOT recommend foreign stocks like US tech companies.

            Output STRICTLY as JSON:
            {{
              "affected_stocks": [
                {{
                    "ticker": "TICKER.NS",
                    "predicted_impact": "BULLISH",
                    "confidence": 85,
                    "reason": "Clear 1-sentence reason"
                }}
              ]
            }}
            """

            success = False
            retries = 0
            stocks = []

            while not success and retries < len(API_KEYS):
                try:
                    ai_resp = client.models.generate_content(
                        model=MODEL_NAME,
                        contents=prompt_pass1,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json"
                        )
                    )
                    analysis = clean_json(ai_resp.text)
                    stocks = analysis.get('affected_stocks', [])
                    success = True
                except Exception as e:
                    error_msg = str(e).lower()
                    if "429" in error_msg or "403" in error_msg or "quota" in error_msg or "key" in error_msg or "exhausted" in error_msg or "resource" in error_msg:
                        current_key_idx = (current_key_idx + 1) % len(API_KEYS)
                        print(f"    -> Key limit reached. Swapping to API Key {current_key_idx + 1}...")
                        client = genai.Client(api_key=API_KEYS[current_key_idx])
                        time.sleep(1)
                        retries += 1
                    else:
                        print(f"    -> AI Error: {str(e)[:80]}")
                        break

            if not success:
                stats["api_errors"] += 1
                continue

            if not stocks:
                print("    -> AI passed -- No high conviction setup found.")
                time.sleep(2)
                continue

            # --- PASS 2: Technical confirmation for each stock ---
            for stock in stocks:
                ticker = stock['ticker']
                impact = stock['predicted_impact']
                confidence = stock.get('confidence', 70)
                reason = stock.get('reason', '')

                # --- V2.2 PRE-TRADE TECHNICAL VALIDATION ---
                tech_data, context_str = get_historical_technical_context(ticker, news_time)
                market_regime = get_historical_market_regime(news_time)
                
                stock['confidence'] = confidence
                should_trade, filter_reason = should_take_trade(stock, tech_data, market_regime)

                if not should_trade:
                    stats["predictions_filtered_out"] += 1
                    print(f"    -> 🔴 {ticker} ({impact.upper()}, conf={confidence}): FILTERED — {filter_reason}")
                    continue

                # Relaxed technical validation — only filter extreme outliers
                if tech_data:
                    ret_5d = tech_data.get('ret_5d', 0)

                    # Only filter if stock MASSIVELY moved already (5%+)
                    if 'bull' in impact.lower() and ret_5d > 5.0:
                        stats["predictions_filtered_out"] += 1
                        print(f"    -> FILTERED {ticker}: Already up +{ret_5d}% in 5d, priced in")
                        continue
                    if 'bear' in impact.lower() and ret_5d < -5.0:
                        stats["predictions_filtered_out"] += 1
                        print(f"    -> FILTERED {ticker}: Already down {ret_5d}% in 5d, priced in")
                        continue

                stats["total_predictions_made"] += 1

                # --- EVALUATION: Scan ALL candles over 3 days using High/Low ---
                result, pct, day = scan_candles_for_result(ticker, news_time, impact)

                if result == "NO_DATA":
                    stats["data_errors"] += 1
                    print(f"    -> {ticker}: Market Data Missing")
                    continue

                if result == "TARGET_HIT":
                    stats["target_hit"] += 1
                    print(f"    -> WIN {ticker} ({impact.upper()}, conf={confidence}): TARGET_HIT Day {day} ({pct:+.2f}%)")
                elif result == "STOP_HIT":
                    stats["stop_hit"] += 1
                    print(f"    -> LOSS {ticker} ({impact.upper()}, conf={confidence}): STOP_HIT Day {day} ({pct:+.2f}%)")
                elif result == "EXPIRED":
                    stats["expired"] += 1
                    print(f"    -> EXPIRED {ticker} ({impact.upper()}, conf={confidence}): No resolution 3 days ({pct:+.2f}%)")

                trade_log.append({"ticker": ticker, "impact": impact, "conf": confidence, "result": result, "pct": pct, "day": day})

            # Throttle between articles
            time.sleep(2)

    # --- FINAL REPORT ---
    print("\n" + "=" * 60)
    print(" 📊 ALPHA LENS v2.0 — FINAL PERFORMANCE REPORT")
    print("=" * 60)
    print(f"Total News Articles Processed:     {stats['total_news_processed']}")
    print(f"Total Predictions Triggered:       {stats['total_predictions_made']}")
    print(f"Predictions Filtered by TA/Conf:   {stats['predictions_filtered_out']}")
    print("-" * 60)

    completed_trades = stats['target_hit'] + stats['stop_hit']
    win_rate = 0
    if completed_trades > 0:
        win_rate = round((stats['target_hit'] / completed_trades) * 100, 2)

    GREEN = "\033[92m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    print(f"{GREEN}✅ TARGET HIT (Wins):              {stats['target_hit']}{RESET}")
    print(f"{RED}❌ STOP HIT (Losses):              {stats['stop_hit']}{RESET}")
    print(f"{YELLOW}⏰ EXPIRED (No resolution 3 days): {stats['expired']}{RESET}")
    print(f"{CYAN}⏳ STILL RUNNING:                  {stats['still_running']}{RESET}")
    print("-" * 60)

    if win_rate >= 70:
        rating = f"{GREEN}🔥 ELITE{RESET}"
    elif win_rate >= 55:
        rating = f"{CYAN}📈 SOLID{RESET}"
    elif win_rate >= 45:
        rating = f"{YELLOW}📊 MODERATE{RESET}"
    else:
        rating = f"{RED}⚠️ NEEDS IMPROVEMENT{RESET}"

    print(f"{BOLD}🏆 WIN RATE (Resolved Trades):     {win_rate}%  ({rating}){RESET}")
    print(f"   (Calculated on {completed_trades} resolved trades, excluding {stats['expired']} expired and {stats['still_running']} running)")
    print("-" * 60)
    print(f"Data Fetch Errors:                 {stats['data_errors']}")
    print(f"API Limit Blocks Dodged:           {stats['api_errors']}")
    print("=" * 60)

    # Save detailed log
    log_file = "backtest_results.json"
    with open(log_file, 'w') as f:
        json.dump({"stats": stats, "trades": trade_log}, f, indent=2)
    print(f"\n📁 Detailed trade log saved to: {log_file}")


if __name__ == "__main__":
    run_bulk_backtest("news_dataset_mini.csv")