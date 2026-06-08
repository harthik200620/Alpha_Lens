        // ==========================================
        // WATCHLIST & PORTFOLIO LOGIC
        // ==========================================
        // Load + normalize the watchlist. Each entry now carries optional holdings
        // (qty, avgPrice) for live P&L — old 2-field entries default to null so they
        // keep working unchanged (backward-compatible, same localStorage key).
        let watchlist = (function _loadWatchlist() {
            let arr = [];
            try { arr = JSON.parse(localStorage.getItem('alpha_lens_watchlist') || '[]'); } catch (e) { arr = []; }
            if (!Array.isArray(arr)) arr = [];
            return arr.map(s => ({
                ticker: s.ticker, name: s.name,
                qty: (typeof s.qty === 'number' && isFinite(s.qty) && s.qty > 0) ? s.qty : null,
                avgPrice: (typeof s.avgPrice === 'number' && isFinite(s.avgPrice) && s.avgPrice > 0) ? s.avgPrice : null,
            })).filter(s => s.ticker);
        })();
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
            if (typeof loadRiskRadar === 'function') loadRiskRadar(true);
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
                watchlist.push({ ticker, name, qty: null, avgPrice: null });
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

        // ── Holdings P&L helpers (mark-to-market, avg-cost, unrealized) ──
        function _wlFmt(v) { return Number(v || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 }); }
        // Per-stock P&L state: 'ok' | 'none' (no qty/avg) | 'unresolved' (no price)
        // | 'warn' (avg price implausible vs live price → likely wrong exchange/split).
        function _wlPnl(stock) {
            const pd = watchlistPrices[stock.ticker] || {};
            const price = Number(pd.price || 0);
            const qty = Number(stock.qty || 0);
            const avg = Number(stock.avgPrice || 0);
            if (!(qty > 0) || !(avg > 0)) return { state: 'none' };
            if (!(price > 0)) return { state: 'unresolved' };
            const ratio = price / avg;
            if (ratio > 10 || ratio < 0.1) return { state: 'warn' };
            const invested = avg * qty, current = price * qty;
            return { state: 'ok', pnl: current - invested, pct: (current - invested) / invested * 100, invested, current };
        }
        function _wlSetHolding(ticker, field, raw) {
            const s = watchlist.find(x => x.ticker === ticker);
            if (!s) return;
            const v = parseFloat(raw);
            s[field] = (isFinite(v) && v > 0) ? v : null;
            saveWatchlist();   // persists + re-renders + refreshes the (weighted) radar
        }
        function _wlTotalTile() {
            let invested = 0, current = 0, n = 0;
            watchlist.forEach(s => { const p = _wlPnl(s); if (p.state === 'ok') { invested += p.invested; current += p.current; n++; } });
            const el = document.createElement('div');
            if (n === 0) {
                el.className = 'wl-total wl-total-empty';
                el.innerHTML = `<span class="wl-total-hint">Add quantity + avg buy price below to see your live P&amp;L.</span>`;
                return el;
            }
            const pnl = current - invested, pct = invested > 0 ? pnl / invested * 100 : 0;
            const cls = pnl >= 0 ? 'pos' : 'neg', sign = pnl >= 0 ? '+' : '';
            el.className = 'wl-total ' + cls;
            el.innerHTML = `
                <div class="wl-total-row">
                    <span class="wl-total-lbl">Unrealized P&amp;L</span>
                    <span class="wl-total-val ${cls}">${sign}₹${_wlFmt(Math.abs(pnl))}</span>
                </div>
                <div class="wl-total-sub">
                    <span class="${cls === 'pos' ? 'text-green-400' : 'text-red-400'}">${sign}${pct.toFixed(2)}%</span>
                    <span>Invested ₹${_wlFmt(invested)} · Now ₹${_wlFmt(current)}</span>
                </div>
                <div class="wl-total-note">Avg-cost · unrealized · ${n} of ${watchlist.length} priced</div>`;
            return el;
        }

        function renderWatchlistPanel() {
            const container = document.getElementById('watchlist-container');
            if (!container) return;

            if (watchlist.length === 0) {
                container.innerHTML = '<div class="text-center py-6 text-slate-500 text-sm border border-dashed border-white/20 rounded-xl bg-black/20">Your watchlist is empty.<br>Search and add stocks above.</div>';
                return;
            }

            container.innerHTML = '';
            container.appendChild(_wlTotalTile());   // portfolio P&L summary
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

                // Per-card P&L badge from the holdings (qty × avg vs live price).
                const p = _wlPnl(stock);
                let pnlHtml;
                if (p.state === 'ok') {
                    const c = p.pnl >= 0 ? 'pos' : 'neg', sg = p.pnl >= 0 ? '+' : '';
                    pnlHtml = `<span class="wl-pnl ${c}">${sg}₹${_wlFmt(Math.abs(p.pnl))} <span class="wl-pnl-pct">(${sg}${p.pct.toFixed(1)}%)</span></span>`;
                } else if (p.state === 'unresolved') {
                    pnlHtml = `<span class="wl-pnl wl-pnl-muted">price unavailable</span>`;
                } else if (p.state === 'warn') {
                    pnlHtml = `<span class="wl-pnl wl-pnl-warn">check avg vs price</span>`;
                } else {
                    pnlHtml = `<span class="wl-pnl wl-pnl-muted">add qty + avg</span>`;
                }

                const card = document.createElement('div');
                card.className = "p-3 bg-black/40 border border-white/5 rounded-xl hover:border-violet-500/20 transition-colors group";
                card.innerHTML = `
                    <div class="flex items-center justify-between">
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
                    </div>
                    <div class="wl-hold">
                        <label class="wl-h-field"><span>Qty</span><input type="number" min="0" step="any" inputmode="decimal" class="wl-qty" value="${stock.qty != null ? stock.qty : ''}" placeholder="0"></label>
                        <label class="wl-h-field"><span>Avg ₹</span><input type="number" min="0" step="any" inputmode="decimal" class="wl-avg" value="${stock.avgPrice != null ? stock.avgPrice : ''}" placeholder="0"></label>
                        ${pnlHtml}
                    </div>
                `;
                card.querySelector('.remove-watchlist-stock')?.addEventListener('click', () => {
                    removeStockFromWatchlist(stock.ticker);
                });
                card.querySelector('.wl-qty')?.addEventListener('change', (e) => _wlSetHolding(stock.ticker, 'qty', e.target.value));
                card.querySelector('.wl-avg')?.addEventListener('change', (e) => _wlSetHolding(stock.ticker, 'avgPrice', e.target.value));
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
                container.innerHTML = '<div class="glass-panel p-8 rounded-2xl text-center text-slate-400">News affecting your holdings appears here once you add stocks above — the same watchlist also powers your Portfolio Risk Radar.</div>';
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

        // ════════════════════════════════════════════════════════════
        // PORTFOLIO RISK RADAR — a daily LOW/MEDIUM/HIGH risk score for the
        // watchlist, broken down across stock / sector / news / macro /
        // valuation / technical weakness / F&O pressure. Backed by the pure
        // quantitative GET /api/portfolio/risk-radar (no LLM). Rendered into
        // #risk-radar at the top of the Portfolio tab's right column. The
        // section stays hidden until there's a watchlist AND a real score —
        // a cold-start / zero-data fetch never shows a broken shell.
        // ════════════════════════════════════════════════════════════
        let _riskRadarInflight = false;
        let _riskRadarLastKey = '';
        let _riskRadarLastTs = 0;

        function _rrLevelClass(level) {
            return level === 'HIGH' ? 'rr-high'
                : level === 'MEDIUM' ? 'rr-med'
                : level === 'LOW' ? 'rr-low' : 'rr-na';
        }
        function _rrLevelLabel(level) {
            return level === 'HIGH' ? 'High Risk'
                : level === 'MEDIUM' ? 'Medium Risk'
                : level === 'LOW' ? 'Low Risk' : 'N/A';
        }
        function _rrClassForScore(v) {
            return v == null ? 'rr-na' : v >= 62 ? 'rr-high' : v >= 34 ? 'rr-med' : 'rr-low';
        }

        async function loadRiskRadar(force = false) {
            const section = document.getElementById('risk-radar');
            if (!section) return;
            if (!watchlist || watchlist.length === 0) {
                // Don't hide it — show a professional teaser so the Risk Radar is
                // discoverable, and tell the user adding stocks unlocks it. The
                // same watchlist then powers the live score + the news below.
                section.classList.remove('hidden');
                section.innerHTML = _rrEmpty();
                _riskRadarLastKey = '';
                return;
            }
            const key = watchlist.map(s => s.ticker).sort().join(',');
            const now = Date.now();
            // Same watchlist within 60s → skip (server caches 30m anyway).
            if (!force && key === _riskRadarLastKey && (now - _riskRadarLastTs) < 60000) return;
            if (_riskRadarInflight) return;
            _riskRadarInflight = true;

            // First paint for a new watchlist → show a skeleton; otherwise keep
            // the prior content visible while we refresh in the background.
            if (key !== _riskRadarLastKey || section.classList.contains('hidden')) {
                section.classList.remove('hidden');
                section.innerHTML = _rrSkeleton();
            }
            try {
                const res = await fetch('/api/portfolio/risk-radar?tickers=' + encodeURIComponent(key));
                if (!res.ok) throw new Error('http ' + res.status);
                const data = await res.json();
                _riskRadarLastKey = key;
                _riskRadarLastTs = now;
                if (!data || !data.overall || !data.holdings_count) {
                    section.classList.add('hidden');
                    section.innerHTML = '';
                    return;
                }
                renderRiskRadar(data);
            } catch (e) {
                section.classList.remove('hidden');
                section.innerHTML = _rrErrorState();
            } finally {
                _riskRadarInflight = false;
            }
        }
        window.loadRiskRadar = loadRiskRadar;

        // Empty/teaser state for the Risk Radar (before any stock is added) —
        // makes the feature discoverable and points to the SAME watchlist that
        // powers both the radar and the portfolio news.
        function _rrEmpty() {
            const dims = [
                ['Per-stock', 'risk of each holding'],
                ['Concentration', 'over-exposure to one sector'],
                ['News flow', 'recent bearish signals'],
                ['Macro', 'India VIX + global shocks'],
                ['Valuation', 'rich P/E · P/B vs 52-week'],
                ['Technical', 'trend & momentum weakness'],
                ['F&O pressure', 'options / OI build-up'],
            ];
            const chips = dims.map(d =>
                `<div class="rr-empty-dim"><span class="rr-empty-dim-name">${d[0]}</span>`
                + `<span class="rr-empty-dim-desc">${d[1]}</span></div>`).join('');
            return `
            <div class="rr-empty">
                <div class="rr-empty-head">
                    <div class="rr-empty-icon">
                        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a10 10 0 1 0 10 10"/><path d="M12 12l7-7"/><path d="M12 7a5 5 0 1 0 5 5"/></svg>
                    </div>
                    <div>
                        <div class="rr-empty-title">Portfolio Risk Radar</div>
                        <div class="rr-empty-sub">A daily <strong>LOW / MEDIUM / HIGH</strong> risk score (0–100) for your holdings, scored across seven dimensions.</div>
                    </div>
                    <span class="rr-empty-badge">Daily</span>
                </div>
                <div class="rr-empty-dims">${chips}</div>
                <div class="rr-empty-cta">
                    <span class="rr-empty-cta-text">Add stocks to your watchlist to see your live risk score.</span>
                    <button type="button" class="rr-empty-btn" onclick="focusWatchlistSearch()">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>
                        Add stocks
                    </button>
                </div>
                <div class="rr-empty-note">One watchlist powers both your Risk Radar and the news below — add a stock once and it appears in both.</div>
            </div>`;
        }

        // Scroll to + focus the watchlist search so "Add stocks" is one click away.
        function focusWatchlistSearch() {
            const el = document.getElementById('stock-search-input');
            if (!el) return;
            try { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e) {}
            setTimeout(() => { try { el.focus(); } catch (e) {} }, 200);
        }
        window.focusWatchlistSearch = focusWatchlistSearch;

        function _rrMeter(score, level) {
            const pct = Math.max(2, Math.min(98, score || 0));
            return `
                <div class="rr-meter">
                    <div class="rr-meter-track">
                        <span class="rr-seg rr-seg-low"></span>
                        <span class="rr-seg rr-seg-med"></span>
                        <span class="rr-seg rr-seg-high"></span>
                        <span class="rr-meter-marker ${_rrLevelClass(level)}" style="left:${pct}%"></span>
                    </div>
                    <div class="rr-meter-scale"><span>Low</span><span>Medium</span><span>High</span></div>
                </div>`;
        }

        function _rrDimTile(d) {
            const lvl = d.score == null ? 'NA' : d.level;
            const cls = _rrLevelClass(lvl);
            const barPct = d.score == null ? 0 : Math.max(0, Math.min(100, d.score));
            let drivers;
            if (d.drivers && d.drivers.length) {
                drivers = d.drivers.map(dr => dr.ticker
                    ? `<span class="rr-chip">${escapeHtml(tickerSymbol(dr.ticker))} <b>${dr.score}</b></span>`
                    : `<span class="rr-chip rr-chip-text">${escapeHtml(dr.text || '')}</span>`
                ).join('');
            } else {
                drivers = `<span class="rr-dim-clear">No flags</span>`;
            }
            return `
                <div class="rr-dim ${cls}">
                    <div class="rr-dim-head">
                        <span class="rr-dim-label">${escapeHtml(d.label)}</span>
                        <span class="rr-dim-score">${d.score == null ? '—' : d.score}</span>
                    </div>
                    <div class="rr-dim-bar"><span style="width:${barPct}%"></span></div>
                    <div class="rr-dim-drivers">${drivers}</div>
                </div>`;
        }

        function _rrStockRow(s) {
            const cls = _rrLevelClass(s.level);
            const barPct = Math.max(0, Math.min(100, s.score));
            const dims = s.dims || {};
            const dimBadges = [
                ['Tech', dims.technical], ['News', dims.news], ['F&O', dims.fno], ['Val', dims.valuation]
            ].filter(pair => pair[1] != null).map(pair =>
                `<span class="rr-mini ${_rrClassForScore(pair[1])}">${pair[0]} ${pair[1]}</span>`
            ).join('');
            return `
                <div class="rr-stock">
                    <div class="rr-stock-top">
                        <div class="rr-stock-id">
                            <span class="rr-stock-ticker">${escapeHtml(tickerSymbol(s.ticker))}</span>
                            <span class="rr-stock-name">${escapeHtml(s.name || '')}</span>
                        </div>
                        <span class="rr-badge ${cls}">${s.score}</span>
                    </div>
                    <div class="rr-stock-bar"><span class="${cls}" style="width:${barPct}%"></span></div>
                    <div class="rr-stock-reason">${escapeHtml(s.top_reason || '')}</div>
                    ${dimBadges ? `<div class="rr-stock-dims">${dimBadges}</div>` : ''}
                </div>`;
        }

        function renderRiskRadar(data) {
            const section = document.getElementById('risk-radar');
            if (!section) return;
            const o = data.overall;
            const cls = _rrLevelClass(o.level);
            const asOf = data.as_of ? new Date(data.as_of) : null;
            const asOfStr = asOf && !isNaN(asOf) ? asOf.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true }) : '';
            const dimsHtml = (data.dimensions || []).map(_rrDimTile).join('');
            const stocksHtml = (data.by_stock || []).slice(0, 8).map(_rrStockRow).join('');
            const degraded = data.degraded
                ? `<span class="rr-degraded" title="Some data sources were unavailable; the score uses what loaded.">partial data</span>`
                : '';
            section.innerHTML = `
                <div class="glass-panel rr-panel ${cls}">
                    <div class="rr-header">
                        <div>
                            <div class="rr-kicker">Portfolio Risk Radar</div>
                            <div class="rr-sub">${data.holdings_count} holding${data.holdings_count === 1 ? '' : 's'}${asOfStr ? ' · as of ' + asOfStr : ''} ${degraded}</div>
                        </div>
                        <button type="button" class="rr-refresh" onclick="loadRiskRadar(true)" aria-label="Refresh risk radar" title="Refresh">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                        </button>
                    </div>
                    <div class="rr-hero">
                        <div class="rr-score-block ${cls}">
                            <div class="rr-score font-mono">${o.score}</div>
                            <div class="rr-level">${_rrLevelLabel(o.level)}</div>
                        </div>
                        <div class="rr-hero-text">
                            <div class="rr-headline">${escapeHtml(o.headline || '')}</div>
                            <div class="rr-summary">${escapeHtml(o.summary || '')}</div>
                            ${_rrMeter(o.score, o.level)}
                        </div>
                    </div>
                    <div class="rr-dims">${dimsHtml}</div>
                    ${stocksHtml ? `<div class="rr-stocks-head">Top risks by stock</div><div class="rr-stocks">${stocksHtml}</div>` : ''}
                    <div class="rr-foot">Quantitative model — technicals, F&amp;O, news flow, macro, valuation &amp; concentration. Not investment advice.</div>
                </div>`;
        }

        function _rrSkeleton() {
            return `<div class="glass-panel rr-panel rr-loading">
                <div class="rr-kicker">Portfolio Risk Radar</div>
                <div class="rr-skel rr-skel-hero"></div>
                <div class="rr-skel-grid">${'<div class="rr-skel rr-skel-tile"></div>'.repeat(6)}</div>
            </div>`;
        }
        function _rrErrorState() {
            return `<div class="glass-panel rr-panel">
                <div class="rr-kicker">Portfolio Risk Radar</div>
                <div class="rr-error">Couldn't compute your risk score right now — it'll retry shortly.</div>
            </div>`;
        }

