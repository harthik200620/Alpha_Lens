import unittest

import ticker_utils as t


class TestNormalizeTicker(unittest.TestCase):
    def test_plain_name_gets_ns_suffix(self):
        self.assertEqual(t.normalize_ticker("reliance"), "RELIANCE.NS")

    def test_exchange_prefix_stripped(self):
        self.assertEqual(t.normalize_ticker("NSE:TCS"), "TCS.NS")

    def test_alias_resolution(self):
        # tata motors -> TMPV via the alias map
        self.assertEqual(t.normalize_ticker("tata motors"), "TMPV.NS")

    def test_index_symbol_rejected(self):
        self.assertIsNone(t.normalize_ticker("^NSEI"))

    def test_empty_returns_none(self):
        self.assertIsNone(t.normalize_ticker(""))
        self.assertIsNone(t.normalize_ticker(None))

    def test_already_normalized_passthrough(self):
        self.assertEqual(t.normalize_ticker("HDFCBANK.NS"), "HDFCBANK.NS")


class TestTickerBase(unittest.TestCase):
    def test_strips_suffix(self):
        self.assertEqual(t.ticker_base("HDFCBANK.NS"), "HDFCBANK")

    def test_empty_for_invalid(self):
        self.assertEqual(t.ticker_base("^NSEI"), "")


class TestHeadlineDirection(unittest.TestCase):
    def test_bullish(self):
        self.assertEqual(t._headline_direction("stock surges to record high"), "BULLISH")

    def test_bearish(self):
        self.assertEqual(t._headline_direction("shares crash on profit warning"), "BEARISH")

    def test_neutral_when_no_sentiment_words(self):
        self.assertEqual(t._headline_direction("company holds annual meeting"), "NEUTRAL")


class TestCandidateQualityScore(unittest.TestCase):
    def test_score_within_bounds(self):
        s = t.candidate_quality_score(
            "Infosys Q4 profit beats estimates", "", "INFY.NS",
            source="llm", materiality_hint=80,
        )
        self.assertGreaterEqual(s, 10)
        self.assertLessEqual(s, 99)

    def test_llm_source_scores_higher_than_rule(self):
        hl = "Reliance announces record dividend"
        llm = t.candidate_quality_score(hl, "", "RELIANCE.NS", source="llm")
        rule = t.candidate_quality_score(hl, "", "RELIANCE.NS", source="rule")
        self.assertGreaterEqual(llm, rule)


if __name__ == "__main__":
    unittest.main()
