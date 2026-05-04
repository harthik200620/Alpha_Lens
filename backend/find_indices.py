content = open('app.py', 'r', encoding='utf-8').read()
lines = content.split('\n')
for i, l in enumerate(lines):
    if any(k in l for k in ['indices', 'change_pct', '^NSEI', '^BSESN', '^NSEBANK', 'INDICES', '/api/ind']):
        print(f"{i+1}: {l}")
