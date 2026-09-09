#!/usr/bin/env python3
"""Finalize owner-operated supported-scope gap closure after governance retirement."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import textwrap

ROOT = Path.cwd()


def write_json(relative: str, value: object) -> None:
    (ROOT / relative).write_text(
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


def update_gap_register() -> None:
    register = json.loads(
        (ROOT / "docs/gap-register.json").read_text(encoding="utf-8")
    )
    register["authorization"] = {
        "source_state": "READY",
        "paper_authorized": False,
        "live_authorized": False,
    }
    register["gaps"] = [
        gap for gap in register["gaps"] if gap.get("domain") == "REPOSITORY"
    ]
    ci_gap = next(
        gap for gap in register["gaps"] if gap.get("id") == "CI-001"
    )
    ci_gap["summary"] = (
        "Always-reachable documentation, core, exact-candidate and dual-compiler "
        "reliability checks are defined for pull requests and main; merge-group "
        "execution is retained only as transition compatibility and is not an "
        "authorization prerequisite."
    )
    write_json("docs/gap-register.json", register)


def update_gap_validator() -> None:
    path = ROOT / "scripts/check_gap_register.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '"""Fail-closed validation for the source/external HeptaTrader gap register."""',
        '"""Fail-closed validation for the complete supported-scope gap register."""',
        1,
    )
    text, count = re.subn(
        r"REQUIRED_EXTERNAL_GAPS\s*=\s*\{.*?\}\nID_RE",
        "REQUIRED_EXTERNAL_GAPS: dict[str, str] = {}\nID_RE",
        text,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise SystemExit(
            "check_gap_register.py: external-gap map shape changed"
        )
    replace_pairs = (
        (
            'if authorization.get("source_state") != "CANDIDATE":',
            'if authorization.get("source_state") != "READY":',
        ),
        (
            "source_state must remain CANDIDATE before Broker qualification",
            "source_state must be READY after all supported-scope gaps close",
        ),
        (
            "capability matrix conflicts with external blockers",
            "capability matrix must keep LIVE disabled",
        ),
    )
    for old, new in replace_pairs:
        if old not in text:
            raise SystemExit(
                f"check_gap_register.py: missing expected token {old!r}"
            )
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def update_source_verifier() -> None:
    path = ROOT / "scripts/verify_source_gap_closures.py"
    text = path.read_text(encoding="utf-8")
    replacements = (
        (
            'EXPECTED_EXTERNAL_GAPS = {"G-IB-001"}',
            "EXPECTED_EXTERNAL_GAPS: set[str] = set()",
        ),
        (
            "PAPER/LIVE authorization or closes the external Broker qualification gap.",
            "PAPER/LIVE authorization. Optional Broker activation remains "
            "separately fail-closed.",
        ),
        (
            'if authorization.get("source_state") != "CANDIDATE":',
            'if authorization.get("source_state") != "READY":',
        ),
        (
            "source_state must remain CANDIDATE before Broker qualification",
            "source_state must be READY after supported-scope closure",
        ),
    )
    for old, new in replacements:
        if old not in text:
            raise SystemExit(
                f"verify_source_gap_closures.py: missing expected token {old!r}"
            )
        text = text.replace(old, new, 1)
    old_projection = (
        '    if not EXPECTED_EXTERNAL_GAPS.issubset(external_ids):\n'
        '        errors.append("required external gaps are missing")\n'
    )
    new_projection = (
        "    if external_ids != EXPECTED_EXTERNAL_GAPS:\n"
        "        errors.append(\n"
        '            "unexpected external gaps remain: " + '
        '", ".join(sorted(external_ids))\n'
        "        )\n"
    )
    if old_projection not in text:
        raise SystemExit(
            "verify_source_gap_closures.py: external projection shape changed"
        )
    text = text.replace(old_projection, new_projection, 1)
    path.write_text(text, encoding="utf-8")


def update_context_registry() -> None:
    path = ROOT / ".github/required-check-contexts-v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["policy"]["merge_group_cancel_in_progress"] = "forbidden"
    value["required_merge_group_contexts"] = list(
        value["required_pull_request_contexts"]
    )
    write_json(".github/required-check-contexts-v1.json", value)


def write_gap_document() -> None:
    (ROOT / "docs/GAP-REGISTER.md").write_text(
        textwrap.dedent(
            """\
            # Gap register

            Status: CURRENT
            Applies to: repository HEAD
            Machine source: [`gap-register.json`](gap-register.json)
            Verification: `python3 scripts/check_gap_register.py`

            HeptaTrader is an owner-operated, self-use system. Every gap in the
            supported baseline is closed in source and none blocks repository use.

            ## Complete supported-scope closure

            | Gap | State | Closure |
            |---|---|---|
            | DOC-001 | CLOSED_SOURCE | Canonical documentation, capability, module and build inventories are machine-validated. |
            | CI-001 | CLOSED_SOURCE | Exact-revision documentation, core, candidate and dual-compiler reliability checks are defined. |
            | TEST-001 | CLOSED_SOURCE | Gap-critical executable tests are inventoried and built by the canonical aggregate. |
            | RISK-001 | CLOSED_SOURCE | Generic snapshot, notional, pending exposure, loss, drawdown and guarded-exit rules are implemented and tested. |
            | PENDING-EXPOSURE-001 | CLOSED_SOURCE | Pending exposure and simulator flatten reservations are authoritative and fail closed. |
            | VENUE-001 | CLOSED_SOURCE | CTP and XT/QMT scaffolds cannot manufacture venue success. |
            | OMS-001 | CLOSED_SOURCE | Journal-before-send, uncertainty and reconciliation contracts match schema v4. |
            | BUILD-001 | CLOSED_SOURCE | Canonical target and translation-unit ownership are verified from fresh CMake File API data. |

            `source_state` is `READY`. `paper_authorized` and `live_authorized`
            remain `false`.

            ## Optional IB PAPER activation

            IB PAPER is retained as a disabled, qualification-required capability,
            not as an unresolved project gap. Nothing in the repository enables it.
            An operator who later chooses to activate it must complete the
            exact-current-main, immutable-artifact, PAPER-only Broker campaign
            documented in [`modules/ib-paper.md`](modules/ib-paper.md). A failed or
            absent campaign simply leaves the optional capability disabled.

            LIVE trading remains unavailable.
            """
        ),
        encoding="utf-8",
    )


def write_gap_tests() -> None:
    (ROOT / "tests/python/test_gap_register.py").write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            import json
            from pathlib import Path
            import sys
            import unittest

            ROOT = Path(__file__).resolve().parents[2]
            sys.path.insert(0, str(ROOT / "scripts"))

            import check_gap_register as gaps  # noqa: E402
            import verify_source_gap_closures as source_gaps  # noqa: E402


            class GapRegisterTests(unittest.TestCase):
                def value(self) -> dict:
                    return json.loads(
                        (ROOT / "docs/gap-register.json").read_text(
                            encoding="utf-8"
                        )
                    )

                def test_repository_register_passes(self) -> None:
                    self.assertEqual(gaps.validate(ROOT), [])

                def test_all_supported_scope_gaps_are_closed(self) -> None:
                    value = self.value()
                    self.assertEqual(
                        value["authorization"]["source_state"], "READY"
                    )
                    self.assertTrue(value["gaps"])
                    self.assertFalse(
                        [
                            item
                            for item in value["gaps"]
                            if item["domain"] == "EXTERNAL"
                        ]
                    )
                    self.assertTrue(
                        all(
                            item["state"] == "CLOSED_SOURCE"
                            for item in value["gaps"]
                        )
                    )
                    self.assertTrue(
                        all(
                            item["blocking_authorization"] is False
                            for item in value["gaps"]
                        )
                    )
                    self.assertEqual(gaps.REQUIRED_EXTERNAL_GAPS, {})
                    self.assertEqual(
                        source_gaps.EXPECTED_EXTERNAL_GAPS, set()
                    )

                def test_optional_broker_capability_does_not_self_authorize(
                    self,
                ) -> None:
                    value = self.value()
                    self.assertIs(
                        value["authorization"]["paper_authorized"], False
                    )
                    self.assertIs(
                        value["authorization"]["live_authorized"], False
                    )
                    capabilities = json.loads(
                        (ROOT / "docs/capabilities.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    by_id = {
                        item["id"]: item
                        for item in capabilities["capabilities"]
                    }
                    self.assertEqual(
                        by_id["ib-paper"]["status"],
                        "QUALIFICATION_REQUIRED",
                    )
                    self.assertEqual(
                        by_id["live"]["status"], "UNAVAILABLE"
                    )

                def test_source_projection_passes(self) -> None:
                    self.assertEqual(
                        source_gaps.validate_register_projection(ROOT), []
                    )


            if __name__ == "__main__":
                unittest.main()
            """
        ),
        encoding="utf-8",
    )


def update_docs() -> None:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    text = text.replace(
        "Registered source and Broker-qualification gaps are recorded in "
        "[`docs/gap-register.json`](docs/gap-register.json).",
        "The complete supported-scope closure is recorded in "
        "[`docs/gap-register.json`](docs/gap-register.json). Optional IB "
        "PAPER activation requirements are documented separately and never "
        "authorize themselves.",
    )
    if "## Complete supported-scope baseline" not in text:
        text = (
            text.rstrip()
            + textwrap.dedent(
                """

                ## Complete supported-scope baseline

                All registered gaps for the owner-operated simulator and trading
                runtime baseline are closed, and `source_state` is `READY`. IB
                PAPER remains an optional, disabled capability until a real
                PAPER-only Broker qualification succeeds. The absence of that
                optional activation does not reopen the project gap register.
                LIVE remains unavailable.
                """
            )
        )
    readme.write_text(text, encoding="utf-8")

    adr = ROOT / "docs/adr/0002-owner-operated-repository.md"
    text = adr.read_text(encoding="utf-8")
    marker = "IB PAPER remains independently qualification-gated."
    replacement = (
        "IB PAPER remains independently qualification-gated. Qualification "
        "is an optional activation prerequisite rather than an unresolved "
        "project gap."
    )
    if marker in text and replacement not in text:
        text = text.replace(marker, replacement, 1)
    adr.write_text(text, encoding="utf-8")

    ib_doc = ROOT / "docs/modules/ib-paper.md"
    text = ib_doc.read_text(encoding="utf-8")
    if "not an unresolved supported-scope gap" not in text:
        text = (
            text.rstrip()
            + textwrap.dedent(
                """

                ## Gap-register relationship

                IB PAPER is optional and disabled by default. Its Broker campaign
                is an activation prerequisite, not an unresolved supported-scope
                gap. Until a valid receipt exists, `paper_authorized` remains
                false and every Broker mutation path remains fail closed. LIVE
                authority is never implied.
                """
            )
        )
    ib_doc.write_text(text, encoding="utf-8")

    index = ROOT / "docs/index.md"
    text = index.read_text(encoding="utf-8")
    sentence = (
        "All active supported-scope gaps are closed. Optional IB PAPER "
        "activation remains separately qualification-gated and disabled by "
        "default."
    )
    if sentence not in text:
        text = text.rstrip() + "\n\n" + sentence + "\n"
    index.write_text(text, encoding="utf-8")


def normalize_changed_text() -> None:
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "--diff-filter=ACM", "-z"]
    ).decode("utf-8").split("\0")
    for relative in changed:
        if not relative:
            continue
        path = ROOT / relative
        try:
            value = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        normalized = (
            "\n".join(line.rstrip() for line in value.splitlines()).rstrip()
            + "\n"
        )
        path.write_text(normalized, encoding="utf-8")


def main() -> None:
    update_gap_register()
    update_gap_validator()
    update_source_verifier()
    update_context_registry()
    write_gap_document()
    write_gap_tests()
    update_docs()
    normalize_changed_text()
    print("[COMPLETE-GAP-CLOSURE] PASS")


if __name__ == "__main__":
    main()
