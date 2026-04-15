import sys
sys.stdout.reconfigure(encoding='utf-8')

with open(r'c:\Project rohan\Alpha_Lens\app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Lines 848-926 (0-indexed: 847-925) are the broken block
# Line 848 starts with: '                base_price = 0.0\n'  (the declare before try)
# Line 847: '                current_price_now = 0.0\n'
# Line 926: '                \n'  (trailing space after except block)

# We want to replace lines 847..925 (0-indexed) = lines 848..926 (1-indexed)
start_0 = 847  # 0-indexed line 848 (base_price = 0.0 / current_price_now = 0.0)
end_0 = 926    # 0-indexed line 927 (# Get current price for comparison) - exclusive

# Verify
print("Lines we're replacing:")
for i in range(start_0, min(start_0+5, end_0)):
    print(f"  {i+1}: {repr(lines[i])}")
print("...")
for i in range(max(end_0-3, start_0), end_0):
    print(f"  {i+1}: {repr(lines[i])}")

replacement = '''\
                base_price = 0.0
                current_price_now = 0.0
                try:
                    _ist = timezone(timedelta(hours=5, minutes=30))
                    _pub_dt = parsedate_to_datetime(article['time']).astimezone(_ist)

                    _is_trading = True
                    if _pub_dt.weekday() >= 5 or (_pub_dt.month, _pub_dt.day) in NSE_HOLIDAYS_2026:
                        _is_trading = False
                    else:
                        _t = _pub_dt.hour * 60 + _pub_dt.minute
                        if not ((9 * 60 + 15) <= _t <= (15 * 60 + 30)):
                            _is_trading = False

                    if _is_trading:
                        # Bulletproof price fetcher — correctly handles yfinance MultiIndex columns
                        base_price = get_base_price_at_time(ticker, _pub_dt)
                    else:
                        # Off-hours news: use previous_close as base
                        try:
                            _fi = yf.Ticker(ticker).fast_info
                            _pc = _extract_scalar(_fi.previous_close)
                            base_price = round(_pc, 2) if _pc and _pc > 0 else 0.0
                        except Exception:
                            base_price = 0.0

                except Exception as _e:
                    print(f"   [!] Price fetch error for {ticker}: {_e}")
                    base_price = 0.0

                # Get current price for comparison
'''

replacement_lines = [line + '\n' for line in replacement.split('\n')]
# Fix: replacement already has newlines embedded — use splitlines(True)
replacement_lines = replacement.splitlines(True)

lines[start_0:end_0] = replacement_lines

with open(r'c:\Project rohan\Alpha_Lens\app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print(f"\nSUCCESS: Replaced lines 848-926 with clean price fetcher call")
print(f"New file line count: {len(lines)}")
