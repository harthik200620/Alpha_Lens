"""
signals/calibration.py — Lever #1 + #4 (calibration + meta-labeling).

Maps the ensemble's raw confidence score -> an EMPIRICAL probability that the
trade hits its 2xATR target before its 1xATR stop. Built by the scratch/
relabel pipeline and stored in signals/calibration_map.json (isotonic / monotone
breakpoints). Use p_win(score) to read the calibrated probability and
passes_gate(score) to apply the meta-label gate.

WHY THIS EXISTS
  Relabelling ~64 resolved trades (production + agent, fresh ATR barriers)
  showed the raw ensemble score is essentially NON-predictive of win/loss —
  high-confidence signals did not win more (mildly inverted). Isotonic
  regression flattens the score to two steps straddling the 2:1 breakeven:
      score < 55  -> ~31.6% win  (BELOW the 33.3% breakeven -> skip)
      score >= 55 -> ~39.5% win  (marginally above)

SAFETY
  That map is from a THIN, single-regime sample, so the meta-label GATE is OFF
  by default (CALIBRATION_GATE_ENABLED=0): p_win is computed and surfaced for
  observability, but it does not reject signals unless explicitly enabled.
  Refresh calibration_map.json as real closed trades accumulate, then turn the
  gate on. Env knobs:
    CALIBRATION_GATE_ENABLED  (default 0)  -> reject signals whose p_win < breakeven
    RR_BREAKEVEN              (default 0.3333 for 2:1 reward:risk)
"""
import os
import json

_DEFAULT_BREAKEVEN = 1.0 / 3.0  # 2:1 reward:risk (target = 2x stop)

# Used only if calibration_map.json is missing/unreadable.
_EMBEDDED_ISO = [
    {"score": 0,   "p_win": 0.316},
    {"score": 54,  "p_win": 0.316},
    {"score": 55,  "p_win": 0.395},
    {"score": 100, "p_win": 0.395},
]

_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_map.json")


def _load_iso():
    try:
        if os.path.exists(_MAP_PATH):
            with open(_MAP_PATH, encoding="utf-8") as f:
                d = json.load(f)
            iso = d.get("isotonic")
            if iso:
                pts = sorted((float(p["score"]), float(p["p_win"])) for p in iso)
                if pts:
                    return pts
    except Exception:
        pass
    return sorted((float(p["score"]), float(p["p_win"])) for p in _EMBEDDED_ISO)


_ISO = _load_iso()


def reload_map():
    """Re-read calibration_map.json (call after refreshing the map on disk)."""
    global _ISO
    _ISO = _load_iso()
    return _ISO


def breakeven():
    try:
        return float(os.environ.get("RR_BREAKEVEN", _DEFAULT_BREAKEVEN))
    except Exception:
        return _DEFAULT_BREAKEVEN


def p_win(score):
    """Calibrated P(target before stop) for a raw ensemble score, via linear
    interpolation over the isotonic breakpoints. Returns None if unusable."""
    if score is None:
        return None
    try:
        s = float(score)
    except Exception:
        return None
    pts = _ISO
    if not pts:
        return None
    if s <= pts[0][0]:
        return round(pts[0][1], 4)
    if s >= pts[-1][0]:
        return round(pts[-1][1], 4)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= s <= x1:
            if x1 == x0:
                return round(y1, 4)
            t = (s - x0) / (x1 - x0)
            return round(y0 + t * (y1 - y0), 4)
    return round(pts[-1][1], 4)


def is_gate_enabled():
    return os.environ.get("CALIBRATION_GATE_ENABLED", "0").lower() in ("1", "true", "yes")


def passes_gate(score):
    """Meta-label decision. When the gate is disabled (default) always True, so
    production behaviour is unchanged. When enabled, True iff calibrated
    p_win >= breakeven (i.e. the trade is expected to be profitable at 2:1)."""
    if not is_gate_enabled():
        return True
    p = p_win(score)
    if p is None:
        return True
    return p >= breakeven()
