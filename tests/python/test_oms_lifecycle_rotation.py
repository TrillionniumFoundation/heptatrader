from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import time
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_oms_checkpoint as checkpoint
import hepta_oms_lifecycle as lifecycle
from verify_oms_journal_replay import JournalError, read_records


def event(kind: str, command: str, request_hash: str, *, status: str = "",
          order_id: int = -1, ts_ms: int = 1000,
          agent_id: str = "agent-a", session_id: str = "session-a") -> dict:
    return {
        "schema_version": 4, "event": kind, "ts_ms": ts_ms, "order_id": order_id,
        "req_id": command, "client_req_id": command, "trace_id": session_id,
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
        "source": f"agent.tool:{agent_id}",
    }


def command_events(command: str, base: int, order_id: int, *,
                   agent_id: str = "agent-a",
                   session_id: str = "session-a") -> list[dict]:
    request_hash = f"hash-{command}"
    return [
        event("order_intent", command, request_hash, ts_ms=base,
              agent_id=agent_id, session_id=session_id),
        event("place_send_attempt", command, request_hash, ts_ms=base + 1,
              agent_id=agent_id, session_id=session_id),
        event("place_sent", command, request_hash, status="submitted",
              order_id=order_id, ts_ms=base + 2,
              agent_id=agent_id, session_id=session_id),
        event("order_owner_reconciled_terminal", command, request_hash,
              status="terminal", order_id=order_id, ts_ms=base + 3,
              agent_id=agent_id, session_id=session_id),
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

    def current_send_keys(self) -> list[tuple[str, str, int, int, str, str, str]]:
        generation = json.loads((self.store / "CURRENT").read_text())["generation"]
        rows = []
        for line in (self.store / generation / "send-attempt-index.tsv").read_text().splitlines():
            fields = line.split("\t")
            rows.append((fields[0], fields[1], int(fields[2]), int(fields[6]),
                         fields[3], fields[4], fields[5]))
        return rows

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
        self.assertEqual(second["command_records"], first["command_records"] + 1)
        self.assertEqual(set(self.current_index_commands()), {"old", "new"})
        self.assertLess(self.journal.stat().st_size, 256)
        lifecycle.verify_generation(self.store, journal=self.journal)

    def test_many_generations_keep_active_tail_bounded_and_old_identity_indexed(self) -> None:
        generations = 12
        manifests = []
        for index in range(generations):
            if index:
                self.append(command_events(f"g{index}", 1000 + index * 100, 100 + index))
            manifest = lifecycle.seal_generation(self.journal, self.store, stopped=True)
            manifests.append(manifest)
            self.assertEqual(manifest["segment_records"], 4)
            self.assertEqual(manifest["history_records"], 4 * (index + 1))
            self.assertEqual(manifest["command_records"], index + 1)
            self.assertLess(self.journal.stat().st_size, 256)
            lifecycle.verify_generation(self.store, journal=self.journal)
        commands = set(self.current_index_commands())
        self.assertIn("old", commands)
        self.assertIn(f"g{generations - 1}", commands)
        for manifest in manifests:
            segment = self.store / manifest["generation"] / "segment-000001.jsonl"
            self.assertLess(segment.stat().st_size, 16 * 1024)
        output = self.root / "many-generations.jsonl"
        exported = lifecycle.export_legacy(self.journal, self.store, output)
        self.assertEqual(exported["records"], 4 * generations)
        self.assertEqual(len(list(read_records(output, max_records=128))), 4 * generations)

    def test_simulator_checkpoint_carries_position_count_and_watermark_across_seals(self) -> None:
        def sim(kind: str, order_id: int, side: str, qty: float, *, status: str = "", price: float = 1.1, ts: int = 1000) -> dict:
            value = event(kind, f"sim-{order_id}", f"hash-{order_id}", status=status,
                          order_id=order_id, ts_ms=ts)
            value.update(venue="SIMULATOR", account="SIM", execution_domain="SIM:fixture",
                         side=side, qty=qty, price=price, instrument="EUR.USD")
            return value

        self.journal.write_bytes(encode([
            sim("place_sent", 1000000, "BUY", 10.0, status="submitted", ts=1000),
            sim("status", 1000000, "BUY", 10.0, status="Filled", ts=1001),
        ]))
        os.chmod(self.journal, 0o600)
        first = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        first_hot = lifecycle._read_hot(self.store / first["generation"], 1024 * 1024, 1024, 262144)
        state = lifecycle._simulator_checkpoint_from_hot(first_hot)
        self.assertEqual(state["max_order_id"], 1000000)
        self.assertEqual(state["admitted_orders"], 1)
        self.assertEqual(state["positions"], {"EUR.USD": 10.0})

        self.append([
            sim("place_sent", 1000001, "SELL", 4.0, status="submitted", ts=2000),
            sim("status", 1000001, "SELL", 4.0, status="Filled", ts=2001),
        ])
        second = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        second_hot = lifecycle._read_hot(self.store / second["generation"], 1024 * 1024, 1024, 262144)
        state = lifecycle._simulator_checkpoint_from_hot(second_hot)
        self.assertEqual(state["max_order_id"], 1000001)
        self.assertEqual(state["admitted_orders"], 2)
        self.assertEqual(state["positions"], {"EUR.USD": 6.0})
        lifecycle.verify_generation(self.store, journal=self.journal)

    def test_rebase_prunes_bound_ancestors_and_preserves_identity_and_export(self) -> None:
        generations = 8
        expected = {"old"}
        for index in range(generations):
            if index:
                command = f"rebase-{index}"
                expected.add(command)
                self.append(command_events(command, 2000 + index * 100, 300 + index))
            lifecycle.seal_generation(self.journal, self.store, stopped=True)

        before_current = json.loads((self.store / "CURRENT").read_text())["generation"]
        before_chain = lifecycle._generation_chain(self.store, before_current)
        before_bytes = sum(
            path.stat().st_size
            for generation, _ in before_chain
            for path in (self.store / generation).iterdir()
            if path.is_file()
        )
        self.assertEqual(set(self.current_index_commands()), expected)

        rebased = lifecycle.rebase_generation(
            self.journal, self.store, stopped=True, prune_ancestors=True)
        self.assertEqual(rebased["result"], "PASS")
        self.assertEqual(rebased["parent_generation"], "")
        self.assertGreaterEqual(rebased["pruned_generations"], generations)
        current = json.loads((self.store / "CURRENT").read_text())["generation"]
        self.assertEqual(current, rebased["generation"])
        self.assertEqual(len(lifecycle._generation_chain(self.store, current)), 1)
        self.assertEqual(set(self.current_index_commands()), expected)
        for generation, _ in before_chain:
            self.assertFalse((self.store / generation).exists())

        after_bytes = sum(
            path.stat().st_size
            for path in (self.store / current).iterdir()
            if path.is_file()
        )
        self.assertLess(after_bytes, before_bytes)

        output = self.root / "rebased-export.jsonl"
        exported = lifecycle.export_legacy(self.journal, self.store, output)
        self.assertEqual(exported["records"], 4 * generations)
        self.assertEqual(
            len(list(read_records(output, max_records=4 * generations))),
            4 * generations)

    def test_generation_cost_curve_observes_seal_verify_storage_and_rebase(self) -> None:
        # Exercise enough history to expose cumulative-index/storage behavior
        # without turning the ordinary core lane into a host benchmark.
        commands_per_generation = 64
        generations = 16
        checkpoints = {4, 8, 16}
        expected_commands: set[str] = set()
        points = []

        def batch(generation_index: int) -> bytes:
            events = []
            for item in range(commands_per_generation):
                command = f"curve-{generation_index:02d}-{item:03d}"
                expected_commands.add(command)
                ordinal = (generation_index - 1) * commands_per_generation + item
                owner_index = item % generation_index
                events.extend(command_events(
                    command,
                    10000 + ordinal * 10,
                    1000 + ordinal,
                    agent_id=f"curve-agent-{owner_index:02d}",
                    session_id=f"curve-session-{owner_index:02d}"))
            return encode(events)

        self.journal.write_bytes(batch(1))
        os.chmod(self.journal, 0o600)

        for generation_index in range(1, generations + 1):
            if generation_index > 1:
                with self.journal.open("ab") as stream:
                    stream.write(batch(generation_index))
            active_bytes_before_seal = self.journal.stat().st_size
            seal_started = time.monotonic_ns()
            manifest = lifecycle.seal_generation(
                self.journal, self.store, stopped=True)
            seal_ns = time.monotonic_ns() - seal_started

            verify_started = time.monotonic_ns()
            verified = lifecycle.verify_generation(
                self.store, journal=self.journal)
            verify_ns = time.monotonic_ns() - verify_started
            self.assertEqual(verified["result"], "PASS")

            if generation_index in checkpoints:
                current = json.loads(
                    (self.store / "CURRENT").read_text())["generation"]
                chain = lifecycle._generation_chain(self.store, current)
                generation_dir = self.store / current
                generation_output_bytes = sum(
                    path.stat().st_size
                    for path in generation_dir.iterdir()
                    if path.is_file())
                command_index_bytes = (
                    generation_dir / "runtime-command-index.tsv").stat().st_size
                send_index_bytes = (
                    generation_dir / "send-attempt-index.tsv").stat().st_size
                logical_event_bytes = sum(
                    (self.store / item_generation / "segment-000001.jsonl").stat().st_size
                    for item_generation, _ in chain)
                disk_bytes = sum(
                    path.stat().st_size
                    for item_generation, _ in chain
                    for path in (self.store / item_generation).iterdir()
                    if path.is_file()
                )
                disk_bytes += sum(
                    path.stat().st_size
                    for path in (self.store / name for name in
                                 ("CURRENT", "CURRENT.runtime"))
                    if path.exists()
                )
                points.append({
                    "generation_count": generation_index,
                    "owner_count": generation_index,
                    "history_records": manifest["history_records"],
                    "command_records": manifest["command_records"],
                    "seal_ns": seal_ns,
                    "verify_ns": verify_ns,
                    "active_bytes_before_seal": active_bytes_before_seal,
                    "generation_output_bytes": generation_output_bytes,
                    "runtime_command_index_bytes": command_index_bytes,
                    "send_attempt_index_bytes": send_index_bytes,
                    "logical_event_bytes": logical_event_bytes,
                    "retained_disk_bytes": disk_bytes,
                    "retained_to_logical_numerator": disk_bytes,
                    "retained_to_logical_denominator": logical_event_bytes,
                })

        self.assertEqual(
            [point["generation_count"] for point in points], [4, 8, 16])
        self.assertEqual(
            [point["owner_count"] for point in points], [4, 8, 16])
        self.assertTrue(all(point["seal_ns"] > 0 for point in points))
        self.assertTrue(all(point["verify_ns"] > 0 for point in points))
        self.assertTrue(all(point["retained_disk_bytes"] > 0 for point in points))
        self.assertEqual(
            [point["command_records"] for point in points], [256, 512, 1024])
        self.assertEqual(
            [point["history_records"] for point in points], [1024, 2048, 4096])
        self.assertTrue(all(
            point["runtime_command_index_bytes"] > 0 and
            point["send_attempt_index_bytes"] > 0 and
            point["logical_event_bytes"] > 0
            for point in points))
        self.assertLess(
            points[0]["retained_disk_bytes"],
            points[-1]["retained_disk_bytes"])

        before_rebase = points[-1]["retained_disk_bytes"]
        rebase_started = time.monotonic_ns()
        rebased = lifecycle.rebase_generation(
            self.journal, self.store, stopped=True, prune_ancestors=True)
        rebase_ns = time.monotonic_ns() - rebase_started
        current = json.loads(
            (self.store / "CURRENT").read_text())["generation"]
        chain = lifecycle._generation_chain(self.store, current)
        self.assertEqual(len(chain), 1)
        after_rebase = sum(
            path.stat().st_size
            for path in (self.store / current).iterdir()
            if path.is_file()
        ) + sum(
            path.stat().st_size
            for path in (self.store / name for name in
                         ("CURRENT", "CURRENT.runtime"))
            if path.exists()
        )
        self.assertGreater(rebase_ns, 0)
        self.assertLess(after_rebase, before_rebase)
        self.assertEqual(rebased["history_records"], 4096)
        self.assertEqual(set(self.current_index_commands()), expected_commands)

        usage = resource.getrusage(resource.RUSAGE_SELF)
        observation = {
            "schema": "heptatrader.synthetic-generation-cost-curve.v2",
            "synthetic": True,
            "broker_io": False,
            "commands_per_generation": commands_per_generation,
            "generation_count": generations,
            "maximum_owner_count": generations,
            "points": points,
            "rebase_ns": rebase_ns,
            "retained_disk_bytes_before_rebase": before_rebase,
            "retained_disk_bytes_after_rebase": after_rebase,
            "process_peak_rss_kib": usage.ru_maxrss,
            "authorization_effect": "NONE",
        }
        print(json.dumps(observation, sort_keys=True))

    def test_rebase_crash_points_preserve_old_authority_or_publish_new_authority(self) -> None:
        phases = (
            "rebase-generation-durable",
            "rebase-tail-published",
            "rebase-current-durable",
            "rebase-ancestors-pruned",
        )
        for phase in phases:
            with self.subTest(phase=phase):
                temp = tempfile.TemporaryDirectory(prefix=f"hepta-rebase-{phase}-")
                self.addCleanup(temp.cleanup)
                root = Path(temp.name)
                os.chmod(root, 0o700)
                journal = root / "oms.jsonl"
                store = Path(str(journal) + ".generations")
                journal.write_bytes(encode(command_events("old", 1000, 101)))
                os.chmod(journal, 0o600)
                lifecycle.seal_generation(journal, store, stopped=True)
                with journal.open("ab") as stream:
                    stream.write(encode(command_events("new", 2000, 202)))
                lifecycle.seal_generation(journal, store, stopped=True)

                def crash(at: str) -> None:
                    if at == phase:
                        raise RuntimeError(phase)

                with self.assertRaisesRegex(RuntimeError, phase):
                    lifecycle.rebase_generation(
                        journal, store, stopped=True, prune_ancestors=True,
                        phase_hook=crash)

                selected = json.loads(
                    (store / "CURRENT").read_text())["generation"]
                command_rows = (
                    store / selected / "runtime-command-index.tsv"
                ).read_text().splitlines()
                commands = {
                    bytes.fromhex(row.split("\t")[2]).decode()
                    for row in command_rows
                }
                self.assertEqual(commands, {"old", "new"})

                if phase == "rebase-tail-published":
                    with self.assertRaises(checkpoint.GenerationError):
                        lifecycle.verify_generation(store, journal=journal)
                    self.assertTrue(any(
                        path.name.startswith("g-")
                        for path in store.iterdir() if path.is_dir()))
                    continue

                verified = lifecycle.verify_generation(
                    store, journal=journal)
                self.assertEqual(verified["result"], "PASS")
                output = root / f"{phase}.jsonl"
                exported = lifecycle.export_legacy(
                    journal, store, output)
                self.assertEqual(exported["records"], 8)
                self.assertEqual(
                    len(list(read_records(output, max_records=8))), 8)

                if phase == "rebase-ancestors-pruned":
                    self.assertTrue(selected.startswith("r-"))
                    self.assertFalse(any(
                        path.name.startswith("g-")
                        for path in store.iterdir() if path.is_dir()))
                else:
                    self.assertTrue(any(
                        path.name.startswith("g-")
                        for path in store.iterdir() if path.is_dir()))

    def test_verifier_streams_cumulative_indexes_instead_of_materializing_them(self) -> None:
        lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.append(command_events("new", 2000, 202))
        lifecycle.seal_generation(self.journal, self.store, stopped=True)
        original = lifecycle._iter_private_lines

        class NoMaterialize:
            def __init__(self, source):
                self.source = iter(source)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self.source)

            def __length_hint__(self):
                raise AssertionError("cumulative index was materialized")

        lifecycle._iter_private_lines = lambda path: NoMaterialize(original(path))
        try:
            verified = lifecycle.verify_generation(self.store, journal=self.journal)
            self.assertEqual(verified["result"], "PASS")
        finally:
            lifecycle._iter_private_lines = original

    def test_generation_chain_has_no_fixed_1024_export_ceiling_and_cycles_fail_closed(self) -> None:
        store = self.root / "long-chain"
        store.mkdir(mode=0o700)
        parent = ""
        count = 1025
        for index in range(count):
            generation = f"g-{index:04d}"
            directory = store / generation
            directory.mkdir(mode=0o700)
            manifest = directory / "manifest.json"
            manifest.write_text(json.dumps({
                "generation": generation,
                "parent_generation": parent,
            }, sort_keys=True, separators=(",", ":")) + "\n")
            os.chmod(manifest, 0o600)
            parent = generation
        chain = lifecycle._generation_chain(store, parent)
        self.assertEqual(len(chain), count)
        self.assertEqual(chain[0][0], "g-0000")
        self.assertEqual(chain[-1][0], parent)

        first_manifest = store / "g-0000" / "manifest.json"
        first_manifest.write_text(json.dumps({
            "generation": "g-0000",
            "parent_generation": parent,
        }, sort_keys=True, separators=(",", ":")) + "\n")
        os.chmod(first_manifest, 0o600)
        with self.assertRaisesRegex(checkpoint.GenerationError,
                                    "OMS_GENERATION_PARENT_CHAIN_INVALID"):
            lifecycle._generation_chain(store, parent)

    def test_send_attempt_index_remains_window_sorted_across_generations(self) -> None:
        first = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(first["send_attempt_records"], 1)
        values = command_events("second", 900, 202)
        for item in values:
            item["account"] = "AAA"
            item["execution_domain"] = "PAPER"
        self.append(values)
        second = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(second["send_attempt_records"], 2)
        rows = self.current_send_keys()
        self.assertEqual(rows, sorted(rows))
        generation = second["generation"]
        runtime = (self.store / generation / "runtime-manifest.txt").read_text()
        self.assertIn(f"send_attempt_index_order={lifecycle.SEND_INDEX_ORDER}\n", runtime)
        lifecycle.verify_generation(self.store, journal=self.journal)

    def test_v1_send_index_is_migrated_to_sorted_v2_without_full_memory_load(self) -> None:
        first = checkpoint.build_generation(self.journal, self.store, stopped=True)
        self.assertEqual(first["schema"], checkpoint.SCHEMA)
        values = command_events("legacy-migration", 900, 202)
        for item in values:
            item["account"] = "AAA"
            item["execution_domain"] = "PAPER"
        self.append(values)
        second = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(second["schema"], lifecycle.SCHEMA)
        self.assertEqual(second["parent_generation"], first["generation"])
        rows = self.current_send_keys()
        self.assertEqual(rows, sorted(rows))
        runtime = (self.store / second["generation"] / "runtime-manifest.txt").read_text()
        self.assertIn(f"send_attempt_index_order={lifecycle.SEND_INDEX_ORDER}\n", runtime)
        lifecycle.verify_generation(self.store, journal=self.journal)

    def test_v1_generation_upgrades_to_v2_delta_and_exports_back_to_legacy(self) -> None:
        first = checkpoint.build_generation(self.journal, self.store, stopped=True)
        self.assertEqual(first["schema"], checkpoint.SCHEMA)
        first_generation = first["generation"]
        self.append(command_events("after-v1", 2000, 202))
        second = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(second["schema"], lifecycle.SCHEMA)
        self.assertEqual(second["parent_generation"], first_generation)
        self.assertEqual(second["segment_records"], 4)
        self.assertEqual(second["history_records"], 8)
        lifecycle.verify_generation(self.store, journal=self.journal)
        output = self.root / "v1-v2-downgrade.jsonl"
        exported = lifecycle.export_legacy(self.journal, self.store, output)
        self.assertEqual(exported["records"], 8)
        records = list(read_records(output, max_records=32))
        self.assertEqual([records[0]["req_id"], records[4]["req_id"]],
                         ["old", "after-v1"])

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

    def test_active_tail_lineage_corruption_is_rejected(self) -> None:
        lifecycle.seal_generation(self.journal, self.store, stopped=True)
        raw = bytearray(self.journal.read_bytes())
        marker = (lifecycle.TAIL_HEADER + "\t").encode()
        self.assertTrue(raw.startswith(marker))
        raw[len(marker)] = ord("x") if raw[len(marker)] != ord("x") else ord("y")
        self.journal.write_bytes(raw)
        os.chmod(self.journal, 0o600)
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
