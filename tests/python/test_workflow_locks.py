from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
ACTION_LOCK = ROOT / "ci/actions.lock.json"
TOOLCHAIN_LOCK = ROOT / "ci/hosted-toolchain.lock.json"
WORKFLOW_ROOT = ROOT / ".github/workflows"
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^v[0-9]+(?:\.[0-9]+){0,2}(?:[-+._0-9A-Za-z]*)$")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path):
    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=unique_object
    )


class WorkflowLockTests(unittest.TestCase):
    def test_external_actions_match_reviewed_allowlist_exactly(self) -> None:
        payload = load_json(ACTION_LOCK)
        self.assertEqual(payload.get("schema"), "heptatrader.github-actions-lock.v1")
        self.assertEqual(set(payload), {"schema", "actions"})
        self.assertIsInstance(payload["actions"], list)

        allowed = {}
        ordered = []
        for item in payload["actions"]:
            self.assertEqual(set(item), {"uses", "version", "revision"})
            uses = item["uses"]
            version = item["version"]
            revision = item["revision"]
            self.assertIsInstance(uses, str)
            self.assertRegex(version, VERSION)
            self.assertRegex(revision, FULL_SHA)
            self.assertNotIn(uses, allowed)
            allowed[uses] = revision
            ordered.append(uses)
        self.assertEqual(ordered, sorted(ordered))

        observed = set()
        for workflow in sorted(WORKFLOW_ROOT.glob("*.y*ml")):
            text = workflow.read_text(encoding="utf-8")
            for specification in USES.findall(text):
                if specification.startswith("./"):
                    continue
                self.assertFalse(
                    specification.startswith("docker://"),
                    f"container action needs a separately reviewed digest lock: {specification}",
                )
                self.assertIn("@", specification)
                action, revision = specification.rsplit("@", 1)
                self.assertIn(action, allowed, f"unreviewed action in {workflow}: {action}")
                self.assertEqual(
                    revision,
                    allowed[action],
                    f"action revision drift in {workflow}: {specification}",
                )
                observed.add(action)
        self.assertEqual(observed, set(allowed), "action lock contains unused entries")

    def test_hosted_workflows_are_offline_after_checkout_and_verify_lock(self) -> None:
        for relative in (
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
            ".github/workflows/nightly-sanitizers.yml",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            lowered = text.lower()
            self.assertNotIn("apt-get", lowered, relative)
            self.assertNotRegex(lowered, r"\bapt\s+(?:update|install|upgrade)\b", relative)
            self.assertIn("scripts/verify_ci_toolchain.py", text, relative)

    def test_toolchain_lock_has_exact_reviewed_shape(self) -> None:
        payload = load_json(TOOLCHAIN_LOCK)
        self.assertEqual(
            payload.get("schema"), "heptatrader.hosted-toolchain-lock.v1"
        )
        self.assertEqual(set(payload), {"schema", "runner", "tools"})
        self.assertEqual(
            set(payload["runner"]),
            {"image_os", "image_version", "os_version_id"},
        )
        self.assertEqual(
            set(payload["tools"]),
            {
                "cmake",
                "ninja",
                "python",
                "git",
                "openssl",
                "libssl_dev_package",
                "gcc",
                "clang",
            },
        )
        for section in (payload["runner"], payload["tools"]):
            for value in section.values():
                self.assertIsInstance(value, str)
                self.assertTrue(value)
                self.assertTrue(value.isascii())


if __name__ == "__main__":
    unittest.main()
