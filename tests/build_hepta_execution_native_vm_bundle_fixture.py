#!/usr/bin/env python3

"""Offline contract tests for deterministic native-VM rootfs bundles."""

from pathlib import Path
import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import build_hepta_execution_native_vm_bundle as bundle  # noqa: E402
import run_hepta_execution_rootful_systemd_gate as shared  # noqa: E402


POLICY = REPOSITORY / "tests/native_systemd/platform-policy-v1.json"


class NativeVmBundleFixtureTests(unittest.TestCase):
    def test_rootful_repository_stage_is_bound_to_clean_source(self) -> None:
        source = (
            REPOSITORY / "scripts/run_hepta_execution_rootful_systemd_gate.py")
        tree = ast.parse(source.read_text(encoding="utf-8", errors="strict"))
        provisioned: set[tuple[str, str, str]] = set()
        for node in ast.walk(tree):
            if (not isinstance(node, ast.Assign) or
                    not any(
                        isinstance(target, ast.Name) and
                        target.id == "staged_sources"
                        for target in node.targets) or
                    not isinstance(node.value, ast.Dict)):
                continue
            for key, value in zip(node.value.keys, node.value.values):
                if (key is None or not isinstance(value, ast.Tuple) or
                        len(value.elts) != 2):
                    continue
                repository_source, mode = value.elts
                if (not isinstance(repository_source, ast.BinOp) or
                        not isinstance(repository_source.op, ast.Div) or
                        not isinstance(repository_source.left, ast.Name) or
                        repository_source.left.id != "root" or
                        not isinstance(repository_source.right, ast.Constant) or
                        not isinstance(repository_source.right.value, str) or
                        not isinstance(mode, ast.Constant) or
                        type(mode.value) is not int):
                    continue
                destination = ast.literal_eval(key)
                self.assertIsInstance(destination, str)
                provisioned.add((
                    repository_source.right.value,
                    destination,
                    format(mode.value, "04o")))
        self.assertTrue(provisioned)
        declared = {
            (source_path, destination, mode)
            for source_path, destinations in
            bundle.SOURCE_STAGE_BINDINGS.items()
            for destination, mode in destinations
        }
        self.assertEqual(provisioned - declared, set())

    def test_reviewed_platform_policy_passes(self) -> None:
        contents = POLICY.read_bytes()
        policy = bundle.validate_platform_policy(contents)
        self.assertEqual(policy["schema"], bundle.POLICY_SCHEMA)
        self.assertFalse(policy["paper_authorized"])
        self.assertEqual(
            bundle.SCHEMA, "hepta.execution-native-vm-bundle.v7")
        self.assertEqual(
            bundle.PROVISIONING_SCHEMA,
            "hepta.execution-native-vm-provisioning-manifest.v6")
        self.assertEqual(
            bundle.IMAGE_SCHEMA,
            "hepta.execution-native-vm-image-manifest.v4")
        self.assertEqual(
            bundle.AGENT_OS_RUNTIME_INPUT_SCHEMA,
            "hepta.agent-os-native-vm-runtime-input-manifest.v1")
        self.assertEqual(
            bundle.SOURCE_BUILD_LINEAGE_SCHEMA,
            "hepta.execution-native-vm-source-build-lineage.v3")
        self.assertEqual(
            bundle.IBAPI_SOURCE_MANIFEST_SCHEMA,
            "hepta.ibapi-sdk-source-manifest.v1")
        self.assertEqual(
            bundle.CAUSAL_BUILD_RECEIPT_SCHEMA,
            "hepta.execution-native-vm-fresh-causal-build.v1")

    def test_platform_policy_drift_fails_closed(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        policy["network"]["default_routes"] = 1
        with self.assertRaisesRegex(bundle.BundleError, "network"):
            bundle.validate_platform_policy(bundle.canonical_json(policy))

    def test_deterministic_tar_round_trips(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-bundle-fixture-") as temporary:
            root = Path(temporary)
            rootfs = root / "rootfs"
            (rootfs / "usr/libexec").mkdir(parents=True, mode=0o755)
            for directory in (rootfs, rootfs / "usr", rootfs / "usr/libexec"):
                os.chmod(directory, 0o755)
            shared.write_private(
                rootfs / "usr/libexec/hepta-fixture", b"fixture\n", 0o755)
            records = bundle.rootfs_records(rootfs)
            first = root / "first.tar"
            second = root / "second.tar"
            bundle.deterministic_tar(rootfs, first)
            bundle.deterministic_tar(rootfs, second)
            bundle.validate_tar(first, records)
            bundle.validate_tar(second, records)
            self.assertEqual(shared.sha256_file(first), shared.sha256_file(second))

    def test_tar_owner_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-bundle-fixture-") as temporary:
            archive = Path(temporary) / "unsafe.tar"
            with tarfile.open(archive, mode="w") as output:
                info = tarfile.TarInfo("usr/libexec/hepta-fixture")
                info.uid = 1000
                info.gid = 0
                info.uname = "user"
                info.gname = "root"
                info.mode = 0o755
                info.mtime = 0
                contents = b"fixture\n"
                info.size = len(contents)
                output.addfile(info, io.BytesIO(contents))
            record = {
                "path": "usr/libexec/hepta-fixture", "mode": "0755",
                "uid": 0, "gid": 0, "size": 8,
                "sha256": shared.hashlib.sha256(b"fixture\n").hexdigest(),
            }
            with self.assertRaisesRegex(bundle.BundleError, "metadata"):
                bundle.validate_tar(archive, [record])

    def test_forbidden_identity_and_formal_elf_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-bundle-fixture-") as temporary:
            rootfs = Path(temporary)
            for relative in (bundle.SENTINEL_PATH, bundle.FORMAL_IB_PATH):
                path = rootfs / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(path.parent, 0o755)
                shared.write_private(path, b"forbidden\n", 0o400)
                with self.assertRaisesRegex(bundle.BundleError, "forbidden"):
                    bundle.ensure_forbidden_absent(rootfs)
                path.unlink()

    def test_static_agent_os_payload_forbids_runtime_fixtures(self) -> None:
        self.assertIn(
            Path("etc/heptatrader/hepta-supervisor-lease.key"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/libexec/hepta-paper-receipt-contracts"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/libexec/hepta_agent_trust_domain.py"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/libexec/hepta-shadow-watch-collector"),
            bundle.AGENT_OS_STATIC_PATHS)
        for helper in (
                "hepta-p1-shadow-host-controller",
                "hepta-p1-load-probe-validator",
                "build-hepta-p1-observation-policy",
                "hepta-p1-shadow-observer-controller",
                "hepta-p1-shadow-admission-launcher",
                "hepta-shadow-host-installer",
                "hepta-p1-watch-profile-deployer",
                "hepta-p1-watch-activation-transaction",
                "hepta-bounded-shadow-closure-verifier",
                "hepta-official-source-capture",
                "hepta_bounded_shadow_observer.py",
                "hepta_market_context_builder.py",
                "hepta_market_evidence_normalizer.py",
                "hepta_market_official_source_extractor.py",
                "hepta_eurusd_confirmed_momentum_strategy.py",
                "hepta_shadow_market_history.py",
                "hepta_strategy_shadow_runner.py",
                "hepta_strategy_contracts.py",
                "validate_hepta_strategy_decision_receipt.py"):
            self.assertIn(
                Path("usr/libexec") / helper,
                bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/share/heptatrader/strategies/"
                 "eurusd-confirmed-momentum-shadow-v2.json"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/libexec/hepta-shadow-watch-exporter"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/libexec/hepta-shadow-watch-custodian"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/libexec/hepta-broker-egress-policy"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/share/heptatrader/hepta-broker-network-policy-v1.json"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("etc/heptatrader/"
                 "hepta-agent-trust-domain-paper-identities-v1.json"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            bundle.AGENT_OS_RUNTIME_PROVISIONING /
            "hepta-agent-trust-domain-paper-identities-v1.json",
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/lib/systemd/system/"
                 "hepta-shadow-watch-collector@.service"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/lib/systemd/system/"
                 "hepta-shadow-watch-collector@.timer"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/lib/systemd/system/"
                 "hepta-shadow-watch-export@.service"),
            bundle.AGENT_OS_STATIC_PATHS)
        for dependency in (
                "hepta-tool-gateway@.service",
                "hepta-tool-gateway@.socket",
                "hepta-tool-session-supervisor@.socket"):
            self.assertIn(
                Path("usr/lib/systemd/system") / dependency,
                bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/lib/systemd/system/"
                 "hepta-shadow-watch-custodian@.service"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/lib/systemd/system/"
                 "hepta-shadow-watch-custodian-reconcile@.service"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/lib/systemd/system/"
                 "hepta-shadow-watch-custodian-reconcile@.timer"),
            bundle.AGENT_OS_STATIC_PATHS)
        for unit in (
                "hepta-broker-egress-policy.service",
                "hepta-p1-watch-activation.service",
                "hepta-p1-watch-activation-reconcile.service",
                "hepta-p1-watch-activation-reconcile.timer"):
            self.assertIn(
                Path("usr/lib/systemd/system") / unit,
                bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/share/doc/heptatrader/examples/"
                 "hepta-shadow-watch-domain.env.example"),
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertIn(
            Path("usr/share/doc/heptatrader/examples/"
                 "hepta-tool-gateway-domain.env.example"),
            bundle.AGENT_OS_STATIC_PATHS)
        expected_bindings = {
            "scripts/hepta_shadow_watch_exporter.py": (
                ("usr/libexec/hepta-shadow-watch-exporter", "0755"),),
            "systemd/hepta-shadow-watch-export@.service": (
                ("usr/lib/systemd/system/"
                 "hepta-shadow-watch-export@.service", "0644"),),
            "systemd/hepta-tool-gateway@.service": (
                ("usr/lib/systemd/system/hepta-tool-gateway@.service",
                 "0644"),),
            "systemd/hepta-tool-gateway@.socket": (
                ("usr/lib/systemd/system/hepta-tool-gateway@.socket",
                 "0644"),),
            "systemd/hepta-tool-session-supervisor@.socket": (
                ("usr/lib/systemd/system/"
                 "hepta-tool-session-supervisor@.socket", "0644"),),
            "systemd/hepta-shadow-watch-domain.env.example": (
                ("usr/share/doc/heptatrader/examples/"
                 "hepta-shadow-watch-domain.env.example", "0644"),),
            "systemd/hepta-tool-gateway-domain.env.example": (
                ("usr/share/doc/heptatrader/examples/"
                 "hepta-tool-gateway-domain.env.example", "0644"),),
            "scripts/hepta_p1_watch_profile_deployer.py": (
                ("usr/libexec/hepta-p1-watch-profile-deployer", "0755"),),
            "scripts/hepta_shadow_host_installer.py": (
                ("usr/libexec/hepta-shadow-host-installer", "0755"),),
            "scripts/hepta_p1_watch_activation_transaction.py": (
                (("usr/libexec/"
                  "hepta-p1-watch-activation-transaction"), "0755"),),
            "systemd/hepta-p1-watch-activation.service": (
                (("usr/lib/systemd/system/"
                  "hepta-p1-watch-activation.service"), "0644"),),
            "systemd/hepta-p1-watch-activation-reconcile.service": (
                (("usr/lib/systemd/system/"
                  "hepta-p1-watch-activation-reconcile.service"),
                 "0644"),),
            "systemd/hepta-p1-watch-activation-reconcile.timer": (
                (("usr/lib/systemd/system/"
                  "hepta-p1-watch-activation-reconcile.timer"), "0644"),),
            "systemd/"
            "hepta-agent-trust-domain-paper-identities-v1.json.example": (
                (("etc/heptatrader/"
                  "hepta-agent-trust-domain-paper-identities-v1.json"),
                 "0600"),
                (("usr/local/share/hepta-agent-os-e2e/provisioning/"
                  "hepta-agent-trust-domain-paper-identities-v1.json"),
                 "0600")),
        }
        for source, destinations in expected_bindings.items():
            with self.subTest(source=source):
                self.assertEqual(
                    bundle.SOURCE_STAGE_BINDINGS.get(source), destinations)
        for runtime_path in bundle.AGENT_OS_RUNTIME_PATHS:
            self.assertNotIn(runtime_path, bundle.AGENT_OS_STATIC_PATHS)
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-bundle-fixture-") as temporary:
            rootfs = Path(temporary)
            runtime = rootfs / bundle.AGENT_OS_RUNTIME_PATHS[1]
            runtime.parent.mkdir(parents=True)
            shared.write_private(runtime, b"fabricated-token\n", 0o600)
            with self.assertRaisesRegex(bundle.BundleError, "forbidden"):
                bundle.ensure_forbidden_absent(rootfs)

    def test_runtime_gate_inputs_are_staged_without_runtime_claim(self) -> None:
        self.assertIn(
            bundle.AGENT_OS_RUNTIME_INNER_GATE,
            bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        self.assertIn(
            bundle.AGENT_OS_RUNTIME_INPUT_MANIFEST,
            bundle.AGENT_OS_STATIC_PATHS)
        self.assertNotIn(
            bundle.AGENT_OS_RUNTIME_INPUT_MANIFEST,
            bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        self.assertNotIn(
            Path("usr/libexec/hepta-paper-receipt-contracts"),
            bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        self.assertIn(
            Path("usr/libexec/hepta_agent_trust_domain.py"),
            bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        self.assertIn(
            Path("usr/libexec/hepta-shadow-watch-collector"),
            bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        for helper in (
                "hepta-p1-shadow-host-controller",
                "hepta-p1-load-probe-validator",
                "build-hepta-p1-observation-policy",
                "hepta-p1-shadow-observer-controller",
                "hepta-p1-shadow-admission-launcher",
                "hepta-shadow-host-installer",
                "hepta-p1-watch-profile-deployer",
                "hepta-p1-watch-activation-transaction",
                "hepta-bounded-shadow-closure-verifier",
                "hepta-official-source-capture",
                "hepta_bounded_shadow_observer.py",
                "hepta_market_context_builder.py",
                "hepta_market_evidence_normalizer.py",
                "hepta_market_official_source_extractor.py",
                "hepta_eurusd_confirmed_momentum_strategy.py",
                "hepta_shadow_market_history.py",
                "hepta_strategy_shadow_runner.py",
                "hepta_strategy_contracts.py",
                "validate_hepta_strategy_decision_receipt.py"):
            self.assertIn(
                Path("usr/libexec") / helper,
                bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        self.assertIn(
            Path("usr/share/heptatrader/strategies/"
                 "eurusd-confirmed-momentum-shadow-v2.json"),
            bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        self.assertNotIn(
            Path("usr/libexec/hepta-shadow-watch-custodian"),
            bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        self.assertIn(
            Path("usr/libexec/hepta-broker-egress-policy"),
            bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        self.assertIn(
            Path("usr/share/heptatrader/hepta-broker-network-policy-v1.json"),
            bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        self.assertIn(
            bundle.AGENT_OS_RUNTIME_PROVISIONING /
            "hepta-agent-trust-domain-paper-identities-v1.json",
            bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        for unit in (
                "hepta-broker-egress-policy.service",
                "hepta-p1-watch-activation.service",
                "hepta-p1-watch-activation-reconcile.service",
                "hepta-p1-watch-activation-reconcile.timer"):
            self.assertIn(
                Path("usr/lib/systemd/system") / unit,
                bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        for path in bundle.AGENT_OS_RUNTIME_PATHS:
            self.assertNotIn(path, bundle.AGENT_OS_RUNTIME_GATE_PATHS)
        self.assertEqual(len(bundle.AGENT_OS_WATCH_TOOLS), 11)
        self.assertIn("watch.get_snapshot", bundle.AGENT_OS_WATCH_TOOLS)
        self.assertNotIn("risk.preview_order", bundle.AGENT_OS_WATCH_TOOLS)
        self.assertFalse(any(
            name.startswith("trade.")
            for name in bundle.AGENT_OS_WATCH_TOOLS))

    def test_manifest_terminology_is_non_self_referential(self) -> None:
        source = (REPOSITORY /
                  "scripts/build_hepta_execution_native_vm_bundle.py").read_text(
                      encoding="utf-8")
        self.assertNotIn("vm_image_sha256", source)
        self.assertIn("vm_image_manifest_sha256", source)

    @staticmethod
    def _source_manifest(
            root: Path, records: list[dict[str, object]],
            *, version: str = "fixture") -> tuple[bytes, dict[str, object],
                                                  dict[str, dict[str, object]]]:
        files_sha256 = hashlib.sha256(json.dumps(
            records, ensure_ascii=True, separators=(",", ":"),
            sort_keys=True).encode()).hexdigest()
        document = {
            "schema": "hepta.clean-source-bundle.v2",
            "bundle_class": "strict-source-only",
            "root": f"heptatrader-{version}",
            "version": version,
            "git_head": "a" * 40,
            "file_count": len(records),
            "files_sha256": files_sha256,
            "files": records,
        }
        contents = (json.dumps(document, sort_keys=True) + "\n").encode()
        index = {
            str(record["path"]): record for record in records}
        return contents, document, index

    def test_exact_no_git_source_tree_is_required(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-source-lineage-") as temporary:
            parent = Path(temporary)
            root = parent / "heptatrader-fixture"
            (root / "src").mkdir(parents=True)
            (root / ".hepta").mkdir()
            payload = b"int fixture = 1;\n"
            source = root / "src/fixture.cpp"
            source.write_bytes(payload)
            os.chmod(source, 0o644)
            records = [{
                "path": "src/fixture.cpp", "mode": "0644",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }]
            manifest_bytes, manifest, index = self._source_manifest(
                root, records)
            internal = root / ".hepta/source-bundle-manifest.json"
            internal.write_bytes(manifest_bytes)
            os.chmod(internal, 0o644)
            result = bundle.scan_exact_source_tree(
                root, manifest_bytes, manifest, index)
            self.assertTrue(result["exact_file_closure"])
            self.assertFalse(result["git_metadata_present"])

            (root / ".git").mkdir()
            with self.assertRaisesRegex(bundle.BundleError, "no-git"):
                bundle.scan_exact_source_tree(
                    root, manifest_bytes, manifest, index)
            (root / ".git").rmdir()

            extra = root / "src/stale-build.o"
            extra.write_bytes(b"old-v7-bytes")
            with self.assertRaisesRegex(bundle.BundleError, "file closure"):
                bundle.scan_exact_source_tree(
                    root, manifest_bytes, manifest, index)

    def test_internal_manifest_cannot_be_cross_bundled(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-source-cross-") as temporary:
            parent = Path(temporary)
            root = parent / "heptatrader-fixture"
            (root / "src").mkdir(parents=True)
            (root / ".hepta").mkdir()
            payload = b"fixture\n"
            path = root / "src/value.txt"
            path.write_bytes(payload)
            os.chmod(path, 0o644)
            records = [{
                "path": "src/value.txt", "mode": "0644",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }]
            manifest_bytes, manifest, index = self._source_manifest(
                root, records)
            internal = root / ".hepta/source-bundle-manifest.json"
            internal.write_bytes(manifest_bytes + b" ")
            os.chmod(internal, 0o644)
            with self.assertRaisesRegex(
                    bundle.BundleError, "internal source-bundle manifest"):
                bundle.scan_exact_source_tree(
                    root, manifest_bytes, manifest, index)

    def test_current_staged_sources_must_match_clean_manifest(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-repository-lineage-") as temporary:
            repository = Path(temporary)
            index: dict[str, dict[str, object]] = {}
            paths = set(bundle.SOURCE_STAGE_BINDINGS) | set(
                bundle.REVIEWED_BUILD_SOURCE_PATHS)
            for relative in sorted(paths):
                path = repository / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                contents = (relative + "\n").encode()
                path.write_bytes(contents)
                mode = (
                    "0755" if any(
                        destination_mode == "0755"
                        for _destination, destination_mode in
                        bundle.SOURCE_STAGE_BINDINGS.get(relative, ()))
                    else "0644")
                os.chmod(path, int(mode, 8))
                index[relative] = {
                    "path": relative, "mode": mode, "size": len(contents),
                    "sha256": hashlib.sha256(contents).hexdigest(),
                }
            staged, reviewed = bundle.validate_repository_source_inputs(
                repository, index)
            self.assertEqual(len(staged), len(bundle.SOURCE_STAGE_BINDINGS))
            self.assertEqual(
                len(reviewed), len(bundle.REVIEWED_BUILD_SOURCE_PATHS))
            victim = repository / "scripts/run_hepta_execution_native_systemd_gate.py"
            victim.write_bytes(b"current-v4-drift\n")
            with self.assertRaisesRegex(
                    bundle.BundleError, "differs from clean bundle"):
                bundle.validate_repository_source_inputs(repository, index)

    def test_build_cache_cannot_cross_clean_source_tree(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-build-cross-") as temporary:
            parent = Path(temporary)
            source_root = parent / "heptatrader-fixture"
            other_root = parent / "heptatrader-other"
            build_dir = parent / "build-off"
            source_root.mkdir()
            other_root.mkdir()
            build_dir.mkdir()
            source_file = source_root / "fixture.cpp"
            source_file.write_bytes(b"int main() { return 0; }\n")
            os.chmod(source_file, 0o644)
            source_index = {
                "fixture.cpp": {
                    "path": "fixture.cpp", "mode": "0644",
                    "size": source_file.stat().st_size,
                    "sha256": hashlib.sha256(
                        source_file.read_bytes()).hexdigest(),
                },
            }
            cache = (
                "CMAKE_BUILD_TYPE:STRING=Release\n"
                "BUILD_TESTING:BOOL=ON\n"
                "CMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON\n"
                "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE:BOOL=OFF\n"
                "HEPTA_ENABLE_IBAPI:BOOL=OFF\n"
                "IBAPI_ROOT:PATH=\n"
                "CMAKE_GENERATOR:INTERNAL=Ninja\n"
                "CMAKE_CXX_COMPILER:FILEPATH=/usr/bin/c++\n"
                f"CMAKE_CACHEFILE_DIR:INTERNAL={build_dir}\n"
                f"CMAKE_HOME_DIRECTORY:INTERNAL={source_root}\n")
            (build_dir / "CMakeCache.txt").write_text(cache, encoding="utf-8")
            compile_commands = [{
                "directory": str(build_dir),
                "command": f"/usr/bin/c++ -c {source_file}",
                "file": str(source_file),
            }]
            (build_dir / "compile_commands.json").write_text(
                json.dumps(compile_commands), encoding="utf-8")
            provenance = {
                "manifest_sha256": "1" * 64,
                "files_sha256": "2" * 64,
                "file_count": 1,
            }
            _build, record, _local = bundle.validate_lineage_build(
                build_dir, ibapi=False, source_root=source_root,
                source_provenance=provenance, source_index=source_index)
            self.assertEqual(record["source_root"], "heptatrader-fixture")
            self.assertIsNone(record["ibapi_source_manifest"])
            self.assertIsNone(record["ibapi_source_manifest_sha256"])
            self.assertEqual(record["ibapi_source_file_count"], 0)
            self.assertIsNone(record["ibapi_source_files_sha256"])
            with self.assertRaisesRegex(
                    bundle.BundleError, "CMAKE_HOME_DIRECTORY"):
                bundle.validate_lineage_build(
                    build_dir, ibapi=False, source_root=other_root,
                    source_provenance=provenance,
                    source_index=source_index)

    def test_ibapi_sdk_manifest_binds_real_bytes_and_exact_tree(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-ibapi-lineage-") as temporary:
            sdk = Path(temporary) / "cppclient"
            (sdk / "client").mkdir(parents=True)
            os.chmod(sdk, 0o755)
            os.chmod(sdk / "client", 0o755)
            source = sdk / "client/EClient.cpp"
            header = sdk / "client/EClient.h"
            source.write_bytes(b"int sdk_fixture = 1;\n")
            header.write_bytes(b"#pragma once\n")
            os.chmod(source, 0o644)
            os.chmod(header, 0o644)

            first, first_index = bundle.scan_ibapi_source_tree(sdk)
            self.assertEqual(first["schema"],
                             bundle.IBAPI_SOURCE_MANIFEST_SCHEMA)
            self.assertEqual(first["file_count"], 2)
            self.assertEqual(
                first_index["client/EClient.cpp"]["sha256"],
                hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertNotEqual(
                first_index["client/EClient.cpp"]["sha256"],
                hashlib.sha256(b"ibapi/client/EClient.cpp").hexdigest())

            source.write_bytes(b"int sdk_fixture = 2;\n")
            with self.assertRaisesRegex(
                    bundle.BundleError, "source closure changed"):
                bundle.validate_ibapi_source_tree_unchanged(sdk, first)
            second, second_index = bundle.scan_ibapi_source_tree(sdk)
            self.assertNotEqual(
                first_index["client/EClient.cpp"]["sha256"],
                second_index["client/EClient.cpp"]["sha256"])
            self.assertNotEqual(first["files_sha256"], second["files_sha256"])

            extra = sdk / "client/Extra.cpp"
            extra.write_bytes(b"int extra = 1;\n")
            os.chmod(extra, 0o644)
            with self.assertRaisesRegex(
                    bundle.BundleError, "source closure changed"):
                bundle.validate_ibapi_source_tree_unchanged(sdk, second)
            third, _third_index = bundle.scan_ibapi_source_tree(sdk)
            self.assertEqual(third["file_count"], 3)
            self.assertNotEqual(second["files_sha256"], third["files_sha256"])

    def test_ibapi_build_record_and_compile_aggregate_follow_sdk_bytes(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-ibapi-build-") as temporary:
            parent = Path(temporary)
            source_root = parent / "heptatrader-fixture"
            build_dir = parent / "build-on"
            sdk = parent / "cppclient"
            source_root.mkdir()
            build_dir.mkdir()
            sdk.mkdir()
            for directory in (source_root, build_dir, sdk):
                os.chmod(directory, 0o755)
            source_file = source_root / "fixture.cpp"
            source_file.write_bytes(b"int main() { return 0; }\n")
            sdk_file = sdk / "EClient.cpp"
            sdk_file.write_bytes(b"int sdk = 1;\n")
            os.chmod(source_file, 0o644)
            os.chmod(sdk_file, 0o644)
            source_index = {
                "fixture.cpp": {
                    "path": "fixture.cpp", "mode": "0644",
                    "size": source_file.stat().st_size,
                    "sha256": hashlib.sha256(
                        source_file.read_bytes()).hexdigest(),
                },
            }
            cache = (
                "CMAKE_BUILD_TYPE:STRING=Release\n"
                "BUILD_TESTING:BOOL=ON\n"
                "CMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON\n"
                "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE:BOOL=OFF\n"
                "HEPTA_ENABLE_IBAPI:BOOL=ON\n"
                f"IBAPI_ROOT:PATH={sdk}\n"
                "CMAKE_GENERATOR:INTERNAL=Ninja\n"
                "CMAKE_CXX_COMPILER:FILEPATH=/usr/bin/c++\n"
                f"CMAKE_CACHEFILE_DIR:INTERNAL={build_dir}\n"
                f"CMAKE_HOME_DIRECTORY:INTERNAL={source_root}\n")
            (build_dir / "CMakeCache.txt").write_text(
                cache, encoding="utf-8")
            compile_commands = [{
                "directory": str(build_dir),
                "command": f"/usr/bin/c++ -c {source_file}",
                "file": str(source_file),
            }, {
                "directory": str(build_dir),
                "command": f"/usr/bin/c++ -c {sdk_file}",
                "file": str(sdk_file),
            }]
            (build_dir / "compile_commands.json").write_text(
                json.dumps(compile_commands), encoding="utf-8")
            provenance = {
                "manifest_sha256": "1" * 64,
                "files_sha256": "2" * 64,
                "file_count": 1,
            }
            _build, first, _local = bundle.validate_lineage_build(
                build_dir, ibapi=True, source_root=source_root,
                source_provenance=provenance, source_index=source_index)
            sdk_record = first["ibapi_source_manifest"]["files"][0]
            self.assertEqual(
                sdk_record["sha256"],
                hashlib.sha256(sdk_file.read_bytes()).hexdigest())

            sdk_file.write_bytes(b"int sdk = 2;\n")
            _build, second, _local = bundle.validate_lineage_build(
                build_dir, ibapi=True, source_root=source_root,
                source_provenance=provenance, source_index=source_index)
            self.assertNotEqual(
                first["ibapi_source_manifest_sha256"],
                second["ibapi_source_manifest_sha256"])
            self.assertNotEqual(
                first["compile_sources_sha256"],
                second["compile_sources_sha256"])

    def test_fresh_causal_rebuild_rejects_sdk_b_elf_after_sdk_a_restore(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-causal-rebuild-") as temporary:
            parent = Path(temporary)
            source_root = parent / "source"
            sdk = parent / "ibapi"
            source_root.mkdir(mode=0o755)
            sdk.mkdir(mode=0o755)
            (source_root / "main.cpp").write_text(
                "extern int sdk_value();\n"
                "int main() { return sdk_value() == 17 ? 0 : 1; }\n",
                encoding="utf-8")
            sdk_source = sdk / "EClient.cpp"
            sdk_a = b"int sdk_value() { return 17; }\n"
            sdk_b = b"int sdk_value() { return 29; }\n"
            sdk_source.write_bytes(sdk_a)
            (source_root / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.16)\n"
                "project(causal_fixture LANGUAGES C CXX)\n"
                "option(BUILD_TESTING \"\" ON)\n"
                "option(HEPTA_ENABLE_LEGACY_0DTE_BRIDGE \"\" OFF)\n"
                "option(HEPTA_ENABLE_IBAPI \"\" OFF)\n"
                "set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELEASE "
                "\"${CMAKE_BINARY_DIR}/bin/Release\")\n"
                "if(NOT HEPTA_ENABLE_IBAPI OR IBAPI_ROOT STREQUAL \"\")\n"
                "  message(FATAL_ERROR \"fixture requires IBAPI\")\n"
                "endif()\n"
                "add_executable(fixture-sdk-app main.cpp "
                "${IBAPI_ROOT}/EClient.cpp)\n",
                encoding="utf-8")
            for path in (source_root / "main.cpp",
                         source_root / "CMakeLists.txt", sdk_source):
                os.chmod(path, 0o644)

            cmake = Path(shutil.which("cmake") or "")
            ninja = Path(shutil.which("ninja") or "")
            c_compiler = Path(shutil.which("cc") or "")
            cxx_compiler = Path(shutil.which("c++") or "")
            self.assertTrue(all(path.is_file() for path in (
                cmake, ninja, c_compiler, cxx_compiler)))

            def real_build(build_dir: Path) -> None:
                environment = {
                    "PATH": "/usr/bin:/bin", "HOME": str(parent),
                    "TMPDIR": str(parent), "LANG": "C", "LC_ALL": "C",
                    "TZ": "UTC", "SOURCE_DATE_EPOCH": "0",
                    "CFLAGS": "", "CXXFLAGS": "", "LDFLAGS": "",
                }
                subprocess.run([
                    str(cmake), "-S", str(source_root), "-B", str(build_dir),
                    "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                    "-DBUILD_TESTING=ON",
                    "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
                    "-DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF",
                    "-DHEPTA_ENABLE_IBAPI=ON", f"-DIBAPI_ROOT={sdk}",
                    f"-DCMAKE_C_COMPILER={c_compiler}",
                    f"-DCMAKE_CXX_COMPILER={cxx_compiler}",
                    f"-DCMAKE_MAKE_PROGRAM={ninja}",
                ], check=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, env=environment)
                subprocess.run([
                    str(cmake), "--build", str(build_dir), "--config",
                    "Release", "--parallel", "1"], check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    env=environment)
                os.chmod(
                    bundle.shared.find_binary(build_dir, "fixture-sdk-app"),
                    0o755)

            profile_a = parent / "profile-a"
            real_build(profile_a)
            fresh_a = parent / "causal-a" / "fresh"
            fresh_a.parent.mkdir(mode=0o700)
            receipt, _tools, _outputs = bundle.fresh_causal_rebuild_lane(
                profile_a, fresh_a, source_root=source_root, ibapi=True,
                ibapi_root=sdk, source_manifest_sha256="1" * 64,
                ibapi_source_manifest_sha256="2" * 64,
                artifact_names=("fixture-sdk-app",),
                build_targets=("fixture-sdk-app",))
            self.assertTrue(receipt["fresh_build_directory_created_empty"])
            self.assertTrue(any(fresh_a.rglob("*.o")))
            self.assertTrue(
                bundle.shared.find_binary(fresh_a, "fixture-sdk-app").is_file())

            sdk_source.write_bytes(sdk_b)
            profile_b = parent / "profile-b"
            real_build(profile_b)
            stale_sha256 = bundle.shared.sha256_file(
                bundle.shared.find_binary(profile_b, "fixture-sdk-app"))
            sdk_source.write_bytes(sdk_a)
            fresh_after_restore = parent / "causal-after-restore" / "fresh"
            fresh_after_restore.parent.mkdir(mode=0o700)
            with self.assertRaisesRegex(
                    bundle.BundleError, "prebuilt artifact differs"):
                bundle.fresh_causal_rebuild_lane(
                    profile_b, fresh_after_restore,
                    source_root=source_root, ibapi=True, ibapi_root=sdk,
                    source_manifest_sha256="1" * 64,
                    ibapi_source_manifest_sha256="2" * 64,
                    artifact_names=("fixture-sdk-app",),
                    build_targets=("fixture-sdk-app",))
            self.assertNotEqual(
                stale_sha256, bundle.shared.sha256_file(
                    bundle.shared.find_binary(
                        fresh_after_restore, "fixture-sdk-app")))

    def test_ibapi_sdk_symlink_and_hardlink_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-ibapi-unsafe-") as temporary:
            sdk = Path(temporary) / "cppclient"
            sdk.mkdir()
            os.chmod(sdk, 0o755)
            source = sdk / "EClient.cpp"
            source.write_bytes(b"int fixture = 1;\n")
            os.chmod(source, 0o644)

            symlink = sdk / "EWrapper.cpp"
            symlink.symlink_to(source.name)
            with self.assertRaisesRegex(
                    bundle.BundleError, "symlink|unsafe regular"):
                bundle.scan_ibapi_source_tree(sdk)
            symlink.unlink()

            root_alias = sdk.parent / "cppclient-alias"
            root_alias.symlink_to(sdk.name, target_is_directory=True)
            with self.assertRaisesRegex(
                    bundle.BundleError, "securely open|changed before"):
                bundle.scan_ibapi_source_tree(root_alias)
            root_alias.unlink()

            hardlink = sdk / "EWrapper.cpp"
            os.link(source, hardlink)
            with self.assertRaisesRegex(
                    bundle.BundleError, "unsafe regular"):
                bundle.scan_ibapi_source_tree(sdk)


if __name__ == "__main__":
    unittest.main(verbosity=2)
