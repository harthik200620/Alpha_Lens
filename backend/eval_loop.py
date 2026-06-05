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
    "ATR_STOP_MULT": "1.0", "ATR_TARGET_MULT": "2.0",
    "ATR_STOP_CAP_PCT": "2.5", "ATR_TARGET_CAP_PCT": "5.0",
    "REQUIRE_TECH_CONFIRM": "1", "TECH_CONFIRM_MIN": "50",
    "W_AI": "0.30", "W_TECHNICAL": "0.30", "W_HISTORICAL": "0.20",
    "W_SECTOR": "0.05", "W_INDIAN": "0.15",
    "REGIME_HARD_BLOCK": "0", "CALIBRATION_GATE_ENABLED": "0",
    "MIN_CONFIDENCE": "50", "ENSEMBLE_MIN_AGREE": "3",
    "ENSEMBLE_AGREE_SCORE_THRESHOLD": "50",
}


def current_config():
    return {k: os.environ.get(k, dv) for k, dv in _CONFIG_DEFAULTS.items()}


# ──────────────────────────────────────────────────────────────────────────
# LOGGING (called from the ai_news worker at every decision point)
# ──────────────────────────────────────────────────────────────────────────
def log_decision(disposition, ticker, direction, news_id=None, headline=None,
                 final_score=None, calibrated_p_win=None, base_price=None,
                 atr_pct=None, stop_pct=None, target_pct=None, news_time=None,
                 config=None):
    """Append one decision row. NEVER raises — a logging failure must not break
    signal generation. `disposition` is e.g. 'approved', 'rejected_liquidity',
    'rejected_atr', 'rejected_ensemble'."""
    if os.environ.get("EVAL_LOG_DISABLED", "").lower() in ("1", "true", "yes"):
        return
    try:
        cfg = json.dumps(config or current_config(), sort_keys=True)

        def _w(conn, c):
            c.execute(
                """INSERT INTO signal_eval_log
                   (news_id, headline, ticker, direction, disposition,
                    final_score, calibrated_p_win, base_price, atr_pct,
                    stop_pct, target_pct, news_time, config)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (news_id, (headline or "")[:300], ticker, direction, disposition,
                 final_score, calibrated_p_win, base_price, atr_pct,
                 stop_pct, target_pct, news_time, cfg),
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
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(s)
        if d is not None and d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


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

        s_mult = float(os.environ.get("ATR_STOP_MULT", "1.0"))
        t_mult = float(os.environ.get("ATR_TARGET_MULT", "2.0"))
        s_cap = float(os.environ.get("ATR_STOP_CAP_PCT", "2.5"))
        t_cap = float(os.environ.get("ATR_TARGET_CAP_PCT", "5.0"))
        stop_pct = round(min(s_cap, max(1.0, atr_pct * s_mult)), 2)
        target_pct = round(min(t_cap, max(2.0, atr_pct * t_mult)), 2)

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

        bull = (direction or "").upper().startswith("BULL")
        tp = base_price * (1 + target_pct / 100) if bull else base_price * (1 - target_pct / 100)
        sp = base_price * (1 - stop_pct / 100) if bull else base_price * (1 + stop_pct / 100)
        for _idx, row in scan.iterrows():
            hi, lo, cl = row["High"], row["Low"], row["Close"]
            th = (hi >= tp) if bull else (lo <= tp)
            sh = (lo <= sp) if bull else (hi >= sp)
            ret = round((cl - base_price) / base_price * 100, 2)
            if th and sh:  # ambiguous candle — decide by close
                ok = (cl >= base_price) if bull else (cl <= base_price)
                return (atr_pct, stop_pct, target_pct, "TARGET_HIT" if ok else "STOP_HIT", ret)
            if th:
                return (atr_pct, stop_pct, target_pct, "TARGET_HIT", ret)
            if sh:
                return (atr_pct, stop_pct, target_pct, "STOP_HIT", ret)
        last = scan["Close"].iloc[-1]
        return (atr_pct, stop_pct, target_pct, "EXPIRED", round((last - base_price) / base_price * 100, 2))
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
        """SELECT id, ticker, direction, decided_at FROM signal_eval_log
           WHERE outcome IS NULL ORDER BY decided_at ASC LIMIT ?""",
        (limit,),
    )
    rows = c.fetchall()
    conn.close()

    labelled = 0
    for sid, ticker, direction, decided_at in rows:
        dt = _parse_dt(decided_at)
        if dt is None or dt > cutoff:
            continue  # bad date, or horizon not elapsed yet
        res = _label_one(ticker, dt.astimezone(IST), direction or "BULLISH")
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
    RESOLVED = ("TARGET_HIT", "STOP_HIT")
    res = [r for r in rows if r[1] in RESOLVED]
    wins = [r for r in res if r[1] == "TARGET_HIT"]
    pnls = [r[3] for r in res if r[3] is not None]
    n = len(res)
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
    c.execute("SELECT disposition, outcome, final_score, outcome_pct FROM signal_eval_log")
    rows = c.fetchall()
    conn.close()

    by_disp = {}
    for d in sorted({(r[0] or "unknown") for r in rows}):
        by_disp[d] = _stats([r for r in rows if (r[0] or "unknown") == d])

    approved = _stats([r for r in rows if r[0] == "approved"])
    rejected = _stats([r for r in rows if (r[0] or "").startswith("rejected")])

    verdict = None
    if approved["win_rate_pct"] is not None and rejected["win_rate_pct"] is not None:
        # If the trades we REJECTED won more than the ones we KEPT, the filters
        # are hurting; if they won less, the filters are helping.
        verdict = ("filters HELPING (rejected trades won less than approved)"
                   if rejected["win_rate_pct"] < approved["win_rate_pct"]
                   else "filters HURTING (rejected trades won MORE than approved) — loosen them")

    return {
        "note": "Forward shadow-ledger. Append-only; nothing is ever deleted. "
                "Breakeven for 2:1 R:R = 33.3%.",
        "logged_total": len(rows),
        "approved": approved,
        "rejected_all": rejected,
        "verdict": verdict,
        "by_disposition": by_disp,
    }
