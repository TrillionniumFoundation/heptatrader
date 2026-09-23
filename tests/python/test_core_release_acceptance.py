"""Execute orchestration with inert subprocess seams; real fixtures run in CI."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import accept_core_release as acceptance

SHA = "a" * 40


class CoreReleaseAcceptanceTests(unittest.TestCase):
    def fixture(self, root: Path, fail=None, tamper=False, untracked=False,
                omit_cost_evidence=False, omit_core_evidence=False, omit_pair=False, tamper_client=False):
        calls = []
        candidate = None

        def run(argv, **kwargs):
            nonlocal candidate
            calls.append(argv)
            self.assertIs(kwargs["check"], True)
            stdout = ""
            if "rev-parse" in argv:
                stdout = (acceptance.REFERENCE_SHA if "-C" in argv else SHA) + "\n"
            elif argv[:2] == ["git", "status"] and untracked:
                stdout = "?? injected-untracked-file\n"
            elif "show" in argv:
                stdout = "1789279500\n"
            elif argv[:2] == ["git", "init"]:
                reference = Path(argv[2])
                reference.mkdir()
                (reference / "VERSION").write_text("0.3.0\n")
            elif argv[:2] == ["cmake", "-S"]:
                reference = Path(argv[argv.index("-S") + 1])
                build = Path(argv[argv.index("-B") + 1])
                reply = build / ".cmake/api/v1/reply"
                reply.mkdir(parents=True)
                (reply / "index-fixture.json").write_text(json.dumps({
                    "reply": {"codemodel-v2": {"jsonFile": "model.json"}}}))
                (reply / "model.json").write_text(json.dumps({
                    "paths": {"source": str(reference), "build": str(build)},
                    "configurations": [{"name": "Release", "targets": [
                        {"name": "fixture-app", "jsonFile": "app.json"}]}]}))
                (reply / "app.json").write_text(json.dumps({"name": "fixture-app",
                    "type": "EXECUTABLE", "install": {"destinations": [{"path": "bin"}]}}))
            elif argv and argv[0] == "tar" and "-czf" in argv:
                Path(argv[argv.index("-czf") + 1]).write_bytes(b"independent-sdk")
            elif argv[:3] == ["sudo", "mktemp", "-d"]:
                stdout = "/tmp/hepta-accept-source.ABCDef12\n"
            phase = None
            if "scripts/run_python_tests.py" in argv:
                phase = argv[argv.index("--lane") + 1]
            elif "scripts/run_release_simulator_smoke.py" in argv:
                phase = "smoke"
            elif "tests/research/installed_client_server_pair.py" in argv:
                phase = "client-pair"
            elif "tests/systemd_simulator_smoke.py" in argv:
                phase = "systemd"
                if tamper:
                    candidate.write_bytes(b"substituted after testing")
                if tamper_client:
                    next((root / "dist/core-evidence").glob("strategy-client-sdk-*.tar.gz")).write_bytes(b"changed-sdk")
            if fail is not None and phase == fail:
                raise subprocess.CalledProcessError(29, argv)
            if phase == "client-pair" and not omit_pair:
                # Orchestration seam only. The independent root-host scenario
                # executes the installed commands and produces the real receipt.
                from test_client_pair_admission import paired_receipt
                path = Path(argv[argv.index("--output") + 1])
                value = paired_receipt(SHA, argv[argv.index("--core-sha256") + 1],
                                       argv[argv.index("--client-sha256") + 1])
                path.write_text(json.dumps(value))
            if phase == "core" and not omit_core_evidence:
                evidence_dir = Path(kwargs["env"]["HEPTA_CORE_EVIDENCE_DIR"])
                source_sha = kwargs["env"]["HEPTA_CORE_EVIDENCE_SOURCE_SHA"]
                evidence_dir.mkdir(parents=True, exist_ok=True)
                logical = 20000 * 512
                start = 1234 * 512
                suffix_bytes = logical - start
                (evidence_dir / "generation-index-read-cost.json").write_text(json.dumps({
                    "schema": "heptatrader.generation-index-read-cost.v1",
                    "result": "PASS", "source_sha": source_sha,
                    "fixture_rows": 20000, "row_bytes": 512,
                    "logical_bytes": logical, "suffix_start_bytes": start,
                    "full": {"lines": 20000, "read_calls": 157,
                             "read_bytes": logical, "selected_bytes": logical},
                    "suffix": {"lines": 20000 - 1234, "read_calls": 147,
                               "read_bytes": suffix_bytes, "selected_bytes": suffix_bytes},
                    "broker_io": False, "authorization_effect": "NONE",
                }))
                points = []
                for generation, owners, commands, history in (
                    (4, 4, 256, 1024), (8, 8, 512, 2048), (16, 16, 1024, 4096)
                ):
                    points.append({
                        "generation_count": generation, "owner_count": owners,
                        "history_records": history, "command_records": commands,
                        "seal_ns": 1, "verify_ns": 1, "active_bytes_before_seal": 1,
                        "generation_output_bytes": 1, "runtime_command_index_bytes": 1,
                        "send_attempt_index_bytes": 1, "logical_event_bytes": 1,
                        "retained_disk_bytes": generation + 10,
                        "retained_to_logical_numerator": generation + 10,
                        "retained_to_logical_denominator": 1,
                    })
                (evidence_dir / "synthetic-generation-cost-curve.json").write_text(json.dumps({
                    "schema": "heptatrader.synthetic-generation-cost-curve.v2",
                    "result": "PASS", "source_sha": source_sha, "synthetic": True,
                    "broker_io": False, "commands_per_generation": 64,
                    "generation_count": 16, "maximum_owner_count": 16,
                    "points": points, "rebase_ns": 1,
                    "retained_disk_bytes_before_rebase": 100,
                    "retained_disk_bytes_after_rebase": 50,
                    "test_process_peak_rss_kib": 1, "authorization_effect": "NONE",
                }))
            if phase == "process" and not omit_cost_evidence:
                evidence_assignment = next(
                    item for item in argv
                    if item.startswith("HEPTA_PROCESS_EVIDENCE_DIR="))
                artifact_assignment = next(
                    item for item in argv
                    if item.startswith("HEPTA_PROCESS_CANDIDATE_SHA256="))
                evidence_dir = Path(evidence_assignment.split("=", 1)[1])
                artifact_sha = artifact_assignment.split("=", 1)[1]
                evidence_dir.mkdir(parents=True, exist_ok=True)
                points = []
                for admitted, history in ((8, 32), (40, 160), (168, 672)):
                    points.append({
                        "admitted_orders": admitted,
                        "history_records": history,
                        "seal_ns": 1,
                        "restart_recovery_ns": 1,
                        "simulator_state_recovery_ns": 1,
                        "startup_ready_ns": 3,
                        "execution_peak_rss_kib": 1,
                        "place_latency_total_samples": 1,
                        "place_latency_total_max_ns": 1,
                        "place_latency_total_p99_upper_ns": 1,
                        "journal_bytes_before_seal": 1,
                        "retained_disk_bytes": admitted + 10,
                    })
                (evidence_dir / "installed-generation-cost-curve.json").write_text(
                    json.dumps({
                        "schema": "heptatrader.installed-generation-cost-curve.v1",
                        "result": "PASS",
                        "synthetic": True,
                        "installed_processes": True,
                        "broker_io": False,
                        "source_sha": SHA,
                        "artifact_sha256": artifact_sha,
                        "points": points,
                        "rebase_ns": 1,
                        "retained_disk_bytes_before_rebase": 100,
                        "retained_disk_bytes_after_rebase": 50,
                        "post_rebase_recovery_ns": 1,
                        "post_rebase_simulator_state_recovery_ns": 1,
                        "post_rebase_startup_ready_ns": 3,
                        "post_rebase_execution_peak_rss_kib": 1,
                        "oldest_command_duplicate_no_resend": True,
                        "final_position": 0,
                        "authorization_effect": "NONE",
                    }))
            if "scripts/build_release_package.py" in argv:
                target = Path(argv[argv.index("--output") + 1])
                source = argv[argv.index("--source-sha") + 1]
                target.write_bytes(source.encode())
                Path(str(target) + ".sha256").write_text(hashlib.sha256(target.read_bytes()).hexdigest() + "  package\n")
                if source == SHA:
                    candidate = target
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        return run, calls

    def test_one_digest_flows_through_both_installed_acceptances(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("0.3.0\n")
            run, calls = self.fixture(root)
            receipt = acceptance.accept(root / "build", root / "dist", SHA, root=root, run=run)
            self.assertEqual(receipt["result"], "PASS")
            self.assertEqual(receipt["checks"], list(acceptance.CHECKS))
            candidate = str(root / "dist" / f"heptatrader-0.3.0-core-{SHA}.tar.gz")
            sha = hashlib.sha256(SHA.encode()).hexdigest()
            process = next(c for c in calls if "--lane" in c and c[-1] == "process")
            self.assertIn("HEPTA_PROCESS_CANDIDATE_ARTIFACT=" + candidate, process)
            self.assertIn("HEPTA_PROCESS_CANDIDATE_SHA256=" + sha, process)
            self.assertIn("HEPTA_ISOLATED_PROCESS_TESTS=1", process)
            systemd = next(c for c in calls if "tests/systemd_simulator_smoke.py" in c)
            self.assertEqual(systemd[systemd.index("--artifact") + 1], candidate)
            self.assertEqual(systemd[systemd.index("--expected-sha256") + 1], sha)
            self.assertIn("HEPTA_DISPOSABLE_SYSTEMD_TEST=1", systemd)
            pair = next(c for c in calls if "tests/research/installed_client_server_pair.py" in c)
            self.assertEqual(pair[pair.index("--core-artifact") + 1], candidate)
            self.assertEqual(pair[pair.index("--core-sha256") + 1], sha)
            self.assertEqual(pair[pair.index("--client-sha256") + 1],
                             hashlib.sha256(b"independent-sdk").hexdigest())
            self.assertEqual(pair[pair.index("--source-sha") + 1], SHA)
            self.assertIn("HEPTA_ISOLATED_PROCESS_TESTS=1", pair)
            install = next(c for c in calls if c[:2] == ["cmake", "--install"])
            self.assertEqual(install[-2:], ["--component", "StrategyClientSDK"])
            self.assertLess(calls.index(process), calls.index(pair))
            self.assertLess(calls.index(pair), calls.index(systemd))
            self.assertEqual(receipt["strategy_client_sha256"], hashlib.sha256(b"independent-sdk").hexdigest())
            # env options must precede assignments; otherwise --chdir becomes
            # a command name instead of setting the protected source cwd.
            self.assertTrue(process[3].startswith("--chdir="))
            self.assertEqual(receipt["package_sha256"], sha)
            self.assertFalse(receipt["paper_authorized"])
            status_calls = [c for c in calls if c[:2] == ["git", "status"]]
            self.assertEqual(len(status_calls), 2)
            self.assertIn(":(exclude,top)dist", status_calls[-1])
            with self.assertRaises(ValueError):
                acceptance.accept(root / "build", root / "dist", SHA, root=root, run=run)

    def test_every_failed_phase_and_changed_package_prevent_pass_receipt(self):
        for phase in ("install", "core", "smoke", "process", "client-pair", "systemd", "tamper"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "VERSION").write_text("0.3.0\n")
                run, calls = self.fixture(root, fail=phase, tamper=phase == "tamper")
                with self.assertRaises((subprocess.CalledProcessError, ValueError)):
                    acceptance.accept(root / "build", root / "dist", SHA, root=root, run=run)
                self.assertFalse((root / "dist/core-acceptance.json").exists())
                if phase in ("process", "client-pair", "systemd", "tamper"):
                    self.assertTrue(any(c[:3] == ["sudo", "rm", "-rf"] for c in calls))
                if phase in ("process", "client-pair"):
                    self.assertFalse(any("tests/systemd_simulator_smoke.py" in c for c in calls))

    def test_missing_pair_evidence_and_changed_client_prevent_acceptance(self):
        for missing in (True, False):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "VERSION").write_text("0.3.0\n")
                run, calls = self.fixture(root, omit_pair=missing, tamper_client=not missing)
                with self.assertRaisesRegex(ValueError, "pair evidence|client package changed"):
                    acceptance.accept(root / "build", root / "dist", SHA, root=root, run=run)
                self.assertFalse((root / "dist/core-acceptance.json").exists())
                self.assertTrue(any(c[:3] == ["sudo", "rm", "-rf"] for c in calls))
                if missing:
                    self.assertFalse(any("tests/systemd_simulator_smoke.py" in c for c in calls))

    def test_missing_core_cost_evidence_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("0.3.0\n")
            run, _calls = self.fixture(root, omit_core_evidence=True)
            with self.assertRaisesRegex(ValueError, "generation index read evidence"):
                acceptance.accept(root / "build", root / "dist", SHA, root=root, run=run)
            self.assertFalse((root / "dist/core-acceptance.json").exists())

    def test_missing_generation_cost_evidence_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("0.3.0\n")
            run, _calls = self.fixture(root, omit_cost_evidence=True)
            with self.assertRaisesRegex(ValueError, "generation cost evidence"):
                acceptance.accept(root / "build", root / "dist", SHA, root=root, run=run)
            self.assertFalse((root / "dist/core-acceptance.json").exists())

    def test_generation_cost_evidence_rejects_identity_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "curve.json"
            path.write_text(json.dumps({
                "schema": "heptatrader.installed-generation-cost-curve.v1",
                "result": "PASS",
                "synthetic": True,
                "installed_processes": True,
                "broker_io": False,
                "source_sha": "b" * 40,
                "artifact_sha256": "c" * 64,
                "points": [],
                "authorization_effect": "NONE",
                "oldest_command_duplicate_no_resend": True,
                "final_position": 0,
            }))
            with self.assertRaisesRegex(ValueError, "identity or result"):
                acceptance.validate_generation_cost_evidence(
                    path, SHA, "c" * 64)

    @staticmethod
    def generation_curve(profile):
        pairs = acceptance.generation_cost_pairs(profile)
        points = []
        for index, batch in enumerate(pairs):
            admitted = 2 * sum(pairs[:index + 1])
            points.append({"admitted_orders": admitted, "history_records": 4 * admitted,
                "seal_ns": 1, "restart_recovery_ns": 1, "simulator_state_recovery_ns": 1,
                "startup_ready_ns": 3, "execution_peak_rss_kib": 1,
                "place_latency_total_samples": 2 * batch, "place_latency_total_max_ns": 1,
                "place_latency_total_p99_upper_ns": 1, "journal_bytes_before_seal": 1,
                "retained_disk_bytes": admitted + 10})
        return {"schema": "heptatrader.installed-generation-cost-curve.v1",
            "result": "PASS", "cost_profile": profile, "synthetic": True,
            "installed_processes": True, "broker_io": False, "source_sha": SHA,
            "artifact_sha256": "c" * 64, "points": points, "rebase_ns": 1,
            "retained_disk_bytes_before_rebase": 100,
            "retained_disk_bytes_after_rebase": 50, "post_rebase_recovery_ns": 1,
            "post_rebase_simulator_state_recovery_ns": 1, "post_rebase_startup_ready_ns": 3,
            "post_rebase_execution_peak_rss_kib": 1, "oldest_command_duplicate_no_resend": True,
            "final_position": 0, "authorization_effect": "NONE", "elapsed_ns": 1,
            "orderly_shutdown_verified": True, "configured_trade_calls_per_minute": 4 * max(pairs),
            "processes": [{"name": name, "uid": uid, "pid": 100 + index,
                           "executable_sha256": "d" * 64}
                for index in range(5)
                for name, uid in (("hepta-executiond", 61002), ("hepta-tool-gatewayd", 61001))]}

    def test_generation_workload_is_explicit_and_bounded(self):
        self.assertEqual(acceptance.generation_cost_pairs(), (4, 16, 64))
        self.assertEqual(acceptance.generation_cost_pairs("extended"), (32, 128, 512))
        self.assertEqual(acceptance.generation_cost_pairs("capacity"), (3584, 3584, 3584))
        for invalid in (None, True, [], "", "EXTENDED", "production"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                acceptance.generation_cost_pairs(invalid)

    def test_generation_receipt_cannot_select_its_own_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "curve.json"
            core = self.generation_curve("core")
            core.pop("cost_profile")  # Existing core v1 observations remain readable.
            path.write_text(json.dumps(core))
            acceptance.validate_generation_cost_evidence(path, SHA, "c" * 64)
            with self.assertRaisesRegex(ValueError, "profile"):
                acceptance.validate_generation_cost_evidence(
                    path, SHA, "c" * 64, expected_profile="extended")
            path.write_text(json.dumps(self.generation_curve("extended")))
            acceptance.validate_generation_cost_evidence(
                path, SHA, "c" * 64, expected_profile="extended")
            with self.assertRaisesRegex(ValueError, "profile"):
                acceptance.validate_generation_cost_evidence(path, SHA, "c" * 64)

    def test_extended_generation_evidence_requires_actual_scope_and_shutdown(self):
        mutations = [
            lambda v: v.update(orderly_shutdown_verified=False),
            lambda v: v.update(elapsed_ns=True),
            lambda v: v.update(configured_trade_calls_per_minute=0),
            lambda v: v.update(configured_trade_calls_per_minute=2048.0),
            lambda v: v["processes"][2].update(pid=v["processes"][0]["pid"]),
            lambda v: v["points"][2].update(admitted_orders=168),
            lambda v: v["points"][2].update(place_latency_total_samples=1),
            lambda v: v["processes"].pop(),
            lambda v: v["processes"][0].update(uid=0),
            lambda v: v["processes"][0].update(executable_sha256="e" * 64),
            lambda v: v["processes"][0].update(pid=True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "curve.json"
            for index, mutate in enumerate(mutations):
                with self.subTest(mutation=index):
                    value = self.generation_curve("extended")
                    mutate(value)
                    path.write_text(json.dumps(value))
                    with self.assertRaises(ValueError):
                        acceptance.validate_generation_cost_evidence(
                            path, SHA, "c" * 64, expected_profile="extended")

    def test_capacity_profile_cannot_borrow_smaller_or_incomplete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "curve.json"
            for profile in ("core", "extended"):
                path.write_text(json.dumps(self.generation_curve(profile)))
                with self.assertRaisesRegex(ValueError, "profile"):
                    acceptance.validate_generation_cost_evidence(
                        path, SHA, "c" * 64, expected_profile="capacity")
            complete = self.generation_curve("capacity")
            self.assertEqual([p["admitted_orders"] for p in complete["points"]],
                             [7168, 14336, 21504])
            path.write_text(json.dumps(complete))
            acceptance.validate_generation_cost_evidence(
                path, SHA, "c" * 64, expected_profile="capacity")
            for field, value in (("orderly_shutdown_verified", False),
                                 ("processes", []), ("configured_trade_calls_per_minute", 2048)):
                damaged = self.generation_curve("capacity")
                damaged[field] = value
                path.write_text(json.dumps(damaged))
                with self.assertRaises(ValueError):
                    acceptance.validate_generation_cost_evidence(
                        path, SHA, "c" * 64, expected_profile="capacity")
            damaged = self.generation_curve("capacity")
            damaged["points"][-1]["place_latency_total_samples"] = 1024
            path.write_text(json.dumps(damaged))
            with self.assertRaisesRegex(ValueError, "sampled workload"):
                acceptance.validate_generation_cost_evidence(
                    path, SHA, "c" * 64, expected_profile="capacity")

    def test_untracked_checkout_content_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("0.3.0\n")
            run, calls = self.fixture(root, untracked=True)
            with self.assertRaisesRegex(ValueError, "untracked files"):
                acceptance.accept(root / "build", root / "dist", SHA, root=root, run=run)
            self.assertTrue(any(c[:2] == ["git", "status"] for c in calls))
            self.assertFalse((root / "dist/core-acceptance.json").exists())


class ReferenceInstallTargetsTests(unittest.TestCase):
    def configure(self, root):
        source, build = root / "source", root / "build"
        source.mkdir()
        query = build / ".cmake/api/v1/query"
        query.mkdir(parents=True)
        (query / "codemodel-v2").touch()
        (source / "app.cpp").write_text("int main() { return 0; }\n")
        (source / "unused.cpp").write_text("#error unrelated historical target must not build\n")
        (source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\nproject(InstallSlice LANGUAGES CXX)\n"
            "add_executable(installed-app app.cpp)\n"
            "add_executable(uninstalled-test unused.cpp)\n"
            "install(TARGETS installed-app RUNTIME DESTINATION bin)\n")
        subprocess.run(["cmake", "-S", source, "-B", build, "-DCMAKE_BUILD_TYPE=Release"],
                       check=True, capture_output=True, timeout=60)
        return source, build

    def test_real_install_omits_unrelated_broken_test_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, build = self.configure(root)
            targets = acceptance.installed_executable_targets(source, build)
            self.assertEqual(targets, ["installed-app"])
            subprocess.run(["cmake", "--build", build, "--target", *targets],
                           check=True, capture_output=True, timeout=60)
            stage = root / "stage"
            subprocess.run(["cmake", "--install", build, "--prefix", stage],
                           check=True, capture_output=True, timeout=60)
            subprocess.run([stage / "bin/installed-app"], check=True, timeout=10)
            self.assertFalse((build / "uninstalled-test").exists())

    def test_new_installed_target_is_discovered_without_another_list(self):
        with tempfile.TemporaryDirectory() as directory:
            source, build = self.configure(Path(directory))
            with (source / "CMakeLists.txt").open("a") as stream:
                stream.write("add_executable(second-app app.cpp)\n"
                             "install(TARGETS second-app RUNTIME DESTINATION bin)\n")
            subprocess.run(["cmake", "-S", source, "-B", build],
                           check=True, capture_output=True, timeout=60)
            self.assertEqual(acceptance.installed_executable_targets(source, build),
                             ["installed-app", "second-app"])

    def test_missing_foreign_and_empty_models_cannot_skip_reference_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                acceptance.installed_executable_targets(root, root / "absent")
            source, build = self.configure(root)
            with self.assertRaisesRegex(ValueError, "source/build mismatch"):
                acceptance.installed_executable_targets(root / "foreign", build)
            reply = build / ".cmake/api/v1/reply"
            index = json.loads(next(reply.glob("index-*.json")).read_text())
            model_path = reply / index["reply"]["codemodel-v2"]["jsonFile"]
            model = json.loads(model_path.read_text())
            model["configurations"][0]["targets"] = []
            model_path.write_text(json.dumps(model))
            with self.assertRaisesRegex(ValueError, "no installed executable"):
                acceptance.installed_executable_targets(source, build)


if __name__ == "__main__":
    unittest.main()
