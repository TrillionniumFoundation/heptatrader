#!/usr/bin/env python3

"""Prove the fixed release verifier is isolated from repository imports."""

from __future__ import annotations

import ast
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify_heptatrader_runtime_package as runtime  # noqa: E402
import hepta_p1_paper_admission_verifier as admission  # noqa: E402


PYTHON = Path("/usr/bin/python3")
FIXED_NAME = "hepta-release-validation-closure-verifier"
SUCCESS = "installed-release-validation-causal-verifier: PASS"

DRIVER = r'''
from datetime import datetime, timedelta, timezone
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

fixed = Path(sys.argv[1]).resolve(strict=True)
fixture = Path(sys.argv[2]).resolve(strict=True)
loader = importlib.machinery.SourceFileLoader(
    "_hepta_installed_release_verifier", str(fixed))
specification = importlib.util.spec_from_loader(loader.name, loader)
if specification is None:
    raise RuntimeError("fixed verifier specification is unavailable")
verifier = importlib.util.module_from_spec(specification)
sys.modules[loader.name] = verifier
loader.exec_module(verifier)
builder = verifier.builder
import json

now = datetime.now(timezone.utc).replace(microsecond=0)
evidence = fixture / "evidence"
artifact = evidence / "heptatrader-round95-engineering-artifacts-v1"
artifact.mkdir(parents=True, mode=0o700)
evidence.chmod(0o700)
artifact.chmod(0o700)
input_path = artifact / builder.INPUT_MANIFEST_NAME
input_path.write_bytes(b"{}\n")
input_path.chmod(0o600)
input_capture = builder._capture_file(input_path, "input")

paths = {}
captures = {}
for role in (
        "receipt", "request", "trust_policy", "index",
        "evidence_set_manifest", "retention_policy"):
    path = fixture / (role + ".json")
    path.write_bytes((json.dumps({"role": role}) + "\n").encode("ascii"))
    path.chmod(0o600)
    paths[role] = path
    captures[role] = builder._capture_file(
        path, role, require_trusted_parent=False)

fresh_until = (
    now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
local = {
    "profile": builder.PROFILE,
    "round": 95,
    "release_version": "0.1.0-beta.1-round95",
    "artifact_directory": artifact.name,
    "input_manifest_sha256": input_capture.snapshot.sha256,
    "source_baseline": {
        "path": "source-baseline-manifest.json",
        "sha256": "1" * 64,
        "size": 1,
        "mode": "0600",
    },
    "source_lineage": {"git_head": "2" * 40},
    "verification": {
        "matrix_generated_at": now.isoformat().replace("+00:00", "Z"),
        "runner_generated_at": now.isoformat().replace("+00:00", "Z"),
        "fresh_until": fresh_until,
        "maximum_age_seconds": builder.MAX_VERIFICATION_AGE_SECONDS,
        "lanes": [],
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
    "critical_files": [{
        "role": "release-input-manifest",
        "path": input_path.relative_to(evidence).as_posix(),
        "sha256": input_capture.snapshot.sha256,
        "size": input_capture.snapshot.size,
        "mode": input_capture.snapshot.mode,
    }],
    "safety_boundaries": dict(builder.SAFETY_BOUNDARIES),
}
retention = {
    "schema": "heptatrader.evidence-ingestion-receipt-verification.v2",
    "trust_scope": "system-production",
    "signature_status": "verified",
    "retention_status": "current-policy-satisfied",
    "current_policy_satisfied_object_count": 1,
    "statement_sha256": "3" * 64,
    "request_sha256": "4" * 64,
    "index_sha256": "5" * 64,
    "evidence_set_manifest_sha256": "6" * 64,
    "trust_policy_sha256": "7" * 64,
    "evidence_set_id": "round95-installed-layout",
    "profile": builder.PROFILE,
    "role_count": 1,
    "production_contract_verified": True,
}
builder.verify_local_input_manifest = lambda *args, **kwargs: dict(local)
builder._receipt_summary = lambda *args, **kwargs: (
    dict(retention), dict(captures))
receipt_inputs = builder.ReceiptInputs(
    receipt=paths["receipt"], request=paths["request"],
    trust_policy=paths["trust_policy"], index=paths["index"],
    evidence_set_manifest=paths["evidence_set_manifest"],
    retention_policy=paths["retention_policy"])
closure = builder.build_closure(
    input_path, evidence, receipt_inputs, evaluated_at=now)
closure_path = fixture / "closure.json"
closure_path.write_bytes(builder.canonical_json(closure) + b"\n")
closure_path.chmod(0o600)
report = verifier.verify(
    closure_path, verification_time=now, _allow_test_time=True)
if report["decision"] != "GO" or report["paper_authorized"] is not False:
    raise RuntimeError("installed verifier weakened the candidate boundary")
print("installed-release-validation-causal-verifier: PASS")
'''


class InstalledReleaseValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        if not PYTHON.is_file():
            self.skipTest("/usr/bin/python3 is unavailable")
        self.temporary = tempfile.TemporaryDirectory(
            prefix="hepta-release-installed-")
        self.root = Path(self.temporary.name)
        self.libexec = self.root / "usr/libexec"
        self.libexec.mkdir(parents=True, mode=0o755)
        self._install()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _install(self) -> None:
        verifier = SCRIPTS / "verify_heptatrader_release_validation_closure.py"
        fixed = self.libexec / FIXED_NAME
        shutil.copyfile(verifier, fixed)
        fixed.chmod(0o755)
        for name in runtime.RELEASE_VALIDATION_COMPANION_NAMES:
            target = self.libexec / f"{name}.py"
            shutil.copyfile(SCRIPTS / f"{name}.py", target)
            target.chmod(0o644)
        package = self.libexec / "hepta_ops"
        package.mkdir(mode=0o755)
        for name in ("__init__.py", "agent_os_source.py", "registry.py"):
            target = package / name
            shutil.copyfile(REPOSITORY / "hepta_ops" / name, target)
            target.chmod(0o644)

    def _run(self, fixed: Path | None = None) -> subprocess.CompletedProcess[str]:
        fixture = Path(tempfile.mkdtemp(prefix="fixture-", dir=self.root))
        fixture.chmod(0o700)
        executable = fixed or (self.libexec / FIXED_NAME)
        return subprocess.run(
            [str(PYTHON), "-I", "-S", "-B", "-X",
             "pycache_prefix=/dev/null/hepta-release-causal-pycache",
             "-c", DRIVER,
             str(executable), str(fixture)],
            cwd="/", stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="strict",
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                 "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"},
            timeout=30, check=False)

    @contextmanager
    def _private_exact_stage(self):
        mapping = {}
        for source, (
                installed, source_mode, installed_mode,
        ) in admission.RELEASE_CAUSAL_SOURCE_INSTALL_PATHS.items():
            target = self.root / installed
            mapping[source] = (str(target), source_mode, installed_mode)
        tools = self.root / "usr/bin"
        tools.mkdir(mode=0o755)
        interpreter = tools / "python3.12"
        openssl = tools / "openssl"
        sbin = self.root / "usr/sbin"
        sbin.mkdir(mode=0o755)
        chroot = sbin / "chroot"
        interpreter.write_bytes(b"bound-python-interpreter\n")
        interpreter.chmod(0o755)
        openssl.write_bytes(b"bound-openssl\n")
        openssl.chmod(0o755)
        chroot.write_bytes(b"bound-chroot\n")
        chroot.chmod(0o755)
        stage_parent = self.root / "run/hepta"
        stage_parent.mkdir(parents=True, mode=0o700)
        stage_parent.chmod(0o700)
        stage_path = stage_parent / ".hepta-release-causal-stage"
        with (
            mock.patch.object(
                admission, "RELEASE_CAUSAL_SOURCE_INSTALL_PATHS", mapping),
            mock.patch.object(
                admission, "RELEASE_CAUSAL_PYTHON", interpreter),
            mock.patch.object(admission, "RELEASE_CAUSAL_OPENSSL", openssl),
            mock.patch.object(admission, "RELEASE_CAUSAL_CHROOT", chroot),
            mock.patch.object(
                admission, "RELEASE_CAUSAL_VERIFIER",
                self.libexec / FIXED_NAME),
            mock.patch.object(admission, "RELEASE_CAUSAL_STAGE", stage_path),
            mock.patch.object(
                admission, "RELEASE_CAUSAL_ABI_LOGICAL_PATHS", ()),
            mock.patch.object(
                admission, "_bind_release_causal_python_tree",
                return_value=((), ())),
            mock.patch.object(admission, "ROOT_UID", os.geteuid()),
        ):
            runtime_binding = admission._bind_release_causal_runtime()
            stage = admission._create_release_causal_stage(
                rootfs_files=runtime_binding.rootfs_files,
                verifier_path=runtime_binding.verifier.path,
                owner_uid=os.geteuid())
            try:
                yield stage
            finally:
                stage.cleanup()

    def test_recursive_dependency_list_is_exact_and_minimal(self) -> None:
        available = set(runtime.RELEASE_VALIDATION_COMPANION_NAMES)
        reached: set[str] = set()
        pending = ["verify_heptatrader_release_validation_closure"]
        package_seen = False
        while pending:
            name = pending.pop()
            if name in reached:
                continue
            reached.add(name)
            tree = ast.parse((SCRIPTS / f"{name}.py").read_bytes())
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(
                        alias.name.split(".", 1)[0]
                        for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".", 1)[0])
            package_seen = package_seen or "hepta_ops" in imported
            pending.extend(sorted(imported & available - reached))
        self.assertEqual(reached, available)
        self.assertTrue(package_seen)
        self.assertEqual(len(available), 27)
        self.assertEqual(len(runtime.RELEASE_VALIDATION_PACKAGE_FILES), 3)

    def test_fixed_isolated_causal_verifier_succeeds_without_repo_path(
            self) -> None:
        completed = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(completed.stdout.strip(), SUCCESS)

    def test_missing_recursive_module_fails_closed(self) -> None:
        (self.libexec / "build_heptatrader_delivery_closure.py").unlink()
        completed = self._run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn(SUCCESS, completed.stdout)

    def test_tampered_recursive_module_fails_closed(self) -> None:
        target = self.libexec / "run_execution_gateway_soak.py"
        target.chmod(0o600)
        target.write_bytes(b"this is not valid Python source\n")
        target.chmod(0o644)
        completed = self._run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn(SUCCESS, completed.stdout)

    def test_extra_stdlib_shadow_module_fails_closed(self) -> None:
        shadow = self.libexec / "json.py"
        shadow.write_text(
            'raise RuntimeError("unexpected installed shadow module")\n',
            encoding="utf-8")
        shadow.chmod(0o644)
        completed = self._run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unexpected installed shadow module", completed.stderr)
        self.assertNotIn(SUCCESS, completed.stdout)

    def test_benign_installed_shadow_cannot_execute_from_private_stage(
            self) -> None:
        marker = self.root / "unbound-shadow-executed"
        shadow = self.libexec / "secrets.py"
        shadow.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed', "
            "encoding='ascii')\n"
            "def token_hex(nbytes=32):\n"
            "    return 'a' * (2 * nbytes)\n",
            encoding="utf-8")
        shadow.chmod(0o644)

        # Prove this is a viable, non-crashing shadow: the unisolated helper
        # reaches GO/PASS and executes its marker.
        direct = self._run()
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertEqual(direct.stdout.strip(), SUCCESS)
        self.assertTrue(marker.is_file())
        marker.unlink()

        # The production admission path executes the same bound verifier and
        # companion bytes from its private exact stage, so the installed
        # same-directory shadow never participates.
        with self._private_exact_stage() as stage:
            staged = self._run(stage.verifier_path)
            stage.reopen()
            self.assertEqual(staged.returncode, 0, staged.stderr)
            self.assertEqual(staged.stderr, "")
            self.assertEqual(staged.stdout.strip(), SUCCESS)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
