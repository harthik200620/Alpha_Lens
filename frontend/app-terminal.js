        async function openStockDrawer(ticker) {
            const drawer = document.getElementById('stock-drawer');
            const backdrop = document.getElementById('stock-drawer-backdrop');
            if (!drawer || !backdrop) return;
            const base = String(ticker).toUpperCase().replace(/\.(NS|BO)$/i, '');
            const fullTicker = ticker.toUpperCase().includes('.') ? ticker.toUpperCase() : (base + '.NS');

            document.getElementById('sd-ticker').textContent = base;
            document.getElementById('sd-name').textContent = 'Loading…';
            // Skeleton placeholder while the live-price fetch is in flight.
            // Mirrors the eventual layout (Live Quote → AI Verdict → Signals)
            // so the swap doesn't reflow the drawer. ARIA so screen readers
            // announce loading state rather than dead silence.
            document.getElementById('sd-body').innerHTML = (
                '<div class="sd-section" aria-busy="true" aria-label="Loading stock details">'
              +   '<div class="sd-section-label">Live Quote</div>'
              +   '<div class="sd-price-row">'
              +     '<span class="skel skel-num lg" style="width:140px"></span>'
              +     '<span class="skel skel-pill" style="margin-left:10px"></span>'
              +   '</div>'
              + '</div>'
              + '<div class="sd-section">'
              +   '<div class="sd-section-label">AI Signals (recent)</div>'
              +   Array.from({length: 3}).map(() =>
                    '<div class="sd-news-item">'
                  +   '<div class="h"><span class="skel skel-line med"></span></div>'
                  +   '<div class="m"><span class="skel skel-line tiny"></span></div>'
                  + '</div>'
                  ).join('')
              + '</div>'
            );

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
                    const cls = chg > 0 ? 'up' : (chg < 0 ? 'dn' : 'flat');
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
                // Show skeleton rows while the fetch is in flight, but ONLY on
                // first load (when _terminalData is still empty). On a refresh
                // we already have rows on screen — flashing skeletons over them
                // would be a regression, not an improvement.
                const tbody = document.getElementById('terminal-body');
                if (tbody && !_terminalData.length) {
                    const COLS = 10;  // matches the terminal-table thead column count
                    const skelCell = '<td><span class="skel skel-cell" style="display:block;width:80%"></span></td>';
                    tbody.innerHTML = Array.from({length: 8})
                        .map(() => '<tr class="skel-row">' + skelCell.repeat(COLS) + '</tr>')
                        .join('');
                }
                const res = await fetch('/api/signal-terminal');
                const data = await res.json();
                _terminalData = data.signals || [];
                document.getElementById('terminal-count').textContent = _terminalData.length + ' signals';
                renderTerminal();
            } catch(e) {
                console.log('Terminal fetch error', e);
                // Don't leave skeleton rows on screen forever if the fetch fails
                // (common during a free-tier cold start) — show a real error state.
                const tbody = document.getElementById('terminal-body');
                if (tbody && !_terminalData.length) {
                    tbody.innerHTML = `<tr><td colspan="10"><div class="term-empty">
                        <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 9v4M12 17h.01"/><circle cx="12" cy="12" r="9"/></svg>
                        <div class="term-empty-title">Couldn't reach the signal engine</div>
                        <div class="term-empty-sub">Retrying automatically — this can take a moment on first load.</div>
                    </div></td></tr>`;
                }
            }
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
                const noData = _terminalData.length === 0;
                tbody.innerHTML = noData
                    ? `<tr><td colspan="10"><div class="term-empty">
                        <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/></svg>
                        <div class="term-empty-title">No active signals right now</div>
                        <div class="term-empty-sub">The engine is monitoring 68 news sources and live prices. New signals surface here during market hours (9:15-15:30 IST).</div>
                       </div></td></tr>`
                    : `<tr><td colspan="10"><div class="term-empty">
                        <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
                        <div class="term-empty-title">No signals match this filter</div>
                        <div class="term-empty-sub">Try a different filter above.</div>
                       </div></td></tr>`;
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
                if (s.status === 'Predicted Target Hit') { stCls = 'status-hit'; stTxt = '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="3" style="vertical-align:-1px;margin-right:3px"><path d="M5 13l4 4L19 7"/></svg>Target'; }
                else if (s.status === 'Stop Loss Hit') { stCls = 'status-stop'; stTxt = '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="3" style="vertical-align:-1px;margin-right:3px"><path d="M6 6l12 12M18 6L6 18"/></svg>Stopped'; }
                else if (s.status === 'Expired') { stCls = 'status-expired'; stTxt = '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;margin-right:3px"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>Expired'; }
                const ticker = (s.ticker||'').replace('.NS','').replace('.BO','');
                const isHigh = s.confidence >= 85;
                const rowBg = isHigh ? 'background:rgba(245,158,11,0.03);' : '';
                const staggerI = Math.min(idx, 12);
                return `<tr data-stagger-i="${idx}" style="${rowBg}--i:${staggerI};">
                    <td><span class="ticker-hover-target font-display font-bold text-white text-sm" data-ticker="${escapeHtml(s.ticker || ticker)}">${ticker}</span>${isHigh?'<span class="ml-1" title="High Conviction"><svg viewBox="0 0 24 24" width="11" height="11" fill="var(--amber)" style="vertical-align:-1px"><path d="M12 2l2.9 6.3 6.9.7-5.1 4.7 1.4 6.8L12 17.8 5.9 21.2l1.4-6.8L2.2 9.7l6.9-.7z"/></svg></span>':''}</td>
                    <td><span class="${dirCls} text-xs">${dirIcon} ${s.direction}</span></td>
                    <td><div class="conf-ring ${confCls}">${s.confidence}</div></td>
                    <td class="text-slate-300 font-mono text-xs">₹${s.entry.toLocaleString('en-IN')}</td>
                    <td class="text-white font-mono text-xs">₹${s.current.toLocaleString('en-IN')}</td>
                    <td class="font-mono text-xs whitespace-nowrap" title="ATR-based target and stop">
                        <span class="dir-bull">+${(s.target_pct ?? 0).toFixed(1)}%</span>
                        <span class="text-slate-600">/</span>
                        <span class="dir-bear">-${(s.stop_pct ?? 0).toFixed(1)}%</span>
                    </td>
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
                if (r.status === 'Predicted Target Hit') { outcomeCls = 'hit'; outcomeTxt = '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="3" style="vertical-align:-1px;margin-right:3px"><path d="M5 13l4 4L19 7"/></svg>Target'; }
                else if (r.status === 'Stop Loss Hit') { outcomeCls = 'stop'; outcomeTxt = '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="3" style="vertical-align:-1px;margin-right:3px"><path d="M6 6l12 12M18 6L6 18"/></svg>Stop'; }
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
                        <span style="display:inline-flex">${isBull?'<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 14l6-6 6 6"/></svg>':'<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 10l6 6 6-6"/></svg>'}</span>
                        <span class="toast-ticker">${ticker}</span>
                        <span class="toast-dir ${isBull?'bull':'bear'} text-xs font-bold">${sig.direction}</span>
                        ${isHigh?'<span style="color:#f59e0b;font-size:10px;font-weight:800;"><svg viewBox="0 0 24 24" width="10" height="10" fill="currentColor" style="vertical-align:-1px;margin-right:3px"><path d="M12 2l2.9 6.3 6.9.7-5.1 4.7 1.4 6.8L12 17.8 5.9 21.2l1.4-6.8L2.2 9.7l6.9-.7z"/></svg>HIGH</span>':''}
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
// "THE RIPPLE" — macro propagation arrow-flow visualization
//
// Premium feature: for macro-grade news events (commodity shocks, RBI/Fed
// decisions, geopolitical, election, policy), the backend pre-generates a
// 3-tier graph showing how the news ripples across NSE stocks.
// Rendered as a clean horizontal arrow-flow: EVENT → Tier 1 → Tier 2 → Tier 3
// ════════════════════════════════════════════════════════════════════════

