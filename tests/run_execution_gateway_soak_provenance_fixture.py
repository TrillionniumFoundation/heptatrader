#!/usr/bin/env python3

"""Offline regression fixtures for the execution Gateway soak provenance."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import run_execution_gateway_soak as soak  # noqa: E402
import run_hepta_broker_network_hard_isolation_gate as hard_runner  # noqa: E402
import run_hepta_broker_network_rootful_gate as broker_runner  # noqa: E402
import run_hepta_p1_dual_domain_rootful_gate as dual_runner  # noqa: E402
import run_hepta_p1_campaign_rootful_liveness_gate as liveness_runner  # noqa: E402
import run_hepta_paper_domain_rootful_systemd_gate as paper_runner  # noqa: E402


EVIDENCE_PREFIX = "fixture_evidence:"


def cmake_cache(
        source_root: Path,
        build_root: Path,
        *,
        omit: str = "",
) -> str:
    entries = {
        "CMAKE_HOME_DIRECTORY": ("INTERNAL", str(source_root)),
        "CMAKE_CACHEFILE_DIR": ("INTERNAL", str(build_root)),
        "CMAKE_BUILD_TYPE": ("STRING", "Release"),
        "BUILD_TESTING": ("BOOL", "ON"),
        "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE": ("BOOL", "OFF"),
        "HEPTA_BUILD_LEGACY_MONOLITH": ("BOOL", "OFF"),
        "HEPTA_BUILD_LEGACY_SIMULATOR": ("BOOL", "OFF"),
        "HEPTA_ENABLE_IBAPI": ("BOOL", "OFF"),
        "CMAKE_GENERATOR": ("INTERNAL", "Unix Makefiles"),
        "CMAKE_CXX_COMPILER": ("FILEPATH", "/usr/bin/c++"),
    }
    return "".join(
        f"{key}:{kind}={value}\n"
        for key, (kind, value) in entries.items()
        if key != omit
    )


class ExecutionGatewaySoakProvenanceFixtureTests(unittest.TestCase):
    def source_bundle_fixture(
            self, root: Path, *, agent_os: bool) -> dict:
        source = root / "source.txt"
        source.write_text("fixture\n", encoding="utf-8")
        record = soak.stable_file_snapshot(root, "source.txt")[0]
        expected = {
            "path": record["path"],
            "mode": "0755" if int(record["mode"], 8) & 0o100 else "0644",
            "size": record["size"],
            "sha256": record["sha256"].removeprefix("sha256:"),
        }
        canonical = json.dumps(
            [expected], ensure_ascii=True, separators=(",", ":"),
            sort_keys=True).encode()
        manifest = {
            "root": root.name,
            "file_count": 1,
            "files": [expected],
            "files_sha256": hashlib.sha256(canonical).hexdigest(),
            "paper_authorized": False,
            "live_authorized": False,
        }
        if agent_os:
            manifest.update({
                "schema": "hepta.agent-os-source-bundle.v1",
                "bundle_class": "agent-os-source-only",
                "parent_strict_source": {
                    "schema": "hepta.clean-source-bundle.v2",
                    "git_head": "2" * 40,
                },
            })
            relative = ".hepta/agent-os-source-manifest.json"
        else:
            manifest.update({
                "schema": "hepta.clean-source-bundle.v2",
                "bundle_class": "strict-source-only",
                "git_head": "1" * 40,
                "excluded_legacy_runtime_tree": "Tools",
                "prebuilt_payload_included": False,
            })
            relative = ".hepta/source-bundle-manifest.json"
        marker = root / relative
        marker.parent.mkdir()
        marker.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return manifest

    def test_strict_and_agent_source_provenance_are_verified(self) -> None:
        for agent_os, expected_head in ((False, "1" * 40), (True, "2" * 40)):
            with self.subTest(agent_os=agent_os):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-soak-source-") as temporary:
                    root = Path(temporary).resolve()
                    self.source_bundle_fixture(root, agent_os=agent_os)
                    report = soak.validated_bundle_provenance(root)
                    self.assertEqual(report["git_head"], expected_head)
                    self.assertEqual(report["file_count"], 1)
                    if agent_os:
                        source = soak.source_manifest(root)
                        self.assertEqual(source["file_count"], 1)
                        self.assertEqual(
                            source["files"][0]["path"], "source.txt")

    def test_per_domain_execution_units_are_source_bound(self) -> None:
        manifest = soak.source_manifest(REPOSITORY)
        paths = {record["path"] for record in manifest["files"]}
        self.assertTrue({
            "systemd/hepta-execution-events-ib-paper@.socket",
            "systemd/hepta-execution-events-simulator@.socket",
            "systemd/hepta-execution-ib-paper@.service",
            "systemd/hepta-execution-ib-paper@.socket",
            "systemd/hepta-execution-simulator@.service",
            "systemd/hepta-execution-simulator@.socket",
        }.issubset(paths))

    def test_broker_rootful_staged_files_are_source_bound(self) -> None:
        manifest = soak.source_manifest(REPOSITORY)
        paths = {record["path"] for record in manifest["files"]}
        self.assertTrue(set(broker_runner.STAGED_FILES).issubset(paths))

    def test_p1_admission_and_dual_domain_inputs_are_source_bound(self) -> None:
        manifest = soak.source_manifest(REPOSITORY)
        paths = {record["path"] for record in manifest["files"]}
        self.assertTrue(set(dual_runner.SOURCE_FILES).issubset(paths))
        self.assertTrue(set(liveness_runner.SOURCE_FILES).issubset(paths))
        self.assertTrue(set(paper_runner.SOURCE_FILES).issubset(paths))
        self.assertTrue({
            "scripts/hepta_p1_safety_soak_policy_planner.py",
            "scripts/hepta_p1_safety_soak_campaign_freezer.py",
            "scripts/hepta_p1_safety_soak_campaign_coordinator.py",
            "scripts/hepta_p1_safety_soak_observer_worker.py",
            "scripts/hepta_p1_safety_soak_recorder_worker.py",
            "scripts/hepta_p1_safety_soak_fault_pin_producer.py",
            "scripts/hepta_p1_safety_soak_evidence_recorder.py",
            "scripts/hepta_p1_safety_soak_independent_observer.py",
            "scripts/hepta_p1_safety_soak_root_fault_injector.py",
            "scripts/hepta_p1_safety_soak_auditor.py",
            "scripts/hepta_p1_watch_to_paper_handoff.py",
            "scripts/hepta_p1_paper_zero_exposure_snapshot_producer.py",
            "scripts/hepta_p1_paper_zero_exposure_attestor.py",
            "scripts/hepta_p1_paper_admission_verifier.py",
            "scripts/hepta_p1_paper_kill_switch_bootstrap.py",
            "scripts/build_heptatrader_release_validation_closure.py",
            "scripts/verify_heptatrader_release_validation_closure.py",
            "scripts/hepta_rootful_review_closure_consumer.py",
            "scripts/hepta_rootful_systemd_environment_provenance.py",
            "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py",
            "scripts/run_hepta_broker_network_hard_isolation_gate.py",
            "systemd/hepta-p1-safety-soak-campaign@.service",
            "systemd/hepta-p1-safety-soak-observer-worker@.service",
            "systemd/hepta-p1-safety-soak-recorder-worker@.service",
            "systemd/hepta-p1-safety-soak@.target",
            "systemd/hepta-systemd-gate.apparmor",
            "tests/hepta_p1_safety_soak_policy_planner_tests.py",
            "tests/hepta_p1_safety_soak_campaign_coordinator_tests.py",
            "tests/hepta_p1_safety_soak_fault_pin_producer_tests.py",
            "tests/hepta_p1_safety_soak_evidence_recorder_tests.py",
            "tests/hepta_p1_safety_soak_independent_observer_tests.py",
            "tests/hepta_p1_safety_soak_root_fault_injector_tests.py",
            "tests/hepta_p1_safety_soak_auditor_tests.py",
            "tests/hepta_p1_watch_to_paper_handoff_tests.py",
            "tests/hepta_p1_paper_zero_exposure_snapshot_producer_tests.py",
            "tests/hepta_p1_paper_zero_exposure_attestor_tests.py",
            "tests/hepta_p1_paper_admission_verifier_tests.py",
            "tests/hepta_rootful_review_closure_consumer_tests.py",
            "tests/hepta_rootful_systemd_environment_provenance_tests.py",
            "tests/hepta_rootful_systemd_base_tests.py",
            "tests/hepta_systemd_gate_apparmor_tests.py",
            "tests/run_hepta_broker_network_hard_isolation_gate_fixture.py",
            "tests/run_hepta_paper_domain_rootful_systemd_gate_fixture.py",
            "tests/run_hepta_p1_dual_domain_rootful_gate_fixture.py",
            "tests/run_hepta_p1_campaign_rootful_liveness_gate_fixture.py",
            "tests/rootful_systemd_base/Dockerfile",
        }.issubset(paths))
        self.assertEqual(
            hard_runner.BOUNDARY["paper_authorized"], False)
        self.assertEqual(
            hard_runner.BOUNDARY["live_authorized"], False)
        self.assertEqual(
            liveness_runner.BOUNDARY["paper_authorized"], False)
        self.assertEqual(
            liveness_runner.BOUNDARY["live_authorized"], False)

    def test_shadow_exporter_artifacts_are_source_bound(self) -> None:
        manifest = soak.source_manifest(REPOSITORY)
        paths = {record["path"] for record in manifest["files"]}
        self.assertTrue({
            "scripts/hepta_shadow_watch_exporter.py",
            "systemd/hepta-shadow-watch-domain.env.example",
            "systemd/hepta-shadow-watch-export@.service",
        }.issubset(paths))

    def test_p1_verifier_semantic_closure_is_source_bound(self) -> None:
        manifest = soak.source_manifest(REPOSITORY)
        paths = {record["path"] for record in manifest["files"]}
        self.assertTrue({
            "scripts/hepta_bounded_shadow_closure_verifier.py",
            "scripts/hepta_bounded_shadow_observer.py",
            "scripts/hepta_market_context_builder.py",
            "scripts/hepta_market_evidence_normalizer.py",
            "scripts/hepta_market_official_source_extractor.py",
            "scripts/hepta_eurusd_confirmed_momentum_strategy.py",
            "scripts/hepta_shadow_market_history.py",
            "scripts/hepta_strategy_shadow_runner.py",
            "scripts/hepta_strategy_contracts.py",
            "scripts/validate_hepta_strategy_decision_receipt.py",
            "scripts/hepta_official_source_capture.py",
            "strategies/eurusd-confirmed-momentum-shadow-v2.json",
            "tests/hepta_bounded_shadow_closure_verifier_tests.py",
        }.issubset(paths))

    def test_agent_source_policy_is_repository_baseline_bound(self) -> None:
        manifest = soak.source_manifest(REPOSITORY)
        paths = {record["path"] for record in manifest["files"]}
        policy = json.loads((
            REPOSITORY /
            "policies/heptatrader-agent-os-source-v2.json"
        ).read_text(encoding="utf-8", errors="strict"))
        for field in ("required_files", "include_files"):
            selected = set(policy[field])
            self.assertEqual(
                selected - paths,
                set(),
                f"{field} contains files absent from the repository "
                "source baseline",
            )

    def test_ambiguous_source_markers_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-soak-source-") as temporary:
            root = Path(temporary).resolve()
            self.source_bundle_fixture(root, agent_os=False)
            strict = root / ".hepta/source-bundle-manifest.json"
            agent = root / ".hepta/agent-os-source-manifest.json"
            agent.write_bytes(strict.read_bytes())
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                soak.validated_bundle_provenance(root)

    def test_no_git_source_does_not_inherit_parent_repository_head(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-soak-parent-git-") as temporary:
            parent = Path(temporary).resolve()
            subprocess.run(["git", "init", "-q"], cwd=parent, check=True)
            subprocess.run(
                ["git", "config", "user.name", "Hepta Test"],
                cwd=parent, check=True)
            subprocess.run(
                ["git", "config", "user.email", "hepta@example.invalid"],
                cwd=parent, check=True)
            (parent / "parent.txt").write_text(
                "parent\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=parent, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "parent"], cwd=parent, check=True)
            root = parent / "agent-source"
            root.mkdir()
            self.source_bundle_fixture(root, agent_os=True)
            self.assertEqual(soak.git_head(root), "2" * 40)

    def test_duplicate_evidence_is_found_outside_the_output_tail(self) -> None:
        full_output = (
            f"{EVIDENCE_PREFIX} result=verified\n"
            + "x" * (soak.OUTPUT_TAIL_BYTES + 257)
            + f"\n{EVIDENCE_PREFIX} result=verified\n"
        )

        fields, error, line_count = soak.parse_machine_evidence(
            full_output, EVIDENCE_PREFIX)

        self.assertEqual(fields, {})
        self.assertEqual(error, "duplicate_lines")
        self.assertEqual(line_count, 2)

    def test_unexpected_evidence_field_fails_the_exact_contract(self) -> None:
        expected = {"result": "verified"}
        fields, error, line_count = soak.parse_machine_evidence(
            f"{EVIDENCE_PREFIX} result=verified extra=unexpected\n",
            EVIDENCE_PREFIX,
        )
        unexpected = sorted(set(fields) - set(expected))
        contract_satisfied = (
            line_count == 1
            and not error
            and set(fields) == set(expected)
            and all(fields[key] == value for key, value in expected.items())
        )

        self.assertEqual(error, "")
        self.assertEqual(unexpected, ["extra"])
        self.assertFalse(contract_satisfied)

    def test_recovery_owner_evidence_is_in_the_exact_contract(self) -> None:
        contract = next(
            item for item in soak.SOAK_EVIDENCE_CONTRACTS
            if item["prefix"] ==
            "ib_paper_reconcile_fault_matrix_evidence:")

        self.assertEqual(
            contract["fields"]["recovery_owner_exact_scope"], "verified")
        self.assertEqual(
            contract["fields"][
                "recovery_owner_unmapped_and_uncertain_rejected"],
            "verified",
        )
        self.assertIs(
            soak.SOAK_EXPECTED_INVARIANTS[
                "ib_paper_recovery_owner_exact_scope"],
            True,
        )
        self.assertIs(
            soak.SOAK_EXPECTED_INVARIANTS[
                "ib_paper_recovery_owner_unmapped_and_uncertain_rejected"],
            True,
        )

    def test_contract_binding_evidence_is_v11_and_exact(self) -> None:
        self.assertEqual(
            soak.SOAK_SCHEMA, "hepta.execution-gateway-soak.v11")
        contract = next(
            item for item in soak.SOAK_EVIDENCE_CONTRACTS
            if item["prefix"] ==
            "ib_authoritative_fault_matrix_evidence:")
        fields = contract["fields"]
        self.assertEqual(
            tuple(fields),
            (
                "stale_connection_epoch_drop",
                "queue_overflow_resync_latch",
                "positions_multi_stale_end_fence",
                "market_data_admission_state",
                "cash_farm_marker_epoch_sequence",
                "event_queue_try_push_drop",
                "active_duplicate_conflict",
                "active_incremental_conflict",
                "terminal_invalid_evidence",
                "terminal_overflow_fail_closed",
                "risk_snapshot_fail_closed",
                "contract_binding_fail_closed",
                "reduce_only_send_revalidation",
            ),
        )
        self.assertEqual(
            fields["contract_binding_fail_closed"], "verified")
        self.assertIs(
            soak.SOAK_EXPECTED_INVARIANTS[
                "ib_paper_contract_binding_fails_closed"],
            True,
        )

        prefix = contract["prefix"]
        output = prefix + " " + " ".join(
            f"{key}={value}" for key, value in fields.items()) + "\n"
        accepted = soak.evaluate_evidence_contract(output, contract)
        self.assertTrue(accepted.satisfied)
        self.assertEqual(accepted.fields, fields)

        omitted_output = prefix + " " + " ".join(
            f"{key}={value}" for key, value in fields.items()
            if key != "contract_binding_fail_closed") + "\n"
        omitted = soak.evaluate_evidence_contract(
            omitted_output, contract)
        self.assertFalse(omitted.satisfied)
        self.assertEqual(
            omitted.missing_fields, ["contract_binding_fail_closed"])

        forged_output = output.replace(
            "contract_binding_fail_closed=verified",
            "contract_binding_fail_closed=forged",
        )
        forged = soak.evaluate_evidence_contract(forged_output, contract)
        self.assertFalse(forged.satisfied)
        self.assertEqual(
            forged.mismatched_fields["contract_binding_fail_closed"],
            {"expected": "verified", "observed": "forged"},
        )

        duplicated_output = output.replace(
            "contract_binding_fail_closed=verified",
            "contract_binding_fail_closed=verified "
            "contract_binding_fail_closed=verified",
        )
        duplicated = soak.evaluate_evidence_contract(
            duplicated_output, contract)
        self.assertFalse(duplicated.satisfied)
        self.assertEqual(duplicated.parse_error, "duplicate_field")

        contradictory_output = output.rstrip() + (
            " contract_binding_fail_closed_claim=forged\n")
        contradictory = soak.evaluate_evidence_contract(
            contradictory_output, contract)
        self.assertFalse(contradictory.satisfied)
        self.assertEqual(
            contradictory.unexpected_fields,
            ["contract_binding_fail_closed_claim"],
        )

    def test_current_soak_documentation_is_v11_and_nine_binary(self) -> None:
        count_words = {
            word: value
            for value, word in enumerate((
                "zero", "one", "two", "three", "four", "five",
                "six", "seven", "eight", "nine", "ten",
            ))
        }

        def require_exact_contract(section: str) -> None:
            self.assertEqual(
                re.findall(
                    r"hepta\.execution-gateway-soak\.v[0-9]+", section),
                [soak.SOAK_SCHEMA],
            )
            count_tokens = re.findall(
                r"\b(?:repeats|executes|uses)\s+"
                r"([0-9]+|[A-Za-z-]+)\s+"
                r"(?:suites?|binaries|inputs)\b",
                section,
            )
            counts = [
                int(token) if token.isdigit() else count_words.get(token, -1)
                for token in count_tokens
            ]
            counts.extend(
                int(value)
                for value in re.findall(r"每轮运行\s*([0-9]+)\s*个", section)
            )
            self.assertEqual(counts, [len(soak.SOAK_BINARY_NAMES)])

        architecture = (
            REPOSITORY / "docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md"
        ).read_text(encoding="utf-8", errors="strict")
        architecture_start = "`scripts/run_execution_gateway_soak.py` repeats"
        architecture_end = "## Production Cutover Boundary"
        self.assertEqual(architecture.count(architecture_start), 1)
        self.assertEqual(architecture.count(architecture_end), 1)
        architecture_contract = architecture_start + architecture.split(
            architecture_start, 1)[1].split(architecture_end, 1)[0]
        require_exact_contract(architecture_contract)
        architecture_current_start = "The current combined offline soak is"
        architecture_current_end = "The distributable archive is"
        self.assertEqual(architecture.count(architecture_current_start), 1)
        self.assertEqual(architecture.count(architecture_current_end), 1)
        architecture_current_contract = architecture.split(
            architecture_current_start, 1
        )[1].split(architecture_current_end, 1)[0]
        require_exact_contract(architecture_current_contract)

        runbook = (
            REPOSITORY / "docs/RUNBOOK-STARTUP.md"
        ).read_text(encoding="utf-8", errors="strict")
        runbook_start = "当前 combined soak schema"
        runbook_end = "## 0. 历史记录：Round18"
        self.assertEqual(runbook.count(runbook_start), 1)
        self.assertEqual(runbook.count(runbook_end), 1)
        current_contract = runbook.split(
            runbook_start, 1)[1].split(runbook_end, 1)[0]
        require_exact_contract(current_contract)
        historical_heading = runbook.split(runbook_end, 1)[1].split(
            "\n", 1)[0]
        self.assertIn("当前认证禁用", historical_heading)

        contradictions = (
            (
                architecture_contract,
                "\nThe current schema is "
                "hepta.execution-gateway-soak.v12. It executes eleven "
                "suites.\n",
            ),
            (
                architecture_current_contract,
                "\nThe current schema is "
                "hepta.execution-gateway-soak.v12. It executes 8 "
                "binaries.\n",
            ),
            (
                current_contract,
                "\n当前 schema 是 hepta.execution-gateway-soak.v12；"
                "每轮运行 10 个 binaries。\n",
            ),
        )
        for section, contradiction in contradictions:
            with self.assertRaises(AssertionError):
                require_exact_contract(section + contradiction)

    def test_diagnostic_tail_redacts_accounts_tokens_and_local_paths(self) -> None:
        repository_root = Path("/srv/heptatrader/source")
        raw = (
            "paper_account=DU123456 account=private-account "
            "token=private-token standalone=DU987654 "
            f"repo={repository_root}/runtime-logs/private.json "
            "home=/home/operator/.config/hepta/secret "
            "temp=/tmp/hepta-private/account.json"
        )

        redacted = soak.redact_output_tail(raw, repository_root)

        for secret in (
            "DU123456",
            "DU987654",
            "private-account",
            "private-token",
            str(repository_root),
            "/home/operator/.config/hepta/secret",
            "/tmp/hepta-private/account.json",
        ):
            self.assertNotIn(secret, redacted)
        self.assertLessEqual(len(redacted), soak.OUTPUT_TAIL_BYTES)
        self.assertIn("<redacted>", redacted)
        self.assertIn("<local-path>", redacted)

    def test_stable_snapshot_rejects_final_and_ancestor_symlinks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-soak-snapshot-") as temporary:
            root = Path(temporary).resolve()
            real_directory = root / "real"
            real_directory.mkdir()
            (real_directory / "input.txt").write_text(
                "fixture\n", encoding="utf-8")
            (root / "final-link").symlink_to("real/input.txt")
            (root / "ancestor-link").symlink_to("real", target_is_directory=True)

            with self.assertRaisesRegex(
                    RuntimeError, "cannot securely snapshot evidence input"):
                soak.stable_file_snapshot(root, "final-link")
            with self.assertRaisesRegex(
                    RuntimeError, "cannot securely snapshot evidence input"):
                soak.stable_file_snapshot(root, "ancestor-link/input.txt")

    def test_build_cache_rejects_foreign_source_and_missing_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-soak-cache-") as temporary:
            fixture = Path(temporary).resolve()
            root = fixture / "repository"
            build = root / "build"
            foreign = fixture / "foreign-repository"
            build.mkdir(parents=True)
            build.chmod(0o700)
            foreign.mkdir()
            cache = build / "CMakeCache.txt"
            location = soak.build_location(root, "build")

            cache.write_text(cmake_cache(root, build), encoding="utf-8")
            accepted = soak.validate_build_cache(
                root, location, "Release")
            self.assertEqual(accepted["build_type"], "Release")

            cache.write_text(cmake_cache(foreign, build), encoding="utf-8")
            with self.assertRaisesRegex(
                    RuntimeError, "foreign CMake source root"):
                soak.validate_build_cache(root, location, "Release")

            cache.write_text(
                cmake_cache(root, build, omit="BUILD_TESTING"),
                encoding="utf-8")
            with self.assertRaisesRegex(
                    RuntimeError,
                    "required CMake cache key is missing: BUILD_TESTING"):
                soak.validate_build_cache(root, location, "Release")

            cache.write_text(
                cmake_cache(root, foreign), encoding="utf-8")
            with self.assertRaisesRegex(
                    RuntimeError, "foreign build root"):
                soak.validate_build_cache(root, location, "Release")

    def test_external_build_is_anchored_without_leaking_absolute_path(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-soak-external-") as temporary:
            fixture = Path(temporary).resolve()
            root = fixture / "repository"
            build = fixture / "state/repository-ibapi-off"
            root.mkdir()
            build.mkdir(parents=True)
            build.chmod(0o700)
            (build / "CMakeCache.txt").write_text(
                cmake_cache(root, build), encoding="utf-8")

            location = soak.build_location(root, str(build))
            accepted = soak.validate_build_cache(
                root, location, "Release")

            self.assertEqual(location.path, build)
            self.assertEqual(location.anchor, build.parent)
            self.assertEqual(location.relative, build.name)
            self.assertEqual(location.logical, "build-tree")
            self.assertEqual(
                accepted["cmake_cache"]["path"],
                "build-tree/CMakeCache.txt")
            self.assertNotIn(str(fixture), json.dumps(accepted))

    def test_report_must_be_a_direct_regular_child_of_build(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-soak-report-") as temporary:
            fixture = Path(temporary).resolve()
            root = fixture / "repository"
            build = fixture / "state/build"
            root.mkdir()
            build.mkdir(parents=True)
            build.chmod(0o700)
            location = soak.build_location(root, str(build))

            report, logical = soak.report_location(
                root, location, str(build / "soak.json"))
            self.assertEqual(report, build / "soak.json")
            self.assertEqual(logical, "build-tree/soak.json")

            (root / "runtime-logs").mkdir()
            with self.assertRaisesRegex(
                    RuntimeError, "direct child"):
                soak.report_location(
                    root, location, str(root / "runtime-logs/soak.json"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
