import unittest

import news_rules as nr


class TestIsFinanceRelevant(unittest.TestCase):
    def test_finance_headline_relevant(self):
        self.assertTrue(nr.is_finance_relevant("Nifty hits record high as RBI holds rate"))

    def test_unrelated_headline_not_relevant(self):
        self.assertFalse(nr.is_finance_relevant("Local bakery wins dessert award"))


class TestClassifyCategory(unittest.TestCase):
    def test_finance_category(self):
        self.assertEqual(nr.classify_category("RBI hikes repo rate"), "Finance")

    def test_technology_category(self):
        self.assertEqual(nr.classify_category("New AI chip boosts cloud software"), "Technology")

    def test_no_keyword_is_general(self):
        self.assertEqual(nr.classify_category("A quiet afternoon in the village"), "General")


class TestStockKeywordMap(unittest.TestCase):
    def test_known_aliases_present(self):
        self.assertEqual(nr.STOCK_KEYWORD_MAP["reliance"], "RELIANCE.NS")
        self.assertEqual(nr.STOCK_KEYWORD_MAP["tcs"], "TCS.NS")

    def test_all_values_are_ns_or_bo_tickers(self):
        for kw, tk in nr.STOCK_KEYWORD_MAP.items():
            self.assertTrue(
                tk.endswith(".NS") or tk.endswith(".BO"),
                f"{kw!r} -> {tk!r} is not a normalized ticker",
            )


class TestSentimentKeywordLists(unittest.TestCase):
    def test_no_overlap_bullish_bearish(self):
        overlap = set(nr.BULLISH_KEYWORDS) & set(nr.BEARISH_KEYWORDS)
        self.assertEqual(overlap, set(), f"sentiment keywords overlap: {overlap}")


if __name__ == "__main__":
    unittest.main()
