import unittest

from marketdata import earnings_data as ed


class TestFiscalQuarterLabel(unittest.TestCase):
    def test_q2_fy26_from_september(self):
        self.assertEqual(ed.fiscal_quarter_label(9, 2025), "Q2 FY26")

    def test_q1_fy26_from_june(self):
        self.assertEqual(ed.fiscal_quarter_label(6, 2025), "Q1 FY26")

    def test_q3_fy26_from_december(self):
        self.assertEqual(ed.fiscal_quarter_label(12, 2025), "Q3 FY26")

    def test_q4_fy25_from_march(self):
        # March belongs to the fiscal year that ends that same March
        self.assertEqual(ed.fiscal_quarter_label(3, 2025), "Q4 FY25")

    def test_nonstandard_month_buckets(self):
        self.assertEqual(ed.fiscal_quarter_label(5, 2025), "Q1 FY26")
        self.assertEqual(ed.fiscal_quarter_label(1, 2025), "Q4 FY25")

    def test_bad_input_returns_none(self):
        self.assertIsNone(ed.fiscal_quarter_label(13, 2025))
        self.assertIsNone(ed.fiscal_quarter_label(None, 2025))


class TestPctChange(unittest.TestCase):
    def test_simple_growth(self):
        self.assertAlmostEqual(ed.pct_change(110, 100), 10.0)

    def test_decline(self):
        self.assertAlmostEqual(ed.pct_change(90, 100), -10.0)

    def test_zero_prev_returns_none(self):
        self.assertIsNone(ed.pct_change(100, 0))

    def test_non_numeric_returns_none(self):
        self.assertIsNone(ed.pct_change("x", 100))
        self.assertIsNone(ed.pct_change(100, None))

    def test_negative_prev_sign_reflects_direction(self):
        # profit improving from -50 to +50 -> positive direction
        self.assertGreater(ed.pct_change(50, -50), 0)


class TestGrowthDescriptor(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(ed.growth_descriptor(110, 100), "normal")

    def test_turnaround(self):
        self.assertEqual(ed.growth_descriptor(20, -10), "turnaround")

    def test_slipped_to_loss(self):
        self.assertEqual(ed.growth_descriptor(-5, 10), "slipped_to_loss")

    def test_loss_widened(self):
        self.assertEqual(ed.growth_descriptor(-20, -10), "loss_widened")

    def test_loss_narrowed(self):
        self.assertEqual(ed.growth_descriptor(-5, -10), "loss_narrowed")

    def test_missing_returns_none(self):
        self.assertIsNone(ed.growth_descriptor(None, 10))


class TestMarginAndBps(unittest.TestCase):
    def test_margin_pct(self):
        self.assertAlmostEqual(ed.margin_pct(18, 100), 18.0)

    def test_margin_zero_denom(self):
        self.assertIsNone(ed.margin_pct(18, 0))

    def test_bps_change(self):
        self.assertAlmostEqual(ed.bps_change(18.0, 16.8), 120.0)

    def test_bps_change_missing(self):
        self.assertIsNone(ed.bps_change(18.0, None))


class TestClassifySurprise(unittest.TestCase):
    def test_beat(self):
        self.assertEqual(ed.classify_surprise(5.0), ("Beat", "pos"))

    def test_miss(self):
        self.assertEqual(ed.classify_surprise(-5.0), ("Miss", "neg"))

    def test_in_line(self):
        self.assertEqual(ed.classify_surprise(0.5), ("In-line", "neutral"))

    def test_awaited_when_none(self):
        self.assertEqual(ed.classify_surprise(None), ("Awaited", "neutral"))


class TestQuarterVerdict(unittest.TestCase):
    def test_strong_quarter(self):
        level, score, drivers = ed.quarter_verdict(15.0, 20.0, 120.0, 6.0)
        self.assertEqual(level, "Strong")
        self.assertGreaterEqual(score, 2)
        self.assertTrue(drivers)

    def test_weak_quarter(self):
        level, score, _ = ed.quarter_verdict(-5.0, -10.0, -150.0, -8.0)
        self.assertEqual(level, "Weak")
        self.assertLessEqual(score, -2)

    def test_mixed_quarter(self):
        level, _, _ = ed.quarter_verdict(12.0, -2.0, None, None)
        self.assertEqual(level, "Mixed")

    def test_all_none_is_mixed(self):
        level, score, drivers = ed.quarter_verdict(None, None, None, None)
        self.assertEqual(level, "Mixed")
        self.assertEqual(score, 0)
        self.assertEqual(drivers, [])


class TestFormatters(unittest.TestCase):
    def test_to_crore(self):
        self.assertAlmostEqual(ed.to_crore(2_67_48_00_00_000), 26748.0)

    def test_format_inr_cr_normal(self):
        self.assertEqual(ed.format_inr_cr(26748 * 1e7), "₹26,748 Cr")

    def test_format_inr_cr_lakh_crore(self):
        self.assertEqual(ed.format_inr_cr(2.4e5 * 1e7), "₹2.40 lakh Cr")

    def test_format_inr_cr_negative(self):
        self.assertTrue(ed.format_inr_cr(-500 * 1e7).startswith("-₹"))

    def test_format_inr_cr_missing(self):
        self.assertEqual(ed.format_inr_cr(None), "—")

    def test_signed_pct(self):
        self.assertEqual(ed.format_signed_pct(12.34), "+12.3%")
        self.assertEqual(ed.format_signed_pct(-4.1), "-4.1%")
        self.assertEqual(ed.format_signed_pct(None), "—")

    def test_format_bps(self):
        self.assertEqual(ed.format_bps(120), "+120 bps")
        self.assertEqual(ed.format_bps(-50), "-50 bps")


class TestBuildScorecard(unittest.TestCase):
    def _rows(self):
        # newest first: Q2 FY26 (Sep-2025), Q1 FY26 (Jun-2025), ... Q2 FY25 (Sep-2024)
        return [
            {"end": "2025-09-30", "revenue": 1200e7, "net_income": 200e7,
             "operating_income": 300e7, "ebitda": 320e7, "eps": 10.0},
            {"end": "2025-06-30", "revenue": 1100e7, "net_income": 180e7,
             "operating_income": 270e7, "ebitda": 290e7, "eps": 9.0},
            {"end": "2025-03-31", "revenue": 1050e7, "net_income": 170e7,
             "operating_income": 250e7, "ebitda": 270e7, "eps": 8.5},
            {"end": "2024-12-31", "revenue": 1000e7, "net_income": 160e7,
             "operating_income": 240e7, "ebitda": 260e7, "eps": 8.0},
            {"end": "2024-09-30", "revenue": 1000e7, "net_income": 150e7,
             "operating_income": 230e7, "ebitda": 250e7, "eps": 7.5},
        ]

    def test_builds_card_with_yoy_and_qoq(self):
        card = ed.build_scorecard(
            self._rows(),
            {"surprise_pct": 4.0, "reported_eps": 10.0, "eps_estimate": 9.6,
             "last_reported_date": "2025-10-20", "next_date": "2026-01-18"},
            "Example Ltd", "Technology", "EXAMPLE", "EXAMPLE.NS", "2025-11-01",
        )
        self.assertIsNotNone(card)
        self.assertEqual(card["quarter"], "Q2 FY26")
        # revenue 1200 vs 1000 YoY = +20%
        self.assertAlmostEqual(card["metrics"]["revenue"]["yoy"], 20.0, places=1)
        # profit 200 vs 150 YoY = +33.3%
        self.assertAlmostEqual(card["metrics"]["profit"]["yoy"], 33.33, places=1)
        # QoQ revenue 1200 vs 1100
        self.assertAlmostEqual(card["metrics"]["revenue"]["qoq"], 9.09, places=1)
        self.assertEqual(card["surprise"]["label"], "Beat")
        self.assertEqual(card["verdict"]["level"], "Strong")
        self.assertEqual(card["next_date"], "2026-01-18")
        self.assertIn("Q2 FY26", card["summary"])

    def test_empty_rows_returns_none(self):
        self.assertIsNone(ed.build_scorecard([], {}, "X", "Y", "X", "X.NS", "2025-11-01"))

    def test_single_quarter_no_yoy(self):
        card = ed.build_scorecard(
            [{"end": "2025-09-30", "revenue": 1200e7, "net_income": 200e7,
              "operating_income": 300e7, "ebitda": None, "eps": 10.0}],
            {}, "Solo Ltd", "Energy", "SOLO", "SOLO.NS", "2025-11-01",
        )
        self.assertIsNotNone(card)
        self.assertIsNone(card["metrics"]["revenue"]["yoy"])
        self.assertEqual(card["surprise"]["label"], "Awaited")


if __name__ == "__main__":
    unittest.main()
