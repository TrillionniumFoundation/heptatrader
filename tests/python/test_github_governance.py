from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import verify_github_governance as governance  # noqa: E402

HEAD = "1" * 40
BASE = "0" * 40
MERGE = "2" * 40
PR = 99


class GitHubGovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy, cls.contexts = governance.load_policy(ROOT)
        cls.specs = governance.context_specs(cls.contexts)

    def _check_evidence(self, sha: str, event: str) -> dict:
        runs = []
        jobs_by_run = {}
        checks = []
        by_workflow: dict[int, list[str]] = {}
        for context in self.contexts[
            "required_pull_request_contexts" if event == "pull_request" else "required_merge_group_contexts"
        ]:
            by_workflow.setdefault(self.specs[context]["workflow_id"], []).append(context)
        for offset, (workflow_id, contexts) in enumerate(sorted(by_workflow.items()), start=1):
            run_id = (1000 if event == "pull_request" else 2000) + offset
            workflow_path = self.specs[contexts[0]]["workflow_path"]
            run = {
                "id": run_id,
                "workflow_id": workflow_id,
                "path": workflow_path,
                "event": event,
                "head_sha": sha,
                "status": "completed",
                "conclusion": "success",
                "run_attempt": 1,
                "created_at": "2026-09-04T00:00:00Z",
                "updated_at": "2026-09-04T00:01:00Z",
                "pull_requests": [
                    {
                        "number": PR,
                        "head": {"sha": HEAD},
                        "base": {"sha": BASE},
                    }
                ],
            }
            runs.append(run)
            jobs = []
            for job_offset, context in enumerate(contexts, start=1):
                job_id = run_id * 100 + job_offset
                job = {
                    "id": job_id,
                    "run_id": run_id,
                    "name": self.specs[context]["job_name"],
                    "status": "completed",
                    "conclusion": "success",
                    "steps": [
                        {"number": 1, "name": "Set up job", "status": "completed", "conclusion": "success"},
                        {"number": 2, "name": "Execute", "status": "completed", "conclusion": "success"},
                    ],
                }
                jobs.append(job)
                checks.append(
                    {
                        "id": job_id + 500000,
                        "name": context,
                        "head_sha": sha,
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-09-04T00:00:10Z",
                        "completed_at": "2026-09-04T00:00:20Z",
                        "app": {"id": 15368, "slug": "github-actions"},
                        "details_url": f"https://github.com/TrillionniumFoundation/heptatrader/actions/runs/{run_id}/job/{job_id}",
                    }
                )
            jobs_by_run[str(run_id)] = {"total_count": len(jobs), "jobs": jobs}
        return {
            "check_runs": {"total_count": len(checks), "check_runs": checks},
            "workflow_runs": {"total_count": len(runs), "workflow_runs": runs},
            "jobs_by_run": jobs_by_run,
        }

    def _snapshot(self) -> dict:
        required_checks = [
            {"context": context, "integration_id": 15368}
            for context in self.contexts["required_branch_contexts"]
        ]
        teams = ["architecture", "execution", "security", "reliability"]
        lines = [
            f"{pattern} @TrillionniumFoundation/{teams[index % len(teams)]}"
            for index, pattern in enumerate(self.policy["codeowners"]["required_patterns"])
        ]
        team_evidence = {}
        for offset, slug in enumerate(teams, start=1):
            maintainer = {"id": offset * 10 + 1, "login": f"{slug}-maintainer"}
            member = {"id": offset * 10 + 2, "login": f"{slug}-member"}
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
                    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
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
                "number": PR,
                "state": "open",
                "draft": False,
                "user": {"login": "author"},
                "head": {"sha": HEAD},
                "base": {"ref": "main", "sha": BASE},
            },
            "reviews": [
                {
                    "id": 1,
                    "state": "APPROVED",
                    "commit_id": HEAD,
                    "submitted_at": "2026-09-04T00:00:00Z",
                    "user": {"login": "reviewer"},
                }
            ],
            "head_evidence": self._check_evidence(HEAD, "pull_request"),
            "merge_group_evidence": self._check_evidence(MERGE, "merge_group"),
            "merge_group_commit": {
                "sha": MERGE,
                "parents": [{"sha": BASE}, {"sha": HEAD}],
            },
            "merge_group_refs": [
                {
                    "ref": f"refs/heads/gh-readonly-queue/main/pr-{PR}-deadbeef",
                    "object": {"sha": MERGE, "type": "commit"},
                }
            ],
        }

    def _errors(self, snapshot: dict) -> list[str]:
        return governance.validate_snapshot(self.policy, self.contexts, snapshot, HEAD, MERGE)

    def test_complete_governance_snapshot_passes(self) -> None:
        self.assertEqual(self._errors(self._snapshot()), [])

    def test_arbitrary_merge_group_substitution_is_rejected(self) -> None:
        snapshot = self._snapshot()
        snapshot["merge_group_refs"][0]["object"]["sha"] = "9" * 40
        errors = self._errors(snapshot)
        self.assertTrue(any("live queue ref" in item for item in errors), errors)

    def test_merge_group_must_contain_exact_head_and_base_parents(self) -> None:
        snapshot = self._snapshot()
        snapshot["merge_group_commit"]["parents"] = [{"sha": BASE}, {"sha": "8" * 40}]
        errors = self._errors(snapshot)
        self.assertTrue(any("exact admitted head and current base" in item for item in errors), errors)

    def test_merge_group_workflow_must_bind_pr_head_base_tuple(self) -> None:
        snapshot = self._snapshot()
        for run in snapshot["merge_group_evidence"]["workflow_runs"]["workflow_runs"]:
            run["pull_requests"][0]["number"] = 100
        errors = self._errors(snapshot)
        self.assertTrue(any("does not identify PR/head/base tuple" in item for item in errors), errors)

    def test_later_same_head_change_request_invalidates_approval(self) -> None:
        snapshot = self._snapshot()
        snapshot["reviews"].append(
            {
                "id": 2,
                "state": "CHANGES_REQUESTED",
                "commit_id": HEAD,
                "submitted_at": "2026-09-04T00:01:00Z",
                "user": {"login": "reviewer"},
            }
        )
        errors = self._errors(snapshot)
        self.assertTrue(any("change requests remain" in item for item in errors), errors)
        self.assertTrue(any("approvals below policy" in item for item in errors), errors)

    def test_check_name_from_wrong_integration_is_rejected(self) -> None:
        snapshot = self._snapshot()
        snapshot["head_evidence"]["check_runs"]["check_runs"][0]["app"]["id"] = 999
        errors = self._errors(snapshot)
        self.assertTrue(any("not bound to GitHub Actions integration" in item for item in errors), errors)

    def test_check_cannot_point_to_another_workflow_job(self) -> None:
        snapshot = self._snapshot()
        snapshot["head_evidence"]["check_runs"]["check_runs"][0]["details_url"] = (
            "https://github.com/TrillionniumFoundation/heptatrader/actions/runs/999/job/999"
        )
        errors = self._errors(snapshot)
        self.assertTrue(any("does not bind the selected workflow run/job" in item for item in errors), errors)

    def test_empty_job_cannot_satisfy_required_context(self) -> None:
        snapshot = self._snapshot()
        first = next(iter(snapshot["head_evidence"]["jobs_by_run"].values()))
        first["jobs"][0]["steps"] = []
        errors = self._errors(snapshot)
        self.assertTrue(any("no non-empty successful execution step" in item for item in errors), errors)

    def test_ruleset_bypass_actor_is_rejected(self) -> None:
        snapshot = self._snapshot()
        snapshot["rulesets"][0]["bypass_actors"] = [{"actor_type": "OrganizationAdmin"}]
        self.assertIn("ruleset.bypass_actors must be empty", self._errors(snapshot))

    def test_individual_codeowner_is_rejected(self) -> None:
        snapshot = self._snapshot()
        snapshot["codeowners_text"] = snapshot["codeowners_text"].replace(
            "@TrillionniumFoundation/architecture", "@ProfHepta", 1
        )
        self.assertTrue(any("owner must be an organization team" in item for item in self._errors(snapshot)))


if __name__ == "__main__":
    unittest.main()
