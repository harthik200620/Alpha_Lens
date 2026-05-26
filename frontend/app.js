/* Alpha Lens — extracted from index.html (was inline at end of body)
   Cached separately. Loaded via <script src=...> with defer.
   Runs after the DOM is parsed (same as inline-at-end-of-body). */
        // --- GLOBAL DATA & VARIABLES ---
        let globalNewsData = [];
        let isNonStockMode = false;
        let currentUser = null;
        let currentArchiveFilter = 'all';
        let selectedHeadlineKey = '';
        let marketOpen = true;  // Updated from API — controls whether stock changes are shown
        const tabs = ['top-news', 'all-news', 'portfolio', 'stocks', 'terminal'];

        function getNewsKey(newsItem) {
            return (newsItem?.headline || '').trim().toLowerCase();
        }

        function setArchiveFilter(filter) {
            currentArchiveFilter = filter;
            document.querySelectorAll('.archive-filter-btn').forEach(btn => btn.classList.remove('active-filter'));
            const activeBtn = document.getElementById('filter-' + filter);
            if (activeBtn) activeBtn.classList.add('active-filter');
            renderArchiveView();
        }

        // Failsafe Date Parser — handles all browsers (returns epoch ms)
        function parseCustomDate(dtStr) {
            if (!dtStr) return 0;
            let t = new Date(dtStr).getTime();
            if (!isNaN(t)) return t;
            t = new Date(dtStr.replace(" ", "T")).getTime(); // Safari fix for SQL dates
            if (!isNaN(t)) return t;
            return 0;
        }

        // ==========================================
        // OFFICIAL GOOGLE IDENTITY LOGIC
        // ==========================================
        const GOOGLE_CLIENT_ID = "691809546767-2c9tmjs7lt5ratcjugt97hohb2kv86i5.apps.googleusercontent.com";

        function initializeGoogleAuth() {
            try {
                google.accounts.id.initialize({
                    client_id: GOOGLE_CLIENT_ID,
                    callback: handleGoogleResponse
                });
                google.accounts.id.renderButton(
                    document.getElementById("google-btn-container"),
                    { theme: "outline", size: "large", shape: "rectangular", width: "320" }
                );
            } catch (e) {
                console.log("Google Auth Script not loaded yet. Make sure you are connected to the internet.");
            }
        }

        async function handleGoogleResponse(response) {
            if (!response.credential) {
                alert("Google did not return a credential.");
                return;
            }
            try {
                const res = await fetch('/api/oauth-signin', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ credential: response.credential })
                });
                const data = await res.json();
                if (res.ok) {
                    currentUser = data.user;
                    closeAuthModal();
                    updateAuthUI();
                    triggerSexyWelcome();
                } else { alert("Google Auth Failed on Server: " + data.error); }
            } catch (err) { alert("Server error during Google Auth"); }
        }

        // GSAP "ACCESS GRANTED" TERMINAL UNLOCK ANIMATION
        function triggerSexyWelcome() {
            const existing = document.getElementById('gsap-auth-overlay');
            if (existing) existing.remove();

            const overlay = document.createElement('div');
            overlay.id = 'gsap-auth-overlay';
            overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;overflow:hidden;background:transparent;pointer-events:none;display:flex;align-items:center;justify-content:center;';

            overlay.innerHTML = `
                <div id="auth-bg-top" style="position:absolute;top:0;left:0;width:100%;height:50%;background:#030712;border-bottom:2px solid #a78bfa;transform-origin:top;"></div>
                <div id="auth-bg-bottom" style="position:absolute;bottom:0;left:0;width:100%;height:50%;background:#030712;border-top:2px solid #a78bfa;transform-origin:bottom;"></div>
                
                <div id="auth-glow" style="position:absolute;width:100%;height:4px;background:#a78bfa;box-shadow:0 0 50px 20px rgba(167,139,250,0.55);opacity:0;transform:scaleY(0);"></div>
                
                <div id="auth-text-container" style="position:relative;z-index:10;display:flex;flex-direction:column;align-items:center;">
                    <div id="auth-lock" style="width:48px;height:48px;border:2px solid #a78bfa;border-radius:50%;display:flex;align-items:center;justify-content:center;margin-bottom:20px;opacity:0;transform:scale(0.5);box-shadow:0 0 24px rgba(167,139,250,0.45);">
                        <svg id="auth-icon-svg" style="width:24px;height:24px;color:#a78bfa;" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"></path></svg>
                    </div>
                    <div id="auth-status" style="font-family:'Space Grotesk',sans-serif;font-size:24px;font-weight:900;color:#a78bfa;letter-spacing:0.1em;text-transform:uppercase;opacity:0;">
                        Authenticating...
                    </div>
                    <div style="width:100%;height:2px;background:rgba(167,139,250,0.18);margin-top:15px;position:relative;overflow:hidden;border-radius:2px;">
                        <div id="auth-bar" style="position:absolute;left:0;top:0;height:100%;width:0%;background:#a78bfa;box-shadow:0 0 10px #a78bfa;"></div>
                    </div>
                </div>
            `;
            document.body.appendChild(overlay);

            const tl = gsap.timeline({
                onComplete: () => {
                    if (overlay.parentNode) overlay.remove();
                }
            });

            tl.to("#auth-lock", { opacity: 1, scale: 1, duration: 0.5, ease: "back.out(1.7)" })
                .to("#auth-status", { opacity: 1, duration: 0.3 }, "-=0.2")
                .to("#auth-bar", { width: "100%", duration: 1.2, ease: "power2.inOut" })
                .add(() => {
                    const statusEl = document.getElementById('auth-status');
                    statusEl.style.color = '#a78bfa'; // Consistent brand cyan for "WELCOME TO"
                    statusEl.style.fontSize = '20px'; // Adjust for logo fit
                    statusEl.innerHTML = `WELCOME TO <span class="bg-clip-text text-transparent bg-gradient-to-r from-violet-500 via-violet-400 to-indigo-300 ml-2">ALPHA</span><span class="text-white ml-2">LENS</span>`;

                    document.getElementById('auth-icon-svg').innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"></path>';

                    // Switch colors to brand cyan for consistency (removing green)
                    gsap.set(["#auth-lock", "#auth-bg-top", "#auth-bg-bottom"], { borderColor: '#a78bfa' });
                    gsap.set("#auth-icon-svg", { color: '#a78bfa' });
                    gsap.set("#auth-bar", { background: '#C9A962', boxShadow: "0 0 15px rgba(201,169,98,0.5)" });
                    gsap.set("#auth-glow", { background: '#D4BC7A', boxShadow: "0 0 50px 20px rgba(201,169,98,0.45)" });
                    gsap.set("#auth-lock", { boxShadow: "0 0 30px rgba(201,169,98,0.4)" });
                })
                // Scale lock & text up sharply
                .to("#auth-text-container", { scale: 1.1, duration: 0.15, ease: "power2.out" })
                .to("#auth-text-container", { scale: 1, duration: 0.1, ease: "power2.in" })
                // Hold for a moment, then snap it away
                .to("#auth-text-container", { opacity: 0, scale: 0.8, duration: 0.2, ease: "power3.in", delay: 0.4 })
                // Horizontal scanline burst
                .to("#auth-glow", { opacity: 1, scaleY: 1, duration: 0.1 })
                .to("#auth-glow", { scaleY: 30, opacity: 0, duration: 0.4, ease: "expo.out" }, "+=0.05")
                // Blast doors slide open to reveal dashboard
                .to("#auth-bg-top", { y: "-100%", duration: 0.8, ease: "power4.inOut" }, "-=0.4")
                .to("#auth-bg-bottom", { y: "100%", duration: 0.8, ease: "power4.inOut" }, "-=0.8");
        }


        // ==========================================
        // PASSWORDLESS SENDGRID OTP LOGIC
        // ==========================================

        async function checkAuthStatus() {
            try {
                const response = await fetch('/api/me');
                const data = await response.json();
                if (data.user) {
                    currentUser = data.user;
                    updateAuthUI();
                }
            } catch (e) { console.error("Error checking auth status", e); }
        }

        function openAuthModal() {
            document.getElementById('auth-error').classList.add('hidden');
            showView('options');
            const modal = document.getElementById('auth-modal');
            const content = document.getElementById('auth-modal-content');
            modal.classList.remove('hidden');
            void modal.offsetWidth;
            modal.classList.remove('opacity-0');
            content.classList.remove('scale-95');
            setTimeout(initializeGoogleAuth, 100);
        }

        function showView(viewType) {
            document.getElementById('auth-signup-options').classList.add('hidden');
            document.getElementById('auth-email-form-container').classList.add('hidden');
            if (viewType === 'options') document.getElementById('auth-signup-options').classList.remove('hidden');
            if (viewType === 'email') {
                document.getElementById('auth-email-form-container').classList.remove('hidden');
                document.getElementById('email-step-1').classList.remove('hidden');
                document.getElementById('email-step-2').classList.add('hidden');
                document.getElementById('auth-email').value = '';
                document.getElementById('auth-otp').value = '';
            }
        }

        function closeAuthModal() {
            const modal = document.getElementById('auth-modal');
            const content = document.getElementById('auth-modal-content');
            modal.classList.add('opacity-0');
            content.classList.add('scale-95');
            setTimeout(() => { modal.classList.add('hidden'); }, 300);
        }

        async function requestEmailOTP() {
            const email = document.getElementById('auth-email').value;
            if (!email || !email.includes('@')) { alert("Please enter a valid email."); return; }
            const btn = document.getElementById('send-otp-btn');
            const errorDiv = document.getElementById('auth-error');
            btn.innerText = "SENDING...";
            errorDiv.classList.add('hidden');
            try {
                const response = await fetch('/api/send-otp', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email })
                });
                const data = await response.json();
                if (response.ok) {
                    document.getElementById('email-step-1').classList.add('hidden');
                    document.getElementById('email-step-2').classList.remove('hidden');
                } else {
                    errorDiv.innerText = data.error || "Failed to send OTP.";
                    errorDiv.classList.remove('hidden');
                }
            } catch (err) {
                errorDiv.innerText = "Server error. Is the Flask backend running?";
                errorDiv.classList.remove('hidden');
            }
            btn.innerText = "SEND OTP CODE";
        }

        async function submitEmailOTP() {
            const email = document.getElementById('auth-email').value;
            const otp = document.getElementById('auth-otp').value;
            const btn = document.getElementById('verify-otp-btn');
            const errorDiv = document.getElementById('auth-error');
            btn.innerText = "VERIFYING...";
            errorDiv.classList.add('hidden');
            try {
                const response = await fetch('/api/verify-otp', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, otp })
                });
                const data = await response.json();
                if (response.ok) {
                    currentUser = data.user;
                    closeAuthModal();
                    updateAuthUI();
                    triggerSexyWelcome();
                } else {
                    errorDiv.innerText = data.error || "Invalid Code.";
                    errorDiv.classList.remove('hidden');
                }
            } catch (err) {
                errorDiv.innerText = "Server error.";
                errorDiv.classList.remove('hidden');
            }
            btn.innerText = "VERIFY & SECURE LOGIN";
        }

        function updateAuthUI() {
            const authSection = document.getElementById('auth-section');
            if (currentUser) {
                authSection.innerHTML = `
                    <button onclick="handleLogout()" class="flex items-center gap-2 px-5 py-1.5 rounded-full bg-red-900/40 border border-red-500/50 text-[11px] font-bold text-red-200 hover:bg-red-800/60 hover:text-white uppercase tracking-widest transition-all shadow-lg hover:scale-105">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path></svg>
                        Logout
                    </button>
                `;
            } else {
                authSection.innerHTML = `
                    <button onclick="openAuthModal()" data-magnetic class="btn-glow px-5 py-1.5 rounded-full text-sm font-display font-bold text-black tracking-wide shadow-lg hover:scale-105 transition-transform">Access Terminal</button>
                `;
            }
        }

        async function handleLogout() {
            if (confirm("Are you sure you want to log out of Alpha Lens?")) {
                await fetch('/api/logout', { method: 'POST' });
                currentUser = null;
                updateAuthUI();
            }
        }

        // ==========================================
        // TAB SWITCHING & UI RENDER LOGIC
        // ==========================================

        function toggleNonStockMode() {
            isNonStockMode = !isNonStockMode;
            const knob = document.getElementById('toggleKnob');
            const bg = document.getElementById('toggleBg');
            const ticker = document.getElementById('index-ticker');
            // Collect stock elements EXCLUDING the ticker (handled separately)
            const stockElements = document.querySelectorAll('.stock-table-container, .stock-mode-element:not(#index-ticker)');
            const stockBadges = document.querySelectorAll('.stock-badge-container');
            const topNewsMain = document.getElementById('top-news-main-col');
            if (isNonStockMode) {
                knob.style.transform = 'translateX(16px)';
                knob.classList.replace('bg-slate-400', 'bg-white');
                bg.classList.replace('bg-slate-800', 'bg-violet-500');
                stockElements.forEach(el => el.style.display = 'none');
                stockBadges.forEach(el => el.style.display = 'none');
                if (ticker) ticker.style.display = 'none'; // always hide ticker in non-stock
                if (topNewsMain) {
                    topNewsMain.classList.remove('xl:col-span-8');
                    topNewsMain.classList.add('xl:col-span-12');
                }
                // If user is on a hidden tab, redirect to top-news
                // (terminal is stock-only — signals are by definition ticker-based)
                const hiddenTabs = ['portfolio', 'stocks', 'terminal'];
                const activeView = hiddenTabs.find(t => !document.getElementById(`view-${t}`).classList.contains('hidden'));
                if (activeView) switchTab('top-news');

                // Show general filters instead of stock filters
                const genFilters = document.getElementById('general-filters');
                if (genFilters) genFilters.style.display = 'flex';

                // Reset filter to 'all' when switching mode to avoid weird states
                setArchiveFilter('all');
            } else {
                knob.style.transform = 'translateX(0)';
                knob.classList.replace('bg-white', 'bg-slate-400');
                bg.classList.replace('bg-violet-500', 'bg-slate-800');
                stockElements.forEach(el => el.style.display = '');
                stockBadges.forEach(el => el.style.display = 'flex');
                // Restore ticker ONLY if currently on top-news tab
                const currentTab = tabs.find(t => {
                    const v = document.getElementById(`view-${t}`);
                    return v && !v.classList.contains('hidden');
                });
                if (ticker) ticker.style.display = (currentTab === 'top-news') ? '' : 'none';
                if (topNewsMain) {
                    topNewsMain.classList.add('xl:col-span-8');
                    topNewsMain.classList.remove('xl:col-span-12');
                }

                // Hide general filters
                const genFilters = document.getElementById('general-filters');
                if (genFilters) genFilters.style.display = 'none';

                setArchiveFilter('all');
            }
        }

        // Nav links that must stay hidden in non-stock mode
        // Terminal is stock-only — every row is a ticker signal, so it has
        // zero meaning when the user has toggled off the stock-mode UI.
        const STOCK_NAV_IDS = ['nav-portfolio', 'nav-stocks', 'nav-terminal'];

        function switchTab(targetTabId) {
            tabs.forEach(id => {
                const view = document.getElementById(`view-${id}`);
                const nav = document.getElementById(`nav-${id}`);
                if (view && nav) {
                    const isStockNav = STOCK_NAV_IDS.includes(`nav-${id}`);
                    const stockCls = isStockNav ? ' stock-mode-element' : '';
                    if (id === targetTabId) {
                        view.classList.remove('hidden');
                        nav.className = `text-violet-400 border-b-2 border-violet-500/20 pb-1 transition${stockCls}`;
                    } else {
                        view.classList.add('hidden');
                        nav.className = `nav-link text-slate-300 hover:text-white transition${stockCls}`;
                    }
                }
            });
            // Index cards only belong on the Top News page
            const ticker = document.getElementById('index-ticker');
            if (ticker) {
                ticker.style.display = (targetTabId === 'top-news' && !isNonStockMode) ? '' : 'none';
            }
            // Lazy-load premium views
            if (targetTabId === 'terminal') fetchTerminalData();
            if (targetTabId === 'stocks') fetchBacktestStats();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        // Parse database timestamp format safely across all databases and browsers
        function parseSQLiteDate(str) {
            if (!str) return new Date(0);
            // Try direct parsing first (e.g. for ISO, GMT, RFC 2822)
            let d = new Date(str);
            if (!isNaN(d.getTime())) return d;
            
            // Fallback for standard SQLite "YYYY-MM-DD HH:MM:SS" format
            const iso = str.replace(' ', 'T') + 'Z';
            d = new Date(iso);
            return isNaN(d) ? new Date(0) : d;
        }

        // Always show the REAL publication date from RSS (news_time).
        // NEVER substitute created_at — that would make April 8 news look like it happened today.
        // EXCEPT when news_time is in the future (some RSS publishers, esp.
        // "Stocks to Watch Today" wires, ship a scheduled-publish timestamp
        // that hasn't actually occurred yet — e.g. an article tagged 07:00 IST
        // appearing at 04:00 IST). For those we fall back to created_at (the
        // DB ingestion time) which is guaranteed to be in the past.
        function getNewsDate(newsItem) {
            if (!newsItem) return new Date(0);
            const dbDate = parseSQLiteDate(newsItem.created_at);
            // 5-minute tolerance absorbs minor clock skew between RSS publishers
            // and the user's browser. Anything beyond that is treated as a
            // future-stamped article and clamped to the ingestion timestamp.
            const futureCutoff = Date.now() + (5 * 60 * 1000);
            if (newsItem.news_time && newsItem.news_time !== "Just Now" && newsItem.news_time !== "System Processing") {
                let d = new Date(newsItem.news_time);
                if (isNaN(d.getTime())) {
                    // Strip leading day name (e.g., "Wed, 08 Apr..." -> "08 Apr...")
                    const cleaned = newsItem.news_time.includes(',') ? newsItem.news_time.split(',')[1].trim() : newsItem.news_time;
                    d = new Date(cleaned);
                }
                if (!isNaN(d.getTime())) {
                    if (d.getTime() > futureCutoff) {
                        // Future timestamp from publisher — use ingestion time
                        return dbDate && dbDate.getTime() > 0 ? dbDate : new Date();
                    }
                    return d;
                }
            }
            // Only use created_at as absolute last resort (when news_time is truly missing)
            return dbDate;
        }

        async function fetchLiveNews() {
            try {
                // T1.4: Use the warm-fetch promise from <head> on the first call
                // (saves 100-500ms because the request was already in flight while
                // the rest of HTML/JS was parsing). Subsequent polls hit /api/news/all
                // normally.
                let payload;
                if (window.__alphaWarmFetches && window.__alphaWarmFetches.news) {
                    payload = await window.__alphaWarmFetches.news;
                    window.__alphaWarmFetches.news = null;  // consume once
                }
                if (!payload) {
                    const response = await fetch('/api/news/all?limit=7500&lite=1');
                    payload = await response.json();
                }
                // Handle both old array format and new {market_open, news} format
                let raw;
                if (Array.isArray(payload)) {
                    raw = payload;
                } else {
                    raw = payload.news || [];
                    marketOpen = !!payload.market_open;
                }
                // Deduplicate by headline — keep newest, merge stocks from duplicates
                const map = new Map();
                raw.forEach(item => {
                    const key = item.headline.trim().toLowerCase();
                    if (!map.has(key)) {
                        map.set(key, { ...item, affected_stocks: [...(item.affected_stocks || [])] });
                    } else {
                        // Merge stocks from duplicate into existing entry
                        const existing = map.get(key);
                        const existTickers = new Set(existing.affected_stocks.map(s => s.ticker));
                        (item.affected_stocks || []).forEach(s => {
                            if (!existTickers.has(s.ticker)) {
                                existing.affected_stocks.push(s);
                                existTickers.add(s.ticker);
                            }
                        });
                        // Keep the entry with the latest publication date
                        if (getNewsDate(item) > getNewsDate(existing)) {
                            existing.created_at = item.created_at;
                            existing.news_time = item.news_time;
                        }
                    }
                });
                // Sort latest first (using real publication dates)
                globalNewsData = Array.from(map.values()).sort((a, b) => getNewsDate(b) - getNewsDate(a));
                if (globalNewsData.length === 0) {
                    document.getElementById('dashboard-news-list').innerHTML = '<div class="text-slate-400 text-sm py-4 col-span-3">AI Engine is processing live feeds. Check back shortly.</div>';
                }
                const thLivePrice = document.getElementById('th-live-price');
                if (thLivePrice) {
                    thLivePrice.innerText = 'Current Price / Δ%';
                }
                renderDashboardView();
                renderArchiveView();
                renderMajorStocksView();
                renderPortfolioView();
                updatePortfolioAssistantState();
            } catch (error) { console.error("Failed to fetch news:", error); }
        }

        function renderDashboardView() {
            const container = document.getElementById('dashboard-news-list');
            container.innerHTML = '';
            // Show top 3 news — in non-stock mode still show all news headlines, badges hidden by CSS
            const topNews = globalNewsData.slice(0, 3);
            if (topNews.length === 0) {
                container.innerHTML = '<div class="text-slate-400 text-sm py-4 col-span-3">AI Engine is processing live feeds. Check back shortly.</div>';
                return;
            }
            topNews.forEach((newsItem) => {
                const div = document.createElement('div');
                const key = getNewsKey(newsItem);
                div.dataset.newsKey = key;
                div.className = `headline-tile cursor-pointer flex flex-col gap-2 ${selectedHeadlineKey === key ? 'is-active' : ''}`;
                const dt = getNewsDate(newsItem);
                const timeLabel = !isNaN(dt) ? dt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true }) : '';
                const dateLabel = !isNaN(dt) ? dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) : '';
                div.innerHTML = `
                    <div class="flex items-center gap-1.5 text-[9px] text-violet-400 font-mono">
                        <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                        ${dateLabel} · ${timeLabel}
                    </div>
                    <h4 class="text-xs font-bold text-slate-200 line-clamp-2">${escapeHtml(newsItem.headline)}</h4>
                `;
                div.onclick = () => loadArticleIntoMainViewer(newsItem);
                container.appendChild(div);
            });
            if (globalNewsData.length > 0) loadArticleIntoMainViewer(globalNewsData[0]);
        }

        function getImpactColorClasses(impact) {
            const i = (impact || '').toLowerCase();
            if (i === 'bullish') return "bg-green-800/60 text-green-300 border-green-500/80 shadow-[0_0_15px_rgba(34,197,94,0.3)]";
            if (i === 'slightly bullish') return "bg-emerald-900/50 text-emerald-300 border-emerald-500/60 shadow-[0_0_10px_rgba(52,211,153,0.2)]";
            if (i === 'bearish') return "bg-red-800/60 text-red-300 border-red-500/80 shadow-[0_0_15px_rgba(239,68,68,0.3)]";
            if (i === 'slightly bearish') return "bg-orange-900/50 text-orange-300 border-orange-500/60 shadow-[0_0_10px_rgba(251,146,60,0.2)]";
            return "bg-slate-800/60 text-slate-300 border-white/5";
        }

        function getStatusBadge(status) {
            if (status === 'Predicted Target Hit') return { text: '🎯 Target Hit', cls: 'text-green-400' };
            if (status === 'Stop Loss Hit') return { text: '🛑 Stop Loss Hit', cls: 'text-red-400' };
            if (status === 'Reacted Against Prediction') return { text: '🛑 Stop Loss Hit', cls: 'text-red-400' };
            return { text: '● Active View', cls: 'text-violet-400' };
        }

        function getConfidenceBadge(score) {
            const val = score || 80;
            let cls = "text-red-400 border-red-500/30 bg-red-900/10";
            let label = "Speculative";
            if (val >= 85) { cls = "text-green-400 border-green-500/30 bg-green-900/10"; label = "High Veracity"; }
            else if (val >= 60) { cls = "text-amber-400 border-amber-500/30 bg-amber-900/10"; label = "Moderate"; }
            return `<div class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-[9px] font-bold uppercase tracking-wider ${cls}">
                <span class="w-1 h-1 rounded-full bg-current animate-pulse"></span>
                ${label} ${val}%
            </div>`;
        }

        function markActiveHeadline(newsItem) {
            const key = getNewsKey(newsItem);
            document.querySelectorAll('.headline-tile').forEach(tile => {
                tile.classList.toggle('is-active', tile.dataset.newsKey === key);
            });
        }

        function setInsightText(id, value) {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        }

        function updateHeroInsightPanel(newsItem) {
            const stocks = Array.isArray(newsItem?.affected_stocks) ? newsItem.affected_stocks : [];
            const scores = stocks.map(s => Number(s.confidence_score)).filter(n => !Number.isNaN(n));
            const conviction = scores.length ? Math.round(scores.reduce((sum, n) => sum + n, 0) / scores.length) : 72;
            const bullish = stocks.filter(s => (s.impact || '').toLowerCase().includes('bullish')).length;
            const bearish = stocks.filter(s => (s.impact || '').toLowerCase().includes('bearish')).length;
            const bias = bullish > bearish ? 'Bullish' : bearish > bullish ? 'Bearish' : 'Neutral';
            const dt = getNewsDate(newsItem);
            const freshness = !isNaN(dt) ? dt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true }) : '--';

            setInsightText('hero-conviction', `${conviction}%`);
            setInsightText('hero-assets', String(stocks.length));
            setInsightText('hero-bias', bias);
            setInsightText('hero-bias-note', stocks.length ? `${bullish} bullish / ${bearish} bearish` : 'no direct equity signal');
            setInsightText('hero-freshness', freshness);

            const bar = document.getElementById('hero-conviction-bar');
            if (bar) bar.style.width = `${Math.min(100, Math.max(0, conviction))}%`;

            const notes = document.getElementById('hero-desk-notes');
            if (!notes) return;
            const topStock = stocks[0];
            const plain = newsItem?.aam_janta_translation || 'The AI desk is still translating the article into a market view.';
            const noteItems = [
                topStock ? `Primary watch: ${topStock.ticker} is tagged ${topStock.impact || 'under review'}.` : 'No direct stock impact has been detected yet.',
                `Bias read: ${bias} across ${stocks.length} linked asset${stocks.length === 1 ? '' : 's'}.`,
                plain
            ];
            notes.innerHTML = noteItems.map((note, idx) => `
                <div class="insight-tile">
                    <div class="insight-label">Note ${idx + 1}</div>
                    <p class="mt-2 text-sm text-slate-300 leading-relaxed">${escapeHtml(note)}</p>
                </div>
            `).join('');
        }

        function loadArticleIntoMainViewer(newsItem) {
            selectedHeadlineKey = getNewsKey(newsItem);
            markActiveHeadline(newsItem);
            updateHeroInsightPanel(newsItem);
            document.getElementById('main-headline-text').innerText = newsItem.headline;
            // ── On-demand explanation lazy-fetch ──
            // Phase 2 background generation was removed to save ~700 Gemini
            // calls/day. Explanations are now generated on first click via
            // /api/news/<id>/explain and cached for subsequent clicks.
            const aamEl = document.getElementById('aam-janta-text');
            if (newsItem.aam_janta_translation) {
                aamEl.innerText = newsItem.aam_janta_translation;
            } else if (newsItem.id) {
                aamEl.innerText = "🧠 Generating AI explanation…";
                fetch(`/api/news/${newsItem.id}/explain`)
                    .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
                    .then(data => {
                        if (data && data.aam_janta_translation) {
                            // Mutate the in-memory copy so re-clicks don't re-fetch
                            newsItem.aam_janta_translation = data.aam_janta_translation;
                            newsItem.macro_pathway = data.macro_pathway || [];
                            aamEl.innerText = data.aam_janta_translation;
                            // Refresh the macro-pathway panel below if the data arrived
                            if (Array.isArray(newsItem.macro_pathway) && newsItem.macro_pathway.length >= 4) {
                                document.getElementById('path-1').innerText = newsItem.macro_pathway[0];
                                document.getElementById('path-2').innerText = newsItem.macro_pathway[1];
                                document.getElementById('path-3').innerText = newsItem.macro_pathway[2];
                                document.getElementById('path-4').innerText = newsItem.macro_pathway[3];
                            }
                        } else if (data && data.error) {
                            aamEl.innerText = "⚠️ " + data.error;
                        } else {
                            aamEl.innerText = "AI explanation unavailable right now. Try refreshing in a moment.";
                        }
                    })
                    .catch(_err => {
                        aamEl.innerText = "AI explanation unavailable right now. Try refreshing in a moment.";
                    });
            } else {
                aamEl.innerText = "AI explanation unavailable.";
            }
            // Full article body — hidden when empty so the panel doesn't show
            // an empty box. Source is the RSS summary (or scraped body), set
            // by the AI worker at insert time.
            const bodyText = (newsItem.body || '').trim();
            const bodyEl = document.getElementById('main-article-body');
            const bodyWrap = document.getElementById('main-article-body-wrap');
            if (bodyEl && bodyWrap) {
                if (bodyText) {
                    bodyEl.innerText = bodyText;
                    bodyWrap.classList.remove('hidden');
                } else {
                    bodyEl.innerText = '';
                    bodyWrap.classList.add('hidden');
                }
            }
            if (newsItem.macro_pathway && newsItem.macro_pathway.length >= 4) {
                document.getElementById('path-1').innerText = newsItem.macro_pathway[0];
                document.getElementById('path-2').innerText = newsItem.macro_pathway[1];
                document.getElementById('path-3').innerText = newsItem.macro_pathway[2];
                document.getElementById('path-4').innerText = newsItem.macro_pathway[3];
            }
            const tableBody = document.getElementById('dynamic-stock-table-body');
            tableBody.innerHTML = '';
            if (!newsItem.affected_stocks || newsItem.affected_stocks.length === 0) {
                tableBody.innerHTML = '<tr><td colspan="4" class="py-4 text-center text-slate-500">No specific stocks identified for this news.</td></tr>';
                return;
            }
            newsItem.affected_stocks.forEach(stock => {
                const colorClasses = getImpactColorClasses(stock.impact);
                const basePrice = parseFloat(stock.base_price);
                const currentPrice = parseFloat(stock.current_price);
                const hasPrice = !isNaN(basePrice) && basePrice > 0;
                const hasCurrent = !isNaN(currentPrice) && currentPrice > 0;

                const isResolved = ['Stop Loss Hit', 'Predicted Target Hit', 'Reacted Against Prediction'].includes(stock.status);
                const isExpired = stock.status === 'Expired';
                const isClosed = isResolved || isExpired;

                // Market change is always current value vs previous close.
                const diffPct = (stock.diff_pct != null) ? stock.diff_pct
                    : (stock.market_change_pct != null) ? stock.market_change_pct
                    : (hasPrice && hasCurrent ? ((currentPrice - basePrice) / basePrice * 100) : null);
                const diffPctStr = diffPct !== null ? (diffPct >= 0 ? '+' : '') + diffPct.toFixed(2) + '%' : '—';
                const diffColorCls = diffPct === null ? 'text-slate-400' : diffPct >= 0 ? 'text-green-400' : 'text-red-400';

                const statusBadge = getStatusBadge(stock.status);
                const tr = document.createElement('tr');
                tr.style.opacity = isClosed ? '0.85' : '1';

                // ── Closed banner (shown above status for resolved/expired signals) ──
                const closedBanner = isClosed
                    ? `<div class="inline-flex items-center gap-1 text-[9px] font-bold uppercase tracking-widest px-2 py-0.5 rounded mb-1"
                           style="background:rgba(148,163,184,0.15);color:#94a3b8;border:1px solid rgba(148,163,184,0.3)">
                           ◼ SIGNAL CLOSED
                       </div><br>`
                    : '';

                // ── Live / price-only badge (in current price column) ──
                const priceBadge = (marketOpen && stock.status === 'Active View')
                    ? `<div class="text-[8px] font-bold mt-1" style="color:#4ade80">● LIVE PRICE</div>`
                    : isClosed
                        ? `<div class="text-[8px] font-bold mt-1" style="color:#64748b">← LIVE PRICE (tracking)</div>`
                        : '';

                tr.innerHTML = `
                    <td class="py-4">
                        ${closedBanner}
                        <div class="font-bold text-white text-base tracking-wide">${escapeHtml(stock.ticker)}</div>
                        <div class="flex items-center gap-2 mt-1">
                            <div class="text-[9px] font-bold ${statusBadge.cls}">${statusBadge.text}</div>
                            ${getConfidenceBadge(stock.confidence_score)}
                        </div>
                        <div class="text-[9px] text-slate-400 uppercase tracking-widest mt-1 bg-black/40 inline-block px-2 py-0.5 rounded border border-white/5">${escapeHtml(stock.view || 'Pending')}</div>
                    </td>
                    <td class="py-4 text-right">
                        <div class="text-white font-bold font-mono text-sm">${hasPrice ? '₹' + basePrice.toFixed(2) : '—'}</div>
                        ${hasPrice ? `<div class="text-[8px] text-slate-500 mt-0.5">At news time</div>` : '<div class="text-[8px] text-slate-600 mt-0.5">Market closed at news</div>'}
                    </td>
                    <td class="py-4 text-right">
                        <div class="text-white font-bold font-mono text-sm">${hasCurrent ? '₹' + currentPrice.toFixed(2) : '—'}</div>
                        <div class="font-mono text-xs font-bold ${diffColorCls} mt-0.5">${diffPctStr}</div>
                        ${priceBadge}
                    </td>
                    <td class="py-4 text-right">
                        <span class="border px-3 py-1.5 rounded-lg font-bold text-[11px] uppercase tracking-widest ${colorClasses}">${escapeHtml(stock.impact)}</span>
                    </td>
                `;
                tableBody.appendChild(tr);
            });
        }


        // ── All News virtualization state ──
        // We hold the filtered list in memory but only mount cards into the
        // DOM in batches as the sentinel scrolls into view. With ~500 cards
        // this is the difference between a 4s freeze and instant render.
        let _archiveFiltered = [];
        let _archiveRendered = 0;
        let _archiveObserver = null;
        const _ARCHIVE_BATCH_SIZE = 30;

        function _buildArchiveCard(news, cardIdx) {
            const item = document.createElement('div');
            item.className = "glass-panel news-card-hover p-6 rounded-2xl cursor-pointer";
            item.style.setProperty('--i', Math.min(cardIdx, 12));
            item.setAttribute('data-stagger-i', String(cardIdx));
            item.onclick = (e) => {
                if (e.target.closest('.ticker-hover-target')) return;
                loadArticleIntoMainViewer(news); switchTab('top-news');
            };
            const dt = getNewsDate(news);
            const dateStr = !isNaN(dt) ? dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';
            const timeStr = !isNaN(dt) ? dt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true }) : '—';
            let impactedStocksHtml = '';
            if (news.affected_stocks && news.affected_stocks.length > 0) {
                const seenBadgeTickers = new Set();
                news.affected_stocks.forEach(stock => {
                    const tkKey = (stock.ticker || '').toUpperCase();
                    if (seenBadgeTickers.has(tkKey)) return;
                    seenBadgeTickers.add(tkKey);
                    const impact = (stock.impact || '').toLowerCase();
                    let color = impact.includes('bullish') ? 'text-green-400 border-green-500/30 bg-green-900/10' :
                        impact.includes('slightly bearish') ? 'text-orange-400 border-orange-500/30 bg-orange-900/10' :
                            'text-red-400 border-red-500/30 bg-red-900/10';
                    impactedStocksHtml += `
                        <div class="flex flex-col gap-1">
                            <span class="ticker-hover-target text-[10px] uppercase tracking-widest font-bold border px-2 py-1 rounded ${color}" data-ticker="${escapeHtml(stock.ticker)}">${escapeHtml(stock.ticker)}</span>
                            ${getConfidenceBadge(stock.confidence_score)}
                        </div>`;
                });
            } else if (news.ai_status === 'pending') {
                // Headline saved during AI downtime — predictions will fill
                // in on the next rescreen pass.
                impactedStocksHtml = `<span class="inline-flex items-center gap-1.5 text-[10px] text-violet-300 uppercase tracking-widest font-bold border border-violet-500/40 bg-violet-900/20 px-2 py-1 rounded">
                    <span class="w-1 h-1 rounded-full bg-violet-300 animate-pulse"></span>
                    AI Analysis Pending
                </span>`;
            } else {
                impactedStocksHtml = `<span class="text-[10px] text-slate-500 uppercase tracking-widest">No direct equity impact</span>`;
            }
            // Body snippet — first ~200 chars of news.body. Backend lite mode
            // already trims server-side; this is a safety belt for very old
            // rows or non-lite responses.
            const rawBody = (news.body || '').trim();
            const bodySnippet = rawBody ? (rawBody.length > 220 ? rawBody.slice(0, 220).trim() + '…' : rawBody) : '';
            const bodyHtml = bodySnippet ? `
                <p class="text-[11px] text-slate-400 leading-relaxed mt-2 mb-3 line-clamp-3">${escapeHtml(bodySnippet)}</p>
            ` : '';
            const aamJanta = (news.aam_janta_translation || '').trim();
            const backHtml = aamJanta ? `
                <div class="nc-back">
                    <div class="nc-back-label">AI Reasoning</div>
                    <div class="nc-back-body">${escapeHtml(aamJanta.length > 320 ? aamJanta.slice(0, 320) + '…' : aamJanta)}</div>
                </div>
            ` : '';
            // "View Ripple" badge — only for big macro events. Backend sets
            // news.has_ripple = 1 after the propagation graph is generated.
            const rippleCta = news.has_ripple ? `
                <button class="ripple-cta" data-ripple-id="${news.id}" aria-label="Open propagation graph">
                    <span class="ripple-cta-icon"></span>
                    The Ripple
                    ${news.ripple_score ? `<span class="ripple-cta-score">${news.ripple_score}</span>` : ''}
                </button>
            ` : '';

            item.innerHTML = `
                <div class="nc-front">
                    <div class="flex items-center justify-between gap-2 mb-2">
                        <div class="flex items-center gap-1.5 text-[9px] text-violet-400 font-mono">
                            <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                            ${dateStr} · ${timeStr}
                        </div>
                        ${rippleCta}
                    </div>
                    <div class="flex items-start justify-between gap-4 mb-1">
                        <h3 class="text-base font-bold text-slate-100 leading-snug flex-1">${escapeHtml(news.headline)}</h3>
                    </div>
                    ${bodyHtml}
                    <div class="stock-badge-container items-center gap-3 pt-3 border-t border-white/5 transition-opacity duration-300"
                         style="display: ${isNonStockMode ? 'none' : 'flex'}">
                        <svg class="w-3.5 h-3.5 text-slate-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/></svg>
                        <div class="flex flex-wrap gap-2">${impactedStocksHtml}</div>
                    </div>
                </div>
                ${backHtml}
            `;
            // Wire the Ripple CTA — stop propagation so clicking the badge
            // doesn't also fire the card's "open article" handler.
            const ctaEl = item.querySelector('[data-ripple-id]');
            if (ctaEl) {
                ctaEl.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openRipple(parseInt(ctaEl.getAttribute('data-ripple-id'), 10));
                });
            }
            return item;
        }

        function _renderArchiveBatch(container) {
            const end = Math.min(_archiveRendered + _ARCHIVE_BATCH_SIZE, _archiveFiltered.length);
            const frag = document.createDocumentFragment();
            for (let i = _archiveRendered; i < end; i++) {
                frag.appendChild(_buildArchiveCard(_archiveFiltered[i], i));
            }
            // Remove the old sentinel before appending more cards
            const oldSentinel = document.getElementById('archive-load-sentinel');
            if (oldSentinel) oldSentinel.remove();
            container.appendChild(frag);
            _archiveRendered = end;
            // Add a fresh sentinel if more cards remain
            if (_archiveRendered < _archiveFiltered.length) {
                const sentinel = document.createElement('div');
                sentinel.id = 'archive-load-sentinel';
                sentinel.style.cssText = 'height:48px;display:flex;align-items:center;justify-content:center;color:#64748b;font-size:11px;letter-spacing:0.1em;text-transform:uppercase;';
                sentinel.textContent = `Loading more · ${_archiveRendered} / ${_archiveFiltered.length}`;
                container.appendChild(sentinel);
                if (_archiveObserver) _archiveObserver.observe(sentinel);
            }
        }

        function renderArchiveView() {
            const container = document.getElementById('archive-news-list');
            if (!container) return;
            container.innerHTML = '';
            _archiveRendered = 0;
            // Tear down any prior observer — we'll create a fresh one for the
            // current list so the sentinel reference doesn't leak across renders.
            if (_archiveObserver) {
                try { _archiveObserver.disconnect(); } catch (_) {}
                _archiveObserver = null;
            }

            const sevenDaysAgo = Date.now() - (168 * 60 * 60 * 1000);
            let recentNews = globalNewsData.filter(news => parseSQLiteDate(news.created_at).getTime() >= sevenDaysAgo);
            if (currentArchiveFilter !== 'all') {
                if (currentArchiveFilter.startsWith('cat:')) {
                    const targetCat = currentArchiveFilter.split(':')[1].toLowerCase();
                    recentNews = recentNews.filter(news => (news.category || 'general').toLowerCase() === targetCat);
                } else {
                    recentNews = recentNews.filter(news => {
                        const hasStocks = news.affected_stocks && news.affected_stocks.length > 0;
                        if (currentArchiveFilter === 'none') return !hasStocks;
                        if (hasStocks) {
                            return news.affected_stocks.some(stock => (stock.impact || '').toLowerCase() === currentArchiveFilter);
                        }
                        return false;
                    });
                }
            }
            _archiveFiltered = recentNews;

            const countEl = document.getElementById('news-count');
            if (countEl) countEl.innerText = `${_archiveFiltered.length} Articles`;
            if (_archiveFiltered.length === 0) {
                container.innerHTML = '<div class="glass-panel p-8 rounded-2xl text-center text-slate-400">No news found in the last 7 days. The AI engine may still be processing feeds.</div>';
                return;
            }

            // Spin up an observer that fires when the sentinel scrolls into view.
            // rootMargin pre-loads a screen ahead so the user never sees the loader
            // unless they're scrolling unreasonably fast.
            _archiveObserver = new IntersectionObserver((entries) => {
                for (const entry of entries) {
                    if (entry.isIntersecting) {
                        _renderArchiveBatch(container);
                    }
                }
            }, { rootMargin: '600px 0px', threshold: 0.01 });

            _renderArchiveBatch(container);
        }

        // ==========================================
        // WATCHLIST & PORTFOLIO LOGIC
        // ==========================================
        let watchlist = JSON.parse(localStorage.getItem('alpha_lens_watchlist') || '[]');
        let watchlistPrices = {};
        let searchTimeout = null;
        let portfolioAssistantBusy = false;

        function tickerSymbol(ticker) {
            return (ticker || '').toUpperCase().replace(/\.(NS|BO)$/i, '').trim();
        }

        function escapeHtml(value) {
            return String(value || '').replace(/[&<>"']/g, (ch) => ({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;'
            }[ch]));
        }

        function formatAssistantAnswer(text) {
            const safe = escapeHtml(text)
                .replace(/\*\*([^*]+)\*\*/g, '<strong class="text-white font-bold">$1</strong>');

            return safe.split('\n').map(line => {
                const trimmed = line.trim();
                if (!trimmed) return '<div class="h-2"></div>';
                if (trimmed.startsWith('- ')) {
                    return `<div class="flex gap-2"><span class="text-violet-400">•</span><span>${trimmed.slice(2)}</span></div>`;
                }
                if (/^\d+\.\s/.test(trimmed)) {
                    return `<div>${trimmed}</div>`;
                }
                return `<div>${trimmed}</div>`;
            }).join('');
        }

        function formatAssistantMeta(meta) {
            if (!meta) return '';
            const sourceLabels = {
                ai: 'AI refined',
                local: 'Saved news',
                fallback: 'Local fallback',
                blocked: 'Scope guard',
                no_context: 'No saved news'
            };
            const parts = [sourceLabels[meta.source] || meta.source || 'Assistant'];
            if (typeof meta.elapsed_ms === 'number') parts.push(`${meta.elapsed_ms}ms`);
            if (typeof meta.context_count === 'number') parts.push(`${meta.context_count} news`);
            if (Array.isArray(meta.matched_tickers) && meta.matched_tickers.length) parts.push(meta.matched_tickers.join(', '));
            return parts.filter(Boolean).join(' · ');
        }

        function saveWatchlist() {
            localStorage.setItem('alpha_lens_watchlist', JSON.stringify(watchlist));
            renderWatchlistPanel();
            renderPortfolioView();
            updatePortfolioAssistantState();
        }

        // LOCAL_STOCKS is loaded from external stocks.js script


        function searchLocalStocks(query) {
            const q = query.toLowerCase().trim();
            if (!q || q.length < 2) return [];
            const results = [];
            const seen = new Set();
            for (const stock of LOCAL_STOCKS) {
                const tLower = stock.t.toLowerCase();
                const nLower = stock.n.toLowerCase();
                const baseLower = stock.t.replace(/\.(NS|BO)$/i,'').toLowerCase();
                let rank = 99;
                if (nLower === q || tLower === q || baseLower === q) rank = 0;
                else if (baseLower.startsWith(q) || nLower.startsWith(q)) rank = 1;
                else if (baseLower.includes(q) || nLower.includes(q) || tLower.includes(q)) rank = 2;
                else continue;
                if (!seen.has(stock.t)) {
                    seen.add(stock.t);
                    results.push({rank, ticker: stock.t, name: stock.n, exchange: stock.t.endsWith('.BO') ? 'BSE' : 'NSE'});
                }
            }
            results.sort((a, b) => a.rank - b.rank || a.ticker.length - b.ticker.length);
            return results.slice(0, 20);
        }

        function renderSearchDropdown(results) {
            const inputEl = document.getElementById('stock-search-input');
            const dropdown = document.getElementById('stock-search-dropdown');
            if (!dropdown) return;
            dropdown.innerHTML = '';
            if (!results || results.length === 0) {
                dropdown.innerHTML = '<div class="px-4 py-3 text-sm text-slate-500">No stocks found</div>';
            } else {
                results.forEach(stock => {
                    const isAdded = watchlist.some(s => tickerSymbol(s.ticker) === tickerSymbol(stock.ticker));
                    const div = document.createElement('div');
                    div.className = `px-4 py-3 hover:bg-white/10 cursor-pointer flex justify-between items-center border-b border-white/5 last:border-0 ${isAdded ? 'opacity-50' : ''}`;
                    div.innerHTML = `
                        <div>
                            <div class="text-sm font-bold text-white">${escapeHtml(stock.ticker)}</div>
                            <div class="text-[10px] text-slate-400 uppercase tracking-wider">${escapeHtml(stock.name)}</div>
                        </div>
                        ${isAdded ? '<span class="text-[10px] text-green-400 font-bold border border-green-500/30 px-2 py-0.5 rounded">ADDED</span>' : ''}
                    `;
                    if (!isAdded) {
                        div.onclick = () => {
                            addStockToWatchlist(stock.ticker, stock.name);
                            dropdown.classList.add('hidden');
                            if (inputEl) inputEl.value = '';
                        };
                    }
                    dropdown.appendChild(div);
                });
            }
            dropdown.classList.remove('hidden');
        }

        async function handleStockSearch(e) {
            const inputEl = document.getElementById('stock-search-input');
            const query = (inputEl ? inputEl.value : '').trim();
            const dropdown = document.getElementById('stock-search-dropdown');

            if (searchTimeout) clearTimeout(searchTimeout);

            if (!query || query.length < 2) {
                if (dropdown) dropdown.classList.add('hidden');
                return;
            }

            // T2.7: Ensure the (lazy-loaded) LOCAL_STOCKS table is present.
            // First call here triggers the fetch of /stocks.js if it hasn't
            // happened yet; subsequent calls resolve instantly from the cache.
            if (typeof LOCAL_STOCKS === 'undefined' && window.__alphaLoadStocks) {
                if (dropdown) {
                    dropdown.innerHTML = '<div class="px-4 py-3 text-sm text-slate-500">Loading universe…</div>';
                    dropdown.classList.remove('hidden');
                }
                try { await window.__alphaLoadStocks(); } catch (e) { /* keep going — backend search still works */ }
            }

            // STEP 1: Show local results instantly (zero latency)
            const localResults = searchLocalStocks(query);
            renderSearchDropdown(localResults.length > 0 ? localResults : null);

            // STEP 2: Also fetch from backend (for obscure stocks) and merge
            searchTimeout = setTimeout(async () => {
                try {
                    const res = await fetch('/api/stock-search?q=' + encodeURIComponent(query));
                    if (!res.ok) return; // Keep local results on API error
                    const apiResults = await res.json();
                    if (!Array.isArray(apiResults)) return;

                    // Merge: local results first, then add any new tickers from API
                    const seen = new Set(localResults.map(s => s.ticker));
                    const extra = apiResults.filter(s => !seen.has(s.ticker));
                    const merged = [...localResults, ...extra].slice(0, 20);
                    renderSearchDropdown(merged.length > 0 ? merged : null);
                } catch (err) {
                    // Keep local results — do not show error to user
                }
            }, 400);
        }

        document.addEventListener('click', (e) => {
            if (!e.target.closest('.relative')) {
                const dropdown = document.getElementById('stock-search-dropdown');
                if (dropdown) dropdown.classList.add('hidden');
            }
        });

        function addStockToWatchlist(ticker, name) {
            if (!watchlist.some(s => tickerSymbol(s.ticker) === tickerSymbol(ticker))) {
                watchlist.push({ ticker, name });
                saveWatchlist();
                updateWatchlistPrices();
            }
        }

        function removeStockFromWatchlist(ticker) {
            watchlist = watchlist.filter(s => s.ticker !== ticker);
            delete watchlistPrices[ticker];
            saveWatchlist();
        }

        async function updateWatchlistPrices() {
            if (watchlist.length === 0) return;
            
            for (const stock of watchlist) {
                try {
                    const res = await fetch('/api/stock-price/' + stock.ticker);
                    const data = await res.json();
                    if (!watchlistPrices[stock.ticker]) watchlistPrices[stock.ticker] = {};
                    watchlistPrices[stock.ticker].price = data.price;
                    watchlistPrices[stock.ticker].change_pct = data.change_pct;
                } catch(e) {}
            }
            renderWatchlistPanel();
        }

        function renderWatchlistPanel() {
            const container = document.getElementById('watchlist-container');
            if (!container) return;
            
            if (watchlist.length === 0) {
                container.innerHTML = '<div class="text-center py-6 text-slate-500 text-sm border border-dashed border-white/20 rounded-xl bg-black/20">Your watchlist is empty.<br>Search and add stocks above.</div>';
                return;
            }
            
            container.innerHTML = '';
            watchlist.forEach(stock => {
                const priceData = watchlistPrices[stock.ticker] || {};
                const priceFmt = priceData.price ? '₹' + priceData.price.toFixed(2) : '...';
                
                let changeHtml = '';
                if (priceData.change_pct !== undefined) {
                    const pct = priceData.change_pct;
                    const colorCls = pct >= 0 ? 'text-green-400' : 'text-red-400';
                    const sign = pct > 0 ? '+' : '';
                    changeHtml = `<div class="text-[10px] font-bold ${colorCls} mt-1">${sign}${pct.toFixed(2)}%</div>`;
                }
                
                const card = document.createElement('div');
                card.className = "flex items-center justify-between p-3 bg-black/40 border border-white/5 rounded-xl hover:border-violet-500/20 transition-colors group";
                card.innerHTML = `
                    <div class="flex flex-col ticker-hover-target" data-ticker="${escapeHtml(stock.ticker)}" style="cursor:pointer">
                        <span class="font-bold font-display text-white tracking-widest text-sm">${escapeHtml(stock.ticker)}</span>
                        <span class="text-[9px] text-slate-400 uppercase tracking-widest max-w-[150px] truncate">${escapeHtml(stock.name)}</span>
                    </div>
                    <div class="flex items-center gap-4">
                        <div class="text-right">
                            <div class="font-mono text-sm font-bold text-white">${priceFmt}</div>
                            ${changeHtml}
                        </div>
                        <button type="button" class="remove-watchlist-stock text-slate-500 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity p-1">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                        </button>
                    </div>
                `;
                card.querySelector('.remove-watchlist-stock')?.addEventListener('click', () => {
                    removeStockFromWatchlist(stock.ticker);
                });
                container.appendChild(card);
            });
        }

        function addPortfolioAssistantMessage(role, text) {
            const container = document.getElementById('portfolio-assistant-messages');
            if (!container) return null;

            const emptyState = container.querySelector('[data-empty-assistant]');
            if (emptyState) emptyState.remove();

            const msg = document.createElement('div');
            const isUser = role === 'user';
            msg.className = `text-sm rounded-xl p-3 border leading-relaxed ${isUser
                ? 'ml-8 bg-violet-500/10 border-violet-500/20 text-violet-50'
                : 'mr-8 bg-black/30 border-white/5 text-slate-300'}`;
            msg.innerHTML = `<div class="text-[9px] uppercase tracking-widest font-bold mb-1 ${isUser ? 'text-violet-300' : 'text-slate-500'}">${isUser ? 'You' : 'Assistant'}</div>
                <div data-message-body="true" class="${isUser ? 'whitespace-pre-wrap' : 'space-y-1'}">${isUser ? escapeHtml(text) : formatAssistantAnswer(text)}</div>
                ${isUser ? '' : '<div data-message-meta="true" class="hidden mt-2 text-[9px] uppercase tracking-widest text-slate-500"></div>'}`;
            container.appendChild(msg);
            container.scrollTop = container.scrollHeight;
            return msg;
        }

        function updatePortfolioAssistantMessage(messageEl, text, meta = null) {
            const body = messageEl?.querySelector('[data-message-body]');
            if (!body) return;
            body.innerHTML = formatAssistantAnswer(text);
            const metaEl = messageEl.querySelector('[data-message-meta]');
            if (metaEl) {
                const metaText = formatAssistantMeta(meta);
                metaEl.textContent = metaText;
                metaEl.classList.toggle('hidden', !metaText);
            }
            const container = document.getElementById('portfolio-assistant-messages');
            if (container) container.scrollTop = container.scrollHeight;
        }

        function updatePortfolioAssistantState() {
            const sendBtn = document.getElementById('portfolio-assistant-send');
            const input = document.getElementById('portfolio-assistant-input');
            const chips = document.querySelectorAll('#portfolio-assistant-chips button');
            if (!sendBtn || !input) return;
            const disabled = portfolioAssistantBusy || watchlist.length === 0;
            sendBtn.disabled = disabled;
            input.disabled = portfolioAssistantBusy;
            input.placeholder = watchlist.length === 0 ? 'Add portfolio stocks first' : 'Ask about portfolio news...';
            chips.forEach(chip => {
                chip.disabled = disabled;
                chip.classList.toggle('opacity-40', disabled);
                chip.classList.toggle('cursor-not-allowed', disabled);
            });
        }

        function getPortfolioNewsForQuestion(question) {
            const q = (question || '').toLowerCase();
            const watchlistSymbols = new Set(watchlist.map(s => tickerSymbol(s.ticker)));
            const mentionedPortfolioSymbols = [...watchlistSymbols].filter(sym => q.includes(sym.toLowerCase()));
            const mentionsAddedName = watchlist.some(stock => {
                const name = (stock.name || '').toLowerCase();
                return name && name.length > 2 && q.includes(name);
            });
            const portfolioTerms = ['portfolio', 'holding', 'watchlist', 'news', 'impact', 'risk', 'view', 'bullish', 'bearish', 'move'];
            const qTokens = new Set((q.match(/[a-z]{4,}/g) || []).filter(tok => ![
                'what', 'when', 'where', 'which', 'about', 'from', 'that', 'this',
                'will', 'with', 'have', 'does', 'there', 'their', 'your', 'explain',
            ].includes(tok)));

            const hasSavedHeadlineMatch = (news) => {
                const text = `${news.headline || ''} ${news.aam_janta_translation || ''}`.toLowerCase();
                let matches = 0;
                qTokens.forEach(tok => { if (text.includes(tok)) matches += 1; });
                return matches >= 2;
            };

            if (
                mentionedPortfolioSymbols.length === 0 &&
                !mentionsAddedName &&
                !portfolioTerms.some(term => q.includes(term)) &&
                !globalNewsData.some(hasSavedHeadlineMatch)
            ) {
                return [];
            }

            return globalNewsData.filter(news => {
                if (!news.affected_stocks || news.affected_stocks.length === 0) return false;
                return news.affected_stocks.some(stock => {
                    const symbol = tickerSymbol(stock.ticker);
                    if (!watchlistSymbols.has(symbol)) return false;
                    if (mentionedPortfolioSymbols.length > 0) return mentionedPortfolioSymbols.includes(symbol);
                    return portfolioTerms.some(term => q.includes(term)) || mentionsAddedName || hasSavedHeadlineMatch(news);
                });
            }).slice(0, 5);
        }

        function buildDataOnlyPortfolioAnswer(question) {
            // Used when the network/AI call fails entirely. We don't have fundamentals
            // client-side, but we can at least list the watchlist with live prices and
            // tailor the intro to the question type so the user gets a real answer.
            if (!watchlist || watchlist.length === 0) {
                return 'Add stocks to your portfolio first, then I can help you analyze them.';
            }
            const q = (question || '').toLowerCase();
            const horizon = /(long[- ]?term|hold|year|month|horizon|future)/.test(q);
            const sell = /(sell|exit|book profit|trim|reduce|offload)/.test(q);
            const valuation = /(expensive|cheap|overvalu|undervalu|valuation|p\/?e|fairly|worth)/.test(q);
            const risk = /(risk|risky|safe|downside|drawdown|volatil)/.test(q);

            let intro;
            if (horizon && sell) {
                intro = 'For a multi-year hold, favour names with steady earnings and reasonable valuation. Trim candidates are usually stocks trading near 52-week highs on stretched P/E, or those in sectors facing structural headwinds.';
            } else if (horizon) {
                intro = 'Long-term holds work best with durable franchises bought at sensible valuations. Sector tailwinds and a P/E near or below the sector median are useful anchors.';
            } else if (sell) {
                intro = 'Stocks worth trimming are typically those with P/E meaningfully above their sector median, near 52-week highs with weakening fundamentals, or facing sector-specific headwinds.';
            } else if (valuation) {
                intro = 'Quick valuation read: compare each holding’s P/E to its sector median. Above median needs growth to justify, below median may signal a discount if earnings hold up.';
            } else if (risk) {
                intro = 'Risk concentrates in stocks well above their 52-week low and on rich P/E. Sector diversification reduces single-name shock risk.';
            } else {
                intro = 'Quick read on your current holdings below. The AI advisor is briefly unavailable, but this snapshot covers the basics.';
            }

            const lines = watchlist.map(s => {
                const px = watchlistPrices[s.ticker] || {};
                const price = (px.price != null) ? `₹${Number(px.price).toFixed(2)}` : '—';
                const chg = (px.change_pct != null) ? ` (${px.change_pct >= 0 ? '+' : ''}${Number(px.change_pct).toFixed(2)}%)` : '';
                return `- **${s.ticker}** — ${price}${chg}`;
            });

            return `**Answer**
${intro}

**Your Holdings**
${lines.join('\n')}

**Note**
The advisor service didn’t respond in time. Ask again in a moment for a full analysis.`;
        }

        function buildLocalPortfolioAnswer(question) {
            const relatedNews = getPortfolioNewsForQuestion(question);
            if (relatedNews.length === 0) {
                return buildDataOnlyPortfolioAnswer(question);
            }

            const watchlistSymbols = new Set(watchlist.map(s => tickerSymbol(s.ticker)));
            const symbolsInAnswer = new Set();
            const rows = [];
            relatedNews.forEach(news => {
                const stocks = (news.affected_stocks || []).filter(stock => watchlistSymbols.has(tickerSymbol(stock.ticker)));
                stocks.forEach(stock => {
                    symbolsInAnswer.add(stock.ticker);
                    const confidence = stock.confidence_score || 'NA';
                    rows.push(`- **${stock.ticker}**: ${stock.impact || 'Impact pending'} (${confidence}% confidence) from "${news.headline}"`);
                });
            });
            const stockLabel = [...symbolsInAnswer].join(', ') || 'the selected portfolio stock';
            const primary = relatedNews[0] || {};
            const explanation = primary.aam_janta_translation || `This saved item is linked to ${stockLabel}.`;

            return `**Answer**
${explanation}

**Portfolio Impact**
${rows.slice(0, 4).join('\n')}

**News Used**
${relatedNews.slice(0, 3).map(news => `- ${news.headline}`).join('\n')}`;
        }

        function askPortfolioAssistantPrompt(prompt) {
            if (portfolioAssistantBusy || watchlist.length === 0) return;
            const input = document.getElementById('portfolio-assistant-input');
            const form = document.getElementById('portfolio-assistant-form');
            if (!input || !form) return;
            input.value = prompt;
            if (typeof form.requestSubmit === 'function') {
                form.requestSubmit();
            } else {
                form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
            }
        }

        async function askPortfolioAssistant(event) {
            event.preventDefault();
            if (portfolioAssistantBusy || watchlist.length === 0) return;

            const input = document.getElementById('portfolio-assistant-input');
            const question = (input?.value || '').trim();
            if (!question) return;

            input.value = '';
            addPortfolioAssistantMessage('user', question);
            const pendingMessage = addPortfolioAssistantMessage('assistant', 'Alpha Lens AI is thinking....');
            portfolioAssistantBusy = true;
            updatePortfolioAssistantState();

            const controller = new AbortController();
            // Backend may rotate through several Gemini keys on timeout (~6.5s each),
            // and yfinance fundamentals can take a few seconds. Give it room.
            const timeoutId = setTimeout(() => controller.abort(), 30000);

            try {
                const res = await fetch('/api/portfolio-assistant', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    signal: controller.signal,
                    body: JSON.stringify({
                        question,
                        tickers: watchlist.map(stock => stock.ticker),
                        holdings: watchlist.map(stock => ({
                            ticker: stock.ticker,
                            name: stock.name || ''
                        }))
                    })
                });
                if (!res.ok) throw new Error('Portfolio assistant returned ' + res.status);
                const data = await res.json();
                updatePortfolioAssistantMessage(pendingMessage, data.answer || 'I could not answer that from your portfolio news.', data);
            } catch (err) {
                console.error('Portfolio assistant failed', err);
                const fallback = buildLocalPortfolioAnswer(question);
                const fallbackCount = getPortfolioNewsForQuestion(question).length;
                updatePortfolioAssistantMessage(pendingMessage, fallback, {
                    source: 'fallback',
                    context_count: fallbackCount
                });
            } finally {
                clearTimeout(timeoutId);
                portfolioAssistantBusy = false;
                updatePortfolioAssistantState();
                input?.focus();
            }
        }

        function renderPortfolioView() {
            const container = document.getElementById('portfolio-news-list');
            if (!container) return;
            container.innerHTML = '';
            
            if (watchlist.length === 0) {
                const countEl = document.getElementById('portfolio-count');
                if (countEl) countEl.innerText = '0 News Items';
                container.innerHTML = '<div class="glass-panel p-8 rounded-2xl text-center text-slate-400">Add stocks to your watchlist to see relevant news here.</div>';
                return;
            }

            const watchlistSymbols = new Set(watchlist.map(s => tickerSymbol(s.ticker)));

            // Build per-symbol headline regexes (word-boundary match, skip very short symbols
            // to avoid false positives like "ABB" matching "abbey").
            const symbolPatterns = [];
            watchlistSymbols.forEach(sym => {
                if (!sym || sym.length < 3) return;
                // Escape regex special chars in ticker symbols (e.g. &)
                const esc = sym.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                symbolPatterns.push({ sym, re: new RegExp(`\\b${esc}\\b`, 'i') });
            });

            const headlineMatchesWatchlist = (headline) => {
                if (!headline) return null;
                for (const { sym, re } of symbolPatterns) {
                    if (re.test(headline)) return sym;
                }
                return null;
            };

            // Show news that EITHER (a) has affected_stocks tagged with a watchlist ticker,
            // or (b) mentions a watchlist ticker in its headline (catches news the AI tagger missed).
            const sevenDaysAgo = Date.now() - (168 * 60 * 60 * 1000);
            const stockNews = globalNewsData.filter(n => {
                if (parseSQLiteDate(n.created_at).getTime() < sevenDaysAgo) return false;

                const taggedMatch = n.affected_stocks && n.affected_stocks.some(stock => {
                    return watchlistSymbols.has(tickerSymbol(stock.ticker));
                });
                if (taggedMatch) return true;

                return !!headlineMatchesWatchlist(n.headline);
            });
            
            const countEl = document.getElementById('portfolio-count');
            if (countEl) countEl.innerText = `${stockNews.length} News Items`;
            
            if (stockNews.length === 0) {
                container.innerHTML = '<div class="glass-panel p-8 rounded-2xl text-center text-slate-400">No recent news found for your watchlist stocks.</div>';
                return;
            }
            
            stockNews.forEach(news => {
                const dt = getNewsDate(news);
                const dateStr = !isNaN(dt) ? dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';
                const timeStr = !isNaN(dt) ? dt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true }) : '—';
                const card = document.createElement('div');
                card.className = 'glass-panel p-6 rounded-2xl mb-4';
                
                const seenPortfolioTickers = new Set();

                let stockRowsHtml = (news.affected_stocks || []).filter(stock => {
                    const tkKey = tickerSymbol(stock.ticker);
                    if (!watchlistSymbols.has(tkKey)) return false; // Hide stocks not in watchlist
                    if (seenPortfolioTickers.has(tkKey)) return false;
                    seenPortfolioTickers.add(tkKey);
                    return true;
                }).map(stock => {
                    const impact = (stock.impact || '').toLowerCase();
                    const bp = parseFloat(stock.base_price);
                    const cp = parseFloat(stock.current_price);
                    const hasP = !isNaN(bp) && bp > 0;
                    const hasC = !isNaN(cp) && cp > 0;
                    const diff = (stock.diff_pct != null) ? stock.diff_pct
                        : (stock.market_change_pct != null) ? stock.market_change_pct
                        : (hasP && hasC ? ((cp - bp) / bp * 100) : null);
                    const diffStr = diff !== null ? (diff >= 0 ? '+' : '') + diff.toFixed(2) + '%' : '—';
                    const diffCls = diff === null ? 'text-slate-400' : diff >= 0 ? 'text-green-400' : 'text-red-400';
                    const statusBadge = getStatusBadge(stock.status);
                    let tCol = impact === 'bullish' ? 'text-green-400' :
                        impact === 'slightly bullish' ? 'text-emerald-400' :
                            impact === 'slightly bearish' ? 'text-orange-400' : 'text-red-400';
                    const pLabel = (marketOpen && stock.status === 'Active View')
                        ? `<span class="text-[8px]" style="color:#4ade80">● LIVE</span>`
                        : (stock.status !== 'Active View' && stock.status !== 'Pending')
                            ? `<span class="text-[8px]" style="color:#94a3b8">● CLOSED</span>`
                            : ``;
                    return `
                        <div class="flex items-center justify-between py-3 border-b border-white/5 last:border-0 hover:bg-white/[0.02] transition-colors">
                            <div class="flex flex-col gap-1">
                                <div class="flex items-center gap-2">
                                    <span class="font-bold font-display text-white text-sm tracking-widest">${escapeHtml(stock.ticker)}</span>
                                    <span class="text-[9px] font-bold uppercase ${tCol}">${escapeHtml(stock.impact)}</span>
                                </div>
                                <div class="flex items-center gap-2">
                                    <div class="text-[9px] ${statusBadge.cls} font-bold">${statusBadge.text}</div>
                                    ${getConfidenceBadge(stock.confidence_score)}
                                </div>
                            </div>
                            <div class="text-right font-mono text-xs">
                                <div class="text-slate-400 mb-0.5">${pLabel}</div>
                                <div class="text-slate-400">${hasP ? '₹' + bp.toFixed(2) : '—'} → <span class="text-white font-bold">${hasC ? '₹' + cp.toFixed(2) : '—'}</span></div>
                                <div class="font-bold ${diffCls}">${diffStr}</div>
                            </div>
                        </div>`;
                }).join('');

                // If no tagged stock matched but the headline mentions a watchlist ticker,
                // render a placeholder row so the card still surfaces the connection.
                if (!stockRowsHtml) {
                    const matchedSym = headlineMatchesWatchlist(news.headline);
                    if (matchedSym) {
                        const wlEntry = watchlist.find(s => tickerSymbol(s.ticker) === matchedSym);
                        const displayTicker = wlEntry ? wlEntry.ticker : matchedSym;
                        stockRowsHtml = `
                            <div class="flex items-center justify-between py-3 border-b border-white/5 last:border-0">
                                <div class="flex items-center gap-2">
                                    <span class="font-bold font-display text-white text-sm tracking-widest">${escapeHtml(displayTicker)}</span>
                                    <span class="text-[9px] font-bold uppercase text-slate-400">MENTIONED</span>
                                </div>
                                <div class="text-[9px] text-slate-500 font-mono">No impact analysis yet</div>
                            </div>`;
                    }
                }

                card.innerHTML = `
                    <div class="flex items-center gap-2 text-[9px] text-violet-400 font-mono mb-3">
                        <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                        ${dateStr} · ${timeStr}
                    </div>
                    <h3 class="text-base font-bold text-slate-100 leading-snug mb-4">${escapeHtml(news.headline)}</h3>
                    <div class="bg-black/30 rounded-xl px-4 py-1">${stockRowsHtml}</div>
                `;
                container.appendChild(card);
            });
        }

        function renderMajorStocksView() {
            const container = document.getElementById('all-stocks-grid');
            if (!container) return;
            container.innerHTML = '';
            let allStocks = [];
            // Use published time
            // Filter by DB insertion time (created_at), NOT stale RSS dates
            const sevenDaysAgo = Date.now() - (168 * 60 * 60 * 1000);
            const recentNews = globalNewsData.filter(n => parseSQLiteDate(n.created_at).getTime() >= sevenDaysAgo);
            recentNews.forEach(news => {
                if (news.affected_stocks && news.affected_stocks.length > 0) {
                    news.affected_stocks.forEach(stock => {
                        allStocks.push({ ...stock, headline: news.headline });
                    });
                }
            });
            // Deduplicate by ticker — keep the entry with highest confidence_score
            const tickerMap = new Map();
            allStocks.forEach(stock => {
                const key = (stock.ticker || '').toUpperCase();
                if (!tickerMap.has(key)) {
                    tickerMap.set(key, stock);
                } else {
                    const existing = tickerMap.get(key);
                    if ((stock.confidence_score || 0) > (existing.confidence_score || 0)) {
                        tickerMap.set(key, stock);
                    }
                }
            });
            allStocks = Array.from(tickerMap.values());
            const stockCountEl = document.getElementById('stock-count');
            if (stockCountEl) stockCountEl.innerText = `${allStocks.length} Identified`;
            if (allStocks.length === 0) {
                container.innerHTML = '<div class="col-span-full text-center py-12 text-slate-400">No affected stocks found. The AI engine may still be processing live feeds.</div>';
                return;
            }
            allStocks.forEach(stock => {
                const impact = (stock.impact || '').toLowerCase();
                const isBull = impact.includes('bullish');
                const isSlightly = impact.includes('slightly');
                let colorClasses, textCol;
                if (impact === 'bullish') { colorClasses = "bg-green-900/20 border-green-500/40 shadow-[0_0_15px_rgba(34,197,94,0.1)]"; textCol = "text-green-400"; }
                else if (impact === 'slightly bullish') { colorClasses = "bg-emerald-900/15 border-emerald-500/30 shadow-[0_0_10px_rgba(52,211,153,0.08)]"; textCol = "text-emerald-400"; }
                else if (impact === 'slightly bearish') { colorClasses = "bg-orange-900/15 border-orange-500/30 shadow-[0_0_10px_rgba(251,146,60,0.08)]"; textCol = "text-orange-400"; }
                else { colorClasses = "bg-red-900/20 border-red-500/40 shadow-[0_0_15px_rgba(239,68,68,0.1)]"; textCol = "text-red-400"; }

                const bp = parseFloat(stock.base_price);
                const cp = parseFloat(stock.current_price);
                const hasPrice = !isNaN(bp) && bp > 0;
                const hasCurrent = !isNaN(cp) && cp > 0;
                // Stock signal change is based on news-time base price first;
                // fallback to market change only if signal diff is unavailable.
                const diffPct = (stock.diff_pct != null) ? stock.diff_pct
                    : (stock.market_change_pct != null) ? stock.market_change_pct
                    : (hasPrice && hasCurrent ? ((cp - bp) / bp * 100) : null);
                const diffPctStr = diffPct !== null ? (diffPct >= 0 ? '+' : '') + diffPct.toFixed(2) + '%' : '—';
                const diffColorCls = diffPct === null ? 'text-slate-400' : diffPct >= 0 ? 'text-green-400' : 'text-red-400';

                const statusBadge = getStatusBadge(stock.status);
                const card = document.createElement('div');
                card.className = `p-5 rounded-2xl border ${colorClasses} hover:scale-[1.01] transition-all cursor-default relative overflow-hidden`;
                card.innerHTML = `
                    <div class="flex justify-between items-start mb-3">
                        <div>
                            <h3 class="text-xl font-bold font-display text-white tracking-widest">${escapeHtml(stock.ticker)}</h3>
                            <span class="text-[9px] uppercase font-bold ${statusBadge.cls}">${statusBadge.text}</span>
                        </div>
                        <span class="text-[10px] uppercase font-bold px-2 py-1 rounded border ${textCol} border-current">${escapeHtml(stock.impact)}</span>
                    </div>
                    <div class="grid grid-cols-3 gap-2 text-xs font-mono mb-3 bg-black/30 rounded-lg p-3">
                        <div class="text-center">
                            <div class="text-slate-500 text-[9px] uppercase mb-1">At News</div>
                            <div class="text-slate-300 font-bold">${hasPrice ? '₹' + bp.toFixed(2) : '—'}</div>
                        </div>
                        <div class="text-center">
                            <div class="text-slate-500 text-[9px] uppercase mb-1">Current Price</div>
                            <div class="text-white font-bold">${hasCurrent ? '₹' + cp.toFixed(2) : '—'}</div>
                        </div>
                        <div class="text-center">
                            <div class="text-slate-500 text-[9px] uppercase mb-1">Change</div>
                            <div class="font-bold ${diffColorCls}">${diffPctStr}</div>
                        </div>
                    </div>
                    <p class="text-xs text-slate-300 leading-relaxed mb-3 line-clamp-2">${escapeHtml(stock.reason || 'Analyzing macro flow...')}</p>
                    <div class="flex items-center gap-1 text-[9px] text-slate-500 border-t border-white/5 pt-2">
                        <svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 12h6m-6 0h.01"/></svg>
                        <span class="line-clamp-1">${escapeHtml(stock.headline)}</span>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        function playInitialWelcome() {
            gsap.set(".glass-panel", { y: 20, opacity: 0 });
            gsap.set("nav", { y: -20, opacity: 0 });

            const tl = gsap.timeline();

            // Elegantly unblur and scale down the logo perfectly
            tl.to("#welcome-logo-container", { opacity: 1, scale: 1, filter: "blur(0px)", duration: 2.5, ease: "power2.out" })
                // Fade in the soft background glow
                .to("#welcome-glow", { opacity: 1, duration: 2 }, "-=2")
                // Let the orb ring pulse
                .to("#welcome-orb-ring", { scale: 1.5, opacity: 0, duration: 2, repeat: -1, ease: "power1.out" }, "-=2")
                // Fade in subtitle
                .to("#welcome-subtitle", { opacity: 1, y: -5, duration: 1.5, ease: "power2.out" }, "-=1.0")

                // Hold for a moment to let the user admire the logo
                .to({}, { duration: 1.0 })

                // Very slow, dreamy fade out of the entire overlay
                .to("#gsap-welcome", { opacity: 0, duration: 1.5, ease: "power2.inOut" })
                .set("#gsap-welcome", { display: "none" })

                // Dashboard fades in smoothly without sudden jerky staggers
                .to("nav", { y: 0, opacity: 1, duration: 1.5, ease: "power3.out" }, "-=1.0")
                .to(".glass-panel", { y: 0, opacity: 1, duration: 1.2, stagger: 0.1, ease: "power3.out" }, "-=1.2");
        }

        async function fetchIndices() {
            try {
                // T1.4: Same warm-fetch pattern as fetchLiveNews — first call
                // consumes the promise that was kicked off in <head>.
                let data;
                if (window.__alphaWarmFetches && window.__alphaWarmFetches.indices) {
                    data = await window.__alphaWarmFetches.indices;
                    window.__alphaWarmFetches.indices = null;
                }
                if (!data) {
                    const res = await fetch('/api/indices');
                    data = await res.json();
                }
                const container = document.getElementById('index-ticker');
                if (!container || !data.length) return;

                // Accent colors per index (up direction)
                const accents = ['#7c3aed', '#a78bfa', '#ff9f0a', '#00d26a'];
                const isLive = data[0]?.is_live ?? true;
                const marketStatus = data[0]?.market_status ?? 'Market Open';

                container.innerHTML = data.map((idx, i) => {
                    const hasQuote = idx.price !== null && idx.price !== undefined && idx.change_pct !== null && idx.change_pct !== undefined;
                    const up = hasQuote && idx.change_pct >= 0;
                    const accentColor = accents[i];
                    const bgGrad = `linear-gradient(135deg, ${accentColor}08 0%, transparent 60%)`;
                    // Always use real change_pct from backend (backend now computes it even when closed)
                    const changeVal = hasQuote ? idx.change_pct : null;
                    const pctText = changeVal !== null ? (changeVal > 0 ? '+' : '') + changeVal.toFixed(2) + '%' : '—';
                    // Color: green if up, red if down, amber only if exactly 0
                    const pctBg = changeVal === null
                        ? 'background:rgba(148,163,184,0.12);color:#94a3b8;border:1px solid rgba(148,163,184,0.3)'
                        : changeVal >= 0
                        ? 'background:rgba(74,222,128,0.12);color:#4ade80;border:1px solid rgba(74,222,128,0.3)'
                        : 'background:rgba(248,113,113,0.12);color:#f87171;border:1px solid rgba(248,113,113,0.3)';

                    const priceFmt = idx.price !== null
                        ? idx.price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                        : '—';

                    // Live dot vs closed label
                    const statusBadge = isLive
                        ? `<span class="live-dot w-1.5 h-1.5 rounded-full inline-block" style="background:${accentColor}"></span>`
                        : `<span class="text-[8px] font-bold tracking-wider px-1.5 py-0.5 rounded" style="background:rgba(100,116,139,0.15);color:#94a3b8;border:1px solid rgba(100,116,139,0.3)">CLOSED</span>`;

                    return `
                        <div class="index-card glass-panel rounded-2xl p-4 cursor-default"
                             style="--card-accent:${accentColor};background:${bgGrad};">
                            <div class="flex items-center justify-between mb-2">
                                <span class="text-[9px] font-bold tracking-[0.15em] text-slate-400 uppercase">${escapeHtml(idx.name)}</span>
                                ${statusBadge}
                            </div>
                            <div class="text-xl font-display font-black text-white tracking-tight mb-2">${priceFmt}</div>
                            <div class="flex items-center justify-between">
                                <div class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold" style="${pctBg}">
                                    ${changeVal === null ? '&mdash;' : changeVal > 0 ? '&#9650;' : changeVal < 0 ? '&#9660;' : '&mdash;'} ${pctText}
                                </div>
                                <div class="text-[8px] font-mono text-slate-500 uppercase">${!isLive ? marketStatus : ''}</div>
                            </div>
                        </div>`;
                }).join('');
            } catch (e) { console.error('Indices fetch failed', e); }
        }

        // Market-aware polling: faster during open hours, slower when closed
        function startSmartPolling() {
            // Initial fetch
            fetchLiveNews();
            fetchIndices();

            function schedulePolling() {
                // Re-check market status every tick
                const nowIST = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
                const day = nowIST.getDay(); // 0=Sun, 6=Sat
                const mins = nowIST.getHours() * 60 + nowIST.getMinutes();
                const isOpen = day >= 1 && day <= 5 && mins >= 555 && mins <= 930; // 9:15–15:30

                const newsInterval = isOpen ? 30000 : 300000;  // 30s open, 5 min closed
                const indexInterval = isOpen ? 30000 : 600000;  // 30s open, 10 min closed

                setTimeout(() => { fetchLiveNews(); scheduleNewsPolling(); }, newsInterval);
                setTimeout(() => { fetchIndices(); scheduleIndexPolling(); }, indexInterval);
            }

            function scheduleNewsPolling() {
                const nowIST = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
                const day = nowIST.getDay();
                const mins = nowIST.getHours() * 60 + nowIST.getMinutes();
                const isOpen = day >= 1 && day <= 5 && mins >= 555 && mins <= 930;
                setTimeout(() => { fetchLiveNews(); scheduleNewsPolling(); }, isOpen ? 30000 : 120000);
            }

            function scheduleIndexPolling() {
                const nowIST = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
                const day = nowIST.getDay();
                const mins = nowIST.getHours() * 60 + nowIST.getMinutes();
                const isOpen = day >= 1 && day <= 5 && mins >= 555 && mins <= 930;
                setTimeout(() => { fetchIndices(); updateWatchlistPrices(); scheduleIndexPolling(); }, isOpen ? 30000 : 60000);
            }

            // Kick off independent polling loops
            scheduleNewsPolling();
            scheduleIndexPolling();
        }

        window.onload = () => {
            maybeShowOnboarding();          // #3 cinematic onboarding (first session per tab)
            playInitialWelcome();
            checkAuthStatus();
            startSmartPolling();
            renderWatchlistPanel();
            updatePortfolioAssistantState();
            updateWatchlistPrices();
            initPremiumFeatures();
            installCardFlipHandlers();      // #6 3D card flip on long-hover
        };

        // T2.12: Three.js particle background removed. The container it rendered
        // into was display:none — the aurora-mesh CSS layer handles the visible
        // background. Saves ~150 KB on every page load + a continuous 60fps
        // animation loop that was running for an invisible canvas.

        // ══════════════════════════════════════════════════════════════
        // PREMIUM FEATURES: Ticker Bar, Signal Terminal, Track Record, Toasts
        // ══════════════════════════════════════════════════════════════

        let _terminalData = [];
        let _terminalSort = { key: 'confidence', asc: false };
        let _terminalFilter = 'all';
        let _lastNotifId = 0;

        function initPremiumFeatures() {
            updateTickerBar();
            setInterval(updateTickerBar, 30000);
            pollSignalNotifications();
            setInterval(pollSignalNotifications, 30000);
            initPremiumInteractions();
            // v2 premium UI features (#9 trail, #11 parallax, #19 live-pulse intensity)
            initCursorTrail();
            initKpiParallax();
            initLivePulseIntensity();
        }

        // ══════════════════════════════════════════════════════════════
        // #3 — Cinematic onboarding ceremony
        // Shown once per session (sessionStorage flag), letter-by-letter
        // ALPHA LENS reveal, then fades out and removes itself.
        // ══════════════════════════════════════════════════════════════
        function maybeShowOnboarding() {
            // Skip if user already saw it this session or prefers reduced motion
            if (sessionStorage.getItem('al_onboarded') === '1') return;
            if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                sessionStorage.setItem('al_onboarded', '1');
                return;
            }
            const overlay = document.getElementById('onboarding-overlay');
            const wordmark = document.getElementById('ob-wordmark');
            if (!overlay || !wordmark) return;

            const text = 'ALPHA LENS';
            wordmark.innerHTML = [...text].map((ch, i) => {
                if (ch === ' ') return '<span class="ob-space"></span>';
                return `<span class="ob-char" style="--char-i:${i}">${ch}</span>`;
            }).join('');
            overlay.classList.remove('hidden');
            overlay.setAttribute('aria-hidden', 'false');

            // Auto-dismiss after the animation finishes (~2.8s)
            setTimeout(dismissOnboarding, 2800);
        }
        function dismissOnboarding() {
            const overlay = document.getElementById('onboarding-overlay');
            if (!overlay) return;
            overlay.classList.add('fade-out');
            sessionStorage.setItem('al_onboarded', '1');
            setTimeout(() => { overlay.classList.add('hidden'); }, 700);
        }

        // ══════════════════════════════════════════════════════════════
        // #9 — Cursor glow trail
        // Soft champagne orb follows the cursor with rAF interpolation
        // so it lags slightly behind (premium "trailing" feel).
        // ══════════════════════════════════════════════════════════════
        function initCursorTrail() {
            const orb = document.getElementById('cursor-glow');
            if (!orb) return;
            if (window.matchMedia('(hover: none)').matches) return;  // mobile/touch — skip
            let tx = -9999, ty = -9999, cx = -9999, cy = -9999;
            let active = false;
            const half = 140; // half of orb size

            window.addEventListener('pointermove', (e) => {
                tx = e.clientX; ty = e.clientY;
                if (!active) {
                    active = true;
                    orb.classList.add('show');
                }
            }, { passive: true });

            window.addEventListener('pointerleave', () => {
                active = false;
                orb.classList.remove('show');
            }, { passive: true });

            function loop() {
                // Lerp — smoother than direct follow
                cx += (tx - cx) * 0.16;
                cy += (ty - cy) * 0.16;
                orb.style.transform = `translate3d(${cx - half}px, ${cy - half}px, 0)`;
                requestAnimationFrame(loop);
            }
            requestAnimationFrame(loop);
        }

        // ══════════════════════════════════════════════════════════════
        // #11 — Scroll-linked parallax on Track Record KPI cards
        // Sets --scroll-y CSS var on #tr-kpi-row based on its offset
        // within the viewport. CSS handles per-card multiplier.
        // ══════════════════════════════════════════════════════════════
        function initKpiParallax() {
            const row = document.getElementById('tr-kpi-row');
            if (!row) return;
            if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
            let ticking = false;
            function updateParallax() {
                ticking = false;
                const rect = row.getBoundingClientRect();
                // Distance from viewport center (positive = below center, negative = above)
                const vhCenter = window.innerHeight / 2;
                const rowCenter = rect.top + rect.height / 2;
                const offset = rowCenter - vhCenter;
                // Clamp so the effect doesn't go crazy at extremes
                const clamped = Math.max(-400, Math.min(400, offset));
                row.style.setProperty('--scroll-y', clamped);
            }
            window.addEventListener('scroll', () => {
                if (!ticking) {
                    requestAnimationFrame(updateParallax);
                    ticking = true;
                }
            }, { passive: true });
            updateParallax();
        }

        // ══════════════════════════════════════════════════════════════
        // #19 — Dynamic LIVE badge pulse intensity
        // Polls /api/debug-worker-status every 30s; if a worker cycle
        // started within the last 60s, mark all .live-dot elements as
        // "hot" (brighter+faster pulse). Otherwise "stale".
        // ══════════════════════════════════════════════════════════════
        function initLivePulseIntensity() {
            async function tick() {
                try {
                    const r = await fetch('/api/debug-worker-status');
                    if (!r.ok) return;
                    const data = await r.json();
                    const aiAge = data?.workers?.ai_news?.last_cycle_started_age_s;
                    const yfAge = data?.workers?.yfinance?.last_cycle_finished_age_s;
                    // "Hot" if either worker has been recently active
                    const hot = (aiAge != null && aiAge < 180) || (yfAge != null && yfAge < 90);
                    const stale = !hot && (
                        (aiAge != null && aiAge > 600) || (yfAge != null && yfAge > 600)
                    );
                    document.querySelectorAll('.live-dot').forEach(el => {
                        el.classList.toggle('is-hot', hot);
                        el.classList.toggle('is-stale', stale);
                    });
                } catch (e) {/* swallow — non-critical */}
            }
            tick();
            setInterval(tick, 30000);
        }

        // ══════════════════════════════════════════════════════════════
        // #7 — Animated equity-curve hero on Track Record
        // Builds a synthetic equity curve from the closed signals' P&L
        // and animates the SVG path drawing itself.
        // ══════════════════════════════════════════════════════════════
        function renderEquityCurveHero(closedSignals) {
            const host = document.getElementById('tr-equity-curve');
            if (!host) return;
            if (!Array.isArray(closedSignals) || closedSignals.length < 2) {
                host.innerHTML = '';
                return;
            }
            // Cumulative P&L over time (chronological order)
            const chrono = [...closedSignals]
                .filter(r => typeof r.pnl_pct === 'number')
                .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
            if (chrono.length < 2) { host.innerHTML = ''; return; }
            let cum = 0;
            const values = chrono.map(r => { cum += r.pnl_pct; return cum; });
            const w = 800, h = 64, pad = 4;
            const min = Math.min(0, ...values), max = Math.max(0, ...values);
            const span = (max - min) || 1;
            const step = (w - pad * 2) / (values.length - 1);
            const pts = values.map((v, i) => {
                const x = pad + i * step;
                const y = pad + (h - pad * 2) * (1 - (v - min) / span);
                return [x, y];
            });
            const path = pts.map(([x, y], i) => (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1)).join(' ');
            const fillPath = `${path} L${pts[pts.length-1][0].toFixed(1)},${h - pad} L${pad},${h - pad} Z`;
            host.innerHTML = `
                <svg class="eq-curve-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
                    <defs>
                        <linearGradient id="eq-curve-gradient" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stop-color="rgba(201,169,98,0.3)" />
                            <stop offset="100%" stop-color="rgba(201,169,98,0)" />
                        </linearGradient>
                    </defs>
                    <path class="eq-curve-fill" d="${fillPath}" />
                    <path class="eq-curve-path" d="${path}" />
                </svg>
            `;
            // Trigger draw animation next frame
            requestAnimationFrame(() => {
                requestAnimationFrame(() => host.classList.add('is-revealed'));
            });
        }

        // ══════════════════════════════════════════════════════════════
        // #6 — 3D signal card flip
        // Click news card's "Why?" button (or long-hover) toggles flip.
        // ══════════════════════════════════════════════════════════════
        // Hover-debounced flip: a card flips after 700ms of sustained hover,
        // un-flips on pointer leave. Avoids accidental flips on quick swipes.
        function installCardFlipHandlers() {
            if (window.__alpha_card_flip_init) return;
            window.__alpha_card_flip_init = true;
            let hoverTimer = null, activeCard = null;
            document.addEventListener('pointerover', (e) => {
                const card = e.target.closest('.news-card-hover');
                if (!card || !card.querySelector('.nc-back')) return;
                if (activeCard && activeCard !== card) {
                    activeCard.removeAttribute('data-flip');
                }
                activeCard = card;
                clearTimeout(hoverTimer);
                hoverTimer = setTimeout(() => card.setAttribute('data-flip', '1'), 700);
            });
            document.addEventListener('pointerout', (e) => {
                const card = e.target.closest('.news-card-hover');
                if (!card || card.contains(e.relatedTarget)) return;
                clearTimeout(hoverTimer);
                card.removeAttribute('data-flip');
                if (activeCard === card) activeCard = null;
            });
        }

        // ══════════════════════════════════════════════════════════════
        // #20 — Typing reveal for AI verdict in drawer
        // Replaces a verdict element's text with per-character animated spans.
        // ══════════════════════════════════════════════════════════════
        function typeReveal(el, text) {
            if (!el) return;
            if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                el.textContent = text;
                return;
            }
            const chars = [...text].map((c, i) =>
                `<span class="sd-verdict-char" style="--char-i:${i}">${c === ' ' ? '&nbsp;' : c}</span>`
            ).join('');
            el.innerHTML = `<span class="sd-verdict-text">${chars}</span><span class="sd-caret"></span>`;
            // Remove caret after typing finishes
            const total = text.length * 40 + 400;
            setTimeout(() => {
                const caret = el.querySelector('.sd-caret');
                if (caret) caret.remove();
            }, total);
        }

        function initPremiumInteractions() {
            if (window.__alphaPremiumInteractionsReady) return;
            window.__alphaPremiumInteractionsReady = true;
            const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

            window.addEventListener('pointermove', (event) => {
                const x = Math.round((event.clientX / window.innerWidth) * 100);
                const y = Math.round((event.clientY / window.innerHeight) * 100);
                document.documentElement.style.setProperty('--spotlight-x', `${x}%`);
                document.documentElement.style.setProperty('--spotlight-y', `${y}%`);
            }, { passive: true });

            document.addEventListener('pointermove', (event) => {
                if (reducedMotion) return;

                // Existing parallax tilt for hero/insight tiles
                const card = event.target.closest('.premium-hero, .headline-tile, .insight-tile, .index-card');
                if (card) {
                    const rect = card.getBoundingClientRect();
                    const px = ((event.clientX - rect.left) / rect.width) - 0.5;
                    const py = ((event.clientY - rect.top) / rect.height) - 0.5;
                    card.style.transform = `perspective(900px) rotateX(${py * -2.2}deg) rotateY(${px * 2.2}deg) translateY(-2px)`;
                }

                // UI-11: per-panel cursor spotlight — set --px/--py in pixels on every panel under the cursor
                const panel = event.target.closest('.glass-panel');
                if (panel) {
                    const pRect = panel.getBoundingClientRect();
                    panel.style.setProperty('--px', (event.clientX - pRect.left) + 'px');
                    panel.style.setProperty('--py', (event.clientY - pRect.top) + 'px');
                }

                // UI-2: magnetic buttons — translate toward cursor when within range
                const mag = event.target.closest('[data-magnetic]');
                if (mag) {
                    const mRect = mag.getBoundingClientRect();
                    const cx = mRect.left + mRect.width / 2;
                    const cy = mRect.top + mRect.height / 2;
                    const dx = event.clientX - cx;
                    const dy = event.clientY - cy;
                    const dist = Math.hypot(dx, dy);
                    const radius = Math.max(mRect.width, mRect.height) * 0.9 + 20;
                    if (dist < radius) {
                        const strength = Math.min(1, (radius - dist) / radius);
                        const pull = 0.32 * strength; // max ~32% of distance
                        mag.style.setProperty('--mx', (dx * pull) + 'px');
                        mag.style.setProperty('--my', (dy * pull) + 'px');
                        mag.classList.add('is-pulling');
                    }
                }
            }, { passive: true });

            document.addEventListener('pointerout', (event) => {
                const card = event.target.closest('.premium-hero, .headline-tile, .insight-tile, .index-card');
                if (card) card.style.transform = '';
                const mag = event.target.closest('[data-magnetic]');
                if (mag && !mag.contains(event.relatedTarget)) {
                    mag.classList.remove('is-pulling');
                    mag.style.setProperty('--mx', '0px');
                    mag.style.setProperty('--my', '0px');
                }
            }, { passive: true });

            // UI-8: pill morph — radial flash on click for sector pills and range buttons
            document.addEventListener('click', (event) => {
                const pill = event.target.closest('.sector-pill, .tr-range-btn');
                if (!pill) return;
                pill.classList.remove('pill-flash');
                // restart animation
                void pill.offsetWidth;
                pill.classList.add('pill-flash');
                setTimeout(() => pill.classList.remove('pill-flash'), 600);
            });

            // UI-7 + UI-5: ticker hover preview and click-to-open drawer (delegated)
            installTickerInteractions();
        }

        // ══════════════════════════════════════════════════════════════
        // UI-1: STAGGER HELPER
        // Tag each child with data-stagger-i = "<index>" up to a cap so
        // the CSS waterfall keeps a tasteful total duration.
        // ══════════════════════════════════════════════════════════════
        function applyStagger(rootEl, selector = ':scope > *', cap = 12) {
            if (!rootEl) return;
            const items = rootEl.querySelectorAll(selector);
            items.forEach((el, idx) => {
                el.style.setProperty('--i', Math.min(idx, cap));
                // Force re-trigger animation on re-render
                el.removeAttribute('data-stagger-i');
                requestAnimationFrame(() => el.setAttribute('data-stagger-i', String(idx)));
            });
        }

        // ══════════════════════════════════════════════════════════════
        // UI-4: SKELETON → CONTENT smooth swap
        // Wraps the "replace innerHTML with real content" pattern with
        // a brief leave/enter fade.
        // ══════════════════════════════════════════════════════════════
        function smoothSwap(containerEl, newHtml, opts = {}) {
            if (!containerEl) return;
            const stagger = opts.stagger; // optional selector to stagger after swap
            const apply = () => {
                containerEl.classList.remove('ui-swap-leaving');
                containerEl.classList.add('ui-swap-entering');
                containerEl.innerHTML = newHtml;
                if (stagger) applyStagger(containerEl, stagger, opts.staggerCap || 12);
                setTimeout(() => containerEl.classList.remove('ui-swap-entering'), 360);
            };
            if (containerEl.dataset.uiSwapInit !== '1') {
                containerEl.classList.add('ui-swap');
                containerEl.dataset.uiSwapInit = '1';
            }
            // If the container is showing skeletons or content, fade them out first.
            containerEl.classList.add('ui-swap-leaving');
            setTimeout(apply, 180);
        }

        // ══════════════════════════════════════════════════════════════
        // UI-3: DIGIT FLIP
        // flipNumber(el, newStr) — replaces text but animates each digit
        // that actually changed. Safe for prices ("₹1,242.50") and pcts.
        // ══════════════════════════════════════════════════════════════
        function _digitsOf(str) {
            // Wrap each char in a span so we can selectively flip changed digits.
            return [...String(str)].map(ch => `<span class="flip-digit">${ch === ' ' ? '&nbsp;' : ch}</span>`).join('');
        }
        function flipNumber(el, newStr) {
            if (!el) return;
            if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                el.textContent = newStr;
                return;
            }
            if (!el.classList.contains('flip-num')) {
                el.classList.add('flip-num');
                el.innerHTML = _digitsOf(newStr);
                return;
            }
            const oldDigits = el.querySelectorAll('.flip-digit');
            const newChars = [...String(newStr)];
            // If length changed, do a full rerender with all flipping
            if (oldDigits.length !== newChars.length) {
                el.innerHTML = _digitsOf(newStr);
                el.querySelectorAll('.flip-digit').forEach(d => {
                    d.classList.add('flipping');
                    setTimeout(() => d.classList.remove('flipping'), 380);
                });
                return;
            }
            // Same length — flip only the digits that changed
            oldDigits.forEach((d, i) => {
                const newCh = newChars[i] === ' ' ? ' ' : newChars[i];
                if (d.textContent === newCh) return;
                d.classList.add('flipping');
                // Swap mid-flip when the rotation is near 90°
                setTimeout(() => { d.textContent = newCh; }, 180);
                setTimeout(() => d.classList.remove('flipping'), 380);
            });
        }

        // ══════════════════════════════════════════════════════════════
        // UI-7: TICKER HOVER PREVIEW + UI-5: STOCK DRAWER
        // Delegated handlers — any element with .ticker-hover-target
        // and a data-ticker attribute participates.
        // ══════════════════════════════════════════════════════════════
        let _tickerHoverTimer = null;
        function _findStockSignalsInGlobal(tickerBase) {
            const matches = [];
            if (!Array.isArray(globalNewsData)) return matches;
            for (const n of globalNewsData) {
                if (!n.affected_stocks) continue;
                for (const s of n.affected_stocks) {
                    if ((s.ticker || '').toUpperCase().replace(/\.(NS|BO)$/i, '') === tickerBase) {
                        matches.push({ ...s, headline: n.headline, news_time: n.news_time, created_at: n.created_at });
                        break;
                    }
                }
                if (matches.length >= 5) break;
            }
            return matches;
        }
        function _miniSparkSvg(values) {
            if (!values || values.length < 2) return '';
            const w = 240, h = 36, pad = 2;
            const min = Math.min(...values), max = Math.max(...values);
            const span = max - min || 1;
            const step = (w - pad * 2) / (values.length - 1);
            const path = values.map((v, i) => {
                const x = pad + i * step;
                const y = pad + (h - pad * 2) * (1 - (v - min) / span);
                return (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
            }).join(' ');
            const last = values[values.length - 1], first = values[0];
            const stroke = last >= first ? '#10D98C' : '#FF3366';
            return `<svg class="thc-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
                <path d="${path}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>`;
        }
        function positionHoverCard(card, anchor) {
            const aRect = anchor.getBoundingClientRect();
            const cRect = card.getBoundingClientRect();
            const margin = 8;
            let left = aRect.left + (aRect.width - cRect.width) / 2;
            let top = aRect.top - cRect.height - margin;
            if (top < 12) top = aRect.bottom + margin; // flip below if no room above
            left = Math.max(8, Math.min(window.innerWidth - cRect.width - 8, left));
            card.style.left = left + 'px';
            card.style.top = top + 'px';
        }
        function _showTickerHover(targetEl) {
            const card = document.getElementById('ticker-hover-card');
            if (!card) return;
            const ticker = targetEl.dataset.ticker || (targetEl.textContent || '').trim().toUpperCase();
            const base = ticker.replace(/\.(NS|BO)$/i, '');
            const signals = _findStockSignalsInGlobal(base);
            const latest = signals[0];
            const verdict = latest ? (latest.impact || 'analysis pending') : null;
            const conf = latest ? (latest.confidence_score || '—') : '—';
            const bp = latest ? parseFloat(latest.base_price) : NaN;
            const cp = latest ? parseFloat(latest.current_price) : NaN;
            const hasP = !isNaN(bp) && bp > 0;
            const hasC = !isNaN(cp) && cp > 0;
            const priceStr = hasC ? '₹' + cp.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : '—';
            const diff = hasP && hasC ? ((cp - bp) / bp * 100) : null;
            const diffStr = diff != null ? (diff >= 0 ? '+' : '') + diff.toFixed(2) + '%' : '—';
            const diffCls = diff == null ? 'tr-muted' : (diff >= 0 ? 'tr-pos' : 'tr-neg');
            const verdictCls = !verdict ? 'tr-muted' : (verdict.toLowerCase().includes('bull') ? 'thc-bull' : 'thc-bear');
            const verdictStyle = !verdict ? 'border-color:var(--border);color:var(--text-tertiary)' :
                (verdict.toLowerCase().includes('bull') ? 'border-color:var(--green-border);color:var(--green);background:var(--green-dim)' :
                                                          'border-color:var(--red-border);color:var(--red);background:var(--red-dim)');
            // tiny synthetic spark from the few signals we have
            const sparkVals = signals.filter(s => parseFloat(s.current_price) > 0).map(s => parseFloat(s.current_price)).reverse();
            const spark = sparkVals.length >= 2 ? _miniSparkSvg(sparkVals) : '';
            card.innerHTML = `
                <div class="thc-row">
                    <span class="thc-ticker">${escapeHtml(base)}</span>
                    <span class="thc-price ${diffCls}">${priceStr}</span>
                </div>
                <div class="thc-row" style="margin-top:6px">
                    <span class="thc-verdict" style="${verdictStyle}">${verdict ? escapeHtml(verdict.toUpperCase()) : 'NO VERDICT YET'}</span>
                    <span class="thc-chg ${diffCls}">${diffStr}</span>
                </div>
                ${spark}
                <div class="thc-meta">${latest ? `Conf ${conf} · click for full drawer` : 'click for full drawer'}</div>
            `;
            card.setAttribute('aria-hidden', 'false');
            card.classList.add('show');
            positionHoverCard(card, targetEl);
        }
        function _hideTickerHover() {
            const card = document.getElementById('ticker-hover-card');
            if (!card) return;
            card.classList.remove('show');
            card.setAttribute('aria-hidden', 'true');
        }

        function installTickerInteractions() {
            document.addEventListener('pointerover', (e) => {
                const tgt = e.target.closest('.ticker-hover-target');
                if (!tgt) return;
                clearTimeout(_tickerHoverTimer);
                _tickerHoverTimer = setTimeout(() => _showTickerHover(tgt), 120);
            });
            document.addEventListener('pointerout', (e) => {
                const tgt = e.target.closest('.ticker-hover-target');
                if (!tgt) return;
                if (tgt.contains(e.relatedTarget)) return;
                clearTimeout(_tickerHoverTimer);
                _hideTickerHover();
            });
            document.addEventListener('click', (e) => {
                const tgt = e.target.closest('.ticker-hover-target');
                if (!tgt) return;
                const ticker = tgt.dataset.ticker || (tgt.textContent || '').trim();
                if (!ticker) return;
                _hideTickerHover();
                openStockDrawer(ticker);
            });
        }

        async function openStockDrawer(ticker) {
            const drawer = document.getElementById('stock-drawer');
            const backdrop = document.getElementById('stock-drawer-backdrop');
            if (!drawer || !backdrop) return;
            const base = String(ticker).toUpperCase().replace(/\.(NS|BO)$/i, '');
            const fullTicker = ticker.toUpperCase().includes('.') ? ticker.toUpperCase() : (base + '.NS');

            document.getElementById('sd-ticker').textContent = base;
            document.getElementById('sd-name').textContent = 'Loading…';
            document.getElementById('sd-body').innerHTML = '<div class="sd-empty">Loading…</div>';

            drawer.classList.add('show');
            drawer.setAttribute('aria-hidden', 'false');
            backdrop.classList.add('show');
            document.body.style.overflow = 'hidden';

            // Pull recent signals + headline matches from local data
            const signals = _findStockSignalsInGlobal(base);
            const latest = signals[0];
            const wlEntry = (Array.isArray(watchlist) ? watchlist : []).find(s => tickerSymbol(s.ticker) === base);
            const displayName = wlEntry?.name || base;
            document.getElementById('sd-name').textContent = displayName;

            // Try to get a live price
            let price = null, chg = null;
            try {
                const r = await fetch('/api/stock-price/' + encodeURIComponent(fullTicker));
                if (r.ok) {
                    const d = await r.json();
                    price = d.price; chg = d.change_pct;
                }
            } catch (e) { /* swallow — drawer still useful without */ }

            // Fundamentals from latest signal if backend hasn't sent any
            const priceStr = (price != null && price > 0) ? '₹' + Number(price).toLocaleString('en-IN', { maximumFractionDigits: 2 }) :
                              (latest && parseFloat(latest.current_price) > 0 ? '₹' + parseFloat(latest.current_price).toFixed(2) : '—');
            const chgVal = (chg != null) ? Number(chg) : (latest && latest.diff_pct != null ? Number(latest.diff_pct) : null);
            const chgStr = chgVal != null ? (chgVal >= 0 ? '+' : '') + chgVal.toFixed(2) + '%' : '—';
            const chgCls = chgVal == null ? 'tr-muted' : (chgVal >= 0 ? 'tr-pos' : 'tr-neg');

            const newsHtml = signals.length === 0
                ? '<div class="sd-empty">No saved news currently flagged for this stock.</div>'
                : signals.slice(0, 5).map(s => {
                    const impact = (s.impact || '').toUpperCase();
                    const impactCls = impact.includes('BULL') ? 'tr-pos' : (impact.includes('BEAR') ? 'tr-neg' : 'tr-muted');
                    return `<div class="sd-news-item">
                        <div class="h">${escapeHtml(s.headline || '')}</div>
                        <div class="m"><span class="${impactCls}">${escapeHtml(impact || 'pending')}</span> · conf ${s.confidence_score || '—'} · ${escapeHtml(s.status || '')}</div>
                    </div>`;
                }).join('');

            // #20 Typing reveal — pull the headline AI verdict (latest signal's impact)
            // and surface it as a typed-out badge above the news list.
            const latestImpact = latest && latest.impact ? latest.impact.toUpperCase() : null;
            const verdictBlock = latestImpact ? `
                <div class="sd-section">
                    <div class="sd-section-label">AI Verdict</div>
                    <div class="sd-price-row">
                        <span class="sd-verdict ${latestImpact.includes('BULL') ? 'tr-pos' : (latestImpact.includes('BEAR') ? 'tr-neg' : 'tr-muted')}"
                              style="font-family:'Space Grotesk',sans-serif;font-weight:900;font-size:22px;letter-spacing:-0.02em;"
                              id="sd-verdict-target"></span>
                    </div>
                </div>
            ` : '';

            document.getElementById('sd-body').innerHTML = `
                <div class="sd-section">
                    <div class="sd-section-label">Live Quote</div>
                    <div class="sd-price-row">
                        <span class="sd-price">${priceStr}</span>
                        <span class="sd-chg ${chgCls}">${chgStr}</span>
                    </div>
                </div>
                ${verdictBlock}
                <div class="sd-section">
                    <div class="sd-section-label">AI Signals (recent)</div>
                    ${newsHtml}
                </div>
                ${wlEntry ? '' : `<div class="sd-section">
                    <div class="sd-section-label">Watchlist</div>
                    <button class="tr-range-btn" data-magnetic onclick="addStockToWatchlist('${escapeHtml(fullTicker)}', '${escapeHtml(displayName)}'); closeStockDrawer();">+ Add to Watchlist</button>
                </div>`}
            `;

            // Fire the typing animation AFTER the DOM is updated, with a small
            // delay so it lines up with the drawer's slide-in finishing.
            if (latestImpact) {
                setTimeout(() => {
                    const tgt = document.getElementById('sd-verdict-target');
                    if (tgt) typeReveal(tgt, latestImpact);
                }, 200);
            }
        }

        function closeStockDrawer() {
            const drawer = document.getElementById('stock-drawer');
            const backdrop = document.getElementById('stock-drawer-backdrop');
            if (!drawer || !backdrop) return;
            drawer.classList.remove('show');
            drawer.setAttribute('aria-hidden', 'true');
            backdrop.classList.remove('show');
            document.body.style.overflow = '';
        }
        // Esc closes drawer; click-outside via backdrop also closes
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                const drawer = document.getElementById('stock-drawer');
                if (drawer && drawer.classList.contains('show')) closeStockDrawer();
            }
        });
        document.addEventListener('click', (e) => {
            if (e.target && e.target.id === 'stock-drawer-backdrop') closeStockDrawer();
        });

        // ── LIVE TICKER BAR ──
        async function updateTickerBar() {
            try {
                const res = await fetch('/api/indices');
                const data = await res.json();
                if (!Array.isArray(data)) return;
                const map = {};
                data.forEach(d => { map[d.name] = d; });
                const pairs = [
                    ['NIFTY 50',     'pt-nifty', 'ptc-nifty', 'pt-nifty2', 'ptc-nifty2'],
                    ['BANK NIFTY',   'pt-bank',  'ptc-bank',  'pt-bank2',  'ptc-bank2'],
                    ['SENSEX',       'pt-sensex','ptc-sensex','pt-sensex2','ptc-sensex2'],
                    ['MIDCAP NIFTY', 'pt-mid',   'ptc-mid',   'pt-mid2',   'ptc-mid2'],
                ];
                let regime = 'neutral';
                pairs.forEach(([name, pId, cId, pId2, cId2]) => {
                    const d = map[name];
                    if (!d) return;
                    const rawPrice = d.price ?? d.last_price;
                    const price = rawPrice != null ? Number(rawPrice).toLocaleString('en-IN',{maximumFractionDigits:1}) : '—';
                    const chg = d.change_pct != null ? Number(d.change_pct) : 0;
                    const chgStr = (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';
                    const cls = chg > 0.1 ? 'up' : (chg < -0.1 ? 'dn' : 'flat');
                    // UI-3: flip each digit that actually changed instead of jumping the whole string
                    [pId, pId2].forEach(id => { const el = document.getElementById(id); if(el) flipNumber(el, price); });
                    [cId, cId2].forEach(id => { const el = document.getElementById(id); if(el) { flipNumber(el, chgStr); el.classList.add('ptick-chg'); el.classList.remove('up','dn','flat'); el.classList.add(cls); }});
                    if (name === 'NIFTY 50') regime = chg > 0.3 ? 'risk-on' : (chg < -0.3 ? 'risk-off' : 'neutral');
                });
                const badge = document.getElementById('regime-badge');
                const txt = document.getElementById('regime-text');
                if (badge) badge.className = regime;
                if (txt) txt.textContent = regime === 'risk-on' ? 'RISK ON ▲' : (regime === 'risk-off' ? 'RISK OFF ▼' : 'NEUTRAL ◆');
            } catch(e) { console.log('Ticker update error', e); }
        }

        // ── SIGNAL TERMINAL ──
        async function fetchTerminalData() {
            try {
                const res = await fetch('/api/signal-terminal');
                const data = await res.json();
                _terminalData = data.signals || [];
                document.getElementById('terminal-count').textContent = _terminalData.length + ' signals';
                renderTerminal();
            } catch(e) { console.log('Terminal fetch error', e); }
        }

        function setTerminalFilter(f) {
            _terminalFilter = f;
            document.querySelectorAll('#view-terminal .sector-pill').forEach(b => b.classList.remove('active'));
            const btn = document.getElementById('tf-' + f);
            if (btn) btn.classList.add('active');
            renderTerminal();
        }

        function sortTerminal(key) {
            if (_terminalSort.key === key) _terminalSort.asc = !_terminalSort.asc;
            else { _terminalSort.key = key; _terminalSort.asc = key === 'ticker'; }
            document.querySelectorAll('.terminal-table thead th').forEach(th => th.classList.remove('sort-active'));
            renderTerminal();
        }

        function renderTerminal() {
            let filtered = [..._terminalData];
            if (_terminalFilter === 'active') filtered = filtered.filter(s => s.status === 'Active View');
            else if (_terminalFilter === 'bullish') filtered = filtered.filter(s => s.direction === 'BULLISH');
            else if (_terminalFilter === 'bearish') filtered = filtered.filter(s => s.direction === 'BEARISH');
            else if (_terminalFilter === 'high') filtered = filtered.filter(s => s.confidence >= 80);

            const k = _terminalSort.key;
            filtered.sort((a, b) => {
                let va = a[k], vb = b[k];
                if (typeof va === 'string') { va = va.toLowerCase(); vb = (vb||'').toLowerCase(); }
                if (va < vb) return _terminalSort.asc ? -1 : 1;
                if (va > vb) return _terminalSort.asc ? 1 : -1;
                return 0;
            });

            const tbody = document.getElementById('terminal-body');
            if (!filtered.length) {
                tbody.innerHTML = '<tr><td colspan="9" class="text-center py-10 text-slate-500">No signals match filter</td></tr>';
                return;
            }
            tbody.innerHTML = filtered.map((s, idx) => {
                const dirCls = s.direction === 'BULLISH' ? 'dir-bull' : 'dir-bear';
                const dirIcon = s.direction === 'BULLISH' ? '▲' : '▼';
                const confCls = s.confidence >= 80 ? 'conf-high' : (s.confidence >= 60 ? 'conf-mid' : 'conf-low');
                const pctCls = s.diff_pct > 0 ? 'dir-bull' : (s.diff_pct < 0 ? 'dir-bear' : 'text-slate-400');
                const pctStr = (s.diff_pct >= 0 ? '+' : '') + s.diff_pct.toFixed(2) + '%';
                const progW = Math.min(100, Math.max(0, Math.abs(s.progress_pct)));
                const progColor = s.progress_pct >= 0 ? '#10b981' : '#f43f5e';
                let stCls = 'status-active', stTxt = 'Active';
                if (s.status === 'Predicted Target Hit') { stCls = 'status-hit'; stTxt = '✅ Target'; }
                else if (s.status === 'Stop Loss Hit') { stCls = 'status-stop'; stTxt = '❌ Stopped'; }
                else if (s.status === 'Expired') { stCls = 'status-expired'; stTxt = '⏱ Expired'; }
                const ticker = (s.ticker||'').replace('.NS','').replace('.BO','');
                const isHigh = s.confidence >= 85;
                const rowBg = isHigh ? 'background:rgba(245,158,11,0.03);' : '';
                const staggerI = Math.min(idx, 12);
                return `<tr data-stagger-i="${idx}" style="${rowBg}--i:${staggerI};">
                    <td><span class="ticker-hover-target font-display font-bold text-white text-sm" data-ticker="${escapeHtml(s.ticker || ticker)}">${ticker}</span>${isHigh?'<span class="ml-1" title="High Conviction">⭐</span>':''}</td>
                    <td><span class="${dirCls} text-xs">${dirIcon} ${s.direction}</span></td>
                    <td><div class="conf-ring ${confCls}">${s.confidence}</div></td>
                    <td class="text-slate-300 font-mono text-xs">₹${s.entry.toLocaleString('en-IN')}</td>
                    <td class="text-white font-mono text-xs">₹${s.current.toLocaleString('en-IN')}</td>
                    <td class="${pctCls} font-mono font-bold text-xs">${pctStr}</td>
                    <td>
                        <div class="flex items-center gap-2">
                            <div class="progress-track" style="margin-bottom:0; flex-shrink:0;">
                                <div class="progress-fill" style="width:${progW}%;background:${progColor}"></div>
                            </div>
                            <span class="text-[10px] font-mono font-bold" style="color:${progColor}; min-width:65px;">
                                ${progW.toFixed(0)}% ${s.progress_pct >= 0 ? 'Tgt' : 'Stop'}
                            </span>
                        </div>
                    </td>
                    <td><span class="${stCls} text-xs font-bold">${stTxt}</span></td>
                    <td class="text-slate-500 text-[11px] max-w-[200px] truncate cursor-pointer hover:text-violet-400 hover:underline transition-colors" title="${escapeHtml(s.headline)}" onclick="openSignalNews(this.title)">${escapeHtml(s.headline)}</td>
                </tr>`;
            }).join('');
        }

        function openSignalNews(headlineText) {
            if (!headlineText) return;
            const targetKey = headlineText.trim().toLowerCase();
            const matchingNews = globalNewsData.find(item => {
                const newsKey = getNewsKey(item);
                return newsKey.startsWith(targetKey) || targetKey.startsWith(newsKey);
            });
            if (matchingNews) {
                loadArticleIntoMainViewer(matchingNews);
                switchTab('top-news');
            } else {
                console.log("No matching news found in globalNewsData for headline:", headlineText);
            }
        }

        // ══════════════════════════════════════════════════════════════
        // TRACK RECORD — premium backtest dashboard
        // ══════════════════════════════════════════════════════════════
        let _backtestRange = '30d';
        let _backtestData = null;
        let _backtestLoading = false;

        function setBacktestRange(range) {
            if (_backtestRange === range) return;
            _backtestRange = range;
            document.querySelectorAll('#tr-range-tabs .tr-range-btn').forEach(b => {
                b.classList.toggle('tr-range-active', b.getAttribute('data-range') === range);
            });
            fetchBacktestStats();
        }

        async function fetchBacktestStats() {
            if (_backtestLoading) return;
            _backtestLoading = true;
            try {
                const res = await fetch('/api/backtest-stats?range=' + encodeURIComponent(_backtestRange));
                const data = await res.json();
                _backtestData = data;
                renderBacktest();
            } catch(e) {
                console.error('Backtest fetch error', e);
                renderBacktestError();
            } finally {
                _backtestLoading = false;
            }
        }

        function _fmtPct(v, withSign = true) {
            if (v == null || Number.isNaN(v)) return '—';
            const sign = (withSign && v > 0) ? '+' : '';
            return sign + Number(v).toFixed(2) + '%';
        }
        function _fmtPrice(v) {
            if (v == null || Number.isNaN(v)) return '—';
            return '₹' + Number(v).toLocaleString('en-IN', {maximumFractionDigits: 2});
        }
        function _fmtDate(s) {
            if (!s) return '—';
            try {
                const d = parseSQLiteDate(s);
                return d.toLocaleDateString('en-IN', {day:'2-digit', month:'short'});
            } catch { return s.slice(0, 10); }
        }
        function _pnlCls(v) { return v == null ? 'tr-muted' : (v >= 0 ? 'tr-pos' : 'tr-neg'); }

        function renderBacktest() {
            if (!_backtestData) return;
            // #7 — animated equity curve drawn from closed signals. Render first so it
            // appears above the KPIs and the SVG draw-in plays as KPIs count up.
            renderEquityCurveHero(_backtestData.recent_closed || []);
            renderBacktestKPIs();
            renderBacktestConfidence();
            renderBacktestDirection();
            renderBacktestRecent();
        }

        // Count-up animator. easeOutCubic for a confident "land" rather than a linear ramp.
        // formatFn receives the in-flight value and returns the string to display.
        function animateNumber(el, to, {duration = 650, formatFn = (v) => v.toFixed(0)} = {}) {
            if (!el) return;
            if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                el.textContent = formatFn(to);
                return;
            }
            const from = 0;
            const startedAt = performance.now();
            function frame(now) {
                const elapsed = now - startedAt;
                const t = Math.min(1, elapsed / duration);
                const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
                const v = from + (to - from) * eased;
                el.textContent = formatFn(v);
                if (t < 1) requestAnimationFrame(frame);
                else el.textContent = formatFn(to);
            }
            requestAnimationFrame(frame);
        }

        function renderBacktestKPIs() {
            const s = _backtestData.summary || {};
            const row = document.getElementById('tr-kpi-row');
            if (!row) return;

            // If no closed signals yet, show an empty-state hero
            if (!s.ruled_signals) {
                row.innerHTML = `
                    <div class="tr-kpi tr-kpi-accent col-span-full">
                        <div class="tr-empty">
                            <div class="tr-empty-headline">First signals are still in play</div>
                            <div class="tr-empty-sub">No closed trades in this window yet. Every Alpha Lens signal is graded against a 2% target / 1% stop — the moment the first batch resolves, the scoreboard below populates.</div>
                            <div class="tr-kpi-sub mt-4"><span class="tr-num" data-cu-to="${s.total_signals || 0}" data-cu-fmt="int">0</span> total signals tracked · <span class="tr-num" data-cu-to="${s.active_or_pending || 0}" data-cu-fmt="int">0</span> currently live</div>
                        </div>
                    </div>`;
                _runCountUps(row);
                return;
            }

            const hr = s.hit_rate;
            const hrAccent = hr == null ? 'tr-kpi-accent' : (hr >= 60 ? 'tr-kpi-green' : (hr >= 50 ? 'tr-kpi-amber' : 'tr-kpi-red'));
            const pnlAccent = (s.avg_pnl == null) ? 'tr-kpi-accent' : (s.avg_pnl >= 0 ? 'tr-kpi-green' : 'tr-kpi-red');
            const winSign = (s.avg_win != null && s.avg_win < 0) ? '-' : '+';

            // Render with data-cu-* attrs the animator reads — keeps render and animation cleanly separated.
            row.innerHTML = `
                <div class="tr-kpi tr-kpi-accent">
                    <div class="tr-kpi-label">Total Signals</div>
                    <div class="tr-kpi-value" data-cu-to="${s.total_signals || 0}" data-cu-fmt="intLocale">0</div>
                    <div class="tr-kpi-sub">${s.closed_signals || 0} closed · ${s.active_or_pending || 0} live</div>
                </div>
                <div class="tr-kpi ${hrAccent}">
                    <div class="tr-kpi-label">Hit Rate</div>
                    <div class="tr-kpi-value lg" ${hr == null ? '' : `data-cu-to="${hr}" data-cu-fmt="pct1"`}>${hr == null ? '—' : '0%'}</div>
                    <div class="tr-kpi-sub">${s.hits || 0} hits · ${s.stops || 0} stops${s.expired ? ' · ' + s.expired + ' expired' : ''}</div>
                </div>
                <div class="tr-kpi tr-kpi-green">
                    <div class="tr-kpi-label">Avg Win</div>
                    <div class="tr-kpi-value" ${s.avg_win == null ? '' : `data-cu-to="${Math.abs(s.avg_win)}" data-cu-fmt="pct2" data-cu-prefix="${winSign}"`}>${s.avg_win == null ? '—' : '+0.00%'}</div>
                    <div class="tr-kpi-sub">On winning trades only</div>
                </div>
                <div class="tr-kpi ${pnlAccent}">
                    <div class="tr-kpi-label">Avg P&L</div>
                    <div class="tr-kpi-value" ${s.avg_pnl == null ? '' : `data-cu-to="${s.avg_pnl}" data-cu-fmt="pctSigned2"`}>${s.avg_pnl == null ? '—' : '0.00%'}</div>
                    <div class="tr-kpi-sub">Per closed signal, all-in</div>
                </div>`;
            _runCountUps(row);
        }

        // Sweep every element with [data-cu-to] inside `scope` and animate it once.
        // Format keys are kept inline so this stays self-contained.
        function _runCountUps(scope) {
            scope.querySelectorAll('[data-cu-to]').forEach(el => {
                if (el.dataset.cuDone === '1') return;
                const to = parseFloat(el.dataset.cuTo);
                if (Number.isNaN(to)) return;
                const fmt = el.dataset.cuFmt || 'int';
                const prefix = el.dataset.cuPrefix || '';
                const formatters = {
                    int:        (v) => prefix + Math.round(v).toString(),
                    intLocale:  (v) => prefix + Math.round(v).toLocaleString('en-IN'),
                    pct1:       (v) => prefix + v.toFixed(1) + '%',
                    pct2:       (v) => prefix + v.toFixed(2) + '%',
                    pctSigned2: (v) => (v >= 0 ? '+' : '') + v.toFixed(2) + '%',
                };
                animateNumber(el, to, { formatFn: formatters[fmt] || formatters.int });
                el.dataset.cuDone = '1';
            });
        }

        function renderBacktestConfidence() {
            const bands = _backtestData.by_confidence || [];
            const tbody = document.getElementById('tr-confidence-body');
            if (!tbody) return;

            const populatedBands = bands.filter(b => b.signals > 0);
            if (populatedBands.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" class="tr-empty">
                    <div class="tr-empty-sub">No closed signals in this window yet. Confidence-band performance will populate as trades resolve.</div>
                </td></tr>`;
                return;
            }

            const maxHr = Math.max(...populatedBands.map(b => b.hit_rate || 0), 0);
            tbody.innerHTML = bands.map(b => {
                if (b.signals === 0) {
                    return `<tr>
                        <td class="tr-band-label">${b.band}%</td>
                        <td class="text-right tr-num tr-muted">—</td>
                        <td class="text-right tr-num tr-muted">—</td>
                        <td class="text-right tr-num tr-muted">—</td>
                        <td class="text-right tr-num tr-muted">—</td>
                        <td class="text-right tr-num tr-muted">—</td>
                        <td><div class="tr-bar-track"><div class="tr-bar-fill" style="width:0%;opacity:0.2"></div></div></td>
                    </tr>`;
                }
                const barW = maxHr > 0 ? Math.max(4, (b.hit_rate / 100) * 100) : 0;
                return `<tr>
                    <td class="tr-band-label">${b.band}%</td>
                    <td class="text-right tr-num">${b.signals}</td>
                    <td class="text-right tr-num tr-pos">${b.hits}</td>
                    <td class="text-right tr-num tr-neg">${b.stops}</td>
                    <td class="text-right tr-num-strong">${b.hit_rate == null ? '—' : b.hit_rate.toFixed(1) + '%'}</td>
                    <td class="text-right tr-num ${_pnlCls(b.avg_pnl)}">${_fmtPct(b.avg_pnl)}</td>
                    <td><div class="tr-bar-track"><div class="tr-bar-fill" style="width:${barW}%"></div></div></td>
                </tr>`;
            }).join('');
        }

        function renderBacktestDirection() {
            const dirs = _backtestData.by_direction || {};
            const row = document.getElementById('tr-direction-row');
            if (!row) return;

            const cardFor = (label, data, cls) => {
                if (!data || !data.signals) {
                    return `<div class="tr-dir-card ${cls}">
                        <div class="tr-dir-head ${cls}">${label} Signals</div>
                        <div class="tr-empty py-6"><div class="tr-empty-sub">No closed ${label.toLowerCase()} signals in this window.</div></div>
                    </div>`;
                }
                return `<div class="tr-dir-card ${cls}">
                    <div class="flex items-baseline justify-between mb-4">
                        <div class="tr-dir-head ${cls}">${label} Signals</div>
                        <div class="tr-num-strong" style="color:${cls==='bull'?'var(--green)':'var(--red)'}">${data.hit_rate == null ? '—' : data.hit_rate.toFixed(1) + '%'}</div>
                    </div>
                    <div class="tr-dir-row">
                        <div class="tr-dir-label">Closed</div>
                        <div class="tr-num">${data.signals}</div>
                    </div>
                    <div class="tr-dir-row">
                        <div class="tr-dir-label">Hits</div>
                        <div class="tr-num tr-pos">${data.hits}</div>
                    </div>
                    <div class="tr-dir-row">
                        <div class="tr-dir-label">Stops</div>
                        <div class="tr-num tr-neg">${data.stops}</div>
                    </div>
                    <div class="tr-dir-row">
                        <div class="tr-dir-label">Avg P&L</div>
                        <div class="tr-num ${_pnlCls(data.avg_pnl)}">${_fmtPct(data.avg_pnl)}</div>
                    </div>
                </div>`;
            };

            row.innerHTML = cardFor('Bullish', dirs.bullish, 'bull') + cardFor('Bearish', dirs.bearish, 'bear');
        }

        function renderBacktestRecent() {
            const rows = _backtestData.recent_closed || [];
            const tbody = document.getElementById('tr-recent-body');
            if (!tbody) return;
            if (!rows.length) {
                tbody.innerHTML = `<tr><td colspan="8" class="tr-empty">
                    <div class="tr-empty-sub">No trades have closed in this window yet. Recent results will appear here as signals hit target, stop, or expire.</div>
                </td></tr>`;
                return;
            }
            tbody.innerHTML = rows.map((r, idx) => {
                const ticker = (r.ticker || '').replace('.NS', '').replace('.BO', '');
                const dirCls = r.direction === 'BULLISH' ? 'bull' : (r.direction === 'BEARISH' ? 'bear' : '');
                const dirArrow = r.direction === 'BULLISH' ? '▲' : (r.direction === 'BEARISH' ? '▼' : '◆');

                let outcomeCls = 'expired', outcomeTxt = 'Expired';
                if (r.status === 'Predicted Target Hit') { outcomeCls = 'hit'; outcomeTxt = '✓ Target'; }
                else if (r.status === 'Stop Loss Hit') { outcomeCls = 'stop'; outcomeTxt = '✕ Stop'; }
                else if (r.status === 'Reacted Against Prediction') { outcomeCls = 'stop'; outcomeTxt = '↘ Reacted'; }

                const staggerI = Math.min(idx, 12);
                return `<tr data-stagger-i="${idx}" style="--i:${staggerI}">
                    <td class="tr-num tr-muted">${_fmtDate(r.created_at)}</td>
                    <td><span class="ticker-hover-target font-display font-black text-white tracking-tight" data-ticker="${escapeHtml(r.ticker || ticker)}" style="cursor:pointer">${ticker}</span></td>
                    <td><span class="tr-dir-pill ${dirCls}"><span class="dot" style="background:currentColor"></span>${r.direction}</span></td>
                    <td class="text-right tr-num">${r.confidence || '—'}</td>
                    <td class="text-right tr-num tr-muted">${_fmtPrice(r.base_price)}</td>
                    <td class="text-right tr-num">${_fmtPrice(r.current_price)}</td>
                    <td class="text-right tr-num-strong ${_pnlCls(r.pnl_pct)}">${_fmtPct(r.pnl_pct)}</td>
                    <td><span class="tr-outcome ${outcomeCls}">${outcomeTxt}</span></td>
                </tr>`;
            }).join('');
        }

        function renderBacktestError() {
            ['tr-kpi-row','tr-direction-row'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.innerHTML = '';
            });
            const cb = document.getElementById('tr-confidence-body');
            if (cb) cb.innerHTML = `<tr><td colspan="7" class="tr-empty">
                <div class="tr-empty-headline">Couldn't load track record</div>
                <div class="tr-empty-sub">There was an issue reaching the backtest service. Try refreshing in a moment.</div>
            </td></tr>`;
            const rb = document.getElementById('tr-recent-body');
            if (rb) rb.innerHTML = `<tr><td colspan="8" class="tr-empty"><div class="tr-empty-sub">—</div></td></tr>`;
        }

        // ── REAL-TIME TOAST NOTIFICATIONS ──
        async function pollSignalNotifications() {
            try {
                const res = await fetch('/api/signals/latest');
                const data = await res.json();
                if (data.id && data.id > _lastNotifId) {
                    if (_lastNotifId > 0) showSignalToast(data);
                    _lastNotifId = data.id;
                }
            } catch(e) {}
        }

        function showSignalToast(sig) {
            const container = document.getElementById('signal-toast-container');
            const isBull = sig.direction === 'BULLISH';
            const isHigh = sig.confidence >= 80;
            const toast = document.createElement('div');
            toast.className = `signal-toast ${isBull ? 'bull-toast' : 'bear-toast'} ${isHigh ? 'high-conviction' : ''}`;
            const ticker = (sig.ticker||'').replace('.NS','').replace('.BO','');
            const entry = sig.entry ? '₹' + Number(sig.entry).toLocaleString('en-IN') : '—';
            toast.innerHTML = `
                <div class="toast-header">
                    <div class="flex items-center gap-2">
                        <span style="font-size:14px">${isBull?'📈':'📉'}</span>
                        <span class="toast-ticker">${ticker}</span>
                        <span class="toast-dir ${isBull?'bull':'bear'} text-xs font-bold">${sig.direction}</span>
                        ${isHigh?'<span style="color:#f59e0b;font-size:10px;font-weight:800;">⭐ HIGH</span>':''}
                    </div>
                    <button class="toast-close" onclick="this.closest('.signal-toast').remove()">✕</button>
                </div>
                <div class="toast-row"><span class="toast-label">Entry</span><span class="toast-val">${entry}</span></div>
                <div class="toast-row"><span class="toast-label">Conviction</span><span class="toast-val">${sig.confidence}/100</span></div>
                ${sig.headline ? `<div class="toast-headline">${sig.headline}</div>` : ''}
                <div class="toast-progress"><div class="toast-progress-fill" style="background:${isBull?'#10b981':'#f43f5e'}"></div></div>
            `;
            container.appendChild(toast);
            requestAnimationFrame(() => { requestAnimationFrame(() => { toast.classList.add('show'); }); });
            setTimeout(() => {
                toast.classList.remove('show');
                setTimeout(() => toast.remove(), 400);
            }, 8000);
            // Browser push notification
            if (Notification.permission === 'granted') {
                new Notification('Alpha Lens Signal', { body: `${ticker} ${sig.direction} — Conviction ${sig.confidence}/100`, icon: '📈' });
            }
        }

        // Request notification permission on first interaction
        document.addEventListener('click', () => {
            if (Notification.permission === 'default') Notification.requestPermission();
        }, { once: true });

// ════════════════════════════════════════════════════════════════════════
// "THE RIPPLE" — macro propagation graph
//
// Premium feature: for macro-grade news events (commodity shocks, RBI/Fed
// decisions, geopolitical, election, policy), the backend pre-generates a
// 3-tier graph showing how the news ripples across NSE stocks. This block
// handles opening the modal, fetching the graph, lazy-loading d3.js, and
// rendering it as a force-directed visualization with a side panel.
// ════════════════════════════════════════════════════════════════════════

let _d3Promise = null;
function _ensureD3() {
    if (typeof window.d3 !== 'undefined') return Promise.resolve(window.d3);
    if (_d3Promise) return _d3Promise;
    _d3Promise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js';
        s.async = true;
        s.onload = () => resolve(window.d3);
        s.onerror = () => reject(new Error('d3 failed to load'));
        document.head.appendChild(s);
    });
    return _d3Promise;
}

function _rippleColorForDirection(dir) {
    return (dir || '').toUpperCase() === 'BULLISH' ? '#10b981' : '#f43f5e';
}
function _rippleTierColor(tier) {
    return tier === 1 ? '#fbbf24' : tier === 2 ? '#60a5fa' : '#a78bfa';
}

function _renderRippleSidePanel(node, container) {
    if (!node) {
        container.innerHTML = `
            <div class="ripple-side-empty">
                <div class="ripple-side-empty-icon">⚡</div>
                <div class="ripple-side-empty-text">Click any stock node to see the causal chain</div>
            </div>`;
        return;
    }
    const dir = (node.direction || '').toUpperCase();
    const dirCls = dir === 'BULLISH' ? 'bullish' : 'bearish';
    const tierLabels = {1: 'Tier 1 · Direct Impact', 2: 'Tier 2 · Supply Chain', 3: 'Tier 3 · Macro Transmission'};
    container.innerHTML = `
        <div class="ripple-side-card">
            <div class="ripple-side-tier-label">${tierLabels[node.tier] || ''}</div>
            <div class="ripple-side-ticker">${escapeHtml(node.ticker || '')}</div>
            <div class="flex items-center gap-3">
                <span class="ripple-side-direction ${dirCls}">${dir || 'NEUTRAL'}</span>
                <span class="ripple-side-conf">Confidence ${node.confidence != null ? node.confidence : '—'}%</span>
            </div>
            <div class="ripple-side-reason">${escapeHtml(node.reason || 'No detailed reason provided.')}</div>
        </div>`;
}

async function _renderRippleGraph(payload) {
    const d3 = await _ensureD3();
    const svgEl = document.getElementById('ripple-graph');
    const wrap = document.getElementById('ripple-graph-wrap');
    const sideEl = document.getElementById('ripple-side');
    const loadingEl = document.getElementById('ripple-loading');

    if (loadingEl) loadingEl.style.display = 'none';
    if (svgEl) svgEl.style.display = 'block';

    // Clear any previous render
    const svg = d3.select('#ripple-graph');
    svg.selectAll('*').remove();

    const rect = wrap.getBoundingClientRect();
    const width = Math.max(400, rect.width);
    const height = Math.max(460, rect.height || 460);
    svg.attr('viewBox', `0 0 ${width} ${height}`)
       .attr('preserveAspectRatio', 'xMidYMid meet');

    // Build nodes + links from the payload tiers
    const nodes = [{ id: 'center', label: 'NEWS', isCenter: true, tier: 0 }];
    const links = [];
    const tiers = Array.isArray(payload.tiers) ? payload.tiers : [];
    tiers.forEach(tier => {
        const tNum = tier.tier;
        (tier.nodes || []).forEach(n => {
            const id = `${tNum}:${n.ticker}`;
            nodes.push({
                id,
                label: n.ticker,
                ticker: n.ticker,
                direction: n.direction,
                confidence: n.confidence,
                reason: n.reason,
                tier: tNum,
            });
            // Connect to center for tier-1, otherwise to a random tier-(N-1) node
            if (tNum === 1) {
                links.push({ source: 'center', target: id });
            } else {
                const prevTier = tiers.find(x => x.tier === tNum - 1);
                if (prevTier && prevTier.nodes && prevTier.nodes.length) {
                    const parent = prevTier.nodes[Math.floor(Math.random() * prevTier.nodes.length)];
                    links.push({ source: `${tNum - 1}:${parent.ticker}`, target: id });
                } else {
                    links.push({ source: 'center', target: id });
                }
            }
        });
    });

    // Concentric guide rings (purely decorative)
    const cx = width / 2, cy = height / 2;
    const ringRadii = [Math.min(width, height) * 0.18, Math.min(width, height) * 0.32, Math.min(width, height) * 0.46];
    svg.append('g')
        .selectAll('circle')
        .data(ringRadii)
        .enter().append('circle')
        .attr('class', 'ripple-tier-ring')
        .attr('cx', cx).attr('cy', cy)
        .attr('r', d => d);

    // Edges layer
    const linkSel = svg.append('g').selectAll('line')
        .data(links).enter().append('line')
        .attr('class', d => {
            const target = nodes.find(n => n.id === (d.target.id || d.target));
            const dir = (target && target.direction || '').toUpperCase();
            if (!target || target.isCenter) return 'ripple-edge center';
            return 'ripple-edge ' + (dir === 'BULLISH' ? 'bullish' : 'bearish');
        })
        .attr('stroke-width', 1.4);

    // Node group (circle + label)
    const nodeSel = svg.append('g').selectAll('g')
        .data(nodes).enter().append('g')
        .style('cursor', d => d.isCenter ? 'default' : 'pointer');

    nodeSel.append('circle')
        .attr('class', d => d.isCenter ? 'ripple-node-circle ripple-node-center' : 'ripple-node-circle')
        .attr('r', d => d.isCenter ? 34 : (d.tier === 1 ? 16 : d.tier === 2 ? 13 : 11))
        .attr('fill', d => {
            if (d.isCenter) return null; // handled by class
            const base = _rippleColorForDirection(d.direction);
            return base;
        })
        .attr('fill-opacity', d => d.isCenter ? null : 0.22)
        .attr('stroke', d => d.isCenter ? null : _rippleColorForDirection(d.direction))
        .attr('stroke-width', 1.6)
        .on('click', (event, d) => {
            if (d.isCenter) return;
            _renderRippleSidePanel(d, sideEl);
        });

    nodeSel.append('text')
        .attr('class', 'ripple-node-label')
        .attr('text-anchor', 'middle')
        .attr('dy', d => d.isCenter ? 4 : (d.tier === 1 ? 28 : 22))
        .text(d => d.isCenter ? '⚡' : (d.label || ''));

    // Force simulation — radial constraint pulls nodes toward their tier ring
    const sim = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links).id(d => d.id).distance(d => {
            const t = (typeof d.target === 'object' ? d.target.tier : 0);
            return t === 1 ? 110 : t === 2 ? 90 : 80;
        }).strength(0.55))
        .force('charge', d3.forceManyBody().strength(d => d.isCenter ? -800 : -260))
        .force('center', d3.forceCenter(cx, cy))
        .force('collide', d3.forceCollide().radius(d => (d.isCenter ? 40 : (d.tier === 1 ? 22 : 17))))
        .force('radial', d3.forceRadial(d => {
            if (d.isCenter) return 0;
            if (d.tier === 1) return ringRadii[0];
            if (d.tier === 2) return ringRadii[1];
            return ringRadii[2];
        }, cx, cy).strength(0.85))
        .on('tick', () => {
            linkSel
                .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
                .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
            nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
        });

    // Stop the simulation eventually so it doesn't burn CPU forever.
    setTimeout(() => sim.alphaTarget(0).stop(), 4000);
}

async function openRipple(newsId) {
    const modal = document.getElementById('ripple-modal');
    const headline = document.getElementById('ripple-headline');
    const summary = document.getElementById('ripple-summary');
    const loading = document.getElementById('ripple-loading');
    const svg = document.getElementById('ripple-graph');
    const side = document.getElementById('ripple-side');

    if (!modal) return;
    headline.innerText = 'Loading…';
    summary.innerText = '';
    if (loading) loading.style.display = 'flex';
    if (svg) { svg.style.display = 'none'; }
    if (side) {
        side.innerHTML = `
            <div class="ripple-side-empty">
                <div class="ripple-side-empty-icon">⚡</div>
                <div class="ripple-side-empty-text">Click any stock node to see the causal chain</div>
            </div>`;
    }
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';

    try {
        const res = await fetch(`/api/news/${newsId}/ripple`);
        if (!res.ok) {
            const errBody = await res.json().catch(() => ({}));
            headline.innerText = 'Could not load The Ripple';
            summary.innerText = errBody.error || `HTTP ${res.status}`;
            if (loading) loading.style.display = 'none';
            return;
        }
        const data = await res.json();
        headline.innerText = data.headline || '';
        summary.innerText = data.summary || '';
        await _renderRippleGraph(data);
    } catch (err) {
        headline.innerText = 'Could not load The Ripple';
        summary.innerText = String(err && err.message ? err.message : err);
        if (loading) loading.style.display = 'none';
    }
}

function closeRipple() {
    const modal = document.getElementById('ripple-modal');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
}

// Global click handlers for backdrop + close button (delegated so they
// survive re-renders).
document.addEventListener('click', (e) => {
    if (e.target.closest('[data-close-ripple]')) {
        closeRipple();
    }
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeRipple();
});

// Re-render the graph on window resize so the SVG fills the new wrap size.
let _rippleResizeTimer = null;
window.addEventListener('resize', () => {
    const modal = document.getElementById('ripple-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    clearTimeout(_rippleResizeTimer);
    _rippleResizeTimer = setTimeout(() => {
        // Re-fit viewBox; the existing simulation positions are still valid.
        const svg = document.getElementById('ripple-graph');
        const wrap = document.getElementById('ripple-graph-wrap');
        if (svg && wrap) {
            const r = wrap.getBoundingClientRect();
            svg.setAttribute('viewBox', `0 0 ${r.width} ${r.height || 460}`);
        }
    }, 150);
});

// Expose globally for inline handlers / debugging
window.openRipple = openRipple;
window.closeRipple = closeRipple;

// ════════════════════════════════════════════════════════════════════════
// MACRO PULSE — live shock detector strip
// Fetches /api/macro/events and renders chips for each detected shock.
// Click → opens the Ripple modal using the macro-event variant.
// Refreshes every 90 seconds.
// ════════════════════════════════════════════════════════════════════════

async function fetchMacroPulse() {
    const wrap = document.getElementById('macro-pulse-wrap');
    const chipsEl = document.getElementById('macro-pulse-chips');
    const countEl = document.getElementById('macro-pulse-count');
    if (!wrap || !chipsEl) return;
    try {
        const res = await fetch('/api/macro/events');
        if (!res.ok) {
            wrap.classList.add('hidden');
            return;
        }
        const data = await res.json();
        const events = (data && data.events) || [];
        if (!events.length) {
            wrap.classList.add('hidden');
            return;
        }
        wrap.classList.remove('hidden');
        if (countEl) countEl.innerText = `${events.length} active shock${events.length === 1 ? '' : 's'}`;
        chipsEl.innerHTML = events.map(ev => {
            const pct = parseFloat(ev.change_pct_1d || 0);
            const dirClass = pct >= 0 ? 'up' : 'down';
            const arrow = pct >= 0
                ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 14l6-6 6 6"/></svg>'
                : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 10l6 6 6-6"/></svg>';
            const levelClass = (ev.shock_level || '').toLowerCase() === 'major' ? 'major' : 'significant';
            return `
                <button class="macro-chip" data-macro-event-id="${ev.id}" data-has-ripple="${ev.has_ripple}" aria-label="Open macro shock ripple">
                    <span class="macro-chip-arrow ${dirClass === 'up' ? 'text-emerald-400' : 'text-rose-400'}">${arrow}</span>
                    <span class="macro-chip-label">${escapeHtml(ev.instrument_label || ev.symbol || ev.instrument_key)}</span>
                    <span class="macro-chip-pct ${dirClass}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</span>
                    <span class="macro-chip-level ${levelClass}">${escapeHtml(ev.shock_level || '')}</span>
                </button>
            `;
        }).join('');
        // Wire click → openMacroRipple
        chipsEl.querySelectorAll('[data-macro-event-id]').forEach(btn => {
            btn.addEventListener('click', () => {
                openMacroRipple(parseInt(btn.getAttribute('data-macro-event-id'), 10));
            });
        });
    } catch (_err) {
        wrap.classList.add('hidden');
    }
}

async function openMacroRipple(eventId) {
    // Reuse the existing ripple-modal shell + D3 renderer; just point at
    // the macro endpoint for the payload.
    const modal = document.getElementById('ripple-modal');
    const headline = document.getElementById('ripple-headline');
    const summary = document.getElementById('ripple-summary');
    const loading = document.getElementById('ripple-loading');
    const svg = document.getElementById('ripple-graph');
    const side = document.getElementById('ripple-side');
    if (!modal) return;
    headline.innerText = 'Loading macro ripple…';
    summary.innerText = '';
    if (loading) loading.style.display = 'flex';
    if (svg) svg.style.display = 'none';
    if (side) {
        side.innerHTML = `
            <div class="ripple-side-empty">
                <div class="ripple-side-empty-icon">⚡</div>
                <div class="ripple-side-empty-text">Click any stock node to see the causal chain</div>
            </div>`;
    }
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';

    try {
        const res = await fetch(`/api/macro/events/${eventId}/ripple`);
        if (!res.ok) {
            const errBody = await res.json().catch(() => ({}));
            headline.innerText = 'Could not load macro ripple';
            summary.innerText = errBody.error || `HTTP ${res.status}`;
            if (loading) loading.style.display = 'none';
            return;
        }
        const data = await res.json();
        const pct = parseFloat(data.change_pct_1d || 0);
        const sign = pct >= 0 ? '+' : '';
        headline.innerText = `${data.instrument} ${sign}${pct.toFixed(2)}% — ${data.shock_level} shock`;
        summary.innerText = data.summary || '';
        await _renderRippleGraph(data);
    } catch (err) {
        headline.innerText = 'Could not load macro ripple';
        summary.innerText = String(err && err.message ? err.message : err);
        if (loading) loading.style.display = 'none';
    }
}

// Boot: fetch on load, then refresh every 90s.
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { fetchMacroPulse(); });
} else {
    fetchMacroPulse();
}
setInterval(() => { fetchMacroPulse(); }, 90 * 1000);

window.openMacroRipple = openMacroRipple;
window.fetchMacroPulse = fetchMacroPulse;
