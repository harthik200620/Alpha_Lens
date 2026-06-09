import unittest

from signals import fno_engine as fe


def _nifty(spot=24400, pcr=1.0, max_pain=24400, call_wall=24700, put_wall=24200,
           atm_iv=12.0, iv_skew=0.0, sentiment="NEUTRAL"):
    gap = round((spot - max_pain) / max_pain * 100, 2) if max_pain else None
    return {
        "symbol": "NIFTY", "label": "Nifty 50", "spot": spot, "pcr": pcr,
        "max_pain": max_pain, "max_pain_gap_pct": gap, "call_wall": call_wall,
        "put_wall": put_wall, "atm_iv": atm_iv, "iv_skew": iv_skew,
        "sentiment": sentiment,
    }


def _participant(net):
    read = ("put-writing (supportive)" if net > 0
            else "call-writing (capping)" if net < 0 else "mixed")
    return {"applicable": True, "headline": {
        "fii_index_fut_net": net, "bias": "BULLISH" if net > 0 else "BEARISH",
        "fii_option_read": read, "summary": f"FII net {net:+,} index-fut contracts."}}


BULL_COUNTS = {"Long Buildup": 40, "Short Buildup": 8, "Short Covering": 12, "Long Unwinding": 6}
BEAR_COUNTS = {"Long Buildup": 8, "Short Buildup": 40, "Short Covering": 6, "Long Unwinding": 14}
FLAT_COUNTS = {"Long Buildup": 15, "Short Buildup": 15, "Short Covering": 8, "Long Unwinding": 8}


class TestStanceDirection(unittest.TestCase):
    def test_bullish_inputs_give_bullish_stance(self):
        o = fe.build_tomorrow_outlook(
            {"score": 45}, [_nifty(spot=24400, pcr=1.4, max_pain=24550,
                                    sentiment="BULLISH", iv_skew=-1.0)],
            _participant(25000), BULL_COUNTS, [], 12.5, [])
        self.assertTrue(o["applicable"])
        self.assertEqual(o["headline"]["direction"], "bullish")
        self.assertGreater(o["headline"]["score"], 0)

    def test_bearish_inputs_give_bearish_stance(self):
        o = fe.build_tomorrow_outlook(
            {"score": -45}, [_nifty(spot=24400, pcr=0.65, max_pain=24250,
                                    sentiment="BEARISH", iv_skew=3.0)],
            _participant(-25000), BEAR_COUNTS, [], 18.0, [])
        self.assertEqual(o["headline"]["direction"], "bearish")
        self.assertLess(o["headline"]["score"], 0)

    def test_balanced_inputs_give_rangebound(self):
        o = fe.build_tomorrow_outlook(
            {"score": 0}, [_nifty(pcr=1.0, sentiment="NEUTRAL")],
            _participant(0), FLAT_COUNTS, [], 13.0, [])
        self.assertEqual(o["headline"]["stance"], "Range-bound")
        self.assertEqual(o["headline"]["direction"], "neutral")


class TestRangeAndLevels(unittest.TestCase):
    def test_range_brackets_spot_and_levels_passthrough(self):
        o = fe.build_tomorrow_outlook(
            {"score": 20}, [_nifty(spot=24400, call_wall=24800, put_wall=24000,
                                   max_pain=24500)],
            _participant(5000), BULL_COUNTS, [], 13.0, [])
        idx = o["index"]
        self.assertLess(idx["range_low"], 24400)
        self.assertGreater(idx["range_high"], 24400)
        self.assertEqual(idx["support"], 24000)
        self.assertEqual(idx["resistance"], 24800)
        self.assertEqual(idx["magnet"], 24500)
        self.assertGreater(idx["expected_move_pct"], 0)

    def test_higher_vix_widens_expected_move(self):
        lo = fe.build_tomorrow_outlook({"score": 10}, [_nifty()], None, FLAT_COUNTS, [], 10.0, [])
        hi = fe.build_tomorrow_outlook({"score": 10}, [_nifty()], None, FLAT_COUNTS, [], 22.0, [])
        self.assertGreater(hi["index"]["expected_move_pct"], lo["index"]["expected_move_pct"])


class TestConfidence(unittest.TestCase):
    def test_confidence_bounded_25_80(self):
        for sc in (-100, -50, 0, 50, 100):
            o = fe.build_tomorrow_outlook({"score": sc}, [_nifty(sentiment="BULLISH")],
                                          _participant(sc * 100), BULL_COUNTS, [], 13.0, [])
            self.assertGreaterEqual(o["headline"]["confidence"], 25)
            self.assertLessEqual(o["headline"]["confidence"], 80)

    def test_high_vix_lowers_confidence(self):
        calm = fe.build_tomorrow_outlook({"score": 60}, [_nifty(sentiment="BULLISH")],
                                         _participant(40000), BULL_COUNTS, [], 11.0, [])
        panic = fe.build_tomorrow_outlook({"score": 60}, [_nifty(sentiment="BULLISH")],
                                          _participant(40000), BULL_COUNTS, [], 26.0, [])
        self.assertLess(panic["headline"]["confidence"], calm["headline"]["confidence"])


class TestSafety(unittest.TestCase):
    def test_no_participant_still_works(self):
        o = fe.build_tomorrow_outlook({"score": 30}, [_nifty()], None, BULL_COUNTS, [], 13.0, [])
        self.assertTrue(o["applicable"])
        self.assertFalse(any(f["key"] == "fii" for f in o["factors"]))

    def test_no_nifty_still_produces_summary(self):
        o = fe.build_tomorrow_outlook({"score": 25}, [], _participant(10000),
                                      BULL_COUNTS, [], 13.0, [])
        self.assertTrue(o["applicable"])
        self.assertIsNone(o["index"]["spot"])
        self.assertIn("tomorrow looks", o["summary"])

    def test_every_factor_has_plain_and_lean(self):
        o = fe.build_tomorrow_outlook({"score": 30}, [_nifty(sentiment="BULLISH", iv_skew=2.0)],
                                      _participant(20000), BULL_COUNTS, [], 13.0, [])
        self.assertTrue(o["factors"])
        for f in o["factors"]:
            self.assertTrue(f["plain"])
            self.assertIn(f["lean"], ("bullish", "bearish", "neutral"))

    def test_board_includes_outlook(self):
        board = fe.build_smart_money_board({}, watchlist=[])
        self.assertIn("outlook", board)

    def test_small_fii_net_reads_as_flat(self):
        # A tiny FII net (below FII_NET_FLAT) must NOT be sold as a strong tell.
        o = fe.build_tomorrow_outlook({"score": 5}, [_nifty()], _participant(500),
                                      FLAT_COUNTS, [], 13.0, [])
        fii = next(f for f in o["factors"] if f["key"] == "fii")
        self.assertEqual(fii["lean"], "neutral")
        self.assertIn("roughly flat", fii["plain"])

    def test_large_fii_net_is_directional(self):
        o = fe.build_tomorrow_outlook({"score": 20}, [_nifty()], _participant(60000),
                                      BULL_COUNTS, [], 13.0, [])
        fii = next(f for f in o["factors"] if f["key"] == "fii")
        self.assertEqual(fii["lean"], "bullish")
        self.assertIn("net LONG", fii["plain"])

    def test_sector_rotation_in_summary(self):
        sectors = [{"sector": "Banks", "direction": "bullish", "count": 5, "net_bias": 40}]
        o = fe.build_tomorrow_outlook({"score": 25}, [_nifty()], _participant(20000),
                                      BULL_COUNTS, sectors, 13.0, [])
        self.assertIn("rotating into Banks", o["summary"])


if __name__ == "__main__":
    unittest.main()
