"""One-refresh attribution snapshots preserve scope, witnesses and failure behavior."""
import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "research/neuron_learning"))
import hepta_decision_attribution as attribution
from laya_cell import resident_rewards as rewards


class AttributionDirectoryCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / "journals"
        self.directory.mkdir()
        self.period = "a" * 64
        self.registrations = [self.registration(i) for i in range(3)]

    def private(self, path, raw):
        path.write_bytes(raw)
        path.chmod(0o600)

    def registration(self, index, directory=None):
        directory = directory or self.directory
        directory.mkdir(exist_ok=True)
        manifest = {"schema": "heptatrader.attribution-producer.v1",
                    "instrument": "EUR.USD", "period_sha256": self.period,
                    "execution_domain": "PAPER", "journal_directory": str(directory),
                    "fixture_identity": index}
        raw = attribution.canonical(manifest).encode()
        path = self.root / ("manifest-%s.json" % index)
        self.private(path, raw)
        registration = {"manifest": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
        rid = "request-%s" % index
        decision = {"action": "BUY"}
        request = {"request_id": rid, "instrument": "EUR.USD", "period_sha256": self.period,
                   "model_id": "model-%s" % index, "input_scope": "FIXTURE",
                   "prompt": "fixture", "prompt_sha256": hashlib.sha256(b"fixture").hexdigest(),
                   "started_at_ms": 10}
        response = {"request_id": rid, "request_sha256": attribution.digest(request),
                    "returncode": 0, "available_at_ms": 20,
                    "stdout": json.dumps({"ok": True, "outputs": [{"text": json.dumps(decision)}]})}
        selected = {"request_id": rid, "request_sha256": attribution.digest(request),
                    "response_sha256": attribution.digest(response), "decision": decision,
                    "decision_sha256": attribution.digest(decision), "available_at_ms": 30}
        binding = {"request_id": rid, "request_sha256": attribution.digest(request),
                   "response_sha256": attribution.digest(response), "decision_sha256": attribution.digest(decision),
                   "state_write_succeeded": True,
                   "basis": "IDENTICAL_RETURNED_OBJECT_AT_ORIGINAL_DURABLE_PREPARED",
                   "command": {"command_id": "command-%s" % index, "created_at_ms": 40,
                               "instrument": "EUR.USD", "kind": "OPEN", "side": "BUY"}}
        header = {"manifest_sha256": registration["sha256"], "instrument": "EUR.USD",
                  "period_sha256": self.period, "execution_domain": "PAPER",
                  "source_kind": "PINNED_DRIVER_CALLSITE", "trading_authority": False}
        values = [("HEADER", header), ("REQUEST", request), ("RESPONSE", response),
                  ("DECISION", selected), ("COMMAND_BINDING", binding)]
        self.write_chain(directory, "producer-%s" % index, values)
        return registration

    def write_chain(self, directory, producer, values):
        previous = "0" * 64
        lines = []
        for sequence, (kind, data) in enumerate(values):
            record = {"schema": attribution.SCHEMA, "producer_id": producer, "sequence": sequence,
                      "previous": previous, "captured_at_ms": int(time.time() * 1000) - 1000,
                      "dropped_total": 0, "kind": kind, "data": data}
            record["sha256"] = attribution.digest(record)
            previous = record["sha256"]
            lines.append(attribution.canonical(record).encode() + b"\n")
        self.private(directory / (producer + ".jsonl"), b"".join(lines))

    def config(self, registrations=None):
        return {"period_sha256": self.period,
                "decision_attribution_registrations": {"EUR.USD": registrations or self.registrations}}

    def test_shared_directory_exact_binding_and_witness_parity(self):
        expected = [attribution.verified_bindings(r, "EUR.USD", self.period) for r in self.registrations]
        cache = {}
        with mock.patch.object(attribution, "_verified_producer_graphs", wraps=attribution._verified_producer_graphs) as verify:
            actual = [attribution.verified_bindings(r, "EUR.USD", self.period, directory_cache=cache)
                      for r in self.registrations]
        self.assertEqual(actual, expected)
        self.assertEqual(verify.call_count, 1)
        self.assertEqual([list(x[0]) for x in actual], [["command-0"], ["command-1"], ["command-2"]])
        self.assertTrue(all(x[0][next(iter(x[0]))]["source_prefixes"] for x in actual))

    def test_source_bindings_reuses_each_directory_once(self):
        other = self.registration(3, self.root / "other")
        with mock.patch.object(attribution, "_verified_producer_graphs", wraps=attribution._verified_producer_graphs) as verify:
            bindings, issues = rewards.source_bindings(self.config(self.registrations + [other]), "EUR.USD", 100)
        self.assertEqual(set(bindings), {"command-0", "command-1", "command-2", "command-3"})
        self.assertEqual(issues, [])
        self.assertEqual(verify.call_count, 2)

    def test_new_refresh_revalidates_health(self):
        first, issues = rewards.source_bindings(self.config(), "EUR.USD", 100)
        self.assertEqual(len(first), 3)
        self.private(self.directory / "producer-1.health.json", b'{"dropped":1}')
        second, issues = rewards.source_bindings(self.config(), "EUR.USD", 100)
        self.assertEqual(set(second), {"command-0", "command-2"})
        self.assertIn({"producer_id": "producer-1", "reason": "DROPPED_OR_FAILED_PRODUCER_NO_CREDIT"}, issues)

    def test_cached_directory_does_not_skip_manifest_pin(self):
        cache = {}
        attribution.verified_bindings(self.registrations[0], "EUR.USD", self.period, directory_cache=cache)
        path = Path(self.registrations[1]["manifest"])
        self.private(path, path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "PRODUCER_MANIFEST_CHANGED"):
            attribution.verified_bindings(self.registrations[1], "EUR.USD", self.period, directory_cache=cache)

    def test_unregistered_malformed_command_graph_keeps_scope(self):
        path = self.directory / "producer-2.jsonl"
        records = [json.loads(line) for line in path.read_bytes().splitlines()]
        values = [("HEADER", records[0]["data"]), ("COMMAND_BINDING", records[-1]["data"])]
        self.write_chain(self.directory, "producer-2", values)
        bindings, issues = attribution.verified_bindings(self.registrations[0], "EUR.USD", self.period)
        self.assertEqual(set(bindings), {"command-0"})
        self.assertEqual(issues, [])
        bindings, issues = attribution.verified_bindings(self.registrations[2], "EUR.USD", self.period)
        self.assertEqual(bindings, {})
        self.assertEqual(issues[0]["reason"], "INCOMPLETE_INPUT_RESPONSE_GRAPH")

    def test_unregistered_chain_corruption_still_rejects_directory(self):
        path = self.directory / "producer-2.jsonl"
        records = [json.loads(line) for line in path.read_bytes().splitlines()]
        records[-1]["previous"] = "f" * 64
        records[-1]["sha256"] = attribution.digest({k: v for k, v in records[-1].items() if k != "sha256"})
        self.private(path, b"".join(attribution.canonical(r).encode() + b"\n" for r in records))
        with self.assertRaisesRegex(ValueError, "ATTRIBUTION_CHAIN_GAP"):
            attribution.verified_bindings(self.registrations[0], "EUR.USD", self.period)


if __name__ == "__main__":
    unittest.main()
