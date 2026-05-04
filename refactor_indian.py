import re

with open('backend/prediction_models.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_global_model_pattern = r'# ==========================================\n# MODEL 6: GLOBAL & INDIAN MARKET SENTIMENT\n# ==========================================\nclass GlobalSentimentModel:.*?(?=# ==========================================\n# MODEL 7: AI LOGIC MODEL)'

new_indian_model = '''# ==========================================
# MODEL 6: INDIAN MARKET SENTIMENT
# ==========================================
class IndianSentimentModel:
    \"\"\"
    Analyzes Indian market conditions (Nifty 50, Bank Nifty, India VIX)
    to determine whether macro sentiment supports the predicted direction.
    \"\"\"

    _cache = {}
    _cache_time = 0

    def _fetch_indian_data(self):
        \"\"\"Fetch and cache Indian market data (5-min cache).\"\"\"
        import time
        import yfinance as yf

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
        \"\"\"Returns 0-100 based on Indian market sentiment alignment.\"\"\"
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

'''

content = re.sub(old_global_model_pattern, new_indian_model, content, flags=re.DOTALL)

# Now update the EnsemblePredictor
content = content.replace("'global': 0.15,", "'indian_market': 0.15,")
content = content.replace("GlobalSentimentModel()", "IndianSentimentModel()")
content = content.replace("w_glob = self.WEIGHTS['global']", "w_ind = self.WEIGHTS['indian_market']")
content = content.replace("w_hist + w_tech + w_glob", "w_hist + w_tech + w_ind")
content = content.replace("w_glob = w_glob / total_remaining", "w_ind = w_ind / total_remaining")
content = content.replace("s6 * w_glob", "s6 * w_ind")
content = content.replace("'global': s6", "'indian_market': s6")

with open('backend/prediction_models.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Refactoring complete')
