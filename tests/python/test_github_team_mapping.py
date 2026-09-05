from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import check_github_team_mapping as team_mapping  # noqa: E402


class GitHubTeamMappingTests(unittest.TestCase):
    def _fixture(self, directory: str) -> Path:
        root = Path(directory)
        (root / ".github").mkdir(parents=True)
        (root / "docs" / "modules").mkdir(parents=True)
        for relative in (
            ".github/github-team-mapping-v1.json",
            ".github/github-governance-policy-v1.json",
            ".github/CODEOWNERS.team-template",
            "docs/modules/module-registry-v2.json",
        ):
            source = ROOT / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        shutil.copytree(
            ROOT / "docs" / "modules" / "manifests",
            root / "docs" / "modules" / "manifests",
        )
        return root

    def _mapping(self, root: Path) -> dict:
        return json.loads(
            (root / ".github" / "github-team-mapping-v1.json").read_text(
                encoding="utf-8"
            )
        )

    def _write_mapping(self, root: Path, document: dict) -> None:
        (root / ".github" / "github-team-mapping-v1.json").write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )

    def test_repository_mapping_covers_every_manifest_owner_once(self) -> None:
        errors = team_mapping.validate(ROOT)
        self.assertEqual(errors, [], errors)

    def test_unmapped_manifest_owner_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            document = self._mapping(root)
            removed = document["teams"][0]["logical_handles"].pop()
            self._write_mapping(root, document)
            errors = team_mapping.validate(root)
            self.assertIn(f"unmapped ModuleManifest owner handle: {removed}", errors)

    def test_duplicate_handle_across_teams_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            document = self._mapping(root)
            duplicate = document["teams"][0]["logical_handles"][0]
            document["teams"][1]["logical_handles"].append(duplicate)
            self._write_mapping(root, document)
            errors = team_mapping.validate(root)
            self.assertTrue(
                any(
                    f"logical handle {duplicate} is mapped by both" in error
                    for error in errors
                ),
                errors,
            )

    def test_stale_mapping_handle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            document = self._mapping(root)
            document["teams"][0]["logical_handles"].append("@hepta/stale-owner")
            self._write_mapping(root, document)
            errors = team_mapping.validate(root)
            self.assertIn(
                "stale mapped owner handle not present in ModuleManifest: "
                "@hepta/stale-owner",
                errors,
            )

    def test_template_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            template = root / ".github" / "CODEOWNERS.team-template"
            template.write_text(
                template.read_text(encoding="utf-8") + "# drift\n",
                encoding="utf-8",
            )
            errors = team_mapping.validate(root)
            self.assertTrue(
                any("drift from deterministic GitHub team mapping" in error for error in errors),
                errors,
            )

    def test_unknown_codeowners_team_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            document = self._mapping(root)
            document["codeowners_rules"][0]["teams"][0] = "missing-team"
            self._write_mapping(root, document)
            errors = team_mapping.validate(root)
            self.assertTrue(
                any("unknown team slug missing-team" in error for error in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
