"""
Alpha Lens v5.0 — Multi-Model Ensemble Prediction Engine
6 independent models analyze news → stock impact.
Signal emitted ONLY when ensemble score >= 70 AND 4+ models agree.
"""
import re
import math

# ==========================================
# MODEL 1: SENTIMENT DEPTH ANALYSIS
# ==========================================
class SentimentDepthModel:
    """Analyzes headline sentiment intensity — keyword strength, negation, percentage modifiers."""
    
    STRONG_BULLISH = ['surge', 'surges', 'soar', 'soars', 'zoom', 'zooms', 'skyrocket',
                      'doubles', 'triples', 'record high', 'all-time high', 'blockbuster',
                      'stellar', 'robust', 'massive', 'breakout', '52-week high']
    MILD_BULLISH = ['rise', 'rises', 'gain', 'gains', 'up ', 'high', 'positive',
                    'growth', 'profit', 'beat', 'rebound', 'recovery', 'dividend',
                    'upgrade', 'buy', 'bullish', 'outperform', 'optimistic', 'winner',
                    'top pick', 'expansion', 'recommend', 'jumps', 'jump', 'advances',
                    'higher', 'gainer', 'gainers', 'best performer', 'outpaces',
                    'valued', 'confident', 'strong results', 'record', 'boost',
                    'allotment', 'listing', 'inflow', 'rally', 'rallies', 'upside']
    STRONG_BEARISH = ['crash', 'crashes', 'plunge', 'plunges', 'collapse', 'tank', 'tanks',
                      'worst', 'crisis', 'scam', 'fraud', 'ban', 'default', 'bloodbath',
                      'meltdown', 'wipeout', 'halt', '52-week low']
    MILD_BEARISH = ['fall', 'falls', 'drop', 'drops', 'decline', 'declines', 'down ',
                    'loss', 'losses', 'weak', 'negative', 'concern', 'fear', 'sell',
                    'downgrade', 'underperform', 'miss', 'cut', 'cuts', 'slash', 'warning',
                    'flee', 'exit', 'outflow', 'slump', 'lower', 'loser', 'losers',
                    'pressure', 'drag', 'disappoint', 'disappoints', 'fii sells',
                    'bearish', 'underperforms', 'slows', 'retreats', 'selling']
    NEGATION = ['despite', 'but', 'however', 'although', 'even as', 'in spite of']
    INTENSITY = {'sharply': 1.5, 'significantly': 1.4, 'massively': 1.8, 'slightly': 0.5,
                 'marginally': 0.4, 'strongly': 1.5, 'heavily': 1.6, 'aggressively': 1.7}

    def score(self, headline, direction):
        """Returns 0-100. Higher = more confidence in the given direction."""
        h = ' ' + headline.lower() + ' '
        strong_bull = sum(2 for kw in self.STRONG_BULLISH if kw in h)
        mild_bull = sum(1 for kw in self.MILD_BULLISH if kw in h)
        strong_bear = sum(2 for kw in self.STRONG_BEARISH if kw in h)
        mild_bear = sum(1 for kw in self.MILD_BEARISH if kw in h)
        bull_total = strong_bull + mild_bull
        bear_total = strong_bear + mild_bear

        # Negation flips partial sentiment
        if any(neg in h for neg in self.NEGATION):
            bull_total, bear_total = bear_total * 0.6, bull_total * 0.6

        # Intensity multiplier
        intensity = max((mult for word, mult in self.INTENSITY.items() if word in h), default=1.0)

        # Percentage bonus
        pct_match = re.search(r'(\d+\.?\d*)%', headline)
        pct_bonus = min(15, float(pct_match.group(1)) * 2) if pct_match else 0

        total = bull_total + bear_total
        if total == 0:
            return 45

        alignment = (bull_total / total) if direction == 'BULLISH' else (bear_total / total)
        base = alignment * intensity * 70
        return max(20, min(95, int(base + pct_bonus)))


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
            return 50
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
# MODEL 5: EVENT PATTERN RECOGNITION
# ==========================================
class EventPatternModel:
    """Classifies event type and applies known market behavior patterns."""

    PATTERNS = {
        'earnings_beat': {
            'kw': ['beat', 'beats', 'above estimate', 'profit rise', 'profit jump',
                   'profit surge', 'net profit', 'strong results', 'stellar',
                   'blockbuster', 'doubles', 'revenue growth', 'record profit',
                   'pat rise', 'pat jump'],
            'dir': 'BULLISH', 'base': 75},
        'earnings_miss': {
            'kw': ['miss', 'misses', 'below estimate', 'profit fall', 'profit drop',
                   'loss widens', 'net loss', 'revenue decline', 'weak results',
                   'disappointing', 'margin squeeze', 'margin pressure'],
            'dir': 'BEARISH', 'base': 72},
        'upgrade': {
            'kw': ['upgrade', 'buy rating', 'outperform', 'top pick',
                   'target raise', 'target hike', 'price target raise'],
            'dir': 'BULLISH', 'base': 68},
        'downgrade': {
            'kw': ['downgrade', 'sell rating', 'underperform', 'underweight',
                   'target cut', 'target slash', 'reduce rating'],
            'dir': 'BEARISH', 'base': 68},
        'insider_buy': {
            'kw': ['promoter buy', 'insider buy', 'bulk buy', 'stake increase', 'buyback'],
            'dir': 'BULLISH', 'base': 70},
        'insider_sell': {
            'kw': ['promoter sell', 'insider sell', 'stake sale', 'offload',
                   'fii sell', 'fii exit', 'fii flee', 'fpi sell'],
            'dir': 'BEARISH', 'base': 65},
        'merger': {
            'kw': ['merger', 'acquisition', 'acquire', 'takeover', 'buyout', 'joint venture'],
            'dir': 'BULLISH', 'base': 65},
        'reg_positive': {
            'kw': ['approval', 'clearance', 'license', 'nod', 'pli', 'subsidy', 'incentive'],
            'dir': 'BULLISH', 'base': 68},
        'reg_negative': {
            'kw': ['ban', 'penalty', 'fine', 'probe', 'investigation', 'sebi order',
                   'suspension', 'scam', 'fraud'],
            'dir': 'BEARISH', 'base': 72},
        'macro_up': {
            'kw': ['rate cut', 'stimulus', 'fii inflow', 'gdp growth', 'recovery', 'ceasefire'],
            'dir': 'BULLISH', 'base': 62},
        'macro_down': {
            'kw': ['rate hike', 'inflation surge', 'fii outflow', 'tariff',
                   'trade war', 'recession', 'geopolitical'],
            'dir': 'BEARISH', 'base': 62},
    }

    def score(self, headline, direction):
        """Returns 0-100 based on event pattern matching."""
        h = headline.lower()
        best, best_n = None, 0
        for p in self.PATTERNS.values():
            n = sum(1 for kw in p['kw'] if kw in h)
            if n > best_n:
                best_n, best = n, p
        if not best or best_n == 0:
            return 55
        if direction == best['dir']:
            return min(90, best['base'] + best_n * 5)
        else:
            return max(25, best['base'] - best_n * 10)


# ==========================================
# MODEL 6: GLOBAL & INDIAN MARKET SENTIMENT
# ==========================================
class GlobalSentimentModel:
    """
    Analyzes global market conditions (S&P 500, VIX, US 10Y yield)
    and Indian market conditions (Nifty 50, India VIX) to determine
    whether macro sentiment supports the predicted direction.
    """

    _cache = {}
    _cache_time = 0

    def _fetch_global_data(self):
        """Fetch and cache global + Indian market data (5-min cache)."""
        import time
        import yfinance_twelvedata_shim as yf

        now = time.time()
        if self._cache and (now - self._cache_time) < 300:
            return self._cache

        data = {}

        # S&P 500 — Global risk appetite
        try:
            sp = yf.Ticker("^GSPC")
            hist = sp.history(period='10d')
            if not hist.empty and len(hist) >= 2:
                c = hist['Close'].tolist()
                data['sp500_ret_5d'] = ((c[-1] - c[0]) / c[0]) * 100
                data['sp500_ret_1d'] = ((c[-1] - c[-2]) / c[-2]) * 100
            else:
                data['sp500_ret_5d'] = 0
                data['sp500_ret_1d'] = 0
        except:
            data['sp500_ret_5d'] = 0
            data['sp500_ret_1d'] = 0

        # VIX — Fear gauge
        try:
            vix = yf.Ticker("^VIX")
            hist = vix.history(period='5d')
            if not hist.empty:
                data['vix'] = hist['Close'].tolist()[-1]
            else:
                data['vix'] = 20  # neutral default
        except:
            data['vix'] = 20

        # US 10-Year Treasury Yield — Risk-free rate environment
        try:
            tny = yf.Ticker("^TNX")
            hist = tny.history(period='10d')
            if not hist.empty and len(hist) >= 2:
                c = hist['Close'].tolist()
                data['us10y_change'] = c[-1] - c[-2]
                data['us10y_level'] = c[-1]
            else:
                data['us10y_change'] = 0
                data['us10y_level'] = 4.0
        except:
            data['us10y_change'] = 0
            data['us10y_level'] = 4.0

        # Nifty 50 — Indian market strength
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
        """Returns 0-100 based on global + Indian market sentiment alignment."""
        data = self._fetch_global_data()
        s = 50
        bull = (direction == 'BULLISH')

        # ── 1. S&P 500 momentum (global risk appetite) ──
        sp_5d = data.get('sp500_ret_5d', 0)
        if sp_5d > 2:
            s += 8 if bull else -6
        elif sp_5d > 0.5:
            s += 4 if bull else -3
        elif sp_5d < -2:
            s += -8 if bull else 8
        elif sp_5d < -0.5:
            s += -4 if bull else 4

        # ── 2. VIX (fear gauge) ──
        vix = data.get('vix', 20)
        if vix > 30:
            # High fear — bearish bias
            s += -10 if bull else 10
        elif vix > 22:
            s += -5 if bull else 5
        elif vix < 14:
            # Complacency — slightly bullish but watch out
            s += 5 if bull else -3

        # ── 3. US 10Y Yield change (rising yields = bearish for equities) ──
        yield_change = data.get('us10y_change', 0)
        if yield_change > 0.1:
            s += -4 if bull else 4
        elif yield_change < -0.1:
            s += 4 if bull else -4

        # ── 4. Nifty 50 momentum (Indian domestic strength) ──
        nifty_5d = data.get('nifty_ret_5d', 0)
        if nifty_5d > 2:
            s += 8 if bull else -6
        elif nifty_5d > 0.5:
            s += 4 if bull else -3
        elif nifty_5d < -2:
            s += -8 if bull else 8
        elif nifty_5d < -0.5:
            s += -4 if bull else 4

        # ── 5. India VIX ──
        ivix = data.get('india_vix', 15)
        if ivix > 22:
            # High India VIX — uncertainty/fear
            s += -6 if bull else 6
        elif ivix > 18:
            s += -3 if bull else 3
        elif ivix < 12:
            s += 4 if bull else -2

        # ── 6. Global-Indian divergence (FII flow proxy) ──
        # If global is up but India is down → FII selling pressure
        # If global is down but India is up → DII support
        sp_1d = data.get('sp500_ret_1d', 0)
        nifty_1d = data.get('nifty_ret_1d', 0)
        divergence = nifty_1d - sp_1d
        if divergence > 0.5:
            # India outperforming global = DII/domestic strength
            s += 4 if bull else -3
        elif divergence < -0.5:
            # India underperforming = FII selling or weakness
            s += -4 if bull else 3

        return max(15, min(90, s))

    def clear_cache(self):
        self._cache = {}
        self._cache_time = 0


# ==========================================
# MODEL 7: AI LOGIC MODEL
# ==========================================
class AILogicModel:
    """
    Sends the headline, ticker, and formatted technical context to Gemini
    to get a deeply reasoned quantitative confirmation score (0-100).
    """
    
    def score(self, headline, ticker, direction, tech_data, api_client, model_name):
        if not api_client or not tech_data:
            return 50
            
        from technical_analysis import format_technical_context_for_prompt
        tech_str = format_technical_context_for_prompt(tech_data)
        
        prompt = f"""As an elite quantitative multi-strategy portfolio manager, evaluate this potential setup:
News Headline: "{headline}"
Target Ticker: {ticker}
Direction Bias: {direction}

Technical & Volatility Context:
{tech_str}

Given the news catalyst and the precise technical context (EMA alignment, Volume Profile, Liquidity sweeps, ADX trend strength), does this represent a highly actionable, high-probability trade setup that will move 3% before hitting a 1.5% stop loss?
Consider if the news is already priced into the technicals.
Return ONLY a valid JSON object in this format: {{"score": <integer from 0 to 100>}}. Try to be decisive; if there is any directional bias, score it above 60."""

        try:
            response = api_client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            text = response.text
            import re, json
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                return max(10, min(95, int(data.get("score", 50))))
            return 50
        except Exception as e:
            return 50


# ==========================================
# ENSEMBLE COMBINER (7 MODELS)
# ==========================================
class EnsemblePredictor:
    """
    Combines all 7 models. Signal only emitted when:
      - Ensemble score >= 70                                    
      - At least 5 of 7 models agree (score > 55)
      - Technical model does NOT veto
    """

    WEIGHTS = {
        'sentiment': 0.10,
        'historical': 0.15,
        'technical': 0.20,
        'sector': 0.00,
        'event': 0.10,
        'global': 0.15,
        'ai_logic': 0.30,
    }

    def __init__(self):
        self.m1 = SentimentDepthModel()
        self.m2 = HistoricalSimilarityModel()
        self.m3 = TechnicalAlignmentModel()
        self.m4 = SectorMomentumModel()
        self.m5 = EventPatternModel()
        self.m6 = GlobalSentimentModel()
        self.m7 = AILogicModel()

    def predict(self, headline, ticker, direction, tech_data, market_regime,
                db_connect_fn, api_client=None, model_name=None, min_score=70):
        s1 = self.m1.score(headline, direction)
        s2 = self.m2.score(headline, ticker, direction, db_connect_fn)
        s3 = self.m3.score(tech_data, direction)
        s4 = self.m4.score(ticker, direction, market_regime)
        s5 = self.m5.score(headline, direction)
        s6 = self.m6.score(direction)
        s7 = self.m7.score(headline, ticker, direction, tech_data, api_client, model_name)

        final = int(
            s1 * self.WEIGHTS['sentiment'] +
            s2 * self.WEIGHTS['historical'] +
            s3 * self.WEIGHTS['technical'] +
            s4 * self.WEIGHTS['sector'] +
            s5 * self.WEIGHTS['event'] +
            s6 * self.WEIGHTS['global'] +
            s7 * self.WEIGHTS['ai_logic']
        )

        agree = sum(1 for s in [s1, s2, s3, s4, s5, s6, s7] if s > 55)
        veto = self.m3.has_veto(tech_data, direction)
        approved = final >= min_score and agree >= 2 and not veto

        detail_str = f"S:{s1} H:{s2} T:{s3} Sec:{s4} E:{s5} G:{s6} AI:{s7} | {agree}/7 agree | {'VETO' if veto else 'OK'}"
        return {
            'approved': approved,
            'final_score': final,
            'direction': direction,
            'models_agreeing': agree,
            'has_veto': veto,
            'detail': detail_str,
            'scores': {'sentiment': s1, 'historical': s2, 'technical': s3,
                       'sector': s4, 'event': s5, 'global': s6, 'ai_logic': s7},
        }

    def clear_caches(self):
        self.m4.clear_cache()
        self.m6.clear_cache()
