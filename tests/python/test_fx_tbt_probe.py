"""Read-only tick-by-tick BidAsk wire and source timestamp regressions."""
from __future__ import annotations
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
import hepta_fx_tick_by_tick_probe as probe


def msg(fields):
    return "\0".join(fields) + "\0"


class TickWireTests(unittest.TestCase):
    def test_only_one_exact_bidask_subscription(self):
        gate = probe.TickWireGate(29755)
        gate.check(msg(["71", "2", "29755"]))
        gate.check(msg(["59", "1", "1"]))
        gate.check(msg(probe.TICK_WIRE))
        gate.check(msg(["98", "5"]))
        self.assertEqual(gate.counts, {"71": 1, "59": 1, "97": 1, "98": 1})
        self.assertTrue(gate.cancelled)

    def test_foreign_opcode_instrument_and_duplicate_denied(self):
        def initialized():
            gate = probe.TickWireGate(29755)
            gate.check(msg(["71", "2", "29755"]))
            return gate
        for fields in (
            ["3", "45", "1"],
            ["4", "2", "3"],
            ["58", "1"],
            ["76", "1", "1", "other", "", "1"],
            probe.TICK_WIRE[:3] + ["GBP"] + probe.TICK_WIRE[4:],
            probe.TICK_WIRE[:1] + ["6"] + probe.TICK_WIRE[2:],
            ["98", "5"],
        ):
            with self.subTest(fields=fields[:3]):
                with self.assertRaises(probe.ProbeViolation):
                    initialized().check(msg(fields))
        gate = initialized()
        gate.check(msg(probe.TICK_WIRE))
        with self.assertRaises(probe.ProbeViolation):
            gate.check(msg(probe.TICK_WIRE))
        gate.check(msg(["98", "5"]))
        with self.assertRaises(probe.ProbeViolation):
            gate.check(msg(["98", "5"]))


class TickTimeTests(unittest.TestCase):
    def test_both_prices_have_one_source_tick(self):
        q = probe.TimedBidAsk()
        epoch = 1791439407
        receipt = epoch * 1000 + 250
        q.receive(epoch, 1.1191, 1.1192, receipt)
        evidence = q.fresh(receipt + 4000)
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["broker_tick_seconds"], epoch)
        self.assertEqual(evidence["source_age_ms"], 4250)
        self.assertFalse(evidence["account_valuation_time_proven"])
        self.assertIsNone(q.fresh(receipt + 5000))
        self.assertEqual(q.callbacks, 1)

    def test_crossed_invalid_future_and_regressed_ticks_fail_closed(self):
        q = probe.TimedBidAsk()
        t = 1791439407
        q.receive(t, 1.13, 1.12, t * 1000 + 500)
        self.assertEqual(q.rejected, 1)
        self.assertIsNone(q.fresh(t * 1000 + 500))
        q.receive(t, 1.12, 1.13, t * 1000 + 500)
        self.assertIsNotNone(q.fresh(t * 1000 + 500))
        q.receive(t - 1, 1.12, 1.13, t * 1000 + 550)
        self.assertEqual(q.source_regressions, 1)
        self.assertIsNone(q.fresh(t * 1000 + 550))
        q.receive(t + 10, 1.12, 1.13, t * 1000 + 600)
        self.assertIsNone(q.fresh(t * 1000 + 600))
        self.assertGreaterEqual(q.rejected, 3)

    def test_missing_and_nonfinite_fail_closed(self):
        q = probe.TimedBidAsk()
        self.assertIsNone(q.fresh(1234))
        for bid, ask in ((float("nan"), 1.2), (1.0, float("inf")), (0.0, 1.0)):
            q.receive(1791439407, bid, ask, 1791439407100)
            self.assertIsNone(q.fresh(1791439407100))


if __name__ == "__main__":
    unittest.main()
