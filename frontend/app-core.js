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
        const tabs = ['top-news', 'all-news', 'macro-pulse', 'calendar', 'portfolio', 'stocks', 'terminal', 'earnings'];

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
        // The Google OAuth client ID is NOT hardcoded here — it's fetched from the
        // backend (`/api/public-config`, sourced from the GOOGLE_OAUTH_CLIENT_ID env
        // var) so one env var is the single source of truth for both server-side
        // token verification and this client button. The ID is public (it ships in
        // the sign-in button regardless), so serving it to the client is fine.
        let GOOGLE_CLIENT_ID = "";

        async function _ensureGoogleClientId() {
            if (GOOGLE_CLIENT_ID) return GOOGLE_CLIENT_ID;
            try {
                const res = await fetch('/api/public-config');
                const cfg = await res.json();
                GOOGLE_CLIENT_ID = (cfg && cfg.google_client_id) || "";
            } catch (e) {
                GOOGLE_CLIENT_ID = "";
            }
            return GOOGLE_CLIENT_ID;
        }

        async function initializeGoogleAuth() {
            try {
                await _ensureGoogleClientId();
                if (!GOOGLE_CLIENT_ID) {
                    console.log("Google sign-in not configured (no client ID from /api/public-config).");
                    return;
                }
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
                const hiddenTabs = ['macro-pulse', 'calendar', 'portfolio', 'stocks', 'terminal'];
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
        const STOCK_NAV_IDS = ['nav-macro-pulse', 'nav-calendar', 'nav-portfolio', 'nav-stocks', 'nav-terminal', 'nav-earnings'];

        function updateAppHeaderOffset() {
            const headerEls = [
                document.getElementById('premium-ticker-bar'),
                document.querySelector('nav.glass-panel')
            ];
            const bottom = headerEls.reduce((max, el) => {
                if (!el) return max;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return max;
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= 0) return max;
                return Math.max(max, Math.min(window.innerHeight, rect.bottom));
            }, 0);
            if (bottom > 0) {
                document.documentElement.style.setProperty('--app-header-offset', `${Math.ceil(bottom)}px`);
            }
        }

        function switchTab(targetTabId) {
            if (isNonStockMode && STOCK_NAV_IDS.includes(`nav-${targetTabId}`)) {
                targetTabId = 'top-news';
            }
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
            // Keep the mobile tab bar's active pill in sync with the desktop nav.
            document.querySelectorAll('#mobile-tabbar .mtab').forEach(t => {
                t.classList.toggle('active', t.getAttribute('data-mtab') === targetTabId);
            });
            // Index cards only belong on the Top News page
            const ticker = document.getElementById('index-ticker');
            if (ticker) {
                ticker.style.display = (targetTabId === 'top-news' && !isNonStockMode) ? '' : 'none';
            }
            // Lazy-load premium views
            if (targetTabId === 'terminal') fetchTerminalData();
            if (targetTabId === 'stocks') fetchBacktestStats();
            if (targetTabId === 'macro-pulse') fetchMacroPulse();
            if (targetTabId === 'portfolio' && typeof loadRiskRadar === 'function') loadRiskRadar();
            if (targetTabId === 'earnings' && typeof loadEarningsIntel === 'function') loadEarningsIntel();
            updateAppHeaderOffset();
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

