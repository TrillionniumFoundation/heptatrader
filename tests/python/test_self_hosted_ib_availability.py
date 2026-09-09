from __future__ import annotations

import copy
import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "self-hosted-ib-availability.yml"
IDENTITIES = ROOT / "systemd" / "hepta-service-identities-v1.json"
HOST_MAP = ROOT / "systemd" / "hepta-x230-paper-host-identity-map-v1.json"
REASON_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,80}\Z")


class SelfHostedIbAvailabilityWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.identities = json.loads(IDENTITIES.read_text(encoding="utf-8"))
        cls.host_map_raw = HOST_MAP.read_bytes()
        cls.host_map = json.loads(cls.host_map_raw)

    @staticmethod
    def _mapping_is_bound(mapping: dict, identities: dict) -> bool:
        try:
            logical = mapping["logical_execution_identity"]
            canonical = identities["identities"][logical["name"]]
            runtime = mapping["runtime_execution_identity"]
            runner = mapping["runtime_runner_identity"]
            return (
                mapping["schema"] == "hepta.x230-paper-host-identity-map.v1"
                and mapping["version"] == 1
                and mapping["host_boundary"] == "x230-ib-paper"
                and mapping["scope"] == "ib-paper-qualification-only"
                and mapping["live_authorized"] is False
                and logical == {
                    "name": "hepta-ib-exec",
                    "uid": canonical["uid"],
                    "gid": canonical["gid"],
                }
                and canonical["role"] == "ib-paper-execution-authority"
                and runtime == {
                    "name": "hepta-codex-ib",
                    "uid": 995,
                    "gid": 993,
                }
                and runner == {
                    "name": "hepta-actions-paper",
                    "uid": 994,
                    "gid": 992,
                }
                and len({logical["uid"], runtime["uid"], runner["uid"]}) == 3
            )
        except (KeyError, TypeError):
            return False

    def test_workflow_is_dispatch_only(self) -> None:
        self.assertIn("on:\n  workflow_dispatch:", self.workflow)
        self.assertNotIn("pull_request", self.workflow)
        self.assertNotIn("pull_request_target", self.workflow)
        self.assertNotIn("repository_dispatch", self.workflow)
        self.assertNotIn("schedule:", self.workflow)
        self.assertNotIn("push:", self.workflow)

    def test_dispatch_input_is_only_transferred_through_environment(self) -> None:
        expression = "${{ inputs.reason }}"
        self.assertEqual(self.workflow.count(expression), 1)
        self.assertIn(f"PROBE_REASON: {expression}", self.workflow)
        self.assertNotIn(f"'{expression}'", self.workflow)
        self.assertNotIn(f'"{expression}"', self.workflow)

    def test_broker_host_job_is_group_bound_tokenless_and_checkout_free(self) -> None:
        section = self.workflow.split("  ib-runner-probe:\n", 1)[1]
        self.assertIn("if: github.event_name == 'workflow_dispatch'", section)
        selector = (
            "runs-on:\n"
            "      group: trillionnium-ib-paper\n"
            "      labels: [self-hosted, linux, x64, x230-ib-paper]"
        )
        self.assertEqual(section.count(selector), 1)
        self.assertEqual(section.count("x230-ib-paper"), 2)
        self.assertNotIn("heptatrader-ib-builder", section)
        self.assertNotIn("heptatrader-ib-paper", section)
        self.assertIn("permissions: {}", section)
        self.assertIn('test "$GITHUB_REF" = refs/heads/main', section)
        self.assertIn('test "$RUNNER_NAME" = x230-ib-paper', section)
        self.assertIn("! nc -z -w 3 127.0.0.1 4002", section)
        self.assertIn("/usr/libexec/hepta-ib-paper-host-probe", section)
        self.assertIn('test "$RUNNER_OS" = Linux', section)
        self.assertIn('test "$RUNNER_ARCH" = X64', section)
        self.assertNotIn("actions/checkout", section)
        self.assertNotIn("uses:", section)
        self.assertNotIn("secrets.", section)
        self.assertNotIn("GITHUB_TOKEN", section)

    def test_reason_is_quoted_and_allowlisted_before_output(self) -> None:
        self.assertIn(
            '[[ "$PROBE_REASON" =~ ^[A-Za-z0-9._:-]{1,80}$ ]]',
            self.workflow,
        )
        self.assertIn('"$PROBE_REASON"', self.workflow)

        for value in (
            "operator-check",
            "current-readiness-20260906",
            "ops.probe:v1_2",
        ):
            with self.subTest(value=value):
                self.assertIsNotNone(REASON_PATTERN.fullmatch(value))

        for value in (
            "",
            "contains space",
            "ok'; id; #",
            "line1\nline2",
            "$(id)",
            "`id`",
            "a" * 81,
        ):
            with self.subTest(value=value):
                self.assertIsNone(REASON_PATTERN.fullmatch(value))

    def test_probe_is_read_only_and_local(self) -> None:
        self.assertGreaterEqual(self.workflow.count("permissions: {}"), 2)
        self.assertIn("127.0.0.1 4002", self.workflow)
        self.assertNotIn("placeOrder", self.workflow)
        self.assertNotIn("cancelOrder", self.workflow)
        self.assertNotIn("HEPTA_QUALIFICATION_MUTATIONS", self.workflow)

    def test_probe_binds_reviewed_logical_and_host_execution_identities(self) -> None:
        digest = hashlib.sha256(self.host_map_raw).hexdigest()
        self.assertTrue(self._mapping_is_bound(self.host_map, self.identities))
        self.assertIn("--logical-execution-uid 2003", self.workflow)
        self.assertIn("--execution-uid 995", self.workflow)
        self.assertEqual(self.workflow.count(digest), 1)
        self.assertIn("--identity-map-sha256 " + digest, self.workflow)

    def test_host_identity_mapping_fails_closed_on_hostile_mismatch(self) -> None:
        mutations = []
        for path, value in (
            (("logical_execution_identity", "uid"), 995),
            (("logical_execution_identity", "gid"), 993),
            (("runtime_execution_identity", "uid"), 2003),
            (("runtime_runner_identity", "uid"), 995),
            (("live_authorized",), True),
            (("scope",), "all-broker-operations"),
        ):
            hostile = copy.deepcopy(self.host_map)
            target = hostile
            for component in path[:-1]:
                target = target[component]
            target[path[-1]] = value
            mutations.append(hostile)
        for hostile in mutations:
            with self.subTest(hostile=hostile):
                self.assertFalse(self._mapping_is_bound(hostile, self.identities))


if __name__ == "__main__":
    unittest.main()
