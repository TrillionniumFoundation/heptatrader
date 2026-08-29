#!/usr/bin/env python3

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))

import build_heptatrader_engineering_closure as builder  # noqa: E402
import build_heptatrader_engineering_artifact_map as map_builder  # noqa: E402
import build_heptatrader_verification_evidence as evidence  # noqa: E402
import verify_heptatrader_engineering_closure as verifier  # noqa: E402


class EngineeringClosureTests(unittest.TestCase):
    @staticmethod
    def _git(repository: Path, *arguments: str) -> str:
        run = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=True)
        return run.stdout.strip()

    def _recovery_repository(
        self, root: Path, *, with_delta_files: bool = False,
    ) -> tuple[
            Path, Path, str, str, dict[str, object], dict[str, object]]:
        repository = root / "repository"
        repository.mkdir(mode=0o700)
        self._git(repository, "init", "-b", "master")
        self._git(repository, "config", "user.name", "Hepta Test")
        self._git(repository, "config", "user.email", "test@example.invalid")
        (repository / "base.txt").write_text("base\n", encoding="utf-8")
        if with_delta_files:
            delta = repository / "delta"
            delta.mkdir()
            for index in range(21):
                (delta / f"m{index:02d}.txt").write_text(
                    f"baseline-{index}\n", encoding="utf-8")
            exact = repository / "exact"
            exact.mkdir()
            for index in range(1084):
                (exact / f"e{index:04d}.txt").write_text(
                    f"exact-{index}\n", encoding="utf-8")
            (repository / "oos-only.txt").write_text(
                "oos-only\n", encoding="utf-8")
        self._git(repository, "add", ".")
        self._git(repository, "commit", "-m", "OOS baseline")
        tag_name = builder.OOS_REF.removeprefix("refs/tags/")
        self._git(repository, "tag", "-a", tag_name, "-m", "OOS baseline")
        self._git(repository, "checkout", "-b", "round38-consolidation")
        if with_delta_files:
            self._git(
                repository, "rm", "-r", "delta", "exact", "oos-only.txt")
        (repository / "base.txt").write_text(
            "round38\n", encoding="utf-8")
        self._git(repository, "add", "base.txt")
        self._git(repository, "commit", "-m", "Round38 product")
        product_head = self._git(repository, "rev-parse", "HEAD")
        release_version = "0.1.0-beta.1-round38"
        baseline_relative = builder._round_baseline_path(release_version)
        baseline_path = repository / baseline_relative
        baseline_path.parent.mkdir(parents=True)
        baseline_path.write_text(json.dumps({
            "schema": "hepta.versioned-source-baseline.v1",
            "version": release_version,
            "git_head": product_head,
        }, sort_keys=True) + "\n", encoding="utf-8")
        self._git(repository, "add", baseline_relative)
        self._git(repository, "commit", "-m", "Round38 baseline")
        release_head = self._git(repository, "rev-parse", "HEAD")
        bundle = root / "rescue.bundle"
        self._git(repository, "bundle", "create", str(bundle), "--all")
        bundle.chmod(0o600)
        if with_delta_files:
            exact = repository / "exact"
            exact.mkdir()
            for index in range(1084):
                (exact / f"e{index:04d}.txt").write_text(
                    f"exact-{index}\n", encoding="utf-8")
        refs, head = builder._bundle_heads(bundle)
        snapshot = builder.common.stable_read(
            bundle, limit=builder.MAX_ARTIFACT_BYTES, capture=False,
            require_trusted_parent=True)
        baseline_data = baseline_path.read_bytes()
        baseline = {
            "path": baseline_relative,
            "sha256": hashlib.sha256(baseline_data).hexdigest(),
            "size": len(baseline_data),
            "mode": "0644",
        }
        manifest: dict[str, object] = {
            "schema": "hepta.git-rescue-ref-manifest.v2",
            "version": 2,
            "product_git_head": product_head,
            "release_git_head": release_head,
            "release_version": release_version,
            "baseline": baseline,
            "bundle_sha256": snapshot.sha256,
            "bundle_size": snapshot.size,
            "ref_count": len(refs),
            "ref_set_sha256": hashlib.sha256(builder.canonical_json({
                "head": head,
                "refs": refs,
            })).hexdigest(),
            "refs": refs,
            "head": head,
        }
        return (
            repository, bundle, product_head, release_head, manifest,
            baseline)

    def _fixture(self, root: Path) -> tuple[Path, dict[str, object]]:
        artifacts = root / "artifacts"
        artifacts.mkdir(mode=0o700)
        records = []
        for role in builder.REQUIRED_ROLES:
            if role == "engineering-artifact-map":
                records.append({"role": role, "path": "artifact-map.json"})
                continue
            suffix = ".json" if role in builder.JSON_ROLES else ".bin"
            path = artifacts / f"{role}{suffix}"
            if role == "source-baseline-manifest":
                payload = {
                    "schema": "hepta.versioned-source-baseline.v1",
                    "git_head": "a" * 40,
                }
            else:
                payload = {"role": role}
            if suffix == ".json":
                path.write_text(
                    json.dumps(payload, sort_keys=True) + "\n",
                    encoding="utf-8")
            else:
                path.write_bytes(role.encode("ascii"))
            path.chmod(0o600)
            records.append({"role": role, "path": path.name})
        artifact_map = {
            "schema": builder.MAP_SCHEMA,
            "version": 2,
            "round": 38,
            "release_version": "0.1.0-beta.1-round38",
            "git_head": "a" * 40,
            "artifacts": records,
        }
        map_path = artifacts / "artifact-map.json"
        map_path.write_text(
            json.dumps(artifact_map, sort_keys=True) + "\n",
            encoding="utf-8")
        map_path.chmod(0o600)
        return map_path, artifact_map

    @staticmethod
    def _summary(*_arguments, **_kwargs):
        return {
            "strict_source_files": 1,
            "agent_os_source_files": 1,
            "runtime_files": 1,
            "native_vm_variant": "stub",
            "product_git_head": "a" * 40,
            "release_git_head": "b" * 40,
            "baseline_path": builder._round_baseline_path(
                "0.1.0-beta.1-round38"),
            "release_version": "0.1.0-beta.1-round38",
        }

    def test_builder_and_verifier_rebind_all_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-engineering-") as temporary:
            root = Path(temporary)
            artifact_map, _ = self._fixture(root)
            with mock.patch.object(
                    builder, "_semantic_verify",
                    side_effect=self._summary):
                closure = builder.build(
                    artifact_map.parent, artifact_map,
                    "2026-07-25T00:00:00Z")
            output = root / "closure.json"
            builder.write_private(output, closure)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with mock.patch.object(
                    builder, "_semantic_verify",
                    side_effect=self._summary):
                result = verifier.verify(output, artifact_map.parent)
            self.assertTrue(result["passed"])
            self.assertEqual(
                result["artifact_count"], len(builder.REQUIRED_ROLES))

    def test_artifact_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-engineering-drift-") as temporary:
            root = Path(temporary)
            artifact_map, _ = self._fixture(root)
            with mock.patch.object(
                    builder, "_semantic_verify",
                    side_effect=self._summary):
                closure = builder.build(
                    artifact_map.parent, artifact_map,
                    "2026-07-25T00:00:00Z")
            output = root / "closure.json"
            builder.write_private(output, closure)
            target = artifact_map.parent / "runtime-package.bin"
            target.write_bytes(b"changed")
            target.chmod(0o600)
            with self.assertRaisesRegex(
                    verifier.VerificationError, "binding drift"):
                verifier.verify(output, artifact_map.parent)

    def test_missing_role_and_map_digest_are_bound(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-engineering-map-") as temporary:
            root = Path(temporary)
            map_path, artifact_map = self._fixture(root)
            artifact_map["artifacts"] = artifact_map["artifacts"][:-1]
            map_path.write_text(
                json.dumps(artifact_map, sort_keys=True) + "\n",
                encoding="utf-8")
            map_path.chmod(0o600)
            with self.assertRaisesRegex(
                    builder.EngineeringClosureError, "required role"):
                builder.load_artifact_map(map_path)
            self.assertNotEqual(
                hashlib.sha256(map_path.read_bytes()).hexdigest(), "0" * 64)

    def test_artifact_map_builder_sorts_and_closes_roles(self) -> None:
        values = [
            f"{role}={role}.bin"
            for role in reversed(builder.REQUIRED_ROLES)
        ]
        document = map_builder.build(
            38, "0.1.0-beta.1-round38", "b" * 40, values)
        self.assertEqual(
            [record["role"] for record in document["artifacts"]],
            list(builder.REQUIRED_ROLES))
        with self.assertRaisesRegex(
                builder.EngineeringClosureError, "role closure"):
            map_builder.build(
                38, "0.1.0-beta.1-round38", "b" * 40, values[:-1])

    def test_runner_report_rebuild_includes_both_source_classes(self) -> None:
        inputs = [
            {"name": f"{label}.cmake-cache", "path": f"{label}.cache"}
            for label in sorted(evidence.RUNNER_LABELS)
        ]
        inputs.extend({
            "name": f"{label}.source-manifest",
            "path": f"{label}.source.json",
        } for label in sorted(evidence.SOURCE_ATTESTATION_LABELS))
        report = {
            "schema": evidence.SCHEMA,
            "version": 2,
            "kind": "runner",
            "generated_at": "2026-07-25T00:00:00Z",
            "passed": True,
            "cases": [{"name": "fixture"}],
            "inputs": inputs,
            "boundary": evidence.BOUNDARY,
        }
        with mock.patch.object(
                builder, "_report_input_binding"), mock.patch.object(
                    evidence, "build_runner",
                    return_value=report) as rebuilt:
            builder._verification_report(Path("/artifact"), report, "runner")
        source_values = rebuilt.call_args.args[3]
        self.assertEqual(
            {value.split("=", 1)[0] for value in source_values},
            evidence.SOURCE_ATTESTATION_LABELS)

    def test_engineering_closure_binds_agent_and_strict_sources_separately(
            self) -> None:
        agent = {"sha256": "a" * 64, "size": 10, "mode": "0600"}
        strict = {"sha256": "b" * 64, "size": 20, "mode": "0600"}
        inputs = {
            **{
                f"{label}.source-manifest": dict(agent)
                for label in evidence.NO_GIT_LABELS
            },
            **{
                f"{label}.source-manifest": dict(strict)
                for label in evidence.STRICT_SOURCE_LABELS
            },
        }
        builder._verify_runner_source_manifests(inputs, agent, strict)
        forged = dict(inputs)
        forged["asan.source-manifest"] = dict(agent)
        with self.assertRaisesRegex(
                builder.EngineeringClosureError, "strict bundle"):
            builder._verify_runner_source_manifests(
                forged, agent, strict)

    def test_verifier_rejects_mutated_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-engineering-identity-") as temporary:
            root = Path(temporary)
            artifact_map, _ = self._fixture(root)
            with mock.patch.object(
                    builder, "_semantic_verify",
                    side_effect=self._summary):
                closure = builder.build(
                    artifact_map.parent, artifact_map,
                    "2026-07-25T00:00:00Z")
            closure["round"] = -1
            closure["release_version"] = "forged"
            closure["generated_at"] = "not-a-time"
            closure["source"]["artifact_map_sha256"] = "not-a-digest"
            closure["source"]["release_git_head"] = "c" * 40
            output = root / "closure.json"
            builder.write_private(output, closure)
            with self.assertRaises(verifier.VerificationError):
                verifier.verify(output, artifact_map.parent)

    def test_recovery_ref_manifest_uses_dynamic_final_round38_refs(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-ref-") as temporary:
            root = Path(temporary)
            _repository, bundle, product, _release, manifest, baseline = (
                self._recovery_repository(root))
            self.assertNotEqual(manifest["ref_count"], 23)
            builder._verify_rescue_bundle(
                bundle, manifest, product,
                "0.1.0-beta.1-round38", baseline)
            forged = dict(manifest)
            forged["product_git_head"] = "f" * 40
            with self.assertRaisesRegex(
                    builder.EngineeringClosureError, "manifest drift"):
                builder._verify_rescue_bundle(
                    bundle, forged, product,
                    "0.1.0-beta.1-round38", baseline)

    def test_release_commit_allows_only_the_round38_baseline_child(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-release-lineage-") as temporary:
            root = Path(temporary)
            repository, _bundle, product, release, _manifest, baseline = (
                self._recovery_repository(root))
            result = builder._verify_release_commit(
                repository, product, release,
                "0.1.0-beta.1-round38", baseline)
            self.assertEqual(result["product_git_head"], product)
            self.assertEqual(result["release_git_head"], release)

            baseline_path = repository / baseline["path"]
            valid_baseline = baseline_path.read_bytes()
            self._git(
                repository, "checkout", "-b", "malicious-extra", product)
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_bytes(valid_baseline)
            (repository / "unexpected.txt").write_text(
                "not a baseline\n", encoding="utf-8")
            self._git(
                repository, "add", baseline["path"], "unexpected.txt")
            self._git(repository, "commit", "-m", "forged release")
            forged_release = self._git(repository, "rev-parse", "HEAD")
            with self.assertRaisesRegex(
                    builder.EngineeringClosureError,
                    "changed paths other than"):
                builder._verify_release_commit(
                    repository, product, forged_release,
                    "0.1.0-beta.1-round38", baseline)

    def test_release_commit_rejects_a_baseline_for_another_product(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-release-baseline-forgery-") as temporary:
            root = Path(temporary)
            repository, _bundle, product, _release, _manifest, _baseline = (
                self._recovery_repository(root))
            release_version = "0.1.0-beta.1-round38"
            relative = builder._round_baseline_path(release_version)
            self._git(
                repository, "checkout", "-b", "malicious-baseline", product)
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = (json.dumps({
                "schema": "hepta.versioned-source-baseline.v1",
                "version": release_version,
                "git_head": "f" * 40,
            }, sort_keys=True) + "\n").encode("utf-8")
            path.write_bytes(data)
            self._git(repository, "add", relative)
            self._git(repository, "commit", "-m", "forged baseline")
            forged_release = self._git(repository, "rev-parse", "HEAD")
            forged_binding = {
                "path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "mode": "0644",
            }
            with self.assertRaisesRegex(
                    builder.EngineeringClosureError,
                    "does not bind product HEAD"):
                builder._verify_release_commit(
                    repository, product, forged_release,
                    release_version, forged_binding)

    def test_verifier_rejects_a_substituted_release_head(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-engineering-release-substitution-") as temporary:
            root = Path(temporary)
            artifact_map, _ = self._fixture(root)
            with mock.patch.object(
                    builder, "_semantic_verify",
                    side_effect=self._summary):
                closure = builder.build(
                    artifact_map.parent, artifact_map,
                    "2026-07-25T00:00:00Z")
            closure["source"]["release_git_head"] = "c" * 40
            output = root / "closure.json"
            builder.write_private(output, closure)
            with mock.patch.object(
                    builder, "_semantic_verify",
                    side_effect=self._summary):
                with self.assertRaisesRegex(
                        verifier.VerificationError,
                        "dual-head lineage drift"):
                    verifier.verify(output, artifact_map.parent)

    def test_verifier_rejects_a_rewritten_product_artifact_map(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-engineering-map-substitution-") as temporary:
            root = Path(temporary)
            map_path, artifact_map = self._fixture(root)
            with mock.patch.object(
                    builder, "_semantic_verify",
                    side_effect=self._summary):
                closure = builder.build(
                    map_path.parent, map_path,
                    "2026-07-25T00:00:00Z")
            artifact_map["git_head"] = "d" * 40
            map_path.write_text(
                json.dumps(artifact_map, sort_keys=True) + "\n",
                encoding="utf-8")
            map_path.chmod(0o600)
            rebound, _ = builder._stable_binding(
                map_path.parent, map_path.name,
                "engineering-artifact-map", capture=False)
            for index, record in enumerate(closure["artifacts"]):
                if record["role"] == "engineering-artifact-map":
                    closure["artifacts"][index] = rebound
                    break
            closure["source"]["artifact_map_sha256"] = rebound["sha256"]
            output = root / "closure.json"
            builder.write_private(output, closure)
            with self.assertRaisesRegex(
                    verifier.VerificationError,
                    "artifact map identity drift"):
                verifier.verify(output, map_path.parent)

    def test_recovery_delta_is_rederived_from_oos_tree(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-recovery-delta-") as temporary:
            root = Path(temporary)
            repository, bundle, _product, _release, ref_manifest, _baseline = (
                self._recovery_repository(
                    root, with_delta_files=True))
            records = []
            payload_files = {}
            for index in range(21):
                relative = f"delta/m{index:02d}.txt"
                baseline_data = f"baseline-{index}\n".encode()
                current_data = f"modified-{index}\n".encode()
                listing = self._git(
                    repository, "ls-tree", builder.OOS_REF, "--", relative)
                mode, kind, object_id, _path = listing.replace(
                    "\t", " ").split(" ", 3)
                self.assertEqual(kind, "blob")
                records.append({
                    "classification": "modified-after-oos-v6.1",
                    "path": relative,
                    "current": {
                        "sha256": hashlib.sha256(
                            current_data).hexdigest(),
                        "size": len(current_data),
                        "mode": "0755" if mode == "100755" else "0644",
                    },
                    "baseline": {
                        "blob_object": object_id,
                        "sha256": hashlib.sha256(
                            baseline_data).hexdigest(),
                        "size": len(baseline_data),
                        "mode": "0755" if mode == "100755" else "0644",
                    },
                })
                payload_files[relative] = current_data
            for index in range(15):
                relative = f"delta/n{index:02d}.txt"
                current_data = f"new-{index}\n".encode()
                records.append({
                    "classification": "new-after-oos-v6.1",
                    "path": relative,
                    "current": {
                        "sha256": hashlib.sha256(
                            current_data).hexdigest(),
                        "size": len(current_data),
                        "mode": "0600" if index == 0 else "0644",
                    },
                    "baseline": None,
                })
                payload_files[relative] = current_data
            records.sort(key=lambda item: item["path"])
            inventory = []
            exact_listing = self._git(
                repository, "ls-tree", "-r", builder.OOS_REF, "--", "exact")
            for raw in exact_listing.splitlines():
                metadata, relative = raw.split("\t", 1)
                mode, kind, object_id = metadata.split(" ")
                self.assertEqual(kind, "blob")
                data = (repository / relative).read_bytes()
                inventory.append({
                    "path": relative,
                    "relation": "exact",
                    "current": {
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size": len(data),
                        "mode": (
                            "0664" if relative == "exact/e0000.txt"
                            else "0755" if mode == "100755" else "0644"),
                    },
                    "baseline_blob_object": object_id,
                })
            for record in records:
                inventory.append({
                    "path": record["path"],
                    "relation": (
                        "modified"
                        if record["classification"] ==
                        "modified-after-oos-v6.1" else "new"),
                    "current": dict(record["current"]),
                    "baseline_blob_object": (
                        record["baseline"]["blob_object"]
                        if record["baseline"] is not None else None),
                })
            inventory.sort(key=lambda item: item["path"])
            overrides = [{"path": "exact/e0000.txt", "mode": "0664"}]
            delta = {
                "schema": "hepta.oos-worktree-delta.v3",
                "version": 3,
                "scope": "untracked-files-differing-from-oos-tag",
                "runner_source_path": builder.RECOVERY_RUNNER_SOURCE,
                "runner_sha256": "a" * 64,
                "oos_ref": builder.OOS_REF,
                "oos_ref_object": self._git(
                    repository, "rev-parse", builder.OOS_REF),
                "oos_tree": self._git(
                    repository, "rev-parse", f"{builder.OOS_REF}^{{tree}}"),
                "untracked_file_count": 1120,
                "exact_match_excluded_count": 1084,
                "exact_mode_override_count": len(overrides),
                "exact_mode_overrides_sha256": hashlib.sha256(
                    builder.canonical_json(overrides)).hexdigest(),
                "exact_mode_overrides": overrides,
                "untracked_inventory_sha256": hashlib.sha256(
                    builder.canonical_json(inventory)).hexdigest(),
                "untracked_inventory": inventory,
                "file_count": 36,
                "classification_counts": {
                    "modified-after-oos-v6.1": 21,
                    "new-after-oos-v6.1": 15,
                },
                "files_sha256": hashlib.sha256(
                    builder.canonical_json(records)).hexdigest(),
                "files": records,
            }
            pinned_values = {
                "OOS_REF_OBJECT": delta["oos_ref_object"],
                "OOS_TREE_OBJECT": delta["oos_tree"],
                "ROUND38_UNTRACKED_INVENTORY_SHA256":
                    delta["untracked_inventory_sha256"],
            }
            for attribute, value in pinned_values.items():
                patcher = mock.patch.object(builder, attribute, value)
                patcher.start()
                self.addCleanup(patcher.stop)
            payload = root / "delta.tar"
            buffer = io.BytesIO()
            with tarfile.open(
                    fileobj=buffer, mode="w",
                    format=tarfile.USTAR_FORMAT) as archive:
                for record in records:
                    data = payload_files[record["path"]]
                    member = tarfile.TarInfo(record["path"])
                    member.size = len(data)
                    member.mode = int(record["current"]["mode"], 8)
                    member.uid = 0
                    member.gid = 0
                    member.mtime = 0
                    archive.addfile(member, io.BytesIO(data))
            payload.write_bytes(buffer.getvalue())
            payload.chmod(0o600)
            builder._verify_delta_against_bundle(
                bundle, ref_manifest, delta, payload)
            release_collision = json.loads(json.dumps(delta))
            base_listing = self._git(
                repository, "ls-tree", builder.OOS_REF, "--", "base.txt")
            base_metadata, _base_path = base_listing.split("\t", 1)
            _base_mode, _base_kind, base_object = base_metadata.split(" ")
            collided = next(
                item for item in release_collision["untracked_inventory"]
                if item["path"] == "exact/e0001.txt")
            collided.update({
                "path": "base.txt",
                "current": {
                    "sha256": hashlib.sha256(b"base\n").hexdigest(),
                    "size": len(b"base\n"),
                    "mode": "0644",
                },
                "baseline_blob_object": base_object,
            })
            release_collision["untracked_inventory"].sort(
                key=lambda item: item["path"])
            release_collision["untracked_inventory_sha256"] = hashlib.sha256(
                builder.canonical_json(
                    release_collision["untracked_inventory"])).hexdigest()
            with mock.patch.object(
                    builder, "ROUND38_UNTRACKED_INVENTORY_SHA256",
                    release_collision["untracked_inventory_sha256"]):
                with self.assertRaisesRegex(
                        builder.EngineeringClosureError, "release tree"):
                    builder._verify_delta_against_bundle(
                        bundle, ref_manifest, release_collision, payload)
            prefix_collision = json.loads(json.dumps(delta))
            prefixed = next(
                item for item in prefix_collision["untracked_inventory"]
                if item["path"] == "exact/e0001.txt")
            prefixed["path"] = "exact/e0000.txt/child"
            prefix_collision["untracked_inventory"].sort(
                key=lambda item: item["path"])
            prefix_collision["untracked_inventory_sha256"] = hashlib.sha256(
                builder.canonical_json(
                    prefix_collision["untracked_inventory"])).hexdigest()
            with self.assertRaisesRegex(
                    builder.EngineeringClosureError, "file-prefix collision"):
                builder._verify_delta_against_bundle(
                    bundle, ref_manifest, prefix_collision, payload)
            moved_oos = json.loads(json.dumps(delta))
            moved_oos["oos_tree"] = "f" * 40
            with self.assertRaisesRegex(
                    builder.EngineeringClosureError,
                    "delta manifest is incomplete"):
                builder._verify_delta_against_bundle(
                    bundle, ref_manifest, moved_oos, payload)
            oversized = json.loads(json.dumps(delta))
            oversized["untracked_inventory"][0]["current"]["size"] = (
                builder.RECOVERY_MAX_FILE_BYTES + 1)
            oversized["untracked_inventory_sha256"] = hashlib.sha256(
                builder.canonical_json(
                    oversized["untracked_inventory"])).hexdigest()
            with self.assertRaisesRegex(
                    builder.EngineeringClosureError,
                    "inventory current is invalid"):
                builder._verify_delta_against_bundle(
                    bundle, ref_manifest, oversized, payload)
            redundant_mode = json.loads(json.dumps(delta))
            redundant_mode["exact_mode_overrides"][0]["mode"] = "0644"
            exact_inventory = next(
                item for item in redundant_mode["untracked_inventory"]
                if item["path"] == "exact/e0000.txt")
            exact_inventory["current"]["mode"] = "0644"
            redundant_mode["exact_mode_overrides_sha256"] = hashlib.sha256(
                builder.canonical_json(
                    redundant_mode["exact_mode_overrides"])).hexdigest()
            redundant_mode["untracked_inventory_sha256"] = hashlib.sha256(
                builder.canonical_json(
                    redundant_mode["untracked_inventory"])).hexdigest()
            with mock.patch.object(
                    builder, "ROUND38_UNTRACKED_INVENTORY_SHA256",
                    redundant_mode["untracked_inventory_sha256"]):
                with self.assertRaisesRegex(
                        builder.EngineeringClosureError, "redundant"):
                    builder._verify_delta_against_bundle(
                        bundle, ref_manifest, redundant_mode, payload)
            missing_exact = json.loads(json.dumps(delta))
            missing_exact["untracked_inventory"] = [
                item for item in missing_exact["untracked_inventory"]
                if item["path"] != "exact/e0001.txt"
            ]
            missing_exact["untracked_inventory_sha256"] = hashlib.sha256(
                builder.canonical_json(
                    missing_exact["untracked_inventory"])).hexdigest()
            with self.assertRaisesRegex(
                    builder.EngineeringClosureError,
                    "untracked inventory closure"):
                builder._verify_delta_against_bundle(
                    bundle, ref_manifest, missing_exact, payload)
            forged = json.loads(json.dumps(delta))
            modified = next(
                item for item in forged["files"]
                if item["classification"] ==
                "modified-after-oos-v6.1")
            new = next(
                item for item in forged["files"]
                if item["classification"] == "new-after-oos-v6.1")
            modified["classification"] = "new-after-oos-v6.1"
            modified["baseline"] = None
            new["classification"] = "modified-after-oos-v6.1"
            new["baseline"] = records[0]["baseline"]
            modified_inventory = next(
                item for item in forged["untracked_inventory"]
                if item["path"] == modified["path"])
            new_inventory = next(
                item for item in forged["untracked_inventory"]
                if item["path"] == new["path"])
            modified_inventory["relation"] = "new"
            modified_inventory["baseline_blob_object"] = None
            new_inventory["relation"] = "modified"
            new_inventory["baseline_blob_object"] = (
                records[0]["baseline"]["blob_object"])
            forged["files_sha256"] = hashlib.sha256(
                builder.canonical_json(forged["files"])).hexdigest()
            forged["untracked_inventory_sha256"] = hashlib.sha256(
                builder.canonical_json(
                    forged["untracked_inventory"])).hexdigest()
            with mock.patch.object(
                    builder, "ROUND38_UNTRACKED_INVENTORY_SHA256",
                    forged["untracked_inventory_sha256"]):
                with self.assertRaisesRegex(
                        builder.EngineeringClosureError,
                        "(classification is forged|baseline tree closure|"
                        "new inventory and OOS tree)"):
                    builder._verify_delta_against_bundle(
                        bundle, ref_manifest, forged, payload)

    def test_semantic_verifier_rejects_handwritten_coverage(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-engineering-coverage-") as temporary:
            root = Path(temporary)
            xml = root / "coverage.xml"
            xml.write_text(
                '<coverage line-rate="0.80"></coverage>\n',
                encoding="utf-8")
            xml.chmod(0o600)
            with self.assertRaises(evidence.EvidenceError):
                evidence.build_coverage(
                    root, xml.name, 0.70,
                    "2026-07-25T00:00:00Z")


if __name__ == "__main__":
    unittest.main(verbosity=2)
