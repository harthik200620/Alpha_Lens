import math
import unittest

from signals.options_math import (
    black76_price, black76_greeks, implied_vol_black76, iv_and_greeks,
    years_to_expiry,
)

R = 0.065


class TestRoundTrip(unittest.TestCase):
    """Price at a known sigma, then recover it — the core correctness check."""
    def test_recovers_iv(self):
        cases = [
            (22000, 22000, 30 / 365, 0.15, True),   # ATM index call
            (22000, 22500, 30 / 365, 0.18, True),   # OTM call
            (22000, 21500, 30 / 365, 0.20, False),  # OTM put
            (3000, 3100, 45 / 365, 0.30, True),     # stock OTM call
            (3000, 2800, 14 / 365, 0.40, False),    # stock OTM put
        ]
        for F, K, T, sig, call in cases:
            p = black76_price(F, K, T, R, sig, call)
            iv = implied_vol_black76(p, F, K, T, R, call)
            self.assertIsNotNone(iv, f"no IV for {F},{K},{call}")
            self.assertAlmostEqual(iv, sig, places=2,
                                   msg=f"{F},{K},{sig},{call} -> {iv}")


class TestGreeks(unittest.TestCase):
    def test_sanity_and_identities(self):
        F, K, T, sig = 22000, 22000, 30 / 365, 0.15
        c = black76_greeks(F, K, T, R, sig, True)
        p = black76_greeks(F, K, T, R, sig, False)
        # ATM-forward delta ~0.5; put delta ~-0.5
        self.assertTrue(0.45 < c['delta'] < 0.55)
        self.assertTrue(-0.55 < p['delta'] < -0.45)
        self.assertGreater(c['gamma'], 0)
        self.assertGreater(c['vega'], 0)
        self.assertLess(c['theta'], 0)            # long option bleeds theta
        # call_delta - put_delta = e^{-rT}
        disc = math.exp(-R * T)
        self.assertAlmostEqual(c['delta'] - p['delta'], disc, places=3)
        # gamma & vega identical for call/put
        self.assertAlmostEqual(c['gamma'], p['gamma'], places=8)
        self.assertAlmostEqual(c['vega'], p['vega'], places=6)

    def test_degenerate_inputs(self):
        g = black76_greeks(22000, 22000, 0.0, R, 0.15, True)
        self.assertIsNone(g['delta'])


class TestSolverSafety(unittest.TestCase):
    def test_below_intrinsic_returns_none(self):
        # deep-ITM call priced below discounted intrinsic (the #1 EOD pitfall)
        self.assertIsNone(implied_vol_black76(50.0, 22000, 20000, 30 / 365, R, True))
        self.assertIsNone(implied_vol_black76(0.0, 22000, 22000, 30 / 365, R, True))
        self.assertIsNone(implied_vol_black76(-5.0, 22000, 22000, 30 / 365, R, True))
        self.assertIsNone(implied_vol_black76(100.0, 22000, 22000, 0.0, R, True))  # T<=0

    def test_above_max_returns_none(self):
        disc = math.exp(-R * 30 / 365)
        self.assertIsNone(implied_vol_black76(22000 * disc + 50, 22000, 22000, 30 / 365, R, True))

    def test_iv_and_greeks_bundle(self):
        F, K, T, sig = 22000, 22000, 30 / 365, 0.15
        p = black76_price(F, K, T, R, sig, True)
        r = iv_and_greeks(p, F, K, T, True)
        self.assertAlmostEqual(r['iv'], sig, places=2)
        self.assertIsNotNone(r['delta'])
        bad = iv_and_greeks(1.0, 22000, 20000, T, True)   # below intrinsic
        self.assertIsNone(bad['iv'])
        self.assertIsNone(bad['delta'])


class TestYearsToExpiry(unittest.TestCase):
    def test_basic(self):
        self.assertAlmostEqual(years_to_expiry('2026-07-01', '2026-06-01'), 30 / 365, places=5)

    def test_expiry_day_is_floored_not_zero(self):
        v = years_to_expiry('2026-06-01', '2026-06-01')
        self.assertGreater(v, 0)
        self.assertLess(v, 0.01)

    def test_bad_input(self):
        self.assertIsNone(years_to_expiry('nope', '2026-06-01'))
        self.assertIsNone(years_to_expiry('', ''))


if __name__ == '__main__':
    unittest.main()
