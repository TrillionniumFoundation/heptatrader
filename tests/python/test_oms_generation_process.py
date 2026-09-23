"""Installed simulator restart across a stopped-state OMS V2 generation seal."""
from __future__ import annotations

import json
import hashlib
import stat
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import unittest

import test_installed_runtime_processes as base
from accept_core_release import generation_cost_pairs, validate_generation_cost_evidence


@unittest.skipUnless(os.environ.get("HEPTA_ISOLATED_PROCESS_TESTS") == "1",
                     "requires explicitly isolated multi-UID process test host")
class OmsGenerationInstalledProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "linux" or os.geteuid() != 0:
            raise RuntimeError("opted-in generation process test requires disposable root Linux")
        base.HOST_INTERLOCK.mkdir(mode=0o711)
        base.HOST_INTERLOCK.chmod(0o711)
        cls.lock = base.HOST_INTERLOCK / "session-lease-terminal-cleanup.lock"
        cls.lock.touch(mode=0o644, exist_ok=False)
        cls.lock.chmod(0o644)
        cls.lock_identity = (cls.lock.stat().st_dev, cls.lock.stat().st_ino)
        cls.host_identity = (base.HOST_INTERLOCK.stat().st_dev,
                             base.HOST_INTERLOCK.stat().st_ino)
        cls.addClassCleanup(cls._remove_interlock)

        cls.tmp = tempfile.TemporaryDirectory(prefix="ht-generation-process-", dir="/tmp")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.root.chmod(0o755)
        artifact = Path(os.environ["HEPTA_PROCESS_CANDIDATE_ARTIFACT"])
        digest = os.environ["HEPTA_PROCESS_CANDIDATE_SHA256"]
        cls.slot, cls.manifest = base.admitted_slot(
            artifact, digest, cls.root / "candidate")

    @classmethod
    def _remove_interlock(cls):
        for path, expected in ((cls.lock, cls.lock_identity),
                               (base.HOST_INTERLOCK, cls.host_identity)):
            observed = path.lstat()
            if (observed.st_dev, observed.st_ino) != expected or observed.st_uid != 0:
                raise RuntimeError("fixture interlock identity changed; refusing cleanup")
        cls.lock.unlink()
        base.HOST_INTERLOCK.rmdir()

    def _retain_diagnostics(self, runtime: base.InstalledRuntime) -> None:
        # Cleanup ordering stops processes first, then copies bounded logs before
        # the class removes its temporary state. Never publish a PASS here.
        root = os.environ.get("HEPTA_PROCESS_EVIDENCE_DIR")
        if not root:
            return
        custody = Path(root).lstat()
        if not stat.S_ISDIR(custody.st_mode):
            raise AssertionError("evidence destination is not a real directory")
        destination = Path(root) / self._testMethodName
        destination.mkdir(mode=0o700)
        def handoff_file(stream) -> None:
            os.fchmod(stream.fileno(), 0o600)
            current = os.fstat(stream.fileno())
            if (current.st_uid, current.st_gid) != (custody.st_uid, custody.st_gid):
                os.fchown(stream.fileno(), custody.st_uid, custody.st_gid)
        inventory = []
        for path in sorted(runtime.root.glob("*.log")):
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise AssertionError("unsafe process diagnostic source")
            with path.open("rb") as stream:
                content = stream.read(16 * 1024 * 1024)
            with (destination / path.name).open("xb") as output:
                output.write(content)
                handoff_file(output)
            inventory.append({"name": path.name, "source_bytes": metadata.st_size,
                              "retained_bytes": len(content),
                              "sha256": hashlib.sha256(content).hexdigest(),
                              "truncated": metadata.st_size > len(content)})
        diagnostic = {"source_sha": self.manifest["source_sha"],
                      "artifact_sha256": os.environ["HEPTA_PROCESS_CANDIDATE_SHA256"],
                      "processes": runtime.observed_processes,
                      "uncertain_observations": runtime.uncertain_observations,
                      "logs": inventory, "authorization_effect": "NONE"}
        with (destination / "diagnostics.json").open("x") as output:
            json.dump(diagnostic, output, sort_keys=True, indent=2)
            output.write("\n")
            handoff_file(output)
        # sudo executes the fixture, but the existing evidence directory names
        # the upload custodian. Transfer only this newly created diagnostic set;
        # do not make logs public or mutate source/runtime ownership.
        current = destination.lstat()
        if (current.st_uid, current.st_gid) != (custody.st_uid, custody.st_gid):
            os.chown(destination, custody.st_uid, custody.st_gid, follow_symlinks=False)

    @staticmethod
    def _execution_peak_rss_kib(runtime: base.InstalledRuntime) -> int:
        process = next(
            process for process, _output, _log, name in runtime.processes
            if name == "hepta-executiond")
        status = subprocess.run(
            ["/usr/bin/cat", f"/proc/{process.pid}/status"],
            env=base.CLEAN_ENV, user=base.EXECUTION_UID, group=base.TEST_GID,
            extra_groups=[], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=5, check=True).stdout
        for line in status.splitlines():
            if line.startswith("VmHWM:"):
                fields = line.split()
                if len(fields) == 3 and fields[2] == "kB":
                    return int(fields[1])
        raise AssertionError("execution process VmHWM is unavailable")

    @staticmethod
    def _execution_metrics(runtime: base.InstalledRuntime,
                           minimum_place_samples: int = 0) -> dict:
        log = next(
            log for _process, _output, log, name in runtime.processes
            if name == "hepta-executiond")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            for line in reversed(log.read_text().splitlines()):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if value.get("schema") != "heptatrader.oms-capacity.v1":
                    continue
                metrics = value.get("execution_metrics")
                if not isinstance(metrics, dict):
                    continue
                place = metrics.get("place_latency_total", {})
                if place.get("samples", 0) < minimum_place_samples:
                    continue
                return metrics
            time.sleep(0.025)
        raise AssertionError("installed Execution metrics were not published")

    @classmethod
    def _execution_startup_timings(cls, runtime: base.InstalledRuntime) -> dict:
        metrics = cls._execution_metrics(runtime)
        output = {}
        for source, target in (
            ("simulator_state_recovery_latency", "simulator_state_recovery_ns"),
            ("recovery_latency", "coordinator_recovery_ns"),
            ("startup_ready_latency", "startup_ready_ns"),
        ):
            latency = metrics.get(source, {})
            observed = latency.get("last_ns")
            if latency.get("samples", 0) <= 0 or not isinstance(observed, int) or observed < 0:
                raise AssertionError(f"missing installed Execution startup metric: {source}")
            output[target] = observed
        included_recovery_ns = (
            output["simulator_state_recovery_ns"] +
            output["coordinator_recovery_ns"]
        )
        if output["startup_ready_ns"] < included_recovery_ns:
            raise AssertionError(
                "complete startup-ready timing must include both sequential recovery phases")
        return output

    @staticmethod
    def _p99_upper_ns(metric: dict) -> int | None:
        samples = metric.get("samples", 0)
        counts = metric.get("bucket_counts")
        bounds = metric.get("bucket_upper_ns")
        if not isinstance(samples, int) or samples <= 0 or not isinstance(counts, list) or \
                not isinstance(bounds, list) or len(counts) != len(bounds):
            return None
        rank = (samples * 99 + 99) // 100
        cumulative = 0
        for count, bound in zip(counts, bounds):
            cumulative += count
            if cumulative >= rank:
                return bound
        return None

    @staticmethod
    def _store_bytes(store: Path) -> int:
        return sum(
            path.stat().st_size
            for path in store.rglob("*")
            if path.is_file())

    def test_capacity_observes_discarded_client_reply_without_resending(self):
        runtime = base.InstalledRuntime(self.root / "runtime-lost-reply")
        self.addCleanup(self._retain_diagnostics, runtime)
        self.addCleanup(runtime.stop)
        runtime.start(self.slot)
        runtime.provision()
        real_call = runtime.call
        lost = []
        calls = []
        def discard_one_reply(tool, fields=(), **options):
            calls.append(tool)
            result = real_call(tool, fields, **options)
            if tool == "trade.place_order" and not lost:
                # Client/application delivery fault AFTER actual IPC success.
                # The observer gets no fabricated authoritative order result.
                lost.append((options["call_id"], result["order_id"]))
                return {"status": "uncertain", "tool": tool,
                        "reason_code": "FIXTURE_CLIENT_REPLY_DISCARDED"}
            return result
        runtime.call = discard_one_reply
        command, _fields, order = runtime.place("BUY", 1, "1.1002", observe_uncertain=True)
        self.assertEqual(lost, [(command, order)])
        runtime.wait_position(1)
        runtime.wait_no_orders()
        self.assertEqual(runtime.send_count(), 1)
        self.assertEqual(calls.count("trade.place_order"), 1)
        self.assertEqual(calls.count("risk.preview_order"), 1)
        self.assertIn("execution.get_command_status", calls)
        self.assertEqual(len(runtime.uncertain_observations), 1)
        self.assertTrue(runtime.uncertain_observations[0]["resolved_by_status"])
        runtime.place("SELL", 1, "1.1000")
        runtime.wait_position(0)
        runtime.wait_no_orders()
        self.assertEqual(runtime.send_count(), 2)
        runtime.stop()

    def test_generation_cost_curve_reports_restart_memory_recovery_seal_and_disk(self):
        # Opt-in extended workload uses the SAME installed binaries, UID,
        # final risk, durable identity and rate-gated tool path as core.
        profile = os.environ.get("HEPTA_GENERATION_COST_PROFILE", "core")
        pairs_per_stage = generation_cost_pairs(profile)
        workload_started = time.monotonic_ns()
        runtime = base.InstalledRuntime(self.root / "runtime-cost-curve",
                                        trade_calls_per_minute=4 * max(pairs_per_stage))
        self.addCleanup(self._retain_diagnostics, runtime)
        self.addCleanup(runtime.stop)
        runtime.start(self.slot)
        runtime.provision()
        journal = runtime.root / "es/oms-journal.jsonl"
        store = Path(str(journal) + ".generations")
        helper = self.slot / "libexec/heptatrader/hepta_oms_lifecycle.py"

        first_command = None
        first_fields = None
        first_order = None
        expected_admitted = 0
        points = []
        for stage, pairs in enumerate(pairs_per_stage, start=1):
            for _ in range(pairs):
                command, fields, order_id = runtime.place("BUY", 1, "1.1002", ttl_ms=600000,
                                                            observe_uncertain=profile == "capacity")
                runtime.wait_position(1)
                runtime.wait_no_orders()
                runtime.place("SELL", 1, "1.1000", ttl_ms=600000,
                              observe_uncertain=profile == "capacity")
                runtime.wait_position(0)
                runtime.wait_no_orders()
                expected_admitted += 2
                if first_command is None:
                    first_command, first_fields, first_order = (
                        command, fields, order_id)

            execution_before_stop = self._execution_metrics(
                runtime, minimum_place_samples=pairs * 2)
            place_latency = execution_before_stop["place_latency_total"]
            journal_bytes_before_seal = journal.stat().st_size
            runtime.stop()
            seal_started = time.monotonic_ns()
            sealed = subprocess.run([
                sys.executable, "-S", str(helper), "seal",
                "--journal", str(journal), "--store", str(store),
                "--stopped-state",
            ], env=base.CLEAN_ENV, user=base.EXECUTION_UID,
                group=base.TEST_GID, extra_groups=[],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=30)
            seal_ns = time.monotonic_ns() - seal_started
            self.assertEqual(sealed.returncode, 0, sealed.stderr)
            receipt = json.loads(sealed.stdout)
            selected = json.loads((store / "CURRENT").read_text())["generation"]
            sealed_manifest = json.loads((store / selected / "manifest.json").read_text())
            self.assertEqual(sealed_manifest["send_attempt_records"], expected_admitted,
                             "status observation must never create a second send")

            runtime.start(self.slot)
            runtime.wait_position(0)
            runtime.wait_no_orders()
            startup = self._execution_startup_timings(runtime)
            recovery_ns = startup["coordinator_recovery_ns"]
            peak_rss_kib = self._execution_peak_rss_kib(runtime)
            risk = runtime.call("risk.get_limits", [])["payload"]
            self.assertEqual(risk["admitted_order_count"], expected_admitted)

            tail_before_duplicate = journal.read_bytes()
            status = runtime.call(
                "execution.get_command_status",
                [f"command_id={first_command}"])["payload"]
            self.assertEqual(status["order_id"], first_order)
            duplicate = runtime.call(
                "trade.place_order", first_fields,
                call_id=first_command, duplicate=True)
            self.assertEqual(duplicate["order_id"], first_order)
            self.assertEqual(journal.read_bytes(), tail_before_duplicate)

            points.append({
                "stage": stage,
                "admitted_orders": expected_admitted,
                "history_records": receipt["history_records"],
                "send_attempt_records": sealed_manifest["send_attempt_records"],
                "seal_ns": seal_ns,
                "restart_recovery_ns": recovery_ns,
                "simulator_state_recovery_ns": startup["simulator_state_recovery_ns"],
                "startup_ready_ns": startup["startup_ready_ns"],
                "execution_peak_rss_kib": peak_rss_kib,
                "place_latency_total_samples": place_latency["samples"],
                "place_latency_total_max_ns": place_latency["max_ns"],
                "place_latency_total_p99_upper_ns": self._p99_upper_ns(place_latency),
                "journal_bytes_before_seal": journal_bytes_before_seal,
                "retained_disk_bytes": self._store_bytes(store),
            })

        self.assertEqual(
            [point["admitted_orders"] for point in points],
            [2 * sum(pairs_per_stage[:i + 1]) for i in range(len(pairs_per_stage))])
        self.assertTrue(all(point["seal_ns"] > 0 for point in points))
        self.assertTrue(all(point["restart_recovery_ns"] >= 0
                            for point in points))
        self.assertTrue(all(point["execution_peak_rss_kib"] > 0
                            for point in points))
        self.assertTrue(all(point["journal_bytes_before_seal"] > 0 and
                            point["retained_disk_bytes"] > 0
                            for point in points))
        self.assertTrue(all(
            isinstance(point["place_latency_total_p99_upper_ns"], int) and
            point["place_latency_total_p99_upper_ns"] >= 0 and
            point["place_latency_total_max_ns"] >= 0
            for point in points),
            "repository envelope requires a finite p99 latency bucket at every stage")
        self.assertEqual(
            [point["history_records"] for point in points],
            sorted(point["history_records"] for point in points))
        self.assertLess(
            points[0]["retained_disk_bytes"],
            points[-1]["retained_disk_bytes"])

        runtime.stop()
        before_rebase = self._store_bytes(store)
        rebase_started = time.monotonic_ns()
        rebased = subprocess.run([
            sys.executable, "-S", str(helper), "rebase",
            "--journal", str(journal), "--store", str(store),
            "--stopped-state", "--prune-ancestors",
        ], env=base.CLEAN_ENV, user=base.EXECUTION_UID,
            group=base.TEST_GID, extra_groups=[],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=60)
        rebase_ns = time.monotonic_ns() - rebase_started
        self.assertEqual(rebased.returncode, 0, rebased.stderr)
        rebase_receipt = json.loads(rebased.stdout)
        after_rebase = self._store_bytes(store)
        self.assertEqual(rebase_receipt["result"], "PASS")
        self.assertGreater(rebase_receipt["pruned_generations"], 0)
        self.assertLess(after_rebase, before_rebase)

        runtime.start(self.slot)
        runtime.wait_position(0)
        runtime.wait_no_orders()
        post_rebase_startup = self._execution_startup_timings(runtime)
        post_rebase_recovery_ns = post_rebase_startup["coordinator_recovery_ns"]
        post_rebase_peak_rss_kib = self._execution_peak_rss_kib(runtime)
        risk = runtime.call("risk.get_limits", [])["payload"]
        self.assertEqual(risk["admitted_order_count"], expected_admitted)
        tail_before_duplicate = journal.read_bytes()
        duplicate = runtime.call(
            "trade.place_order", first_fields,
            call_id=first_command, duplicate=True)
        self.assertEqual(duplicate["order_id"], first_order)
        self.assertEqual(journal.read_bytes(), tail_before_duplicate)

        # Do not publish PASS while the last real processes are still alive.
        # stop() rejects forced shutdown/nonzero exits; cleanup remains idempotent.
        runtime.stop()
        observation = {
            "schema": "heptatrader.installed-generation-cost-curve.v1",
            "cost_profile": profile,
            "orderly_shutdown_verified": True,
            "uncertain_observations": runtime.uncertain_observations,
            "mutation_resends": 0,
            "elapsed_ns": time.monotonic_ns() - workload_started,
            "processes": runtime.observed_processes,
            "result": "PASS",
            "synthetic": True,
            "installed_processes": True,
            "broker_io": False,
            "source_sha": self.manifest["source_sha"],
            "artifact_sha256": os.environ["HEPTA_PROCESS_CANDIDATE_SHA256"],
            "points": points,
            "configured_trade_calls_per_minute": runtime.trade_calls_per_minute,
            "rebase_ns": rebase_ns,
            "retained_disk_bytes_before_rebase": before_rebase,
            "retained_disk_bytes_after_rebase": after_rebase,
            "post_rebase_recovery_ns": post_rebase_recovery_ns,
            "post_rebase_simulator_state_recovery_ns":
                post_rebase_startup["simulator_state_recovery_ns"],
            "post_rebase_startup_ready_ns": post_rebase_startup["startup_ready_ns"],
            "post_rebase_execution_peak_rss_kib": post_rebase_peak_rss_kib,
            "oldest_command_duplicate_no_resend": True,
            "final_position": 0,
            "authorization_effect": "NONE",
        }
        evidence_root = os.environ.get("HEPTA_PROCESS_EVIDENCE_DIR")
        if evidence_root:
            evidence_directory = Path(evidence_root)
            if not evidence_directory.is_dir() or evidence_directory.is_symlink():
                raise AssertionError("process evidence directory is unavailable or unsafe")
            evidence_path = evidence_directory / "installed-generation-cost-curve.json"
            # Validate staged evidence before an atomic create-only publication.
            with tempfile.TemporaryDirectory(prefix=".generation-evidence-",
                                             dir=evidence_directory) as staging:
                staged = Path(staging) / "curve.json"
                with staged.open("x") as stream:
                    json.dump(observation, stream, sort_keys=True, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                validate_generation_cost_evidence(
                    staged, self.manifest["source_sha"],
                    os.environ["HEPTA_PROCESS_CANDIDATE_SHA256"], expected_profile=profile)
                os.link(staged, evidence_path)  # never replace previous evidence
                directory_fd = os.open(
                    evidence_directory,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        print(json.dumps(observation, sort_keys=True))

    def test_two_cash_instruments_survive_installed_generation_restart(self):
        runtime = base.InstalledRuntime(self.root / "runtime-two-cash",
                                        two_cash_instruments=True)
        self.addCleanup(self._retain_diagnostics, runtime)
        self.addCleanup(runtime.stop)
        runtime.start(self.slot)
        runtime.provision()

        def wait_positions(expected):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                rows = runtime.call("portfolio.list_positions")["payload"]["positions"]
                observed = {row["instrument"]: row["quantity"] for row in rows}
                if all(observed.get(symbol, 0) == quantity
                       for symbol, quantity in expected.items()):
                    return
                time.sleep(0.025)
            self.fail(f"two-instrument positions did not settle: {observed}")

        first = runtime.place("BUY", 25, "1.1002", symbol="EUR")
        second = runtime.place("BUY", 40, "1.2502", symbol="GBP")
        self.assertNotEqual(first[2], second[2])
        wait_positions({"EUR.USD": 25, "GBP.USD": 40})
        runtime.wait_no_orders()
        self.assertEqual(runtime.call("risk.get_limits")["payload"]["admitted_order_count"], 2)
        runtime.stop()
        journal = runtime.root / "es/oms-journal.jsonl"
        result = subprocess.run([
            sys.executable, "-S", str(self.slot / "libexec/heptatrader/hepta_oms_lifecycle.py"),
            "seal", "--journal", str(journal), "--store", str(journal) + ".generations",
            "--stopped-state"], env=base.CLEAN_ENV, user=base.EXECUTION_UID,
            group=base.TEST_GID, extra_groups=[], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        runtime.start(self.slot)
        wait_positions({"EUR.USD": 25, "GBP.USD": 40})
        before = journal.read_bytes()
        for command, fields, order_id in (first, second):
            duplicate = runtime.call("trade.place_order", fields,
                                     call_id=command, duplicate=True)
            self.assertEqual(duplicate["order_id"], order_id)
        self.assertEqual(journal.read_bytes(), before)
        runtime.place("SELL", 25, "1.1000", symbol="EUR")
        runtime.place("SELL", 40, "1.2500", symbol="GBP")
        wait_positions({"EUR.USD": 0, "GBP.USD": 0})
        runtime.wait_no_orders()
        self.assertEqual(runtime.call("risk.get_limits")["payload"]["admitted_order_count"], 4)
        runtime.stop()

    def test_installed_trade_rate_gate_remains_enabled(self):
        runtime = base.InstalledRuntime(self.root / "runtime-rate-gate",
                                        trade_calls_per_minute=1)
        self.addCleanup(self._retain_diagnostics, runtime)
        self.addCleanup(runtime.stop)
        runtime.start(self.slot)
        runtime.provision()
        runtime.place("BUY", 1, "1.0000")  # rests; no invented fill
        before = runtime.send_count()
        self.assertEqual(before, 1)
        with self.assertRaisesRegex(AssertionError, "AGENT_TRADE_RATE_LIMIT"):
            runtime.place("BUY", 1, "1.0000")
        self.assertEqual(runtime.send_count(), before)
        self.assertEqual(runtime.position(), 0)

    def test_fill_stop_seal_restart_preserves_state_identity_and_order_watermark(self):
        runtime = base.InstalledRuntime(self.root / "runtime")
        self.addCleanup(self._retain_diagnostics, runtime)
        self.addCleanup(runtime.stop)
        runtime.start(self.slot)
        runtime.provision()
        command, fields, first_order = runtime.place("BUY", 25, "1.1002")
        runtime.wait_position(25)
        runtime.wait_no_orders()
        risk_before = runtime.call("risk.get_limits", [])["payload"]
        self.assertEqual(risk_before["admitted_order_count"], 1)
        runtime.stop()

        journal = runtime.root / "es/oms-journal.jsonl"
        store = Path(str(journal) + ".generations")
        helper = self.slot / "libexec/heptatrader/hepta_oms_lifecycle.py"
        result = subprocess.run([
            sys.executable, "-S", str(helper), "seal",
            "--journal", str(journal), "--store", str(store), "--stopped-state",
        ], env=base.CLEAN_ENV, user=base.EXECUTION_UID, group=base.TEST_GID,
            extra_groups=[], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["schema"], "heptatrader.oms-generation.v2")
        self.assertEqual(receipt["authorization_effect"], "NONE")
        self.assertTrue(journal.read_bytes().startswith(b"HEPTA_OMS_ACTIVE_TAIL_V1\t"))

        generation = json.loads((store / "CURRENT").read_text())["generation"]
        manifest = json.loads((store / generation / "manifest.json").read_text())
        self.assertGreaterEqual(manifest["send_attempt_records"], 1)
        tail_before_duplicate = journal.read_bytes()

        runtime.start(self.slot)
        runtime.wait_position(25)
        risk_after = runtime.call("risk.get_limits", [])["payload"]
        self.assertEqual(risk_after["admitted_order_count"], 1,
                         "daily admission state must survive generation rotation")
        status = runtime.call("execution.get_command_status",
                              [f"command_id={command}"])["payload"]
        self.assertEqual(status["command_status"], "accepted")
        self.assertEqual(status["order_id"], first_order)
        duplicate = runtime.call("trade.place_order", fields,
                                 call_id=command, duplicate=True)
        self.assertEqual(duplicate["order_id"], first_order)
        self.assertEqual(journal.read_bytes(), tail_before_duplicate,
                         "idempotent replay must not append or resend after seal")

        _, _, next_order = runtime.place("SELL", 5, "1.1000")
        self.assertGreater(next_order, first_order,
                           "order-id watermark must survive generation rotation")
        runtime.wait_position(20)
        runtime.wait_no_orders()
        self.assertEqual(runtime.call("risk.get_limits", [])["payload"]
                         ["admitted_order_count"], 2)
        runtime.place("SELL", 20, "1.1000")
        runtime.wait_position(0)
        runtime.wait_no_orders()
        self.assertEqual(runtime.call("risk.get_limits", [])["payload"]
                         ["admitted_order_count"], 3)
        runtime.stop()

        # A second restart proves the post-seal active tail composes with the
        # immutable generation rather than replacing its economic base state.
        runtime.start(self.slot)
        runtime.wait_position(0)
        status = runtime.call("execution.get_command_status",
                              [f"command_id={command}"])["payload"]
        self.assertEqual(status["order_id"], first_order)
        self.assertEqual(runtime.call("risk.get_limits", [])["payload"]
                         ["admitted_order_count"], 3)
        runtime.wait_no_orders()
