import pandas as pd
import requests
import logging
from datetime import datetime

# Disable logging spam matching yfinance
logger = logging.getLogger('yfinance_twelvedata_shim')
logger.disabled = True
logger.propagate = False

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "0f6aebeb95e14a479c240f839b0cefe8")

def set_tz_cache_location(*args, **kwargs):
    # Mock method to allow app.py compatibility
    pass

class FastInfo:
    def __init__(self, last_price, previous_close, day_high, day_low):
        self.last_price = last_price
        self.previous_close = previous_close
        self.day_high = day_high
        self.day_low = day_low

def _twelve_to_yf_ticker(ticker):
    """
    Twelve Data requires exchanges via appending or specifying in params.
    NIFTY 50 -> NIFTY 50:NSE
    RELIANCE.NS -> RELIANCE:NSE
    """
    if isinstance(ticker, str):
        if ticker.endswith(".NS"):
            return f"{ticker[:-3]}:NSE"
        elif ticker.endswith(".BO"):
            return f"{ticker[:-3]}:BSE"
        # Indices
        if ticker == "^NSEI":
            return "NIFTY 50:NSE"
        elif ticker == "^BSESN":
            return "SENSEX:BSE"
        elif ticker == "^NSEBANK":
            return "NIFTY BANK:NSE"
        elif ticker == "^NSMIDCP":
            return "NIFTY MIDCAP 50:NSE"
    return ticker

class Ticker:
    def __init__(self, ticker):
        self.ticker = ticker
        self.td_ticker = _twelve_to_yf_ticker(ticker)
        self._quote = None

    def _fetch_quote(self):
        try:
            # For Twelve Data, quote provides Open, High, Low, Close, Previous_Close
            url = f"https://api.twelvedata.com/quote?symbol={self.td_ticker}&apikey={TWELVE_DATA_API_KEY}"
            resp = requests.get(url, timeout=5)
            self._quote = resp.json()
        except:
            self._quote = {"status": "error"}

    @property
    def fast_info(self):
        if not self._quote:
            self._fetch_quote()
            
        if self._quote.get('status') == 'error' or 'close' not in self._quote:
            return FastInfo(0.0, 0.0, 0.0, 0.0)
            
        try:
            lp = float(self._quote.get('close', 0.0))
            pc = float(self._quote.get('previous_close', 0.0))
            dh = float(self._quote.get('high', 0.0))
            dl = float(self._quote.get('low', 0.0))
            return FastInfo(lp, pc, dh, dl)
        except Exception:
            return FastInfo(0.0, 0.0, 0.0, 0.0)

    def history(self, period="2d", interval="1d"):
        # Map yfinance period strings to twelve data outputsize
        if period.endswith('d'):
            try:
                days = int(period[:-1])
                out_size = max(1, days) * 2 # Safety buffer for closed days
            except:
                out_size = 5
        elif 'mo' in period:
            out_size = 60 # Arbitrary fallback
        else:
            out_size = 20

        # Twelve Data requires "1min", "1day" etc instead of "1m", "1d"
        td_interval = interval
        if td_interval == "1d":
            td_interval = "1day"
        elif td_interval == "1m":
            td_interval = "1min"
        elif td_interval.endswith("m"):
            td_interval = td_interval + "in"
            
        try:
            url = f"https://api.twelvedata.com/time_series?symbol={self.td_ticker}&interval={td_interval}&outputsize={out_size}&apikey={TWELVE_DATA_API_KEY}"
            resp = requests.get(url, timeout=10).json()
            
            if resp.get('status') == 'error':
                return pd.DataFrame()
                
            values = resp.get('values', [])
            if not values:
                return pd.DataFrame()
                
            values.reverse() # Chronological oldest to newest
            df = pd.DataFrame(values)
            df['datetime'] = pd.to_datetime(df['datetime'])
            df.set_index('datetime', inplace=True)
            df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                if col in df.columns:
                    df[col] = df[col].astype(float)
            return df
        except Exception as e:
            return pd.DataFrame()

def download(tickers, period="7d", interval="1m", progress=False, auto_adjust=True):
    # Twelve Data logic for global download
    # If passed as string, handle as single array
    is_str = isinstance(tickers, str)
    ticker_list = [tickers] if is_str else tickers
    
    td_interval = interval
    if td_interval == "1d":
        td_interval = "1day"
    elif td_interval == "1m":
        td_interval = "1min"
    elif td_interval.endswith("m"):
        td_interval = td_interval + "in"
        
    try:
        # Assuming single ticker download since backend only passes one at a time for technical_analysis.
        td_ticker = _twelve_to_yf_ticker(ticker_list[0])
        url = f"https://api.twelvedata.com/time_series?symbol={td_ticker}&interval={td_interval}&outputsize=2500&apikey={TWELVE_DATA_API_KEY}"
        resp = requests.get(url, timeout=10).json()
        
        if resp.get('status') == 'error':
            return pd.DataFrame()
        
        values = resp.get('values', [])
        if not values:
            return pd.DataFrame()
            
        values.reverse()
        df = pd.DataFrame(values)
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
        df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if col in df.columns:
                df[col] = df[col].astype(float)
        
        return df
    except Exception as e:
        return pd.DataFrame()

# Fallback wrapper definitions
Ticker.FastInfo = FastInfo
