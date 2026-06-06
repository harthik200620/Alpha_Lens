function _rippleColorForDirection(dir) {
    return (dir || '').toUpperCase() === 'BULLISH' ? '#10b981' : '#f43f5e';
}
function _rippleBgForDirection(dir) {
    return (dir || '').toUpperCase() === 'BULLISH'
        ? 'rgba(16,185,129,0.12)'
        : 'rgba(244,63,94,0.12)';
}
function _rippleBorderForDirection(dir) {
    return (dir || '').toUpperCase() === 'BULLISH'
        ? 'rgba(16,185,129,0.35)'
        : 'rgba(244,63,94,0.35)';
}

let _rippleActiveNode = null;

function _renderRippleSidePanel(node, container) {
    _rippleActiveNode = node;
    // Reset all chip highlights
    document.querySelectorAll('.rfl-chip').forEach(el => el.classList.remove('rfl-chip--active'));
    if (node) {
        const el = document.getElementById(`rfl-chip-${node._uid}`);
        if (el) el.classList.add('rfl-chip--active');
    }
    if (!node) {
        container.innerHTML = `
            <div class="ripple-side-empty">
                <div class="ripple-side-empty-icon">⚡</div>
                <div class="ripple-side-empty-text">Click any stock chip to see its causal chain &amp; reasoning</div>
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
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <span class="ripple-side-direction ${dirCls}">
                    ${dir === 'BULLISH'
                        ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 14l6-6 6 6"/></svg>'
                        : '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 10l6 6 6-6"/></svg>'
                    }
                    ${dir || 'NEUTRAL'}
                </span>
                <span class="ripple-side-conf">Confidence ${node.confidence != null ? node.confidence : '—'}%</span>
            </div>
            <div class="ripple-side-reason">${escapeHtml(node.reason || 'No detailed reason provided.')}</div>
        </div>`;
}

async function _renderRippleGraph(payload) {
    const wrap = document.getElementById('ripple-graph-wrap');
    const sideEl = document.getElementById('ripple-side');
    const loadingEl = document.getElementById('ripple-loading');
    const svgEl = document.getElementById('ripple-graph');

    if (loadingEl) loadingEl.style.display = 'none';
    if (svgEl) svgEl.style.display = 'none'; // hide the legacy SVG element

    // Build tier data
    const tiers = Array.isArray(payload.tiers) ? payload.tiers : [];
    let uidCounter = 0;
    const tierDefs = [
        { num: 1, label: 'Tier 1', sublabel: 'Direct Impact',        color: '#fbbf24', borderColor: 'rgba(251,191,36,0.30)' },
        { num: 2, label: 'Tier 2', sublabel: 'Supply Chain',         color: '#60a5fa', borderColor: 'rgba(96,165,250,0.30)' },
        { num: 3, label: 'Tier 3', sublabel: 'Macro Transmission',   color: '#a78bfa', borderColor: 'rgba(167,139,250,0.30)' },
    ];

    const resolvedTiers = tierDefs.map(td => {
        const found = tiers.find(t => t.tier === td.num);
        const nodes = (found && found.nodes) ? found.nodes.map(n => ({ ...n, tier: td.num, _uid: uidCounter++ })) : [];
        return { ...td, nodes };
    });

    // Remove any previous arrow-flow container
    const prev = wrap.querySelector('.rfl-container');
    if (prev) prev.remove();

    // Build the HTML arrow-flow layout
    const triggerLabel = escapeHtml(payload.instrument || payload.headline || 'MACRO EVENT');
    const allTiersEmpty = resolvedTiers.every(t => t.nodes.length === 0);

    if (allTiersEmpty) {
        wrap.insertAdjacentHTML('beforeend', `
            <div class="rfl-container rfl-empty-state">
                <div class="ripple-side-empty-icon">⚡</div>
                <div class="ripple-side-empty-text">No propagation data available for this event yet.</div>
            </div>`);
        return;
    }

    let flowHTML = `<div class="rfl-container">`;

    // ── Trigger block ──
    flowHTML += `
        <div class="rfl-column">
            <div class="rfl-col-header">
                <span class="rfl-col-label" style="color:#c4b5fd;">TRIGGER</span>
                <span class="rfl-col-sublabel">Shock Event</span>
            </div>
            <div class="rfl-chip rfl-chip--trigger">
                <span class="rfl-chip-icon">⚡</span>
                <span class="rfl-chip-ticker">${triggerLabel}</span>
            </div>
        </div>`;

    // ── Tier columns ──
    resolvedTiers.forEach((td, idx) => {
        if (td.nodes.length === 0) return;

        // Arrow separator — flows from the previous tier's color into this one.
        const fromColor = idx === 0 ? '#c4b5fd' : (tierDefs[idx-1] ? tierDefs[idx-1].color : '#c4b5fd');
        flowHTML += `
            <div class="rfl-arrow-col">
                <svg class="rfl-arrow-svg" viewBox="0 0 64 18" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <defs>
                        <linearGradient id="rfl-grad-${idx}" x1="0" y1="0" x2="1" y2="0">
                            <stop offset="0%" stop-color="${fromColor}" stop-opacity="0.85"/>
                            <stop offset="100%" stop-color="${td.color}" stop-opacity="1"/>
                        </linearGradient>
                        <marker id="rfl-arrow-${idx}" markerWidth="9" markerHeight="9" refX="5" refY="4" orient="auto">
                            <path d="M0,0 L0,8 L8,4 z" fill="${td.color}"/>
                        </marker>
                    </defs>
                    <line class="rfl-arrow-flow" x1="2" y1="9" x2="50" y2="9"
                        stroke="url(#rfl-grad-${idx})" stroke-width="3" stroke-linecap="round"
                        marker-end="url(#rfl-arrow-${idx})"
                        stroke-dasharray="6 5" />
                </svg>
                <span class="rfl-arrow-label" style="color:${td.color};">${td.nodes.length} stock${td.nodes.length !== 1 ? 's' : ''}</span>
            </div>`;

        // Tier column
        flowHTML += `
            <div class="rfl-column">
                <div class="rfl-col-header">
                    <span class="rfl-col-label" style="color:${td.color};">${td.label}</span>
                    <span class="rfl-col-sublabel">${td.sublabel}</span>
                </div>
                <div class="rfl-chips-wrap">`;

        td.nodes.forEach(n => {
            const dir = (n.direction || '').toUpperCase();
            const isBull = dir === 'BULLISH';
            const chipColor = isBull ? '#34d399' : '#fb7185';
            const chipBg = isBull ? 'rgba(16,185,129,0.10)' : 'rgba(244,63,94,0.10)';
            const chipBorder = isBull ? 'rgba(16,185,129,0.30)' : 'rgba(244,63,94,0.30)';
            const arrowIcon = isBull
                ? `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 14l6-6 6 6"/></svg>`
                : `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 10l6 6 6-6"/></svg>`;
            flowHTML += `
                <div class="rfl-chip" id="rfl-chip-${n._uid}"
                    data-uid="${n._uid}"
                    style="border-color:${chipBorder};background:${chipBg};"
                    onclick="_rflChipClick(${n._uid})">
                    <span class="rfl-chip-dir" style="color:${chipColor};">${arrowIcon}</span>
                    <span class="rfl-chip-ticker">${escapeHtml(n.ticker || '')}</span>
                    ${n.confidence != null ? `<span class="rfl-chip-conf" style="color:${td.color};">${n.confidence}%</span>` : ''}
                </div>`;
        });

        flowHTML += `</div></div>`;
    });

    flowHTML += `</div>`; // close rfl-container

    wrap.insertAdjacentHTML('beforeend', flowHTML);

    // Store node data globally for click handler
    window._rflNodeMap = {};
    resolvedTiers.forEach(td => {
        td.nodes.forEach(n => { window._rflNodeMap[n._uid] = n; });
    });

    // Reset side panel
    _renderRippleSidePanel(null, sideEl);
}

window._rflChipClick = function(uid) {
    const node = window._rflNodeMap && window._rflNodeMap[uid];
    const sideEl = document.getElementById('ripple-side');
    if (!sideEl) return;
    if (_rippleActiveNode && _rippleActiveNode._uid === uid) {
        // Deselect on second click
        _rippleActiveNode = null;
        _renderRippleSidePanel(null, sideEl);
    } else {
        _renderRippleSidePanel(node, sideEl);
    }
};

async function openRipple(newsId) {
    const modal = document.getElementById('ripple-modal');
    const headline = document.getElementById('ripple-headline');
    const summary = document.getElementById('ripple-summary');
    const loading = document.getElementById('ripple-loading');
    const svg = document.getElementById('ripple-graph');
    const side = document.getElementById('ripple-side');
    const wrap = document.getElementById('ripple-graph-wrap');

    if (!modal) return;
    headline.innerText = 'Loading…';
    summary.innerText = '';
    // Clear any previous arrow-flow
    if (wrap) { const old = wrap.querySelector('.rfl-container'); if (old) old.remove(); }
    if (loading) loading.style.display = 'flex';
    if (svg) { svg.style.display = 'none'; }
    _rippleActiveNode = null;
    if (side) {
        side.innerHTML = `
            <div class="ripple-side-empty">
                <div class="ripple-side-empty-icon">⚡</div>
                <div class="ripple-side-empty-text">Click any stock chip to see its causal chain &amp; reasoning</div>
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
    // Clean up arrow-flow on close
    const wrap = document.getElementById('ripple-graph-wrap');
    if (wrap) { const old = wrap.querySelector('.rfl-container'); if (old) old.remove(); }
    _rippleActiveNode = null;
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

