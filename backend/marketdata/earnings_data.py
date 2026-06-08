"""
Pure earnings-math helpers for the "Earnings & Results Intelligence" feature.

This module is intentionally **pure** — no network, no DB, no pandas, no Gemini.
It takes already-extracted plain-Python quarterly figures (the caller in
``app.py`` does the yfinance I/O and hands us lists of dicts) and turns them
into the precise, investor-readable scorecard the frontend renders.

Keeping the arithmetic here (instead of inline in ``app.py``) means it is unit
tested by ``backend/tests/test_earnings_data.py`` with no I/O — exactly like the
other extracted pure modules (``market_calendar``, ``ticker_utils``, …).

Indian-market conventions baked in:
  * Fiscal year runs **Apr → Mar**.  Quarter ends: Jun=Q1, Sep=Q2, Dec=Q3,
    Mar=Q4.  ``fiscal_quarter_label(9, 2025) -> "Q2 FY26"``.
  * Currency figures arrive in **absolute INR** (Yahoo's reporting unit for NSE
    names) and are shown in **₹ crore** (÷ 1e7).
"""

from datetime import datetime

# ── Verdict / surprise thresholds (tuned for "normal investor" readability) ──
_SURPRISE_THRESHOLD = 2.0     # |EPS surprise %| below this reads as "in-line"
_REV_STRONG = 10.0            # YoY revenue growth % that counts as strong
_PAT_STRONG = 12.0            # YoY profit growth % that counts as strong
_MARGIN_MOVE_BPS = 50.0       # net-margin move (bps) that counts as meaningful


def fiscal_quarter_label(month, year):
    """Indian fiscal-quarter label from a period-END month/year.

    FY runs Apr–Mar, so a quarter ending Sep-2025 is Q2 of FY26 (the year that
    ends Mar-2026).  Non-standard month-ends are bucketed to the nearest quarter.
    Returns e.g. ``"Q2 FY26"`` or ``None`` on bad input.
    """
    try:
        month = int(month)
        year = int(year)
    except (TypeError, ValueError):
        return None
    if not (1 <= month <= 12):
        return None
    if month in (4, 5, 6):
        q = 1
    elif month in (7, 8, 9):
        q = 2
    elif month in (10, 11, 12):
        q = 3
    else:                      # Jan, Feb, Mar
        q = 4
    fy = year + 1 if month >= 4 else year
    return f"Q{q} FY{fy % 100:02d}"


def pct_change(curr, prev):
    """Percent change of ``curr`` vs ``prev``.  ``None`` if not computable.

    Uses ``abs(prev)`` in the denominator so the SIGN of the result still
    reflects the real direction of the move even when the prior figure was
    negative (a loss).  Magnitude past a loss base is not very meaningful, so
    callers should pair this with :func:`growth_descriptor` for profit lines.
    """
    try:
        curr = float(curr)
        prev = float(prev)
    except (TypeError, ValueError):
        return None
    if prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100.0


def growth_descriptor(curr, prev):
    """Classify a profit/revenue move that a raw percent can misrepresent.

    Returns one of ``"turnaround"`` (loss → profit), ``"slipped_to_loss"``
    (profit → loss), ``"loss_widened"``, ``"loss_narrowed"``, or ``"normal"``
    (both positive — use :func:`pct_change`).  ``None`` if either side missing.
    """
    try:
        curr = float(curr)
        prev = float(prev)
    except (TypeError, ValueError):
        return None
    if prev >= 0 and curr >= 0:
        return "normal"
    if prev < 0 and curr >= 0:
        return "turnaround"
    if prev >= 0 and curr < 0:
        return "slipped_to_loss"
    # both negative
    return "loss_widened" if curr < prev else "loss_narrowed"


def margin_pct(numerator, denominator):
    """Margin = numerator / denominator * 100.  ``None`` if not computable."""
    try:
        numerator = float(numerator)
        denominator = float(denominator)
    except (TypeError, ValueError):
        return None
    if denominator == 0:
        return None
    return numerator / denominator * 100.0


def bps_change(curr_pct, prev_pct):
    """Change between two percentages, expressed in basis points (1% = 100 bps)."""
    if curr_pct is None or prev_pct is None:
        return None
    return (curr_pct - prev_pct) * 100.0


def classify_surprise(surprise_pct, threshold=_SURPRISE_THRESHOLD):
    """Map an EPS surprise % to ``(label, tone)``.

    ``("Beat", "pos")`` / ``("Miss", "neg")`` / ``("In-line", "neutral")``;
    ``("Awaited", "neutral")`` when no estimate/result is available.
    """
    if surprise_pct is None:
        return ("Awaited", "neutral")
    try:
        surprise_pct = float(surprise_pct)
    except (TypeError, ValueError):
        return ("Awaited", "neutral")
    if surprise_pct >= threshold:
        return ("Beat", "pos")
    if surprise_pct <= -threshold:
        return ("Miss", "neg")
    return ("In-line", "neutral")


def quarter_verdict(rev_yoy, pat_yoy, net_margin_chg_bps, surprise_pct):
    """Transparent, rule-based read of the quarter → ``(level, score, drivers)``.

    This is NOT a fabricated "management quote" — it is a deterministic verdict
    derived only from the reported numbers, so it is honest and reproducible.
    ``level`` is ``"Strong" | "Mixed" | "Weak"``; ``drivers`` is a short list of
    plain-English reasons.
    """
    score = 0
    drivers = []

    if rev_yoy is not None:
        if rev_yoy >= _REV_STRONG:
            score += 1
            drivers.append(f"revenue up {rev_yoy:.0f}% YoY")
        elif rev_yoy <= 0:
            score -= 1
            drivers.append(f"revenue down {abs(rev_yoy):.0f}% YoY")

    if pat_yoy is not None:
        if pat_yoy >= _PAT_STRONG:
            score += 1
            drivers.append(f"profit up {pat_yoy:.0f}% YoY")
        elif pat_yoy <= 0:
            score -= 1
            drivers.append(f"profit down {abs(pat_yoy):.0f}% YoY")

    if net_margin_chg_bps is not None:
        if net_margin_chg_bps >= _MARGIN_MOVE_BPS:
            score += 1
            drivers.append(f"margin expanded {net_margin_chg_bps:.0f} bps")
        elif net_margin_chg_bps <= -_MARGIN_MOVE_BPS:
            score -= 1
            drivers.append(f"margin contracted {abs(net_margin_chg_bps):.0f} bps")

    if surprise_pct is not None:
        if surprise_pct >= _SURPRISE_THRESHOLD:
            score += 1
            drivers.append("earnings beat estimates")
        elif surprise_pct <= -_SURPRISE_THRESHOLD:
            score -= 1
            drivers.append("earnings missed estimates")

    if score >= 2:
        level = "Strong"
    elif score <= -2:
        level = "Weak"
    else:
        level = "Mixed"
    return level, score, drivers


def to_crore(value):
    """Absolute INR → ₹ crore (÷ 1e7).  ``None`` if not numeric."""
    try:
        return float(value) / 1e7
    except (TypeError, ValueError):
        return None


def format_inr_cr(value):
    """Absolute INR figure → compact investor-readable ₹-crore string.

    >1 lakh crore reads as "₹2.40 lakh Cr"; otherwise "₹26,748 Cr".  Negatives
    (a loss) carry a leading minus.  ``"—"`` when the value is missing.
    """
    cr = to_crore(value)
    if cr is None:
        return "—"
    sign = "-" if cr < 0 else ""
    a = abs(cr)
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f} lakh Cr"
    if a >= 1:
        return f"{sign}₹{a:,.0f} Cr"
    return f"{sign}₹{a * 100:.1f} L"     # sub-crore (rare for quarterly lines)


def format_signed_pct(x, decimals=1):
    """Number → signed percent string, e.g. ``"+12.3%"`` / ``"-4.1%"``."""
    if x is None:
        return "—"
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"{x:+.{decimals}f}%"


def format_bps(x):
    """Number (bps) → signed string, e.g. ``"+120 bps"``."""
    if x is None:
        return "—"
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"{x:+.0f} bps"


def _parse_iso_date(s):
    """Parse ``"YYYY-MM-DD"`` (optionally with a time suffix) → ``date``; ``None`` on failure."""
    if not s:
        return None
    if isinstance(s, datetime):
        return s.date()
    txt = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(txt[: len(fmt) + 6], fmt).date()
        except ValueError:
            continue
    # last resort: take the leading date token
    try:
        return datetime.strptime(txt[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _days_between(later_iso, earlier_iso):
    a = _parse_iso_date(later_iso)
    b = _parse_iso_date(earlier_iso)
    if a is None or b is None:
        return None
    return (a - b).days


def _find_yoy_row(rows, latest_end):
    """Find the row ~1 year before ``latest_end`` (same month, prior year)."""
    target = _parse_iso_date(latest_end)
    if target is None:
        return None
    for r in rows:
        d = _parse_iso_date(r.get("end"))
        if d is None:
            continue
        if d.month == target.month and d.year == target.year - 1:
            return r
    # fallback: closest row 10–14 months earlier
    best = None
    best_gap = None
    for r in rows:
        d = _parse_iso_date(r.get("end"))
        if d is None:
            continue
        gap = (target - d).days
        if 300 <= gap <= 430:
            if best_gap is None or abs(gap - 365) < abs(best_gap - 365):
                best, best_gap = r, gap
    return best


def build_scorecard(period_rows, edata, name, sector, base, ticker, today_iso):
    """Assemble the full per-stock earnings scorecard (pure).

    ``period_rows``: list of ``{"end","revenue","net_income","operating_income",
    "ebitda","eps"}`` (absolute INR), **newest first**.  ``edata``: optional
    ``{"last_reported_date","eps_estimate","reported_eps","surprise_pct",
    "next_date"}``.  ``today_iso``: ``"YYYY-MM-DD"`` for freshness math (kept as a
    param so this stays deterministic/testable).

    Returns a dict the frontend renders, or ``None`` if there isn't even one
    usable revenue/profit figure to show.
    """
    edata = edata or {}
    rows = [r for r in (period_rows or []) if isinstance(r, dict)]
    if not rows:
        return None

    latest = rows[0]
    rev = latest.get("revenue")
    pat = latest.get("net_income")
    op = latest.get("operating_income")
    if rev is None and pat is None:
        return None

    end = latest.get("end")
    d = _parse_iso_date(end)
    label = fiscal_quarter_label(d.month, d.year) if d else None

    yoy = _find_yoy_row(rows[1:], end)
    qoq = rows[1] if len(rows) > 1 else None

    rev_yoy = pct_change(rev, yoy.get("revenue")) if yoy else None
    rev_qoq = pct_change(rev, qoq.get("revenue")) if qoq else None
    pat_yoy = pct_change(pat, yoy.get("net_income")) if yoy else None
    pat_qoq = pct_change(pat, qoq.get("net_income")) if qoq else None
    pat_desc = growth_descriptor(pat, yoy.get("net_income")) if yoy else None

    op_margin = margin_pct(op, rev)
    net_margin = margin_pct(pat, rev)
    op_margin_yoy = margin_pct(yoy.get("operating_income"), yoy.get("revenue")) if yoy else None
    net_margin_yoy = margin_pct(yoy.get("net_income"), yoy.get("revenue")) if yoy else None
    op_margin_chg = bps_change(op_margin, op_margin_yoy)
    net_margin_chg = bps_change(net_margin, net_margin_yoy)

    surprise_pct = edata.get("surprise_pct")
    surprise_label, surprise_tone = classify_surprise(surprise_pct)

    level, vscore, drivers = quarter_verdict(rev_yoy, pat_yoy, net_margin_chg, surprise_pct)

    reported_date = edata.get("last_reported_date") or end
    days_since = _days_between(today_iso, reported_date)

    summary = _build_summary(label, rev, rev_yoy, pat, pat_yoy, pat_desc,
                             net_margin, net_margin_chg, surprise_label)

    return {
        "ticker": ticker,
        "base": base,
        "name": name or base,
        "sector": sector or "—",
        "quarter": label or "Latest quarter",
        "period_end": end,
        "reported_date": reported_date,
        "days_since": days_since,
        "verdict": {"level": level, "score": vscore, "drivers": drivers[:3]},
        "summary": summary,
        "metrics": {
            "revenue": {"value_str": format_inr_cr(rev), "yoy": rev_yoy, "qoq": rev_qoq,
                        "yoy_str": format_signed_pct(rev_yoy), "qoq_str": format_signed_pct(rev_qoq)},
            "profit": {"value_str": format_inr_cr(pat), "yoy": pat_yoy, "qoq": pat_qoq,
                       "yoy_str": format_signed_pct(pat_yoy), "qoq_str": format_signed_pct(pat_qoq),
                       "descriptor": pat_desc},
            "op_margin": {"value": _round1(op_margin), "value_str": _pct_str(op_margin),
                          "chg_bps": _round1(op_margin_chg), "chg_str": format_bps(op_margin_chg)},
            "net_margin": {"value": _round1(net_margin), "value_str": _pct_str(net_margin),
                           "chg_bps": _round1(net_margin_chg), "chg_str": format_bps(net_margin_chg)},
        },
        "surprise": {
            "label": surprise_label, "tone": surprise_tone,
            "pct": _round1(surprise_pct),
            "pct_str": format_signed_pct(surprise_pct) if surprise_pct is not None else "—",
            "eps_estimate": edata.get("eps_estimate"),
            "reported_eps": edata.get("reported_eps"),
        },
        "next_date": edata.get("next_date"),
    }


def _round1(x):
    try:
        return round(float(x), 1)
    except (TypeError, ValueError):
        return None


def _pct_str(x, decimals=1):
    if x is None:
        return "—"
    try:
        return f"{float(x):.{decimals}f}%"
    except (TypeError, ValueError):
        return "—"


def _build_summary(label, rev, rev_yoy, pat, pat_yoy, pat_desc,
                   net_margin, net_margin_chg, surprise_label):
    """One precise, plain-English sentence a non-expert investor understands."""
    q = label or "The latest quarter"
    parts = [f"{q}:"]

    if rev is not None:
        seg = f"revenue {format_inr_cr(rev)}"
        if rev_yoy is not None:
            seg += f" ({format_signed_pct(rev_yoy, 0)} YoY)"
        parts.append(seg)

    if pat is not None:
        if pat_desc == "turnaround":
            parts.append(f"swung to a net profit of {format_inr_cr(pat)}")
        elif pat_desc == "slipped_to_loss":
            parts.append(f"slipped to a net loss of {format_inr_cr(pat)}")
        elif pat_desc in ("loss_widened", "loss_narrowed"):
            verb = "wider" if pat_desc == "loss_widened" else "narrower"
            parts.append(f"net loss {format_inr_cr(pat)} ({verb} YoY)")
        else:
            seg = f"net profit {format_inr_cr(pat)}"
            if pat_yoy is not None:
                seg += f" ({format_signed_pct(pat_yoy, 0)} YoY)"
            parts.append(seg)

    tail = []
    if net_margin is not None:
        m = f"net margin {net_margin:.1f}%"
        if net_margin_chg is not None and abs(net_margin_chg) >= 10:
            m += f" ({format_bps(net_margin_chg)})"
        tail.append(m)
    if surprise_label in ("Beat", "Miss"):
        tail.append(f"earnings {'beat' if surprise_label == 'Beat' else 'missed'} estimates")

    sentence = parts[0] + " " + ", ".join(parts[1:])
    if tail:
        sentence += "; " + ", ".join(tail)
    return sentence.rstrip(",; ") + "."
