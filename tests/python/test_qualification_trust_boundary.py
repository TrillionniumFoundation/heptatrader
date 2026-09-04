from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_qualification_trust_boundary as boundary  # noqa: E402
import verify_ib_candidate_artifact as artifact  # noqa: E402
import verify_qualification_candidate as admission  # noqa: E402


class QualificationTrustBoundaryTests(unittest.TestCase):
    def test_repository_workflows_keep_candidate_code_out_of_privileged_jobs(self) -> None:
        self.assertEqual(boundary.validate(ROOT), [])

    def _admission_snapshot(self) -> tuple[dict, list[dict], dict]:
        candidate = "1" * 40
        required = ["context-a", "context-b"]
        pull = {
            "number": 17,
            "state": "open",
            "merged": False,
            "user": {"login": "author"},
            "head": {
                "sha": candidate,
                "repo": {"full_name": "TrillionniumFoundation/heptatrader"},
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": "TrillionniumFoundation/heptatrader"},
            },
        }
        reviews = [
            {
                "id": 1,
                "state": "APPROVED",
                "commit_id": candidate,
                "submitted_at": "2026-09-04T00:00:00Z",
                "user": {"login": "reviewer"},
            }
        ]
        checks = {
            "check_runs": [
                {
                    "id": index,
                    "name": context,
                    "head_sha": candidate,
                    "status": "completed",
                    "conclusion": "success",
                }
                for index, context in enumerate(required, start=1)
            ]
        }
        return pull, reviews, checks

    def test_reviewed_exact_pr_head_is_admitted_as_data(self) -> None:
        pull, reviews, checks = self._admission_snapshot()
        errors = admission.validate_snapshot(
            "TrillionniumFoundation/heptatrader",
            17,
            "1" * 40,
            "main",
            ["context-a", "context-b"],
            1,
            pull,
            reviews,
            checks,
        )
        self.assertEqual(errors, [], errors)

    def test_current_exact_head_change_request_blocks_admission(self) -> None:
        pull, reviews, checks = self._admission_snapshot()
        reviews.append(
            {
                "id": 2,
                "state": "CHANGES_REQUESTED",
                "commit_id": "1" * 40,
                "submitted_at": "2026-09-04T00:01:00Z",
                "user": {"login": "security-reviewer"},
            }
        )
        errors = admission.validate_snapshot(
            "TrillionniumFoundation/heptatrader",
            17,
            "1" * 40,
            "main",
            ["context-a", "context-b"],
            1,
            pull,
            reviews,
            checks,
        )
        self.assertTrue(
            any("change requests remain" in error for error in errors), errors
        )

    def test_arbitrary_sha_not_current_pr_head_is_rejected(self) -> None:
        pull, reviews, checks = self._admission_snapshot()
        errors = admission.validate_snapshot(
            "TrillionniumFoundation/heptatrader",
            17,
            "2" * 40,
            "main",
            ["context-a", "context-b"],
            1,
            pull,
            reviews,
            checks,
        )
        self.assertTrue(
            any("not the current pull-request head" in error for error in errors),
            errors,
        )

    def test_candidate_artifact_round_trip_is_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / artifact.BINARY_NAME
            binary.write_bytes(b"\x7fELF" + b"candidate" * 64)
            binary.chmod(0o500)
            log = root / "build.log"
            log.write_text("::error::captured-not-replayed\n", encoding="utf-8")
            archive = root / "candidate.tar"
            candidate = "1" * 40
            builder = "2" * 64
            artifact.pack(binary, candidate, "3" * 64, builder, log, archive)
            destination = root / "verified"
            artifact.verify_and_extract(archive, candidate, builder, destination)
            self.assertEqual((destination / artifact.BINARY_NAME).read_bytes(), binary.read_bytes())
            manifest = json.loads((destination / "manifest.json").read_text())
            self.assertEqual(manifest["candidate_sha"], candidate)
            self.assertEqual(manifest["builder_sha256"], builder)
            self.assertEqual(stat.S_IMODE((destination / artifact.BINARY_NAME).stat().st_mode), 0o500)

    def test_archive_link_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "malicious.tar"
            manifest = {
                "schema": artifact.SCHEMA,
                "candidate_sha": "1" * 40,
                "binary": {
                    "name": artifact.BINARY_NAME,
                    "sha256": "2" * 64,
                    "size": 1,
                    "format": "ELF64",
                },
                "sdk_tree_sha256": "3" * 64,
                "builder_sha256": "4" * 64,
                "build_log_sha256": "5" * 64,
                "isolation": {
                    "network_namespace": "unshared",
                    "environment": "cleared",
                    "source_mount": "read-only-archive",
                    "sdk_mount": "read-only",
                    "candidate_output": "captured-not-replayed",
                },
            }
            with tarfile.open(archive, "w") as handle:
                payload = artifact._canonical_bytes(manifest)
                info = tarfile.TarInfo("manifest.json")
                info.size = len(payload)
                info.mode = 0o400
                info.mtime = 0
                handle.addfile(info, io.BytesIO(payload))
                link = tarfile.TarInfo(artifact.BINARY_NAME)
                link.type = tarfile.SYMTYPE
                link.linkname = "/bin/sh"
                link.mtime = 0
                handle.addfile(link)
            with self.assertRaises(artifact.ArtifactError):
                artifact.verify_and_extract(
                    archive, "1" * 40, "4" * 64, root / "destination"
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
                path.read_text(encoding="utf-8").replace(
                    "ref: ${{ github.sha }}\n          path: trusted",
                    "ref: ${{ inputs.expected_head_sha }}\n          path: candidate",
                    1,
                ),
                encoding="utf-8",
            )
            errors = boundary.validate(fixture)
            self.assertTrue(
                any("input SHA controls a checkout" in error for error in errors),
                errors,
            )

    def test_candidate_cmake_in_credential_job_is_rejected(self) -> None:
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
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "trusted/scripts/run_ib_paper_artifact_qualification.sh \\",
                    "cmake -S candidate -B build\n          trusted/scripts/run_ib_paper_artifact_qualification.sh \\",
                    1,
                ),
                encoding="utf-8",
            )
            errors = boundary.validate(fixture)
            self.assertTrue(
                any("cmake " in error for error in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
