import re

# Read current HTML
html_path = 'frontend/index.html'
content = open(html_path, 'r', encoding='utf-8').read()

# Replace hardcoded color classes and styles to shift theme from Cyan/Blue to Violet/Obsidian
replacements = [
    # Main Navigation Tabs & Header
    ('from-blue-600 via-blue-400 to-cyan-300', 'from-violet-500 via-violet-400 to-indigo-300'),
    ('from-blue-500 to-cyan-300', 'from-violet-500 to-indigo-400'),
    ('from-blue-600 to-cyan-400', 'from-violet-600 to-indigo-400'),
    ('shadow-[0_0_15px_#06b6d4]', 'shadow-[0_0_15px_rgba(124,58,237,0.3)]'),
    ('shadow-[0_0_50px_#06b6d4]', 'shadow-[0_0_50px_rgba(124,58,237,0.3)]'),
    ('border-cyan-400', 'border-violet-500/20'),
    ('bg-cyan-400', 'bg-violet-500'),
    ('border-cyan-500/30', 'border-violet-500/20'),
    ('border-cyan-500', 'border-violet-500'),
    ('bg-cyan-900/20', 'bg-violet-950/20'),
    ('text-cyan-400', 'text-violet-400'),
    ('bg-cyan-500/30', 'bg-violet-500/20'),
    ('bg-cyan-600', 'bg-violet-600'),
    ('text-cyan-300', 'text-violet-300'),
    ('border-cyan-500/20', 'border-violet-500/20'),
    ('hover:border-blue-500/50', 'hover:border-violet-500/30'),
    ('bg-[#030712]', 'bg-[#080808]'),
    ('bg-cyan-700/10', 'bg-violet-700/5'),
    ('border-white/10', 'border-white/5'),
    
    # Active Navigation State
    ('text-cyan-400 border-b-2 border-cyan-400 pb-1 transition', 'text-violet-400 border-b-2 border-violet-500 pb-1 transition'),
    ('nav.className = `text-cyan-400 border-b-2 border-cyan-400 pb-1 transition${stockCls}`', 'nav.className = `text-violet-400 border-b-2 border-violet-500 pb-1 transition${stockCls}`'),
    
    # Welcome orb glow ring
    ('border border-cyan-400 opacity-20', 'border border-violet-500/30 opacity-20'),
    
    # Change viewport size of tab wrapper to flex instead of hidden xl:flex
    ('hidden xl:flex space-x-6 items-center text-[11px] font-bold tracking-widest uppercase', 'hidden md:flex space-x-6 items-center text-[11px] font-bold tracking-widest uppercase'),
    
    # Fix white-on-white button (Access Terminal)
    ('class="btn-glow px-5 py-1.5 rounded-full text-sm font-display font-bold text-white tracking-wide shadow-lg hover:scale-105 transition-transform"',
     'class="btn-glow px-5 py-1.5 rounded-full text-sm font-display font-bold text-black tracking-wide shadow-lg hover:scale-105 transition-transform"'),
     
    # Indices accent colors in JS
    ("const accents = ['#06b6d4', '#8b5cf6', '#f59e0b', '#10b981'];",
     "const accents = ['#7c3aed', '#a78bfa', '#ff9f0a', '#00d26a'];")
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
    print("Successfully updated frontend/index.html")
else:
    print("No changes made.")
