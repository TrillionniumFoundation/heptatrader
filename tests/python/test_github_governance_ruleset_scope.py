from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import verify_github_governance as governance  # noqa: E402


class RulesetScopeTests(unittest.TestCase):
    def test_hostile_exclusion_matrix_runs_at_import(self) -> None:
        self.assertTrue(governance.RULESET_SCOPE_SELF_TEST_PASSED)

    def test_effective_condition_projection_is_receipt_ready(self) -> None:
        ruleset = {
            "conditions": {
                "ref_name": {
                    "include": ["~DEFAULT_BRANCH"],
                    "exclude": ["refs/heads/release/**"],
                }
            }
        }
        self.assertEqual(
            governance._ruleset_ref_condition_projection(ruleset, "main"),
            {
                "effective_default_ref": "refs/heads/main",
                "include": ["~DEFAULT_BRANCH"],
                "exclude": ["refs/heads/release/**"],
                "default_branch_effectively_included": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
