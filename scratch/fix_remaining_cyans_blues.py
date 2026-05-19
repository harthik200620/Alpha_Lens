html_path = 'frontend/index.html'
content = open(html_path, 'r', encoding='utf-8').read()

replacements = [
    # Business general filter button background color
    ('border border-violet-500/20 text-violet-400 bg-cyan-900/10 hover:bg-cyan-900/30 transition-all">💼',
     'border border-violet-500/20 text-violet-400 bg-violet-900/10 hover:bg-violet-900/30 transition-all">💼'),
    
    # Portfolio assistant user message color classes
    ("? 'ml-8 bg-cyan-500/10 border-violet-500/20 text-cyan-50'",
     "? 'ml-8 bg-violet-500/10 border-violet-500/20 text-violet-50'"),
     
    # Double check if any other text-cyan-400 pb-1 remains
    ("class=\"text-violet-400 border-b-2 border-cyan-400 pb-1 transition\"",
     "class=\"text-violet-400 border-b-2 border-violet-500 pb-1 transition\""),
     
    # Welcome orb background and orb glow in landing screen
    ('bg-gradient-to-tr from-blue-500 to-cyan-300 shadow-[0_0_15px_#06b6d4]',
     'bg-gradient-to-tr from-violet-600 to-indigo-400 shadow-[0_0_15px_rgba(124,58,237,0.3)]'),
    ('shadow-[0_0_15px_#06b6d4]', 'shadow-[0_0_15px_rgba(124,58,237,0.3)]'),
    ('shadow-[0_0_50px_#06b6d4]', 'shadow-[0_0_50px_rgba(124,58,237,0.3)]'),
    ('from-blue-500 to-cyan-300', 'from-violet-600 to-indigo-400'),
    ('bg-cyan-700/10', 'bg-violet-700/5'),
    ('border-cyan-400', 'border-violet-500/20')
]

modified = content
count = 0
for old, new in replacements:
    if old in modified:
        modified = modified.replace(old, new)
        count += 1
        print(f"Replaced: {old[:50]} -> {new[:50]}")
    else:
        print(f"Not found: {old[:50]}")

if count > 0:
    open(html_path, 'w', encoding='utf-8').write(modified)
    print("Successfully updated remaining cyans/blues")
else:
    print("No changes made.")
