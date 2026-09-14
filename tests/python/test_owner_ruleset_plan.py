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
        return {"id": planner.RULESET_ID, "source": planner.REPOSITORY, "target": "branch",
                "name": "owner-main", "enforcement": "active", "bypass_actors": [],
                "conditions": {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
                "rules": [{"type": "deletion"}, {"type": "non_fast_forward"},
                          {"type": "pull_request", "parameters": {"required_approving_review_count": 2}},
                          {"type": "merge_queue", "parameters": {"merge_method": "SQUASH"}},
                          {"type": "required_status_checks", "parameters": {
                              "strict_required_status_checks_policy": True,
                              "required_status_checks": [{"context": c, "integration_id": 15368}
                                                         for c in sorted(planner.REQUIRED_CHECKS)]}},
                          {"type": "future_non_governance_rule", "parameters": {"retain": True}}]}

    def test_only_approval_and_queue_rules_change(self):
        before = self.fixture()
        unchanged = copy.deepcopy(before)
        result = planner.plan(before)
        self.assertEqual(before, unchanged)
        self.assertEqual(result["rules"], [r for r in before["rules"] if r["type"] not in {"pull_request", "merge_queue"}])
        self.assertEqual(result["bypass_actors"], [])
        after = {**before, **result}
        self.assertTrue(planner.verify_applied(before, after))
        after["rules"] = after["rules"][:-1]
        self.assertFalse(planner.verify_applied(before, after))

    def test_non_object_input_and_readback_are_rejected(self):
        for value in (None, [], "rules", 12, True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "must be an object"):
                    planner.plan(value)
                self.assertFalse(planner.verify_applied(self.fixture(), value))

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

    def test_malformed_status_check_objects_are_rejected(self):
        for parameters in (None, [], {"required_status_checks": None},
                           {"required_status_checks": [None]}, {"required_status_checks": [42]}):
            hostile = self.fixture()
            hostile["rules"][4]["parameters"] = parameters
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                planner.plan(hostile)

    def test_identity_bypass_and_missing_checks_require_fresh_review(self):
        for key, value in (("source", "someone/else"), ("id", 42), ("enforcement", "disabled"),
                           ("bypass_actors", [{"actor_id": 1}]), ("conditions", {})):
            hostile = self.fixture()
            hostile[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                planner.plan(hostile)
        hostile = self.fixture()
        hostile["rules"][4]["parameters"]["required_status_checks"].pop()
        with self.assertRaises(ValueError):
            planner.plan(hostile)


if __name__ == "__main__":
    unittest.main()
