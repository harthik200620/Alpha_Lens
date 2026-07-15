"""
test_model_levers.py — unit tests for the entry-edge model levers:

  * marketdata.price_resolver.captured_atr   (T1.2 freshness / "unreacted move")
  * newsproc.filing_classifier.catalyst_tier (T1.3 macro de-rate + T0.3 tagging)

Both are PURE (no I/O), so these run in milliseconds. The `tests/__init__.py`
puts backend/ on sys.path, so the sibling subpackages import directly.
"""
import unittest

from marketdata.price_resolver import captured_atr
from newsproc.filing_classifier import catalyst_tier


class TestCapturedATR(unittest.TestCase):
    def test_bullish_already_moved_up_is_positive(self):
        # +1% favourable on a 2% ATR -> 0.5 ATR already captured (alpha decaying)
        self.assertAlmostEqual(captured_atr(100.0, 101.0, 2.0, True), 0.5, places=3)

    def test_bearish_already_fell_is_positive(self):
        # bearish thesis, price already dropped 1% -> favourable -> positive
        self.assertAlmostEqual(captured_atr(100.0, 99.0, 2.0, False), 0.5, places=3)

    def test_bullish_moved_against_is_negative(self):
        # bullish but price fell -> NOT yet reacted our way -> a fresh entry
        self.assertAlmostEqual(captured_atr(100.0, 99.0, 2.0, True), -0.5, places=3)

    def test_bearish_moved_against_is_negative(self):
        self.assertAlmostEqual(captured_atr(100.0, 101.0, 2.0, False), -0.5, places=3)

    def test_no_move_is_zero(self):
        self.assertEqual(captured_atr(100.0, 100.0, 2.0, True), 0.0)

    def test_no_atr_uses_floor(self):
        # atr 0 -> floored to 0.5 so +1% favourable reads as 2.0 captured
        self.assertAlmostEqual(captured_atr(100.0, 101.0, 0.0, True), 2.0, places=3)

    def test_fail_open_on_zero_base(self):
        self.assertEqual(captured_atr(0.0, 101.0, 2.0, True), 0.0)

    def test_fail_open_on_none(self):
        self.assertEqual(captured_atr(None, 101.0, 2.0, True), 0.0)
        self.assertEqual(captured_atr(100.0, None, 2.0, True), 0.0)

    def test_fail_open_on_garbage(self):
        self.assertEqual(captured_atr("x", "y", 2.0, True), 0.0)

    def test_negative_price_fails_open(self):
        self.assertEqual(captured_atr(-100.0, 101.0, 2.0, True), 0.0)


def _momentum_would_skip(base_price, current_price, atr_pct, is_bullish, min_r=0.0):
    """Mirror of the app.py MOMENTUM-CONFIRMATION gate predicate: drop a
    candidate whose thesis price-action has NOT confirmed (captured_r <= min_r),
    but ONLY when the prices are valid (fail-open on a data glitch). Kept here so
    a future sign-flip of the gate is caught by a test."""
    prices_valid = bool(base_price and base_price > 0 and current_price and current_price > 0)
    if not prices_valid:
        return False
    return captured_atr(base_price, current_price, atr_pct, is_bullish) <= min_r


class TestMomentumGate(unittest.TestCase):
    def test_not_moved_is_skipped(self):
        # flat since the news (the worst-performing cohort, 41% win) -> skip
        self.assertTrue(_momentum_would_skip(100.0, 100.0, 2.0, True))

    def test_moved_against_is_skipped(self):
        # bullish thesis but price fell -> unconfirmed -> skip
        self.assertTrue(_momentum_would_skip(100.0, 99.0, 2.0, True))

    def test_confirmed_move_is_kept(self):
        # bullish and price already up 1% (0.5 ATR our way) -> confirmed -> KEEP
        self.assertFalse(_momentum_would_skip(100.0, 101.0, 2.0, True))

    def test_bearish_confirmed_move_is_kept(self):
        self.assertFalse(_momentum_would_skip(100.0, 99.0, 2.0, False))

    def test_fail_open_on_bad_prices(self):
        # invalid price data must NEVER cause a skip (fail-open)
        self.assertFalse(_momentum_would_skip(0.0, 100.0, 2.0, True))
        self.assertFalse(_momentum_would_skip(100.0, 0.0, 2.0, True))
        self.assertFalse(_momentum_would_skip(None, 100.0, 2.0, True))

    def test_higher_threshold_requires_stronger_confirmation(self):
        # min_r=0.6: a 0.5-ATR move is no longer enough -> skip
        self.assertTrue(_momentum_would_skip(100.0, 101.0, 2.0, True, min_r=0.6))
        # but a 1.5% move (0.75 ATR) clears it -> KEEP
        self.assertFalse(_momentum_would_skip(100.0, 101.5, 2.0, True, min_r=0.6))


class TestCatalystTier(unittest.TestCase):
    def test_hard_promoter_pledge(self):
        self.assertEqual(
            catalyst_tier("Promoter pledges 5% stake in XYZ Ltd"), "HARD")

    def test_hard_order_win(self):
        self.assertEqual(
            catalyst_tier("ABC bags Rs 1,250 crore order from NHAI"), "HARD")

    def test_hard_rating_change(self):
        self.assertEqual(
            catalyst_tier("CRISIL downgrades long-term rating of ABC to AA-"), "HARD")

    def test_macro_crude(self):
        self.assertEqual(
            catalyst_tier("Crude oil prices surge after OPEC output cut"), "MACRO")

    def test_macro_rbi(self):
        self.assertEqual(
            catalyst_tier("RBI keeps repo rate unchanged at 6.5%"), "MACRO")

    def test_macro_brent(self):
        self.assertEqual(
            catalyst_tier("Brent crude falls below $90 a barrel"), "MACRO")

    def test_macro_fed(self):
        self.assertEqual(
            catalyst_tier("US Fed signals one more rate hike on inflation"), "MACRO")

    def test_hard_beats_macro_when_both_present(self):
        # idiosyncratic catalyst wins even though a macro word ("oil") appears
        self.assertEqual(
            catalyst_tier("CARE downgrades rating of OilCo amid crude crash"), "HARD")

    def test_soft_generic_earnings(self):
        # earnings aren't one of the nine filing types -> SOFT (so it is NOT
        # de-rated by the macro penalty; passes at the normal bar)
        self.assertEqual(
            catalyst_tier("XYZ reports steady Q4 numbers"), "SOFT")

    def test_soft_empty(self):
        self.assertEqual(catalyst_tier(""), "SOFT")

    def test_soft_none(self):
        self.assertEqual(catalyst_tier(None), "SOFT")


if __name__ == "__main__":
    unittest.main()
