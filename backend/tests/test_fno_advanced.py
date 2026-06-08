import unittest

from signals.fno_engine import (
    conviction_score, max_pain, option_chain_view, ranked_walls,
    suggest_setup, _participant_positioning, _basis_pct, _rollover_pct,
)
from signals.options_math import black76_price


class TestDirectionalConviction(unittest.TestCase):
    def test_price_must_confirm_buildup(self):
        confirm = conviction_score(20, 5, 100, buildup='LONG_BUILDUP')    # px up confirms
        contra = conviction_score(20, -5, 100, buildup='LONG_BUILDUP')    # px down contradicts
        self.assertGreater(confirm, contra)

    def test_legacy_is_magnitude_only(self):
        # No buildup → the old magnitude contract is preserved (sign-agnostic)
        self.assertEqual(conviction_score(20, 5, 100), conviction_score(20, -5, 100))


class TestMaxPainTie(unittest.TestCase):
    def test_tie_breaks_to_nearest_spot(self):
        chain = [{'strike': 100, 'ce_oi': 50, 'pe_oi': 50},
                 {'strike': 200, 'ce_oi': 50, 'pe_oi': 50}]
        self.assertEqual(max_pain(chain, spot=200), 200)
        self.assertEqual(max_pain(chain, spot=100), 100)


class TestBasisRollover(unittest.TestCase):
    def test_basis(self):
        self.assertAlmostEqual(_basis_pct(101, 100), 1.0, places=2)   # premium
        self.assertAlmostEqual(_basis_pct(99, 100), -1.0, places=2)   # discount
        self.assertIsNone(_basis_pct(0, 0))

    def test_rollover(self):
        self.assertAlmostEqual(_rollover_pct(60, 40), 40.0, places=1)
        self.assertIsNone(_rollover_pct(0, 0))


class TestRankedWalls(unittest.TestCase):
    def test_ranked_and_fresh(self):
        strikes = [{'strike': 100, 'ce_oi': 10, 'ce_chg': 1},
                   {'strike': 110, 'ce_oi': 50, 'ce_chg': 40},
                   {'strike': 120, 'ce_oi': 100, 'ce_chg': 5}]
        w = ranked_walls(strikes, 'ce', top=2)
        self.assertEqual(w[0]['strike'], 120)         # highest CE OI
        self.assertEqual(w[1]['strike'], 110)
        self.assertTrue(w[1]['fresh'])                # 40 vs prior 10 → fresh writing
        self.assertFalse(w[0]['fresh'])               # 5 vs prior 95 → standing


class TestOptionChainIV(unittest.TestCase):
    def test_iv_attached_and_recovers(self):
        F, K, T, sig = 100.0, 100.0, 30 / 365, 0.30
        ce = black76_price(F, K, T, 0.065, sig, True)
        pe = black76_price(F, K, T, 0.065, sig, False)
        entry = {
            'expiry': '2026-07-01', 'spot': 100.0, 'is_index': False,
            'ce_oi': 100, 'pe_oi': 120, 'ce_chg': 0, 'pe_chg': 0,
            'strikes': [{'strike': 100, 'ce_oi': 100, 'pe_oi': 120, 'ce_chg': 0,
                         'pe_chg': 0, 'ce_vol': 0, 'pe_vol': 0,
                         'ce_ltp': ce, 'pe_ltp': pe, 'ce_settle': ce, 'pe_settle': pe}],
        }
        v = option_chain_view('XYZ', entry, {'front_close': 100.0}, '2026-06-01')
        self.assertIsNotNone(v['atm_iv'])
        self.assertAlmostEqual(v['atm_iv'], 30.0, delta=2.0)   # ~30% recovered
        self.assertIsNotNone(v['ladder'][0]['ce_delta'])

    def test_no_iv_without_forward_or_date(self):
        entry = {'expiry': '2026-07-01', 'spot': 100.0, 'strikes':
                 [{'strike': 100, 'ce_oi': 1, 'pe_oi': 1, 'ce_settle': 5, 'pe_settle': 5}]}
        v = option_chain_view('XYZ', entry)   # no futures/asof → IV None, no crash
        self.assertIsNone(v['atm_iv'])


class TestParticipant(unittest.TestCase):
    def test_fii_net_and_bias(self):
        part = {
            'FII': {'fut_idx_long': 100, 'fut_idx_short': 300, 'total_long': 500,
                    'total_short': 900, 'opt_idx_call_long': 0, 'opt_idx_call_short': 200,
                    'opt_idx_put_long': 0, 'opt_idx_put_short': 0,
                    'fut_stk_long': 0, 'fut_stk_short': 0},
            '_date': '2026-06-06',
        }
        v = _participant_positioning(part)
        self.assertTrue(v['applicable'])
        fii = next(c for c in v['cohorts'] if c['cohort'] == 'FII')
        self.assertEqual(fii['fut_index_net'], -200)
        self.assertEqual(v['headline']['bias'], 'BEARISH')

    def test_empty(self):
        self.assertFalse(_participant_positioning(None)['applicable'])
        self.assertFalse(_participant_positioning({})['applicable'])


class TestSetup(unittest.TestCase):
    def test_bullish_rich_iv(self):
        row = {'symbol': 'RELIANCE', 'buildup': 'LONG_BUILDUP',
               'buildup_label': 'Long Buildup', 'direction': 'bullish', 'conviction': 80}
        s = suggest_setup(row, {'put_wall': 2800, 'call_wall': 3000,
                                'max_pain': 2900, 'atm_iv': 45.0})
        self.assertEqual(s['stance'], 'Bullish')
        self.assertIn('IV rich', s['idea'])
        self.assertEqual(s['levels']['support'], 2800)
        self.assertEqual(s['levels']['resistance'], 3000)


if __name__ == '__main__':
    unittest.main()
