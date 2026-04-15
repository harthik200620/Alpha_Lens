import sys
sys.stdout.reconfigure(encoding='utf-8')
with open(r'c:\Project rohan\Alpha_Lens\app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = None
for i, line in enumerate(lines):
    if "_hist = yf.download(ticker, period='7d', interval='1m', progress=False)" in line:
        start_idx = i
        break

if start_idx is None:
    print('Could not find target line')
else:
    print(f'Found at line {start_idx+1}')
    replacement = [
        "                        _hist = yf.download(ticker, period='7d', interval='1m', progress=False, auto_adjust=True)\r\n",
        "                        if not _hist.empty:\r\n",
        "                            _hist.index = pd.to_datetime(_hist.index).tz_convert(_ist)\r\n",
        "                            # Find closest 1-min bar within 30-min window before pub time\r\n",
        "                            _window_start = _pub_dt - timedelta(minutes=30)\r\n",
        "                            _window_ticks = _hist[(_hist.index >= _window_start) & (_hist.index <= _pub_dt)]\r\n",
        "                            if not _window_ticks.empty:\r\n",
        "                                _cv = _window_ticks.iloc[-1]['Close']\r\n",
        "                                if hasattr(_cv, 'iloc'): _cv = _cv.iloc[0]\r\n",
        "                                base_price = round(float(_cv), 2)\r\n",
        "                            else:\r\n",
        "                                _past_ticks = _hist[_hist.index <= _pub_dt]\r\n",
        "                                if not _past_ticks.empty:\r\n",
        "                                    _cv = _past_ticks.iloc[-1]['Close']\r\n",
        "                                    if hasattr(_cv, 'iloc'): _cv = _cv.iloc[0]\r\n",
        "                                    base_price = round(float(_cv), 2)\r\n",
        "                                 \r\n",
    ]
    old_block_len = 9
    lines[start_idx:start_idx+old_block_len] = replacement

    with open(r'c:\Project rohan\Alpha_Lens\app.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f'SUCCESS: Wrote {len(lines)} lines')
