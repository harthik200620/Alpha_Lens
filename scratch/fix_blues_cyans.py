# Read current HTML
html_path = 'frontend/index.html'
content = open(html_path, 'r', encoding='utf-8').read()

# Replace hardcoded color classes and styles to shift theme from Cyan/Blue to Violet/Obsidian
replacements = [
    # Active navigation tabs (first tab is active on page load)
    ('class="text-cyan-400 border-b-2 border-cyan-400 pb-1 transition"',
     'class="text-violet-400 border-b-2 border-violet-500 pb-1 transition"'),
     
    # Malformed classes from previous replacements
    ('hover:border-violet-500/20/40', 'hover:border-violet-500/40'),
    ('border-violet-500/20/30', 'border-violet-500/20'),
    ('border-violet-500/20/20', 'border-violet-500/20'),
    ('border-violet-500/20/30 text-cyan-200', 'border-violet-500/20 text-violet-200'),
    
    # Portfolio helper buttons
    ('hover:text-cyan-200', 'hover:text-violet-200'),
    
    # Chat helper input/send button area
    ('bg-cyan-500/10 border-violet-500/20/20 text-cyan-50', 'bg-violet-500/10 border-violet-500/20 text-violet-50'),
    ('bg-cyan-500/15 border-violet-500/20/30 text-cyan-200 hover:bg-cyan-500/25',
     'bg-violet-500/15 border-violet-500/20 text-violet-200 hover:bg-violet-500/25'),
    ('bg-cyan-500/10 border border-violet-500/20 flex items-center justify-center',
     'bg-violet-500/10 border border-violet-500/20 flex items-center justify-center'),
     
    # Chat input button cyans
    ('bg-cyan-500/15 border', 'bg-violet-500/15 border'),
    ('text-cyan-200 hover:bg-cyan-500/25', 'text-violet-200 hover:bg-violet-500/25'),
    ('ml-8 bg-cyan-500/10 border-violet-500/20/20 text-cyan-50',
     'ml-8 bg-violet-500/10 border-violet-500/20 text-violet-50'),
     
    # BG Glow blobs
    ('bg-blue-600/10 rounded-full blur-[100px]', 'bg-violet-600/10 rounded-full blur-[100px]'),
    ('bg-blue-600/5 rounded-full blur-[100px]', 'bg-violet-600/5 rounded-full blur-[100px]'),
    
    # Hero/Text blocks
    ('bg-gradient-to-br from-blue-900/20 to-black/20 border border-blue-500/20 p-5 rounded-2xl backdrop-blur-md shadow-md hover:border-blue-500/30 transition duration-300 w-full flex-1',
     'bg-gradient-to-br from-violet-950/10 to-black/20 border border-violet-500/20 p-5 rounded-2xl backdrop-blur-md shadow-md hover:border-violet-500/30 transition duration-300 w-full flex-1'),
    ('text-blue-100', 'text-violet-100'),
    
    # Toggle switch active states
    ("bg.classList.replace('bg-slate-800', 'bg-blue-500');",
     "bg.classList.replace('bg-slate-800', 'bg-violet-500');"),
    ("bg.classList.replace('bg-blue-500', 'bg-slate-800');",
     "bg.classList.replace('bg-violet-500', 'bg-slate-800');"),
     
    # GSAP Welcome / Auth Orb Animation Cyan colors
    ("statusEl.style.color = '#06b6d4';", "statusEl.style.color = '#a78bfa';"),
    ("borderColor: '#06b6d4'", "borderColor: '#a78bfa'"),
    ("color: '#06b6d4'", "color: '#a78bfa'"),
    ("background: '#06b6d4'", "background: '#a78bfa'"),
    ('boxShadow: "0 0 15px #06b6d4"', 'boxShadow: "0 0 15px rgba(167,139,250,0.5)"'),
    ('boxShadow: "0 0 50px 20px rgba(6,182,212,0.8)"', 'boxShadow: "0 0 50px 20px rgba(167,139,250,0.5)"'),
    ('boxShadow: "0 0 30px rgba(6,182,212,0.6)"', 'boxShadow: "0 0 30px rgba(167,139,250,0.4)"')
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
    print("Successfully updated frontend/index.html with color fixes")
else:
    print("No changes made.")
