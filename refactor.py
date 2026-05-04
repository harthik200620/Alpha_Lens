import re

with open('backend/prediction_models.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove SentimentDepthModel
content = re.sub(r'# ==========================================\n# MODEL 1: SENTIMENT DEPTH ANALYSIS\n# ==========================================\nclass SentimentDepthModel:.*?(?=# ==========================================\n# MODEL 2: HISTORICAL SIMILARITY)', '', content, flags=re.DOTALL)

# Remove EventPatternModel
content = re.sub(r'# ==========================================\n# MODEL 5: EVENT PATTERN RECOGNITION\n# ==========================================\nclass EventPatternModel:.*?(?=# ==========================================\n# MODEL 6: GLOBAL & INDIAN MARKET SENTIMENT)', '', content, flags=re.DOTALL)

# Update EnsemblePredictor
old_ensemble = r'# ==========================================\n# ENSEMBLE COMBINER \(7 MODELS\)\n# ==========================================\nclass EnsemblePredictor:.*'
new_ensemble = '''# ==========================================
# ENSEMBLE COMBINER (5 MODELS)
# ==========================================
class EnsemblePredictor:
    \"\"\"
    Combines all 5 models. Signal only emitted when:
      - Ensemble score >= 70                                    
      - At least 3 of 5 models agree (score > 55)
      - Technical model does NOT veto
    \"\"\"

    WEIGHTS = {
        'historical': 0.20,
        'technical': 0.30,
        'sector': 0.00,
        'global': 0.20,
        'ai_logic': 0.30,
    }

    def __init__(self):
        self.m2 = HistoricalSimilarityModel()
        self.m3 = TechnicalAlignmentModel()
        self.m4 = SectorMomentumModel()
        self.m6 = GlobalSentimentModel()
        self.m7 = AILogicModel()

    def predict(self, headline, ticker, direction, tech_data, market_regime,
                db_connect_fn, api_client=None, model_name=None, min_score=70):
        s2 = self.m2.score(headline, ticker, direction, db_connect_fn)
        s3 = self.m3.score(tech_data, direction)
        s4 = self.m4.score(ticker, direction, market_regime)
        s6 = self.m6.score(direction)
        s7 = self.m7.score(headline, ticker, direction, tech_data, api_client, model_name)

        final = int(
            s2 * self.WEIGHTS['historical'] +
            s3 * self.WEIGHTS['technical'] +
            s4 * self.WEIGHTS['sector'] +
            s6 * self.WEIGHTS['global'] +
            s7 * self.WEIGHTS['ai_logic']
        )

        agree = sum(1 for s in [s2, s3, s4, s6, s7] if s > 55)
        veto = self.m3.has_veto(tech_data, direction)
        approved = final >= min_score and agree >= 3 and not veto

        detail_str = f"H:{s2} T:{s3} Sec:{s4} G:{s6} AI:{s7} | {agree}/5 agree | {'VETO' if veto else 'OK'}"
        return {
            'approved': approved,
            'final_score': final,
            'direction': direction,
            'models_agreeing': agree,
            'has_veto': veto,
            'detail': detail_str,
            'scores': {'historical': s2, 'technical': s3,
                       'sector': s4, 'global': s6, 'ai_logic': s7},
        }

    def clear_caches(self):
        self.m4.clear_cache()
        self.m6.clear_cache()
'''
content = re.sub(old_ensemble, new_ensemble, content, flags=re.DOTALL)

with open('backend/prediction_models.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done refactoring')
