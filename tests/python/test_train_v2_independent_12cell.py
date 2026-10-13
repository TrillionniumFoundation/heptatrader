"""Independent frozen-3x4 auditor tests: synthetic values, no broker/source API."""
import copy
from decimal import Decimal
import hashlib
import unittest
from train_v2.independent_frozen_12cell import (
    independent_window_price, CustodyError, HORIZONS,
)


class IndependentFrozenPriceUnitTests(unittest.TestCase):
    def setUp(self):
        begin = 1791600000000
        self.rows = []
        for index in range(370):
            event = begin + index * 10000
            identifier = hashlib.sha256(str(index).encode()).hexdigest()
            mid = Decimal("1.100000") + Decimal(index)/Decimal("1000000")
            self.rows.append((event+2, event, event+5000, identifier, mid))
        first = self.rows[5]
        self.src = {
            "original_source_id": first[3],
            "source_event_at_ms": first[1],
            "source_available_at_ms": first[0],
            "source_valid_until_ms": first[2],
            "input_quote_midpoint": str(first[4]),
            "decision_at_ms": first[1] + 5000,
            "original_feature_input_receipts": [
                {
                    "source_id": row[3],
                    "source_sha256": row[3],
                    "available_at_ms": row[0],
                    "event_at_ms": row[1],
                }
                for row in self.rows[:6]
            ],
        }
        self.outcomes = []
        self.audits = []
        for horizon in HORIZONS:
            due = self.src["decision_at_ms"]+horizon*60000
            target = next(row for row in self.rows if row[1]>=due)
            observed = (target[4]/first[4]-Decimal(1))*Decimal(10000)
            self.outcomes.append({
                "due_at_ms": due, "horizon_min":horizon,
                "original_midprice_change_bps":str(observed),
            })
            self.audits.append({
                "immutable_outcome_sha256":hashlib.sha256(
                    str(horizon).encode()).hexdigest(),
            })
        self.source = {"window_rows": self.rows}

    def run_audit(self, source=None, row=None, outcomes=None, audits=None):
        return independent_window_price(
            self.source if source is None else source,
            self.src if row is None else row,
            self.outcomes if outcomes is None else outcomes,
            self.audits if audits is None else audits,
        )

    def test_four_independent_original_price_recomputations(self):
        result = self.run_audit()
        self.assertEqual([r["horizon_min"] for r in result],list(HORIZONS))
        self.assertTrue(all(r["outcome_matches_original_bytes"] for r in result))
        self.assertTrue(all(not r["economically_executable_return_attested"]
                            for r in result))
        self.assertEqual([r["original_outcome_sha256"] for r in result],
                         [p["immutable_outcome_sha256"] for p in self.audits])

    def test_price_tampering_rejected(self):
        outcomes=copy.deepcopy(self.outcomes)
        outcomes[1]["original_midprice_change_bps"]="9999"
        with self.assertRaisesRegex(CustodyError,"PRICE_MISMATCH"):
            self.run_audit(outcomes=outcomes)

    def test_horizon_reordering_rejected(self):
        outcomes=list(reversed(self.outcomes))
        with self.assertRaisesRegex(CustodyError,"FROZEN_HORIZON_CHANGED"):
            self.run_audit(outcomes=outcomes)

    def test_no_source_reference_refused(self):
        altered=copy.deepcopy(self.src)
        altered["original_source_id"]="f"*64
        with self.assertRaisesRegex(CustodyError,"INPUT_QUOTE_ABSENT"):
            self.run_audit(row=altered)

    def test_input_arrival_relabel_refused(self):
        altered=copy.deepcopy(self.src)
        altered["source_available_at_ms"] += 1
        with self.assertRaisesRegex(CustodyError,"INPUT_ID_TIME_PRICE_CHANGED"):
            self.run_audit(row=altered)

    def test_future_predecision_feature_refused(self):
        altered=copy.deepcopy(self.src)
        altered["original_feature_input_receipts"][-1]["available_at_ms"] = (
            self.src["decision_at_ms"]+1)
        with self.assertRaises(CustodyError):
            self.run_audit(row=altered)

    def test_input_broker_expiry_refused(self):
        altered=copy.deepcopy(self.src)
        altered["decision_at_ms"] += 1
        outcomes=copy.deepcopy(self.outcomes)
        for outcome in outcomes:outcome["due_at_ms"]+=1
        with self.assertRaisesRegex(CustodyError,"BROKER_EXPIRY_OR_TIME"):
            self.run_audit(row=altered,outcomes=outcomes)

    def test_endpoint_missing_refused(self):
        cut=self.rows[:330]
        with self.assertRaisesRegex(CustodyError,"ENDPOINT_MISSING"):
            self.run_audit(source={"window_rows":cut})

    def test_original_endpoint_path_gap_refused(self):
        cut=[row for n,row in enumerate(self.rows) if n not in range(100,108)]
        with self.assertRaisesRegex(CustodyError,"INDEPENDENT_ORIGINAL_PATH_GAP"):
            self.run_audit(source={"window_rows":cut})

    def test_input_lineage_hash_refused(self):
        altered=copy.deepcopy(self.src)
        altered["original_feature_input_receipts"][1]["source_sha256"]="a"*64
        with self.assertRaisesRegex(CustodyError,"FEATURE_LINEAGE_CHANGED"):
            self.run_audit(row=altered)

    def test_source_midpoint_changed_refused(self):
        altered=copy.deepcopy(self.src)
        altered["input_quote_midpoint"]="1.999999"
        with self.assertRaisesRegex(CustodyError,"INPUT_ID_TIME_PRICE_CHANGED"):
            self.run_audit(row=altered)
