from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import verify_github_governance as governance  # noqa: E402


HEAD = "1" * 40
MERGE = "2" * 40


class GitHubGovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy, cls.contexts = governance.load_policy(ROOT)

    def _snapshot(self) -> dict:
        pr_contexts = self.contexts["required_pull_request_contexts"]
        merge_contexts = self.contexts["required_merge_group_contexts"]
        required_checks = [
            {"context": context, "integration_id": 15368}
            for context in dict.fromkeys(pr_contexts + merge_contexts)
        ]
        teams = ["architecture", "execution", "security", "reliability"]
        required_patterns = self.policy["codeowners"]["required_patterns"]
        lines = []
        for index, pattern in enumerate(required_patterns):
            lines.append(
                f"{pattern} "
                f"@TrillionniumFoundation/{teams[index % len(teams)]}"
            )
        team_evidence = {}
        for offset, slug in enumerate(teams, start=1):
            maintainer = {
                "id": offset * 10 + 1,
                "login": f"{slug}-maintainer",
            }
            member = {
                "id": offset * 10 + 2,
                "login": f"{slug}-member",
            }
            team_evidence[slug] = {
                "team": {
                    "slug": slug,
                    "privacy": "closed",
                    "organization": {"login": "TrillionniumFoundation"},
                },
                "members": [maintainer, member],
                "maintainers": [maintainer],
                "repository": {"permissions": {"push": True}},
            }
        head_runs = [
            {
                "id": index,
                "name": context,
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-09-02T00:00:00Z",
                "completed_at": "2026-09-02T00:01:00Z",
            }
            for index, context in enumerate(pr_contexts, start=1)
        ]
        merge_runs = [
            {
                "id": 100 + index,
                "name": context,
                "head_sha": MERGE,
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-09-02T00:02:00Z",
                "completed_at": "2026-09-02T00:03:00Z",
            }
            for index, context in enumerate(merge_contexts, start=1)
        ]
        return {
            "repository": {
                "full_name": "TrillionniumFoundation/heptatrader",
                "default_branch": "main",
            },
            "branch": {"name": "main", "protected": True},
            "rulesets": [
                {
                    "id": 42,
                    "target": "branch",
                    "enforcement": "active",
                    "bypass_actors": [],
                    "conditions": {
                        "ref_name": {
                            "include": ["~DEFAULT_BRANCH"],
                            "exclude": [],
                        }
                    },
                    "rules": [
                        {"type": "deletion"},
                        {"type": "non_fast_forward"},
                        {
                            "type": "pull_request",
                            "parameters": {
                                "allowed_merge_methods": ["squash"],
                                "dismiss_stale_reviews_on_push": True,
                                "require_code_owner_review": True,
                                "require_last_push_approval": True,
                                "required_approving_review_count": 2,
                                "required_review_thread_resolution": True,
                            },
                        },
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "do_not_enforce_on_create": False,
                                "strict_required_status_checks_policy": True,
                                "required_status_checks": required_checks,
                            },
                        },
                        {
                            "type": "merge_queue",
                            "parameters": {
                                "check_response_timeout_minutes": 30,
                                "grouping_strategy": "ALLGREEN",
                                "max_entries_to_build": 5,
                                "max_entries_to_merge": 1,
                                "merge_method": "SQUASH",
                                "min_entries_to_merge": 1,
                                "min_entries_to_merge_wait_minutes": 0,
                            },
                        },
                    ],
                }
            ],
            "codeowners_text": "\n".join(lines) + "\n",
            "codeowners_errors": {"errors": []},
            "teams": team_evidence,
            "pull_request": {
                "number": 99,
                "state": "open",
                "draft": False,
                "user": {"login": "author"},
                "head": {"sha": HEAD},
                "base": {"ref": "main"},
            },
            "reviews": [
                {
                    "state": "APPROVED",
                    "commit_id": HEAD,
                    "user": {"login": "reviewer"},
                }
            ],
            "head_check_runs": {
                "total_count": len(head_runs),
                "check_runs": head_runs,
            },
            "merge_group_check_runs": {
                "total_count": len(merge_runs),
                "check_runs": merge_runs,
            },
        }

    def _errors(self, snapshot: dict) -> list[str]:
        return governance.validate_snapshot(
            self.policy, self.contexts, snapshot, HEAD, MERGE
        )

    def test_required_context_projections_match_canonical_branch_set(self) -> None:
        canonical = self.contexts["required_branch_contexts"]
        self.assertEqual(
            self.contexts["required_pull_request_contexts"], canonical
        )
        self.assertEqual(
            self.contexts["required_merge_group_contexts"], canonical
        )

    def test_complete_governance_snapshot_passes(self) -> None:
        errors = self._errors(self._snapshot())
        self.assertEqual(errors, [], errors)

    def test_ruleset_bypass_actor_is_rejected(self) -> None:
        snapshot = self._snapshot()
        snapshot["rulesets"][0]["bypass_actors"] = [
            {
                "actor_type": "OrganizationAdmin",
                "actor_id": None,
                "bypass_mode": "always",
            }
        ]
        errors = self._errors(snapshot)
        self.assertIn("ruleset.bypass_actors must be empty", errors)

    def test_missing_bypass_evidence_is_rejected(self) -> None:
        snapshot = self._snapshot()
        del snapshot["rulesets"][0]["bypass_actors"]
        errors = self._errors(snapshot)
        self.assertTrue(
            any("bypass_actors is absent" in error for error in errors),
            errors,
        )

    def test_individual_codeowner_is_rejected(self) -> None:
        snapshot = self._snapshot()
        snapshot["codeowners_text"] = snapshot["codeowners_text"].replace(
            "@TrillionniumFoundation/architecture", "@ProfHepta", 1
        )
        errors = self._errors(snapshot)
        self.assertTrue(
            any("owner must be an organization team" in error for error in errors),
            errors,
        )

    def test_team_without_write_permission_is_rejected(self) -> None:
        snapshot = self._snapshot()
        snapshot["teams"]["execution"]["repository"] = {
            "permissions": {"pull": True, "push": False}
        }
        errors = self._errors(snapshot)
        self.assertIn(
            "team execution: no write/maintain/admin repository permission",
            errors,
        )

    def test_stale_approval_is_rejected(self) -> None:
        snapshot = self._snapshot()
        snapshot["reviews"][0]["commit_id"] = "3" * 40
        errors = self._errors(snapshot)
        self.assertIn(
            "fresh non-author exact-head approval requirement is not satisfied",
            errors,
        )

    def test_latest_failed_rerun_blocks_required_context(self) -> None:
        snapshot = self._snapshot()
        context = self.contexts["required_pull_request_contexts"][0]
        snapshot["head_check_runs"]["check_runs"].append(
            {
                "id": 999,
                "name": context,
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-09-02T00:04:00Z",
                "completed_at": "2026-09-02T00:05:00Z",
            }
        )
        errors = self._errors(snapshot)
        self.assertIn(
            f"source head: check {context} is not terminal success", errors
        )

    def test_every_merge_group_check_is_required(self) -> None:
        snapshot = self._snapshot()
        context = self.contexts["required_merge_group_contexts"][0]
        snapshot["merge_group_check_runs"]["check_runs"] = [
            item
            for item in snapshot["merge_group_check_runs"]["check_runs"]
            if item["name"] != context
        ]
        errors = self._errors(snapshot)
        self.assertIn(f"merge group: missing check {context}", errors)

    def test_ruleset_check_context_is_bound_to_github_actions(self) -> None:
        snapshot = self._snapshot()
        checks = snapshot["rulesets"][0]["rules"][3]["parameters"][
            "required_status_checks"
        ]
        checks[0]["integration_id"] = 123
        errors = self._errors(snapshot)
        self.assertTrue(
            any(
                "must be bound to integration_id 15368" in error
                for error in errors
            ),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
