from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_gap_register as gaps


class GapRegisterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "docs").mkdir()
        (self.root / "regression.py").write_text("# evidence\n")
        self.path = self.root / "docs/gap-register.json"
        self.register = {
            "schema": gaps.SCHEMA,
            "authorization": {"paper_authorized": False, "live_authorized": False},
            "gaps": [{"id": "NEW-001", "domain": "REPOSITORY", "state": "OPEN",
                      "summary": "A newly discovered defect", "evidence": [], "issue": 123,
                      "blocking_releases": [], "disposition": ""}],
        }

    def write(self):
        self.path.write_text(json.dumps(self.register))

    def check(self, profile=None):
        self.write()
        return gaps.validate(self.root, profile)

    def test_repository_register_is_valid_without_claiming_all_work_closed(self):
        self.assertEqual(gaps.validate(ROOT), [])
        self.assertEqual(gaps.validate(ROOT, "core"), [])

    def test_new_id_and_open_issue_are_allowed(self):
        self.assertEqual(self.check(), [])
        self.register["gaps"][0]["id"] = "PREVIOUSLY-UNKNOWN-999"
        self.assertEqual(self.check(), [])

    def test_reopening_closed_issue_does_not_break_normal_source_validation(self):
        item = self.register["gaps"][0]
        item.update(state="CLOSED", disposition="Implemented and covered by regression", evidence=["regression.py"])
        self.assertEqual(self.check(), [])
        item.update(state="OPEN", blocking_releases=["core"])
        self.assertEqual(self.check(), [])
        self.assertTrue(any("NEW-001" in error for error in self.check("core")))

    def test_blockers_are_scoped_to_requested_release(self):
        self.register["gaps"][0]["blocking_releases"] = ["ib-paper"]
        self.assertEqual(self.check(), [])
        self.assertEqual(self.check("core"), [])
        self.assertTrue(any("unresolved release blockers" in item for item in self.check("ib-paper")))

    def test_accepted_and_deferred_need_reason_and_do_not_implicitly_waive_blockers(self):
        item = self.register["gaps"][0]
        for state in ("ACCEPTED", "DEFERRED"):
            with self.subTest(state=state):
                item.update(state=state, disposition="", blocking_releases=["core"])
                self.assertTrue(self.check())
                item["disposition"] = "Retained for a documented migration dependency"
                self.assertEqual(self.check(), [])
                self.assertTrue(self.check("core"))
                item["blocking_releases"] = []
                self.assertEqual(self.check("core"), [])

    def test_closed_issue_requires_existing_evidence(self):
        item = self.register["gaps"][0]
        item.update(state="CLOSED", disposition="Fixed")
        self.assertTrue(self.check())
        item["evidence"] = ["missing.py"]
        self.assertTrue(any("missing evidence" in error for error in self.check()))
        item["evidence"] = ["regression.py"]
        self.assertEqual(self.check(), [])

    def test_external_prerequisite_can_be_recorded_without_fabricating_completion(self):
        self.register["gaps"][0].update(domain="EXTERNAL", issue=None)
        self.assertEqual(self.check(), [])

    def test_issue_state_never_authorizes_trading(self):
        for field in ("paper_authorized", "live_authorized"):
            self.register["authorization"][field] = True
            self.assertTrue(any("cannot authorize" in item for item in self.check()))
            self.register["authorization"][field] = False

    def test_duplicate_ids_paths_profiles_and_json_keys_rejected(self):
        item = self.register["gaps"][0]
        self.register["gaps"].append(copy.deepcopy(item))
        self.assertTrue(self.check())
        self.register["gaps"].pop()
        item["evidence"] = ["regression.py", "regression.py"]
        self.assertTrue(self.check())
        item["evidence"] = []
        item["blocking_releases"] = ["core", "core"]
        self.assertTrue(self.check())
        self.path.write_text('{"schema":1,"schema":2}')
        self.assertTrue(gaps.validate(self.root))

    def test_unsafe_evidence_path_is_rejected(self):
        for value in ("../outside", "regression.py/../regression.py", "/tmp/other", "./regression.py"):
            self.register["gaps"][0]["evidence"] = [value]
            self.assertTrue(self.check(), value)
        alias = self.root / "alias.py"
        alias.symlink_to(self.root / "regression.py")
        self.register["gaps"][0]["evidence"] = ["alias.py"]
        self.assertTrue(self.check())

    def test_unknown_state_or_release_profile_is_rejected(self):
        self.register["gaps"][0]["state"] = "ALL_DONE"
        self.assertTrue(self.check())
        self.register["gaps"][0]["state"] = "OPEN"
        self.assertTrue(self.check("live"))

    def test_source_changes_and_test_renames_do_not_lock_implementation_spelling(self):
        item = self.register["gaps"][0]
        item.update(state="CLOSED", disposition="Behavior remains covered", evidence=["regression.py"])
        (self.root / "regression.py").write_text("def renamed_behavior_test():\n    assert 2 + 2 == 4\n")
        self.assertEqual(self.check(), [])

    def test_cli_register_and_release_modes_have_different_exit_semantics(self):
        self.register["gaps"][0]["blocking_releases"] = ["core"]
        self.write()
        command = [sys.executable, str(ROOT / "scripts/check_gap_register.py"), "--root", str(self.root)]
        self.assertEqual(subprocess.run(command, capture_output=True, timeout=5).returncode, 0)
        result = subprocess.run(command + ["--release-profile", "core"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn("NEW-001", result.stderr)


if __name__ == "__main__":
    unittest.main()
