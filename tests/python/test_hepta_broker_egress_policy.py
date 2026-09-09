from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "hepta_broker_egress_policy",
    ROOT / "scripts" / "hepta_broker_egress_policy.py",
)
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class BrokerEgressRulesetTests(unittest.TestCase):
    def render(self, *, deny_all: bool) -> str:
        return POLICY._ruleset(  # noqa: SLF001 - exact policy unit seam
            "inet",
            "hepta_broker_egress_v1",
            "output",
            (4001, 4002, 7496, 7497),
            (995,),
            deny_all=deny_all,
            replace_existing=False,
        ).decode("ascii")

    def test_apply_is_loopback_only_and_uid_scoped(self) -> None:
        rules = self.render(deny_all=False)
        self.assertIn(
            "ip daddr 127.0.0.0/8 tcp dport { 4001, 4002, 7496, 7497 } "
            "meta skuid { 995 } accept",
            rules,
        )
        self.assertIn(
            "ip6 daddr ::1 tcp dport { 4001, 4002, 7496, 7497 } "
            "meta skuid { 995 } accept",
            rules,
        )
        self.assertEqual(rules.count("reject with tcp reset"), 2)
        self.assertNotIn(
            "output tcp dport { 4001, 4002, 7496, 7497 }",
            rules,
        )

    def test_deny_all_retains_both_loopback_families(self) -> None:
        rules = self.render(deny_all=True)
        self.assertNotIn("meta skuid", rules)
        self.assertIn("ip daddr 127.0.0.0/8", rules)
        self.assertIn("ip6 daddr ::1", rules)
        self.assertEqual(rules.count("reject with tcp reset"), 2)


if __name__ == "__main__":
    unittest.main()
