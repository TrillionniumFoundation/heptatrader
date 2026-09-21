from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_oms_checkpoint as lifecycle


def event(kind: str, command: str, request_hash: str, *, status: str = "",
          order_id: int = -1, risk_code: str = "", correlation: str = "",
          source: str = "agent.tool:agent-a") -> dict:
    return {
        "schema_version": 4,
        "event": kind,
        "ts_ms": 1000,
        "order_id": order_id,
        "req_id": command,
        "client_req_id": command,
        "trace_id": "session-a",
        "event_id": f"{kind}:{command}:{status}",
        "risk_code": risk_code,
        "venue": "SIM",
        "strategy": "fixture",
        "account": "SIM-1",
        "execution_domain": "SIM",
        "request_hash": request_hash,
        "venue_correlation_id": correlation,
        "broker_callback_type": "",
        "broker_service_epoch": "",
        "broker_connection_epoch": 0,
        "broker_request_id": 0,
        "broker_error_code": 0,
        "broker_message": "",
        "broker_advanced_order_reject_json": "",
        "broker_why_held": "",
        "broker_execution_id": "",
        "broker_remaining_quantity": 0.0,
        "broker_market_cap_price": 0.0,
        "instrument": "EUR.USD",
        "side": "BUY",
        "qty": 10.0,
        "price": 1.1,
        "status": status,
        "reason": risk_code,
        "source": source,
    }


class OmsCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-oms-generation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.journal = self.root / "oms.jsonl"
        self.store = self.root / "generations"

    def write_events(self, values: list[dict]) -> None:
        self.journal.write_text("".join(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
                                        for value in values), encoding="utf-8")
        os.chmod(self.journal, 0o600)

    def baseline(self) -> list[dict]:
        return [
            event("order_intent", "old-command", "hash-old", correlation="corr-old"),
            event("place_send_attempt", "old-command", "hash-old", correlation="corr-old"),
            event("place_sent", "old-command", "hash-old", status="submitted",
                  order_id=101, correlation="corr-old"),
            event("order_owner_reconciled_terminal", "owner-terminal", "hash-owner",
                  status="terminal", order_id=101),
            event("order_intent", "uncertain-command", "hash-uncertain", correlation="corr-u"),
            event("place_send_attempt", "uncertain-command", "hash-uncertain", correlation="corr-u"),
        ]

    def test_generation_has_full_key_index_hot_checkpoint_and_exact_lookup(self) -> None:
        self.write_events(self.baseline())
        manifest = lifecycle.build_generation(self.journal, self.store, stopped=True)
        self.assertEqual(manifest["journal_records"], 6)
        verified = lifecycle.verify_generation(self.store)
        self.assertEqual(verified["result"], "PASS")
        # The owner-terminal control ID is not a third execution command.
        self.assertEqual(verified["command_records"], 2)
        self.assertEqual(lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "owner-terminal", "hash-owner")["status"], "missing")
        self.assertEqual(verified["send_attempt_records"], 2)
        duplicate = lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "old-command", "hash-old")
        conflict = lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "old-command", "different-hash")
        missing = lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "never-seen", "hash")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["order_id"], 101)
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(missing["status"], "missing")
        current = json.loads((self.store / "CURRENT").read_text())
        generation = self.store / current["generation"]
        checkpoint = json.loads((generation / "checkpoint.json").read_text())
        self.assertEqual([item["command_id"] for item in checkpoint["hot_commands"]],
                         ["uncertain-command"])
        self.assertFalse(checkpoint["paper_authorized"])
        self.assertFalse(checkpoint["live_authorized"])
        self.assertTrue((generation / "runtime-manifest.txt").is_file())
        self.assertTrue((generation / "runtime-command-index.tsv").is_file())
        self.assertTrue((generation / "send-attempt-index.tsv").is_file())
        self.assertTrue((generation / "hot-replay.jsonl").is_file())
        runtime_current = (self.store / "CURRENT.runtime").read_text()
        self.assertIn("HEPTA_OMS_RUNTIME_CURRENT_V1\n", runtime_current)
        self.assertIn(f"generation={current['generation']}\n", runtime_current)
        hot = [json.loads(line) for line in (generation / "hot-replay.jsonl").read_text().splitlines()]
        self.assertTrue(hot)
        self.assertTrue(all(value["req_id"] == "uncertain-command" for value in hot))

    def test_production_terminal_control_ids_are_not_execution_commands(self) -> None:
        import hepta_oms_lifecycle as rotation
        self.store = Path(str(self.journal) + ".generations")

        def commands(manifest):
            rows = (self.store / manifest["generation"] / "runtime-command-index.tsv").read_bytes().splitlines(True)
            return {record["command_id"]: record for _, record, _ in
                    (rotation._runtime_row(row) for row in rows)}

        def hot(manifest):
            return rotation._read_hot(self.store / manifest["generation"],
                                      1024 * 1024, 1024, 262144)

        def append(values):
            with self.journal.open("a") as stream:
                for value in values:
                    stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")

        # Real RecordOrderTerminalDurably emits an independent terminal ID.
        values = [event("order_intent", "placed", "hash-place"),
                  event("place_send_attempt", "placed", "hash-place"),
                  event("place_sent", "placed", "hash-place", status="submitted", order_id=101),
                  event("order_owner_reconciled_terminal", "order-terminal-101", "",
                        status="terminal", order_id=101)]
        self.write_events(values)
        first = rotation.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(first["command_records"], 1)
        self.assertEqual(set(commands(first)), {"placed"})
        self.assertFalse(any(value["event"] == "place_sent" for value in hot(first)))
        controls = [event("session_owner_fenced", "owner-fence", ""),
                    event("session_owner_recovery_only", "owner-recovery", "", status="17"),
                    event("execution_projection_resolved", "projection-resolution", "")]
        append(controls)
        second = rotation.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(second["command_records"], 1)
        self.assertEqual(set(commands(second)), {"placed"})
        self.assertEqual({value["event"] for value in hot(second)},
                         {"session_owner_fenced", "session_owner_recovery_only"})
        # No dropped ledger bytes or weakened verification when control IDs
        # are correctly excluded from the mutation-command index.
        output = self.root / "control-export.jsonl"
        rotation.export_legacy(self.journal, self.store, output)
        expected = "".join(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
                           for value in values + controls).encode()
        self.assertEqual(output.read_bytes(), expected)
        rotation.verify_generation(self.store, journal=self.journal)
        append([event("session_owner_fence_release", "owner-release", "")])
        third = rotation.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(third["command_records"], 1)
        self.assertEqual({value["event"] for value in hot(third)}, {"session_owner_recovery_only"})
        rebased = rotation.rebase_generation(self.journal, self.store, stopped=True,
                                             prune_ancestors=False)
        self.assertEqual(set(commands(rebased)), {"placed"})
        rotation.verify_generation(self.store, journal=self.journal)

    def test_rejected_command_without_prior_intent_remains_indexed(self) -> None:
        import hepta_oms_lifecycle as rotation
        self.store = Path(str(self.journal) + ".generations")

        # Match native recovery: pre-intent reject defaults to place and must
        # retain its command identity, unlike owner/fence control records.
        self.write_events([event("reject", "reject-before-intent", "hash-reject", status="rejected"),
                           event("flatten_reject", "flatten-rejected", "hash-flat", status="rejected")])
        sealed = rotation.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(sealed["command_records"], 2)
        rows = (self.store / sealed["generation"] / "runtime-command-index.tsv").read_bytes().splitlines(True)
        records = {record["command_id"]: record for _, record, _ in
                   (rotation._runtime_row(row) for row in rows)}
        self.assertEqual(records["reject-before-intent"]["operation"], "place")
        self.assertEqual(records["flatten-rejected"]["operation"], "flatten")
        self.assertTrue(all(record["status"] == "rejected" for record in records.values()))
        self.assertTrue(all(not record["durable_mutation_intent"] for record in records.values()))
        rotation.verify_generation(self.store, journal=self.journal)

    def test_simulator_status_receipts_preserve_state_but_not_command_identity(self) -> None:
        import hepta_oms_lifecycle as rotation
        self.store = Path(str(self.journal) + ".generations")
        values = [
            event("order_intent", "placed", "hash-place", correlation="corr-place"),
            event("place_send_attempt", "placed", "hash-place", correlation="corr-place"),
            event("place_sent", "placed", "hash-place", order_id=1000000, status="activation_pending"),
            event("place_activated", "placed", "hash-place", order_id=1000000, status="submitted"),
            event("status", "sim-status-1000000-Submitted", "", order_id=1000000,
                  status="Submitted", source="agent:agent-a"),
            event("status", "sim-status-1000000-Filled", "", order_id=1000000,
                  status="Filled", source="agent:agent-a"),
            event("order_owner_reconciled_terminal", "order-terminal-1000000", "",
                  status="terminal", order_id=1000000),
        ]
        for value in values:
            value["venue"], value["account"] = "SIMULATOR", "SIM"
        values[4]["qty"] = 0.0
        self.write_events(values)
        expected_ledger = self.journal.read_bytes()
        first = rotation.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(first["command_records"], 1)
        generation = self.store / first["generation"]
        rows = (generation / "runtime-command-index.tsv").read_bytes().splitlines(True)
        self.assertEqual([rotation._runtime_row(row)[1]["command_id"] for row in rows], ["placed"])
        hot = rotation._read_hot(generation, 1024 * 1024, 1024, 262144)
        state = rotation._simulator_checkpoint_from_hot(hot)
        self.assertEqual(state["positions"], {"EUR.USD": 10.0})
        self.assertEqual(state["admitted_orders"], 1)
        self.assertEqual(state["max_order_id"], 1000000)
        # Re-reading the parent must not encounter empty-operation status rows.
        second = rotation.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(second["command_records"], 1)
        output = self.root / "simulator-export.jsonl"
        rotation.export_legacy(self.journal, self.store, output)
        self.assertEqual(output.read_bytes(), expected_ledger)
        rotation.verify_generation(self.store, journal=self.journal)

    def test_pending_activation_stays_uncertain_until_explicit_activation(self) -> None:
        import hepta_oms_lifecycle as rotation
        self.store = Path(str(self.journal) + ".generations")
        values = [event("order_intent", "pending", "hash-pending"),
                  event("place_send_attempt", "pending", "hash-pending"),
                  event("place_sent", "pending", "hash-pending", order_id=101,
                        status="activation_pending")]
        self.write_events(values)
        manifest = lifecycle.build_generation(self.journal, self.store, stopped=True)
        generation = self.store / manifest["generation"]
        checkpoint = json.loads((generation / "checkpoint.json").read_text())
        self.assertEqual([record["status"] for record in checkpoint["hot_commands"]], ["uncertain"])
        self.assertEqual(lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "pending", "hash-pending")["command_status"], "uncertain")
        with self.journal.open("a") as stream:
            stream.write(json.dumps(event("place_activated", "pending", "hash-pending",
                                          order_id=101, status="submitted"),
                                    sort_keys=True, separators=(",", ":")) + "\n")
        sealed = rotation.seal_generation(self.journal, self.store, stopped=True)
        rows = (self.store / sealed["generation"] / "runtime-command-index.tsv").read_bytes().splitlines(True)
        self.assertEqual([rotation._runtime_row(row)[1]["status"] for row in rows], ["accepted"])
        rotation.verify_generation(self.store, journal=self.journal)

    def test_current_and_historical_agent_source_namespaces_are_indexed(self) -> None:
        values = [
            event("order_intent", "current", "hash-current"),
            event("reject", "current", "hash-current", status="rejected", risk_code="RISK"),
            event("order_intent", "legacy", "hash-legacy", source="agent:legacy-agent"),
            event("reject", "legacy", "hash-legacy", status="rejected", risk_code="RISK",
                  source="agent:legacy-agent"),
        ]
        self.write_events(values)
        lifecycle.build_generation(self.journal, self.store, stopped=True)
        self.assertEqual(lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "current", "hash-current")["status"], "duplicate")
        self.assertEqual(lifecycle.lookup_command(
            self.store, "legacy-agent", "session-a", "legacy", "hash-legacy")["status"], "duplicate")

    def test_generation_parent_chain_advances_without_deleting_old_identity(self) -> None:
        self.write_events(self.baseline())
        first = lifecycle.build_generation(self.journal, self.store, stopped=True)
        first_dir = self.store / first["generation"]
        more = self.baseline() + [
            event("execution_command_resolved", "uncertain-command", "hash-uncertain",
                  status="rejected", risk_code="AUTHORITATIVE_CORRELATION_NOT_FOUND"),
            event("order_intent", "new-command", "hash-new"),
            event("reject", "new-command", "hash-new", status="rejected",
                  risk_code="RISK_LIMIT"),
        ]
        self.write_events(more)
        second = lifecycle.build_generation(self.journal, self.store, stopped=True)
        self.assertEqual(second["parent_generation"], first["generation"])
        self.assertTrue(first_dir.is_dir())
        self.assertEqual(lifecycle.verify_generation(self.store)["generation"], second["generation"])
        self.assertEqual(lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "old-command", "hash-old")["status"], "duplicate")
        self.assertEqual(lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "new-command", "hash-new")["command_status"], "rejected")

    def test_interrupted_new_generation_never_replaces_current(self) -> None:
        self.write_events(self.baseline())
        first = lifecycle.build_generation(self.journal, self.store, stopped=True)
        more = self.baseline() + [event("order_intent", "later", "hash-later")]
        self.write_events(more)
        def fail(phase: str) -> None:
            if phase == "generation-durable":
                raise RuntimeError("fixture crash before CURRENT")
        with self.assertRaisesRegex(RuntimeError, "fixture crash"):
            lifecycle.build_generation(self.journal, self.store, stopped=True, phase_hook=fail)
        self.assertEqual(lifecycle.verify_generation(self.store)["generation"], first["generation"])
        self.assertEqual(lifecycle.lookup_command(
            self.store, "agent-a", "session-a", "later", "hash-later")["status"], "missing")

    def test_crash_after_json_current_before_runtime_pointer_fails_closed(self) -> None:
        self.write_events(self.baseline())
        lifecycle.build_generation(self.journal, self.store, stopped=True)
        self.write_events(self.baseline() + [event("order_intent", "later", "hash-later")])
        def fail(phase: str) -> None:
            if phase == "current-json-durable":
                raise RuntimeError("fixture crash between pointers")
        with self.assertRaisesRegex(RuntimeError, "fixture crash"):
            lifecycle.build_generation(self.journal, self.store, stopped=True, phase_hook=fail)
        with self.assertRaisesRegex(lifecycle.GenerationError, "RUNTIME_CURRENT_MISMATCH"):
            lifecycle.verify_generation(self.store)

    def test_corrupt_current_generation_fails_closed_without_parent_fallback(self) -> None:
        self.write_events(self.baseline())
        first = lifecycle.build_generation(self.journal, self.store, stopped=True)
        self.write_events(self.baseline() + [event("order_intent", "later", "hash-later")])
        second = lifecycle.build_generation(self.journal, self.store, stopped=True)
        index = self.store / second["generation"] / "runtime-command-index.tsv"
        with index.open("ab") as stream:
            stream.write(b"corrupt\n")
        with self.assertRaisesRegex(lifecycle.GenerationError, "DIGEST_MISMATCH"):
            lifecycle.verify_generation(self.store)
        self.assertTrue((self.store / first["generation"]).is_dir())
        with self.assertRaises(lifecycle.GenerationError):
            lifecycle.lookup_command(self.store, "agent-a", "session-a", "old-command", "hash-old")

    def test_conflicting_hash_inside_source_journal_is_rejected(self) -> None:
        values = [
            event("order_intent", "same", "hash-a"),
            event("place_send_attempt", "same", "hash-b"),
        ]
        self.write_events(values)
        with self.assertRaisesRegex(lifecycle.GenerationError, "COMMAND_HASH_CONFLICT"):
            lifecycle.build_generation(self.journal, self.store, stopped=True)
        self.assertFalse((self.store / "CURRENT").exists())

    def test_mutating_generation_requires_explicit_stopped_state(self) -> None:
        self.write_events(self.baseline())
        with self.assertRaisesRegex(lifecycle.GenerationError, "STOP_ALL_WRITERS_REQUIRED"):
            lifecycle.build_generation(self.journal, self.store, stopped=False)

    def test_runtime_generation_rejects_gzip_source_until_expanded(self) -> None:
        import gzip
        raw = "".join(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
                      for value in self.baseline()).encode()
        self.journal.write_bytes(gzip.compress(raw, mtime=0))
        os.chmod(self.journal, 0o600)
        with self.assertRaisesRegex(lifecycle.GenerationError, "REQUIRES_EXPANDED_JOURNAL"):
            lifecycle.build_generation(self.journal, self.store, stopped=True)


if __name__ == "__main__":
    unittest.main()
