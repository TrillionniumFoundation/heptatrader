#!/usr/bin/env python3

from pathlib import Path
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(1, str(REPOSITORY / "scripts"))

from hepta_ops import cli as operations  # noqa: E402
from hepta_ops import registry as operations_registry  # noqa: E402
import build_heptatrader_evidence_index as evidence_builder  # noqa: E402
import verify_heptatrader_evidence_index as evidence_verifier  # noqa: E402
import verify_heptatrader_vendor_assets as vendor_verifier  # noqa: E402


REGISTRY = REPOSITORY / "ops/hepta-ops-v1.json"
POLICY = REPOSITORY / "policies/heptatrader-evidence-retention-v1.json"
VENDOR_MANIFEST = REPOSITORY / "third_party/ctp/6.7.7/manifest-v1.json"


class OperationsRegistryTests(unittest.TestCase):
    def test_registry_cannot_authorize_trading(self) -> None:
        registry = operations_registry.load_registry(REGISTRY)
        self.assertEqual(registry["schema"], "hepta.ops-registry.v1")
        self.assertGreaterEqual(len(registry["jobs"]), 7)
        for job in registry["jobs"].values():
            self.assertFalse(job["network_allowed"])
            self.assertFalse(job["paper_authorized"])
            self.assertFalse(job["live_authorized"])

    def test_current_registry_job_executables_are_available(self) -> None:
        registry = operations_registry.load_registry(REGISTRY)
        for job_id, job in registry["jobs"].items():
            with self.subTest(job_id=job_id):
                executable = operations._resolve_executable(
                    REPOSITORY, job["executable"])
                self.assertEqual(
                    executable,
                    (REPOSITORY / job["executable"]).resolve(strict=True))

    def test_registry_rejects_duplicate_keys_and_nonfinite_values(self) -> None:
        for payload in (
                b'{"schema":"a","schema":"b","version":1,"jobs":{}}',
                b'{"schema":"hepta.ops-registry.v1","version":NaN,'
                b'"jobs":{}}'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory(
                    prefix="hepta-ops-registry-json-") as temporary:
                path = Path(temporary) / "registry.json"
                path.write_bytes(payload)
                path.chmod(0o600)
                with self.assertRaisesRegex(
                        operations_registry.RegistryError,
                        "strict UTF-8 JSON"):
                    operations_registry.load_registry(path)

    def test_generated_shims_are_deterministic_and_detect_drift(self) -> None:
        registry = operations_registry.load_registry(REGISTRY)
        with tempfile.TemporaryDirectory(prefix="hepta-ops-shims-") as temporary:
            output = Path(temporary)
            count = operations.install_shims(output, registry, False)
            expected = sum(
                len(job["compatibility_wrappers"])
                for job in registry["jobs"].values())
            self.assertEqual(count, expected)
            self.assertEqual(
                operations.install_shims(output, registry, True), expected)
            shim = output / "verify_heptatrader_vendor_assets.sh"
            shim.write_text("drift\n", encoding="utf-8")
            with self.assertRaisesRegex(
                    operations_registry.RegistryError, "shim drift"):
                operations.install_shims(output, registry, True)

    def test_generated_shims_use_reviewed_absolute_interpreters(self) -> None:
        content = operations.shim_bytes(
            "agent-os.units.check", "check_hepta_agent_os_units.sh")
        self.assertTrue(content.startswith(b"#!/bin/sh\n"))
        self.assertIn(b"exec /usr/bin/python3 ", content)
        self.assertNotIn(b"#!/usr/bin/env", content)
        self.assertNotIn(b"exec python3 ", content)
        self.assertNotIn(b"dirname", content)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux runner")
    def test_checked_in_shim_cannot_be_hijacked_through_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-ops-path-") as temporary:
            root = Path(temporary)
            marker = root / "path-interpreter-ran"
            fake = root / "python3"
            fake.write_text(
                "#!/bin/sh\n"
                f"/usr/bin/touch {marker}\n"
                "exit 99\n",
                encoding="utf-8")
            fake.chmod(0o755)
            dirname_marker = root / "path-dirname-ran"
            fake_dirname = root / "dirname"
            fake_dirname.write_text(
                "#!/bin/sh\n"
                f"/usr/bin/touch {dirname_marker}\n"
                "exit 99\n",
                encoding="utf-8")
            fake_dirname.chmod(0o755)
            startup_marker = root / "shell-startup-ran"
            startup = root / "shell-startup"
            startup.write_text(
                f"/usr/bin/touch {startup_marker}\n",
                encoding="utf-8")
            result = subprocess.run(
                [
                    str(REPOSITORY /
                        "compat/hepta-ops-generated/"
                        "check_hepta_agent_os_units.sh"),
                    "--help",
                ],
                cwd=REPOSITORY,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={
                    "PATH": f"{root}:/usr/bin:/bin",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "BASH_ENV": str(startup),
                    "ENV": str(startup),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                check=False,
            )
            self.assertNotEqual(
                result.returncode, 99,
                result.stderr.decode("utf-8", errors="replace"))
            self.assertFalse(marker.exists())
            self.assertFalse(dirname_marker.exists())
            self.assertFalse(startup_marker.exists())

    def test_install_is_anchored_when_output_path_is_replaced(self) -> None:
        registry = operations_registry.load_registry(REGISTRY)
        with tempfile.TemporaryDirectory(prefix="hepta-ops-swap-") as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            detached = root / "detached"
            target = root / "target"
            target.mkdir()
            real_flock = operations.fcntl.flock
            swapped = False

            def flock_then_swap(descriptor: int, operation: int) -> None:
                nonlocal swapped
                real_flock(descriptor, operation)
                if not swapped:
                    swapped = True
                    output.rename(detached)
                    output.symlink_to(target, target_is_directory=True)

            with mock.patch.object(
                    operations.fcntl, "flock",
                    side_effect=flock_then_swap):
                with self.assertRaisesRegex(
                        operations_registry.RegistryError,
                        "shim output changed"):
                    operations.install_shims(output, registry, False)
            self.assertTrue(swapped)
            self.assertEqual(list(target.iterdir()), [])

    def test_group_writable_registry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-ops-registry-") as temporary:
            path = Path(temporary) / "registry.json"
            path.write_bytes(REGISTRY.read_bytes())
            path.chmod(0o664)
            with self.assertRaisesRegex(
                    operations_registry.RegistryError, "group/world writable"):
                operations_registry.load_registry(path)

    def test_registry_rejects_hardlink_and_same_inode_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-ops-registry-") as temporary:
            root = Path(temporary)
            path = root / "registry.json"
            path.write_bytes(REGISTRY.read_bytes())
            path.chmod(0o600)
            hardlink = root / "hardlink.json"
            os.link(path, hardlink)
            with self.assertRaisesRegex(
                    operations_registry.RegistryError, "regular"):
                operations_registry.load_registry(path)
            hardlink.unlink()

            original = path.stat()
            real_read = operations_registry.os.read
            mutated = False

            def read_then_mutate(descriptor: int, size: int) -> bytes:
                nonlocal mutated
                chunk = real_read(descriptor, size)
                if not chunk and not mutated:
                    mutated = True
                    payload = bytearray(path.read_bytes())
                    payload[-2] = 0x20
                    path.write_bytes(payload)
                    os.utime(
                        path,
                        ns=(original.st_atime_ns, original.st_mtime_ns))
                return chunk

            with mock.patch.object(
                    operations_registry.os, "read",
                    side_effect=read_then_mutate):
                with self.assertRaisesRegex(
                        operations_registry.RegistryError, "changed"):
                    operations_registry.load_registry(path)
            self.assertTrue(mutated)

    def test_registry_rejects_shell_injection_and_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-ops-registry-") as temporary:
            payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
            job = payload["jobs"]["vendor.assets.verify"]
            job["compatibility_wrappers"] = [
                'compat/hepta-ops-generated/x";echo-INJECTED.sh']
            path = Path(temporary) / "registry.json"
            path.write_text(
                json.dumps(payload, sort_keys=True), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(operations_registry.RegistryError):
                operations_registry.load_registry(path)
            job["compatibility_wrappers"] = [
                "compat/hepta-ops-generated/vendor-assets.sh"]
            job["paper_authorized"] = True
            path.write_text(
                json.dumps(payload, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(
                    operations_registry.RegistryError, "cannot authorize"):
                operations_registry.load_registry(path)

    def test_check_mode_and_symlink_output_are_non_mutating(self) -> None:
        registry = operations_registry.load_registry(REGISTRY)
        with tempfile.TemporaryDirectory(prefix="hepta-ops-output-") as temporary:
            root = Path(temporary)
            missing = root / "missing"
            with self.assertRaisesRegex(
                    operations_registry.RegistryError, "check mode"):
                operations.install_shims(missing, registry, True)
            self.assertFalse(missing.exists())
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                    operations_registry.RegistryError, "symlink"):
                operations.install_shims(link, registry, False)
            self.assertEqual(list(target.iterdir()), [])

    def test_report_and_telemetry_refuse_symlinks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-ops-output-") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("protected\n", encoding="utf-8")
            target.chmod(0o600)
            link = root / "link"
            link.symlink_to(target.name)
            with self.assertRaises(operations_registry.RegistryError):
                operations._atomic_private_write(link, b"replacement\n")
            with mock.patch.dict(
                    os.environ, {"HEPTA_OPS_TELEMETRY": str(link)}):
                with self.assertRaises(operations_registry.RegistryError):
                    operations.telemetry("fixture.sh")
            self.assertEqual(target.read_text(encoding="utf-8"), "protected\n")

    def test_report_output_is_anchored_during_parent_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-ops-report-") as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir(mode=0o700)
            report = output / "report.json"
            operations._atomic_private_write(report, b"{\"first\":true}\n")
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            detached = root / "detached"
            target = root / "target"
            target.mkdir(mode=0o700)
            real_replace = operations.os.replace
            swapped = False

            def replace_after_swap(*arguments: object, **keywords: object) -> None:
                nonlocal swapped
                if not swapped:
                    swapped = True
                    output.rename(detached)
                    output.symlink_to(target, target_is_directory=True)
                real_replace(*arguments, **keywords)

            with mock.patch.object(
                    operations.os, "replace",
                    side_effect=replace_after_swap):
                with self.assertRaisesRegex(
                        operations_registry.RegistryError,
                        "parent changed"):
                    operations._atomic_private_write(
                        report, b"{\"second\":true}\n")
            self.assertTrue(swapped)
            self.assertEqual(list(target.iterdir()), [])

    def test_telemetry_is_anchored_in_a_private_nonsymlink_directory(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-ops-telemetry-") as temporary:
            root = Path(temporary)
            private = root / "private"
            private.mkdir(mode=0o700)
            destination = private / "compat-wrapper-usage.jsonl"
            with mock.patch.dict(
                    os.environ, {"HEPTA_OPS_TELEMETRY": str(destination)}):
                operations.telemetry("fixture.sh")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertIn(
                '"wrapper":"fixture.sh"',
                destination.read_text(encoding="utf-8"))

            linked_target = root / "linked-target"
            linked_target.mkdir(mode=0o700)
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(
                linked_target, target_is_directory=True)
            linked_destination = (
                linked_parent / "compat-wrapper-usage.jsonl")
            with mock.patch.dict(
                    os.environ,
                    {"HEPTA_OPS_TELEMETRY": str(linked_destination)}):
                with self.assertRaisesRegex(
                        operations_registry.RegistryError,
                        "unsafe parent"):
                    operations.telemetry("fixture.sh")
            self.assertEqual(list(linked_target.iterdir()), [])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux seccomp")
    def test_offline_runner_blocks_network_syscalls(self) -> None:
        script = (
            "import errno,socket;"
            "from hepta_ops.sandbox import apply_no_network_filter;"
            "apply_no_network_filter();"
            "\ntry: socket.socket()\n"
            "except OSError as error: "
            " raise SystemExit(0 if error.errno == errno.EPERM else 2)\n"
            "raise SystemExit(1)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPOSITORY,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        self.assertEqual(result.returncode, 0)

    def test_inventory_preserves_legacy_wrappers(self) -> None:
        registry = operations_registry.load_registry(REGISTRY)
        inventory = operations.wrapper_inventory(REPOSITORY, registry)
        bundled = (REPOSITORY / ".hepta/source-bundle-manifest.json").is_file()
        expected_implementations = sum(
            path.is_file() and not path.is_symlink()
            for path in (REPOSITORY / "scripts").glob("openclaw_fx_*.py"))
        expected_implementation_tests = sum(
            path.is_file() and not path.is_symlink()
            for path in (REPOSITORY / "scripts").glob(
                "test_openclaw_fx_*.py"))
        self.assertEqual(
            inventory["implementation_count"], expected_implementations)
        self.assertEqual(
            inventory["implementation_test_count"],
            expected_implementation_tests)
        if inventory["wrapper_count"]:
            root_wrappers = sum(
                path.is_file() and not path.is_symlink() and
                path.suffix in {".sh", ".ps1"}
                for path in REPOSITORY.iterdir())
            generated = sum(
                len(job["compatibility_wrappers"])
                for job in registry["jobs"].values())
            self.assertEqual(
                inventory["wrapper_count"], root_wrappers + generated)
            self.assertEqual(
                inventory["wrapper_counts"]["canonical"], generated)
            if bundled:
                self.assertEqual(inventory["wrapper_count"], generated)
                self.assertEqual(inventory["implementation_count"], 0)
                self.assertEqual(inventory["implementation_test_count"], 0)
                self.assertEqual(inventory["wrapper_counts"]["compat"], 0)
                self.assertEqual(inventory["wrapper_counts"]["archive"], 0)
        else:
            self.assertEqual(inventory["implementation_count"], 0)

    def test_inventory_v2_strictly_binds_release_and_raw_source_baseline(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-ops-v2-report-") as temporary:
            root = Path(temporary).resolve()
            registry_path = root / "ops/hepta-ops-v1.json"
            registry_path.parent.mkdir(mode=0o700)
            registry_path.write_bytes(REGISTRY.read_bytes())
            registry_path.chmod(0o600)
            registry = operations_registry.load_registry(registry_path)
            shim_root = root / "compat/hepta-ops-generated"
            shim_root.mkdir(parents=True, mode=0o700)
            operations.install_shims(shim_root, registry, False)
            artifact_root = root / "delivery-artifacts"
            artifact_root.mkdir(mode=0o700)
            baseline = artifact_root / "source-baseline-manifest.json"
            payload = b"{\"source\":\"baseline\"}\n"
            baseline.write_bytes(payload)
            baseline.chmod(0o600)
            relative = baseline.relative_to(root)
            relative_artifact_root = artifact_root.relative_to(root)
            inventory = operations.wrapper_inventory(
                root, registry,
                round_number=35,
                release_version="0.1.0-beta.1-round35",
                source_baseline=relative,
                source_baseline_artifact_root=relative_artifact_root)
            self.assertEqual(inventory["schema"], "hepta.ops-inventory.v2")
            self.assertEqual(inventory["version"], 2)
            self.assertEqual(inventory["project_id"], "heptatrader-agent-os")
            self.assertEqual(inventory["round"], 35)
            self.assertEqual(
                inventory["release_version"], "0.1.0-beta.1-round35")
            self.assertEqual(inventory["source_baseline"], {
                "path": baseline.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "mode": "0600",
            })

            output = root / "inventory.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = operations.main([
                    "--root", str(root),
                    "report",
                    "--output", str(output),
                    "--round", "35",
                    "--release-version", "0.1.0-beta.1-round35",
                    "--source-baseline", relative.as_posix(),
                    "--source-baseline-artifact-root",
                    relative_artifact_root.as_posix(),
                ])
            self.assertEqual(result, 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(output.read_text(encoding="utf-8"), stdout.getvalue())
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))[
                    "source_baseline"],
                inventory["source_baseline"])

    def test_inventory_v2_identity_and_baseline_inputs_fail_closed(
            self) -> None:
        registry = operations_registry.load_registry(REGISTRY)
        baseline = Path(
            "release-manifests/"
            "heptatrader-agent-os-v0.1.0-beta.1-round35/manifest.json")
        invalid = (
            {"round_number": 35},
            {
                "round_number": 35,
                "release_version": "0.1.0-beta.1-round34",
                "source_baseline": baseline,
                "source_baseline_artifact_root": baseline.parent,
            },
            {
                "round_number": True,
                "release_version": "0.1.0-beta.1-round1",
                "source_baseline": baseline,
                "source_baseline_artifact_root": baseline.parent,
            },
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(operations_registry.RegistryError):
                    operations.wrapper_inventory(
                        REPOSITORY, registry, **arguments)

        with tempfile.TemporaryDirectory(
                prefix="hepta-ops-baseline-") as temporary:
            root = Path(temporary).resolve()
            baseline_path = root / "manifest.json"
            baseline_path.write_text("{}\n", encoding="utf-8")
            baseline_path.chmod(0o600)
            linked = root / "linked.json"
            linked.symlink_to(baseline_path.name)
            with self.assertRaisesRegex(
                    operations_registry.RegistryError, "symlink"):
                operations._source_baseline_binding(root, linked, root)
            separate = root / "separate"
            separate.mkdir(mode=0o700)
            with self.assertRaisesRegex(
                    operations_registry.RegistryError,
                    "outside its declared artifact root"):
                operations._source_baseline_binding(
                    root, baseline_path, separate)
            hardlink = root / "hardlink.json"
            os.link(baseline_path, hardlink)
            with self.assertRaisesRegex(
                    operations_registry.RegistryError, "regular"):
                operations._source_baseline_binding(
                    root, baseline_path, root)
            hardlink.unlink()
            baseline_path.chmod(0o620)
            with self.assertRaisesRegex(
                    operations_registry.RegistryError, "group/world writable"):
                operations._source_baseline_binding(
                    root, baseline_path, root)
            baseline_path.chmod(0o4600)
            with self.assertRaisesRegex(
                    operations_registry.RegistryError, "special mode"):
                operations._source_baseline_binding(
                    root, baseline_path, root)

    def test_inventory_v2_baseline_read_rejects_parent_replacement(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-ops-baseline-race-") as temporary:
            root = Path(temporary).resolve()
            parent = root / "release-manifests"
            parent.mkdir(mode=0o700)
            baseline = parent / "manifest.json"
            payload = b"{\"source\":true}\n"
            baseline.write_bytes(payload)
            baseline.chmod(0o600)
            target = root / "attacker"
            target.mkdir(mode=0o700)
            (target / baseline.name).write_bytes(payload)
            (target / baseline.name).chmod(0o600)
            detached = root / "detached"
            real_read = operations.os.read
            swapped = False

            def read_then_swap(descriptor: int, size: int) -> bytes:
                nonlocal swapped
                chunk = real_read(descriptor, size)
                if not chunk and not swapped:
                    swapped = True
                    parent.rename(detached)
                    parent.symlink_to(target, target_is_directory=True)
                return chunk

            with mock.patch.object(
                    operations.os, "read", side_effect=read_then_swap):
                with self.assertRaisesRegex(
                        operations_registry.RegistryError,
                        "ancestor changed"):
                    operations._source_baseline_binding(
                        root, Path("release-manifests/manifest.json"),
                        Path("release-manifests"))
            self.assertTrue(swapped)
            self.assertEqual(
                (target / baseline.name).read_bytes(), payload)


class EvidenceLifecycleTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path]:
        evidence = root / "runtime-logs"
        evidence.mkdir()
        (evidence / "execution-boundary-soak-round99-fixture-8-final.json").write_text(
            "{\"passed\":true}\n", encoding="utf-8")
        (evidence / "execution-boundary-soak-round99-fixture-8-final.json").chmod(
            0o600)
        output = root / "index.json"
        return evidence, output

    def test_content_addressed_index_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence, output = self.fixture(root)
            index = evidence_builder.build_index(
                evidence, POLICY, [], "2026-01-01T00:00:00+00:00")
            output.write_text(
                json.dumps(index, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            output.chmod(0o600)
            verified = evidence_verifier.verify(
                output, evidence, POLICY, verify_files=True)
            self.assertEqual(verified["file_count"], 1)
            self.assertEqual(verified["version"], 2)
            self.assertFalse(verified["source_files_deleted"])
            self.assertEqual(
                verified["retention_anchor_status"],
                "pending-external-ingestion-receipt")
            self.assertTrue(verified["files"][0]["git_index_allowed"])
            self.assertEqual(
                verified["files"][0]["object_key"],
                "sha256/" + verified["files"][0]["sha256"])

    def test_shared_ancestor_directory_churn_is_not_payload_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence, _output = self.fixture(root)
            payload = (
                evidence /
                "execution-boundary-soak-round99-fixture-8-final.json")
            original_read = os.read
            churned = False

            def read_with_unrelated_churn(
                    descriptor: int, size: int) -> bytes:
                nonlocal churned
                if not churned:
                    churned = True
                    unrelated = Path("/tmp") / (
                        "hepta-evidence-unrelated-" + os.urandom(8).hex())
                    unrelated.mkdir()
                    unrelated.rmdir()
                return original_read(descriptor, size)

            with mock.patch.object(os, "read", side_effect=read_with_unrelated_churn):
                metadata, size, digest = evidence_builder.stable_digest(payload)
            self.assertTrue(churned)
            self.assertEqual(size, metadata.st_size)
            self.assertEqual(
                digest, hashlib.sha256(payload.read_bytes()).hexdigest())

    def test_payload_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence, output = self.fixture(root)
            index = evidence_builder.build_index(
                evidence, POLICY, [], "2026-01-01T00:00:00+00:00")
            output.write_text(
                json.dumps(index, sort_keys=True) + "\n", encoding="utf-8")
            output.chmod(0o600)
            indexed = evidence / index["files"][0]["path"]
            indexed.write_text("drift\n", encoding="utf-8")
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError,
                    "size drift|digest drift"):
                evidence_verifier.verify(
                    output, evidence, POLICY, verify_files=True)

    def test_symlink_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence = root / "runtime-logs"
            evidence.mkdir()
            target = evidence / "target.json"
            target.write_text("{}\n", encoding="utf-8")
            link = evidence / "heptatrader-round99-closure-v1.json"
            link.symlink_to(target.name)
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError,
                    "not a regular file|escapes"):
                evidence_builder.build_index(
                    evidence, POLICY, [link.name],
                    "2026-01-01T00:00:00+00:00")

    def test_symlink_payload_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence = root / "runtime-logs"
            actual = evidence / "native-vm-bundles-round99-real"
            actual.mkdir(parents=True)
            name = "hepta-native-vm-real.bundle.json"
            payload = actual / name
            payload.write_text("{\"passed\":true}\n", encoding="utf-8")
            payload.chmod(0o600)
            linked = evidence / "linked"
            linked.symlink_to(actual.name, target_is_directory=True)
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError,
                    "ancestor|unstable|unsafe"):
                evidence_builder.build_index(
                    evidence, POLICY, [f"linked/{name}"],
                    "2026-01-01T00:00:00+00:00")

    def test_forged_upload_status_and_metadata_only_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence, output = self.fixture(root)
            index = evidence_builder.build_index(
                evidence, POLICY, [], "2026-01-01T00:00:00+00:00")
            index["object_store_upload_status"] = "verified-without-receipt"
            output.write_text(
                json.dumps(index, sort_keys=True) + "\n", encoding="utf-8")
            output.chmod(0o600)
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError, "safety boundary"):
                evidence_verifier.verify(output, evidence, POLICY)
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError, "metadata-only"):
                evidence_verifier.verify(
                    output, evidence, POLICY, verify_files=False)

    def test_policy_requires_safe_retention_and_upload_boundary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-policy-") as temporary:
            payload = json.loads(POLICY.read_text(encoding="utf-8"))
            payload["object_store"][
                "upload_required_before_source_removal"] = False
            payload["tiers"]["latest"]["retention_days"] = -1
            policy = Path(temporary) / "policy.json"
            policy.write_text(
                json.dumps(payload, sort_keys=True), encoding="utf-8")
            policy.chmod(0o644)
            with self.assertRaises(evidence_builder.EvidenceIndexError):
                evidence_builder.load_policy(policy)

    def test_policy_rejects_duplicate_keys_and_non_finite_numbers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-policy-") as temporary:
            policy = Path(temporary) / "policy.json"
            policy.write_bytes(b'{"schema":"one","schema":"two"}\n')
            policy.chmod(0o644)
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError, "duplicate"):
                evidence_builder.load_policy(policy)
            policy.write_bytes(b'{"value":NaN}\n')
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError, "non-finite"):
                evidence_builder.load_policy(policy)

    def test_native_vm_direct_path_is_classified(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence = root / "runtime-logs"
            path = (
                evidence / "native-vm-bundles-round99-real" /
                "hepta-native-vm-real.bundle.json")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            path.chmod(0o600)
            index = evidence_builder.build_index(
                evidence, POLICY,
                [path.relative_to(evidence).as_posix()],
                "2026-01-01T00:00:00+00:00")
            self.assertEqual(index["files"][0]["tier"], "certification")

    def test_complete_tree_requires_classification_and_tracks_local_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence = root / "runtime-logs"
            evidence.mkdir()
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError, "empty"):
                evidence_builder.build_index(
                    evidence, POLICY, [],
                    "2026-01-01T00:00:00+00:00")
            unknown = evidence / "unknown.bin"
            unknown.write_bytes(b"x")
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError, "not classified"):
                evidence_builder.build_index(
                    evidence, POLICY, [],
                    "2026-01-01T00:00:00+00:00")
            unknown.unlink()
            (evidence / "heptatrader-round99-closure-v1.json").write_text(
                "{}\n", encoding="utf-8")
            (evidence / "heptatrader-round99-closure-v1.json").chmod(0o600)
            (evidence / "compile.log").write_text(
                "local only\n", encoding="utf-8")
            index = evidence_builder.build_index(
                evidence, POLICY, [],
                "2026-01-01T00:00:00+00:00")
            self.assertEqual(index["selection_mode"], "complete-tree")
            self.assertEqual(index["file_count"], 1)
            self.assertEqual(index["excluded_local_only_count"], 1)

    def test_index_output_cannot_overlap_payload_or_follow_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence, _ = self.fixture(root)
            index_root = root / "evidence-indexes"
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError, "escapes"):
                evidence_builder.write_index(
                    evidence / "payload.json", root, evidence, b"{}\n")
            index_root.mkdir()
            index_root.chmod(0o700)
            target = index_root / "target.json"
            target.write_text("protected\n", encoding="utf-8")
            target.chmod(0o600)
            link = index_root / "index.json"
            link.symlink_to(target.name)
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError, "symlink"):
                evidence_builder.write_index(
                    link, root, evidence, b"{}\n")
            self.assertEqual(
                target.read_text(encoding="utf-8"), "protected\n")

    def test_index_output_requires_private_owned_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence, _ = self.fixture(root)
            index_root = root / "evidence-indexes"
            index_root.mkdir()
            index_root.chmod(0o777)
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError,
                    "group/world writable"):
                evidence_builder.write_index(
                    index_root / "index.json", root, evidence, b"{}\n")
            self.assertFalse((index_root / "index.json").exists())

            index_root.chmod(0o700)
            parent = index_root / "unsafe-parent"
            parent.mkdir()
            parent.chmod(0o770)
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError,
                    "group/world writable"):
                evidence_builder.write_index(
                    parent / "index.json", root, evidence, b"{}\n")
            self.assertFalse((parent / "index.json").exists())

    def test_index_output_is_fixed_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence, _ = self.fixture(root)
            index_root = root / "evidence-indexes"
            index_root.mkdir(mode=0o700)
            existing = index_root / "protected.json"
            existing.write_bytes(b"PROTECTED\n")
            existing.chmod(0o600)
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError, "must not be overwritten"):
                evidence_builder.write_index(
                    existing, root, evidence, b"{\"replacement\":true}\n")
            self.assertEqual(existing.read_bytes(), b"PROTECTED\n")

            command = [
                sys.executable,
                str(REPOSITORY / "scripts/build_heptatrader_evidence_index.py"),
                "--evidence-root", str(evidence),
                "--output", "protected.json",
                "--path",
                "execution-boundary-soak-round99-fixture-8-final.json",
                "--generated-at", "2026-01-01T00:00:00+00:00",
            ]
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False, env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PYTHONDONTWRITEBYTECODE": "1",
                })
            self.assertEqual(result.returncode, 78)
            self.assertIn(b"under evidence-indexes", result.stderr)
            self.assertEqual(existing.read_bytes(), b"PROTECTED\n")

            overlap = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY /
                        "scripts/build_heptatrader_evidence_index.py"),
                    "--evidence-root", str(REPOSITORY),
                    "--output", "evidence-indexes/overlap.json",
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False, env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PYTHONDONTWRITEBYTECODE": "1",
                })
            self.assertEqual(overlap.returncode, 78)
            self.assertIn(b"overlaps evidence", overlap.stderr)

    def test_verifier_rejects_symlink_replacement_with_same_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence, output = self.fixture(root)
            index = evidence_builder.build_index(
                evidence, POLICY, [], "2026-01-01T00:00:00+00:00")
            output.write_text(
                json.dumps(index, sort_keys=True) + "\n", encoding="utf-8")
            output.chmod(0o600)
            indexed = evidence / index["files"][0]["path"]
            alternate = evidence / "same-bytes.json"
            alternate.write_bytes(indexed.read_bytes())
            alternate.chmod(0o600)
            indexed.unlink()
            indexed.symlink_to(alternate.name)
            with self.assertRaisesRegex(
                    evidence_builder.EvidenceIndexError,
                    "regular file|unstable|unsafe"):
                evidence_verifier.verify(
                    output, evidence, POLICY, verify_files=True)

    def test_index_output_rejects_post_open_root_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence, _ = self.fixture(root)
            index_root = root / "evidence-indexes"
            index_root.mkdir(mode=0o700)
            forged = root / "forged"
            forged.mkdir(mode=0o700)
            (forged / "index.json").write_bytes(b"FORGED\n")
            (forged / "index.json").chmod(0o600)
            detached = root / "detached-indexes"
            real_replace = os.replace

            real_link = os.link

            def link_then_swap(*args, **kwargs):
                result = real_link(*args, **kwargs)
                real_replace(index_root, detached)
                index_root.symlink_to(forged, target_is_directory=True)
                return result

            try:
                with mock.patch.object(
                        evidence_builder.os, "link",
                        side_effect=link_then_swap):
                    with self.assertRaisesRegex(
                            evidence_builder.EvidenceIndexError,
                            "changed after publication"):
                        evidence_builder.write_index(
                            index_root / "index.json", root,
                            evidence, b"{\"trusted\":true}\n")
                self.assertEqual(
                    (index_root / "index.json").read_bytes(), b"FORGED\n")
                self.assertFalse((detached / "index.json").exists())
            finally:
                if index_root.is_symlink():
                    index_root.unlink()
                if detached.exists():
                    real_replace(detached, index_root)

    def test_index_output_rejects_post_open_parent_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence, _ = self.fixture(root)
            index_root = root / "evidence-indexes"
            index_root.mkdir(mode=0o700)
            parent = index_root / "nested"
            parent.mkdir(mode=0o700)
            forged = root / "forged"
            forged.mkdir(mode=0o700)
            (forged / "index.json").write_bytes(b"FORGED\n")
            (forged / "index.json").chmod(0o600)
            detached = index_root / "detached-nested"
            real_replace = os.replace

            real_link = os.link

            def link_then_swap(*args, **kwargs):
                result = real_link(*args, **kwargs)
                real_replace(parent, detached)
                parent.symlink_to(forged, target_is_directory=True)
                return result

            try:
                with mock.patch.object(
                        evidence_builder.os, "link",
                        side_effect=link_then_swap):
                    with self.assertRaisesRegex(
                            evidence_builder.EvidenceIndexError,
                            "path changed after publication"):
                        evidence_builder.write_index(
                            parent / "index.json", root,
                            evidence, b"{\"trusted\":true}\n")
                self.assertEqual(
                    (parent / "index.json").read_bytes(), b"FORGED\n")
                self.assertFalse((detached / "index.json").exists())
            finally:
                if parent.is_symlink():
                    parent.unlink()
                if detached.exists():
                    real_replace(detached, parent)

    def test_index_output_rejects_post_read_same_inode_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-evidence-") as temporary:
            root = Path(temporary)
            evidence, _ = self.fixture(root)
            output = root / "evidence-indexes" / "index.json"
            payload = b"{\"good\":true}\n"
            real_read = evidence_builder.os.read
            mutated = False

            def read_then_mutate(descriptor: int, size: int) -> bytes:
                nonlocal mutated
                chunk = real_read(descriptor, size)
                if not chunk and output.exists() and not mutated:
                    mutated = True
                    output.write_bytes(b"{\"evil\":true}\n")
                return chunk

            with mock.patch.object(
                    evidence_builder.os, "read",
                    side_effect=read_then_mutate):
                with self.assertRaisesRegex(
                        evidence_builder.EvidenceIndexError,
                        "content drift|identity drift"):
                    evidence_builder.write_index(
                        output, root, evidence, payload)
            self.assertTrue(mutated)
            self.assertFalse(output.exists())


class VendorBoundaryTests(unittest.TestCase):
    @staticmethod
    def canonical_payload_available(root: Path = REPOSITORY) -> bool:
        manifest = json.loads(VENDOR_MANIFEST.read_text(encoding="utf-8"))
        canonical = [
            root / record["path"]
            for record in manifest["canonical_headers"]
        ]
        present = [path for path in canonical if path.exists()]
        if len(present) not in {0, len(canonical)}:
            raise AssertionError(
                "nonredistributable CTP canonical payload is partial")
        return len(present) == len(canonical)

    def fixture(self, root: Path) -> Path:
        if not self.canonical_payload_available():
            self.skipTest(
                "nonredistributable CTP canonical payload is unavailable")
        manifest_payload = json.loads(
            VENDOR_MANIFEST.read_text(encoding="utf-8"))
        paths = [
            record["path"]
            for record in (
                manifest_payload["canonical_headers"] +
                manifest_payload["platform_assets"])
        ]
        for directory in vendor_verifier.convergence.PLATFORM_DIRECTORIES:
            paths.extend(
                f"{directory}/{name}"
                for name in vendor_verifier.convergence.HEADER_NAMES)
        for relative in paths:
            source = REPOSITORY / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        manifest = root / VENDOR_MANIFEST.relative_to(REPOSITORY)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(VENDOR_MANIFEST, manifest)
        return manifest

    def test_vendor_asset_modes_accept_restrictive_checkout_umasks(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-mode-") as temporary:
            root = Path(temporary)
            asset = root / "asset.bin"
            asset.write_bytes(b"reviewed\n")
            for mode in (0o400, 0o440, 0o444, 0o600, 0o640, 0o644):
                with self.subTest(mode=f"{mode:04o}"):
                    asset.chmod(mode)
                    self.assertEqual(
                        vendor_verifier.convergence.stable_relative_bytes(
                            root, "asset.bin"),
                        b"reviewed\n",
                    )

    def test_vendor_asset_modes_reject_write_execute_and_special_bits(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-mode-") as temporary:
            root = Path(temporary)
            asset = root / "asset.bin"
            asset.write_bytes(b"reviewed\n")
            for mode in (0o200, 0o620, 0o664, 0o700, 0o755, 0o4600):
                with self.subTest(mode=f"{mode:04o}"):
                    asset.chmod(mode)
                    with self.assertRaisesRegex(
                            vendor_verifier.convergence.ConvergenceError,
                            "asset is unsafe"):
                        vendor_verifier.convergence.stable_relative_bytes(
                            root, "asset.bin")

    def test_vendor_asset_hardlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-link-") as temporary:
            root = Path(temporary)
            asset = root / "asset.bin"
            asset.write_bytes(b"reviewed\n")
            asset.chmod(0o600)
            os.link(asset, root / "alias.bin")
            with self.assertRaisesRegex(
                    vendor_verifier.convergence.ConvergenceError,
                    "asset is unsafe"):
                vendor_verifier.convergence.stable_relative_bytes(
                    root, "asset.bin")

    def test_vendor_asset_hardlink_during_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-race-") as temporary:
            root = Path(temporary)
            asset = root / "asset.bin"
            asset.write_bytes(b"reviewed\n")
            asset.chmod(0o600)
            real_read = vendor_verifier.convergence.os.read
            linked = False

            def read_then_link(descriptor: int, size: int) -> bytes:
                nonlocal linked
                chunk = real_read(descriptor, size)
                if not chunk and not linked:
                    linked = True
                    os.link(asset, root / "alias.bin")
                return chunk

            with mock.patch.object(
                    vendor_verifier.convergence.os, "read",
                    side_effect=read_then_link):
                with self.assertRaisesRegex(
                        vendor_verifier.convergence.ConvergenceError,
                        "changed during read"):
                    vendor_verifier.convergence.stable_relative_bytes(
                        root, "asset.bin")
            self.assertTrue(linked)

    def test_vendor_manifest_and_forwarders_pass(self) -> None:
        bundled = (REPOSITORY / ".hepta/source-bundle-manifest.json").is_file()
        canonical_headers_present = self.canonical_payload_available()
        manifest = vendor_verifier.verify(
            REPOSITORY,
            VENDOR_MANIFEST,
            require_payload=canonical_headers_present)
        self.assertEqual(manifest["capability_status"], "disabled-experimental")
        self.assertFalse(manifest["distribution_authorized"])
        self.assertTrue(manifest["license_review_required"])
        self.assertEqual(manifest["version"], "6.7.7")
        self.assertEqual(len(manifest["platform_assets"]), 19)
        if bundled or not canonical_headers_present:
            self.assertFalse(
                (REPOSITORY / "third_party/ctp/6.7.7/include").exists())

    def test_duplicate_vendor_manifest_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-") as temporary:
            root = Path(temporary)
            manifest = root / VENDOR_MANIFEST.relative_to(REPOSITORY)
            manifest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(VENDOR_MANIFEST, manifest)
            manifest.write_bytes(
                manifest.read_bytes().replace(
                    b"{", b'{"schema":"forged",', 1))
            with self.assertRaisesRegex(
                    vendor_verifier.VendorVerificationError,
                    "strict UTF-8 JSON"):
                vendor_verifier.verify(root, manifest)

    def test_only_one_full_header_copy_remains(self) -> None:
        if (REPOSITORY / ".hepta/source-bundle-manifest.json").is_file():
            self.assertFalse(
                (REPOSITORY / "third_party/ctp/6.7.7/include").exists())
            for directory in vendor_verifier.convergence.PLATFORM_DIRECTORIES:
                self.assertFalse((REPOSITORY / directory).exists())
            return
        canonical_payload_available = self.canonical_payload_available()
        full_headers = list(
            (REPOSITORY / "third_party/ctp/6.7.7/include").glob("*.h"))
        if canonical_payload_available:
            self.assertEqual(len(full_headers), 4)
            self.assertTrue(
                all(path.stat().st_size > 5000 for path in full_headers))
        else:
            self.assertEqual(full_headers, [])
        for directory in (
                "Interface/CTPTradeApi32",
                "Interface/CTPTradeApi64",
                "Interface/CTPTradeApiLinux"):
            forwarders = list((REPOSITORY / directory).glob("*.h"))
            self.assertEqual(len(forwarders), 4)
            self.assertTrue(all(
                not path.is_symlink() and path.is_file() and
                path.stat().st_size < 128
                for path in forwarders))

    def test_forwarder_only_gate_does_not_require_vendor_overlay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-") as temporary:
            root = Path(temporary)
            for directory in vendor_verifier.convergence.PLATFORM_DIRECTORIES:
                destination = root / directory
                destination.mkdir(parents=True)
                for name in vendor_verifier.convergence.HEADER_NAMES:
                    path = destination / name
                    path.write_bytes(
                        vendor_verifier.convergence.forwarder(name))
                    path.chmod(0o644)
            self.assertEqual(
                vendor_verifier.convergence.verify_forwarders(root), 12)
            drift = (
                root /
                vendor_verifier.convergence.PLATFORM_DIRECTORIES[0] /
                vendor_verifier.convergence.HEADER_NAMES[0])
            drift.write_bytes(b"#pragma once\n")
            drift.chmod(0o644)
            with self.assertRaisesRegex(
                    vendor_verifier.convergence.ConvergenceError,
                    "forwarder drift"):
                vendor_verifier.convergence.verify_forwarders(root)

    def test_symlink_forwarder_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-") as temporary:
            root = Path(temporary)
            manifest = self.fixture(root)
            path = root / "Interface/CTPTradeApi32/ThostFtdcMdApi.h"
            path.unlink()
            path.symlink_to(
                "../CTPTradeApi64/ThostFtdcMdApi.h")
            with self.assertRaisesRegex(
                    vendor_verifier.convergence.ConvergenceError, "unsafe"):
                vendor_verifier.verify(root, manifest)

    def test_parent_component_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-") as temporary:
            temporary_root = Path(temporary)
            outside = temporary_root / "outside"
            self.fixture(outside)
            root = temporary_root / "root"
            root.mkdir()
            shutil.copytree(outside / "third_party", root / "third_party")
            (root / "Interface").symlink_to(
                outside / "Interface", target_is_directory=True)
            manifest = root / VENDOR_MANIFEST.relative_to(REPOSITORY)
            with self.assertRaisesRegex(
                    vendor_verifier.convergence.ConvergenceError,
                    "component is unsafe"):
                vendor_verifier.verify(root, manifest)

    def test_apply_preflights_every_header_before_writing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-") as temporary:
            root = Path(temporary)
            self.fixture(root)
            first = (
                root / "Interface/CTPTradeApi32" /
                vendor_verifier.convergence.HEADER_NAMES[0])
            for directory in vendor_verifier.convergence.PLATFORM_DIRECTORIES:
                for name in vendor_verifier.convergence.HEADER_NAMES:
                    canonical = (
                        root / vendor_verifier.convergence.CANONICAL_DIRECTORY /
                        name)
                    target = root / directory / name
                    target.write_bytes(canonical.read_bytes())
                    target.chmod(0o644)
            last = (
                root / vendor_verifier.convergence.PLATFORM_DIRECTORIES[-1] /
                vendor_verifier.convergence.HEADER_NAMES[-1])
            last.write_bytes(b"reviewed digest drift\n")
            last.chmod(0o644)
            before = first.read_bytes()
            with self.assertRaisesRegex(
                    vendor_verifier.convergence.ConvergenceError,
                    "differs from reviewed canonical"):
                vendor_verifier.convergence.converge(root, apply=True)
            self.assertEqual(first.read_bytes(), before)
            self.assertNotEqual(
                first.read_bytes(),
                vendor_verifier.convergence.forwarder(first.name))

    def test_incomplete_platform_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-") as temporary:
            root = Path(temporary)
            manifest = root / VENDOR_MANIFEST.relative_to(REPOSITORY)
            manifest.parent.mkdir(parents=True)
            shutil.copy2(VENDOR_MANIFEST, manifest)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["platform_assets"].pop()
            manifest.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            with self.assertRaisesRegex(
                    vendor_verifier.VendorVerificationError, "closure"):
                vendor_verifier.verify(
                    root, manifest, require_payload=False)

    def test_vendor_version_directory_is_an_exact_closure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-") as temporary:
            root = Path(temporary)
            manifest = self.fixture(root)
            unexpected = root / "third_party/ctp/6.7.7/lib"
            unexpected.mkdir()
            (unexpected / "future.so").write_bytes(b"unreviewed\n")
            with self.assertRaisesRegex(
                    vendor_verifier.VendorVerificationError,
                    "version directory closure"):
                vendor_verifier.verify(root, manifest)

    def test_vendor_canonical_include_is_an_exact_closure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-") as temporary:
            root = Path(temporary)
            manifest = self.fixture(root)
            unexpected = (
                root / vendor_verifier.convergence.CANONICAL_DIRECTORY /
                "future-vendor-payload.so")
            unexpected.write_bytes(b"unreviewed\n")
            unexpected.chmod(0o644)
            with self.assertRaisesRegex(
                    vendor_verifier.VendorVerificationError,
                    "canonical include directory closure"):
                vendor_verifier.verify(root, manifest)

    def test_check_mode_does_not_create_missing_canonical_tree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-vendor-") as temporary:
            root = Path(temporary)
            canonical = (
                root / vendor_verifier.convergence.CANONICAL_DIRECTORY)
            with self.assertRaises(
                    vendor_verifier.convergence.ConvergenceError):
                vendor_verifier.convergence.converge(root, apply=False)
            self.assertFalse(canonical.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
