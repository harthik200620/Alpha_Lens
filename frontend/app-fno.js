/* ===========================================================================
 * app-fno.js  (chunk 9/10)  —  F&O SMART MONEY
 *
 * Renders the F&O Smart-Money board: institutional positioning decoded from the
 * daily NSE derivatives bhavcopy. Market-wide bias, the four OI×price buildup
 * quadrants, unusual OI surges, delivery conviction, the index option matrix
 * (PCR / max-pain / OI walls), sector clustering, bulk/block deals, and a
 * per-stock option-chain drill-down modal.
 *
 * Data: GET /api/fno/smart-money?tickers=...  +  GET /api/fno/option-chain/<sym>
 * Both are deterministic (no LLM). Lazy-loaded by switchTab('fno').
 * Classic script — shares the global scope with the other app-*.js chunks.
 * ======================================================================== */

let _fnoData = null;
let _fnoLastFetch = 0;
let _fnoLoading = false;
const _FNO_THROTTLE_MS = 60000;   // client throttle over the server cache

function _fnoWatchlistTickers() {
    // `watchlist` is a global from app-stocks.js: [{ticker, name}, ...].
    try {
        if (Array.isArray(window.watchlist)) {
            return window.watchlist.map(w => (w && (w.ticker || w.symbol)) || '').filter(Boolean);
        }
        const raw = JSON.parse(localStorage.getItem('alpha_lens_watchlist') || '[]');
        return raw.map(w => (w && (w.ticker || w.symbol)) || w).filter(Boolean);
    } catch (e) { return []; }
}

// ── formatters ────────────────────────────────────────────────────────────
function _fnoOI(n) {
    n = Number(n || 0);
    const a = Math.abs(n);
    if (a >= 1e7) return (n / 1e7).toFixed(2) + ' Cr';
    if (a >= 1e5) return (n / 1e5).toFixed(2) + ' L';
    if (a >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return String(Math.round(n));
}
function _fnoNumF(n, d = 0) {
    const v = Number(n);
    if (!isFinite(v)) return '—';
    return v.toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function _fnoMove(pct, digits = 2) {
    const v = Number(pct || 0);
    const up = v >= 0;
    const arrow = up
        ? '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 14l6-6 6 6"/></svg>'
        : '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 10l6 6 6-6"/></svg>';
    return `<span class="fno-move ${up ? 'bull' : 'bear'}">${arrow}${up ? '+' : ''}${v.toFixed(digits)}%</span>`;
}
function _fnoDirClass(dir) {
    return dir === 'bullish' ? 'bull' : dir === 'bearish' ? 'bear' : 'flat';
}
function _fnoBiasColor(label) {
    return label === 'BULLISH' ? 'var(--green)' : label === 'BEARISH' ? 'var(--red)' : 'var(--amber)';
}
function _fnoConvBar(conv, dir) {
    const c = Math.max(0, Math.min(99, Number(conv || 0)));
    return `<div class="fno-conv"><div class="fno-conv-fill ${_fnoDirClass(dir)}" style="width:${c}%"></div><span class="fno-conv-num">${c}</span></div>`;
}
function _fnoSentChip(s) {
    const cls = s === 'BULLISH' ? 'bull' : s === 'BEARISH' ? 'bear' : 'flat';
    return `<span class="fno-chip ${cls}">${escapeHtml(s || 'NEUTRAL')}</span>`;
}
function _fnoStar(on) {
    return on
        ? '<svg class="fno-star" width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l3 7h7l-5.5 4 2 7L12 16l-6.5 4 2-7L2 9h7z"/></svg>'
        : '';
}

// ── main fetch ──────────────────────────────────────────────────────────
async function fetchFnoSmartMoney(force) {
    const now = Date.now();
    if (!force && _fnoData && (now - _fnoLastFetch) < _FNO_THROTTLE_MS) return;
    if (_fnoLoading) return;
    _fnoLoading = true;
    try {
        const tickers = _fnoWatchlistTickers();
        const q = tickers.length ? `?tickers=${encodeURIComponent(tickers.join(','))}` : '';
        const res = await fetch(`/api/fno/smart-money${q}`);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        _fnoData = data;
        _fnoLastFetch = Date.now();
        _renderFno(data);
    } catch (err) {
        _fnoRenderError(err);
    } finally {
        _fnoLoading = false;
    }
}

function _renderFno(d) {
    if (!d || d.applicable === false || !d.universe_count) { _fnoRenderEmpty(d); return; }
    _fnoRenderBias(d);
    _fnoRenderStats(d);
    _fnoRenderMeta(d);
    _fnoRenderNarrative(d);
    _fnoRenderParticipant(d.participant, (d.degraded || {}).participant);
    _fnoRenderIndexMatrix(d.index_matrix || []);
    _fnoRenderQuadrants(d.buildups || {});
    _fnoRenderSetups(d.setups || []);
    _fnoRenderUnusual(d.unusual_oi || []);
    _fnoRenderDelivery(d.delivery_spikes || [], (d.degraded || {}).delivery);
    _fnoRenderSectors(d.sectors || []);
    _fnoRenderDeals(d.deals || [], (d.degraded || {}).deals);
    _fnoHandleBuildingState(d);
}

// While the live snapshot is "building", retry fast (every 15s) so the board
// flips to LIVE within seconds of the background build finishing — instead of
// waiting for the normal 3-min poll. Self-stops once the state leaves 'building'.
let _fnoBuildTimer = null;
function _fnoHandleBuildingState(d) {
    const building = ((d && d.intraday_status) || {}).state === 'building';
    if (building && !_fnoBuildTimer && _fnoPollTimer) {
        _fnoBuildTimer = setTimeout(() => {
            _fnoBuildTimer = null;
            if (!document.hidden) fetchFnoSmartMoney(true);   // force past the client throttle
        }, 15000);
    } else if (!building && _fnoBuildTimer) {
        clearTimeout(_fnoBuildTimer);
        _fnoBuildTimer = null;
    }
}

// ── hero: bias gauge + stats + meta ───────────────────────────────────────
function _fnoRenderBias(d) {
    const b = d.market_bias || { score: 0, label: 'NEUTRAL' };
    const valEl = document.getElementById('fno-bias-value');
    const fillEl = document.getElementById('fno-bias-fill');
    const subEl = document.getElementById('fno-bias-sub');
    const color = _fnoBiasColor(b.label);
    if (valEl) { valEl.textContent = b.label; valEl.style.color = color; }
    if (fillEl) {
        const pct = Math.max(2, Math.min(98, (Number(b.score || 0) + 100) / 2));
        fillEl.style.width = pct + '%';
        fillEl.style.background = color;
    }
    if (subEl) {
        subEl.innerHTML = `Bias score <strong style="color:${color}">${Number(b.score || 0) >= 0 ? '+' : ''}${Number(b.score || 0).toFixed(0)}</strong> `
            + `· ${_fnoNumF(b.bull_pressure)} bull / ${_fnoNumF(b.bear_pressure)} bear pressure`;
    }
}
function _fnoRenderStats(d) {
    const c = d.counts || {};
    const vix = d.india_vix;
    const vixTxt = (vix === null || vix === undefined) ? '—' : Number(vix).toFixed(2);
    const vixCls = (vix == null) ? 'flat' : (vix >= 18 ? 'bear' : vix <= 12 ? 'bull' : 'flat');
    const cells = [
        [_fnoNumF(c['Long Buildup'] || 0), 'Long Buildup', 'bull'],
        [_fnoNumF(c['Short Buildup'] || 0), 'Short Buildup', 'bear'],
        [vixTxt, 'India VIX', vixCls],
        [_fnoNumF(d.universe_count || 0), 'F&O Universe', 'flat'],
    ];
    const el = document.getElementById('fno-stats');
    if (!el) return;
    el.innerHTML = cells.map((x, i) =>
        `${i ? '<div class="fno-stat-divider"></div>' : ''}<div class="fno-stat-cell"><div class="fno-stat-value ${x[2]}">${x[0]}</div><div class="fno-stat-label">${x[1]}</div></div>`
    ).join('');
}
// Small inline swap icon (emoji-free, per the design system).
const _FNO_SWAP_SVG = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10l-4 4 4 4"/><path d="M3 14h12"/><path d="M17 14l4-4-4-4"/><path d="M21 10H9"/></svg>';

function _fnoFmtAsOfIST(iso) {
    try {
        return new Date(iso).toLocaleTimeString('en-IN',
            { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' });
    } catch (e) { return ''; }
}
// ms until the next ~19:30 IST F&O bhavcopy publish (skips weekends).
function _fnoNextBhavcopyMs() {
    try {
        const istNow = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
        const target = new Date(istNow);
        target.setHours(19, 30, 0, 0);
        if (istNow >= target) target.setDate(target.getDate() + 1);
        while (target.getDay() === 0 || target.getDay() === 6) target.setDate(target.getDate() + 1);
        return Math.max(0, target.getTime() - istNow.getTime());
    } catch (e) { return 0; }
}
function _fnoFmtDur(ms) {
    if (!isFinite(ms) || ms < 0) ms = 0;
    const h = Math.floor(ms / 3600000), m = Math.floor((ms % 3600000) / 60000);
    return h >= 1 ? `${h}h ${m}m` : `${m}m`;
}
function _fnoTickCountdown() {
    const el = document.getElementById('fno-countdown');
    if (el) el.textContent = _fnoFmtDur(_fnoNextBhavcopyMs());
}
// HH:MM(:SS) in IST for the "Refreshed …" stamp.
function _fnoFmtTimeIST(iso, withSecs) {
    try {
        const opts = { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' };
        if (withSecs) opts.second = '2-digit';
        return new Date(iso).toLocaleTimeString('en-GB', opts);
    } catch (e) { return ''; }
}

function _fnoRenderMeta(d) {
    const el = document.getElementById('fno-meta');
    if (!el) return;
    const src = d.source || 'eod';
    const live = src.indexOf('intraday') === 0;
    const st = (d.intraday_status || {}).state || 'off';
    const wl = (d.watchlist || []).length;
    let pills = '';

    if (live) {
        // Angel One intraday OI (#5): live during market hours, OI deltas vs the
        // previous close. Honest green pill + the as-of time.
        const asof = d.as_of ? _fnoFmtAsOfIST(d.as_of) : '';
        pills += `<span class="fno-meta-pill fno-live"><span class="pill-dot"></span>LIVE${asof ? ' · ' + asof + ' IST' : ''}</span>`;
        if (d.bhavcopy_date) pills += `<span class="fno-meta-pill">OI vs ${escapeHtml(d.bhavcopy_date)} close</span>`;
    } else {
        // End-of-day bhavcopy: label "as of <date> close" + a live countdown to
        // tonight's publish so the board visibly flips the moment it lands.
        const cached = src === 'eod_restored';
        const dateTxt = d.bhavcopy_date ? `As of ${escapeHtml(d.bhavcopy_date)} close` : 'Bhavcopy pending';
        pills += `<span class="fno-meta-pill fno-eod">END-OF-DAY${cached ? ' · CACHED' : ''}</span>`;
        pills += `<span class="fno-meta-pill"><span class="pill-dot"></span>${dateTxt}</span>`;
        // Live-build status (only meaningful once Angel intraday is enabled).
        if (st === 'building') {
            pills += `<span class="fno-meta-pill fno-building"><span class="fno-spin"></span>Building live data…</span>`;
        } else if (st === 'unavailable') {
            pills += `<span class="fno-meta-pill fno-unavail">Live data unavailable here · showing end-of-day</span>`;
        } else if (st === 'closed') {
            pills += `<span class="fno-meta-pill">Live OI resumes at market open</span>`;
        }
        pills += `<span class="fno-meta-pill">Next update in <span id="fno-countdown">${_fnoFmtDur(_fnoNextBhavcopyMs())}</span></span>`;
    }

    // Day-over-day change summary (#4).
    const ch = d.changes;
    if (ch && (ch.flipped_count || ch.new_count)) {
        const bits = [];
        if (ch.flipped_count) bits.push(`${ch.flipped_count} flipped`);
        if (ch.new_count) bits.push(`${ch.new_count} new`);
        pills += `<span class="fno-meta-pill fno-changed">${_FNO_SWAP_SVG} ${bits.join(' · ')}${ch.prev_date ? ' vs ' + escapeHtml(ch.prev_date) : ''}</span>`;
    }

    if (wl) pills += `<span class="fno-meta-pill">${wl} in your watchlist</span>`;

    // When this board was last refreshed on the server (always shown).
    const refreshed = d.served_at ? _fnoFmtTimeIST(d.served_at, true) : '';
    if (refreshed) pills += `<span class="fno-meta-pill fno-refreshed">Refreshed ${refreshed} IST</span>`;

    el.innerHTML = pills;
}
function _fnoRenderNarrative(d) {
    const el = document.getElementById('fno-narrative');
    if (!el) return;
    el.innerHTML = `<div class="fno-narr-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a10 10 0 100 20 10 10 0 000-20z"/><path d="M12 8v4M12 16h.01"/></svg></div>`
        + `<p class="fno-narr-text">${escapeHtml(d.narrative || '')}</p>`;
}

// ── FII / DII / Pro / Client positioning (the literal smart money) ──────────
function _fnoRenderParticipant(p, degraded) {
    const el = document.getElementById('fno-participant');
    if (!el) return;
    if (degraded || !p || p.applicable === false) {
        el.innerHTML = _fnoMini(degraded
            ? 'FII/DII participant data unavailable from the source today.'
            : 'FII/DII positioning appears once the participant file publishes (~7:30 PM IST).');
        return;
    }
    const h = p.headline;
    const head = h ? `<div class="fno-part-head">
        <div class="fno-part-fii">
            <span class="fno-part-lbl">FII INDEX FUTURES · NET</span>
            <span class="fno-part-net ${h.bias === 'BULLISH' ? 'bull' : h.bias === 'BEARISH' ? 'bear' : 'flat'}">${h.fii_index_fut_net >= 0 ? '+' : ''}${_fnoNumF(h.fii_index_fut_net)}</span>
            ${_fnoSentChip(h.bias)}
        </div>
        <div class="fno-part-sum">${escapeHtml(h.summary || '')}</div>
    </div>` : '';
    const bars = (p.cohorts || []).map(c => {
        const ls = c.long_share;
        const cls = ls == null ? 'flat' : (ls > 55 ? 'bull' : ls < 45 ? 'bear' : 'flat');
        return `<div class="fno-part-row">
            <span class="fno-part-cohort">${escapeHtml(c.cohort)}</span>
            <div class="fno-part-bar"><div class="fno-part-fill ${cls}" style="width:${ls != null ? Math.min(100, ls) : 50}%"></div></div>
            <span class="fno-part-share ${cls}">${ls != null ? ls.toFixed(0) + '% long' : '—'}</span>
            <span class="fno-part-idx ${c.fut_index_net >= 0 ? 'bull' : 'bear'}">idx ${c.fut_index_net >= 0 ? '+' : ''}${_fnoNumF(c.fut_index_net)}</span>
        </div>`;
    }).join('');
    el.innerHTML = head + `<div class="fno-part-bars">${bars}</div>`;
}

// ── deterministic setups (bias + levels) ────────────────────────────────────
function _fnoRenderSetups(setups) {
    const el = document.getElementById('fno-setups');
    if (!el) return;
    if (!setups || !setups.length) { el.innerHTML = _fnoMini('No high-conviction setups today.'); return; }
    el.innerHTML = `<div class="fno-setups">` + setups.map(s => {
        const cls = s.stance === 'Bullish' ? 'bull' : s.stance === 'Bearish' ? 'bear' : 'flat';
        const lv = s.levels || {};
        const levels = [
            lv.support != null ? `S ${_fnoNumF(lv.support, 0)}` : null,
            lv.magnet != null ? `MP ${_fnoNumF(lv.magnet, 0)}` : null,
            lv.resistance != null ? `R ${_fnoNumF(lv.resistance, 0)}` : null,
        ].filter(Boolean).join('  ·  ');
        return `<div class="fno-setup" onclick="openFnoOptionChain('${escapeHtml(s.symbol)}')">
            <div class="fno-setup-top">${_fnoStar(false)}<span class="fno-sym">${escapeHtml(s.symbol)}</span><span class="fno-chip ${cls}">${escapeHtml(s.stance)}</span><span class="fno-setup-conv">${s.conviction != null ? s.conviction : ''}</span></div>
            <div class="fno-setup-idea">${escapeHtml(s.idea || '')}</div>
            ${levels ? `<div class="fno-setup-levels">${levels}</div>` : ''}
        </div>`;
    }).join('') + `</div>`;
}

// ── index option matrix ───────────────────────────────────────────────────
function _fnoRenderIndexMatrix(rows) {
    const el = document.getElementById('fno-index-grid');
    if (!el) return;
    if (!rows.length) {
        el.innerHTML = _fnoMini('No index option data in the latest bhavcopy.');
        document.getElementById('fno-index-section').style.display = '';
        return;
    }
    el.innerHTML = rows.map(r => {
        const gap = r.max_pain_gap_pct;
        const gapTxt = (gap === null || gap === undefined) ? '' :
            `<span class="fno-idx-gap ${gap >= 0 ? 'bull' : 'bear'}">${gap >= 0 ? '+' : ''}${Number(gap).toFixed(1)}% vs spot</span>`;
        const pcr = (r.pcr === null || r.pcr === undefined) ? '—' : Number(r.pcr).toFixed(2);
        const pcrCls = r.pcr >= 1.2 ? 'bull' : r.pcr <= 0.8 ? 'bear' : 'flat';
        return `<button type="button" class="fno-idx-card" onclick="openFnoOptionChain('${escapeHtml(r.symbol)}')">
            <div class="fno-idx-head"><span class="fno-idx-name">${escapeHtml(r.label || r.symbol)}</span>${_fnoSentChip(r.sentiment)}</div>
            <div class="fno-idx-spot">${r.spot ? _fnoNumF(r.spot, 0) : '—'}</div>
            <div class="fno-idx-pcr"><span class="fno-idx-pcr-num ${pcrCls}">${pcr}</span><span class="fno-idx-pcr-lbl">PCR</span></div>
            <div class="fno-idx-rows">
                <div class="fno-idx-row"><span>Max Pain</span><strong>${r.max_pain ? _fnoNumF(r.max_pain, 0) : '—'}</strong></div>
                ${gapTxt ? `<div class="fno-idx-row"><span></span>${gapTxt}</div>` : ''}
                <div class="fno-idx-row"><span>Call Wall (R)</span><strong class="bear">${r.call_wall ? _fnoNumF(r.call_wall, 0) : '—'}</strong></div>
                <div class="fno-idx-row"><span>Put Wall (S)</span><strong class="bull">${r.put_wall ? _fnoNumF(r.put_wall, 0) : '—'}</strong></div>
                <div class="fno-idx-row"><span>ATM IV</span><strong>${r.atm_iv != null ? Number(r.atm_iv).toFixed(1) + '%' : '—'}</strong></div>
                <div class="fno-idx-row"><span>Basis</span><strong class="${r.basis_pct >= 0 ? 'bull' : 'bear'}">${r.basis_pct != null ? (r.basis_pct >= 0 ? '+' : '') + Number(r.basis_pct).toFixed(2) + '%' : '—'}</strong></div>
            </div>
        </button>`;
    }).join('');
}

// ── smart-money quadrants ─────────────────────────────────────────────────
const _FNO_QUADS = [
    ['LONG_BUILDUP', 'Long Buildup', 'bull', 'Fresh longs · price ↑ OI ↑'],
    ['SHORT_BUILDUP', 'Short Buildup', 'bear', 'Fresh shorts · price ↓ OI ↑'],
    ['SHORT_COVERING', 'Short Covering', 'bull', 'Shorts exiting · price ↑ OI ↓'],
    ['LONG_UNWINDING', 'Long Unwinding', 'bear', 'Longs exiting · price ↓ OI ↓'],
];
// A NEW (just entered F&O positioning) / FLIPPED (direction switched vs the
// previous day) chip — the day-over-day "what changed" tag (#4).
function _fnoChangeTag(vp) {
    if (!vp) return '';
    if (vp.is_new) return '<span class="fno-tag new" title="New vs previous session">NEW</span>';
    if (vp.flipped) {
        const t = vp.buildup_prev_label ? `Flipped from ${vp.buildup_prev_label}` : 'Direction flipped vs previous session';
        return `<span class="fno-tag flip" title="${escapeHtml(t)}">FLIPPED</span>`;
    }
    return '';
}
function _fnoRowTable(rows) {
    if (!rows.length) return `<div class="fno-quad-empty">No names in this bucket today.</div>`;
    return `<table class="fno-table"><thead><tr><th>Symbol</th><th class="fno-th-num">Price</th><th class="fno-th-num">OI Δ</th><th>Conviction</th></tr></thead><tbody>` + rows.map(r => `
        <tr onclick="openFnoOptionChain('${escapeHtml(r.symbol)}')">
            <td class="fno-td-sym">${_fnoStar(r.in_watchlist)}<span class="fno-sym">${escapeHtml(r.symbol)}</span>${_fnoChangeTag(r.vs_prev)}<span class="fno-sec">${escapeHtml(r.sector)}</span></td>
            <td class="fno-td-num" data-label="Price">${_fnoMove(r.px_chg_pct)}</td>
            <td class="fno-td-num" data-label="OI Δ">${_fnoMove(r.oi_chg_pct)}</td>
            <td class="fno-td-conv" data-label="Conviction">${_fnoConvBar(r.conviction, r.direction)}</td>
        </tr>`).join('') + `</tbody></table>`;
}
function _fnoRenderQuadrants(buildups) {
    const el = document.getElementById('fno-quadrants');
    if (!el) return;
    el.innerHTML = _FNO_QUADS.map(([key, label, cls, hint]) => {
        const rows = buildups[key] || [];
        return `<div class="fno-quad fno-quad-${cls}">
            <div class="fno-quad-head">
                <div><div class="fno-quad-title">${label}</div><div class="fno-quad-hint">${hint}</div></div>
                <span class="fno-quad-count ${cls}">${rows.length}</span>
            </div>
            ${_fnoRowTable(rows)}
        </div>`;
    }).join('');
}

// ── unusual OI ─────────────────────────────────────────────────────────────
function _fnoRenderUnusual(rows) {
    const el = document.getElementById('fno-unusual');
    if (!el) return;
    if (!rows.length) { el.innerHTML = _fnoMini('No unusual OI surges today.'); return; }
    el.innerHTML = `<div class="fno-list">` + rows.map(r => `
        <div class="fno-list-row" onclick="openFnoOptionChain('${escapeHtml(r.symbol)}')">
            <div class="fno-list-main"><span class="fno-sym">${escapeHtml(r.symbol)}</span><span class="fno-chip ${_fnoDirClass(r.direction)}">${escapeHtml(r.buildup_label)}</span></div>
            <div class="fno-list-stat"><span class="fno-big ${_fnoDirClass(r.direction)}">${r.oi_chg_pct >= 0 ? '+' : ''}${Number(r.oi_chg_pct).toFixed(0)}%</span><span class="fno-small">OI · ${_fnoMove(r.px_chg_pct)}</span></div>
        </div>`).join('') + `</div>`;
}

// ── delivery conviction ────────────────────────────────────────────────────
function _fnoRenderDelivery(rows, degraded) {
    const el = document.getElementById('fno-delivery');
    if (!el) return;
    if (degraded) { el.innerHTML = _fnoMini('Delivery data unavailable from the source today.'); return; }
    if (!rows.length) { el.innerHTML = _fnoMini('No high-delivery accumulation flagged today.'); return; }
    el.innerHTML = `<div class="fno-list">` + rows.map(r => `
        <div class="fno-list-row" onclick="openFnoOptionChain('${escapeHtml(r.symbol)}')">
            <div class="fno-list-main"><span class="fno-sym">${escapeHtml(r.symbol)}</span><span class="fno-chip ${_fnoDirClass(r.direction)}">${escapeHtml(r.buildup_label)}</span></div>
            <div class="fno-deliv"><div class="fno-deliv-bar"><div class="fno-deliv-fill" style="width:${Math.min(100, Number(r.delivery_pct || 0))}%"></div></div><span class="fno-deliv-num">${Number(r.delivery_pct).toFixed(0)}%</span></div>
        </div>`).join('') + `</div>`;
}

// ── sector clustering ──────────────────────────────────────────────────────
function _fnoRenderSectors(rows) {
    const el = document.getElementById('fno-sectors');
    if (!el) return;
    if (!rows.length) { el.innerHTML = _fnoMini('No sector-level clustering detected.'); return; }
    el.innerHTML = `<div class="fno-sectors">` + rows.map(r => {
        const net = Number(r.net_bias || 0);
        const w = Math.min(50, Math.abs(net) / 2);   // half-bar, 0..50%
        const cls = net > 15 ? 'bull' : net < -15 ? 'bear' : 'flat';
        return `<div class="fno-sec-row">
            <div class="fno-sec-name">${escapeHtml(r.sector)}<span class="fno-sec-count">${r.count}</span></div>
            <div class="fno-sec-track">
                <div class="fno-sec-bar ${cls}" style="width:${w}%;${net >= 0 ? 'left:50%' : 'right:50%'}"></div>
                <div class="fno-sec-axis"></div>
            </div>
            <div class="fno-sec-val ${cls}">${net >= 0 ? '+' : ''}${net.toFixed(0)}</div>
        </div>`;
    }).join('') + `</div>`;
}

// ── bulk / block deals ─────────────────────────────────────────────────────
function _fnoRenderDeals(rows, degraded) {
    const el = document.getElementById('fno-deals');
    if (!el) return;
    if (degraded || !rows.length) {
        el.innerHTML = _fnoMini(degraded ? 'Bulk/block deal feed unavailable from the source today.' : 'No bulk or block deals reported in the latest session.');
        return;
    }
    el.innerHTML = `<table class="fno-table fno-deals-table"><thead><tr>
            <th>Stock</th><th>Side</th><th>Client</th><th class="fno-num">Qty</th><th class="fno-num">Price</th><th>Type</th></tr></thead><tbody>`
        + rows.map(r => `<tr>
            <td class="fno-td-sym"><span class="fno-sym">${escapeHtml(r.symbol)}</span></td>
            <td data-label="Side"><span class="fno-chip ${r.side === 'BUY' ? 'bull' : r.side === 'SELL' ? 'bear' : 'flat'}">${escapeHtml(r.side || '—')}</span></td>
            <td class="fno-td-client" data-label="Client">${escapeHtml(r.client || '—')}</td>
            <td class="fno-num" data-label="Qty">${escapeHtml(r.qty || '—')}</td>
            <td class="fno-num" data-label="Price">${escapeHtml(r.price || '—')}</td>
            <td data-label="Type"><span class="fno-chip flat">${escapeHtml((r.kind || '').toUpperCase())}</span></td>
        </tr>`).join('') + `</tbody></table>`;
}

// ── shared tiny states ─────────────────────────────────────────────────────
function _fnoMini(msg) {
    return `<div class="fno-mini">${escapeHtml(msg)}</div>`;
}
function _fnoRenderEmpty(d) {
    const grid = document.getElementById('fno-index-grid');
    const quad = document.getElementById('fno-quadrants');
    const narr = document.getElementById('fno-narrative');
    const degraded = (d && d.degraded) || {};
    const why = degraded.futures
        ? 'The NSE F&O bhavcopy could not be reached right now. It publishes ~7-8 PM IST each trading day — this view fills in once it is available.'
        : 'No F&O positioning to show yet. Check back after the market session and the evening bhavcopy publish.';
    const shell = `<div class="fno-empty">
        <div class="fno-empty-icon"><svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/></svg></div>
        <div class="fno-empty-title">No F&amp;O data right now</div>
        <p class="fno-empty-sub">${why}</p></div>`;
    if (narr) narr.innerHTML = shell;
    if (grid) grid.innerHTML = '';
    if (quad) quad.innerHTML = '';
    ['fno-participant', 'fno-setups', 'fno-unusual', 'fno-delivery', 'fno-sectors', 'fno-deals'].forEach(id => {
        const e = document.getElementById(id); if (e) e.innerHTML = _fnoMini('—');
    });
    const biasVal = document.getElementById('fno-bias-value');
    if (biasVal) { biasVal.textContent = 'NO DATA'; biasVal.style.color = 'var(--text-3, #8a8a99)'; }
}
function _fnoRenderError(err) {
    const narr = document.getElementById('fno-narrative');
    if (narr) narr.innerHTML = `<div class="fno-empty">
        <div class="fno-empty-icon err"><svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/></svg></div>
        <div class="fno-empty-title">Couldn’t reach the F&amp;O engine</div>
        <p class="fno-empty-sub">${escapeHtml(String(err && err.message ? err.message : err))} — retrying on the next visit.</p></div>`;
    const biasVal = document.getElementById('fno-bias-value');
    if (biasVal) { biasVal.textContent = 'OFFLINE'; biasVal.style.color = 'var(--red)'; }
}

/* ===========================================================================
 * OPTION-CHAIN DRILL-DOWN MODAL
 * ======================================================================== */
async function openFnoOptionChain(symbol) {
    const modal = document.getElementById('fno-modal');
    const body = document.getElementById('fno-modal-body');
    if (!modal || !body) return;
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    body.innerHTML = `<div class="fno-modal-loading"><div class="fno-spinner"></div><span>Loading ${escapeHtml(symbol)} option chain…</span></div>`;
    try {
        const res = await fetch(`/api/fno/option-chain/${encodeURIComponent(symbol)}`);
        if (res.status === 404) {
            body.innerHTML = `<div class="fno-empty"><div class="fno-empty-title">No option chain</div><p class="fno-empty-sub">${escapeHtml(symbol)} has no F&amp;O options in the latest bhavcopy.</p></div>`;
            return;
        }
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        _renderFnoOptionChain(data);
    } catch (err) {
        body.innerHTML = `<div class="fno-empty"><div class="fno-empty-title">Couldn’t load chain</div><p class="fno-empty-sub">${escapeHtml(String(err && err.message ? err.message : err))}</p></div>`;
    }
}

function closeFnoModal() {
    const modal = document.getElementById('fno-modal');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
}

function _renderFnoOptionChain(d) {
    const body = document.getElementById('fno-modal-body');
    if (!body) return;
    const ladder = d.ladder || [];
    const spot = Number(d.spot || 0);
    const atmStrike = d.atm_strike;
    const maxOI = Math.max(1, ...ladder.map(s => Math.max(Number(s.ce_oi || 0), Number(s.pe_oi || 0))));
    const pcr = (d.pcr === null || d.pcr === undefined) ? '—' : Number(d.pcr).toFixed(2);
    const pcrCls = d.pcr >= 1.2 ? 'bull' : d.pcr <= 0.8 ? 'bear' : 'flat';
    const gap = d.max_pain_gap_pct;
    const skew = d.iv_skew;

    const stats = [
        [`<span class="${pcrCls}">${pcr}</span>`, 'PCR (OI)'],
        [d.atm_iv != null ? `${Number(d.atm_iv).toFixed(1)}%` : '—', 'ATM IV'],
        [(skew != null) ? `<span class="${skew >= 0 ? 'bear' : 'bull'}">${skew >= 0 ? '+' : ''}${Number(skew).toFixed(1)}</span>` : '—', 'IV Skew P−C'],
        [d.max_pain ? _fnoNumF(d.max_pain, 0) : '—', 'Max Pain'],
        [(gap != null) ? `<span class="${gap >= 0 ? 'bull' : 'bear'}">${gap >= 0 ? '+' : ''}${Number(gap).toFixed(1)}%</span>` : '—', 'Spot vs Pain'],
        [d.call_wall ? `<span class="bear">${_fnoNumF(d.call_wall, 0)}</span>` : '—', 'Call Wall (R)'],
        [d.put_wall ? `<span class="bull">${_fnoNumF(d.put_wall, 0)}</span>` : '—', 'Put Wall (S)'],
    ];

    const ivCell = (iv, delta, side) =>
        `<td class="fno-oc-iv ${side}" title="${delta != null ? 'Delta ' + Number(delta).toFixed(2) : ''}">${iv != null ? Number(iv).toFixed(1) : '—'}</td>`;

    const rowsHtml = ladder.map(s => {
        const ceW = Math.min(100, Number(s.ce_oi || 0) / maxOI * 100);
        const peW = Math.min(100, Number(s.pe_oi || 0) / maxOI * 100);
        const isAtm = s.strike === atmStrike;
        const isCW = s.strike === d.call_wall;
        const isPW = s.strike === d.put_wall;
        return `<tr class="${isAtm ? 'fno-oc-atm' : ''}">
            ${ivCell(s.ce_iv, s.ce_delta, 'ce')}
            <td class="fno-oc-ce">
                <div class="fno-oc-bar-wrap"><div class="fno-oc-bar ce" style="width:${ceW}%"></div></div>
                <span class="fno-oc-oi">${_fnoOI(s.ce_oi)}</span>
                <span class="fno-oc-chg ${Number(s.ce_chg) >= 0 ? 'bear' : 'bull'}">${Number(s.ce_chg) >= 0 ? '+' : ''}${_fnoOI(s.ce_chg)}</span>
            </td>
            <td class="fno-oc-strike">${_fnoNumF(s.strike, 0)}${isCW ? '<span class="fno-oc-tag r">R</span>' : ''}${isPW ? '<span class="fno-oc-tag s">S</span>' : ''}</td>
            <td class="fno-oc-pe">
                <span class="fno-oc-chg ${Number(s.pe_chg) >= 0 ? 'bull' : 'bear'}">${Number(s.pe_chg) >= 0 ? '+' : ''}${_fnoOI(s.pe_chg)}</span>
                <span class="fno-oc-oi">${_fnoOI(s.pe_oi)}</span>
                <div class="fno-oc-bar-wrap"><div class="fno-oc-bar pe" style="width:${peW}%"></div></div>
            </td>
            ${ivCell(s.pe_iv, s.pe_delta, 'pe')}
        </tr>`;
    }).join('');

    body.innerHTML = `
        <div class="fno-oc-head">
            <div>
                <div class="fno-oc-title">${escapeHtml(d.symbol)} ${d.is_index ? '<span class="fno-chip flat">INDEX</span>' : ''}</div>
                <div class="fno-oc-sub">Expiry ${escapeHtml(d.expiry || '—')} · Spot ${spot ? _fnoNumF(spot, 2) : '—'}${d.forward ? ' · Fut ' + _fnoNumF(d.forward, 2) : ''}</div>
            </div>
            ${_fnoSentChip(d.sentiment)}
        </div>
        <div class="fno-oc-stats">${stats.map(s => `<div class="fno-oc-stat"><div class="fno-oc-stat-v">${s[0]}</div><div class="fno-oc-stat-l">${s[1]}</div></div>`).join('')}</div>
        <div class="fno-oc-legend"><span><i class="dot ce"></i>Calls — IV · OI</span><span>Strike</span><span>OI · IV — Puts<i class="dot pe"></i></span></div>
        <div class="fno-oc-table-wrap">
            <table class="fno-oc-table"><thead><tr><th>IV</th><th>Call OI / Δ</th><th>Strike</th><th>Put OI / Δ</th><th>IV</th></tr></thead>
            <tbody>${rowsHtml || '<tr><td colspan="5" class="fno-oc-empty">No strikes in the chain.</td></tr>'}</tbody></table>
        </div>
        <div class="fno-oc-foot">IV &amp; Δ via Black-76 on EOD settlement prices — hover an IV cell for Delta.</div>`;
}

// Close modal on Escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const m = document.getElementById('fno-modal');
        if (m && !m.classList.contains('hidden')) closeFnoModal();
    }
});

/* ===========================================================================
 * AUTO-POLL while the F&O tab is open (#3)
 *
 * switchTab('fno') already does the first fetch (app-core.js). Here we add a
 * visibility-aware refresh so the board re-checks for tonight's bhavcopy (or,
 * with Angel intraday on, the live OI) WITHOUT a manual reload — and a 60s
 * countdown ticker. Polling pauses when the tab is hidden or backgrounded, so
 * it never hammers the server cache. We wrap window.switchTab the same way
 * app-calendar.js does; wrapper chaining is order-independent.
 * ======================================================================== */
const _FNO_POLL_MS = 180000;   // 3 min — server cache (board 10m / intraday TTL) absorbs it
let _fnoPollTimer = null, _fnoCountdownTimer = null;

function _fnoStartPolling() {
    if (!_fnoPollTimer) {
        _fnoPollTimer = setInterval(() => {
            if (!document.hidden) fetchFnoSmartMoney();
        }, _FNO_POLL_MS);
    }
    if (!_fnoCountdownTimer) {
        _fnoCountdownTimer = setInterval(_fnoTickCountdown, 60000);
    }
}
function _fnoStopPolling() {
    if (_fnoPollTimer) { clearInterval(_fnoPollTimer); _fnoPollTimer = null; }
    if (_fnoCountdownTimer) { clearInterval(_fnoCountdownTimer); _fnoCountdownTimer = null; }
    if (_fnoBuildTimer) { clearTimeout(_fnoBuildTimer); _fnoBuildTimer = null; }
}

(function _fnoHookTabLifecycle() {
    const _origSwitchTabFno = window.switchTab;
    if (typeof _origSwitchTabFno !== 'function') return;
    window.switchTab = function (tab) {
        _origSwitchTabFno.apply(this, arguments);
        if (tab === 'fno') _fnoStartPolling();
        else _fnoStopPolling();
    };
})();

// Refresh once when the tab/window regains focus while F&O is the open view
// (covers the "left it open overnight" case so the morning shows fresh data).
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && _fnoPollTimer) fetchFnoSmartMoney();
});
