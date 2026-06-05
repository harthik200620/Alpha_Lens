"""
Static data tables used by the news engine — pure data, no logic/state.
Extracted verbatim from app.py:
  * MACRO_IMPACT_MAP        — macro keyword -> sector / 2nd-order effect map
  * MATERIAL_EVENT_KEYWORDS — materiality signal phrases
  * LOW_SIGNAL_PHRASES      — noise phrases that suppress a signal
  * INDEX_LIKE_SYMBOLS      — index-like symbols handled specially
  * COMMON_UPPERCASE_WORDS  — uppercase tokens that are NOT tickers (ticker parsing)

app.py imports these names back, so behaviour is identical.
"""


# ==========================================
# MACRO & SECTOR IMPACT MAP — 2nd order effects
# ==========================================
MACRO_IMPACT_MAP = {
    # ── Crude oil ──
    'crude oil rise': [('ONGC.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH'), ('ASIANPAINT.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    'crude oil crash': [('ONGC.NS', 'BEARISH'), ('BPCL.NS', 'BULLISH'), ('ASIANPAINT.NS', 'BULLISH'), ('INDIGO.NS', 'BULLISH')],
    'crude rises': [('ONGC.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    'crude falls': [('ONGC.NS', 'BEARISH'), ('BPCL.NS', 'BULLISH'), ('INDIGO.NS', 'BULLISH')],
    'oil prices rise': [('ONGC.NS', 'BULLISH'), ('HINDPETRO.NS', 'BEARISH'), ('BPCL.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    'oil prices fall': [('ONGC.NS', 'BEARISH'), ('HINDPETRO.NS', 'BULLISH'), ('BPCL.NS', 'BULLISH'), ('INDIGO.NS', 'BULLISH')],
    'opec cut': [('ONGC.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH'), ('IOC.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    'opec increase': [('ONGC.NS', 'BEARISH'), ('BPCL.NS', 'BULLISH'), ('IOC.NS', 'BULLISH'), ('INDIGO.NS', 'BULLISH')],
    # ── FII / FPI ──
    'fii selling': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'fii sell': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'fiis sell': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'fii outflow': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'fpi outflow': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'fii buying': [('HDFCBANK.NS', 'BULLISH'), ('ICICIBANK.NS', 'BULLISH'), ('RELIANCE.NS', 'BULLISH')],
    'fii inflow': [('HDFCBANK.NS', 'BULLISH'), ('ICICIBANK.NS', 'BULLISH'), ('RELIANCE.NS', 'BULLISH')],
    'fpi inflow': [('HDFCBANK.NS', 'BULLISH'), ('ICICIBANK.NS', 'BULLISH'), ('RELIANCE.NS', 'BULLISH')],
    # ── RBI / Rates ──
    'rate hike': [('DLF.NS', 'BEARISH'), ('LODHA.NS', 'BEARISH'), ('SBIN.NS', 'BULLISH'), ('BAJFINANCE.NS', 'BEARISH')],
    'rate cut': [('DLF.NS', 'BULLISH'), ('LODHA.NS', 'BULLISH'), ('SBIN.NS', 'BEARISH'), ('BAJFINANCE.NS', 'BULLISH')],
    'repo rate cut': [('HDFCBANK.NS', 'BULLISH'), ('SBIN.NS', 'BULLISH'), ('DLF.NS', 'BULLISH'), ('BAJFINANCE.NS', 'BULLISH')],
    'repo rate hike': [('HDFCBANK.NS', 'BEARISH'), ('SBIN.NS', 'BEARISH'), ('DLF.NS', 'BEARISH')],
    'rbi policy': [('HDFCBANK.NS', 'BULLISH'), ('SBIN.NS', 'BULLISH'), ('ICICIBANK.NS', 'BULLISH')],
    # ── Semiconductor / Chips (Global → India) ──
    'semiconductor shortage': [('INFY.NS', 'BEARISH'), ('WIPRO.NS', 'BEARISH'), ('TMPV.NS', 'BEARISH'), ('MARUTI.NS', 'BEARISH')],
    'chip shortage': [('TMPV.NS', 'BEARISH'), ('MARUTI.NS', 'BEARISH'), ('HEROMOTOCO.NS', 'BEARISH'), ('EICHERMOT.NS', 'BEARISH')],
    'semiconductor ban': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('WIPRO.NS', 'BEARISH'), ('TMPV.NS', 'BEARISH')],
    'chip export ban': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('TMPV.NS', 'BEARISH')],
    'semiconductor plant india': [('VEDL.NS', 'BULLISH'), ('TATAELXSI.NS', 'BULLISH'), ('DIXON.NS', 'BULLISH')],
    'chip fab india': [('VEDL.NS', 'BULLISH'), ('TATAELXSI.NS', 'BULLISH')],
    # ── Japan / China / US Geopolitical Supply Chain ──
    'japan export control': [('TMPV.NS', 'BEARISH'), ('MARUTI.NS', 'BEARISH'), ('INFY.NS', 'BEARISH')],
    'japan semiconductor': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('WIPRO.NS', 'BEARISH'), ('TMPV.NS', 'BEARISH')],
    'china slowdown': [('TATASTEEL.NS', 'BEARISH'), ('JSWSTEEL.NS', 'BEARISH'), ('HINDALCO.NS', 'BEARISH'), ('COALINDIA.NS', 'BEARISH')],
    'china stimulus': [('TATASTEEL.NS', 'BULLISH'), ('JSWSTEEL.NS', 'BULLISH'), ('HINDALCO.NS', 'BULLISH')],
    'china tariff': [('TATASTEEL.NS', 'BULLISH'), ('JSWSTEEL.NS', 'BULLISH'), ('DIXON.NS', 'BULLISH')],
    'china dumping': [('TATASTEEL.NS', 'BEARISH'), ('JSWSTEEL.NS', 'BEARISH'), ('HINDALCO.NS', 'BEARISH')],
    'us fed rate': [('HDFCBANK.NS', 'BEARISH'), ('ICICIBANK.NS', 'BEARISH'), ('INFY.NS', 'BEARISH')],
    'fed rate cut': [('INFY.NS', 'BULLISH'), ('TCS.NS', 'BULLISH'), ('HDFCBANK.NS', 'BULLISH')],
    'fed rate hike': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('HDFCBANK.NS', 'BEARISH')],
    'us recession': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('WIPRO.NS', 'BEARISH'), ('HCLTECH.NS', 'BEARISH')],
    'us sanctions': [('RELIANCE.NS', 'BEARISH'), ('ONGC.NS', 'BEARISH')],
    'taiwan tension': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH'), ('TMPV.NS', 'BEARISH')],
    'taiwan strait': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH')],
    'russia ukraine': [('ONGC.NS', 'BULLISH'), ('COALINDIA.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH')],
    'middle east tension': [('ONGC.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    'iran conflict': [('ONGC.NS', 'BULLISH'), ('BPCL.NS', 'BEARISH'), ('INDIGO.NS', 'BEARISH')],
    # ── Currency / Trade ──
    'rupee falls': [('INFY.NS', 'BULLISH'), ('TCS.NS', 'BULLISH'), ('WIPRO.NS', 'BULLISH'), ('SUNPHARMA.NS', 'BULLISH')],
    'rupee weakens': [('INFY.NS', 'BULLISH'), ('TCS.NS', 'BULLISH'), ('SUNPHARMA.NS', 'BULLISH')],
    'rupee rises': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH')],
    'rupee strengthens': [('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH')],
    'dollar surge': [('INFY.NS', 'BULLISH'), ('TCS.NS', 'BULLISH'), ('MARUTI.NS', 'BEARISH')],
    'tariff': [('TMPV.NS', 'BEARISH'), ('INFY.NS', 'BEARISH'), ('TCS.NS', 'BEARISH')],
    'trade war': [('TMPV.NS', 'BEARISH'), ('INFY.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'anti-dumping duty': [('TATASTEEL.NS', 'BULLISH'), ('JSWSTEEL.NS', 'BULLISH')],
    'import duty hike': [('DIXON.NS', 'BULLISH'), ('TATASTEEL.NS', 'BULLISH')],
    'pli scheme': [('DIXON.NS', 'BULLISH'), ('VEDL.NS', 'BULLISH'), ('TATAELXSI.NS', 'BULLISH')],
    # ── Commodities (deep supply chain) ──
    'steel prices rise': [('TATASTEEL.NS', 'BULLISH'), ('JSWSTEEL.NS', 'BULLISH'), ('MARUTI.NS', 'BEARISH'), ('LT.NS', 'BEARISH')],
    'steel prices fall': [('TATASTEEL.NS', 'BEARISH'), ('JSWSTEEL.NS', 'BEARISH'), ('MARUTI.NS', 'BULLISH'), ('LT.NS', 'BULLISH')],
    'aluminium prices': [('HINDALCO.NS', 'BULLISH'), ('VEDL.NS', 'BULLISH')],
    'copper prices rise': [('HINDALCO.NS', 'BULLISH'), ('VEDL.NS', 'BULLISH')],
    'lithium shortage': [('TMPV.NS', 'BEARISH'), ('M&M.NS', 'BEARISH')],
    'lithium prices fall': [('TMPV.NS', 'BULLISH'), ('M&M.NS', 'BULLISH')],
    'coal prices rise': [('COALINDIA.NS', 'BULLISH'), ('NTPC.NS', 'BEARISH'), ('JSWSTEEL.NS', 'BEARISH')],
    'natural gas prices': [('IGL.NS', 'BEARISH'), ('MGL.NS', 'BEARISH'), ('GAIL.NS', 'BULLISH')],
    'gold surges': [('MUTHOOTFIN.NS', 'BULLISH'), ('MANAPPURAM.NS', 'BULLISH'), ('TITAN.NS', 'BEARISH')],
    'gold rises': [('MUTHOOTFIN.NS', 'BULLISH'), ('MANAPPURAM.NS', 'BULLISH')],
    'gold falls': [('MUTHOOTFIN.NS', 'BEARISH'), ('MANAPPURAM.NS', 'BEARISH'), ('TITAN.NS', 'BULLISH')],
    # ── Sector Deep / Government Policy ──
    'defense budget': [('HAL.NS', 'BULLISH'), ('BEL.NS', 'BULLISH'), ('BHARATFORG.NS', 'BULLISH')],
    'defence budget': [('HAL.NS', 'BULLISH'), ('BEL.NS', 'BULLISH'), ('BHARATFORG.NS', 'BULLISH')],
    'defense order': [('HAL.NS', 'BULLISH'), ('BEL.NS', 'BULLISH')],
    'railway budget': [('RVNL.NS', 'BULLISH'), ('IRFC.NS', 'BULLISH'), ('IRCTC.NS', 'BULLISH')],
    'infrastructure spending': [('LT.NS', 'BULLISH'), ('RVNL.NS', 'BULLISH'), ('NTPC.NS', 'BULLISH')],
    'inflation rise': [('HDFCBANK.NS', 'BEARISH'), ('DLF.NS', 'BEARISH'), ('RELIANCE.NS', 'BEARISH')],
    'gdp growth': [('HDFCBANK.NS', 'BULLISH'), ('RELIANCE.NS', 'BULLISH'), ('LT.NS', 'BULLISH')],
    'monsoon forecast': [('UPL.NS', 'BULLISH'), ('PIDILITIND.NS', 'BULLISH'), ('DABUR.NS', 'BULLISH')],
    'drought': [('UPL.NS', 'BEARISH'), ('DABUR.NS', 'BEARISH'), ('ITC.NS', 'BEARISH')],
    'ev policy': [('TMPV.NS', 'BULLISH'), ('M&M.NS', 'BULLISH'), ('MARUTI.NS', 'BEARISH')],
    'electric vehicle': [('TMPV.NS', 'BULLISH'), ('M&M.NS', 'BULLISH')],
    'renewable energy': [('ADANIGREEN.NS', 'BULLISH'), ('TATAPOWER.NS', 'BULLISH'), ('NTPC.NS', 'BULLISH')],
    'solar tariff': [('ADANIGREEN.NS', 'BULLISH'), ('TATAPOWER.NS', 'BULLISH')],
    'upi transaction': [('PAYTM.NS', 'BULLISH'), ('SBICARD.NS', 'BULLISH')],
    'digital payment': [('PAYTM.NS', 'BULLISH'), ('SBICARD.NS', 'BULLISH')],
    'pharma sector rally': [('SUNPHARMA.NS', 'BULLISH'), ('CIPLA.NS', 'BULLISH'), ('DRREDDY.NS', 'BULLISH')],
    'fda approval': [('SUNPHARMA.NS', 'BULLISH'), ('CIPLA.NS', 'BULLISH'), ('DRREDDY.NS', 'BULLISH')],
    'fda warning': [('SUNPHARMA.NS', 'BEARISH'), ('CIPLA.NS', 'BEARISH'), ('DRREDDY.NS', 'BEARISH')],
    'it sector rally': [('INFY.NS', 'BULLISH'), ('TCS.NS', 'BULLISH'), ('WIPRO.NS', 'BULLISH')],
    'banking sector': [('HDFCBANK.NS', 'BULLISH'), ('ICICIBANK.NS', 'BULLISH'), ('SBIN.NS', 'BULLISH')],
    'auto sector': [('MARUTI.NS', 'BULLISH'), ('TMPV.NS', 'BULLISH'), ('M&M.NS', 'BULLISH')],
    'realty stocks': [('DLF.NS', 'BULLISH'), ('LODHA.NS', 'BULLISH'), ('OBEROIRLTY.NS', 'BULLISH')],
    'metal stocks': [('TATASTEEL.NS', 'BULLISH'), ('JSWSTEEL.NS', 'BULLISH'), ('HINDALCO.NS', 'BULLISH')],
}

MATERIAL_EVENT_KEYWORDS = [
    'earnings', 'result', 'results', 'profit', 'loss', 'revenue', 'margin',
    'order win', 'wins order', 'contract', 'deal', 'merger', 'acquisition',
    'stake sale', 'block deal', 'bulk deal', 'buyback', 'dividend', 'split',
    'bonus', 'ipo', 'listing', 'approval', 'ban', 'penalty', 'fine', 'fraud',
    'default', 'downgrade', 'upgrade', 'guidance', 'capex', 'expansion',
    'plant', 'shutdown', 'launch', 'tariff', 'rbi', 'repo rate', 'budget',
    'policy', 'export', 'import', 'crude', 'rupee', 'fii', 'fpi',
    # Geopolitical / Supply Chain / Macro (hidden chain triggers)
    'semiconductor', 'chip', 'sanction', 'embargo', 'trade war', 'tariff war',
    'fed rate', 'federal reserve', 'ecb', 'bank of japan', 'boj',
    'china', 'japan', 'taiwan', 'russia', 'ukraine', 'iran', 'middle east',
    'opec', 'recession', 'slowdown', 'stimulus', 'dumping', 'anti-dumping',
    'supply chain', 'shortage', 'disruption', 'blockade', 'strike',
    'inflation', 'deflation', 'gdp', 'current account', 'fiscal deficit',
    'monsoon', 'drought', 'flood', 'climate',
    'lithium', 'cobalt', 'rare earth', 'copper', 'aluminium', 'steel',
    'natural gas', 'lng', 'coal', 'solar', 'renewable', 'ev ', 'electric vehicle',
    'pli', 'subsidy', 'deregulation', 'privatization', 'disinvestment',
    'fda', 'usfda', 'dcgi', 'who', 'pandemic', 'epidemic',
    'defence order', 'defense order', 'arms deal', 'military',
    'digital payment', 'upi', 'fintech', 'cryptocurrency', 'bitcoin',
    'promoter', 'insider', 'pledge', 'rating', 'moody', 'fitch', 's&p',
]

LOW_SIGNAL_PHRASES = [
    'analyst says', 'price target', 'target price', 'stocks to buy',
    'should you buy', 'what should investors do', 'technical breakout',
    'watch today', 'market live', 'sensex today', 'nifty today',
]

INDEX_LIKE_SYMBOLS = {
    'NIFTY', 'NIFTY50', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY',
    'SENSEX', 'NSEI', 'BSESN', 'NSE', 'BSE',
}

COMMON_UPPERCASE_WORDS = {
    'RBI', 'SEBI', 'FII', 'FIIS', 'FPI', 'FPIS', 'DII', 'DIIS', 'IPO',
    'CEO', 'CFO', 'MD', 'QIP', 'GDP', 'GST', 'EV', 'AI', 'IT', 'US',
    'UK', 'EU', 'Q1', 'Q2', 'Q3', 'Q4', 'PAT', 'EBITDA', 'NPA',
}
