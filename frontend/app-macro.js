// ── NIFTY NEXT-SESSION OUTLOOK (deterministic pre-open bias) ──────────────
function _mpOutColor(stance) {
    if (stance === 'BULLISH' || stance === 'MILD_BULLISH') return 'var(--green)';
    if (stance === 'BEARISH' || stance === 'MILD_BEARISH') return 'var(--red)';
    return 'var(--amber)';
}
function _mpInr(v, d = 0) {
    const n = Number(v);
    return isFinite(n) ? n.toLocaleString('en-IN', { maximumFractionDigits: d }) : '—';
}

async function loadNiftyOutlook() {
    const el = document.getElementById('mp-nifty-outlook');
    if (!el) return;
    try {
        const res = await fetch('/api/macro/nifty-outlook');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        _mpRenderNiftyOutlook(await res.json());
    } catch (e) {
        el.hidden = true;   // never show a broken tile
    }
}

function _mpRenderNiftyOutlook(d) {
    const el = document.getElementById('mp-nifty-outlook');
    if (!el) return;
    if (!d || d.applicable === false) {
        el.hidden = false;
        el.innerHTML = `<div class="mp-out-head"><div class="mp-out-kicker"><span class="mp-out-dot"></span>NIFTY NEXT-SESSION OUTLOOK</div></div>`
            + `<div class="mp-out-empty">Awaiting the global macro board — the next-session bias populates once overnight cues load.</div>`;
        return;
    }
    el.hidden = false;
    const color = _mpOutColor(d.stance);
    const expSign = d.expected_move_pct >= 0 ? '+' : '';
    const drivers = (d.drivers || []).map(c => {
        const w = Math.min(100, Math.abs(c.contribution_pct) / 1.2 * 100);
        const cls = c.contribution_pct >= 0 ? 'bull' : 'bear';
        return `<div class="mp-out-driver" title="${escapeHtml(c.why || '')}">
            <div class="mp-out-dl"><span class="mp-out-dname">${escapeHtml(c.label)}</span><span class="mp-out-dchg ${c.change_pct >= 0 ? 'bull' : 'bear'}">${c.change_pct >= 0 ? '+' : ''}${Number(c.change_pct).toFixed(2)}%</span></div>
            <div class="mp-out-dbar"><div class="mp-out-dfill ${cls}" style="width:${w}%"></div></div>
            <div class="mp-out-dcontrib ${cls}">${c.contribution_pct >= 0 ? '+' : ''}${Number(c.contribution_pct).toFixed(2)}%</div>
        </div>`;
    }).join('');
    const projRange = (d.projected_low != null && d.projected_high != null)
        ? `${_mpInr(d.projected_low)} – ${_mpInr(d.projected_high)}` : '—';

    el.innerHTML = `
        <div class="mp-out-head">
            <div class="mp-out-kicker"><span class="mp-out-dot"></span>NIFTY NEXT-SESSION OUTLOOK</div>
            <span class="mp-out-horizon">${escapeHtml(d.horizon || '')}${d.horizon_note ? ' · ' + escapeHtml(d.horizon_note) : ''}</span>
        </div>
        <div class="mp-out-body">
            <div class="mp-out-left">
                <div class="mp-out-level-lbl">NIFTY 50 · last</div>
                <div class="mp-out-level">${_mpInr(d.nifty_last)}</div>
                <div class="mp-out-proj">~68% range <strong>${projRange}</strong></div>
                ${(d.projected_wide_low != null && d.projected_wide_high != null) ? `<div class="mp-out-proj mp-out-proj-wide">~95% ${_mpInr(d.projected_wide_low)} – ${_mpInr(d.projected_wide_high)}</div>` : ''}
            </div>
            <div class="mp-out-mid">
                <div class="mp-out-stance" style="color:${color}">${escapeHtml(d.stance_label || '')}</div>
                <div class="mp-out-move" style="color:${color}">${expSign}${Number(d.expected_move_pct).toFixed(2)}%</div>
                <div class="mp-out-rangepct">~68% band ${Number(d.range_low_pct).toFixed(2)}% to ${Number(d.range_high_pct).toFixed(2)}%${(d.wide_low_pct != null) ? ` · ~95% ${Number(d.wide_low_pct).toFixed(2)}% to ${Number(d.wide_high_pct).toFixed(2)}%` : ''}</div>
                <div class="mp-out-conf">
                    <div class="mp-out-conf-bar"><div class="mp-out-conf-fill" style="width:${d.confidence}%;background:${color}"></div></div>
                    <span class="mp-out-conf-num">${d.confidence}% conviction</span>
                </div>
            </div>
        </div>
        <div class="mp-out-drivers-title">What is driving it <span class="mp-out-cues">${d.bull} bullish · ${d.bear} bearish cues</span></div>
        <div class="mp-out-drivers">${drivers}</div>
        <p class="mp-out-summary">${escapeHtml(d.summary || '')}</p>
        <p class="mp-out-disclaimer"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/></svg>${escapeHtml(d.disclaimer || '')}</p>`;
}

async function fetchMacroPulse() {
    const chipsEl  = document.getElementById('macro-pulse-chips');
    const countEl  = document.getElementById('macro-pulse-count');
    const snapGrid = document.getElementById('macro-snapshot-grid');
    // New UI elements
    const shockCountEl      = document.getElementById('mp-shock-count');
    const actionableCountEl = document.getElementById('mp-actionable-count');
    const instrumentCountEl = document.getElementById('mp-instruments-count');
    const regimeValueEl     = document.getElementById('mp-regime-value');
    const regimeFillEl      = document.getElementById('mp-regime-fill');
    const alertDotEl        = document.getElementById('mp-alert-indicator');

    if (!chipsEl) return;

    // Nifty Next-Session Outlook — independent fetch, renders its own tile.
    loadNiftyOutlook();

    try {
        const res = await fetch('/api/macro/events');

        if (!res.ok) {
            _mpRenderError(chipsEl, snapGrid, countEl, _MP_WARN_SVG, 'Feed Offline', 'Unable to fetch live macroeconomic events. Check your connection.');
            return;
        }

        const data     = await res.json();
        const events   = (data && data.events)   || [];
        const snapshot = (data && data.snapshot) || [];

        // ── Update stats row ──
        const actionable = events.filter(e => !e.during_nse_hours).length;
        if (shockCountEl)      shockCountEl.textContent      = events.length;
        if (actionableCountEl) actionableCountEl.textContent = actionable;
        if (instrumentCountEl) instrumentCountEl.textContent = snapshot.length || '—';
        if (countEl)           countEl.textContent = events.length
            ? `${events.length} active shock${events.length === 1 ? '' : 's'}`
            : '0 shocks';

        // ── Update regime card ──
        _mpUpdateRegime(events, regimeValueEl, regimeFillEl);

        // ── Alert dot ──
        if (alertDotEl) {
            if (events.length) {
                alertDotEl.classList.add('is-active');
            } else {
                alertDotEl.classList.remove('is-active');
            }
        }

        // ── Render alert cards ──
        if (!events.length) {
            chipsEl.innerHTML = `
                <div class="mp-alert-empty">
                    <div class="mp-alert-empty-icon"><svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/></svg></div>
                    <div class="mp-alert-empty-title">All Systems Stable</div>
                    <p class="mp-alert-empty-sub">No macroeconomic shock thresholds have been breached in the last 24 hours. Global regime is nominal.</p>
                </div>`;
        } else {
            chipsEl.innerHTML = events.map(ev => _mpRenderAlertCard(ev)).join('');
            chipsEl.querySelectorAll('[data-macro-event-id]').forEach(btn => {
                btn.addEventListener('click', () => {
                    const id = parseInt(btn.getAttribute('data-macro-event-id'), 10);
                    // Ripple 2.0 — deterministic quant cascade (falls back to the
                    // legacy LLM ripple only if the new renderer isn't loaded).
                    if (typeof openRipple2 === 'function') openRipple2(id);
                    else openMacroRipple(id);
                });
            });
        }

        // ── Render instrument table ──
        _mpRenderSnapshotTable(snapGrid, snapshot, events);

    } catch (err) {
        console.error('[MacroPulse] fetch error:', err);
        _mpRenderError(chipsEl, snapGrid, countEl, _MP_WARN_SVG, 'Connection Error', 'Could not load macro data. Will retry automatically in 90s.');
    }
}

/** Determine and render the macro regime indicator */
function _mpUpdateRegime(events, valueEl, fillEl) {
    if (!valueEl || !fillEl) return;
    const majorCount = events.filter(e => (e.shock_level || '').toLowerCase() === 'major').length;
    const sigCount   = events.filter(e => (e.shock_level || '').toLowerCase() === 'significant').length;
    let label, cls, fillPct;
    if (majorCount >= 2) {
        label = 'SHOCK REGIME'; cls = 'regime-shock'; fillPct = 90;
    } else if (majorCount === 1 || sigCount >= 3) {
        label = 'ELEVATED RISK'; cls = 'regime-caution'; fillPct = 60;
    } else if (sigCount >= 1 || events.length > 0) {
        label = 'CAUTION'; cls = 'regime-caution'; fillPct = 40;
    } else {
        label = 'STABLE'; cls = 'regime-stable'; fillPct = 15;
    }
    valueEl.textContent = label;
    valueEl.className = `mp-regime-value ${cls}`;
    fillEl.style.width = fillPct + '%';
    // Colour the fill based on regime
    if (cls === 'regime-shock')   fillEl.style.background = 'linear-gradient(90deg,var(--red),#ff6b8f)';
    else if (cls === 'regime-caution') fillEl.style.background = 'linear-gradient(90deg,var(--amber),var(--accent-bright))';
    else fillEl.style.background = 'linear-gradient(90deg,var(--green),#34e0a0)';
}

/** Robustly format a DB timestamp ('YYYY-MM-DD HH:MM:SS', assumed UTC) as IST
 *  clock time. Returns '' if unparseable — fixes the "Invalid Date" bug that
 *  came from appending 'Z' to a space-separated (non-ISO) datetime. */
function _mpFmtDetected(s) {
    if (!s) return '';
    let t = String(s).trim().replace(' ', 'T');
    if (!/[zZ]$|[+-]\d\d:?\d\d$/.test(t)) t += 'Z';   // treat naive time as UTC
    const d = new Date(t);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Asia/Kolkata' });
}

/** Render a single premium alert card */
function _mpRenderAlertCard(ev) {
    const pct         = parseFloat(ev.change_pct_1d || 0);
    const isUp        = pct >= 0;
    const pctFmt      = `${isUp ? '+' : ''}${pct.toFixed(2)}%`;
    const levelRaw    = (ev.shock_level || '').toLowerCase();
    const levelCls    = levelRaw === 'major' ? 'major' : 'significant';
    const isActionable = !ev.during_nse_hours;
    const label       = escapeHtml(ev.instrument_label || ev.symbol || ev.instrument_key || '—');
    const lastPx      = ev.last_price != null
        ? parseFloat(ev.last_price).toLocaleString(undefined, { maximumFractionDigits: 4 })
        : '—';
    const prevPx      = ev.prev_close != null
        ? parseFloat(ev.prev_close).toLocaleString(undefined, { maximumFractionDigits: 4 })
        : '—';
    const detectedAt  = _mpFmtDetected(ev.detected_at);

    // Volatility-normalized z-score (how many σ this move is vs its own history)
    const sigmaVal = (ev.sigma != null && isFinite(ev.sigma)) ? Math.abs(ev.sigma) : null;
    const sigmaTitle = sigmaVal != null
        ? `${sigmaVal.toFixed(1)}σ move — vol-normalized z-score${ev.pctile != null ? ` · ${Math.round(ev.pctile)}th percentile vs its own 6-month history` : ''}`
        : '';
    const sigmaChip = sigmaVal != null
        ? `<span class="mp-alert-sigma ${sigmaVal >= 3.5 ? 'hot' : ''}" title="${sigmaTitle}">${sigmaVal.toFixed(1)}&sigma;</span>`
        : '';

    const arrowSvg = isUp
        ? `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 14l6-6 6 6"/></svg>`
        : `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 10l6 6 6-6"/></svg>`;

    // Generate Systemic Impact Predictor list
    let effectsHtml = '';
    if (ev.effects && ev.effects.length) {
        const effectsList = ev.effects.map(eff => {
            const isBullish = eff.direction === 'BULLISH';
            const pillClass = isBullish ? 'bullish' : 'bearish';
            const sign = isBullish ? '+' : '';
            return `
                <div class="mp-effect-item">
                    <span class="mp-effect-ticker">${escapeHtml(eff.name || eff.ticker)}</span>
                    <span class="mp-effect-pill ${pillClass}">${sign}${eff.expected_move_pct.toFixed(2)}%</span>
                </div>`;
        }).join('');
        
        effectsHtml = `
            <div class="mp-card-effects">
                <div class="mp-effects-title">Systemic Impact Predictor</div>
                <div class="mp-effects-list">
                    ${effectsList}
                </div>
            </div>`;
    }

    return `
        <button class="mp-alert-card shock-${levelCls}" data-macro-event-id="${ev.id}" data-has-ripple="${ev.has_ripple}" aria-label="Open Ripple 2.0 for ${label}">
            <div class="mp-alert-head">
                <span class="mp-alert-name">${label}</span>
                <span class="mp-alert-pct ${isUp ? 'up' : 'down'}">${arrowSvg}${pctFmt}</span>
            </div>
            <div class="mp-alert-quote">
                <div class="mp-alert-q">
                    <span class="mp-alert-q-k">Last</span>
                    <span class="mp-alert-q-v">${lastPx}</span>
                </div>
                <div class="mp-alert-q">
                    <span class="mp-alert-q-k">Prev</span>
                    <span class="mp-alert-q-v">${prevPx}</span>
                </div>
            </div>
            <div class="mp-alert-badges">
                <span class="mp-alert-level-badge ${levelCls}">${escapeHtml(ev.shock_level || '')}</span>
                ${sigmaChip}
                <span class="mp-alert-action-badge ${isActionable ? 'actionable' : 'info'}">
                    ${isActionable ? '<svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor" style="vertical-align:-1px;margin-right:3px"><path d="M7 2v11h3v9l7-12h-4l4-8z"/></svg>Actionable' : '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align:-2px;margin-right:3px"><circle cx="12" cy="12" r="10"/><line x1="12" y1="11" x2="12" y2="16"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>Info'}
                </span>
            </div>
            ${effectsHtml}
            <div class="mp-alert-footer">
                <span class="mp-alert-detected">${detectedAt ? 'Detected ' + detectedAt + ' IST' : 'Active shock'}</span>
                <span class="mp-alert-ripple-cta">
                    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>
                    Ripple 2.0
                </span>
            </div>
        </button>`;
}

/** Render the live snapshot as a professional table */
function _mpRenderSnapshotTable(tbodyEl, snapshot, events) {
    if (!tbodyEl) return;
    if (!snapshot || !snapshot.length) {
        tbodyEl.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:32px;color:var(--text-muted);font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:0.10em;">NO LIVE DATA AVAILABLE</td></tr>`;
        return;
    }
    // Build a set of shocked instrument keys for badge display
    const shockedKeys = new Set(events.map(e => e.instrument_key || ''));

    // ── Deduplicate by instrument key so each instrument shows only once ──
    const seen = new Set();
    const uniqueSnapshot = snapshot.filter(item => {
        const key = item.key || item.instrument_key || item.label || item.instrument_label || '';
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });

    tbodyEl.innerHTML = uniqueSnapshot.map(item => {
        const pct    = parseFloat(item.change_pct_1d || 0);
        const isUp   = pct >= 0;
        const pctFmt = `${isUp ? '+' : ''}${pct.toFixed(2)}%`;
        const label  = item.label || item.instrument_label || item.key || item.instrument_key || '';
        const key    = item.key || item.instrument_key || '';
        
        const lastVal = item.last != null ? item.last : item.last_price;
        const prevCloseVal = item.prev_close != null ? item.prev_close : (lastVal != null ? (lastVal / (1 + pct / 100)) : null);
        
        const lastPx = lastVal != null
            ? parseFloat(lastVal).toLocaleString(undefined, { maximumFractionDigits: 4 })
            : '—';
            
        const prevPx = prevCloseVal != null
            ? parseFloat(prevCloseVal).toLocaleString(undefined, { maximumFractionDigits: 4 })
            : '—';
            
        // Calculate absolute point change
        let absDiffFmt = '—';
        if (lastVal != null && prevCloseVal != null) {
            const diff = lastVal - prevCloseVal;
            absDiffFmt = `${diff >= 0 ? '+' : ''}${diff.toLocaleString(undefined, { maximumFractionDigits: 4 })}`;
        }
        
        const isShock = item.is_shock_3pct || item.is_shock_5pct || shockedKeys.has(key);
        const dirCls  = isUp ? 'up' : 'down';
        const arrowSvg = isUp
            ? `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 14l6-6 6 6"/></svg>`
            : `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 10l6 6 6-6"/></svg>`;

        // Render mini-tags for Systemic Effects
        let effectsHtml = '<span style="color:var(--text-muted);font-size:10px;">No systemic shock impact</span>';
        if (item.effects && item.effects.length) {
            effectsHtml = `
                <div class="mp-table-effects-container">
                    ${item.effects.map(eff => {
                        const isBull = eff.direction === 'BULLISH';
                        const tagCls = isBull ? 'bullish' : 'bearish';
                        const sign = isBull ? '+' : '';
                        return `
                            <span class="mp-table-effect-tag ${tagCls}">
                                ${escapeHtml(eff.name || eff.ticker.split('.')[0])} ${sign}${eff.expected_move_pct.toFixed(1)}%
                            </span>`;
                    }).join('')}
                </div>`;
        } else if (isShock) {
            effectsHtml = '<span style="color:var(--text-tertiary);font-size:10px;">Evaluating shock impact...</span>';
        }

        return `
            <tr class="${isShock ? 'is-shock-row' : ''}">
                <td>
                    <div class="mp-td-instrument">
                        <div>
                            <div class="mp-td-instrument-label">${escapeHtml(label)}${isShock ? ' <span class="mp-td-shock-badge"><svg viewBox="0 0 24 24" width="10" height="10" fill="currentColor" style="vertical-align:-1px;margin-right:2px"><path d="M7 2v11h3v9l7-12h-4l4-8z"/></svg>SHOCK</span>' : ''}</div>
                            ${key && key !== label ? `<div class="mp-td-instrument-key">${escapeHtml(key.toUpperCase())}</div>` : ''}
                        </div>
                    </div>
                </td>
                <td class="text-right">
                    <span class="mp-td-price" style="color:var(--text-tertiary);">${prevPx}</span>
                </td>
                <td class="text-right">
                    <span class="mp-td-price">${lastPx}</span>
                </td>
                <td class="text-right">
                    <div style="display:flex;flex-direction:column;align-items:flex-end;">
                        <span class="mp-td-pct ${dirCls}" style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;font-size:11px;">${arrowSvg}${pctFmt}</span>
                        <span style="font-size:9px;color:var(--text-muted);font-family:'JetBrains Mono',monospace;margin-top:2px;">${(item.sigma != null && isFinite(item.sigma)) ? Math.abs(item.sigma).toFixed(1) + '&sigma; · ' : ''}${absDiffFmt}</span>
                    </div>
                </td>
                <td>
                    ${effectsHtml}
                </td>
                <td class="text-center">
                    <span class="mp-status-badge ${isShock ? 'shock' : 'normal'}">
                        ${isShock ? '<svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor" style="vertical-align:-1px;margin-right:3px"><path d="M7 2v11h3v9l7-12h-4l4-8z"/></svg>SHOCK' : '<span class="pill-dot"></span>Normal'}
                    </span>
                </td>
            </tr>`;
    }).join('');
}

/** Warning glyph (SVG, not emoji — keeps iconography consistent with the design system) */
const _MP_WARN_SVG = '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';

/** Render an error state into both alert and table areas */
function _mpRenderError(chipsEl, snapGrid, countEl, icon, title, sub) {
    if (chipsEl) chipsEl.innerHTML = `
        <div class="mp-alert-empty" style="border-color:var(--red-border);background:var(--red-dim);">
            <div class="mp-alert-empty-icon" style="color:var(--red)">${icon}</div>
            <div class="mp-alert-empty-title">${title}</div>
            <p class="mp-alert-empty-sub">${sub}</p>
        </div>`;
    if (snapGrid) snapGrid.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:32px;color:var(--text-muted);font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:0.10em;">FEED UNAVAILABLE</td></tr>`;
    if (countEl) countEl.textContent = 'Error';
}


async function openMacroRipple(eventId) {
    // Reuse the existing ripple-modal shell; point at the macro endpoint.
    const modal = document.getElementById('ripple-modal');
    const headline = document.getElementById('ripple-headline');
    const summary = document.getElementById('ripple-summary');
    const loading = document.getElementById('ripple-loading');
    const svg = document.getElementById('ripple-graph');
    const side = document.getElementById('ripple-side');
    const wrap = document.getElementById('ripple-graph-wrap');
    if (!modal) return;
    headline.innerText = 'Loading macro ripple…';
    summary.innerText = '';
    // Clear any previous arrow-flow
    if (wrap) { const old = wrap.querySelector('.rfl-container'); if (old) old.remove(); }
    if (loading) loading.style.display = 'flex';
    if (svg) svg.style.display = 'none';
    _rippleActiveNode = null;
    if (side) {
        side.innerHTML = `
            <div class="ripple-side-empty">
                <div class="ripple-side-empty-icon"><svg viewBox="0 0 24 24" width="34" height="34" fill="currentColor"><path d="M7 2v11h3v9l7-12h-4l4-8z"/></svg></div>
                <div class="ripple-side-empty-text">Click any stock chip to see its causal chain &amp; reasoning</div>
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

// Boot: do NOT auto-fetch on page load. The macro tab is hidden on the
// dashboard, so firing here just burned 2 API calls (/api/macro/nifty-outlook
// + /api/macro/events) + a perpetual 90s poll on EVERY page load, competing
// with the dashboard's own calls on cold start. switchTab('macro-pulse')
// (app-core.js) already calls fetchMacroPulse() when the user opens the tab.
// Keep a refresh poll, but ONLY while the macro tab is actually visible
// (mirrors the F&O / Calendar auto-poll-while-visible pattern).
function _mpTabVisible() {
    const v = document.getElementById('view-macro-pulse');
    return !!(v && v.offsetParent !== null && !document.hidden);
}
setInterval(() => { if (_mpTabVisible()) fetchMacroPulse(); }, 90 * 1000);

window.openMacroRipple = openMacroRipple;
window.fetchMacroPulse = fetchMacroPulse;

// ════════════════════════════════════════════════════════════════════════
// THE CALENDAR — forward catalyst tracker
// Fetches /api/calendar, renders day-by-day event groups, opens a slide-in
// detail modal with 3-scenario analysis + historical analogues.
// ════════════════════════════════════════════════════════════════════════

let _calData = null;
let _calFilter = 'all'; // 'all' | 'HIGH' | 'IN' | 'US' | 'CENTRAL_BANK'
let _calCountdownTimer = null;

