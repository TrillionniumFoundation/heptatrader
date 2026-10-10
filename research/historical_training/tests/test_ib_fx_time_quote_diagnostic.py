"""Strict broker-server-second versus source-verified quote diagnostic tests."""
import unittest
from historical_training.common import EvidenceError
from historical_training.ib_fx_time_quote_diagnostic import diagnostic_match

class ExecutionTimeQuoteTests(unittest.TestCase):
    def setUp(self):
        self.time=1791552000000
        self.row={"execution_id":"e.01","server_time_second_ms":self.time,
                  "server_time_precision_ms":1000,"side":"BOT",
                  "price":"1.1202","matched_exact_oms_economics":True,
                  "price_revision_pending":False}
        self.q={"t":self.time-1100,"known_at_ms":self.time-900,
                "bid":"1.1200","ask":"1.1201"}
    def test_strict_preceding_quote_is_diagnostic_only(self):
        result=diagnostic_match([self.row],[self.q])
        self.assertEqual(result["strict_preceding_quote_matches"],1)
        self.assertGreater(result["apparent_price_deviation_bps"]["p50"],0)
        self.assertFalse(result["actual_slippage_verified"])
        self.assertTrue(result["broker_server_time_not_exchange_time"])
        self.assertEqual(result["orders_sent"],0)
    def test_quote_received_after_server_second_cannot_backfill(self):
        future={**self.q,"known_at_ms":self.time+1}
        result=diagnostic_match([self.row],[future])
        self.assertEqual(result["strict_preceding_quote_matches"],0)
        self.assertEqual(result["excluded"]["quote_unavailable"],1)
    def test_quote_two_seconds_old_rejected(self):
        old={**self.q,"known_at_ms":self.time-3000,"t":self.time-3200}
        result=diagnostic_match([self.row],[old])
        self.assertEqual(result["excluded"]["quote_stale"],1)
    def test_mark_five_seconds_old_rejected_even_with_recent_ingest(self):
        old={**self.q,"t":self.time-5100}
        result=diagnostic_match([self.row],[old])
        self.assertEqual(result["excluded"]["quote_stale"],1)
    def test_latest_but_future_unavailable_quote_ignored(self):
        future={**self.q,"known_at_ms":self.time+5,"t":self.time-80}
        result=diagnostic_match([self.row],[self.q,future])
        self.assertEqual(result["strict_preceding_quote_matches"],1)
    def test_unmatched_oms_and_pending_revision_never_count(self):
        a={**self.row,"matched_exact_oms_economics":False}
        b={**self.row,"execution_id":"f.01","price_revision_pending":True}
        result=diagnostic_match([a,b],[self.q])
        self.assertEqual(result["strict_preceding_quote_matches"],0)
        self.assertEqual(result["excluded"]["not_original_oms_matched"],1)
        self.assertEqual(result["excluded"]["price_pending"],1)
    def test_precision_is_not_silently_promoted_to_milliseconds(self):
        for precision in (1,100,999,None):
            row={**self.row,"server_time_precision_ms":precision}
            with self.subTest(precision=precision),self.assertRaisesRegex(EvidenceError,"ROW_INVALID"):
                diagnostic_match([row],[self.q])
    def test_spread_inversion_or_nan_fails_closed(self):
        for bad in ({**self.q,"ask":"1.1199"},{**self.q,"bid":"nan"}):
            with self.subTest(bad=bad),self.assertRaisesRegex(EvidenceError,"QUOTE_OR_EXECUTION_PRICE"):
                diagnostic_match([self.row],[bad])
    def test_sell_price_deviation_sign_is_conservative(self):
        row={**self.row,"side":"SLD","price":"1.1199"}
        result=diagnostic_match([row],[self.q])
        self.assertGreater(result["apparent_price_deviation_bps"]["p50"],0)
    def test_correction_family_not_double_counted_as_independent_fill(self):
        old=dict(self.row,execution_id="broker.01")
        correction=dict(self.row,execution_id="broker.02",price="1.1203")
        with self.assertRaisesRegex(EvidenceError,"CORRECTION_FAMILY_AMBIGUOUS"):
            diagnostic_match([old,correction],[self.q])

    def test_unsupported_side_fails_closed(self):
        row={**self.row,"side":"BUY"}
        with self.assertRaisesRegex(EvidenceError,"ROW_INVALID"):
            diagnostic_match([row],[self.q])

if __name__=="__main__":
    unittest.main()
