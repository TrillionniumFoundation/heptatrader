"""Isolation tests for the non-deployable original-decision net probe."""
import unittest
import numpy as np

from historical_training.common import EvidenceError
from historical_training.value_original_oos_probe import (
    features, three_way_split, fit_huber_ridge, predict, stats, economic_tail, EMBARGO_MS,
)


def example(t=1700000000000, side="BUY"):
    history = []
    for i in range(24):
        mid = 1.1 + (i - 12) * 0.000001
        history.append({"observed_at_ms": t - (24-i)*12000,
                        "bid": mid - .000005, "mid": mid, "ask": mid + .000005})
    return {"input_started_at_ms": t, "action": side,
            "source_request": {"request": {"started_at_ms": t, "history_snapshot": history}}}


class PretradeTests(unittest.TestCase):
    def test_only_pretrade_snapshot_and_action(self):
        r = example()
        a = features(r)
        self.assertEqual(len(a), 16)
        self.assertTrue(all(np.isfinite(a)))

    def test_future_quote_must_fail(self):
        r = example()
        r["source_request"]["request"]["history_snapshot"][-1]["observed_at_ms"] = r["input_started_at_ms"]
        with self.assertRaisesRegex(EvidenceError, "NONCAUSAL_QUOTE"):
            features(r)

    def test_bad_bid_mid_ask_must_fail(self):
        r = example()
        r["source_request"]["request"]["history_snapshot"][7]["bid"] = 1.9
        with self.assertRaisesRegex(EvidenceError, "INVALID_PRETRADE_QUOTE"):
            features(r)

    def test_stale_snapshot_must_fail(self):
        r = example()
        for q in r["source_request"]["request"]["history_snapshot"]:
            q["observed_at_ms"] -= 180_000
        with self.assertRaisesRegex(EvidenceError, "STALE_PRETRADE_QUOTE"):
            features(r)

    def test_action_must_be_original_direction(self):
        with self.assertRaisesRegex(EvidenceError, "ACTION_SCOPE"):
            features(example(side="HOLD"))


class MaturityTests(unittest.TestCase):
    def test_outcomes_after_boundary_purged(self):
        times = [1700000000000 + i*20*60_000 for i in range(150)]
        rows = [{"input_started_at_ms":t} for t in times]
        # Every normal fill matures one minute after request.
        m = [t + 60_000 for t in times]
        m[89] = times[90] + 1
        m[119] = times[120] + 1
        tr, va, te, bounds = three_way_split(rows, m)
        self.assertEqual(len(te), 30)
        self.assertLess(len(tr), 90)
        self.assertLess(len(va), 30)
        self.assertTrue(all(m[i] + EMBARGO_MS < bounds[0] for i in tr))
        self.assertTrue(all(m[i] + EMBARGO_MS < bounds[1] for i in va))

    def test_all_training_labels_late_rejected(self):
        times = [1700000000000 + i*20*60_000 for i in range(150)]
        m = [times[-1] + 60000]*150
        with self.assertRaisesRegex(EvidenceError, "PURGED_TRAIN_VALIDATION"):
            three_way_split([{"input_started_at_ms":t} for t in times],m)


class ObjectiveTests(unittest.TestCase):
    def test_deterministic_finite_cpu_readout(self):
        x = np.stack([np.arange(70)/30, np.ones(70), np.sin(np.arange(70)/6)],axis=1)
        # Explicit fixed label function, not historical returns.
        y = x[:,0]*3 - 4
        model = fit_huber_ridge(x, y, 10.)
        self.assertTrue(np.isfinite(predict(model, x)).all())
        self.assertEqual(stats(predict(model, x), y)["count"],70)

    def test_no_test_tuning_or_trading_side_effects(self):
        import historical_training.value_original_oos_probe as module
        self.assertEqual(module.ALPHAS, (1.,10.,100.,1000.))
        self.assertEqual(module.EMBARGO_MS,15*60_000)




def episode(t=1700000000000, net="-2", fee="4", gross="2"):
    row = example(t)
    row["net_usd"] = net
    row["actual_commissions_usd"] = fee
    row["reward"] = {
        "realized_gross_usd": gross,
        "open_command_id": "original-open",
        "executions": [
            {"execution_id": "entry", "command_id": "original-open"},
            {"execution_id": "exit", "command_id": "original-close"},
        ],
    }
    return row


class ExecutionEconomicsTests(unittest.TestCase):
    def test_fee_flip_from_actual_execution_with_short_duration(self):
        row = episode()
        out = economic_tail([row], {"entry": 1700000001000, "exit": 1700000061000})
        self.assertEqual(out["all_rows"]["net_usd"], "-2")
        self.assertEqual(out["all_rows"]["gross_usd"], "2")
        self.assertEqual(out["all_rows"]["reported_commissions_usd"], "4")
        self.assertEqual(out["all_rows"]["gross_positive_but_net_nonpositive"], 1)
        self.assertEqual(out["by_execution_span"]["LT_5_MIN"]["count"], 1)
        self.assertEqual(out["worst_5_share_of_negative_loss_pool_pct"], "100.0000")

    def test_fee_inconsistency_is_never_rounded_or_ignored(self):
        row = episode(net="-2.0001")
        with self.assertRaisesRegex(EvidenceError, "EXACT_GROSS_FEE_NET_IDENTITY"):
            economic_tail([row], {"entry": 1700000001000, "exit": 1700000061000})

    def test_missing_exit_evidence_rejected(self):
        with self.assertRaisesRegex(EvidenceError, "OPEN_CLOSE_EXECUTION_TIME_MISSING"):
            economic_tail([episode()], {"entry": 1700000001000})

    def test_exit_before_open_rejected(self):
        with self.assertRaisesRegex(EvidenceError, "CLOSE_EARLIER_THAN_OPEN"):
            economic_tail([episode()], {"entry": 1700000061000, "exit": 1700000001000})

    def test_fee_burden_and_short_hold_are_not_alternate_action_profits(self):
        row = episode()
        out = economic_tail([row], {"entry": 1700000001000, "exit": 1700000061000})
        self.assertEqual(out["scope"], "EXECUTED_ORIGINAL_POLICY_ONLY_NOT_COUNTERFACTUAL_OR_TRADING_SIGNAL")
        self.assertTrue(out["pretrade_quote_not_execution_spread"])

if __name__ == "__main__":
    unittest.main()
