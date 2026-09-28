from __future__ import annotations

import copy
import json
import subprocess
import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import plan_owner_ruleset as planner


class OwnerRulesetPlanTests(unittest.TestCase):
    def fixture(self):
        return {
            "id": planner.RULESET_ID,
            "source": planner.REPOSITORY,
            "target": "branch",
            "name": "heptatrader-main-governance-v1",
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
            ],
        }

    def test_minimal_current_baseline_adds_only_canonical_admission(self):
        before = self.fixture()
        unchanged = copy.deepcopy(before)
        result = planner.plan(before)
        self.assertEqual(before, unchanged)
        self.assertEqual(result["rules"][:-1], before["rules"])
        self.assertEqual(result["rules"][-1], planner.STATUS_RULE)
        self.assertEqual(result["bypass_actors"], [])
        after = {**before, **result}
        self.assertTrue(planner.verify_applied(before, after))

    def test_stale_approval_queue_or_status_rules_are_replaced_not_preserved(self):
        before = self.fixture()
        before["rules"].extend([
            {"type": "pull_request", "parameters": {"required_approving_review_count": 2}},
            {"type": "merge_queue", "parameters": {"merge_method": "SQUASH"}},
            {"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": False,
                "required_status_checks": [{"context": "old", "integration_id": 1}],
            }},
            {"type": "future_non_governance_rule", "parameters": {"retain": True}},
        ])
        result = planner.plan(before)
        self.assertNotIn("pull_request", {rule["type"] for rule in result["rules"]})
        self.assertNotIn("merge_queue", {rule["type"] for rule in result["rules"]})
        self.assertEqual([r for r in result["rules"] if r["type"] == "required_status_checks"],
                         [planner.STATUS_RULE])
        self.assertIn({"type": "future_non_governance_rule", "parameters": {"retain": True}},
                      result["rules"])

    def test_non_object_identity_bypass_and_missing_baseline_are_rejected(self):
        for value in (None, [], "rules", 12, True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "must be an object"):
                planner.plan(value)
            self.assertFalse(planner.verify_applied(self.fixture(), value))
        for key, value in (
            ("source", "someone/else"),
            ("id", 42),
            ("enforcement", "disabled"),
            ("bypass_actors", [{"actor_id": 1}]),
            ("conditions", {}),
        ):
            hostile = self.fixture(); hostile[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                planner.plan(hostile)
        for missing in ("deletion", "non_fast_forward"):
            hostile = self.fixture()
            hostile["rules"] = [rule for rule in hostile["rules"] if rule["type"] != missing]
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                planner.plan(hostile)

    def test_malformed_or_duplicate_rules_are_rejected(self):
        for rules in (None, [], [None], [{"type": 1}],
                      [{"type": "deletion"}, {"type": "deletion"}, {"type": "non_fast_forward"}]):
            hostile = self.fixture(); hostile["rules"] = rules
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                planner.plan(hostile)

    def test_cli_is_read_only_and_does_not_replace_an_existing_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before, output = root / "before.json", root / "plan.json"
            raw = json.dumps(self.fixture())
            before.write_text(raw)
            command = [sys.executable, str(Path(planner.__file__)),
                       "--observed", str(before), "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text()), planner.plan(self.fixture()))
            produced = output.read_bytes()
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), produced)
            self.assertEqual(before.read_text(), raw)
            for hostile in ('[]', '{"id":1,"id":2}', '{"id":1e309}'):
                before.write_text(hostile)
                result = subprocess.run(command, capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ruleset plan rejected", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(output.read_bytes(), produced)

    def test_readback_requires_exact_reviewed_payload(self):
        before = self.fixture()
        after = {**before, **planner.plan(before)}
        self.assertTrue(planner.verify_applied(before, after))
        for mutation in ("extra_rule", "bypass", "wrong_check"):
            hostile = copy.deepcopy(after)
            if mutation == "extra_rule":
                hostile["rules"].append({"type": "pull_request", "parameters": {}})
            elif mutation == "bypass":
                hostile["bypass_actors"] = [{"actor_id": 1}]
            else:
                hostile["rules"][-1]["parameters"]["required_status_checks"][0]["context"] = "other"
            with self.subTest(mutation=mutation):
                self.assertFalse(planner.verify_applied(before, hostile))


if __name__ == "__main__":
    unittest.main()
