import unittest

from marketdata import market_calendar as mc


class TestMarketHoliday(unittest.TestCase):
    def test_republic_day_2026_is_holiday(self):
        self.assertTrue(mc.is_market_holiday(1, 26, 2026))

    def test_regular_weekday_is_not_holiday(self):
        self.assertFalse(mc.is_market_holiday(1, 27, 2026))

    def test_christmas_2026_is_holiday(self):
        self.assertTrue(mc.is_market_holiday(12, 25, 2026))

    def test_unknown_year_falls_back_to_2026_set(self):
        # Republic Day is in the 2026 fallback set
        self.assertTrue(mc.is_market_holiday(1, 26, 1999))


class TestPublishedAfterMarketHours(unittest.TestCase):
    def test_empty_string_is_false(self):
        self.assertFalse(mc.published_after_market_hours(""))

    def test_weekend_publication_is_after_hours(self):
        # 2026-06-06 is a Saturday -> outside trading hours
        self.assertTrue(
            mc.published_after_market_hours("Sat, 06 Jun 2026 06:00:00 +0000")
        )


class TestHasMarketTradedSince(unittest.TestCase):
    def test_none_returns_true(self):
        self.assertTrue(mc.has_market_traded_since(None))

    def test_future_publication_returns_false(self):
        self.assertFalse(mc.has_market_traded_since("Fri, 01 Jan 2999 00:00:00 +0000"))


if __name__ == "__main__":
    unittest.main()
