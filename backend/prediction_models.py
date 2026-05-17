"""
Alpha Lens v5.0 — Multi-Model Ensemble Prediction Engine
6 independent models analyze news → stock impact.
Signal emitted ONLY when ensemble score >= 70 AND 4+ models agree.
"""
import re
import math
import os

# ==========================================
# MODEL 2: HISTORICAL SIMILARITY
# ==========================================
class HistoricalSimilarityModel:
    """Finds similar past headlines and checks if those signals hit target or failed."""

    @staticmethod
    def _tokenize(text):
        return set(re.findall(r'[a-z]{3,}', text.lower()))

    @staticmethod
    def _similarity(s1, s2):
        if not s1 or not s2:
            return 0.0
        return len(s1 & s2) / len(s1 | s2)

    def score(self, headline, ticker, direction, db_connect_fn):
        """Returns 0-100 based on how similar past headlines performed."""
        try:
            conn = db_connect_fn()
            c = conn.cursor()
            c.execute("SELECT headline, direction, outcome FROM historical_patterns WHERE ticker = ?", (ticker,))
            patterns = c.fetchall()
            conn.close()

            if len(patterns) < 3:
                return 68  # Not enough data — neutral-high default

            tokens = self._tokenize(headline)
            matches = []
            for past_h, past_dir, outcome in patterns:
                sim = self._similarity(tokens, self._tokenize(past_h))
                if sim > 0.15:
                    matches.append({'sim': sim, 'same_dir': past_dir == direction, 'hit': outcome == 'HIT'})

            if not matches:
                return 68

            same_dir = [m for m in matches if m['same_dir']]
            if not same_dir:
                return 50

            weighted_hits = sum(m['sim'] for m in same_dir if m['hit'])
            weighted_total = sum(m['sim'] for m in same_dir)
            win_rate = weighted_hits / weighted_total if weighted_total > 0 else 0.5
            return max(30, min(95, int(win_rate * 100)))
        except Exception:
            return 65


# ==========================================
# MODEL 3: ADVANCED TECHNICAL ALIGNMENT
# ==========================================
class TechnicalAlignmentModel:
    """
    Uses advanced quant indicators: EMA alignment, MACD crossover, ADX trend strength,
    Stochastic RSI, Bollinger squeeze, OBV divergence, VWAP position, Fibonacci levels.
    """

    def score(self, tech_data, direction):
        """Returns 0-100 based on technical alignment with advanced indicators."""
        if tech_data is None:
            return 60  # Neutral-positive: don't sink ensemble when tech data unavailable
        s = 50
        bull = (direction == 'BULLISH')

        # ── 1. EMA Alignment (strongest trend signal) ──
        ema_align = tech_data.get('ema_alignment', 'UNKNOWN')
        if bull:
            if ema_align == 'PERFECT_BULLISH': s += 12
            elif ema_align == 'BULLISH': s += 8
            elif ema_align == 'PERFECT_BEARISH': s -= 12
            elif ema_align == 'BEARISH': s -= 8
        else:
            if ema_align == 'PERFECT_BEARISH': s += 12
            elif ema_align == 'BEARISH': s += 8
            elif ema_align == 'PERFECT_BULLISH': s -= 12
            elif ema_align == 'BULLISH': s -= 8

        # ── 2. MACD Crossover (trend confirmation) ──
        macd_cross = tech_data.get('macd_crossover', 'NONE')
        if macd_cross == 'BULLISH_CROSSOVER':
            s += 8 if bull else -6
        elif macd_cross == 'BEARISH_CROSSOVER':
            s += 8 if not bull else -6

        # ── 3. MACD Histogram momentum ──
        macd_hist = tech_data.get('macd_histogram')
        if macd_hist is not None:
            if macd_hist > 0 and bull: s += 3
            elif macd_hist < 0 and not bull: s += 3
            elif macd_hist > 0 and not bull: s -= 3
            elif macd_hist < 0 and bull: s -= 3

        # ── 4. ADX Trend Strength (only trade strong trends) ──
        adx = tech_data.get('adx')
        trend_str = tech_data.get('trend_strength', 'UNKNOWN')
        if trend_str == 'VERY_STRONG_TREND':
            s += 6  # Strong trend = higher confidence in directional prediction
        elif trend_str == 'STRONG_TREND':
            s += 3
        elif trend_str == 'WEAK_NO_TREND':
            s -= 8  # Penalize signals in ranging markets

        # ── 5. RSI + Stochastic RSI (momentum extremes) ──
        rsi = tech_data.get('rsi_14')
        stoch_k = tech_data.get('stoch_rsi_k')
        if rsi is not None:
            if bull:
                if rsi < 30:
                    s += 10  # Oversold = bullish opportunity
                elif rsi < 45:
                    s += 5
                elif rsi > 80:
                    s -= 10  # Extreme overbought = risky for bulls
                elif rsi > 70:
                    s -= 5
            else:
                if rsi > 70:
                    s += 10  # Overbought = bearish opportunity
                elif rsi > 55:
                    s += 5
                elif rsi < 20:
                    s -= 10  # Extreme oversold = risky for bears
                elif rsi < 30:
                    s -= 5

        # Stochastic RSI confirmation
        if stoch_k is not None:
            if bull and stoch_k < 20: s += 5  # Oversold StochRSI
            elif bull and stoch_k > 90: s -= 5
            elif not bull and stoch_k > 80: s += 5  # Overbought StochRSI
            elif not bull and stoch_k < 10: s -= 5

        # ── 6. Bollinger Band Squeeze (breakout setup) ──
        bb_squeeze = tech_data.get('bb_squeeze', False)
        bb_pct_b = tech_data.get('bb_percent_b')
        if bb_squeeze:
            s += 5  # Volatility squeeze = imminent breakout

        if bb_pct_b is not None:
            if bull and bb_pct_b < 0.1: s += 5  # Near lower band = bullish
            elif bull and bb_pct_b > 0.95: s -= 5  # At upper band
            elif not bull and bb_pct_b > 0.9: s += 5  # Near upper band = bearish
            elif not bull and bb_pct_b < 0.05: s -= 5  # At lower band

        # ── 7. OBV Trend (smart money / volume-price divergence) ──
        obv = tech_data.get('obv_trend', 'UNKNOWN')
        if obv == 'ACCUMULATING':
            s += 5 if bull else -3
        elif obv == 'DISTRIBUTING':
            s += 5 if not bull else -3

        # ── 8. VWAP Position (institutional reference) ──
        above_vwap = tech_data.get('above_vwap')
        if above_vwap is not None:
            if bull and above_vwap: s += 4
            elif bull and not above_vwap: s -= 3
            elif not bull and not above_vwap: s += 4
            elif not bull and above_vwap: s -= 3

        # ── 9. Fibonacci Position ──
        fib_pos = tech_data.get('fib_position', 'UNKNOWN')
        if bull:
            if fib_pos in ('BETWEEN_618_786', 'BELOW_FIB_786'): s += 5  # Golden zone bounce
            elif fib_pos == 'BETWEEN_500_618': s += 3
            elif fib_pos == 'ABOVE_SWING_HIGH': s -= 3  # Already extended
        else:
            if fib_pos in ('ABOVE_SWING_HIGH', 'ABOVE_FIB_236'): s += 5  # Near resistance
            elif fib_pos == 'BETWEEN_236_382': s += 3
            elif fib_pos == 'BELOW_FIB_786': s -= 3  # Already oversold

        # ── 10. Volume Confirmation ──
        vol = tech_data.get('volume_ratio_vs_20d_avg', 1.0)
        if vol > 2.0: s += 5   # Heavy volume confirms move
        elif vol > 1.5: s += 3
        elif vol < 0.5: s -= 4  # Low volume = weak conviction

        # ── 11. Range Position ──
        rp = tech_data.get('range_position_52w', 0.5)
        if bull and rp > 0.9: s -= 4  # Near 52w high, limited upside
        elif bull and rp < 0.25: s += 4  # Near 52w low, value buy
        elif not bull and rp < 0.1: s -= 4  # Near 52w low, limited downside
        elif not bull and rp > 0.75: s += 4  # Near 52w high, room to fall

        return max(10, min(95, s))

    def has_veto(self, tech_data, direction):
        """
        True if technicals STRONGLY contradict the direction.
        Uses multiple confirmation: EMA + MACD + RSI + ADX must all oppose.
        """
        if tech_data is None:
            return False
        bull = (direction == 'BULLISH')
        rsi = tech_data.get('rsi_14')
        ema_align = tech_data.get('ema_alignment', 'UNKNOWN')
        macd_cross = tech_data.get('macd_crossover', 'NONE')
        adx = tech_data.get('adx')
        rp = tech_data.get('range_position_52w', 0.5)

        if bull:
            # Veto bullish if: bearish EMA + bearish MACD + RSI extreme + strong trend
            contra_count = 0
            if ema_align in ('PERFECT_BEARISH', 'BEARISH'): contra_count += 1
            if macd_cross == 'BEARISH_CROSSOVER': contra_count += 1
            if rsi and rsi > 85: contra_count += 1
            if rp > 0.95: contra_count += 1
            if adx and adx > 30 and ema_align in ('PERFECT_BEARISH', 'BEARISH'): contra_count += 1
            return contra_count >= 3
        else:
            contra_count = 0
            if ema_align in ('PERFECT_BULLISH', 'BULLISH'): contra_count += 1
            if macd_cross == 'BULLISH_CROSSOVER': contra_count += 1
            if rsi and rsi < 15: contra_count += 1
            if rp < 0.05: contra_count += 1
            if adx and adx > 30 and ema_align in ('PERFECT_BULLISH', 'BULLISH'): contra_count += 1
            return contra_count >= 3


# ==========================================
# MODEL 4: SECTOR MOMENTUM
# ==========================================
class SectorMomentumModel:
    """Checks if stock's sector is trending in a direction that supports the prediction."""

    SECTOR_MAP = {
        'HDFCBANK.NS': '^NSEBANK', 'ICICIBANK.NS': '^NSEBANK', 'SBIN.NS': '^NSEBANK',
        'AXISBANK.NS': '^NSEBANK', 'KOTAKBANK.NS': '^NSEBANK', 'INDUSINDBK.NS': '^NSEBANK',
        'PNB.NS': '^NSEBANK', 'BANKBARODA.NS': '^NSEBANK', 'CANBK.NS': '^NSEBANK',
        'BAJFINANCE.NS': '^NSEBANK', 'BAJAJFINSV.NS': '^NSEBANK', 'CHOLAFIN.NS': '^NSEBANK',
        'SHRIRAMFIN.NS': '^NSEBANK', 'MUTHOOTFIN.NS': '^NSEBANK', 'MANAPPURAM.NS': '^NSEBANK',
        'BANDHANBNK.NS': '^NSEBANK', 'FEDERALBNK.NS': '^NSEBANK', 'YESBANK.NS': '^NSEBANK',
        'IDBI.NS': '^NSEBANK',

        'INFY.NS': '^CNXIT', 'TCS.NS': '^CNXIT', 'WIPRO.NS': '^CNXIT',
        'HCLTECH.NS': '^CNXIT', 'TECHM.NS': '^CNXIT', 'LTIM.NS': '^CNXIT',
        'PERSISTENT.NS': '^CNXIT', 'COFORGE.NS': '^CNXIT', 'MPHASIS.NS': '^CNXIT',

        'SUNPHARMA.NS': '^CNXPHARMA', 'CIPLA.NS': '^CNXPHARMA', 'DRREDDY.NS': '^CNXPHARMA',
        'DIVISLAB.NS': '^CNXPHARMA', 'AUROPHARMA.NS': '^CNXPHARMA', 'LUPIN.NS': '^CNXPHARMA',

        'TATAMOTORS.NS': '^CNXAUTO', 'MARUTI.NS': '^CNXAUTO', 'M&M.NS': '^CNXAUTO',
        'BAJAJ-AUTO.NS': '^CNXAUTO', 'HEROMOTOCO.NS': '^CNXAUTO', 'EICHERMOT.NS': '^CNXAUTO',

        'TATASTEEL.NS': '^CNXMETAL', 'JSWSTEEL.NS': '^CNXMETAL', 'HINDALCO.NS': '^CNXMETAL',
        'VEDL.NS': '^CNXMETAL', 'JINDALSTEL.NS': '^CNXMETAL', 'COALINDIA.NS': '^CNXMETAL',

        'RELIANCE.NS': '^CNXENERGY', 'ONGC.NS': '^CNXENERGY', 'BPCL.NS': '^CNXENERGY',
        'HINDPETRO.NS': '^CNXENERGY', 'IOC.NS': '^CNXENERGY',
        'NTPC.NS': '^CNXENERGY', 'POWERGRID.NS': '^CNXENERGY', 'TATAPOWER.NS': '^CNXENERGY',

        'HAL.NS': '^NSEI', 'BEL.NS': '^NSEI', 'BHARATFORG.NS': '^NSEI',
    }

    _cache = {}

    def _get_sector_ret(self, idx):
        import yfinance_twelvedata_shim as yf
        if idx in self._cache:
            return self._cache[idx]
        try:
            hist = yf.Ticker(idx).history(period='10d')
            if hist.empty or len(hist) < 2:
                self._cache[idx] = 0
                return 0
            c = hist['Close'].tolist()
            r = ((c[-1] - c[0]) / c[0]) * 100
            self._cache[idx] = r
            return r
        except:
            self._cache[idx] = 0
            return 0

    def score(self, ticker, direction, market_regime):
        """Returns 0-100 based on sector alignment."""
        s = 50
        bull = (direction == 'BULLISH')
        idx = self.SECTOR_MAP.get(ticker)
        if idx:
            m = self._get_sector_ret(idx)
            if bull:
                if m > 2: s += 15
                elif m > 0.5: s += 8
                elif m < -2: s -= 12
                elif m < -0.5: s -= 5
            else:
                if m < -2: s += 15
                elif m < -0.5: s += 8
                elif m > 2: s -= 12
                elif m > 0.5: s -= 5

        if market_regime == "RISK_ON":
            s += 10 if bull else -10
        elif market_regime == "RISK_OFF":
            s += -10 if bull else 10

        return max(20, min(90, s))

    def clear_cache(self):
        self._cache = {}


# ==========================================
# MODEL 6: INDIAN MARKET SENTIMENT
# ==========================================
class IndianSentimentModel:
    """
    Analyzes Indian market conditions (Nifty 50, Bank Nifty, India VIX)
    to determine whether macro sentiment supports the predicted direction.
    """

    _cache = {}
    _cache_time = 0

    def _fetch_indian_data(self):
        """Fetch and cache Indian market data (5-min cache)."""
        import time
        import yfinance_twelvedata_shim as yf

        now = time.time()
        if self._cache and (now - self._cache_time) < 300:
            return self._cache

        data = {}

        # Nifty 50 — Indian broader market strength
        try:
            nifty = yf.Ticker("^NSEI")
            hist = nifty.history(period='10d')
            if not hist.empty and len(hist) >= 2:
                c = hist['Close'].tolist()
                data['nifty_ret_5d'] = ((c[-1] - c[0]) / c[0]) * 100
                data['nifty_ret_1d'] = ((c[-1] - c[-2]) / c[-2]) * 100
            else:
                data['nifty_ret_5d'] = 0
                data['nifty_ret_1d'] = 0
        except:
            data['nifty_ret_5d'] = 0
            data['nifty_ret_1d'] = 0

        # Bank Nifty — Backbone of Indian Market
        try:
            bank = yf.Ticker("^NSEBANK")
            hist = bank.history(period='10d')
            if not hist.empty and len(hist) >= 2:
                c = hist['Close'].tolist()
                data['bank_ret_5d'] = ((c[-1] - c[0]) / c[0]) * 100
                data['bank_ret_1d'] = ((c[-1] - c[-2]) / c[-2]) * 100
            else:
                data['bank_ret_5d'] = 0
                data['bank_ret_1d'] = 0
        except:
            data['bank_ret_5d'] = 0
            data['bank_ret_1d'] = 0

        # India VIX — Indian fear gauge
        try:
            ivix = yf.Ticker("^INDIAVIX")
            hist = ivix.history(period='5d')
            if not hist.empty:
                data['india_vix'] = hist['Close'].tolist()[-1]
            else:
                data['india_vix'] = 15  # neutral default
        except:
            data['india_vix'] = 15

        self._cache = data
        self._cache_time = now
        return data

    def score(self, direction):
        """Returns 0-100 based on Indian market sentiment alignment."""
        data = self._fetch_indian_data()
        s = 50
        bull = (direction == 'BULLISH')

        # ── 1. Nifty 50 momentum ──
        nifty_5d = data.get('nifty_ret_5d', 0)
        if nifty_5d > 2:
            s += 8 if bull else -6
        elif nifty_5d > 0.5:
            s += 4 if bull else -3
        elif nifty_5d < -2:
            s += -8 if bull else 8
        elif nifty_5d < -0.5:
            s += -4 if bull else 4

        # ── 2. Bank Nifty momentum ──
        bank_5d = data.get('bank_ret_5d', 0)
        if bank_5d > 2:
            s += 6 if bull else -4
        elif bank_5d > 0.5:
            s += 3 if bull else -2
        elif bank_5d < -2:
            s += -6 if bull else 6
        elif bank_5d < -0.5:
            s += -3 if bull else 3

        # ── 3. India VIX ──
        ivix = data.get('india_vix', 15)
        if ivix > 22:
            # High India VIX — uncertainty/fear
            s += -8 if bull else 8
        elif ivix > 18:
            s += -4 if bull else 4
        elif ivix < 12:
            s += 5 if bull else -3

        # ── 4. Internal Divergence (Bank vs Nifty) ──
        # If Bank Nifty strongly outperforms Nifty 50, it's very bullish
        nifty_1d = data.get('nifty_ret_1d', 0)
        bank_1d = data.get('bank_ret_1d', 0)
        divergence = bank_1d - nifty_1d
        if divergence > 0.5:
            s += 5 if bull else -3
        elif divergence < -0.5:
            s += -5 if bull else 3

        return max(15, min(90, s))

    def clear_cache(self):
        self._cache = {}
        self._cache_time = 0


# ==========================================
# MODEL 7: AI LOGIC MODEL
# ==========================================
class AILogicModel:
    """
    Asks the AI to score a potential trade setup 0-100.
    Carries 50% ensemble weight. Uses Gemini (primary) or SM-Gemini (fallback).
    """

    # SM-Gemini fallback client (hardcoded key for when .env Gemini keys are missing)
    _sm_client = None
    _SM_KEY = os.environ.get("SM_GEMINI_API_KEY", "")
    _SM_MODEL = "google/gemini-2.5-flash"

    @classmethod
    def _get_sm_client(cls):
        if cls._sm_client is None:
            try:
                from openai import OpenAI as _OAI
                cls._sm_client = _OAI(api_key=cls._SM_KEY, base_url="https://api.aimlapi.com/v1")
            except Exception:
                pass
        return cls._sm_client

    def score(self, headline, ticker, direction, tech_data, api_client, model_name):
        import re, json as _json

        prompt = (
            f'As a quantitative portfolio manager, evaluate this trade setup.\n'
            f'News: "{headline}"\n'
            f'Stock: {ticker} | Direction: {direction}\n'
            f'Does this news represent a high-probability move of 3%+ before a 1.5% stop?\n'
            f'Return ONLY valid JSON: {{"score": <0-100>}}'
        )

        raw_text = None

        # --- PRIMARY: Gemini via google-genai SDK ---
        if api_client:
            try:
                response = api_client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                raw_text = response.text
            except Exception as e:
                print(f"   [AILogicModel] Gemini error: {e}")

        # --- FALLBACK: SM-Gemini via OpenAI-compat API ---
        if raw_text is None:
            try:
                sm = self._get_sm_client()
                if sm:
                    resp = sm.chat.completions.create(
                        model=self._SM_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        timeout=20,
                    )
                    raw_text = resp.choices[0].message.content
            except Exception as e:
                print(f"   [AILogicModel] SM-Gemini error: {e}")

        if raw_text is None:
            return None

        try:
            match = re.search(r'\{[^{}]*\}', raw_text, re.DOTALL)
            if match:
                data = _json.loads(match.group(0))
                score = data.get("score")
                if score is not None:
                    return max(10, min(95, int(score)))
        except Exception as e:
            print(f"   [AILogicModel] JSON parse error: {e} | raw={raw_text[:80]}")
        return None


# ==========================================
# ENSEMBLE COMBINER (5 MODELS)
# ==========================================
class EnsemblePredictor:
    """
    Combines all 5 models. Signal only emitted when:
      - Ensemble score >= 50
      - At least 3 of 5 models agree (score > 50)
      - Technical model does NOT veto
    """

    WEIGHTS = {
        'historical': 0.15,
        'technical': 0.20,
        'sector': 0.00,
        'indian_market': 0.15,
        'ai_logic': 0.50,
    }

    def __init__(self):
        self.m2 = HistoricalSimilarityModel()
        self.m3 = TechnicalAlignmentModel()
        self.m4 = SectorMomentumModel()
        self.m6 = IndianSentimentModel()
        self.m7 = AILogicModel()

    def predict(self, headline, ticker, direction, tech_data, market_regime,
                db_connect_fn, api_client=None, model_name=None, min_score=50):
        s2 = self.m2.score(headline, ticker, direction, db_connect_fn)
        s3 = self.m3.score(tech_data, direction)
        s4 = self.m4.score(ticker, direction, market_regime)
        s6 = self.m6.score(direction)
        s7 = self.m7.score(headline, ticker, direction, tech_data, api_client, model_name)

        w_hist = self.WEIGHTS['historical']
        w_tech = self.WEIGHTS['technical']
        w_sec = self.WEIGHTS['sector']
        w_ind = self.WEIGHTS['indian_market']
        w_ai = self.WEIGHTS['ai_logic']

        valid_models = [s2, s3, s4, s6]

        if s7 is None:
            # AI model unavailable — rebalance weights across remaining models
            # Keep sector weight at 0 since it's intentionally disabled
            total_remaining = w_hist + w_tech + w_ind  # 0.50
            if total_remaining > 0:
                scale = (w_hist + w_tech + w_ind + w_ai) / total_remaining
                w_hist *= scale
                w_tech *= scale
                w_ind  *= scale
            w_ai = 0
            s7_val = 0
            min_agree = 2  # Only 4 models available — require 2/4 to agree
        else:
            s7_val = s7
            valid_models.append(s7)
            min_agree = 3  # 5 models available — require 3/5 to agree

        final = int(
            s2 * w_hist +
            s3 * w_tech +
            s4 * w_sec +
            s6 * w_ind +
            s7_val * w_ai
        )

        agree = sum(1 for s in valid_models if s > 50)
        veto = self.m3.has_veto(tech_data, direction)
        approved = final >= min_score and agree >= min_agree and not veto

        s7_str = s7 if s7 is not None else "FAIL"
        total_models = len(valid_models)
        detail_str = f"H:{s2} T:{s3} Sec:{s4} Ind:{s6} AI:{s7_str} | {agree}/{total_models} agree | {'VETO' if veto else 'OK'}"
        return {
            'approved': approved,
            'final_score': final,
            'direction': direction,
            'models_agreeing': agree,
            'has_veto': veto,
            'detail': detail_str,
            'scores': {'historical': s2, 'technical': s3,
                       'sector': s4, 'indian_market': s6, 'ai_logic': s7_val},
        }

    def clear_caches(self):
        self.m4.clear_cache()
        self.m6.clear_cache()
