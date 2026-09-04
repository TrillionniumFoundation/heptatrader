from __future__ import annotations

import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
TESTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import check_qualification_trust_boundary as boundary  # noqa: E402
from test_team_codeowners_activation import (  # noqa: E402,F401
    TeamCodeownersActivationTests,
)
import verify_ib_candidate_artifact as artifact  # noqa: E402
import verify_qualification_candidate as admission  # noqa: E402

CANDIDATE = "1" * 40
BASE = "0" * 40
IMAGE = "hepta/ib-builder@sha256:" + "a" * 64
IMAGE_ID = "sha256:" + "b" * 64


class QualificationTrustBoundaryTests(unittest.TestCase):
    def test_repository_workflows_keep_candidate_code_out_of_privileged_jobs(self) -> None:
        self.assertEqual(boundary.validate(ROOT), [])

    def _context_fixture(self) -> tuple[list[str], dict[str, dict], dict, dict, dict]:
        required = ["context-a", "context-b"]
        specs = {
            "context-a": {"workflow_id": 11, "workflow_path": ".github/workflows/a.yml", "job_name": "context-a"},
            "context-b": {"workflow_id": 12, "workflow_path": ".github/workflows/b.yml", "job_name": "context-b"},
        }
        checks = []
        runs = []
        jobs_by_run = {}
        for offset, context in enumerate(required, start=1):
            run_id = 100 + offset
            job_id = 1000 + offset
            runs.append(
                {
                    "id": run_id,
                    "workflow_id": specs[context]["workflow_id"],
                    "path": specs[context]["workflow_path"],
                    "event": "pull_request",
                    "head_sha": CANDIDATE,
                    "status": "completed",
                    "conclusion": "success",
                    "run_attempt": 1,
                    "created_at": "2026-09-04T00:00:00Z",
                    "updated_at": "2026-09-04T00:01:00Z",
                }
            )
            jobs_by_run[str(run_id)] = {
                "total_count": 1,
                "jobs": [
                    {
                        "id": job_id,
                        "run_id": run_id,
                        "name": context,
                        "status": "completed",
                        "conclusion": "success",
                        "steps": [
                            {"number": 1, "status": "completed", "conclusion": "success"},
                            {"number": 2, "status": "completed", "conclusion": "success"},
                        ],
                    }
                ],
            }
            checks.append(
                {
                    "id": 2000 + offset,
                    "name": context,
                    "head_sha": CANDIDATE,
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-09-04T00:00:00Z",
                    "completed_at": "2026-09-04T00:01:00Z",
                    "app": {"id": 15368},
                    "details_url": f"https://github.com/TrillionniumFoundation/heptatrader/actions/runs/{run_id}/job/{job_id}",
                }
            )
        return (
            required,
            specs,
            {"total_count": len(checks), "check_runs": checks},
            {"total_count": len(runs), "workflow_runs": runs},
            jobs_by_run,
        )

    def _admission_snapshot(self):
        pull = {
            "number": 17,
            "state": "open",
            "merged": False,
            "draft": True,
            "user": {"login": "author"},
            "head": {
                "sha": CANDIDATE,
                "repo": {"full_name": "TrillionniumFoundation/heptatrader"},
            },
            "base": {
                "ref": "main",
                "sha": BASE,
                "repo": {"full_name": "TrillionniumFoundation/heptatrader"},
            },
        }
        reviews = [
            {
                "id": 1,
                "state": "APPROVED",
                "commit_id": CANDIDATE,
                "submitted_at": "2026-09-04T00:00:00Z",
                "user": {"login": "reviewer"},
            }
        ]
        return pull, reviews

    def test_reviewed_exact_pr_head_is_admitted_with_provenance(self) -> None:
        pull, reviews = self._admission_snapshot()
        required, specs, checks, runs, jobs = self._context_fixture()
        errors, projection = admission.validate_snapshot(
            "TrillionniumFoundation/heptatrader",
            17,
            CANDIDATE,
            "main",
            required,
            1,
            pull,
            reviews,
            checks,
            runs,
            jobs,
            specs,
            15368,
        )
        self.assertEqual(errors, [], errors)
        self.assertEqual(len(projection["required_contexts"]), 2)

    def test_latest_same_head_change_request_blocks_admission(self) -> None:
        pull, reviews = self._admission_snapshot()
        reviews.append(
            {
                "id": 2,
                "state": "CHANGES_REQUESTED",
                "commit_id": CANDIDATE,
                "submitted_at": "2026-09-04T00:01:00Z",
                "user": {"login": "reviewer"},
            }
        )
        required, specs, checks, runs, jobs = self._context_fixture()
        errors, _ = admission.validate_snapshot(
            "TrillionniumFoundation/heptatrader",
            17,
            CANDIDATE,
            "main",
            required,
            1,
            pull,
            reviews,
            checks,
            runs,
            jobs,
            specs,
            15368,
        )
        self.assertTrue(any("change requests remain" in item for item in errors), errors)
        self.assertTrue(any("approvals below policy" in item for item in errors), errors)

    def test_pre_post_admission_pair_rejects_state_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_body = {
                "schema": admission.RECEIPT_SCHEMA,
                "repository": "TrillionniumFoundation/heptatrader",
                "pull_number": 17,
                "candidate_sha": CANDIDATE,
                "base_sha": BASE,
                "required_contexts": ["a"],
                "admission_state_sha256": "sha256:" + "c" * 64,
            }
            before = {"body": base_body, "receipt_sha256": admission.canonical_digest(base_body)}
            after_body = dict(base_body)
            after_body["admission_state_sha256"] = "sha256:" + "d" * 64
            after = {"body": after_body, "receipt_sha256": admission.canonical_digest(after_body)}
            before_path = root / "before.json"
            after_path = root / "after.json"
            before_path.write_text(json.dumps(before), encoding="utf-8")
            after_path.write_text(json.dumps(after), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "state changed"):
                admission.compare_admission_receipts(before_path, after_path)

    def test_candidate_artifact_binds_oci_toolchain_and_resource_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / artifact.BINARY_NAME
            binary.write_bytes(b"\x7fELF" + b"candidate" * 64)
            binary.chmod(0o500)
            log = root / "build.log"
            log.write_text("::error::captured-not-replayed\n", encoding="utf-8")
            provenance = artifact.create_builder_provenance(
                ROOT, IMAGE, IMAGE_ID, "c" * 64, "d" * 64
            )
            archive = root / "candidate.tar"
            artifact.pack(
                binary,
                CANDIDATE,
                "e" * 64,
                provenance["bundle_sha256"],
                log,
                archive,
                provenance,
            )
            destination = root / "verified"
            artifact.verify_and_extract(
                archive,
                CANDIDATE,
                provenance["bundle_sha256"],
                destination,
                trusted_root=ROOT,
                expected_image=IMAGE,
            )
            manifest = json.loads((destination / "manifest.json").read_text())
            self.assertEqual(manifest["builder"]["image_reference"], IMAGE)
            self.assertEqual(manifest["builder"]["toolchain_sha256"], "c" * 64)
            self.assertEqual(manifest["builder"]["resource_policy_sha256"], "d" * 64)
            self.assertEqual(
                stat.S_IMODE((destination / artifact.BINARY_NAME).stat().st_mode), 0o500
            )

    def test_wrong_builder_image_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / artifact.BINARY_NAME
            binary.write_bytes(b"\x7fELF" + b"candidate" * 64)
            binary.chmod(0o500)
            log = root / "build.log"
            log.write_text("captured\n", encoding="utf-8")
            provenance = artifact.create_builder_provenance(
                ROOT, IMAGE, IMAGE_ID, "c" * 64, "d" * 64
            )
            archive = root / "candidate.tar"
            artifact.pack(binary, CANDIDATE, "e" * 64, provenance["bundle_sha256"], log, archive, provenance)
            with self.assertRaisesRegex(artifact.ArtifactError, "image reference mismatch"):
                artifact.verify_and_extract(
                    archive,
                    CANDIDATE,
                    provenance["bundle_sha256"],
                    root / "verified",
                    trusted_root=ROOT,
                    expected_image="hepta/other@sha256:" + "f" * 64,
                )

    def test_input_sha_checkout_in_privileged_job_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            for relative in boundary.TRUSTED_FILES:
                target = fixture / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
            for relative in (boundary.GOVERNANCE, boundary.IB):
                target = fixture / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
            path = fixture / boundary.GOVERNANCE
            path.write_text(
                path.read_text().replace(
                    "ref: ${{ github.sha }}\n          path: trusted",
                    "ref: ${{ inputs.expected_head_sha }}\n          path: candidate",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(any("input SHA controls a checkout" in item for item in boundary.validate(fixture)))

    def test_final_receipt_must_follow_post_campaign_admission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            for relative in boundary.TRUSTED_FILES:
                target = fixture / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
            for relative in (boundary.GOVERNANCE, boundary.IB):
                target = fixture / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
            path = fixture / boundary.IB
            text = path.read_text(encoding="utf-8")
            before = text.index("Re-admit unchanged candidate after Broker campaign")
            final = text.index("Issue final receipt only after stable post-campaign admission")
            text = text[:before] + text[final:] + text[before:final]
            path.write_text(text, encoding="utf-8")
            self.assertTrue(any("step ordering" in item for item in boundary.validate(fixture)))


if __name__ == "__main__":
    unittest.main()
