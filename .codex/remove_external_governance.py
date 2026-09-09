#!/usr/bin/env python3
"""Apply the owner-operated repository overlay and retire external governance."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / ".codex/owner-operated-overlay"


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{relative}: expected one occurrence of {old!r}, found {count}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def remove(relative: str) -> None:
    path = ROOT / relative
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def apply_overlay() -> None:
    if not OVERLAY.is_dir():
        raise SystemExit("owner-operated overlay is missing")
    for source in sorted(OVERLAY.rglob("*")):
        if not source.is_file() or source.is_symlink():
            continue
        relative = source.relative_to(OVERLAY)
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def update_gap_register() -> None:
    path = ROOT / "docs/gap-register.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["gaps"] = [
        item for item in value["gaps"] if item.get("id") != "G-TEAM-001"
    ]
    broker = next(item for item in value["gaps"] if item.get("id") == "G-IB-001")
    broker["summary"] = (
        "Exact current-main source, immutable IB SDK/BID and builder inputs, "
        "distinct no-secret builder and PAPER execution identities, PAPER-only "
        "TWS/IB Gateway/account, pinned external harness, bounded Broker campaign, "
        "kill-switch/recovery checks, authoritative terminal reconciliation and "
        "a verifier receipt must be materialized and verified."
    )
    broker["evidence"] = [
        ".github/workflows/ib-paper-qualification.yml",
        "scripts/build_ib_candidate_artifact.sh",
        "scripts/verify_ib_candidate_artifact.py",
        "scripts/run_ib_paper_artifact_qualification.sh",
        "scripts/verify_ib_paper_qualification.py",
        "docs/ib-paper-profile-policy-v1.json",
    ]
    write_json(path, value)


def update_catalog() -> None:
    path = ROOT / "docs/module-catalog.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["modules"] = [
        item
        for item in value["modules"]
        if item.get("id") != "governance-qualification"
    ]
    ib = next(item for item in value["modules"] if item.get("id") == "ib-paper")
    ib["implementation"] = [
        "HeptaTrade/adapter_ib",
        "HeptaTrade/execution/hepta_ib_executiond.cpp",
        ".github/workflows/ib-paper-qualification.yml",
        "scripts/build_ib_candidate_artifact.sh",
        "scripts/verify_ib_candidate_artifact.py",
        "scripts/run_ib_paper_artifact_qualification.sh",
        "scripts/verify_ib_paper_qualification.py",
        "scripts/check_qualification_trust_boundary.py",
        "systemd/hepta-execution-ib-paper.service",
        "docs/ib-paper-profile-policy-v1.json",
        "scripts/verify_canonical_ib_paper_profile.py",
    ]
    ib["tests"] = [
        "tests/ib_order_lifecycle_tests.cpp",
        "tests/ib_paper_kill_switch_tests.cpp",
        "tests/ib_live_terminal_reconciliation_tests.cpp",
        "tests/execution_coordinator_tests.cpp",
        "tests/python/test_canonical_ib_paper_profile.py",
        "tests/python/test_ib_paper_qualification.py",
        "tests/python/test_qualification_trust_boundary.py",
        "tests/python/test_ib_workflow_interfaces.py",
    ]
    ib["broker_mutation"] = "PAPER_GATED"
    ib["production_authorized"] = False
    write_json(path, value)


def update_context_registry() -> None:
    path = ROOT / ".github/required-check-contexts-v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["policy"]["merge_group_cancel_in_progress"] = "not-applicable"
    value["required_merge_group_contexts"] = []
    value["external_qualification_contexts"] = [
        "ib-paper-exact-artifact-qualification"
    ]
    value["non_required_observation_contexts"] = [
        item
        for item in value["non_required_observation_contexts"]
        if item != "governance-bootstrap-admission"
    ]
    write_json(path, value)


def update_validators() -> None:
    replace_once(
        "scripts/check_gap_register.py",
        'REQUIRED_EXTERNAL_GAPS = {\n'
        '    "G-TEAM-001": '
        '"https://github.com/TrillionniumFoundation/heptatrader/issues/8",\n'
        '    "G-IB-001": '
        '"https://github.com/TrillionniumFoundation/heptatrader/issues/9",\n'
        '}',
        'REQUIRED_EXTERNAL_GAPS = {\n'
        '    "G-IB-001": '
        '"https://github.com/TrillionniumFoundation/heptatrader/issues/9",\n'
        '}',
    )
    replace_once(
        "scripts/check_gap_register.py",
        "source_state must remain CANDIDATE before protected admission",
        "source_state must remain CANDIDATE before Broker qualification",
    )
    replace_once(
        "scripts/check_gap_register.py",
        'for module_id in ("ib-paper", "governance-qualification"):',
        'for module_id in ("ib-paper",):',
    )
    replace_once(
        "scripts/check_gap_register.py",
        "external blockers require production_authorized=false",
        "Broker qualification requires production_authorized=false",
    )
    replace_once(
        "scripts/verify_source_gap_closures.py",
        'EXPECTED_EXTERNAL_GAPS = {"G-TEAM-001", "G-IB-001"}',
        'EXPECTED_EXTERNAL_GAPS = {"G-IB-001"}',
    )
    replace_once(
        "scripts/verify_source_gap_closures.py",
        "PAPER/LIVE authorization or closes external qualification gaps.",
        "PAPER/LIVE authorization or closes the external Broker qualification gap.",
    )
    replace_once(
        "scripts/verify_source_gap_closures.py",
        "source_state must remain CANDIDATE before merge",
        "source_state must remain CANDIDATE before Broker qualification",
    )
    replace_once(
        "scripts/verify_source_gap_closures.py",
        "PAPER must remain unauthorized without external receipt",
        "PAPER must remain unauthorized without a Broker receipt",
    )
    replace_once(
        "scripts/verify_ib_candidate_artifact.py",
        'TRUSTED_BUILDER_FILES = (\n'
        '    "scripts/build_ib_candidate_artifact.sh",\n'
        '    "scripts/verify_ib_candidate_artifact.py",\n'
        '    "scripts/verify_qualification_candidate.py",\n'
        '    "scripts/github_qualification_evidence.py",\n'
        '    ".github/workflows/ib-paper-qualification.yml",\n'
        ')',
        'TRUSTED_BUILDER_FILES = (\n'
        '    "scripts/build_ib_candidate_artifact.sh",\n'
        '    "scripts/verify_ib_candidate_artifact.py",\n'
        '    "scripts/run_ib_paper_artifact_qualification.sh",\n'
        '    "scripts/verify_ib_paper_qualification.py",\n'
        '    "docs/ib-paper-profile-policy-v1.json",\n'
        '    ".github/workflows/ib-paper-qualification.yml",\n'
        ')',
    )


def update_docs_index() -> None:
    path = ROOT / "docs/index.md"
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if "governance-qualification" not in line
    ]
    adr = "- [`adr/0002-owner-operated-repository.md`](adr/0002-owner-operated-repository.md)"
    if adr not in lines:
        lines.append(adr)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    heading = "## Owner-operated repository"
    if heading not in readme:
        block = (
            "\n\n## Owner-operated repository\n\n"
            "This is a self-use, single-operator system. The owner may commit, "
            "merge, and release directly. Teams, CODEOWNERS, mandatory approvals, "
            "branch rulesets, Merge Queue, protected governance environments, "
            "and governance receipts are not authorization prerequisites.\n\n"
            "This does not weaken Execution authority, journal-before-send, risk "
            "checks, credential isolation, kill-switch behavior, exact-current-main "
            "artifact binding, Broker reconciliation, or the IB PAPER receipt. "
            "PAPER remains unauthorized until the real Broker campaign passes; "
            "LIVE remains unavailable.\n"
        )
        readme_path.write_text(readme.rstrip() + block + "\n", encoding="utf-8")


def remove_merge_group_events() -> None:
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        output: list[str] = []
        skipping = False
        for line in lines:
            if line == "  merge_group:":
                skipping = True
                continue
            if skipping:
                if line.startswith("    ") or not line.strip():
                    continue
                skipping = False
            output.append(line)
        path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def delete_formalism() -> None:
    for relative in (
        ".github/CODEOWNERS",
        ".github/CODEOWNERS.team-template",
        ".github/github-governance-policy-v1.json",
        ".github/github-team-mapping-v1.json",
        ".github/workflows/github-governance-qualification.yml",
        ".github/workflows/governance-bootstrap-admission.yml",
        "scripts/check_bootstrap_postflight.py",
        "scripts/github_qualification_evidence.py",
        "scripts/verify_bootstrap_postflight_contract.py",
        "scripts/verify_github_governance.py",
        "scripts/verify_github_governance_legacy.py",
        "scripts/verify_qualification_candidate.py",
        "scripts/verify_team_codeowners_activation.py",
        "tests/python/team_codeowners_activation_cases.py",
        "tests/python/test_bootstrap_postflight.py",
        "tests/python/test_bootstrap_postflight_contract.py",
        "tests/python/test_github_governance.py",
        "tests/python/test_github_governance_ruleset_scope.py",
        "tests/python/test_governance_bootstrap_admission.py",
        "tests/python/test_team_codeowners_activation.py",
    ):
        remove(relative)

    for path in sorted((ROOT / ".github/workflows").glob("codex-*")):
        remove(path.relative_to(ROOT).as_posix())


def main() -> None:
    apply_overlay()
    update_gap_register()
    update_catalog()
    update_context_registry()
    update_validators()
    update_docs_index()
    remove_merge_group_events()
    delete_formalism()
    remove(".codex")
    print("[OWNER-OPERATED-MIGRATION] PASS")


if __name__ == "__main__":
    main()
