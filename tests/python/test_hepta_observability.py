from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "hepta_observability", ROOT / "scripts/hepta_observability.py"
)
assert SPEC and SPEC.loader
OBS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OBS)


class HeptaObservabilityTests(unittest.TestCase):
    def test_metrics_and_alerts_are_derived_deterministically(self) -> None:
        records = [
            {
                "schema_version": 4,
                "event": "order_intent",
                "ts_ms": 1000,
                "order_id": 7,
                "event_id": "event-1",
            },
            {
                "schema_version": 4,
                "event": "risk_blocked",
                "ts_ms": 2000,
                "order_id": 7,
                "event_id": "event-1",
                "risk_code": "RISK_QTY_OUT_OF_RANGE",
            },
            {
                "schema_version": 4,
                "event": "place_outcome_uncertain",
                "ts_ms": 3000,
                "order_id": 8,
                "event_id": "event-3",
                "broker_error_code": 201,
            },
        ]
        metrics, alerts = OBS.derive(records, malformed=1)
        self.assertIn(
            'heptatrader_oms_events_total{event="order_intent"} 1', metrics
        )
        self.assertIn(
            'heptatrader_oms_risk_blocks_total{risk_code="RISK_QTY_OUT_OF_RANGE"} 1',
            metrics,
        )
        self.assertIn("heptatrader_oms_duplicate_event_ids_total 1", metrics)
        self.assertIn("heptatrader_oms_outcome_uncertain_total 1", metrics)
        self.assertEqual(
            [item["rule"] for item in alerts],
            [
                "OMS_JOURNAL_MALFORMED",
                "OMS_DUPLICATE_EVENT_ID",
                "OMS_OUTCOME_UNCERTAIN",
                "IB_ORDER_REJECTED_201",
            ],
        )

    def test_reader_rejects_symlink_and_reads_private_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = root / "journal.jsonl"
            journal.write_text(
                json.dumps({"event": "app_boot", "ts_ms": 1}) + "\n",
                encoding="utf-8",
            )
            journal.chmod(0o600)
            records, malformed = OBS.read_records(journal, 1024)
            self.assertEqual(malformed, 0)
            self.assertEqual(records[0]["event"], "app_boot")

            link = root / "link.jsonl"
            link.symlink_to(journal)
            with self.assertRaises(RuntimeError):
                OBS.read_records(link, 1024)

            journal.chmod(0o640)
            with self.assertRaises(RuntimeError):
                OBS.read_records(journal, 1024)

    def test_atomic_output_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "metrics.prom"
            OBS.atomic_write(output, "metric 1\n")
            self.assertEqual(output.read_text(encoding="utf-8"), "metric 1\n")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
