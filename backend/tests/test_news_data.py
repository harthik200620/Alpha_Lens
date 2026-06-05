import unittest

import news_data as nd


class TestMacroImpactMap(unittest.TestCase):
    def test_entries_are_lists_of_ticker_direction_pairs(self):
        for phrase, impacts in nd.MACRO_IMPACT_MAP.items():
            self.assertIsInstance(impacts, list, f"{phrase!r} value is not a list")
            self.assertTrue(impacts, f"{phrase!r} has no impacts")
            for pair in impacts:
                self.assertEqual(len(pair), 2, f"{phrase!r} pair {pair!r} is not (ticker, dir)")
                ticker, direction = pair
                self.assertTrue(
                    ticker.endswith(".NS") or ticker.endswith(".BO"),
                    f"{phrase!r} ticker {ticker!r} not normalized",
                )
                self.assertIn(direction, ("BULLISH", "BEARISH"))


class TestKeywordTables(unittest.TestCase):
    def test_material_event_keywords_nonempty_strings(self):
        self.assertTrue(nd.MATERIAL_EVENT_KEYWORDS)
        for kw in nd.MATERIAL_EVENT_KEYWORDS:
            self.assertIsInstance(kw, str)
            self.assertEqual(kw, kw.lower(), f"{kw!r} should be lowercase for matching")

    def test_low_signal_phrases_nonempty(self):
        self.assertTrue(nd.LOW_SIGNAL_PHRASES)

    def test_index_like_symbols_is_set(self):
        self.assertIsInstance(nd.INDEX_LIKE_SYMBOLS, set)

    def test_common_uppercase_words_is_set(self):
        self.assertIsInstance(nd.COMMON_UPPERCASE_WORDS, set)


if __name__ == "__main__":
    unittest.main()
