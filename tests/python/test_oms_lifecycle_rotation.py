from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_oms_checkpoint as checkpoint
import hepta_oms_lifecycle as lifecycle
from verify_oms_journal_replay import JournalError, read_records


def event(kind: str, command: str, request_hash: str, *, status: str = "",
          order_id: int = -1, ts_ms: int = 1000) -> dict:
    return {
        "schema_version": 4, "event": kind, "ts_ms": ts_ms, "order_id": order_id,
        "req_id": command, "client_req_id": command, "trace_id": "session-a",
        "event_id": f"{kind}:{command}:{status}:{ts_ms}", "risk_code": "",
        "venue": "SIM", "strategy": "fixture", "account": "SIM-1",
        "execution_domain": "SIM", "request_hash": request_hash,
        "venue_correlation_id": f"corr-{command}", "broker_callback_type": "",
        "broker_service_epoch": "", "broker_connection_epoch": 0,
        "broker_request_id": 0, "broker_error_code": 0, "broker_message": "",
        "broker_advanced_order_reject_json": "", "broker_why_held": "",
        "broker_execution_id": "", "broker_remaining_quantity": 0.0,
        "broker_market_cap_price": 0.0, "instrument": "EUR.USD", "side": "BUY",
        "qty": 10.0, "price": 1.1, "status": status, "reason": "",
        "source": "agent.tool:agent-a",
    }


def command_events(command: str, base: int, order_id: int) -> list[dict]:
    request_hash = f"hash-{command}"
    return [
        event("order_intent", command, request_hash, ts_ms=base),
        event("place_send_attempt", command, request_hash, ts_ms=base + 1),
        event("place_sent", command, request_hash, status="submitted",
              order_id=order_id, ts_ms=base + 2),
        event("order_owner_reconciled_terminal", f"terminal-{command}",
              f"terminal-hash-{command}", status="terminal",
              order_id=order_id, ts_ms=base + 3),
    ]


def encode(events: list[dict]) -> bytes:
    return b"".join((json.dumps(v, sort_keys=True, separators=(",", ":")) + "\n").encode()
                    for v in events)


class OmsLifecycleRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-oms-lifecycle-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.journal = self.root / "oms.jsonl"
        self.store = Path(str(self.journal) + ".generations")
        self.journal.write_bytes(encode(command_events("old", 1000, 101)))
        os.chmod(self.journal, 0o600)

    def append(self, values: list[dict]) -> None:
        with self.journal.open("ab") as stream:
            stream.write(encode(values))

    def current_index_commands(self) -> list[str]:
        generation = json.loads((self.store / "CURRENT").read_text())["generation"]
        commands = []
        for line in (self.store / generation / "runtime-command-index.tsv").read_text().splitlines():
            fields = line.split("\t")
            commands.append(bytes.fromhex(fields[2]).decode())
        return commands

    def test_seal_rotates_to_lineage_tail_and_legacy_reader_rejects_it(self) -> None:
        result = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(result["schema"], lifecycle.SCHEMA)
        verified = lifecycle.verify_generation(self.store, journal=self.journal)
        self.assertEqual(verified["result"], "PASS")
        raw = self.journal.read_bytes()
        self.assertTrue(raw.startswith((lifecycle.TAIL_HEADER + "\t").encode()))
        with self.assertRaises(JournalError):
            list(read_records(self.journal))
        self.assertEqual(result["history_records"], 4)
        self.assertEqual(result["segment_records"], 4)

    def test_repeated_seal_accumulates_history_without_recopying_parent_segment(self) -> None:
        first = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        first_generation = first["generation"]
        self.append(command_events("new", 2000, 202))
        second = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(second["parent_generation"], first_generation)
        self.assertEqual(second["segment_records"], 4)
        self.assertEqual(second["history_records"], 8)
        self.assertEqual(second["command_records"], first["command_records"] + 2)
        self.assertEqual(set(self.current_index_commands()),
                         {"old", "terminal-old", "new", "terminal-new"})
        self.assertLess(self.journal.stat().st_size, 256)
        lifecycle.verify_generation(self.store, journal=self.journal)

    def test_export_reconstructs_strict_legacy_jsonl_across_generations(self) -> None:
        lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.append(command_events("new", 2000, 202))
        lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.append(command_events("tail", 3000, 303))
        output = self.root / "downgrade.jsonl"
        exported = lifecycle.export_legacy(self.journal, self.store, output)
        self.assertEqual(exported["records"], 12)
        values = list(read_records(output, max_records=64))
        self.assertEqual(len(values), 12)
        self.assertEqual(values[0]["req_id"], "old")
        self.assertEqual(values[-4]["req_id"], "tail")
        self.assertNotIn(lifecycle.TAIL_HEADER.encode(), output.read_bytes())

    def test_current_parent_digest_is_part_of_v2_authority(self) -> None:
        first = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.append(command_events("new", 2000, 202))
        lifecycle.seal_generation(self.journal, self.store, stopped=True)
        parent_manifest = self.store / first["generation"] / "manifest.json"
        with parent_manifest.open("ab") as stream:
            stream.write(b" ")
        with self.assertRaises(checkpoint.GenerationError):
            lifecycle.verify_generation(self.store, journal=self.journal)

    def test_publication_crash_points_never_expose_new_tail_as_clean_legacy_ledger(self) -> None:
        phases = ("generation-durable", "tail-ready", "tail-published",
                  "current-json-durable")
        for phase in phases:
            with self.subTest(phase=phase):
                temp = tempfile.TemporaryDirectory(prefix=f"hepta-phase-{phase}-")
                self.addCleanup(temp.cleanup)
                root = Path(temp.name)
                os.chmod(root, 0o700)
                journal = root / "oms.jsonl"
                store = Path(str(journal) + ".generations")
                journal.write_bytes(encode(command_events("old", 1000, 101)))
                os.chmod(journal, 0o600)

                def crash(at: str) -> None:
                    if at == phase:
                        raise RuntimeError(phase)

                with self.assertRaises(RuntimeError):
                    lifecycle.seal_generation(journal, store, stopped=True,
                                              phase_hook=crash)
                if phase in {"generation-durable", "tail-ready"}:
                    self.assertEqual(len(list(read_records(journal))), 4)
                    self.assertFalse((store / "CURRENT").exists())
                else:
                    with self.assertRaises(JournalError):
                        list(read_records(journal))
                    if phase == "tail-published":
                        self.assertFalse((store / "CURRENT").exists())
                    else:
                        self.assertTrue((store / "CURRENT").exists())
                        self.assertFalse((store / "CURRENT.runtime").exists())
                        with self.assertRaises((OSError, checkpoint.GenerationError)):
                            lifecycle.verify_generation(store, journal=journal)


if __name__ == "__main__":
    unittest.main()
