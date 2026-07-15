"""
eval_loop.py — forward "shadow ledger" for measuring the signal engine.

Logs EVERY decision the ai_news worker makes (approved AND rejected, with the
reason + a snapshot of the active config) into the append-only
`signal_eval_log` table. Once the trade horizon has elapsed, a background
labeler computes the SAME ATR triple-barrier outcome for ALL of them — kept and
dropped — so you can measure whether each filter / weight actually helps win
rate (e.g. "the liquidity filter rejected 40 trades that would have been 22%
win → good" vs "the tech-confirm gate rejected 30 that would have been 58% win
→ bad").

DESIGN GUARANTEES
  * APPEND-ONLY. Nothing in this module — or anywhere else in the codebase —
    DELETEs from signal_eval_log. The prune/archival workers never touch it and
    the reset-all-news endpoint does not list it. The measurement record is
    permanent by construction.
  * Logging is cheap and CRASH-PROOF: log_decision() never raises, so a logging
    failure can never break signal generation.
  * Outcome labelling is decoupled (network-heavy), idempotent, and only runs
    once a row is older than the horizon.
"""
import os
import json
from datetime import datetime, timedelta, timezone

from persistence.db import connect_news_db, db_write

IST = timezone(timedelta(hours=5, minutes=30))
HORIZON_DAYS = int(os.environ.get("EVAL_HORIZON_DAYS", "4"))  # ~3 trading sessions

# Env knobs whose values define a "config" — snapshotted with every decision so
# win rate can be compared across config changes over time.
_CONFIG_DEFAULTS = {
    "MIN_SIGNAL_PRICE": "20", "MIN_TURNOVER_CR": "1.0",
    "ATR_STOP_MULT": "0.5", "ATR_TARGET_MULT": "1.0",
    "ATR_STOP_CAP_PCT": "10.0", "ATR_TARGET_CAP_PCT": "20.0",
    "REQUIRE_TECH_CONFIRM": "1", "TECH_CONFIRM_MIN": "50",
    "W_AI": "0.30", "W_TECHNICAL": "0.30", "W_HISTORICAL": "0.20",
    "W_SECTOR": "0.05", "W_INDIAN": "0.15",
    "REGIME_HARD_BLOCK": "0", "CALIBRATION_GATE_ENABLED": "0",
    "MIN_CONFIDENCE": "50", "ENSEMBLE_MIN_AGREE": "3",
    "ENSEMBLE_AGREE_SCORE_THRESHOLD": "50",
    "PARTIAL_PROFIT_ENABLED": "1", "PARTIAL_PROFIT_R": "1.0",
    "PARTIAL_FRACTION": "0.5", "COST_ROUNDTRIP_PCT": "0.20",
    # Entry-edge levers (so a knob flip is recorded in the config snapshot).
    "UNREACTED_GATE_ENABLED": "0", "UNREACTED_MAX_R": "0.5",
    "MACRO_TIER_CONF_PENALTY": "0",
}


def current_config():
    return {k: os.environ.get(k, dv) for k, dv in _CONFIG_DEFAULTS.items()}


# ──────────────────────────────────────────────────────────────────────────
# LOGGING (called from the ai_news worker at every decision point)
# ──────────────────────────────────────────────────────────────────────────
def log_decision(disposition, ticker, direction, news_id=None, headline=None,
                 final_score=None, calibrated_p_win=None, base_price=None,
                 atr_pct=None, stop_pct=None, target_pct=None, news_time=None,
                 config=None, catalyst_tier=None, captured_r=None, news_age_h=None):
    """Append one decision row. NEVER raises — a logging failure must not break
    signal generation. `disposition` is e.g. 'approved', 'rejected_liquidity',
    'rejected_atr', 'rejected_ensemble', 'rejected_unreacted'.

    catalyst_tier/captured_r/news_age_h (T0.4) are the entry-edge measurement
    fields — the HARD/MACRO/SOFT tier, ATRs already moved our way at decision
    time, and news age in hours — so each lever can be tuned to realised
    outcomes instead of guessed."""
    if os.environ.get("EVAL_LOG_DISABLED", "").lower() in ("1", "true", "yes"):
        return
    try:
        cfg = json.dumps(config or current_config(), sort_keys=True)

        def _w(conn, c):
            c.execute(
                """INSERT INTO signal_eval_log
                   (news_id, headline, ticker, direction, disposition,
                    final_score, calibrated_p_win, base_price, atr_pct,
                    stop_pct, target_pct, catalyst_tier, captured_r, news_age_h,
                    news_time, config)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (news_id, (headline or "")[:300], ticker, direction, disposition,
                 final_score, calibrated_p_win, base_price, atr_pct,
                 stop_pct, target_pct, catalyst_tier, captured_r, news_age_h,
                 news_time, cfg),
            )
        db_write(_w)
    except Exception as e:
        try:
            print(f"   [EVAL] log failed (non-fatal): {e}", flush=True)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────
# OUTCOME LABELLING (background, after the horizon elapses)
# ──────────────────────────────────────────────────────────────────────────
def _parse_dt(s):
    # ⚠️ Delegate to the shared parse_timestamp: on Postgres, decided_at/news_time
    # come back as **datetime objects**, which the old string-only strptime here
    # silently returned None for → the labeler skipped EVERY prod row and the whole
    # counterfactual returned empty. (Same root cause as the created_at fix.)
    from marketdata.price_resolver import parse_timestamp
    return parse_timestamp(s)


def _to_ist(df):
    df.index = (df.index.tz_localize("UTC") if df.index.tz is None else df.index).tz_convert(IST)
    return df


def _label_one(ticker, anchor_ist, direction):
    """Compute the fresh ATR triple-barrier outcome for one (ticker, time,
    direction). Returns (atr_pct, stop_pct, target_pct, outcome, outcome_pct)
    or None on data failure. Mirrors the live engine's ATR sizing."""
    try:
        import yfinance_twelvedata_shim as yf
        from signals.technical_analysis import compute_atr

        daily = yf.Ticker(ticker).history(period="6mo")
        if daily is None or daily.empty:
            return None
        daily = _to_ist(daily)
        dd = daily[daily.index.date <= anchor_ist.date()]
        if len(dd) < 16:
            dd = daily
        dd = dd.tail(40)
        atr = compute_atr(dd["High"].tolist(), dd["Low"].tolist(), dd["Close"].tolist(), 14)
        ref_price = dd["Close"].tolist()[-1]
        if not atr or not ref_price:
            return None
        atr_pct = round(atr / ref_price * 100, 2)

        # Stop = ATR/2, Target = ATR (matches the live signal path; no 1%/2%
        # floor — that flat pair is only the *no-ATR* fallback, and here ATR>0).
        s_mult = float(os.environ.get("ATR_STOP_MULT", "0.5"))
        t_mult = float(os.environ.get("ATR_TARGET_MULT", "1.0"))
        s_cap = float(os.environ.get("ATR_STOP_CAP_PCT", "10.0"))
        t_cap = float(os.environ.get("ATR_TARGET_CAP_PCT", "20.0"))
        stop_pct = round(min(s_cap, atr_pct * s_mult), 2)
        target_pct = round(min(t_cap, atr_pct * t_mult), 2)

        # forward scan: prefer 15m, fall back to daily
        scan_src = None
        try:
            intra = yf.Ticker(ticker).history(period="60d", interval="15m")
            if intra is not None and not intra.empty:
                intra = _to_ist(intra)
                if not intra[intra.index >= anchor_ist].empty:
                    scan_src = intra
        except Exception:
            scan_src = None
        if scan_src is None:
            scan_src = daily

        base = scan_src[scan_src.index >= anchor_ist]
        if base.empty or len(base) < 2:
            return (atr_pct, stop_pct, target_pct, "NO_DATA", 0.0)
        base_price = base["Close"].iloc[0]
        t0 = base.index[0]
        scan = scan_src[(scan_src.index > t0) & (scan_src.index <= t0 + timedelta(days=6))]
        if scan.empty:
            return (atr_pct, stop_pct, target_pct, "NO_DATA", 0.0)

        # Resolve via the SAME partial-profit + breakeven simulator the live engine
        # uses, so the ledger's win-rate is defined identically to the Track Record
        # (a win = net-positive realized P&L, incl. partial / breakeven / expiry).
        from marketdata.price_resolver import simulate_exit, to_favorable_bars
        bull = (direction or "").upper().startswith("BULL")
        bars = [(row.get("Open"), row["High"], row["Low"], row["Close"])
                for _idx, row in scan.iterrows()]
        fav = to_favorable_bars(bars, base_price, bull)
        last_close = scan["Close"].iloc[-1]
        exp = (last_close - base_price) / base_price * 100.0
        exp = exp if bull else -exp
        res = simulate_exit(
            fav, stop_pct, target_pct,
            partial_enabled=os.environ.get("PARTIAL_PROFIT_ENABLED", "1").lower() in ("1", "true", "yes"),
            partial_r=float(os.environ.get("PARTIAL_PROFIT_R", "1.0")),
            partial_frac=float(os.environ.get("PARTIAL_FRACTION", "0.5")),
            cost_pct=float(os.environ.get("COST_ROUNDTRIP_PCT", "0.20")),
            expire_close_pct=exp,
        )
        outcome = {"Predicted Target Hit": "TARGET_HIT", "Stop Loss Hit": "STOP_HIT",
                   "Breakeven Exit": "BREAKEVEN", "Expired": "EXPIRED"}.get(res.get("status"), "EXPIRED")
        return (atr_pct, stop_pct, target_pct, outcome, res.get("pnl_pct"))
    except Exception:
        return None


def _update_outcome(sid, atr_pct, stop_pct, target_pct, outcome, outcome_pct):
    def _w(conn, c):
        c.execute(
            """UPDATE signal_eval_log
               SET atr_pct = COALESCE(atr_pct, ?), stop_pct = COALESCE(stop_pct, ?),
                   target_pct = COALESCE(target_pct, ?), outcome = ?, outcome_pct = ?,
                   resolved_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (atr_pct, stop_pct, target_pct, outcome, outcome_pct, sid),
        )
    db_write(_w)


def label_pending(limit=500, horizon_days=None):
    """Label every unresolved row older than the horizon. Idempotent — only
    touches rows where outcome IS NULL. Returns the count newly labelled."""
    horizon_days = horizon_days or HORIZON_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=horizon_days)
    conn = connect_news_db()
    c = conn.cursor()
    c.execute(
        """SELECT id, ticker, direction, decided_at, news_time FROM signal_eval_log
           WHERE outcome IS NULL ORDER BY decided_at ASC LIMIT ?""",
        (limit,),
    )
    rows = c.fetchall()
    conn.close()

    labelled = 0
    for sid, ticker, direction, decided_at, news_time in rows:
        dt = _parse_dt(decided_at)
        if dt is None or dt > cutoff:
            continue  # bad date, or horizon not elapsed yet
        # Anchor the forward scan on the NEWS time (when a real trade could have
        # entered), not decided_at (when a possibly-cold-started dyno processed it).
        anchor = _parse_dt(news_time) or dt
        res = _label_one(ticker, anchor.astimezone(IST), direction or "BULLISH")
        if res is None:
            _update_outcome(sid, None, None, None, "NO_DATA", None)
            continue
        atr_pct, stop_pct, target_pct, outcome, outcome_pct = res
        _update_outcome(sid, atr_pct, stop_pct, target_pct, outcome, outcome_pct)
        labelled += 1
    return labelled


# ──────────────────────────────────────────────────────────────────────────
# REPORTING
# ──────────────────────────────────────────────────────────────────────────
def _stats(rows):
    # A trade counts once it has a realized outcome with a P&L; a WIN is net-positive
    # P&L (favorable terms), matching the Track Record's expectancy-first definition.
    RESOLVED = ("TARGET_HIT", "STOP_HIT", "BREAKEVEN", "EXPIRED")
    judged = [r for r in rows if r[1] in RESOLVED and r[3] is not None]
    wins = [r for r in judged if r[3] > 0]
    pnls = [r[3] for r in judged]
    n = len(judged)
    return {
        "total": len(rows),
        "resolved": n,
        "wins": len(wins),
        "win_rate_pct": round(100.0 * len(wins) / n, 1) if n else None,
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "pending": sum(1 for r in rows if r[1] is None),
        "no_data": sum(1 for r in rows if r[1] == "NO_DATA"),
    }


def report():
    """Win rate + expectancy for approved vs rejected (the counterfactual that
    tells you if a filter is dropping losers or winners), plus per-disposition."""
    conn = connect_news_db()
    c = conn.cursor()
    try:
        c.execute("SELECT disposition, outcome, final_score, outcome_pct, catalyst_tier FROM signal_eval_log")
        rows = c.fetchall()
    except Exception:
        # Pre-migration DB without the catalyst_tier column — degrade gracefully.
        # Reconnect FIRST: on Postgres the failed execute aborts the transaction,
        # so the fallback SELECT must run on a fresh connection, not the poisoned one.
        try:
            conn.close()
        except Exception:
            pass
        conn = connect_news_db()
        c = conn.cursor()
        c.execute("SELECT disposition, outcome, final_score, outcome_pct FROM signal_eval_log")
        rows = [tuple(r) + (None,) for r in c.fetchall()]
    conn.close()

    by_disp = {}
    for d in sorted({(r[0] or "unknown") for r in rows}):
        by_disp[d] = _stats([r for r in rows if (r[0] or "unknown") == d])

    # ── Catalyst-tier counterfactual (T0.3) over the APPROVED book ──
    # The strategy question: do idiosyncratic HARD catalysts out-win priced-in
    # MACRO reactions? Populates as new tagged rows resolve (old rows = NULL tier,
    # excluded). win_rate_pct stays null per tier until that tier has resolved
    # trades — honest, not zero.
    appr = [r for r in rows if r[0] == "approved"]
    by_catalyst_tier = {t: _stats([r for r in appr if (r[4] or "") == t])
                        for t in ("HARD", "MACRO", "SOFT")}

    approved = _stats([r for r in rows if r[0] == "approved"])
    rejected = _stats([r for r in rows if (r[0] or "").startswith("rejected")])
    # The CLEAN counterfactual: ensemble-rejected vs approved on the SAME liquid
    # universe (liquidity-rejected names are penny/illiquid → their barrier outcomes
    # are garbage and pollute a 'rejected_all' comparison). Judge the gate on this.
    rejected_ensemble = _stats([r for r in rows if r[0] == "rejected_ensemble"])

    verdict = None
    a_wr, e_wr = approved["win_rate_pct"], rejected_ensemble["win_rate_pct"]
    if a_wr is not None and e_wr is not None:
        verdict = ("ensemble gate HELPING (ensemble-rejected won less than approved)"
                   if e_wr < a_wr
                   else "ensemble gate HURTING (ensemble-rejected won MORE than approved) — loosen it")

    return {
        "note": "Forward shadow-ledger. Append-only; nothing is ever deleted. Win = "
                "net-positive realized P&L (partial+breakeven model). Judge selection on "
                "'rejected_ensemble' vs 'approved' (the liquidity arm has untradeable names).",
        "logged_total": len(rows),
        "approved": approved,
        "rejected_ensemble": rejected_ensemble,
        "rejected_all": rejected,
        "verdict": verdict,
        "by_disposition": by_disp,
        "by_catalyst_tier": by_catalyst_tier,
    }


# ──────────────────────────────────────────────────────────────────────────
# DEEPER CUTS (read-only) — the evidence needed to tune the entry-edge levers
# and to rebuild the calibration map. Same win definition as _stats().
# ──────────────────────────────────────────────────────────────────────────
def _bucketise(rows, value_idx, edges, labels):
    """Group `rows` into buckets by the numeric column at value_idx, using the
    half-open bin edges. Returns {label: _stats(bucket_rows)}. Rows whose value
    is None are skipped (they can't be placed). `rows` tuples are shaped like
    _stats expects: (disposition_or_key, outcome, final_score, outcome_pct, ...)."""
    out = {}
    for i, lab in enumerate(labels):
        lo = edges[i]
        hi = edges[i + 1]
        sel = []
        for r in rows:
            v = r[value_idx]
            if v is None:
                continue
            if (v >= lo) and (v < hi if i < len(labels) - 1 else v <= hi):
                sel.append(r)
        out[lab] = _stats(sel)
    return out


def cuts():
    """Read-only deeper cuts of the shadow ledger — the evidence to tune the
    entry-edge levers and rebuild calibration. NO writes. Computes:

      • by_score_bucket  — win rate + avg P&L by ensemble final_score band, for
        BOTH the approved book and the ensemble-rejected book. If the score is
        predictive, the win rate should climb monotonically with the score
        (that greenlights a higher MIN_CONFIDENCE and a calibration rebuild).
      • by_captured_r    — approved book bucketed by how far the stock had ALREADY
        moved our way at decision time (in ATRs). If high-captured_r trades win
        LESS, the unreacted-move gate (UNREACTED_GATE_ENABLED) is validated.
      • by_calibrated_p  — approved book bucketed by the stored calibrated_p_win,
        to see whether the CURRENT calibration map already separates win/lose.

    Win = net-positive realized P&L (same as report()).
    """
    conn = connect_news_db()
    c = conn.cursor()
    # Pull the columns the cuts need. Fall back gracefully if the entry-edge
    # columns predate this schema (old prod rows → NULL, skipped by _bucketise).
    cols = "disposition, outcome, final_score, outcome_pct, captured_r, calibrated_p_win"
    try:
        c.execute(f"SELECT {cols} FROM signal_eval_log")
        raw = c.fetchall()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        conn = connect_news_db()
        c = conn.cursor()
        c.execute("SELECT disposition, outcome, final_score, outcome_pct FROM signal_eval_log")
        raw = [tuple(r) + (None, None) for r in c.fetchall()]
    conn.close()

    # Normalise to plain tuples (Postgres may hand back Row/record objects).
    rows = [tuple(r) for r in raw]
    approved = [r for r in rows if r[0] == "approved"]
    rej_ens = [r for r in rows if r[0] == "rejected_ensemble"]

    # Score bands: scores cluster 45–80; fine bands reveal monotonicity.
    score_edges = [0, 50, 55, 60, 65, 70, 75, 100]
    score_labels = ["<50", "50-55", "55-60", "60-65", "65-70", "70-75", "75+"]
    # captured_r: how many ATRs the stock already moved our way pre-entry.
    capr_edges = [-1e9, 0.0, 0.25, 0.5, 1.0, 1e9]
    capr_labels = ["<=0 (not moved)", "0-0.25", "0.25-0.5", "0.5-1.0", ">1.0 (alpha spent?)"]
    # calibrated_p_win around the ~0.333 breakeven of a 2:1 R:R book.
    pwin_edges = [0.0, 0.30, 0.3333, 0.40, 1.0]
    pwin_labels = ["<0.30", "0.30-0.333", "0.333-0.40", ">=0.40"]

    return {
        "note": "Read-only cuts of the append-only shadow ledger. Win = net-positive "
                "realized P&L. Use by_score_bucket to judge whether raising "
                "MIN_CONFIDENCE helps and whether calibration is worth rebuilding; "
                "by_captured_r to validate the unreacted-move gate; by_calibrated_p "
                "to check the current calibration map. n = resolved trades in the bucket.",
        "logged_total": len(rows),
        "by_score_bucket": {
            "approved": _bucketise(approved, 2, score_edges, score_labels),
            "rejected_ensemble": _bucketise(rej_ens, 2, score_edges, score_labels),
        },
        "by_captured_r": _bucketise(approved, 4, capr_edges, capr_labels),
        "by_calibrated_p": _bucketise(approved, 5, pwin_edges, pwin_labels),
    }
