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

import verify_team_codeowners_activation as activation  # noqa: E402


class TeamCodeownersActivationTests(unittest.TestCase):
    def _fixture(self, directory: str) -> Path:
        root = Path(directory)
        for relative in (
            activation.MAPPING_REL,
            activation.POLICY_REL,
            activation.TEMPLATE_REL,
            activation.ACTIVE_REL,
        ):
            source = ROOT / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        return root

    def _mapping(self, root: Path) -> dict:
        return json.loads((root / activation.MAPPING_REL).read_text(encoding="utf-8"))

    def _write_mapping(self, root: Path, document: dict) -> None:
        (root / activation.MAPPING_REL).write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )

    def test_repository_activation_contract_passes(self) -> None:
        self.assertEqual(activation.validate(ROOT), [])

    def test_active_codeowners_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            active = root / activation.ACTIVE_REL
            active.write_text(
                active.read_text(encoding="utf-8") + "# drift\n",
                encoding="utf-8",
            )
            errors = activation.validate(root)
            self.assertTrue(
                any("active bytes differ" in error for error in errors), errors
            )

    def test_individual_owner_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            active = root / activation.ACTIVE_REL
            text = active.read_text(encoding="utf-8")
            first_team = "@TrillionniumFoundation/architecture-contracts"
            active.write_text(text.replace(first_team, "@ProfHepta", 1), encoding="utf-8")
            errors = activation.validate(root)
            self.assertTrue(
                any("owner must be an organization team" in error for error in errors),
                errors,
            )

    def test_unknown_mapping_team_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            document = self._mapping(root)
            document["codeowners_rules"][0]["teams"][0] = "missing-team"
            self._write_mapping(root, document)
            errors = activation.validate(root)
            self.assertTrue(
                any("unknown team slug missing-team" in error for error in errors),
                errors,
            )

    def test_single_team_rule_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            document = self._mapping(root)
            document["codeowners_rules"][0]["teams"] = [
                document["codeowners_rules"][0]["teams"][0]
            ]
            self._write_mapping(root, document)
            errors = activation.validate(root)
            self.assertTrue(
                any("requires at least two independent teams" in error for error in errors),
                errors,
            )

    def test_template_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            template = root / activation.TEMPLATE_REL
            template.write_text(
                template.read_text(encoding="utf-8") + "# drift\n",
                encoding="utf-8",
            )
            errors = activation.validate(root)
            self.assertTrue(
                any("drift from deterministic team mapping" in error for error in errors),
                errors,
            )

    def test_symlinked_activation_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            active = root / activation.ACTIVE_REL
            backup = active.with_name("CODEOWNERS.real")
            active.rename(backup)
            active.symlink_to(backup.name)
            errors = activation.validate(root)
            self.assertTrue(
                any("regular single-link file" in error for error in errors), errors
            )


if __name__ == "__main__":
    unittest.main()
