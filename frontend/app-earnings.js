/* ============================================================
 * app-earnings.js  (chunk 7/10 — loads after app-terminal.js)
 * Earnings & Results Intelligence tab — sits between the Signal
 * Terminal and Track Record. Auto-summarizes the latest quarterly
 * results for the user's watchlist holdings (or, if the watchlist
 * is empty, the names the engine is currently tracking):
 * revenue / profit / margin / EPS-surprise + a transparent verdict,
 * with an optional, grounded AI brief (tone / guidance / order book).
 *
 * Data: GET /api/earnings/intelligence?tickers=A,B,C  (server-cached
 * 6h; quantitative core needs no AI keys). Classic script, shared
 * global scope — reuses escapeHtml(), tickerSymbol(), `watchlist`.
 * ============================================================ */

let _eiLastKey = '';
let _eiLastTs = 0;
let _eiInflight = false;

function _eiWatchKey() {
    try {
        if (Array.isArray(watchlist) && watchlist.length) {
            return watchlist.map(s => s.ticker).sort().join(',');
        }
    } catch (_) { /* watchlist may not be ready */ }
    return '__auto__';
}

async function loadEarningsIntel(force = false) {
    const body = document.getElementById('earnings-body');
    if (!body) return;
    const key = _eiWatchKey();
    const now = Date.now();
    // Same holdings within 60s → skip (server caches 6h anyway).
    if (!force && key === _eiLastKey && (now - _eiLastTs) < 60000) return;
    if (_eiInflight) return;
    _eiInflight = true;

    // First paint for a new holding set → skeleton; otherwise keep
    // the prior cards on screen while refreshing in the background.
    if (key !== _eiLastKey || !body.dataset.loaded) {
        body.innerHTML = _eiSkeleton();
    }
    try {
        const qs = key === '__auto__' ? '' : ('?tickers=' + encodeURIComponent(key));
        const res = await fetch('/api/earnings/intelligence' + qs);
        if (!res.ok) throw new Error('http ' + res.status);
        const data = await res.json();
        _eiLastKey = key;
        _eiLastTs = now;
        body.dataset.loaded = '1';
        renderEarningsIntel(data);
    } catch (e) {
        body.innerHTML = _eiError();
    } finally {
        _eiInflight = false;
    }
}
window.loadEarningsIntel = loadEarningsIntel;

function renderEarningsIntel(data) {
    const body = document.getElementById('earnings-body');
    if (!body) return;
    const cards = (data && data.cards) || [];
    if (!cards.length) {
        body.innerHTML = _eiEmpty(data);
        return;
    }
    const upcoming = data.upcoming || [];
    const upHtml = upcoming.length ? `
        <div class="ei-upcoming">
            <div class="ei-sub-head">Upcoming results</div>
            <div class="ei-upcoming-row">${upcoming.map(_eiUpcoming).join('')}</div>
        </div>` : '';
    const degraded = data.degraded
        ? `<span class="ei-degraded" title="Some names could not be resolved; showing what loaded.">partial data</span>`
        : '';
    body.innerHTML = `
        ${_eiStatsRow(data)}
        ${data.headline ? `<div class="ei-headline">${escapeHtml(data.headline)}</div>` : ''}
        <div class="ei-cards">${cards.map(_eiCard).join('')}</div>
        ${upHtml}
        <div class="ei-foot">Figures from public quarterly filings via market data, auto-summarized for clarity. ${degraded} Not investment advice.</div>`;
}

function _eiStatsRow(data) {
    const tiles = [
        ['Reported', data.covered_count || 0, ''],
        ['Beats', data.beats || 0, (data.beats ? 'ei-pos' : '')],
        ['Misses', data.misses || 0, (data.misses ? 'ei-neg' : '')],
        ['Upcoming', (data.upcoming || []).length, ''],
    ];
    return `<div class="ei-stats">${tiles.map(t => `
        <div class="ei-stat">
            <div class="ei-stat-label">${t[0]}</div>
            <div class="ei-stat-value font-mono ${t[2]}">${t[1]}</div>
        </div>`).join('')}</div>`;
}

function _eiVerdictClass(level) {
    return level === 'Strong' ? 'ei-strong' : level === 'Weak' ? 'ei-weak' : 'ei-mixed';
}
function _eiDeltaCls(v) {
    return v == null ? '' : v > 0 ? 'ei-up' : v < 0 ? 'ei-down' : '';
}

function _eiFmtDate(iso) {
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' });
    } catch (_) { return iso; }
}

function _eiMetricTile(label, value, deltaStr, deltaVal, suffix) {
    const cls = _eiDeltaCls(deltaVal);
    const delta = (deltaStr && deltaStr !== '—')
        ? `<span class="ei-delta ${cls}">${escapeHtml(deltaStr)}${suffix ? ' ' + suffix : ''}</span>`
        : '';
    return `
        <div class="ei-metric">
            <div class="ei-metric-label">${escapeHtml(label)}</div>
            <div class="ei-metric-value font-mono">${escapeHtml(value || '—')}</div>
            ${delta}
        </div>`;
}

function _eiSurpriseTile(s) {
    const deltaCls = s.tone === 'pos' ? 'ei-up' : s.tone === 'neg' ? 'ei-down' : '';
    const badgeCls = s.tone === 'pos' ? 'ei-pos' : s.tone === 'neg' ? 'ei-neg' : 'ei-neutral';
    let sub = '';
    if (s.reported_eps != null && s.eps_estimate != null) {
        sub = `EPS ${s.reported_eps} vs est ${s.eps_estimate}`;
    } else if (s.pct_str && s.pct_str !== '—') {
        sub = s.pct_str;
    }
    return `
        <div class="ei-metric">
            <div class="ei-metric-label">vs Estimates</div>
            <div class="ei-metric-value"><span class="ei-surprise ${badgeCls}">${escapeHtml(s.label || '—')}</span></div>
            ${sub ? `<span class="ei-delta ${deltaCls} font-mono">${escapeHtml(sub)}</span>` : ''}
        </div>`;
}

function _eiBrief(b) {
    const toneCls = b.management_tone === 'Positive' ? 'ei-pos'
        : b.management_tone === 'Cautious' ? 'ei-neg' : 'ei-neutral';
    const rows = [];
    if (b.guidance) rows.push(['Guidance', b.guidance]);
    if (b.order_book) rows.push(['Order book', b.order_book]);
    const rowsHtml = rows.map(r =>
        `<div class="ei-brief-row"><span class="ei-brief-k">${escapeHtml(r[0])}</span><span class="ei-brief-v">${escapeHtml(r[1])}</span></div>`
    ).join('');
    return `
        <div class="ei-brief">
            <div class="ei-brief-head">
                <span class="ei-brief-kicker">AI Results Brief</span>
                <span class="ei-tone ${toneCls}">${escapeHtml(b.management_tone || 'Neutral')} tone</span>
            </div>
            ${b.plain_summary ? `<div class="ei-brief-summary">${escapeHtml(b.plain_summary)}</div>` : ''}
            ${b.tone_note ? `<div class="ei-brief-note">${escapeHtml(b.tone_note)}</div>` : ''}
            ${rowsHtml}
        </div>`;
}

function _eiCard(c) {
    const m = c.metrics || {};
    const rev = m.revenue || {}, pat = m.profit || {}, nm = m.net_margin || {};
    const s = c.surprise || {};
    const v = c.verdict || {};
    const vCls = _eiVerdictClass(v.level);
    const reported = c.reported_date ? _eiFmtDate(c.reported_date) : '';
    const metrics = [
        _eiMetricTile('Revenue', rev.value_str, rev.yoy_str, rev.yoy, 'YoY'),
        _eiMetricTile('Net Profit', pat.value_str, pat.yoy_str, pat.yoy, 'YoY'),
        _eiMetricTile('Net Margin', nm.value_str, nm.chg_str, nm.chg_bps, ''),
        _eiSurpriseTile(s),
    ].join('');
    const drivers = (v.drivers && v.drivers.length)
        ? `<div class="ei-drivers">${v.drivers.map(d => `<span class="ei-driver">${escapeHtml(d)}</span>`).join('')}</div>`
        : '';
    const ai = c.ai_brief ? _eiBrief(c.ai_brief) : '';
    return `
        <div class="ei-card ${vCls}">
            <div class="ei-card-head">
                <div class="ei-id">
                    <span class="ei-ticker">${escapeHtml(tickerSymbol(c.ticker))}</span>
                    <span class="ei-name">${escapeHtml(c.name || '')}</span>
                    ${c.sector && c.sector !== '—' ? `<span class="ei-sector">${escapeHtml(c.sector)}</span>` : ''}
                </div>
                <div class="ei-qmeta">
                    <span class="ei-verdict ${vCls}">${escapeHtml(v.level || '—')}</span>
                    <span class="ei-quarter">${escapeHtml(c.quarter || '')}</span>
                    ${reported ? `<span class="ei-reported">Reported ${escapeHtml(reported)}</span>` : ''}
                </div>
            </div>
            <div class="ei-summary">${escapeHtml(c.summary || '')}</div>
            <div class="ei-metrics">${metrics}</div>
            ${drivers}
            ${ai}
        </div>`;
}

function _eiUpcoming(u) {
    return `<span class="ei-up-chip"><b>${escapeHtml(tickerSymbol(u.ticker))}</b> ${escapeHtml(_eiFmtDate(u.date))}</span>`;
}

function _eiEmpty(data) {
    const msg = (data && data.headline) ? data.headline
        : 'No recent quarterly results found for your holdings yet.';
    return `
        <div class="term-empty">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M3 3v18h18M7 14l3-3 3 3 5-6"/></svg>
            <div class="term-empty-title">No results to summarize yet</div>
            <div class="term-empty-sub">${escapeHtml(msg)} Earnings cards appear here automatically once your holdings report.</div>
        </div>`;
}

function _eiError() {
    return `
        <div class="term-empty">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>
            <div class="term-empty-title">Couldn't reach the earnings engine</div>
            <div class="term-empty-sub">We'll retry automatically. Quarterly data loads from market sources and can take a moment.</div>
        </div>`;
}

function _eiSkeleton() {
    const card = `<div class="ei-card ei-skel">
        <span class="skel skel-cell" style="width:42%;height:18px"></span>
        <span class="skel skel-cell" style="width:92%;height:12px;margin-top:12px"></span>
        <div class="ei-metrics">${'<div class="ei-metric"><span class="skel skel-cell" style="width:60%;height:9px"></span><span class="skel skel-cell" style="width:80%;height:16px;margin-top:8px"></span></div>'.repeat(4)}</div>
    </div>`;
    const stat = `<div class="ei-stat"><span class="skel skel-cell" style="width:55%;height:9px"></span><span class="skel skel-cell" style="width:34%;height:22px;margin-top:8px"></span></div>`;
    return `<div class="ei-stats">${stat.repeat(4)}</div><div class="ei-cards">${card.repeat(2)}</div>`;
}
