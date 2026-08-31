from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE_EPOCH = "1700000000"


def load_script_module(name: str, relative_path: str):
    path = ROOT / relative_path
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_repo_contracts_module():
    return load_script_module(
        "hepta_check_repo_contracts", "scripts/check_repo_contracts.py"
    )


def load_install_verifier_module():
    return load_script_module(
        "hepta_verify_install_tree", "scripts/verify_install_tree.py"
    )


def load_release_ci_verifier_module():
    return load_script_module(
        "hepta_verify_release_ci", "scripts/verify_release_ci.py"
    )


def create_minimal_install_tree(root: Path) -> None:
    verifier = load_install_verifier_module()
    root.mkdir(parents=True, mode=0o755)
    root.chmod(0o755)
    for item in verifier.CORE_EXECUTABLES:
        path = root / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-executable\n")
        path.chmod(0o755)
    for item in verifier.CORE_FILES:
        path = root / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test-file\n", encoding="utf-8")
        path.chmod(0o644)
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda value: len(value.parts),
    ):
        directory.chmod(0o755)


def run_install_verifier(
    root: Path, manifest: Path | None = None
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(ROOT / "scripts/verify_install_tree.py"),
        "--root",
        str(root),
        "--logical-root",
        "/usr",
    ]
    if manifest is not None:
        command.extend(["--manifest", str(manifest)])
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_sbom(root: Path, version: Path, output: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/generate_sbom.py"),
            "--root",
            str(root),
            "--version-file",
            str(version),
            "--git-sha",
            "a" * 40,
            "--source-date-epoch",
            SOURCE_EPOCH,
            "--output",
            str(output),
        ],
        check=True,
    )


def run_archive(root: Path, output: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build_release_archive.py"),
            "--root",
            str(root),
            "--prefix",
            "usr",
            "--source-date-epoch",
            SOURCE_EPOCH,
            "--output",
            str(output),
        ],
        check=True,
    )


def successful_ci_fixture(verifier, sha: str, run_id: int = 17):
    runs = {
        "workflow_runs": [
            {
                "id": run_id,
                "head_sha": sha,
                "head_branch": "main",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
            }
        ]
    }
    jobs = {
        run_id: {
            "jobs": [
                {"name": name, "status": "completed", "conclusion": "success"}
                for name in sorted(verifier.REQUIRED_JOBS)
            ]
        }
    }
    return runs, jobs


class ReleaseToolTests(unittest.TestCase):
    def test_sbom_is_deterministic_and_contains_every_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            install = workspace / "usr"
            (install / "bin").mkdir(parents=True)
            binary = install / "bin/heptactl"
            binary.write_bytes(b"test-binary")
            binary.chmod(0o755)
            version = workspace / "VERSION"
            version.write_text("0.1.0-beta.1\n", encoding="utf-8")
            first = workspace / "first.spdx.json"
            second = workspace / "second.spdx.json"
            run_sbom(install, version, first)
            run_sbom(install, version, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

            payload = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(payload["spdxVersion"], "SPDX-2.3")
            self.assertEqual(payload["creationInfo"]["created"], "2023-11-14T22:13:20Z")
            self.assertEqual(payload["packages"][0]["versionInfo"], "0.1.0-beta.1")
            self.assertEqual([item["fileName"] for item in payload["files"]], ["./bin/heptactl"])

    @unittest.skipUnless(os.name == "posix", "install mode tests require POSIX permissions")
    def test_install_verifier_accepts_private_minimal_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "usr"
            create_minimal_install_tree(root)
            result = run_install_verifier(root)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.name == "posix", "install mode tests require POSIX permissions")
    def test_install_manifest_is_independent_of_staging_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            first_root = workspace / "first-stage/usr"
            second_root = workspace / "different-stage/usr"
            create_minimal_install_tree(first_root)
            create_minimal_install_tree(second_root)
            first_manifest = workspace / "first.json"
            second_manifest = workspace / "second.json"
            first = run_install_verifier(first_root, first_manifest)
            second = run_install_verifier(second_root, second_manifest)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first_manifest.read_bytes(), second_manifest.read_bytes())
            payload = json.loads(first_manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["logical_root"], "/usr")
            self.assertTrue(
                all(item["path"].startswith("/usr/") for item in payload["files"])
            )

    @unittest.skipUnless(os.name == "posix", "archive tests require POSIX permissions")
    def test_release_archive_is_byte_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            install = workspace / "stage/usr"
            create_minimal_install_tree(install)
            first = workspace / "first.tar.gz"
            second = workspace / "second.tar.gz"
            run_archive(install, first)
            run_archive(install, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

            with tarfile.open(first, "r:gz") as archive:
                members = archive.getmembers()
            names = [member.name for member in members]
            self.assertEqual(names, sorted(names))
            self.assertEqual(names[0], "usr")
            for member in members:
                self.assertEqual(member.uid, 0)
                self.assertEqual(member.gid, 0)
                self.assertEqual(member.uname, "root")
                self.assertEqual(member.gname, "root")
                self.assertEqual(member.mtime, int(SOURCE_EPOCH))
                self.assertFalse(member.issym())
                self.assertFalse(member.islnk())

    @unittest.skipUnless(os.name == "posix", "install mode tests require POSIX permissions")
    def test_install_verifier_rejects_writable_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "usr"
            create_minimal_install_tree(root)
            root.chmod(0o777)
            result = run_install_verifier(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("install root is group/world writable", result.stderr)

    @unittest.skipUnless(os.name == "posix", "install mode tests require POSIX permissions")
    def test_install_verifier_rejects_replaceable_executable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "usr"
            create_minimal_install_tree(root)
            (root / "libexec").chmod(0o777)
            result = run_install_verifier(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("replaceable release directory", result.stderr)
            self.assertIn("libexec", result.stderr)

    @unittest.skipUnless(os.name == "posix", "install mode tests require POSIX permissions")
    def test_install_verifier_rejects_writable_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "usr"
            create_minimal_install_tree(root)
            executable = root / "libexec/hepta-executiond"
            executable.chmod(0o777)
            result = run_install_verifier(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("group/world writable", result.stderr)
            self.assertIn("hepta-executiond", result.stderr)

    @unittest.skipUnless(os.name == "posix", "symlink tests require POSIX semantics")
    def test_install_verifier_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "usr"
            create_minimal_install_tree(root)
            target = root / "share/doc/heptatrader/VERSION"
            (root / "share/replaceable-link").symlink_to(target)
            result = run_install_verifier(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr)

    def test_workflow_actions_require_immutable_revisions(self) -> None:
        contracts = load_repo_contracts_module()
        self.assertTrue(
            contracts.action_use_is_immutable(
                "actions/checkout@" + "a" * 40
            )
        )
        self.assertTrue(
            contracts.action_use_is_immutable(
                "docker://example.invalid/image@sha256:" + "b" * 64
            )
        )
        self.assertTrue(contracts.action_use_is_immutable("./.github/actions/local"))
        self.assertFalse(contracts.action_use_is_immutable("actions/checkout@v4"))
        self.assertFalse(
            contracts.action_use_is_immutable("docker://example.invalid/image:latest")
        )
        self.assertFalse(contracts.action_use_is_immutable("owner/action"))

    def test_release_candidate_requires_exact_main_and_complete_ci(self) -> None:
        verifier = load_release_ci_verifier_module()
        sha = "a" * 40
        runs, jobs = successful_ci_fixture(verifier, sha)
        self.assertEqual(verifier.validate_candidate(sha, sha, runs, jobs), [])

        missing_jobs = copy.deepcopy(jobs)
        missing_jobs[17]["jobs"] = [
            job for job in missing_jobs[17]["jobs"] if job["name"] != "package"
        ]
        errors = verifier.validate_candidate(sha, sha, runs, missing_jobs)
        self.assertTrue(any("package" in error for error in errors))

        errors = verifier.validate_candidate(sha, "b" * 40, runs, jobs)
        self.assertTrue(any("exact current main" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
