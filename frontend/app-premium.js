        function initPremiumFeatures() {
            updateTickerBar();
            setInterval(() => { if (!document.hidden) updateTickerBar(); }, 30000);
            pollSignalNotifications();
            setInterval(() => { if (!document.hidden) pollSignalNotifications(); }, 30000);
            initPremiumInteractions();
            // Live-pulse intensity stays (purposeful: reflects real worker activity).
            // Cursor-trail + scroll-parallax were removed — gimmicky, not premium.
            initLivePulseIntensity();
        }

        // ── Tab-visibility gating ──────────────────────────────────────────
        // While the browser tab is backgrounded: pause the always-on CSS
        // background animations (via the body.is-hidden class — see styles.css)
        // and skip the recurring poll fetches (each poll callback checks
        // document.hidden). On return-to-visible, force ONE immediate refresh
        // so the user sees fresh numbers instantly. We call the FETCH functions
        // directly here — NEVER the recursive schedulers — so we don't spawn
        // duplicate timer chains. Top-level registration so it's wired no
        // matter the init order.
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) { document.body.classList.add('is-hidden'); return; }
            document.body.classList.remove('is-hidden');
            try { if (typeof fetchIndices === 'function') fetchIndices(); } catch (e) {}
            try { if (typeof fetchLiveNews === 'function') fetchLiveNews(); } catch (e) {}
            try { if (typeof updateTickerBar === 'function') updateTickerBar(); } catch (e) {}
            try { if (typeof loadCommandBar === 'function') loadCommandBar(); } catch (e) {}
        });

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
            setInterval(() => { if (!document.hidden) tick(); }, 30000);
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
            // PERF: the two cursor-tracking "spotlight" pointermove handlers were
            // REMOVED. They fired on every mouse move and one of them called
            // getBoundingClientRect() (forced synchronous layout) plus rewrote a
            // full-screen background gradient — i.e. a whole-page repaint on every
            // pixel of cursor motion. That was the single biggest source of lag.
            // The body::after glow now stays static (its CSS defaults to 50%/18%),
            // which looks the same but costs nothing.

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

