from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "systemd" / "hepta-broker-network-policy-v1.json"
IDENTITIES_PATH = ROOT / "systemd" / "hepta-service-identities-v1.json"
HOST_MAP_PATH = ROOT / "systemd" / "hepta-x230-paper-host-identity-map-v1.json"
WORKFLOW_PATH = (
    ROOT / ".github" / "workflows" / "self-hosted-ib-availability.yml"
)
SPEC = importlib.util.spec_from_file_location(
    "hepta_broker_egress_policy",
    ROOT / "scripts" / "hepta_broker_egress_policy.py",
)
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class BrokerEgressRulesetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy_value = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        cls.identities = json.loads(
            IDENTITIES_PATH.read_text(encoding="utf-8")
        )
        cls.host_map_raw = HOST_MAP_PATH.read_bytes()
        cls.host_map = json.loads(cls.host_map_raw)
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.validated = POLICY._validate_policy(  # noqa: SLF001
            POLICY._read_policy(POLICY_PATH)  # noqa: SLF001
        )

    @staticmethod
    def render(
        *,
        uids: tuple[int, ...],
        deny_all: bool,
    ) -> str:
        return POLICY._ruleset(  # noqa: SLF001 - exact policy unit seam
            "inet",
            "hepta_broker_egress_v1",
            "output",
            (4001, 4002, 7496, 7497),
            uids,
            deny_all=deny_all,
            replace_existing=False,
        ).decode("ascii")

    def test_canonical_policy_binds_the_logical_execution_identity(self) -> None:
        family, table, chain, ports, uids = self.validated
        logical = self.host_map["logical_execution_identity"]
        canonical = self.identities["identities"][logical["name"]]
        self.assertEqual(family, "inet")
        self.assertEqual(table, "hepta_broker_egress_v1")
        self.assertEqual(chain, "output")
        self.assertEqual(ports, (4001, 4002, 7496, 7497))
        self.assertEqual(uids, (2003,))
        self.assertEqual(self.policy_value["authorized_uids"], [2003])
        self.assertEqual(
            logical,
            {
                "name": "hepta-ib-exec",
                "uid": canonical["uid"],
                "gid": canonical["gid"],
            },
        )
        self.assertEqual(canonical["role"], "ib-paper-execution-authority")

    def test_x230_host_mapping_is_distinct_digest_bound_and_non_live(self) -> None:
        logical = self.host_map["logical_execution_identity"]
        execution = self.host_map["runtime_execution_identity"]
        runner = self.host_map["runtime_runner_identity"]
        self.assertEqual(self.host_map["scope"], "ib-paper-qualification-only")
        self.assertIs(self.host_map["live_authorized"], False)
        self.assertEqual(execution["name"], "hepta-codex-ib")
        self.assertEqual(execution["uid"], 995)
        self.assertEqual(runner["name"], "hepta-actions-paper")
        self.assertEqual(runner["uid"], 994)
        self.assertEqual(len({logical["uid"], execution["uid"], runner["uid"]}), 3)
        digest = hashlib.sha256(self.host_map_raw).hexdigest()
        self.assertEqual(self.workflow.count(digest), 1)
        self.assertIn("--identity-map-sha256 " + digest, self.workflow)
        self.assertIn("--logical-execution-uid 2003", self.workflow)
        self.assertIn("--execution-uid 995", self.workflow)
        self.assertIn('! nc -z -w 3 127.0.0.1 4002', self.workflow)

    def test_canonical_apply_is_loopback_only_and_uid_scoped(self) -> None:
        rules = self.render(uids=(2003,), deny_all=False)
        self.assertIn(
            "ip daddr 127.0.0.0/8 tcp dport { 4001, 4002, 7496, 7497 } "
            "meta skuid { 2003 } accept",
            rules,
        )
        self.assertIn(
            "ip6 daddr ::1 tcp dport { 4001, 4002, 7496, 7497 } "
            "meta skuid { 2003 } accept",
            rules,
        )
        self.assertEqual(rules.count("reject with tcp reset"), 2)
        self.assertNotIn(
            "output tcp dport { 4001, 4002, 7496, 7497 }",
            rules,
        )

    def test_host_policy_renderer_allows_only_mapped_execution_uid(self) -> None:
        execution_uid = self.host_map["runtime_execution_identity"]["uid"]
        runner_uid = self.host_map["runtime_runner_identity"]["uid"]
        rules = self.render(uids=(execution_uid,), deny_all=False)
        self.assertIn("meta skuid { 995 } accept", rules)
        self.assertNotIn("meta skuid { 2003 } accept", rules)
        self.assertNotIn("meta skuid { 994 } accept", rules)
        self.assertNotEqual(execution_uid, runner_uid)

    def test_deny_all_retains_both_loopback_families(self) -> None:
        rules = self.render(uids=(2003,), deny_all=True)
        self.assertNotIn("meta skuid", rules)
        self.assertIn("ip daddr 127.0.0.0/8", rules)
        self.assertIn("ip6 daddr ::1", rules)
        self.assertEqual(rules.count("reject with tcp reset"), 2)


if __name__ == "__main__":
    unittest.main()
