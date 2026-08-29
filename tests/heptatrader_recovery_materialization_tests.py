#!/usr/bin/env python3

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(1, str(REPOSITORY / "scripts"))

import build_heptatrader_clean_source_bundle as strict_source  # noqa: E402
import build_heptatrader_engineering_closure as closure  # noqa: E402
import build_heptatrader_recovery_evidence as evidence  # noqa: E402
import verify_heptatrader_recovery_materialization as verifier  # noqa: E402
from hepta_ops import agent_os_source  # noqa: E402


class RecoveryMaterializationTests(unittest.TestCase):
    @staticmethod
    def _git(repository: Path, *arguments: str) -> str:
        run = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=True,
        )
        return run.stdout.strip()

    @staticmethod
    def _write_private(path: Path, payload: bytes) -> None:
        path.write_bytes(payload)
        path.chmod(0o600)

    def _fixture(self, root: Path) -> dict[str, object]:
        repository = root / "repository"
        repository.mkdir(mode=0o700)
        self._git(repository, "init", "-b", "master")
        self._git(repository, "config", "user.name", "Hepta Test")
        self._git(
            repository, "config", "user.email", "test@example.invalid")

        scripts = repository / "scripts"
        scripts.mkdir()
        for relative in (
                closure.RECOVERY_RUNNER_SOURCE,
                verifier.DELIVERY_CLOSURE_SOURCE,
                verifier.ENGINEERING_CLOSURE_SOURCE,
                verifier.TOOL_SOURCE):
            source = REPOSITORY / relative
            destination = repository / relative
            destination.write_bytes(source.read_bytes())
            destination.chmod(0o644)
        (repository / "base.txt").write_text(
            "oos baseline\n", encoding="utf-8")
        delta_root = repository / "delta"
        delta_root.mkdir()
        for index in range(21):
            (delta_root / f"m{index:02d}.txt").write_text(
                f"baseline-{index}\n", encoding="utf-8")
        exact_root = repository / "exact"
        exact_root.mkdir()
        for index in range(1084):
            (exact_root / f"e{index:04d}.txt").write_text(
                f"exact-{index}\n", encoding="utf-8")
        (repository / "oos-only.txt").write_text(
            "oos-only\n", encoding="utf-8")
        self._git(repository, "add", ".")
        self._git(repository, "commit", "-m", "OOS baseline")
        self._git(
            repository, "tag", "-a",
            closure.OOS_REF.removeprefix("refs/tags/"),
            "-m", "OOS baseline")

        self._git(repository, "checkout", "-b", "round38-consolidation")
        self._git(repository, "rm", "-r", "delta", "exact", "oos-only.txt")
        (repository / "base.txt").write_text(
            "round38 product\n", encoding="utf-8")
        self._git(repository, "add", "base.txt")
        self._git(repository, "commit", "-m", "Round38 product")
        product_head = self._git(repository, "rev-parse", "HEAD")

        release_version = "0.1.0-beta.1-round38"
        baseline_relative = closure._round_baseline_path(release_version)
        baseline_path = repository / baseline_relative
        baseline_path.parent.mkdir(parents=True)
        baseline_path.write_bytes(
            closure.canonical_json({
                "schema": "hepta.versioned-source-baseline.v1",
                "version": release_version,
                "git_head": product_head,
            }) + b"\n")
        self._git(repository, "add", baseline_relative)
        self._git(repository, "commit", "-m", "Round38 baseline")
        release_head = self._git(repository, "rev-parse", "HEAD")

        exact_root.mkdir()
        for index in range(1084):
            (exact_root / f"e{index:04d}.txt").write_text(
                f"exact-{index}\n", encoding="utf-8")
        (exact_root / "e0000.txt").chmod(0o664)
        delta_root.mkdir()
        for index in range(21):
            modified = delta_root / f"m{index:02d}.txt"
            modified.write_text(
                f"modified-{index}\n", encoding="utf-8")
            modified.chmod(0o644)
        new_root = repository / "new"
        new_root.mkdir()
        for index in range(15):
            (new_root / f"n{index:02d}.txt").write_text(
                f"new-{index}\n", encoding="utf-8")

        # This synthetic repository intentionally has different pinned Git
        # object and inventory identities.  The real verifier is restored
        # below and exercised against the fixture's derived identities.
        with mock.patch.object(
                closure, "_verify_delta_manifest", return_value=([], [])):
            delta_document, payload_files = evidence._snapshot_delta(
                repository, closure.OOS_REF)
        payload = evidence._payload(delta_document, payload_files)

        artifacts = root / "artifacts"
        artifacts.mkdir(mode=0o700)
        bundle = artifacts / "round38.bundle"
        self._git(repository, "bundle", "create", str(bundle), "--all")
        bundle.chmod(0o600)
        refs, head = closure._bundle_heads(bundle)
        bundle_snapshot = closure.common.stable_read(
            bundle, limit=closure.MAX_ARTIFACT_BYTES, capture=False,
            require_trusted_parent=True)
        baseline_bytes = baseline_path.read_bytes()
        ref_document = {
            "schema": "hepta.git-rescue-ref-manifest.v2",
            "version": 2,
            "product_git_head": product_head,
            "release_git_head": release_head,
            "release_version": release_version,
            "baseline": {
                "path": baseline_relative,
                "sha256": hashlib.sha256(baseline_bytes).hexdigest(),
                "size": len(baseline_bytes),
                "mode": "0644",
            },
            "bundle_sha256": bundle_snapshot.sha256,
            "bundle_size": bundle_snapshot.size,
            "ref_count": len(refs),
            "ref_set_sha256": hashlib.sha256(
                closure.canonical_json({
                    "head": head,
                    "refs": refs,
                })).hexdigest(),
            "refs": refs,
            "head": head,
        }
        ref_manifest = artifacts / "round38-ref-manifest.json"
        delta_manifest = artifacts / "round38-delta-manifest.json"
        delta_payload = artifacts / "round38-delta-payload.tar"
        self._write_private(
            ref_manifest, closure.canonical_json(ref_document) + b"\n")
        self._write_private(
            delta_manifest,
            closure.canonical_json(delta_document) + b"\n")
        self._write_private(delta_payload, payload)
        paths = {
            "rescue-bundle": bundle,
            "rescue-ref-manifest": ref_manifest,
            "rescue-delta-manifest": delta_manifest,
            "rescue-delta-payload": delta_payload,
        }
        expected = {
            role: hashlib.sha256(path.read_bytes()).hexdigest()
            for role, path in paths.items()
        }
        return {
            "repository": repository,
            "paths": paths,
            "expected": expected,
            "delta": delta_document,
        }

    def _patch_delta_constants(
        self, delta: dict[str, object],
    ) -> mock._patch:
        return mock.patch.multiple(
            closure,
            OOS_REF_OBJECT=delta["oos_ref_object"],
            OOS_TREE_OBJECT=delta["oos_tree"],
            ROUND38_UNTRACKED_INVENTORY_SHA256=(
                delta["untracked_inventory_sha256"]),
        )

    def test_verifier_enters_strict_source_but_not_agent_product(self) -> None:
        source_manifest, _captured = strict_source.load_security_manifest(
            REPOSITORY)
        paths = {
            record["path"] for record in source_manifest["files"]
        }
        self.assertIn(verifier.TOOL_SOURCE, paths)
        self.assertIn(
            "tests/heptatrader_recovery_materialization_tests.py", paths)
        policy = agent_os_source.load_policy(
            REPOSITORY / "policies/heptatrader-agent-os-source-v2.json")
        self.assertFalse(policy.selects(verifier.TOOL_SOURCE))
        self.assertFalse(policy.selects(
            "tests/heptatrader_recovery_materialization_tests.py"))

    def test_running_source_accepts_restrictive_checkout_mode(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-source-mode-") as temporary:
            source = Path(temporary) / "source.py"
            source.write_bytes(b"pass\n")
            source.chmod(0o600)
            snapshot = verifier._snapshot_running_source(
                source, "restrictive source")
            self.assertEqual(snapshot.mode, "0600")
            self.assertEqual(
                snapshot.sha256, hashlib.sha256(b"pass\n").hexdigest())

    def test_running_source_still_rejects_unsafe_metadata(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-source-unsafe-") as temporary:
            root = Path(temporary)
            source = root / "source.py"
            source.write_bytes(b"pass\n")
            for mode in (0o620, 0o666, 0o700):
                with self.subTest(mode=f"{mode:04o}"):
                    source.chmod(mode)
                    with self.assertRaisesRegex(
                            verifier.RecoveryMaterializationError,
                            "running source metadata is unsafe"):
                        verifier._snapshot_running_source(
                            source, "unsafe source")
            source.chmod(0o600)
            alias = root / "source-hardlink.py"
            os.link(source, alias)
            with self.assertRaisesRegex(
                    verifier.RecoveryMaterializationError,
                    "running source metadata is unsafe"):
                verifier._snapshot_running_source(source, "unsafe source")

    def test_materializes_exact_private_recovery_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-materialization-") as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            paths = fixture["paths"]
            assert isinstance(paths, dict)
            expected = fixture["expected"]
            assert isinstance(expected, dict)
            delta = fixture["delta"]
            assert isinstance(delta, dict)
            materialization = root / "materialized"
            with self._patch_delta_constants(delta):
                receipt = verifier.verify_materialization(
                    rescue_bundle=paths["rescue-bundle"],
                    ref_manifest=paths["rescue-ref-manifest"],
                    delta_manifest=paths["rescue-delta-manifest"],
                    delta_payload=paths["rescue-delta-payload"],
                    materialization_root=materialization,
                    expected_sha256=expected,
                )
            self.assertTrue(receipt["passed"])
            self.assertEqual(
                receipt["materialization"]["untracked"]
                ["inventory_file_count"],
                1120)
            self.assertEqual(
                stat.S_IMODE(materialization.stat().st_mode), 0o700)
            receipt_path = (
                materialization / verifier.DEFAULT_RECEIPT_NAME)
            self.assertEqual(
                stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
            receipt_bytes = receipt_path.read_bytes()
            self.assertEqual(
                receipt_bytes, closure.canonical_json(receipt) + b"\n")
            self.assertEqual(
                (materialization / "untracked" /
                 "delta" / "m00.txt").read_text(encoding="utf-8"),
                "modified-0\n")
            self.assertEqual(
                (materialization / "untracked" /
                 "new" / "n14.txt").read_text(encoding="utf-8"),
                "new-14\n")
            self.assertEqual(
                stat.S_IMODE(
                    (materialization / "untracked" /
                     "exact" / "e0000.txt").stat().st_mode),
                0o664)
            files = sum(
                len(names)
                for _directory, _directories, names in os.walk(
                    materialization / "untracked"))
            self.assertEqual(files, 1120)
            self.assertFalse(
                (materialization / "repository.git" /
                 "objects" / "info" / "alternates").exists())
            self.assertEqual(
                receipt["materialization"]["untracked"]
                ["materialized_tree_sha256"],
                receipt["materialization"]["untracked"]
                ["verified_tree_sha256"])
            self.assertEqual(
                receipt["tools"]["materialization_verifier"]["sha256"],
                hashlib.sha256(
                    (REPOSITORY / verifier.TOOL_SOURCE).read_bytes()
                ).hexdigest())
            for role, relative in (
                    ("delivery_closure", verifier.DELIVERY_CLOSURE_SOURCE),
                    ("engineering_closure",
                     verifier.ENGINEERING_CLOSURE_SOURCE)):
                self.assertEqual(
                    receipt["tools"][role]["sha256"],
                    hashlib.sha256(
                        (REPOSITORY / relative).read_bytes()).hexdigest())

            with self._patch_delta_constants(delta):
                _records, inventory = closure._verify_delta_manifest(delta)
            untracked_root = materialization / "untracked"
            original_tree = closure.verify_materialized_recovery(
                untracked_root, inventory)
            content_target = untracked_root / "delta" / "m00.txt"
            original_content = content_target.read_bytes()
            original_mode = stat.S_IMODE(content_target.stat().st_mode)
            self.assertEqual(original_mode, 0o644)
            adversarial_file = untracked_root / "adversarial-extra"
            adversarial_directory = untracked_root / "adversarial-dir"

            def mutate_mode() -> None:
                self.assertEqual(
                    stat.S_IMODE(content_target.stat().st_mode), 0o644)
                content_target.chmod(0o600)
                self.assertEqual(
                    stat.S_IMODE(content_target.stat().st_mode), 0o600)

            for name, mutate, restore in (
                (
                    "content-drift",
                    lambda: content_target.write_bytes(b"tampered\n"),
                    lambda: (
                        content_target.write_bytes(original_content),
                        content_target.chmod(original_mode),
                    ),
                ),
                (
                    "mode-drift",
                    mutate_mode,
                    lambda: content_target.chmod(original_mode),
                ),
                (
                    "added-file",
                    lambda: adversarial_file.write_bytes(b"extra\n"),
                    lambda: adversarial_file.unlink(),
                ),
                (
                    "deleted-file",
                    lambda: content_target.unlink(),
                    lambda: (
                        content_target.write_bytes(original_content),
                        content_target.chmod(original_mode),
                    ),
                ),
                (
                    "extra-directory",
                    lambda: adversarial_directory.mkdir(),
                    lambda: adversarial_directory.rmdir(),
                ),
                (
                    "extra-fifo",
                    lambda: os.mkfifo(adversarial_file),
                    lambda: adversarial_file.unlink(),
                ),
                (
                    "extra-symlink",
                    lambda: os.symlink("delta/m00.txt", adversarial_file),
                    lambda: adversarial_file.unlink(),
                ),
            ):
                with self.subTest(materialized_tree_attack=name):
                    mutate()
                    try:
                        with self.assertRaises(
                                closure.EngineeringClosureError):
                            closure.verify_materialized_recovery(
                                untracked_root, inventory)
                    finally:
                        restore()
                    self.assertEqual(
                        closure.verify_materialized_recovery(
                            untracked_root, inventory),
                        original_tree)

            restored_repository = materialization / "repository.git"
            pack = next(
                (restored_repository / "objects" / "pack").glob("*.pack"))
            original_pack_mode = stat.S_IMODE(pack.stat().st_mode)
            pack.chmod(0o666)
            try:
                with self.assertRaisesRegex(
                        verifier.RecoveryMaterializationError, "unsafe"):
                    verifier._verify_materialized_repository(
                        restored_repository,
                        json.loads(
                            paths["rescue-ref-manifest"].read_text(
                                encoding="utf-8")),
                    )
            finally:
                pack.chmod(original_pack_mode)

            external_pack = root / "external-pack-hardlink"
            os.link(pack, external_pack)
            try:
                with self.assertRaisesRegex(
                        verifier.RecoveryMaterializationError, "hardlink"):
                    verifier._verify_materialized_repository(
                        restored_repository,
                        json.loads(
                            paths["rescue-ref-manifest"].read_text(
                                encoding="utf-8")),
                    )
            finally:
                external_pack.unlink()

            mirror_symlink = restored_repository / "adversarial-symlink"
            mirror_symlink.symlink_to("HEAD")
            try:
                with self.assertRaisesRegex(
                        verifier.RecoveryMaterializationError,
                        "special file"):
                    verifier._verify_materialized_repository(
                        restored_repository,
                        json.loads(
                            paths["rescue-ref-manifest"].read_text(
                                encoding="utf-8")),
                    )
            finally:
                mirror_symlink.unlink()

            alternates = (
                restored_repository / "objects" / "info" / "alternates")
            alternates.write_text(
                str(root / "foreign-objects") + "\n", encoding="utf-8")
            alternates.chmod(0o600)
            try:
                with self.assertRaisesRegex(
                        verifier.RecoveryMaterializationError,
                        "object alternates"):
                    verifier._verify_materialized_repository(
                        restored_repository,
                        json.loads(
                            paths["rescue-ref-manifest"].read_text(
                                encoding="utf-8")),
                    )
            finally:
                alternates.unlink()

            symbolic_head = self._git(
                restored_repository, "symbolic-ref", "HEAD")
            detached_head = self._git(
                restored_repository, "rev-parse", "HEAD")
            self._git(
                restored_repository, "update-ref", "--no-deref",
                "HEAD", detached_head)
            (restored_repository / "HEAD").chmod(0o644)
            try:
                with self.assertRaisesRegex(
                        verifier.RecoveryMaterializationError,
                        "symbolic HEAD"):
                    verifier._verify_materialized_repository(
                        restored_repository,
                        json.loads(
                            paths["rescue-ref-manifest"].read_text(
                                encoding="utf-8")),
                    )
            finally:
                self._git(
                    restored_repository, "symbolic-ref",
                    "HEAD", symbolic_head)
                (restored_repository / "HEAD").chmod(0o644)

            injected = subprocess.run(
                ["git", "-C", str(restored_repository),
                 "hash-object", "-w", "--stdin"],
                input=b"unreachable recovery object\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                check=True,
            )
            object_id = injected.stdout.decode("ascii").strip()
            object_directory = (
                restored_repository / "objects" / object_id[:2])
            object_directory.chmod(0o755)
            (object_directory / object_id[2:]).chmod(0o444)
            with self.assertRaisesRegex(
                    verifier.RecoveryMaterializationError,
                    "unreachable objects"):
                verifier._verify_materialized_repository(
                    restored_repository,
                    json.loads(
                        paths["rescue-ref-manifest"].read_text(
                            encoding="utf-8")),
                )

            bad_target = root / "bad-checksum-target"
            bad_expected = dict(expected)
            bad_expected["rescue-delta-payload"] = "0" * 64
            with self.assertRaisesRegex(
                    verifier.RecoveryMaterializationError,
                    "independent expected checksum"):
                verifier.verify_materialization(
                    rescue_bundle=paths["rescue-bundle"],
                    ref_manifest=paths["rescue-ref-manifest"],
                    delta_manifest=paths["rescue-delta-manifest"],
                    delta_payload=paths["rescue-delta-payload"],
                    materialization_root=bad_target,
                    expected_sha256=bad_expected,
                )
            self.assertFalse(bad_target.exists())

    def test_cli_requires_explicit_verify_before_touching_paths(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-opt-in-") as temporary:
            target = Path(temporary) / "must-not-exist"
            arguments = [
                "--rescue-bundle", "/does/not/exist.bundle",
                "--ref-manifest", "/does/not/exist-ref.json",
                "--delta-manifest", "/does/not/exist-delta.json",
                "--delta-payload", "/does/not/exist-payload.tar",
                "--materialization-root", str(target),
                "--expected-rescue-bundle-sha256", "0" * 64,
                "--expected-ref-manifest-sha256", "0" * 64,
                "--expected-delta-manifest-sha256", "0" * 64,
                "--expected-delta-payload-sha256", "0" * 64,
            ]
            with self.assertRaisesRegex(
                    verifier.RecoveryMaterializationError,
                    "explicit --verify"):
                verifier.main(arguments)
            self.assertFalse(target.exists())

    def test_rejects_environment_object_alternate_before_materializing(
            self) -> None:
        self.assertNotIn(
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            verifier.GIT_ENVIRONMENT)
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-env-alternate-") as temporary:
            root = Path(temporary)
            target = root / "must-not-exist"
            with mock.patch.dict(
                    os.environ,
                    {"GIT_ALTERNATE_OBJECT_DIRECTORIES":
                     str(root / "foreign-objects")}):
                with self.assertRaisesRegex(
                        verifier.RecoveryMaterializationError,
                        "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
                    verifier.verify_materialization(
                        rescue_bundle=root / "missing.bundle",
                        ref_manifest=root / "missing-refs.json",
                        delta_manifest=root / "missing-delta.json",
                        delta_payload=root / "missing-payload.tar",
                        materialization_root=target,
                        expected_sha256={
                            "rescue-bundle": "0" * 64,
                            "rescue-ref-manifest": "0" * 64,
                            "rescue-delta-manifest": "0" * 64,
                            "rescue-delta-payload": "0" * 64,
                        },
                    )
            self.assertFalse(target.exists())

    def test_receipt_post_link_failure_removes_owned_publication(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-receipt-rollback-") as temporary:
            root = Path(temporary) / "materialized"
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            for name in ("repository.git", "untracked"):
                (root / name).mkdir(mode=0o700)
                (root / name).chmod(0o700)
            layout = verifier._verify_root_layout(
                root, {"repository.git", "untracked"})
            receipt = root / verifier.DEFAULT_RECEIPT_NAME
            with mock.patch.object(
                    verifier, "_sync_published_directory",
                    side_effect=OSError("injected directory sync failure")):
                with self.assertRaisesRegex(
                        verifier.RecoveryMaterializationError,
                        "atomic recovery receipt"):
                    verifier._atomic_receipt(
                        root,
                        verifier.DEFAULT_RECEIPT_NAME,
                        {"passed": True},
                        layout,
                        lambda: None,
                    )
            self.assertFalse(receipt.exists())
            self.assertEqual(
                {path.name for path in root.iterdir()},
                {"repository.git", "untracked"})

    def test_receipt_rechecks_open_temporary_payload_before_publication(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-receipt-temp-drift-") as temporary:
            root = Path(temporary) / "materialized"
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            for name in ("repository.git", "untracked"):
                (root / name).mkdir(mode=0o700)
                (root / name).chmod(0o700)
            layout = verifier._verify_root_layout(
                root, {"repository.git", "untracked"})
            receipt = root / verifier.DEFAULT_RECEIPT_NAME

            def mutate_temporary_receipt() -> None:
                candidates = list(root.glob(
                    f".{verifier.DEFAULT_RECEIPT_NAME}.tmp-*"))
                self.assertEqual(len(candidates), 1)
                candidates[0].write_bytes(b'{"passed":null}\n')
                candidates[0].chmod(0o600)

            with self.assertRaisesRegex(
                    verifier.RecoveryMaterializationError,
                    "atomic recovery receipt"):
                verifier._atomic_receipt(
                    root,
                    verifier.DEFAULT_RECEIPT_NAME,
                    {"passed": True},
                    layout,
                    mutate_temporary_receipt,
                )
            self.assertFalse(receipt.exists())
            self.assertEqual(
                {path.name for path in root.iterdir()},
                {"repository.git", "untracked"})

    def test_materialization_root_identity_is_bound_at_creation(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-root-identity-") as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            paths = fixture["paths"]
            expected = fixture["expected"]
            delta = fixture["delta"]
            assert isinstance(paths, dict)
            assert isinstance(expected, dict)
            assert isinstance(delta, dict)
            materialization = root / "materialized"
            displaced = root / "materialized-created-root"
            original = verifier._clone_and_verify_repository

            def replace_root_before_clone(
                    bundle: Path,
                    candidate: Path,
                    ref_document: dict[str, object],
            ) -> tuple[Path, dict[str, object]]:
                candidate.rename(displaced)
                candidate.mkdir(mode=0o700)
                candidate.chmod(0o700)
                return original(bundle, candidate, ref_document)

            with (
                    self._patch_delta_constants(delta),
                    mock.patch.object(
                        verifier,
                        "_clone_and_verify_repository",
                        side_effect=replace_root_before_clone),
                    self.assertRaisesRegex(
                        verifier.RecoveryMaterializationError,
                        "root identity drift"),
            ):
                verifier.verify_materialization(
                    rescue_bundle=paths["rescue-bundle"],
                    ref_manifest=paths["rescue-ref-manifest"],
                    delta_manifest=paths["rescue-delta-manifest"],
                    delta_payload=paths["rescue-delta-payload"],
                    materialization_root=materialization,
                    expected_sha256=expected,
                )
            self.assertTrue(displaced.is_dir())
            self.assertFalse(
                (materialization / verifier.DEFAULT_RECEIPT_NAME).exists())
            self.assertFalse(
                (displaced / verifier.DEFAULT_RECEIPT_NAME).exists())

    def test_materialization_root_publication_binds_created_inode(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-root-publication-") as temporary:
            parent = Path(temporary)
            materialization = parent / "materialized"
            displaced = parent / "materialized-created-root"
            original = verifier._rename_directory_noreplace

            def replace_after_atomic_publication(
                    parent_descriptor: int,
                    source_name: str,
                    target_name: str,
            ) -> None:
                original(
                    parent_descriptor,
                    source_name,
                    target_name,
                )
                os.rename(
                    target_name,
                    displaced.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                os.mkdir(
                    target_name,
                    mode=0o700,
                    dir_fd=parent_descriptor,
                )

            with (
                    mock.patch.object(
                        verifier,
                        "_rename_directory_noreplace",
                        side_effect=replace_after_atomic_publication),
                    self.assertRaisesRegex(
                        verifier.RecoveryMaterializationError,
                        "private materialization root"),
            ):
                verifier._create_private_root(materialization)
            self.assertTrue(displaced.is_dir())
            self.assertTrue(materialization.is_dir())
            self.assertNotEqual(
                displaced.stat().st_ino,
                materialization.stat().st_ino,
            )

    def test_receipt_rechecks_materialized_tree_after_initial_scan(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-pre-receipt-rescan-") as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            paths = fixture["paths"]
            expected = fixture["expected"]
            delta = fixture["delta"]
            assert isinstance(paths, dict)
            assert isinstance(expected, dict)
            assert isinstance(delta, dict)
            materialization = root / "materialized"
            untracked = materialization / "untracked"
            original = closure.verify_materialized_recovery
            persistent_scans = 0

            def mutate_after_initial_scan(
                    candidate: Path,
                    inventory: list[dict[str, object]],
            ) -> dict[str, object]:
                nonlocal persistent_scans
                if Path(candidate) == untracked:
                    persistent_scans += 1
                result = original(candidate, inventory)
                if Path(candidate) == untracked and persistent_scans == 2:
                    (untracked / "delta" / "m00.txt").write_text(
                        "changed-after-first-scan\n", encoding="utf-8")
                return result

            with (
                    self._patch_delta_constants(delta),
                    mock.patch.object(
                        closure, "verify_materialized_recovery",
                        side_effect=mutate_after_initial_scan),
                    self.assertRaisesRegex(
                        verifier.RecoveryMaterializationError,
                        "drift"),
            ):
                verifier.verify_materialization(
                    rescue_bundle=paths["rescue-bundle"],
                    ref_manifest=paths["rescue-ref-manifest"],
                    delta_manifest=paths["rescue-delta-manifest"],
                    delta_payload=paths["rescue-delta-payload"],
                    materialization_root=materialization,
                    expected_sha256=expected,
                )
            self.assertGreaterEqual(persistent_scans, 3)
            self.assertFalse(
                (materialization / verifier.DEFAULT_RECEIPT_NAME).exists())

    def test_receipt_rechecks_subtree_after_root_layout_snapshot(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-prepublish-rescan-") as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            paths = fixture["paths"]
            expected = fixture["expected"]
            delta = fixture["delta"]
            assert isinstance(paths, dict)
            assert isinstance(expected, dict)
            assert isinstance(delta, dict)
            materialization = root / "materialized"
            original = verifier._verify_root_layout

            def mutate_after_layout(
                    candidate: Path,
                    expected_entries: set[str],
                    expected_root_identity: dict[
                        str, int | str] | None = None,
            ) -> dict[str, object]:
                result = original(
                    candidate,
                    expected_entries,
                    expected_root_identity,
                )
                (candidate / "untracked" / "delta" / "m00.txt").write_text(
                    "changed-after-root-layout\n", encoding="utf-8")
                return result

            with (
                    self._patch_delta_constants(delta),
                    mock.patch.object(
                        verifier,
                        "_verify_root_layout",
                        side_effect=mutate_after_layout),
                    self.assertRaisesRegex(
                        verifier.RecoveryMaterializationError,
                        "atomic recovery receipt|drift|publication"),
            ):
                verifier.verify_materialization(
                    rescue_bundle=paths["rescue-bundle"],
                    ref_manifest=paths["rescue-ref-manifest"],
                    delta_manifest=paths["rescue-delta-manifest"],
                    delta_payload=paths["rescue-delta-payload"],
                    materialization_root=materialization,
                    expected_sha256=expected,
                )
            self.assertFalse(
                (materialization / verifier.DEFAULT_RECEIPT_NAME).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
