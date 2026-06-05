"""
Rule-based news classification — pure, AI-free heuristics extracted verbatim
from app.py:
  * FINANCE_KEYWORDS / is_finance_relevant  — fast relevance gate
  * BULLISH_KEYWORDS / BEARISH_KEYWORDS      — sentiment word lists
  * CATEGORY_KEYWORDS / classify_category    — rule-based category
  * STOCK_KEYWORD_MAP                        — instant keyword -> NSE ticker map

No app state / DB / network — stdlib only. app.py imports these names back, so
every call site behaves identically.
"""
import re  # noqa: F401  (kept for parity; matching helpers live in app.py)


# ==========================================
# KEYWORD FILTER — fast relevance check
# ==========================================
FINANCE_KEYWORDS = [
    'stock', 'share', 'shares', 'market', 'nifty', 'sensex', 'bse', 'nse',
    'rally', 'crash', 'bull', 'bear', 'trade', 'trading', 'etf', 'ipo', 'fpo',
    'dividend', 'earnings', 'profit', 'loss', 'revenue', 'quarter',
    'q1', 'q2', 'q3', 'q4', 'rbi', 'sebi', 'inflation', 'rate', 'bond',
    'rupee', 'crude', 'oil', 'gold', 'bank', 'nbfc', 'mutual fund',
    'buy', 'sell', 'target', 'upgrade', 'downgrade', 'fii', 'dii', 'fpi',
    'block deal', 'bulk deal', 'merger', 'acquisition', 'buyback', 'delisting',
    'rebound', 'correction', 'breakout', 'support', 'resistance',
    'sector', 'pharma', 'auto', 'realty', 'infra', 'defence', 'power',
    'cement', 'fmcg', 'telecom', 'midcap', 'smallcap', 'largecap',
    'result', 'growth', 'margin', 'ebitda', 'pat', 'eps',
    'investor', 'portfolio', 'fund', 'index', 'return', 'equity',
    'debt', 'credit', 'loan', 'interest', 'fiscal', 'gdp',
    'export', 'import', 'tariff', 'manufacturing', 'corporate', 'company',
]

def is_finance_relevant(headline):
    h = headline.lower()
    return any(kw in h for kw in FINANCE_KEYWORDS)

# ==========================================
# SENTIMENT KEYWORDS — bullish/bearish rules
# ==========================================
BULLISH_KEYWORDS = [
    'rise', 'rises', 'rising', 'rally', 'rallies', 'surge', 'surges',
    'jump', 'jumps', 'gain', 'gains', 'gained', 'up ', 'high', 'highs',
    'record', 'soar', 'soars', 'zoom', 'zooms', 'profit', 'growth',
    'upgrade', 'outperform', 'buy', 'bullish', 'positive', 'strong',
    'beat', 'beats', 'exceed', 'boost', 'rebound', 'recovery', 'breakout',
    'dividend', 'buyback', 'expansion', 'robust', 'stellar', 'doubles',
    'optimistic', 'upside', 'winner', 'outpace', 'top pick',
]

BEARISH_KEYWORDS = [
    'fall', 'falls', 'falling', 'drop', 'drops', 'crash', 'crashes',
    'plunge', 'plunges', 'decline', 'declines', 'declined', 'down ', 'low',
    'lows', 'sink', 'sinks', 'tumble', 'tumbles', 'loss', 'losses',
    'downgrade', 'underperform', 'sell', 'bearish', 'negative', 'weak',
    'miss', 'misses', 'cut', 'cuts', 'slash', 'concern', 'fear',
    'warning', 'ban', 'penalty', 'fine', 'fraud', 'scam', 'debt',
    'default', 'flee', 'exit', 'outflow', 'worst', 'slump',
]

# ==========================================
# CATEGORY CLASSIFICATION — rule-based
# ==========================================
CATEGORY_KEYWORDS = {
    'Finance': ['stock', 'market', 'nifty', 'sensex', 'rbi', 'sebi', 'fund', 'fii', 'dii', 'bond', 'yield', 'inflation', 'rate', 'rupee', 'forex', 'index', 'rally', 'crash', 'bull', 'bear'],
    'Business': ['company', 'merger', 'acquisition', 'ipo', 'earnings', 'profit', 'revenue', 'ceo', 'board', 'startup', 'valuation', 'q1', 'q2', 'q3', 'q4', 'quarter', 'result', 'dividend', 'buyback'],
    'Technology': ['tech', 'ai ', 'software', 'digital', 'chip', 'semiconductor', 'data', 'cloud', 'cyber', 'app ', 'gadget'],
    'Politics': ['government', 'election', 'minister', 'parliament', 'policy', 'modi', 'bjp', 'congress', 'bill ', 'political'],
    'World': ['global', 'us ', 'china', 'trump', 'fed ', 'european', 'war', 'tariff', 'trade war', 'geopolitical', 'iran', 'russia'],
}

def classify_category(headline):
    h = headline.lower()
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw in h)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else 'General'

# ==========================================
# RULE-BASED STOCK MAPPING — instant, no AI
# ==========================================

# All keywords use plain strings; matching uses regex word-boundaries (see get_candidate_stocks)
STOCK_KEYWORD_MAP = {
    # ── NIFTY 50 ──
    'reliance industries': 'RELIANCE.NS', 'reliance': 'RELIANCE.NS', 'ril': 'RELIANCE.NS',
    'tata consultancy': 'TCS.NS', 'tcs': 'TCS.NS',
    'infosys': 'INFY.NS', 'infy': 'INFY.NS',
    'hdfc bank': 'HDFCBANK.NS', 'hdfcbank': 'HDFCBANK.NS', 'hdfc': 'HDFCBANK.NS',
    'icici bank': 'ICICIBANK.NS', 'icicibank': 'ICICIBANK.NS', 'icici': 'ICICIBANK.NS',
    'state bank of india': 'SBIN.NS', 'state bank': 'SBIN.NS', 'sbi': 'SBIN.NS',
    'bharti airtel': 'BHARTIARTL.NS', 'airtel': 'BHARTIARTL.NS',
    'hindustan unilever': 'HINDUNILVR.NS', 'hul': 'HINDUNILVR.NS',
    'itc': 'ITC.NS',
    'kotak mahindra': 'KOTAKBANK.NS', 'kotak bank': 'KOTAKBANK.NS', 'kotak': 'KOTAKBANK.NS',
    'larsen & toubro': 'LT.NS', 'larsen and toubro': 'LT.NS', 'larsen': 'LT.NS', 'l&t': 'LT.NS', 'l and t': 'LT.NS',
    'axis bank': 'AXISBANK.NS', 'axis': 'AXISBANK.NS',
    'bajaj finance': 'BAJFINANCE.NS',
    'bajaj finserv': 'BAJAJFINSV.NS',
    'maruti suzuki': 'MARUTI.NS', 'maruti': 'MARUTI.NS',
    'asian paints': 'ASIANPAINT.NS',
    'titan company': 'TITAN.NS', 'titan': 'TITAN.NS',
    'sun pharmaceutical': 'SUNPHARMA.NS', 'sun pharma': 'SUNPHARMA.NS',
    'wipro': 'WIPRO.NS',
    'hcl technologies': 'HCLTECH.NS', 'hcl tech': 'HCLTECH.NS', 'hcl': 'HCLTECH.NS',
    'power grid': 'POWERGRID.NS', 'powergrid': 'POWERGRID.NS',
    'ntpc': 'NTPC.NS',
    'tata motors': 'TMPV.NS',
    'tata steel': 'TATASTEEL.NS',
    'mahindra & mahindra': 'M&M.NS', 'mahindra and mahindra': 'M&M.NS', 'mahindra': 'M&M.NS', 'm&m': 'M&M.NS',
    'adani enterprises': 'ADANIENT.NS', 'adani ent': 'ADANIENT.NS',
    'adani ports': 'ADANIPORTS.NS',
    'adani green': 'ADANIGREEN.NS',
    'adani power': 'ADANIPOWER.NS',
    'adani total': 'ADANITOTAL.NS',
    'adani': 'ADANIENT.NS',
    'ultratech cement': 'ULTRACEMCO.NS', 'ultratech': 'ULTRACEMCO.NS',
    'nestle india': 'NESTLEIND.NS', 'nestle': 'NESTLEIND.NS',
    'tech mahindra': 'TECHM.NS',
    'indusind bank': 'INDUSINDBK.NS', 'indusind': 'INDUSINDBK.NS',
    'grasim': 'GRASIM.NS',
    'bajaj auto': 'BAJAJ-AUTO.NS',
    'cipla': 'CIPLA.NS',
    'dr reddy': 'DRREDDY.NS', "dr. reddy's": 'DRREDDY.NS', 'dr reddys': 'DRREDDY.NS',
    'hero motocorp': 'HEROMOTOCO.NS', 'hero moto': 'HEROMOTOCO.NS', 'hero': 'HEROMOTOCO.NS',
    'coal india': 'COALINDIA.NS',
    'ongc': 'ONGC.NS',
    'bharat petroleum': 'BPCL.NS', 'bpcl': 'BPCL.NS',
    "divi's laboratories": 'DIVISLAB.NS', "divi's lab": 'DIVISLAB.NS', 'divis lab': 'DIVISLAB.NS', 'divis': 'DIVISLAB.NS',
    'britannia': 'BRITANNIA.NS',
    'eicher motors': 'EICHERMOT.NS', 'royal enfield': 'EICHERMOT.NS',
    'apollo hospitals': 'APOLLOHOSP.NS', 'apollo hospital': 'APOLLOHOSP.NS', 'apollo': 'APOLLOHOSP.NS',
    'tata consumer': 'TATACONSUM.NS',
    'sbi life': 'SBILIFE.NS',
    'hdfc life': 'HDFCLIFE.NS',
    'shriram finance': 'SHRIRAMFIN.NS',
    'bhel': 'BHEL.NS', 'bharat heavy electricals': 'BHEL.NS',
    'jsw steel': 'JSWSTEEL.NS', 'jsw': 'JSWSTEEL.NS',
    'hindalco': 'HINDALCO.NS',
    # ── Popular Mid/Small Caps ──
    'muthoot finance': 'MUTHOOTFIN.NS', 'muthoot fin': 'MUTHOOTFIN.NS', 'muthoot': 'MUTHOOTFIN.NS',
    'aurobindo pharma': 'AUROPHARMA.NS', 'aurobindo': 'AUROPHARMA.NS',
    'hindustan petroleum': 'HINDPETRO.NS', 'hpcl': 'HINDPETRO.NS',
    'indian oil': 'IOC.NS', 'ioc': 'IOC.NS',
    'bharat electronics': 'BEL.NS', 'bel': 'BEL.NS',
    'hindustan aeronautics': 'HAL.NS', 'hal': 'HAL.NS',
    'solar industries': 'SOLARINDS.NS',
    'vodafone idea': 'IDEA.NS', 'vi ': 'IDEA.NS',
    'godfrey phillips': 'GODFRYPHLP.NS',
    'tejas networks': 'TEJASNET.NS', 'tejas network': 'TEJASNET.NS',
    'bandhan bank': 'BANDHANBNK.NS', 'bandhan': 'BANDHANBNK.NS',
    'manappuram': 'MANAPPURAM.NS',
    'zomato': 'ZOMATO.NS',
    'paytm': 'PAYTM.NS', 'one97': 'PAYTM.NS',
    'nykaa': 'NYKAA.NS',
    'delhivery': 'DELHIVERY.NS',
    'vedanta': 'VEDL.NS',
    'jindal steel': 'JINDALSTEL.NS', 'jindal': 'JINDALSTEL.NS',
    'tata power': 'TATAPOWER.NS',
    'tata elxsi': 'TATAELXSI.NS',
    'ltimindtree': 'LTIM.NS', 'lti mindtree': 'LTIM.NS', 'lti': 'LTIM.NS',
    'punjab national bank': 'PNB.NS', 'punjab national': 'PNB.NS', 'pnb': 'PNB.NS',
    'bank of baroda': 'BANKBARODA.NS', 'bob': 'BANKBARODA.NS',
    'canara bank': 'CANBK.NS', 'canara': 'CANBK.NS',
    'idbi bank': 'IDBI.NS', 'idbi': 'IDBI.NS',
    'federal bank': 'FEDERALBNK.NS',
    'yes bank': 'YESBANK.NS',
    'irctc': 'IRCTC.NS',
    'irfc': 'IRFC.NS',
    'rvnl': 'RVNL.NS', 'rail vikas': 'RVNL.NS',
    'nhpc': 'NHPC.NS',
    'suzlon energy': 'SUZLON.NS', 'suzlon': 'SUZLON.NS',
    'tata chemicals': 'TATACHEM.NS',
    'godrej consumer': 'GODREJCP.NS', 'godrej': 'GODREJCP.NS',
    'pidilite': 'PIDILITIND.NS',
    'havells': 'HAVELLS.NS',
    'siemens': 'SIEMENS.NS',
    'abb india': 'ABB.NS', 'abb': 'ABB.NS',
    'page industries': 'PAGEIND.NS',
    'dmart': 'DMART.NS', 'avenue supermarts': 'DMART.NS',
    'biocon': 'BIOCON.NS',
    'lupin': 'LUPIN.NS',
    'torrent pharma': 'TORNTPHARM.NS', 'torrent': 'TORNTPHARM.NS',
    'jubilant foodworks': 'JUBLFOOD.NS', 'jubilant food': 'JUBLFOOD.NS',
    'indigo airlines': 'INDIGO.NS', 'interglobe aviation': 'INDIGO.NS', 'indigo': 'INDIGO.NS',
    'spicejet': 'SPICEJET.NS',
    'dixon technologies': 'DIXON.NS', 'dixon tech': 'DIXON.NS', 'dixon': 'DIXON.NS',
    'polycab': 'POLYCAB.NS',
    'persistent systems': 'PERSISTENT.NS', 'persistent': 'PERSISTENT.NS',
    'coforge': 'COFORGE.NS',
    'mphasis': 'MPHASIS.NS',
    'max healthcare': 'MAXHEALTH.NS', 'max health': 'MAXHEALTH.NS',
    'motherson sumi': 'MOTHERSON.NS', 'motherson': 'MOTHERSON.NS',
    'srf': 'SRF.NS',
    'pi industries': 'PIIND.NS',
    'cholamandalam investment': 'CHOLAFIN.NS', 'cholamandalam': 'CHOLAFIN.NS', 'chola': 'CHOLAFIN.NS',
    'voltas': 'VOLTAS.NS',
    'bharat forge': 'BHARATFORG.NS',
    'exide industries': 'EXIDEIND.NS', 'exide': 'EXIDEIND.NS',
    'amara raja': 'AMARAJABAT.NS',
    'panasonic energy': 'LAKHNNATNL.NS', 'panasonic': 'LAKHNNATNL.NS',
    'marico': 'MARICO.NS',
    'dabur': 'DABUR.NS',
    'colgate palmolive': 'COLPAL.NS', 'colgate': 'COLPAL.NS',
    'acc cement': 'ACC.NS', 'acc': 'ACC.NS',
    'ambuja cements': 'AMBUJACEM.NS', 'ambuja cement': 'AMBUJACEM.NS', 'ambuja': 'AMBUJACEM.NS',
    'shree cement': 'SHREECEM.NS', 'shree': 'SHREECEM.NS',
    'dalmia bharat': 'DALBHARAT.NS', 'dalmia': 'DALBHARAT.NS',
    'hatsun agro': 'HATSUN.NS', 'hatsun': 'HATSUN.NS',
    # ── Tata Group (generic "tata" catches news about the whole group) ──
    'tata group': 'TMPV.NS',
    # ── Other large populars ──
    'dlf': 'DLF.NS',
    'lodha': 'LODHA.NS', 'macrotech': 'LODHA.NS',
    'oberoi realty': 'OBEROIRLTY.NS', 'oberoi': 'OBEROIRLTY.NS',
    'lici': 'LICI.NS', 'lic india': 'LICI.NS', 'lic': 'LICI.NS',
    'nuvoco': 'NUVOCO.NS',
    'syngene': 'SYNGENE.NS',
    'laurus labs': 'LAURUSLABS.NS', 'laurus': 'LAURUSLABS.NS',
    'alkem laboratories': 'ALKEM.NS', 'alkem': 'ALKEM.NS',
    'the ramco': 'RAMCOCEM.NS', 'ramco cement': 'RAMCOCEM.NS',
    'emami': 'EMAMILTD.NS',
    'astral': 'ASTRAL.NS',
    'supreme industries': 'SUPREMEIND.NS',
    'kajaria': 'KAJARIACER.NS', 'kajaria ceramics': 'KAJARIACER.NS',
    'relaxo': 'RELAXO.NS',
    'campus activewear': 'CAMPUS.NS',
    'one mobi': 'ONMOBILE.NS',
    'nesco': 'NESCO.NS',
    'gland pharma': 'GLAND.NS',
    'ipca laboratories': 'IPCALAB.NS', 'ipca': 'IPCALAB.NS',
    'navin fluorine': 'NAVINFLUOR.NS',
    'deepak nitrite': 'DEEPAKNTR.NS', 'deepak': 'DEEPAKNTR.NS',
    'clean science': 'CLEANSCI.NS',
    'fine organics': 'FINEORG.NS',
    'aarti industries': 'AARTIIND.NS', 'aarti': 'AARTIIND.NS',
    'nocil': 'NOCIL.NS',
    'bombay burmah': 'BBTC.NS',
    'edelweiss': 'EDELWEISS.NS',
    'angel one': 'ANGELONE.NS', 'angel broking': 'ANGELONE.NS',
    'hdfc amc': 'HDFCAMC.NS',
    'nippon india': 'NAM-INDIA.NS', 'nippon': 'NAM-INDIA.NS',
    'bajaj consumer': 'BAJAJCON.NS',
    'trent': 'TRENT.NS',
    'v-mart': 'VMART.NS', 'v mart': 'VMART.NS',
    'metro brands': 'METROBRAND.NS',
    'bata': 'BATAIND.NS', 'bata india': 'BATAIND.NS',
    'kpit technologies': 'KPITTECH.NS', 'kpit': 'KPITTECH.NS',
    'tata technologies': 'TATATECH.NS', 'tata tech': 'TATATECH.NS',
    'cams': 'CAMS.NS',
    'cdsl': 'CDSL.NS',
    'bse': 'BSE.NS',
    'mcx': 'MCX.NS',
    'nse india': 'NSEI.NS',
    'mamaearth': 'HONASA.NS', 'honasa': 'HONASA.NS',
    'boat': 'IMAGINE.NS',
    'swiggy': 'SWIGGY.NS',
    'ola electric': 'OLAELEC.NS', 'ola': 'OLAELEC.NS',
}
