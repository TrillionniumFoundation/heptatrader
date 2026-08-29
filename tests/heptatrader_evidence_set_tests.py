#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(1, str(REPOSITORY / "scripts"))

import build_heptatrader_evidence_index as index_builder  # noqa: E402
import build_heptatrader_delivery_closure as closure_builder  # noqa: E402
import build_heptatrader_engineering_closure as engineering_builder  # noqa: E402
import build_heptatrader_evidence_set as set_builder  # noqa: E402
import build_heptatrader_release_validation_closure as release_builder  # noqa: E402
from hepta_ops import cli as operations  # noqa: E402
import verify_heptatrader_evidence_set as set_verifier  # noqa: E402
from tests.heptatrader_delivery_closure_tests import (  # noqa: E402
    DeliveryClosureFixture,
)


POLICY = REPOSITORY / "policies/heptatrader-evidence-retention-v1.json"
TRUST_POLICY = (
    REPOSITORY / "policies/heptatrader-evidence-receipt-trust-v1.json")


class EvidenceSetFixture:
    def __init__(self, root: Path, *, complete_tree: bool = False) -> None:
        self.root = root
        self.round = 99
        self.release_version = "0.1.0-beta.1-round99"
        self.evidence = root / "runtime-logs"
        self.evidence.mkdir()
        self.evidence.chmod(0o700)
        delivery_source = root / "delivery-fixture"
        delivery_source.mkdir(mode=0o700)
        delivery_source.chmod(0o700)
        self.delivery = DeliveryClosureFixture(
            delivery_source, round_number=self.round,
            release_version=self.release_version)
        delivery_paths = {
            role: set_verifier.DELIVERY_ARTIFACT_FILENAMES[role]
            for role in closure_builder.REQUIRED_ARTIFACT_ROLES
        }
        for role, destination in delivery_paths.items():
            self.delivery.path_for(role).rename(
                self.delivery.artifact_root / destination)
        self.delivery.paths = delivery_paths
        self.delivery.closure = self.delivery.build()
        self.delivery.closure_path.unlink()
        closure_builder.write_closure(
            self.delivery.closure_path, self.delivery.closure)
        self.delivery_clean_source_result = (
            self.delivery.clean_source_result())
        self.delivery_root_name = (
            "heptatrader-round99-semantic-delivery-artifacts-v1")
        (self.delivery.artifact_root).rename(
            self.evidence / self.delivery_root_name)
        self.paths_by_role = {
            "repository-inventory":
                "heptatrader-round99-hepta-ops-inventory-v2.json",
            "round-closure":
                "heptatrader-round99-agent-os-delivery-closure-v1.json",
            **{
                role: f"{self.delivery_root_name}/{path}"
                for role, path in delivery_paths.items()
            },
        }
        self.roles_by_path = {
            path: role for role, path in self.paths_by_role.items()
        }
        self.explicit_paths = (
            [] if complete_tree else sorted(self.roles_by_path))
        source_record = next(
            artifact for artifact in self.delivery.closure["artifacts"]
            if artifact["role"] == "source-baseline-manifest")
        self.source_baseline = {
            field: source_record[field]
            for field in set_verifier.SOURCE_BASELINE_FIELDS
        }
        self.documents = {
            "repository-inventory": self._inventory_document(),
            "round-closure": deepcopy(self.delivery.closure),
        }
        for role, document in self.documents.items():
            path = self.evidence / self.paths_by_role[role]
            if role == "round-closure":
                path.write_bytes(
                    closure_builder.canonical_json(document) + b"\n")
                path.chmod(0o600)
            else:
                self._write_json(path, document)

        self.index_path = root / "index.json"
        self.manifest = {
            "schema": set_verifier.MANIFEST_SCHEMA,
            "version": 2,
            "project_id": set_verifier.PROJECT_ID,
            "round": self.round,
            "release_version": self.release_version,
            "evidence_set_id": "round99-certification",
            "profile": set_verifier.PROFILE,
            "coverage": (
                "full-index-eligible-tree"
                if complete_tree else "manifest-defined"),
            "index": {},
            "required_roles": [],
            "source_files_deleted": False,
            "source_removal_authorized": False,
            "paper_authorized": False,
            "live_authorized": False,
            "artifacts": [],
        }
        self.manifest_path = root / "evidence-set.json"
        self.rebind()

    def _inventory_document(self) -> dict[str, object]:
        return {
            "schema": set_verifier.INVENTORY_SCHEMA,
            "version": set_verifier.INVENTORY_VERSION,
            "project_id": set_verifier.PROJECT_ID,
            "round": self.round,
            "release_version": self.release_version,
            "source_baseline": deepcopy(self.source_baseline),
            "wrapper_count": 0,
            "wrapper_counts": {
                "canonical": 0,
                "compat": 0,
                "archive": 0,
            },
            "implementation_count": 0,
            "implementation_test_count": 0,
            "wrappers": [],
            "implementations": [],
            "implementation_tests": [],
        }

    def _closure_document(self) -> dict[str, object]:
        return deepcopy(self.delivery.closure)

    def mock_full_delivery_verification(self):
        return mock.patch.object(
            closure_builder.clean_source_verifier, "verify_bundle",
            return_value=deepcopy(self.delivery_clean_source_result))

    def rebind(self) -> None:
        self.index = index_builder.build_index(
            self.evidence, POLICY, self.explicit_paths,
            "2026-01-01T00:00:00+00:00")
        self._write_json(self.index_path, self.index)
        index_bytes = self.index_path.read_bytes()
        artifacts = sorted(
            ({
                "role": self.roles_by_path[record["path"]],
                "path": record["path"],
                "sha256": record["sha256"],
                "size": record["size"],
                "mode": record["mode"],
                "tier": record["tier"],
            } for record in self.index["files"]),
            key=lambda item: item["role"])
        self.manifest["index"] = {
            "sha256": hashlib.sha256(index_bytes).hexdigest(),
            "records_sha256": self.index["records_sha256"],
            "selection_mode": self.index["selection_mode"],
        }
        self.manifest["required_roles"] = [
            artifact["role"] for artifact in artifacts
        ]
        self.manifest["artifacts"] = artifacts
        self.write_manifest(self.manifest)

    def replace_document(
            self, role: str, document: dict[str, object]) -> None:
        self.documents[role] = document
        self._write_json(
            self.evidence / self.paths_by_role[role], document)
        self.rebind()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        path.chmod(0o600)

    def write_manifest(self, value: object) -> None:
        self._write_json(self.manifest_path, value)

    def verify(self) -> dict[str, object]:
        with self.mock_full_delivery_verification():
            return set_verifier.verify(
                self.manifest_path, self.index_path, self.evidence, POLICY)

    def build_manifest(self) -> dict[str, object]:
        with self.mock_full_delivery_verification():
            return set_builder.build_manifest(
                self.index_path, self.evidence, POLICY,
                self.round, self.release_version)

    def build_and_publish(self, output: Path) -> dict[str, object]:
        with self.mock_full_delivery_verification():
            return set_builder.build_and_publish(
                self.root, self.index_path, self.evidence, POLICY,
                output, self.round, self.release_version)


class Round38EngineeringEvidenceFixture:
    def __init__(
            self, root: Path, *, supporting_files: int = 0) -> None:
        self.root = root.resolve()
        self.round = 38
        self.release_version = "0.1.0-beta.1-round38"
        self.git_head = "a" * 40
        self.release_git_head = "b" * 40
        self.evidence = self.root / "round38-evidence"
        self.evidence.mkdir(mode=0o700)
        self.artifact_directory_name = (
            "heptatrader-round38-engineering-artifacts-v1")
        self.artifacts = self.evidence / self.artifact_directory_name
        self.artifacts.mkdir(mode=0o700)
        map_records = []
        for role in engineering_builder.REQUIRED_ROLES:
            if role == "engineering-artifact-map":
                relative = "round38-engineering-artifact-map.json"
                map_records.append({"role": role, "path": relative})
                continue
            suffix = (
                ".json" if role in engineering_builder.JSON_ROLES
                else ".tar")
            relative = f"{role}{suffix}"
            path = self.artifacts / relative
            document: object
            if role == "source-baseline-manifest":
                document = {
                    "schema": "hepta.versioned-source-baseline.v1",
                    "git_head": self.git_head,
                }
            else:
                document = {"role": role}
            if suffix == ".json":
                self._write_json(path, document)
            else:
                path.write_bytes(role.encode("ascii"))
                path.chmod(0o600)
            map_records.append({"role": role, "path": relative})
        self.map_path = (
            self.artifacts / "round38-engineering-artifact-map.json")
        self.artifact_map = {
            "schema": engineering_builder.MAP_SCHEMA,
            "version": 2,
            "round": self.round,
            "release_version": self.release_version,
            "git_head": self.git_head,
            "artifacts": map_records,
        }
        self._write_json(self.map_path, self.artifact_map)
        with mock.patch.object(
                engineering_builder, "_semantic_verify",
                return_value=self._semantic_summary()):
            self.closure = engineering_builder.build(
                self.artifacts, self.map_path,
                "2026-07-25T00:00:00Z")
        self.closure_path = (
            self.artifacts / set_verifier.ENGINEERING_CLOSURE_NAME)
        engineering_builder.write_private(
            self.closure_path, self.closure)
        for position in range(supporting_files):
            path = (
                self.artifacts / "raw" /
                f"matrix-{position:02d}.stdout.txt")
            path.parent.mkdir(mode=0o700, exist_ok=True)
            path.write_text(
                f"sealed raw input {position}\n", encoding="utf-8")
            path.chmod(0o600)
        self.index_path = self.root / "round38-index.json"
        self.manifest_path = self.root / "round38-set.json"
        self.rebind()

    def _semantic_summary(self) -> dict[str, object]:
        return {
            "strict_source_files": 1,
            "agent_os_source_files": 1,
            "runtime_files": 1,
            "native_vm_variant": "sandbox",
            "product_git_head": self.git_head,
            "release_git_head": self.release_git_head,
            "baseline_path": engineering_builder._round_baseline_path(
                self.release_version),
            "release_version": self.release_version,
        }

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, sort_keys=True) + "\n",
            encoding="utf-8")
        path.chmod(0o600)

    def rebind(self) -> None:
        self.index = index_builder.build_index(
            self.evidence, POLICY, [],
            "2026-07-25T00:00:00+00:00")
        self._write_json(self.index_path, self.index)
        self.manifest = set_builder.build_manifest(
            self.index_path, self.evidence, POLICY,
            self.round, self.release_version,
            set_verifier.ENGINEERING_PROFILE)
        self._write_json(self.manifest_path, self.manifest)

    def verify(self) -> dict[str, object]:
        with mock.patch.object(
                engineering_builder, "_semantic_verify",
                return_value=self._semantic_summary()):
            return set_verifier.verify(
                self.manifest_path, self.index_path,
                self.evidence, POLICY)

    def build_and_publish(self) -> dict[str, object]:
        output_root = self.root / "evidence-indexes"
        output_root.mkdir(mode=0o700, exist_ok=True)
        output = output_root / (
            "heptatrader-round38-evidence-set-manifest-v2.json")
        with mock.patch.object(
                engineering_builder, "_semantic_verify",
                return_value=self._semantic_summary()):
            return set_builder.build_and_publish(
                self.root, self.index_path, self.evidence, POLICY,
                output, self.round, self.release_version,
                set_verifier.ENGINEERING_PROFILE)


class ReleaseValidationEvidenceFixture:
    def __init__(
            self, root: Path, *, round_number: int = 95,
            supporting_files: int = 1) -> None:
        self.root = root.resolve()
        self.round = round_number
        self.release_version = f"0.1.0-beta.1-round{round_number}"
        self.evidence = self.root / "release-evidence"
        self.evidence.mkdir(mode=0o700)
        self.directory_name = (
            f"heptatrader-round{round_number}-engineering-artifacts-v1")
        self.directory = self.evidence / self.directory_name
        self.directory.mkdir(mode=0o700)
        self.roles = list(release_builder.CORE_EVIDENCE_ROLES)
        self.roles.extend(
            f"{release_builder.SUPPORTING_ROLE_PREFIX}{position:04d}"
            for position in range(1, supporting_files + 1))
        self.paths_by_role: dict[str, str] = {}
        input_manifest = release_builder.build_input_manifest(
            round_number=self.round,
            release_version=self.release_version,
            generated_at="2026-08-03T00:00:00Z",
            roots={
                "delivery-artifact-root":
                    f"{self.directory_name}/delivery",
                "verification-artifact-root":
                    f"{self.directory_name}/verification",
            },
            components={
                role: f"{self.directory_name}/components/{role}.json"
                for role in release_builder.COMPONENT_ROLES
            })
        for role in self.roles:
            relative = (
                f"{self.directory_name}/{release_builder.INPUT_MANIFEST_NAME}"
                if role == "release-input-manifest" else
                f"{self.directory_name}/{role}.evidence")
            path = self.evidence / relative
            if role == "release-input-manifest":
                path.write_bytes(
                    release_builder.canonical_json(input_manifest) + b"\n")
            else:
                path.write_bytes((role + "\n").encode("ascii"))
            path.chmod(0o600)
            self.paths_by_role[role] = relative
        self.index_path = self.root / "release-index.json"
        self.index = index_builder.build_index(
            self.evidence, POLICY, sorted(self.paths_by_role.values()),
            "2026-01-01T00:00:00+00:00")
        self._write_json(self.index_path, self.index)
        indexed = {record["path"]: record for record in self.index["files"]}
        self.critical_files = sorted(({
            "role": role,
            "path": path,
            "sha256": indexed[path]["sha256"],
            "size": indexed[path]["size"],
            "mode": indexed[path]["mode"],
        } for role, path in self.paths_by_role.items()),
            key=lambda record: record["role"])
        input_record = next(
            record for record in self.critical_files
            if record["role"] == "release-input-manifest")
        baseline_record = next(
            record for record in self.critical_files
            if record["role"] == "source-baseline-manifest")
        self.local = {
            "profile": set_verifier.RELEASE_PROFILE,
            "round": self.round,
            "release_version": self.release_version,
            "artifact_directory": self.directory_name,
            "input_manifest_sha256": input_record["sha256"],
            "source_baseline": {
                "path": "source-baseline-manifest.json",
                "sha256": baseline_record["sha256"],
                "size": baseline_record["size"],
                "mode": baseline_record["mode"],
            },
            "source_lineage": {
                "git_head": "a" * 40,
                "strict_source_bundle_sha256": "b" * 64,
            },
            "verification": {
                "fresh_until": "2026-08-04T00:00:00Z",
            },
            "delivery": {"four_soaks_eight_rounds_verified": True},
            "native": {
                "schema": "hepta.execution-native-systemd-aggregate.v6",
                "certification_level":
                    "native-disposable-vm-agent-os-watch-runtime-rootful-systemd",
                "distinct_native_vms": 3,
                "distinct_provisioner_attested_instances": 3,
                "external_instance_receipts_verified": True,
                "runtime_contract_verified": True,
            },
            "critical_files": self.critical_files,
        }
        self.roles_by_path = {
            record["path"]: record["role"]
            for record in self.critical_files
        }
        self.manifest_path = self.root / "release-set.json"
        with self.mock_release_index_roles():
            self.manifest = set_builder.build_manifest(
                self.index_path, self.evidence, POLICY,
                self.round, self.release_version,
                set_verifier.RELEASE_PROFILE)
        self._write_json(self.manifest_path, self.manifest)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def mock_release_index_roles(self):
        input_bytes = (
            self.evidence /
            self.paths_by_role["release-input-manifest"]).read_bytes()
        return mock.patch.object(
            set_verifier, "_release_index_roles",
            return_value=(
                deepcopy(self.roles_by_path), self.directory_name,
                deepcopy(self.local), input_bytes))

    def verify(self) -> dict[str, object]:
        with self.mock_release_index_roles():
            return set_verifier.verify(
                self.manifest_path, self.index_path,
                self.evidence, POLICY)

    def build_manifest(self) -> dict[str, object]:
        with self.mock_release_index_roles():
            return set_builder.build_manifest(
                self.index_path, self.evidence, POLICY,
                self.round, self.release_version,
                set_verifier.RELEASE_PROFILE)


class EvidenceSetTests(unittest.TestCase):
    def test_release_validation_profile_round95_builds_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-release-set-") as temporary:
            fixture = ReleaseValidationEvidenceFixture(Path(temporary))
            report = fixture.verify()
            self.assertEqual(
                report["profile"], set_verifier.RELEASE_PROFILE)
            self.assertEqual(report["round"], 95)
            self.assertEqual(
                report["role_count"],
                len(release_builder.CORE_EVIDENCE_ROLES) + 1)
            self.assertEqual(
                report["source_baseline"], fixture.local["source_baseline"])
            self.assertTrue(report["four_soaks_eight_rounds_verified"])
            self.assertEqual(report["native_distinct_vms"], 3)
            self.assertFalse(report["release_authorized"])
            self.assertFalse(report["paper_authorized"])
            self.assertFalse(report["live_authorized"])

    def test_release_validation_profile_is_generic_beyond_round95(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-release-set-") as temporary:
            fixture = ReleaseValidationEvidenceFixture(
                Path(temporary), round_number=112, supporting_files=0)
            report = fixture.verify()
            self.assertEqual(report["round"], 112)
            self.assertEqual(
                report["release_version"], "0.1.0-beta.1-round112")
            self.assertEqual(
                report["role_count"],
                len(release_builder.CORE_EVIDENCE_ROLES))

    def test_release_validation_supporting_roles_are_contiguous(self) -> None:
        roles = sorted([
            *release_builder.CORE_EVIDENCE_ROLES,
            "supporting-evidence-0002",
        ])
        with self.assertRaisesRegex(
                set_verifier.EvidenceSetError, "not contiguous"):
            set_verifier._release_required_roles(roles)

    def test_release_validation_semantic_verifier_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-release-set-") as temporary:
            fixture = ReleaseValidationEvidenceFixture(Path(temporary))
            with mock.patch.object(
                    release_builder, "verify_local_input_manifest",
                    side_effect=release_builder.ReleaseValidationError(
                        "injected semantic failure")):
                with self.assertRaisesRegex(
                        set_verifier.EvidenceSetError,
                        "full P0 semantic verification failed"):
                    set_verifier._release_index_roles(
                        fixture.index, fixture.evidence,
                        fixture.round, fixture.release_version)

    def test_release_validation_index_must_cover_exact_p0_closure(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-release-set-") as temporary:
            fixture = ReleaseValidationEvidenceFixture(Path(temporary))
            local = deepcopy(fixture.local)
            local["critical_files"] = local["critical_files"][:-1]
            with (mock.patch.object(
                    release_builder, "verify_local_input_manifest",
                    return_value=local),
                  mock.patch.object(
                    release_builder, "release_index_roles",
                    return_value=(
                        {record["path"]: record["role"]
                         for record in local["critical_files"]}, local)),
                  self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "exact P0 file closure")):
                set_verifier._release_index_roles(
                    fixture.index, fixture.evidence,
                    fixture.round, fixture.release_version)

    def test_round38_retention_policy_covers_and_trusts_complete_tree(
            self) -> None:
        policy, digest = index_builder.load_policy(POLICY)
        rule, tier = index_builder.classify(
            "heptatrader-round38-engineering-artifacts-v1/"
            "raw/repository-ib-off.sidecar.json",
            policy)
        self.assertEqual(rule, "round38-engineering-evidence")
        self.assertEqual(tier, "certification")
        trust = json.loads(TRUST_POLICY.read_text(encoding="utf-8"))
        self.assertIn(
            digest, trust["allowed_retention_policy_sha256"])

    def test_round38_engineering_builder_publishes_and_self_verifies(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round38-set-") as temporary:
            fixture = Round38EngineeringEvidenceFixture(
                Path(temporary), supporting_files=1)
            report = fixture.build_and_publish()
            self.assertEqual(
                report["profile"], set_verifier.ENGINEERING_PROFILE)
            self.assertEqual(
                report["role_count"],
                len(set_verifier.ENGINEERING_CORE_ROLES) + 1)

    def test_round38_engineering_profile_semantically_closes_core_roles(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round38-set-") as temporary:
            fixture = Round38EngineeringEvidenceFixture(Path(temporary))
            report = fixture.verify()
            self.assertEqual(
                report["profile"],
                set_verifier.ENGINEERING_PROFILE)
            self.assertEqual(
                report["role_count"],
                len(set_verifier.ENGINEERING_CORE_ROLES))
            self.assertEqual(
                set(report["roles"]),
                set(set_verifier.ENGINEERING_CORE_ROLES))
            self.assertEqual(
                report["engineering_artifact_root"],
                fixture.artifact_directory_name)
            self.assertEqual(
                report["product_git_head"], fixture.git_head)
            self.assertEqual(
                report["release_git_head"],
                fixture.release_git_head)
            self.assertFalse(report["production_passed"])
            self.assertFalse(report["release_authorized"])

    def test_round38_complete_tree_assigns_all_supporting_evidence(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round38-set-") as temporary:
            fixture = Round38EngineeringEvidenceFixture(
                Path(temporary), supporting_files=2)
            report = fixture.verify()
            self.assertEqual(
                report["coverage"], "full-index-eligible-tree")
            self.assertIn(
                "supporting-evidence-0001", report["roles"])
            self.assertIn(
                "supporting-evidence-0002", report["roles"])
            self.assertEqual(
                report["role_count"],
                len(set_verifier.ENGINEERING_CORE_ROLES) + 2)
            raw_records = [
                record for record in fixture.index["files"]
                if "/raw/" in record["path"]
            ]
            self.assertEqual(len(raw_records), 2)
            self.assertTrue(all(
                record["tier"] == "certification"
                for record in raw_records))

    def test_round38_artifact_map_must_match_closure_role_paths(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round38-set-") as temporary:
            fixture = Round38EngineeringEvidenceFixture(Path(temporary))
            artifact_map = deepcopy(fixture.artifact_map)
            first, second = artifact_map["artifacts"][:2]
            first["path"], second["path"] = (
                second["path"], first["path"])
            fixture._write_json(fixture.map_path, artifact_map)
            map_bytes = fixture.map_path.read_bytes()
            closure = deepcopy(fixture.closure)
            closure["source"]["artifact_map_sha256"] = hashlib.sha256(
                map_bytes).hexdigest()
            binding = next(
                record for record in closure["artifacts"]
                if record["role"] == "engineering-artifact-map")
            binding["sha256"] = hashlib.sha256(map_bytes).hexdigest()
            binding["size"] = len(map_bytes)
            fixture._write_json(fixture.closure_path, closure)
            fixture.rebind()
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "full semantic verification failed|"
                    "map and closure role/path bindings differ"):
                fixture.verify()

    def test_round38_engineering_semantic_verifier_is_mandatory(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round38-set-") as temporary:
            fixture = Round38EngineeringEvidenceFixture(Path(temporary))
            with mock.patch.object(
                    set_verifier.engineering_closure_verifier, "verify",
                    side_effect=
                    set_verifier.engineering_closure_verifier.VerificationError(
                        "injected semantic failure")):
                with self.assertRaisesRegex(
                        set_verifier.EvidenceSetError,
                        "full semantic verification failed"):
                    set_verifier.verify(
                        fixture.manifest_path, fixture.index_path,
                        fixture.evidence, POLICY)

    def test_round38_supporting_roles_must_be_contiguous(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round38-set-") as temporary:
            fixture = Round38EngineeringEvidenceFixture(
                Path(temporary), supporting_files=1)
            forged = deepcopy(fixture.manifest)
            index = forged["required_roles"].index(
                "supporting-evidence-0001")
            forged["required_roles"][index] = "supporting-evidence-0002"
            artifact = next(
                record for record in forged["artifacts"]
                if record["role"] == "supporting-evidence-0001")
            artifact["role"] = "supporting-evidence-0002"
            forged["required_roles"].sort()
            forged["artifacts"].sort(key=lambda record: record["role"])
            fixture._write_json(fixture.manifest_path, forged)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError, "not contiguous"):
                fixture.verify()

    def test_round38_profile_cannot_certify_another_round(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round38-set-") as temporary:
            fixture = Round38EngineeringEvidenceFixture(Path(temporary))
            with self.assertRaisesRegex(
                    set_builder.EvidenceSetBuildError,
                    "restricted to Round38"):
                set_builder.build_manifest(
                    fixture.index_path, fixture.evidence, POLICY,
                    39, "0.1.0-beta.1-round39",
                    set_verifier.ENGINEERING_PROFILE)

    def test_inventory_builder_and_closure_share_exact_artifact_lineage(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            root = Path(temporary).resolve()
            fixture = EvidenceSetFixture(root)
            artifact_root = (
                fixture.evidence / fixture.delivery_root_name)
            physical = artifact_root / "source-baseline-manifest.json"
            inventory = operations.wrapper_inventory(
                root, {"jobs": {}},
                round_number=fixture.round,
                release_version=fixture.release_version,
                source_baseline=physical.relative_to(root),
                source_baseline_artifact_root=
                    artifact_root.relative_to(root))
            self.assertEqual(inventory["source_baseline"], {
                "path": physical.name,
                "sha256": hashlib.sha256(physical.read_bytes()).hexdigest(),
                "size": physical.stat().st_size,
                "mode": "0600",
            })
            fixture.documents["repository-inventory"] = inventory
            fixture._write_json(
                fixture.evidence /
                fixture.paths_by_role["repository-inventory"],
                inventory)
            fixture.rebind()
            output = fixture.evidence / (
                "heptatrader-round99-evidence-set-manifest-v2.json")
            report = fixture.build_and_publish(output)
            self.assertEqual(
                report["source_baseline"],
                inventory["source_baseline"])

    def test_production_builder_publishes_and_self_verifies_manifest_v2(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            root = Path(temporary).resolve()
            fixture = EvidenceSetFixture(root)
            output = (
                fixture.evidence /
                "heptatrader-round99-evidence-set-manifest-v2.json")
            report = fixture.build_and_publish(output)
            published = output.stat()
            second_report = fixture.build_and_publish(output)
            confirmed = output.stat()
            self.assertEqual(report["status"], "verified")
            self.assertEqual(second_report, report)
            self.assertEqual(report["evidence_set_id"], "round99-certification")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                (published.st_dev, published.st_ino, published.st_mtime_ns,
                 published.st_ctime_ns),
                (confirmed.st_dev, confirmed.st_ino, confirmed.st_mtime_ns,
                 confirmed.st_ctime_ns))
            self.assertEqual(
                output.read_bytes(),
                index_builder.canonical_json(
                    fixture.build_manifest()))
            self.assertFalse(report["source_files_deleted"])
            self.assertFalse(report["source_removal_authorized"])
            self.assertFalse(report["paper_authorized"])
            self.assertFalse(report["live_authorized"])
            self.assertEqual(
                sorted(path.name for path in fixture.evidence.iterdir()),
                sorted([
                    fixture.paths_by_role["repository-inventory"],
                    fixture.paths_by_role["round-closure"],
                    fixture.delivery_root_name,
                    output.name,
                ]))

    def test_builder_maps_only_the_complete_trusted_role_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            root = Path(temporary).resolve()
            fixture = EvidenceSetFixture(root)
            fixture.explicit_paths = [
                fixture.paths_by_role["repository-inventory"]]
            fixture.rebind()
            with self.assertRaisesRegex(
                    set_builder.EvidenceSetBuildError,
                    "complete trusted role set"):
                fixture.build_manifest()

        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            root = Path(temporary).resolve()
            fixture = EvidenceSetFixture(root)
            extra = fixture.evidence / "heptatrader-unmapped-inventory-v2.json"
            extra.write_text("{}\n", encoding="utf-8")
            extra.chmod(0o600)
            fixture.explicit_paths.append(extra.name)
            fixture.index = index_builder.build_index(
                fixture.evidence, POLICY, fixture.explicit_paths,
                "2026-01-01T00:00:00+00:00")
            fixture._write_json(fixture.index_path, fixture.index)
            with self.assertRaisesRegex(
                    set_builder.EvidenceSetBuildError,
                    "exactly one trusted evidence role"):
                fixture.build_manifest()

    def test_builder_enforces_output_name_location_and_safe_destination(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            root = Path(temporary).resolve()
            fixture = EvidenceSetFixture(root)
            with self.assertRaisesRegex(
                    set_builder.EvidenceSetBuildError, "must be named"):
                fixture.build_and_publish(
                    fixture.evidence / "evidence-set.json")
            outside = root / (
                "heptatrader-round99-evidence-set-manifest-v2.json")
            with self.assertRaisesRegex(
                    set_builder.EvidenceSetBuildError,
                    "evidence root or evidence-indexes"):
                fixture.build_and_publish(outside)
            expected = fixture.evidence / (
                "heptatrader-round99-evidence-set-manifest-v2.json")
            target = fixture.evidence / "protected.json"
            target.write_text("protected\n", encoding="utf-8")
            target.chmod(0o600)
            expected.symlink_to(target.name)
            with self.assertRaisesRegex(
                    set_builder.EvidenceSetBuildError, "destination is unsafe"):
                fixture.build_and_publish(expected)
            self.assertEqual(
                target.read_text(encoding="utf-8"), "protected\n")
            expected.unlink()
            different = b"{\"immutable\":true}\n"
            expected.write_bytes(different)
            expected.chmod(0o600)
            with self.assertRaisesRegex(
                    set_builder.EvidenceSetBuildError,
                    "immutable and differs"):
                fixture.build_and_publish(expected)
            self.assertEqual(expected.read_bytes(), different)

    def test_complete_tree_manifest_is_published_outside_indexed_tree(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            root = Path(temporary).resolve()
            fixture = EvidenceSetFixture(root, complete_tree=True)
            inside = fixture.evidence / (
                "heptatrader-round99-evidence-set-manifest-v2.json")
            with self.assertRaisesRegex(
                    set_builder.EvidenceSetBuildError,
                    "complete-tree.*outside"):
                fixture.build_and_publish(inside)
            outside_root = root / "evidence-indexes"
            outside_root.mkdir(mode=0o700)
            outside = outside_root / inside.name
            report = fixture.build_and_publish(outside)
            self.assertEqual(
                report["coverage"], "full-index-eligible-tree")
            self.assertTrue(outside.is_file())
            self.assertFalse(inside.exists())

    def test_builder_rejects_parent_replacement_without_stray_publication(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            root = Path(temporary).resolve()
            fixture = EvidenceSetFixture(root)
            output = fixture.evidence / (
                "heptatrader-round99-evidence-set-manifest-v2.json")
            detached = root / "detached-evidence"
            target = root / "attacker"
            target.mkdir(mode=0o700)
            real_link = set_builder.os.link
            swapped = False

            def link_after_swap(*arguments: object, **keywords: object) -> None:
                nonlocal swapped
                if not swapped:
                    swapped = True
                    fixture.evidence.rename(detached)
                    fixture.evidence.symlink_to(
                        target, target_is_directory=True)
                real_link(*arguments, **keywords)

            with mock.patch.object(
                    set_builder.os, "link", side_effect=link_after_swap):
                with self.assertRaises(set_builder.EvidenceSetBuildError):
                    fixture.build_and_publish(output)
            self.assertTrue(swapped)
            self.assertEqual(list(target.iterdir()), [])
            self.assertFalse((detached / output.name).exists())
            self.assertEqual(
                list(detached.glob(f".{output.name}.*.tmp")), [])

    def test_builder_rolls_back_if_temporary_unlink_fails_after_link(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            root = Path(temporary).resolve()
            fixture = EvidenceSetFixture(root)
            output = fixture.evidence / (
                "heptatrader-round99-evidence-set-manifest-v2.json")
            real_unlink = set_builder.os.unlink
            failed = False

            def fail_first_temporary_unlink(
                    path: object, *arguments: object,
                    **keywords: object) -> None:
                nonlocal failed
                if str(path).startswith(f".{output.name}.") and not failed:
                    failed = True
                    raise OSError("injected temporary unlink failure")
                real_unlink(path, *arguments, **keywords)

            with mock.patch.object(
                    set_builder.os, "unlink",
                    side_effect=fail_first_temporary_unlink):
                with self.assertRaises(set_builder.EvidenceSetBuildError):
                    fixture.build_and_publish(output)
            self.assertTrue(failed)
            self.assertFalse(output.exists())
            self.assertEqual(
                list(fixture.evidence.glob(f".{output.name}.*.tmp")), [])

    def test_manifest_defined_set_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            report = fixture.verify()
            self.assertEqual(report["status"], "verified")
            self.assertEqual(report["coverage"], "manifest-defined")
            self.assertEqual(report["role_count"], 9)
            self.assertEqual(
                report["roles"],
                sorted([
                    "repository-inventory", "round-closure",
                    *closure_builder.REQUIRED_ARTIFACT_ROLES,
                ]))
            self.assertEqual(
                report["delivery_artifact_root"],
                fixture.delivery_root_name)
            self.assertEqual(report["round"], fixture.round)
            self.assertEqual(
                report["release_version"], fixture.release_version)
            self.assertEqual(
                report["source_baseline"], fixture.source_baseline)
            self.assertFalse(report["source_files_deleted"])
            self.assertFalse(report["source_removal_authorized"])
            self.assertFalse(report["paper_authorized"])
            self.assertFalse(report["live_authorized"])

    def test_complete_tree_set_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(
                Path(temporary), complete_tree=True)
            report = fixture.verify()
            self.assertEqual(
                report["coverage"], "full-index-eligible-tree")
            forged = deepcopy(fixture.manifest)
            forged["coverage"] = "manifest-defined"
            fixture.write_manifest(forged)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "complete-tree.*must declare.*full-index-eligible-tree"):
                fixture.verify()

    def test_explicit_index_cannot_claim_full_tree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            forged = deepcopy(fixture.manifest)
            forged["coverage"] = "full-index-eligible-tree"
            fixture.write_manifest(forged)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "explicit.*cannot claim full-tree"):
                fixture.verify()

    def test_required_roles_are_fixed_unique_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            for roles in (
                    ["round-closure", "round-closure"],
                    ["round-closure", "repository-inventory"],
                    ["ROUND-CLOSURE", "repository-inventory"]):
                forged = deepcopy(fixture.manifest)
                forged["required_roles"] = roles
                fixture.write_manifest(forged)
                with self.subTest(roles=roles):
                    with self.assertRaisesRegex(
                            set_verifier.EvidenceSetError,
                            "fixed, unique, and canonical"):
                        fixture.verify()
            forged = deepcopy(fixture.manifest)
            forged["required_roles"] = ["custom-a", "custom-b"]
            forged["artifacts"][0]["role"] = "custom-a"
            forged["artifacts"][1]["role"] = "custom-b"
            fixture.write_manifest(forged)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError, "trusted profile"):
                fixture.verify()

    def test_roles_obey_trusted_path_and_tier_contracts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            forged = deepcopy(fixture.manifest)
            first_path = forged["artifacts"][0]["path"]
            forged["artifacts"][0]["path"] = forged["artifacts"][1]["path"]
            forged["artifacts"][1]["path"] = first_path
            fixture.write_manifest(forged)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "trusted path/tier contract"):
                fixture.verify()

    def test_each_role_has_one_unique_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            forged = deepcopy(fixture.manifest)
            forged["artifacts"][1]["path"] = (
                forged["artifacts"][0]["path"])
            fixture.write_manifest(forged)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "cannot satisfy multiple roles"):
                fixture.verify()
            forged = deepcopy(fixture.manifest)
            forged["artifacts"][1]["role"] = (
                forged["artifacts"][0]["role"])
            fixture.write_manifest(forged)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "trusted path/tier contract|roles do not exactly match"):
                fixture.verify()

    def test_roles_must_cover_exact_index_path_set(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            forged = deepcopy(fixture.manifest)
            forged["artifacts"][1]["path"] = (
                "heptatrader-round99-alternate-delivery-closure-v1.json")
            fixture.write_manifest(forged)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "trusted path/tier contract|exact indexed path set"):
                fixture.verify()

    def test_delivery_artifacts_must_derive_one_unique_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            role = "no-git-soak-ibapi-off"
            original = fixture.paths_by_role[role]
            alternate = original.replace(
                "semantic-delivery-artifacts-v1/",
                "semantic-delivery-artifacts-v2/")
            destination = fixture.evidence / alternate
            destination.parent.mkdir(mode=0o700)
            (fixture.evidence / original).rename(destination)
            fixture.paths_by_role[role] = alternate
            fixture.roles_by_path.pop(original)
            fixture.roles_by_path[alternate] = role
            fixture.explicit_paths = sorted(fixture.roles_by_path)
            fixture.rebind()
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "one unique artifact root"):
                fixture.verify()

    def test_full_delivery_verifier_rejects_rebound_fake_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            bundle = (
                fixture.evidence /
                fixture.paths_by_role["strict-source-bundle"])
            bundle.write_bytes(b"mutated fixture tar payload\n")
            bundle.chmod(0o600)
            closure = deepcopy(fixture.documents["round-closure"])
            record = next(
                artifact for artifact in closure["artifacts"]
                if artifact["role"] == "strict-source-bundle")
            record["sha256"] = hashlib.sha256(bundle.read_bytes()).hexdigest()
            record["size"] = bundle.stat().st_size
            closure_path = (
                fixture.evidence /
                fixture.paths_by_role["round-closure"])
            closure_path.write_bytes(
                closure_builder.canonical_json(closure) + b"\n")
            closure_path.chmod(0o600)
            fixture.documents["round-closure"] = closure
            fixture.rebind()
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "full delivery verification failed"):
                fixture.verify()

    def test_path_hash_size_mode_and_tier_are_exactly_bound(self) -> None:
        mutations = {
            "path":
                "heptatrader-round99-alternate-delivery-closure-v1.json",
            "sha256": "00" * 32,
            "size": 999,
            "mode": "0400",
            "tier": "forensic",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-set-") as temporary:
                    fixture = EvidenceSetFixture(Path(temporary))
                    forged = deepcopy(fixture.manifest)
                    forged["artifacts"][1][field] = value
                    fixture.write_manifest(forged)
                    expected = (
                        "trusted path/tier contract|exact indexed path set"
                        if field == "path"
                        else (
                            "trusted path/tier contract" if field == "tier"
                            else f"{field} binding drift"))
                    with self.assertRaisesRegex(
                            set_verifier.EvidenceSetError, expected):
                        fixture.verify()

    def test_index_digest_and_record_closure_are_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            for field in ("sha256", "records_sha256"):
                forged = deepcopy(fixture.manifest)
                forged["index"][field] = "00" * 32
                fixture.write_manifest(forged)
                with self.subTest(field=field):
                    with self.assertRaisesRegex(
                            set_verifier.EvidenceSetError,
                            "index binding drift"):
                        fixture.verify()

    def test_safety_flags_cannot_authorize_removal_or_trading(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            for field in (
                    "source_files_deleted", "source_removal_authorized",
                    "paper_authorized", "live_authorized"):
                forged = deepcopy(fixture.manifest)
                forged[field] = True
                fixture.write_manifest(forged)
                with self.subTest(field=field):
                    with self.assertRaisesRegex(
                            set_verifier.EvidenceSetError,
                            "safety boundary"):
                        fixture.verify()

    def test_payload_drift_between_verification_passes_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            real_validate = set_verifier._validate_delivery_closure
            mutated = False

            def validate_then_mutate(*args, **kwargs):
                nonlocal mutated
                result = real_validate(*args, **kwargs)
                path = (
                    fixture.evidence /
                    fixture.paths_by_role["round-closure"])
                path.write_bytes(b"{\"passed\":false}\\n")
                path.chmod(0o600)
                mutated = True
                return result

            with mock.patch.object(
                    set_verifier, "_validate_delivery_closure",
                    side_effect=validate_then_mutate):
                with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "full delivery verification failed|payload/tree changed"):
                    fixture.verify()
            self.assertTrue(mutated)

    def test_duplicate_manifest_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            original = fixture.manifest_path.read_text(encoding="utf-8")
            field = f'"schema": "{set_verifier.MANIFEST_SCHEMA}",'
            fixture.manifest_path.write_text(
                original.replace(
                    field, field + field, 1),
                encoding="utf-8")
            fixture.manifest_path.chmod(0o600)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError, "duplicate"):
                fixture.verify()

    def test_unhashable_schema_values_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            mutations = (
                ("coverage", ["full-tree"]),
                ("index.selection_mode", ["explicit"]),
                ("artifact.tier", ["certification"]),
            )
            for field, value in mutations:
                forged = deepcopy(fixture.manifest)
                if field == "coverage":
                    forged["coverage"] = value
                elif field == "index.selection_mode":
                    forged["index"]["selection_mode"] = value
                else:
                    forged["artifacts"][0]["tier"] = value
                fixture.write_manifest(forged)
                with self.subTest(field=field):
                    with self.assertRaises(set_verifier.EvidenceSetError):
                        fixture.verify()

    def test_v1_manifest_and_inventory_cannot_certify(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            legacy = deepcopy(fixture.manifest)
            legacy["schema"] = "hepta.evidence-set-manifest.v1"
            legacy["version"] = 1
            legacy["profile"] = "round-closure-inventory-v1"
            fixture.write_manifest(legacy)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError, "unsupported"):
                fixture.verify()

        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            legacy_inventory = deepcopy(
                fixture.documents["repository-inventory"])
            legacy_inventory["schema"] = "hepta.ops-inventory.v1"
            legacy_inventory["version"] = 1
            fixture.replace_document(
                "repository-inventory", legacy_inventory)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "unsupported repository-inventory"):
                fixture.verify()

    def test_manifest_release_identity_is_exact_and_strictly_typed(
            self) -> None:
        mutations = (
            ("round", True),
            ("round", 99.0),
            ("round", 98),
            ("release_version", "9.9.9-round99"),
            ("evidence_set_id", "round98-certification"),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-set-") as temporary:
                    fixture = EvidenceSetFixture(Path(temporary))
                    forged = deepcopy(fixture.manifest)
                    forged[field] = value
                    fixture.write_manifest(forged)
                    with self.assertRaises(set_verifier.EvidenceSetError):
                        fixture.verify()

    def test_artifact_round_and_release_identity_must_match_manifest(
            self) -> None:
        mutations = (
            ("repository-inventory", "round", 98),
            ("repository-inventory", "release_version", "9.9.9-round99"),
            ("round-closure", "round", 98),
            ("round-closure", "release_version", "9.9.9-round99"),
        )
        for role, field, value in mutations:
            with self.subTest(role=role, field=field):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-set-") as temporary:
                    fixture = EvidenceSetFixture(Path(temporary))
                    document = deepcopy(fixture.documents[role])
                    document[field] = value
                    if field == "round":
                        document["release_version"] = (
                            f"0.1.0-beta.1-round{value}")
                    fixture.replace_document(role, document)
                    with self.assertRaisesRegex(
                            set_verifier.EvidenceSetError,
                            "release identity"):
                        fixture.verify()

    def test_inventory_and_closure_source_lineage_must_match(self) -> None:
        for role in ("repository-inventory", "round-closure"):
            with self.subTest(role=role):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-set-") as temporary:
                    fixture = EvidenceSetFixture(Path(temporary))
                    document = deepcopy(fixture.documents[role])
                    if role == "repository-inventory":
                        document["source_baseline"]["sha256"] = "cd" * 32
                    else:
                        source = next(
                            artifact for artifact in document["artifacts"]
                            if artifact["role"] ==
                            "source-baseline-manifest")
                        source["sha256"] = "cd" * 32
                    fixture.replace_document(role, document)
                    with self.assertRaisesRegex(
                            set_verifier.EvidenceSetError,
                            "source-baseline lineage differ"):
                        fixture.verify()

    def test_inventory_records_require_exact_semantic_closure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            inventory = deepcopy(
                fixture.documents["repository-inventory"])
            inventory["wrapper_count"] = 1
            inventory["wrapper_counts"]["canonical"] = 1
            inventory["wrappers"] = [{}]
            fixture.replace_document("repository-inventory", inventory)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "wrapper record fields"):
                fixture.verify()

        with tempfile.TemporaryDirectory(prefix="hepta-set-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            inventory = deepcopy(
                fixture.documents["repository-inventory"])
            inventory["implementation_count"] = 1
            inventory["implementations"] = [{
                "path": "scripts/not-an-openclaw-fx-implementation.py",
                "sha256": "ab" * 32,
                "size": 1,
                "lifecycle": "compat",
            }]
            fixture.replace_document("repository-inventory", inventory)
            with self.assertRaisesRegex(
                    set_verifier.EvidenceSetError,
                    "implementations record metadata"):
                fixture.verify()

    def test_schema_versions_require_json_integers(self) -> None:
        for invalid in (True, 2.0):
            with self.subTest(manifest_version=invalid):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-set-") as temporary:
                    fixture = EvidenceSetFixture(Path(temporary))
                    forged = deepcopy(fixture.manifest)
                    forged["version"] = invalid
                    fixture.write_manifest(forged)
                    with self.assertRaisesRegex(
                            set_verifier.EvidenceSetError, "unsupported"):
                        fixture.verify()
        for invalid in (True, 2.0):
            with self.subTest(inventory_version=invalid):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-set-") as temporary:
                    fixture = EvidenceSetFixture(Path(temporary))
                    inventory = deepcopy(
                        fixture.documents["repository-inventory"])
                    inventory["version"] = invalid
                    fixture.replace_document(
                        "repository-inventory", inventory)
                    with self.assertRaisesRegex(
                            set_verifier.EvidenceSetError,
                            "unsupported repository-inventory"):
                        fixture.verify()


if __name__ == "__main__":
    unittest.main(verbosity=2)
