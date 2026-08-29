#!/usr/bin/env python3

"""Rootless contract tests for the explicit network-only rootful runner."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve(strict=True).parents[1]
MODULE_PATH = ROOT / "scripts/run_hepta_broker_network_rootful_gate.py"
SPEC = importlib.util.spec_from_file_location(
    "run_hepta_broker_network_rootful_gate_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot import broker network rootful runner")
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def pinned_reference() -> str:
    return "registry.example/hepta/network@sha256:" + "a" * 64


def valid_inner() -> dict[str, object]:
    return {
        "schema": RUNNER.INNER_SCHEMA,
        "passed": True,
        "checks": {
            name: True for name in sorted(RUNNER.EXPECTED_CHECKS)
        },
        "identities": copy.deepcopy(RUNNER.EXPECTED_IDENTITIES),
        "boundary": copy.deepcopy(RUNNER.EXPECTED_BOUNDARY),
    }


class BrokerNetworkRootfulRunnerFixture(unittest.TestCase):
    def test_digest_pinned_base_image_is_mandatory(self) -> None:
        reference = pinned_reference()
        self.assertEqual(RUNNER.require_pinned_image(reference), reference)
        for invalid in (
                "debian:bookworm",
                "debian@sha256:" + "a" * 63,
                "debian@sha256:" + "A" * 64,
                "sha256:" + "a" * 64):
            with self.subTest(invalid=invalid), self.assertRaises(
                    RUNNER.GateError):
                RUNNER.require_pinned_image(invalid)

    def test_context_is_an_exact_network_only_allowlist(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-broker-network-fixture-") as directory:
            context = Path(directory)
            records = RUNNER.stage_context(ROOT, context)
            self.assertEqual(set(records), set(RUNNER.STAGED_FILES))
            self.assertEqual(
                {item.name for item in context.iterdir()},
                {
                    target for target, _mode
                    in RUNNER.STAGED_FILES.values()
                })
            for source, (target, _mode) in RUNNER.STAGED_FILES.items():
                self.assertEqual(
                    (ROOT / source).read_bytes(),
                    (context / target).read_bytes())
        staged = "\n".join(RUNNER.STAGED_FILES)
        self.assertIn(
            "scripts/hepta_ib_paper_domain_authority.py",
            RUNNER.STAGED_FILES)
        for forbidden in (
                "hepta-execution-ib-paper",
                "credential",
                "tmpfiles.d",
                "ib_paper_execution"):
            self.assertNotIn(forbidden, staged)

    def test_docker_build_and_runtime_boundaries_are_exact(self) -> None:
        build = RUNNER.build_arguments(
            pinned_reference(), "hepta:test", Path("/context"),
            Path("/context/.iid"))
        self.assertEqual(build[0], "build")
        self.assertIn("--network", build)
        self.assertEqual(build[build.index("--network") + 1], "none")
        self.assertIn("--pull=false", build)
        self.assertIn("--no-cache", build)
        self.assertNotIn("--mount", build)

        create = RUNNER.create_arguments(
            "hepta:test", "hepta-test-container", "a" * 32)
        self.assertEqual(create[0], "create")
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertIn("--read-only", create)
        self.assertEqual(create[create.index("--cap-drop") + 1], "ALL")
        self.assertEqual(
            [create[index + 1] for index, value in enumerate(create)
             if value == "--cap-add"],
            list(RUNNER.RUNTIME_CAPABILITIES))
        self.assertEqual(
            create[create.index("--security-opt") + 1],
            "no-new-privileges")
        self.assertEqual(create[create.index("--pids-limit") + 1], "128")
        for forbidden in (
                "--volume", "-v", "--mount", "--publish", "-p",
                "--privileged", "/run/docker.sock", "/var/run/docker.sock"):
            self.assertNotIn(forbidden, create)

    def test_inner_result_is_exact_and_mutations_fail_closed(self) -> None:
        self.assertTrue({
            "broker_guard_detects_table_flush_and_tightens",
            "broker_guard_detects_manifest_replacement_and_tightens",
            "authority_guard_holds_lifetime_host_lease",
            "second_domain_rejected_while_first_guard_active",
            "foreign_domain_exec_stop_post_is_noop",
            "second_domain_guard_allowed_after_first_stops",
            "broker_exec_stop_post_revokes_all_after_sigkill",
            "authority_exec_stop_post_revokes_after_sigkill",
            "authority_sigkill_tombstone_blocks_competing_start",
            "authority_clean_stop_revokes_domain_preserves_broker_guard",
        }.issubset(RUNNER.EXPECTED_CHECKS))
        self.assertEqual(
            RUNNER.EXPECTED_BOUNDARY[
                "default_engaged_kill_switch_fixtures"], 2)
        valid = valid_inner()
        encoded = (
            RUNNER.INNER_MARKER +
            json.dumps(valid, sort_keys=True, separators=(",", ":")) +
            "\n")
        self.assertEqual(RUNNER.validate_inner(encoded), valid)
        mutations = []
        missing_check = copy.deepcopy(valid)
        missing_check["checks"].pop("fixed_only_default")
        mutations.append(missing_check)
        allowed_live = copy.deepcopy(valid)
        allowed_live["boundary"]["live_authorized"] = True
        mutations.append(allowed_live)
        extra = copy.deepcopy(valid)
        extra["unexpected"] = True
        mutations.append(extra)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(
                    RUNNER.GateError):
                RUNNER.validate_inner(
                    RUNNER.INNER_MARKER + json.dumps(mutation))
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_inner("noise\n" + encoded)

    def test_fixture_contains_no_broker_protocol_or_paper_runtime(self) -> None:
        gate = (
            ROOT / "tests/broker_network_rootful/"
            "hepta_broker_network_opt_in_gate.py").read_text(
                encoding="utf-8", errors="strict")
        dockerfile = (
            ROOT / "tests/broker_network_rootful/Dockerfile").read_text(
                encoding="utf-8", errors="strict")
        for forbidden in (
                "import ibapi", "placeOrder(", "reqIds(",
                "EClientSocket(", "trade.place_order"):
            self.assertNotIn(forbidden, gate)
        for forbidden in (
                "COPY --chown=0:0 hepta-ib-executiond",
                "COPY --chown=0:0 hepta-execution-ib-paper.service",
                "COPY --chown=0:0 credentials",
                "RUN systemd-tmpfiles"):
            self.assertNotIn(forbidden, dockerfile)
        self.assertIn("ENTRYPOINT", dockerfile)
        self.assertIn("network-only", gate)
        self.assertIn("default-engaged", gate)
        for required in (
                "replace_opt_in(",
                "\"delete\", \"table\", \"inet\"",
                "another host PAPER authority",
                "tighten_deny_all()",
                "\"--check-deny-all\"",
                "finalize_authority(",
                "authority_a.crash()"):
            self.assertIn(required, gate)


if __name__ == "__main__":
    unittest.main(verbosity=2)
