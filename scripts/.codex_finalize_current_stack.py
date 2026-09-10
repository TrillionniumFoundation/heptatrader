#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
P1 = "plan/p1-release-package-preflight-20260910"
P2 = "plan/p2-bounded-paper-v5-source-20260910"
P0 = "plan/p0-owner-operated-truth-ci-20260910"
SELF_PATH = Path("scripts/.codex_finalize_current_stack.py")
WORKFLOW_PATH = Path(".github/workflows/codex-finalize-current-stack-v4.yml")


def run(*args: str, env: dict[str, str] | None = None) -> None:
    command = list(args)
    print("+", " ".join(command), flush=True)
    effective = os.environ.copy()
    if env:
        effective.update(env)
    subprocess.run(command, cwd=ROOT, env=effective, check=True)


def capture(*args: str) -> str:
    return subprocess.check_output(list(args), cwd=ROOT, text=True).strip()


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def write(relative: str, value: str) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def require_tokens(relative: str, tokens: Iterable[str]) -> None:
    text = read(relative)
    missing = [token for token in tokens if token not in text]
    if missing:
        raise RuntimeError(f"{relative}: missing required tokens: {missing}")


def remove_paths(patterns: Iterable[str]) -> None:
    for pattern in patterns:
        for path in ROOT.glob(pattern):
            if path.is_file() or path.is_symlink():
                print(f"remove carrier {path.relative_to(ROOT)}", flush=True)
                path.unlink()


def commit_if_needed(message: str) -> None:
    run("git", "add", "-A")
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=False
    )
    if result.returncode == 0:
        print("no staged delta", flush=True)
        return
    if result.returncode != 1:
        raise RuntimeError("git diff --cached failed")
    run("git", "commit", "-m", message)


def push(branch: str) -> None:
    run("git", "push", "origin", f"HEAD:{branch}")


def install_integration(label: str) -> None:
    build = ROOT / "build" / label
    shutil.rmtree(build, ignore_errors=True)
    run(
        "cmake", "-S", ".", "-B", str(build), "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_TESTING=ON",
        "-DBUILD_IB_PROBE=OFF",
        "-DHEPTA_ENABLE_IBAPI=OFF",
        "-DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF",
        "-DHEPTA_BUILD_LEGACY_MONOLITH=OFF",
        "-DHEPTA_BUILD_LEGACY_SIMULATOR=OFF",
        "-DHEPTA_RELEASE_LABEL=0.1.0-beta.1",
    )
    run("cmake", "--build", str(build), "--parallel", "2")
    run(
        sys.executable,
        "-m", "unittest", "tests.python.test_cmake_install_integration", "-v",
        env={"HEPTA_RELEASE_INTEGRATION_BUILD_DIR": str(build)},
    )


def full_core_python() -> None:
    run("./scripts/dev_core.sh", env={"HEPTA_JOBS": "2"})
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests/python", "-p", "test_*.py")


def maybe_apply_historical_p1_fix() -> None:
    source = read("scripts/build_release_package.py")
    preflight = read("scripts/hepta_preflight_core.py")
    if (
        "O_TMPFILE" in source
        and "HARD_ALLOWED_BROKER_HOSTS" in preflight
        and (ROOT / "tests/python/test_publication_identity.py").is_file()
    ):
        return
    print("materializing previously reviewed publication/endpoint repair", flush=True)
    original = capture(
        "git", "show",
        "eb635a4cbf85d846857cbcfd5839b88ec47f8a0e:.github/workflows/codex-fix-pr57-publication-endpoints.yml",
    )
    write(".github/workflows/codex-fix-pr57-publication-endpoints.yml", original + "\n")
    retry = capture(
        "git", "show",
        "fff00a4679143194c415075206477ec8b773e1ca:.github/workflows/codex-fix-pr57-publication-endpoints-v2.yml",
    )
    marker = "          python3 - <<'PY'\n"
    start = retry.index(marker) + len(marker)
    end = retry.index("\n          PY", start)
    import textwrap
    code = textwrap.dedent(retry[start:end])
    code = code.replace("textwrap.dedent(endpoint_old)", "endpoint_old")
    code = code.replace("textwrap.dedent(endpoint_new)", "endpoint_new")
    code = code.replace("textwrap.dedent(endpoint_test)", "endpoint_test")
    exec(compile(code, "historical_pr57_repair.py", "exec"), {"__name__": "__main__"})
    test_path = ROOT / "tests/python/test_hepta_preflight.py"
    test = test_path.read_text(encoding="utf-8")
    test = test.replace(
        'self.assertIn("outside the PAPER policy", check(receipt, "broker.reachability")["detail"])',
        'self.assertIn("compiled PAPER endpoint boundary", check(receipt, "broker.reachability")["detail"])',
    )
    test_path.write_text(test, encoding="utf-8")


def validate_p1() -> None:
    require_tokens(
        "scripts/build_release_package.py",
        (
            "O_TMPFILE",
            'f"/proc/self/fd/{descriptor}"',
            "operator-custodied",
            "published output identity, bytes or mode",
        ),
    )
    require_tokens(
        "scripts/hepta_preflight_core.py",
        (
            "O_TMPFILE",
            "HARD_ALLOWED_BROKER_HOSTS",
            "HARD_ALLOWED_BROKER_PORTS",
            "broker policy hosts must be literal IP addresses",
            "compiled PAPER endpoint boundary",
            "published receipt identity, bytes or mode",
        ),
    )
    require_tokens(
        "tests/python/test_publication_identity.py",
        (
            "test_package_and_checksum_ignore_regular_and_symlink_source_decoys",
            "test_private_receipt_ignores_regular_and_symlink_source_decoys",
            "test_destination_collisions_preserve_competitor_bytes",
            "test_shared_output_directories_are_rejected",
        ),
    )
    require_tokens(
        "tests/python/test_hepta_preflight.py",
        ("test_packaged_policy_cannot_widen_compiled_broker_endpoint_ceiling",),
    )
    run(sys.executable, "-m", "unittest", "tests.python.test_publication_identity", "-v")
    run(sys.executable, "-m", "unittest", "tests.python.test_release_package", "-v")
    run(sys.executable, "-m", "unittest", "tests.python.test_hepta_preflight", "-v")
    run(sys.executable, "-m", "unittest", "tests.python.test_preflight_special_files", "-v")
    full_core_python()
    install_integration("final-p1-install")


def review_verifier_source() -> str:
    return '''#!/usr/bin/env python3
"""Behavior-bound closure checks for independently reported review blockers."""
from __future__ import annotations

import json
from pathlib import Path
import stat
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TOKENS: dict[str, tuple[str, ...]] = {
    "scripts/build_release_package.py": (
        "O_TMPFILE",
        'f"/proc/self/fd/{descriptor}"',
        "operator-custodied",
        "published output identity, bytes or mode",
    ),
    "scripts/hepta_preflight_core.py": (
        "O_TMPFILE",
        "HARD_ALLOWED_BROKER_HOSTS",
        "HARD_ALLOWED_BROKER_PORTS",
        "broker policy hosts must be literal IP addresses",
        "compiled PAPER endpoint boundary",
        "published receipt identity, bytes or mode",
    ),
    "tests/python/test_publication_identity.py": (
        "test_package_and_checksum_ignore_regular_and_symlink_source_decoys",
        "test_private_receipt_ignores_regular_and_symlink_source_decoys",
        "test_destination_collisions_preserve_competitor_bytes",
        "test_shared_output_directories_are_rejected",
    ),
    "tests/python/test_hepta_preflight.py": (
        "test_packaged_policy_cannot_widen_compiled_broker_endpoint_ceiling",
        "connect.assert_not_called()",
    ),
    "tests/python/test_preflight_special_files.py": (
        "test_fifo_policy_is_rejected_without_blocking_or_receipt",
        "test_fifo_artifact_is_rejected_without_blocking_or_pass_receipt",
        "test_regular_to_fifo_builder_swap_is_rejected_without_blocking",
    ),
    "HeptaTrade/execution/ib_paper_execution_profile.cpp": (
        "maxOrderQuantity != maxGrossPosition",
        "IB_PAPER_QUALIFICATION_ORDER_MODE_LIMITS_INVALID",
    ),
    "tests/ib_paper_execution_profile_tests.cpp": (
        "TestQualificationEnvelopeAlwaysHasAnAtomicFlattenPath",
        "IB_PAPER_MAX_GROSS_POSITION_EXCEEDED",
        "IB_PAPER_EXTERNAL_FLATTEN_POSITION_LIMIT_EXCEEDED",
        "IbPaperKillSwitchState::Engaged",
    ),
    "tests/execution_coordinator_tests.cpp": (
        "TestQualificationExternalFlattenIsExactAndAbsolutelyBounded",
    ),
}

REQUIRED_EVIDENCE: dict[str, set[str]] = {
    "TEST-001": {
        "tests/python/test_publication_identity.py",
        "tests/python/test_review_blocker_closures.py",
        "tests/ib_paper_execution_profile_tests.cpp",
    },
    "PENDING-EXPOSURE-001": {
        "HeptaTrade/execution/ib_paper_execution_profile.cpp",
        "tests/ib_paper_execution_profile_tests.cpp",
        "tests/execution_coordinator_tests.cpp",
        "scripts/verify_review_blocker_closures.py",
    },
    "RELEASE-001": {
        "scripts/build_release_package.py",
        "scripts/hepta_preflight_core.py",
        "tests/python/test_preflight_special_files.py",
        "tests/python/test_publication_identity.py",
        "tests/python/test_hepta_preflight.py",
        "scripts/verify_review_blocker_closures.py",
        "tests/python/test_review_blocker_closures.py",
    },
}

FORBIDDEN_CARRIERS = (
    ".github/workflows/codex-fix-pr57-publication-endpoints.yml",
    ".github/workflows/codex-fix-pr57-publication-endpoints-v2.yml",
    ".github/workflows/codex-fix-pr57-publication-endpoints-v3.yml",
    ".github/workflows/codex-finalize-current-stack-v4.yml",
    "scripts/.codex_finalize_current_stack.py",
)


def _read(root: Path, relative: str) -> str:
    path = root / relative
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("expected regular non-symlink file")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"{relative}: unreadable: {error}") from error


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    for relative, tokens in REQUIRED_TOKENS.items():
        try:
            text = _read(root, relative)
        except ValueError as error:
            errors.append(str(error))
            continue
        for token in tokens:
            if token not in text:
                errors.append(f"{relative}: missing review-closure token {token!r}")
    try:
        register = json.loads(_read(root, "docs/gap-register.json"))
        authorization = register.get("authorization", {})
        if authorization != {
            "source_state": "READY",
            "paper_authorized": False,
            "live_authorized": False,
        }:
            errors.append("gap authorization projection is not READY/false/false")
        by_id = {
            item.get("id"): item
            for item in register.get("gaps", [])
            if isinstance(item, dict)
        }
        for gap_id, required in REQUIRED_EVIDENCE.items():
            item = by_id.get(gap_id, {})
            evidence = set(item.get("evidence", []))
            missing = sorted(required - evidence)
            if missing:
                errors.append(
                    f"{gap_id}: missing behavior evidence: " + ", ".join(missing)
                )
            if item.get("state") != "CLOSED_SOURCE":
                errors.append(f"{gap_id}: source state is not CLOSED_SOURCE")
            if item.get("blocking_authorization") is not False:
                errors.append(f"{gap_id}: source closure still blocks authorization")
    except (ValueError, json.JSONDecodeError) as error:
        errors.append(str(error))
    for relative in FORBIDDEN_CARRIERS:
        if (root / relative).exists():
            errors.append(f"temporary remediation carrier remains: {relative}")
    return errors


def main() -> int:
    errors = validate(ROOT)
    for error in errors:
        print(f"[REVIEW-BLOCKERS] {error}")
    if errors:
        return 1
    print("[REVIEW-BLOCKERS] PASS behavior-bound closure; no PAPER/LIVE authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def review_verifier_test_source() -> str:
    return '''from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_review_blocker_closures as review  # noqa: E402


class ReviewBlockerClosureTests(unittest.TestCase):
    def fixture(self, destination: Path) -> Path:
        relative_paths = set(review.REQUIRED_TOKENS)
        relative_paths.add("docs/gap-register.json")
        for relative in relative_paths:
            source = ROOT / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return destination

    def test_repository_review_blockers_are_closed(self) -> None:
        self.assertEqual(review.validate(ROOT), [])

    def assert_mutation_rejected(
        self, relative: str, old: str, new: str = "REMOVED_REVIEW_GUARD"
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(Path(directory))
            path = root / relative
            text = path.read_text(encoding="utf-8")
            self.assertIn(old, text)
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            self.assertTrue(review.validate(root))

    def test_publication_inode_guard_cannot_be_removed(self) -> None:
        self.assert_mutation_rejected(
            "scripts/build_release_package.py", "O_TMPFILE"
        )

    def test_compiled_endpoint_ceiling_cannot_be_removed(self) -> None:
        self.assert_mutation_rejected(
            "scripts/hepta_preflight_core.py", "HARD_ALLOWED_BROKER_HOSTS"
        )

    def test_v5_atomic_envelope_cannot_be_weakened(self) -> None:
        self.assert_mutation_rejected(
            "HeptaTrade/execution/ib_paper_execution_profile.cpp",
            "maxOrderQuantity != maxGrossPosition",
            "maxOrderQuantity > maxGrossPosition",
        )

    def test_publication_hostile_regression_cannot_disappear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(Path(directory))
            (root / "tests/python/test_publication_identity.py").unlink()
            self.assertTrue(review.validate(root))

    def test_gap_evidence_cannot_drop_publication_regression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(Path(directory))
            path = root / "docs/gap-register.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            release = next(item for item in value["gaps"] if item["id"] == "RELEASE-001")
            release["evidence"].remove("tests/python/test_publication_identity.py")
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            self.assertTrue(review.validate(root))


if __name__ == "__main__":
    unittest.main()
'''


def inject_review_validator(relative: str, prefix: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if "verify_review_blocker_closures.validate(root)" in text:
        return
    signature = "def validate(root: Path | str = ROOT) -> list[str]:"
    start = text.index(signature)
    marker = "    errors: list[str] = []\n"
    position = text.index(marker, start) + len(marker)
    addition = (
        "    import verify_review_blocker_closures\n"
        f"    errors.extend({prefix!r} + item for item in "
        "verify_review_blocker_closures.validate(root))\n"
    )
    path.write_text(text[:position] + addition + text[position:], encoding="utf-8")


def update_truth_sources() -> None:
    write("scripts/verify_review_blocker_closures.py", review_verifier_source())
    write("tests/python/test_review_blocker_closures.py", review_verifier_test_source())

    register_path = ROOT / "docs/gap-register.json"
    register = json.loads(register_path.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in register["gaps"]}
    additions = {
        "TEST-001": (
            "tests/python/test_publication_identity.py",
            "tests/python/test_review_blocker_closures.py",
            "tests/ib_paper_execution_profile_tests.cpp",
        ),
        "PENDING-EXPOSURE-001": (
            "HeptaTrade/execution/ib_paper_execution_profile.cpp",
            "tests/ib_paper_execution_profile_tests.cpp",
            "tests/execution_coordinator_tests.cpp",
            "scripts/verify_review_blocker_closures.py",
        ),
        "RELEASE-001": (
            "scripts/build_release_package.py",
            "scripts/hepta_preflight_core.py",
            "tests/python/test_preflight_special_files.py",
            "tests/python/test_publication_identity.py",
            "tests/python/test_hepta_preflight.py",
            "scripts/verify_review_blocker_closures.py",
            "tests/python/test_review_blocker_closures.py",
        ),
    }
    for gap_id, paths in additions.items():
        evidence = by_id[gap_id]["evidence"]
        for relative in paths:
            if relative not in evidence:
                evidence.append(relative)
    release_note = (
        " Anonymous inode-bound no-replace publication preserves the fsynced "
        "package/checksum/receipt inode under an operator-custodied directory, "
        "and Broker probing is capped by compiled literal loopback PAPER endpoints."
    )
    if release_note.strip() not in by_id["RELEASE-001"]["summary"]:
        by_id["RELEASE-001"]["summary"] += release_note
    register_path.write_text(
        json.dumps(register, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    catalog_path = ROOT / "docs/module-catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    release = next(item for item in catalog["modules"] if item["id"] == "release-engineering")
    for relative in ("scripts/verify_review_blocker_closures.py",):
        if relative not in release["implementation"]:
            release["implementation"].append(relative)
    for relative in (
        "tests/python/test_publication_identity.py",
        "tests/python/test_review_blocker_closures.py",
    ):
        if relative not in release["tests"]:
            release["tests"].append(relative)
    catalog_path.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    inject_review_validator("scripts/check_gap_register.py", "behavior closure: ")
    inject_review_validator("scripts/verify_source_gap_closures.py", "review blocker: ")

    doc_path = ROOT / "docs/GAP-REGISTER.md"
    doc = doc_path.read_text(encoding="utf-8")
    heading = "## Independent review blocker binding"
    if heading not in doc:
        doc_path.write_text(
            doc.rstrip()
            + "\n\n"
            + heading
            + "\n\n"
            + "`RELEASE-001` is bound to anonymous descriptor publication, "
              "operator custody, destination inode/byte/mode validation, a compiled "
              "literal loopback PAPER endpoint ceiling, and hostile publication/policy "
              "regressions. `PENDING-EXPOSURE-001` remains bound to the V5 equality "
              "envelope and exact signed flatten tests. Removing an implementation "
              "guard, regression, or named evidence path makes canonical validation fail.\n",
            encoding="utf-8",
        )


def validate_truth_stack() -> None:
    run(sys.executable, "scripts/check_documentation.py")
    run(sys.executable, "scripts/verify_build_ownership.py")
    run(sys.executable, "scripts/verify_review_blocker_closures.py")
    run(sys.executable, "scripts/check_gap_register.py")
    run(sys.executable, "scripts/verify_canonical_ib_paper_profile.py")
    run(sys.executable, "scripts/check_qualification_trust_boundary.py", "--self-test")
    run(sys.executable, "scripts/verify_source_gap_closures.py")
    run(sys.executable, "-m", "unittest", "tests.python.test_review_blocker_closures", "-v")
    full_core_python()
    install_integration("final-p0-install")


def main() -> int:
    os.chdir(ROOT)
    run("git", "config", "user.name", "ProfHepta")
    run("git", "config", "user.email", "102159240+ProfHepta@users.noreply.github.com")
    run("git", "fetch", "origin", P1, P2, P0)

    current = capture("git", "rev-parse", "HEAD")
    remote = capture("git", "rev-parse", f"origin/{P1}")
    if current != remote:
        raise RuntimeError(f"P1 moved before finalization: current={current} remote={remote}")
    maybe_apply_historical_p1_fix()
    remove_paths(
        (
            ".github/workflows/codex-fix-pr57-publication-endpoints*.yml",
            ".github/workflows/codex-finalize-current-stack-v4.yml",
            "scripts/.codex_finalize_current_stack.py",
        )
    )
    validate_p1()
    run("git", "diff", "--check")
    commit_if_needed("fix(release): close publication identity and endpoint blockers")
    push(P1)
    p1_final = capture("git", "rev-parse", "HEAD")

    run("git", "fetch", "origin", P2)
    run("git", "checkout", "-B", P2, f"origin/{P2}")
    run("git", "merge", "--no-edit", p1_final)
    remove_paths(
        (
            ".github/workflows/codex-restack-pr58*.yml",
            ".github/workflows/codex-fix-pr58*.yml",
        )
    )
    require_tokens(
        "HeptaTrade/execution/ib_paper_execution_profile.cpp",
        ("maxOrderQuantity != maxGrossPosition",),
    )
    require_tokens(
        "tests/ib_paper_execution_profile_tests.cpp",
        ("TestQualificationEnvelopeAlwaysHasAnAtomicFlattenPath",),
    )
    full_core_python()
    run("git", "diff", "--check")
    commit_if_needed("merge: restack bounded PAPER-V5 on final release slice")
    push(P2)
    p2_final = capture("git", "rev-parse", "HEAD")

    run("git", "fetch", "origin", P0)
    run("git", "checkout", "-B", P0, f"origin/{P0}")
    run("git", "merge", "--no-edit", p2_final)
    remove_paths(
        (
            ".github/workflows/codex-restack-pr59*.yml",
            ".github/workflows/codex-bind-gap*.yml",
            ".github/workflows/codex-finalize-pr59*.yml",
            ".github/workflows/codex-materialize-pr59*.yml",
            ".github/workflows/codex-verify-pr59*.yml",
        )
    )
    update_truth_sources()
    validate_truth_stack()
    run("git", "diff", "--check")
    commit_if_needed("fix(control-plane): bind final review blockers to behavior evidence")
    push(P0)
    p0_final = capture("git", "rev-parse", "HEAD")

    result = {
        "p1": p1_final,
        "p2": p2_final,
        "p0": p0_final,
        "paper_authorized": False,
        "live_authorized": False,
    }
    Path("/tmp/heptatrader-final-stack-shas.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
