"""Adversarial contract for strictly non-retroactive CLOSE trigger binding."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research"))
from pnl_cost_decomposition import close_reason_coverage, read_bounded_driver_receipts


def fixture():
    episodes = [{"end_command": "cmd-close", "last_original_send_ms": 3000,
                 "net_usd": "-3.50"}]
    fills = [{"command_id": "cmd-close", "execution_id": "exec-1", "order_id": 17, "quantity": "100", "price": "1.10"}]
    receipts = [{
        "schema": "heptatrader.driver-close-command-attribution.v1",
        "instrument": "EUR.USD", "original_command_id": "cmd-close",
        "broker_order_id": 17, "broker_execution_id": "exec-1",
        "broker_execution_ids": ["exec-1"], "terminal_partial_exit": False,
        "broker_terminal_observed": True, "position_after_terminal_fill": 0,
        "broker_fill_quantity": 100.0, "broker_average_fill_price": 1.10,
        "recorded_at_ms": 5000,
        "exit_trigger": {
            "schema": "heptatrader.driver-close-trigger.v1",
            "original_command_id": "cmd-close",
            "reason_code": "MODEL_REVERSAL", "reason_source": "DRIVER_EVALUATED",
            "trigger_at_ms": 2500, "quote_observed_at_ms": 2200,
        },
    }]
    return episodes, fills, receipts


class ExactReasonTests(unittest.TestCase):
    def check(self, episodes, fills, receipts):
        return close_reason_coverage(episodes, fills, receipts, "EUR.USD")

    def test_exact_reason_with_original_broker_execution(self):
        e, f, r = fixture()
        d = self.check(e, f, r)
        self.assertEqual(d["exact_original_reason_bound"], 1)
        self.assertEqual(d["reason_buckets"]["MODEL_REVERSAL"],
                         {"roundtrips": 1, "net_usd": "-3.50"})
        self.assertEqual(d["unknown_or_unverified"], 0)
        self.assertTrue(d["historical_unattributed_not_repaired"])

    def test_no_receipt_preserves_unknown_denominator(self):
        e, f, _ = fixture()
        d = self.check(e, f, [])
        self.assertEqual(d["unknown_or_unverified"], 1)
        self.assertEqual(d["reason_buckets"], {})
        self.assertEqual(d["nonqualifying_reasons"]["NO_EXACT_CLOSE_RECEIPT"], 1)

    def test_unrelated_recent_receipts_do_not_backfill(self):
        e, f, r = fixture()
        r[0]["original_command_id"] = "different"
        r[0]["exit_trigger"]["original_command_id"] = "different"
        d = self.check(e, f, r)
        self.assertEqual(d["exact_original_reason_bound"], 0)
        self.assertEqual(d["unmatched_retained_recent_receipts"], 1)

    def test_wrong_execution_identity_is_not_attributed(self):
        e, f, r = fixture()
        r[0]["broker_execution_ids"] = ["unknown"]
        d = self.check(e, f, r)
        self.assertEqual(d["unknown_or_unverified"], 1)
        self.assertEqual(d["nonqualifying_reasons"]["ORIGINAL_EXECUTION_IDENTITY_NOT_PROVEN"], 1)

    def test_wrong_broker_order_is_not_attributed(self):
        e, f, r = fixture()
        r[0]["broker_order_id"] = 19
        self.assertEqual(self.check(e, f, r)["exact_original_reason_bound"], 0)

    def test_late_trigger_not_retroactively_assigned(self):
        e, f, r = fixture()
        r[0]["exit_trigger"]["trigger_at_ms"] = 3500
        self.assertEqual(self.check(e, f, r)["nonqualifying_reasons"]
                         ["CAUSAL_TRIGGER_TIMESTAMP_NOT_PROVEN"], 1)

    def test_pretrigger_quote_must_precede_trigger(self):
        e, f, r = fixture()
        r[0]["exit_trigger"]["quote_observed_at_ms"] = 2600
        self.assertEqual(self.check(e, f, r)["exact_original_reason_bound"], 0)

    def test_not_terminal_flat_is_not_attributed(self):
        e, f, r = fixture()
        r[0]["position_after_terminal_fill"] = 1
        self.assertEqual(self.check(e, f, r)["nonqualifying_reasons"]["NOT_FLAT_TERMINAL_FILL"], 1)

    def test_partial_exit_cannot_backdate_flat_reason(self):
        e, f, r = fixture()
        r[0]["terminal_partial_exit"] = True
        self.assertEqual(self.check(e, f, r)["exact_original_reason_bound"], 0)

    def test_tampered_quantity_never_qualifies(self):
        e, f, r = fixture()
        r[0]["broker_fill_quantity"] = 99.
        self.assertEqual(self.check(e, f, r)["nonqualifying_reasons"]
                         ["CLOSE_FILL_QUANTITY_OR_PRICE_UNRECONCILED"], 1)

    def test_tampered_price_never_qualifies(self):
        e, f, r = fixture()
        r[0]["broker_average_fill_price"] = 1.105
        self.assertEqual(self.check(e, f, r)["nonqualifying_reasons"]
                         ["CLOSE_FILL_QUANTITY_OR_PRICE_UNRECONCILED"], 1)

    def test_missing_execution_economics_never_qualifies(self):
        e, f, r = fixture()
        f[0].pop("price")
        self.assertEqual(self.check(e, f, r)["exact_original_reason_bound"], 0)

    def test_last_exit_reason_is_never_evidence(self):
        e, f, r = fixture()
        r[0]["exit_trigger"]["reason_code"] = "LEGACY_UNATTRIBUTED"
        r[0]["exit_trigger"]["reason_source"] = "LEGACY_MISSING"
        self.assertEqual(self.check(e, f, r)["nonqualifying_reasons"]
                         ["MISSING_OR_UNSUPPORTED_ORIGINAL_REASON"], 1)

    def test_duplicate_proofs_fail(self):
        e, f, r = fixture()
        with self.assertRaisesRegex(ValueError, "DUPLICATE_COMMAND"):
            self.check(e, f, [r[0], r[0]])

    def test_more_than_bounded_history_refused(self):
        e, f, r = fixture()
        with self.assertRaisesRegex(ValueError, "CLOSE_REASON_SOURCE_SCOPE"):
            self.check(e, f, r * 65)

    def test_wrong_instrument_claim_never_qualified(self):
        e, f, r = fixture()
        r[0]["instrument"] = "BTC.USD"
        self.assertEqual(self.check(e, f, r)["exact_original_reason_bound"], 0)

    def test_driver_state_hash_and_empty_history(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "state.json"
            p.write_text(json.dumps({"instrument": "EUR.USD", "close_command_attribution": []}))
            receipts, sha = read_bounded_driver_receipts(p, "EUR.USD")
            self.assertEqual(receipts, [])
            self.assertEqual(len(sha), 64)

    def test_symlinked_driver_state_refused(self):
        with tempfile.TemporaryDirectory() as root:
            original, link = Path(root) / "state.json", Path(root) / "link"
            original.write_text(json.dumps({"instrument": "EUR.USD"}))
            link.symlink_to(original)
            with self.assertRaisesRegex(ValueError, "UNSAFE_STATE_PATH"):
                read_bounded_driver_receipts(link, "EUR.USD")


if __name__ == "__main__":
    unittest.main()
