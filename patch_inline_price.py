import sys
sys.stdout.reconfigure(encoding='utf-8')

with open(r'c:\Project rohan\Alpha_Lens\app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# The old messy inline block (all the _ist, _pub_dt, _is_trading, _hist stuff)
# We'll find it by searching for the start and end markers
START_MARKER = "                base_price = 0.0\r\n                current_price_now = 0.0\r\n                try:\r\n                    _ist = timezone(timedelta(hours=5, minutes=30))\r\n                    _pub_dt = parsedate_to_datetime(article['time']).astimezone(_ist)"
END_MARKER = "                # Fallback: if intraday lookup failed, use current price (0.00% start)"

start_idx = content.find(START_MARKER)
end_idx = content.find(END_MARKER)

if start_idx == -1 or end_idx == -1:
    print(f"START found: {start_idx != -1}")
    print(f"END found: {end_idx != -1}")
    print("Could not find the block — aborting.")
else:
    # The replacement: clean, single-function call
    new_block = """                base_price = 0.0
                current_price_now = 0.0
                try:
                    _ist = timezone(timedelta(hours=5, minutes=30))
                    _pub_dt = parsedate_to_datetime(article['time']).astimezone(_ist)

                    # Determine if it was a trading day/time
                    _is_trading = True
                    if _pub_dt.weekday() >= 5 or (_pub_dt.month, _pub_dt.day) in NSE_HOLIDAYS_2026:
                        _is_trading = False
                    else:
                        _t = _pub_dt.hour * 60 + _pub_dt.minute
                        if not ((9 * 60 + 15) <= _t <= (15 * 60 + 30)):
                            _is_trading = False

                    if _is_trading:
                        # Use the bulletproof price fetcher — correctly handles
                        # yfinance MultiIndex columns and never falls to stale daily close
                        base_price = get_base_price_at_time(ticker, _pub_dt)
                    else:
                        # Off-hours news: use previous_close from fast_info as base
                        try:
                            _fi = yf.Ticker(ticker).fast_info
                            _pc = _extract_scalar(_fi.previous_close)
                            base_price = round(_pc, 2) if _pc and _pc > 0 else 0.0
                        except Exception:
                            base_price = 0.0

                except Exception as _e:
                    print(f"   [!] Price fetch error for {ticker}: {_e}")
                    base_price = 0.0

                """

    # Find the exact end of the old block (end of the except clause before "# Fallback")
    # We need to look for the try/except block that starts at START_MARKER and ends just before END_MARKER
    # More precisely: the except clause ends, then there's a blank line, then END_MARKER
    # Let's find the except block end
    old_block = content[start_idx:end_idx]
    # print the last 300 chars of old block to understand the boundary
    print("=== Last part of old block ===")
    print(repr(old_block[-400:]))
    print("=== New block ===")
    print(new_block)
    print(f"\nOld block length: {len(old_block)}")
    
    new_content = content[:start_idx] + new_block + content[end_idx:]
    with open(r'c:\Project rohan\Alpha_Lens\app.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("SUCCESS: File updated")
