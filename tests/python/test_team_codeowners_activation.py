from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import verify_team_codeowners_activation as activation  # noqa: E402


class TeamCodeownersActivationTests(unittest.TestCase):
    def _mapping(self, root: Path) -> dict:
        return json.loads((root / activation.MAPPING_REL).read_text(encoding="utf-8"))

    def _policy(self, root: Path) -> dict:
        return json.loads((root / activation.POLICY_REL).read_text(encoding="utf-8"))

    def _write_mapping(self, root: Path, document: dict) -> None:
        (root / activation.MAPPING_REL).write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )

    def _write_policy(self, root: Path, document: dict) -> None:
        (root / activation.POLICY_REL).write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )

    def _seed_tracked_roots(self, root: Path) -> None:
        mapping = self._mapping(root)
        for rule in mapping["codeowners_rules"]:
            pattern = rule["pattern"]
            if pattern == "*":
                continue
            marker = root / pattern.strip("/") / ".codeowners-fixture"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("tracked fixture\n", encoding="utf-8")

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
        self._seed_tracked_roots(root)
        return root

    def _sync_generated_documents(
        self, root: Path, mapping: dict, policy: dict | None = None
    ) -> None:
        if policy is None:
            policy = self._policy(root)
        render_errors: list[str] = []
        organization = mapping["organization"]
        team_slugs = [item["slug"] for item in mapping["teams"]]
        rendered, patterns, _ = activation._render_template(
            mapping, organization, team_slugs, render_errors
        )
        self.assertEqual(render_errors, [])
        policy["codeowners"]["required_patterns"] = patterns
        self._write_mapping(root, mapping)
        self._write_policy(root, policy)
        (root / activation.TEMPLATE_REL).write_text(rendered, encoding="utf-8")
        (root / activation.ACTIVE_REL).write_text(rendered, encoding="utf-8")

    def _assert_cli_rejects(self, root: Path) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(activation, "ROOT", root),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = activation.main()
        self.assertEqual(exit_code, 1)
        self.assertNotIn("PASS", stdout.getvalue())
        self.assertIn("[TEAM-CODEOWNERS-ACTIVATION]", stderr.getvalue())

    def test_repository_activation_contract_passes(self) -> None:
        self.assertEqual(activation.validate(ROOT), [])

    def test_isolated_closed_world_fixture_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            self.assertEqual(activation.validate(root), [])

    def test_active_codeowners_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            active = root / activation.ACTIVE_REL
            active.write_text(
                active.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8"
            )
            errors = activation.validate(root)
            self.assertTrue(any("active bytes differ" in error for error in errors), errors)

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
            self.assertTrue(any("unknown team slug missing-team" in error for error in errors), errors)

    def test_every_mapped_team_requires_a_matched_non_wildcard_rule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            mapping = self._mapping(root)
            for rule in mapping["codeowners_rules"]:
                if rule["pattern"] == "*":
                    rule["teams"].append("strategy-runtime")
                else:
                    rule["teams"] = [
                        "architecture-contracts" if slug == "strategy-runtime" else slug
                        for slug in rule["teams"]
                    ]
                    rule["teams"] = list(dict.fromkeys(rule["teams"]))
                    if len(rule["teams"]) < 2:
                        rule["teams"].append("reliability-operations-research")
            self._sync_generated_documents(root, mapping)
            errors = activation.validate(root)
            self.assertTrue(
                any(
                    "team strategy-runtime has no matched non-wildcard CODEOWNERS rule"
                    in error
                    for error in errors
                ),
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
            self.assertTrue(any("requires at least two independent teams" in error for error in errors), errors)

    def test_template_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            template = root / activation.TEMPLATE_REL
            template.write_text(
                template.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8"
            )
            errors = activation.validate(root)
            self.assertTrue(any("drift from deterministic team mapping" in error for error in errors), errors)

    def test_non_object_json_is_fail_closed_for_mapping_and_policy(self) -> None:
        payloads = {
            "empty": b"",
            "null": b"null\n",
            "array": b"[]\n",
            "string": b'"value"\n',
            "integer": b"1\n",
            "boolean": b"true\n",
            "nan": b"NaN\n",
            "positive-infinity": b"Infinity\n",
            "negative-infinity": b"-Infinity\n",
        }
        for relative in (activation.MAPPING_REL, activation.POLICY_REL):
            for label, payload in payloads.items():
                with self.subTest(relative=relative.as_posix(), payload=label):
                    with tempfile.TemporaryDirectory() as directory:
                        root = self._fixture(directory)
                        (root / relative).write_bytes(payload)
                        (root / activation.ACTIVE_REL).write_text(
                            "* @unapproved-individual\n", encoding="utf-8"
                        )
                        errors = activation.validate(root)
                        self.assertTrue(errors)
                        self._assert_cli_rejects(root)

    def test_security_policy_downgrades_are_rejected(self) -> None:
        mutations = (
            lambda m, p: p["codeowners"].__setitem__("minimum_distinct_teams", True),
            lambda m, p: p["codeowners"].__setitem__("minimum_distinct_teams", 0),
            lambda m, p: p["codeowners"].__setitem__("minimum_distinct_teams", 3),
            lambda m, p: (
                m.__setitem__("minimum_maintainers_per_team", True),
                p["codeowners"].__setitem__("minimum_maintainers_per_team", True),
            ),
            lambda m, p: p["codeowners"].__setitem__("require_non_secret_team", False),
            lambda m, p: p["codeowners"].__setitem__("require_write_access", False),
            lambda m, p: p.__setitem__("repository", "OtherOrg/heptatrader"),
            lambda m, p: p.__setitem__("default_branch", "develop"),
            lambda m, p: m["tracked_path_policy"].__setitem__("allow_future_only_patterns", True),
            lambda m, p: p["codeowners"]["tracked_path_policy"].__setitem__("source", "filesystem-scan"),
        )
        for position, mutate in enumerate(mutations):
            with self.subTest(mutation=position):
                with tempfile.TemporaryDirectory() as directory:
                    root = self._fixture(directory)
                    mapping = self._mapping(root)
                    policy = self._policy(root)
                    mutate(mapping, policy)
                    self._write_mapping(root, mapping)
                    self._write_policy(root, policy)
                    self.assertTrue(activation.validate(root))

    def test_foreign_organization_cannot_be_made_self_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            mapping = self._mapping(root)
            policy = self._policy(root)
            organization = "OtherOrganization"
            mapping["organization"] = organization
            policy["organization"] = organization
            policy["repository"] = f"{organization}/heptatrader"
            policy["codeowners"]["required_team_prefix"] = f"@{organization}/"
            render_errors: list[str] = []
            rendered, _, _ = activation._render_template(
                mapping,
                organization,
                [item["slug"] for item in mapping["teams"]],
                render_errors,
            )
            self.assertEqual(render_errors, [])
            self._write_mapping(root, mapping)
            self._write_policy(root, policy)
            (root / activation.TEMPLATE_REL).write_text(rendered, encoding="utf-8")
            (root / activation.ACTIVE_REL).write_text(rendered, encoding="utf-8")
            errors = activation.validate(root)
            self.assertTrue(any("organization" in error or "repository" in error for error in errors), errors)

    def test_codeowners_byte_drift_is_not_normalized(self) -> None:
        transforms = {
            "utf8-bom": lambda data: b"\xef\xbb\xbf" + data,
            "crlf": lambda data: data.replace(b"\n", b"\r\n"),
        }
        for relative in (activation.TEMPLATE_REL, activation.ACTIVE_REL):
            for label, transform in transforms.items():
                with self.subTest(relative=relative.as_posix(), transform=label):
                    with tempfile.TemporaryDirectory() as directory:
                        root = self._fixture(directory)
                        path = root / relative
                        path.write_bytes(transform(path.read_bytes()))
                        errors = activation.validate(root)
                        expected = (
                            "drift from deterministic team mapping"
                            if relative == activation.TEMPLATE_REL
                            else "active bytes differ"
                        )
                        self.assertTrue(any(expected in error for error in errors), errors)

    def test_invalid_utf8_is_rejected_before_codeowners_parsing(self) -> None:
        for relative in (activation.TEMPLATE_REL, activation.ACTIVE_REL):
            with self.subTest(relative=relative.as_posix()):
                with tempfile.TemporaryDirectory() as directory:
                    root = self._fixture(directory)
                    (root / relative).write_bytes(b"\xff\n")
                    errors = activation.validate(root)
                    self.assertTrue(any("invalid UTF-8" in error for error in errors), errors)

    def test_nonexistent_future_pattern_cannot_qualify_a_team(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            mapping = self._mapping(root)
            target = next(
                rule for rule in mapping["codeowners_rules"]
                if rule["pattern"] == "/HeptaSimulator/"
            )
            target["pattern"] = "/FutureSimulator/"
            self._sync_generated_documents(root, mapping)
            errors = activation.validate(root)
            self.assertTrue(any("matches no tracked path" in error for error in errors), errors)
            self.assertTrue(any("missing exact top-level" in error for error in errors), errors)

    def test_case_drift_cannot_match_a_real_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            mapping = self._mapping(root)
            target = next(
                rule for rule in mapping["codeowners_rules"]
                if rule["pattern"] == "/HeptaTrade/"
            )
            target["pattern"] = "/heptatrade/"
            self._sync_generated_documents(root, mapping)
            errors = activation.validate(root)
            self.assertTrue(any("matches no tracked path" in error for error in errors), errors)

    def test_renamed_root_requires_an_exact_mapping_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            (root / "HeptaTrade").rename(root / "HeptaTrading")
            errors = activation.validate(root)
            self.assertTrue(any("matches no tracked path" in error for error in errors), errors)
            self.assertTrue(any("/HeptaTrading/" in error for error in errors), errors)

    def test_empty_directory_is_not_tracked_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            marker = root / "HeptaSimulator" / ".codeowners-fixture"
            marker.unlink()
            errors = activation.validate(root)
            self.assertTrue(any("/HeptaSimulator/ matches no tracked path" in error for error in errors), errors)

    def test_new_top_level_root_fails_closed_until_owned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            new_file = root / "NewRuntime" / "component.cpp"
            new_file.parent.mkdir()
            new_file.write_text("int component;\n", encoding="utf-8")
            errors = activation.validate(root)
            self.assertTrue(any("/NewRuntime/" in error for error in errors), errors)

    def test_nested_qualification_rule_is_rejected_even_when_it_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            nested = root / "HeptaTrade" / "future" / "component.cpp"
            nested.parent.mkdir(parents=True)
            nested.write_text("int component;\n", encoding="utf-8")
            mapping = self._mapping(root)
            mapping["codeowners_rules"].append(
                {
                    "pattern": "/HeptaTrade/future/",
                    "teams": ["execution-oms", "portfolio-risk"],
                }
            )
            policy = self._policy(root)
            policy["codeowners"]["required_patterns"].append("/HeptaTrade/future/")
            self._write_mapping(root, mapping)
            self._write_policy(root, policy)
            errors = activation.validate(root)
            self.assertTrue(any("exact top-level directory pattern" in error for error in errors), errors)

    def test_root_rule_order_is_bound_to_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            mapping = self._mapping(root)
            mapping["codeowners_rules"][1], mapping["codeowners_rules"][2] = (
                mapping["codeowners_rules"][2],
                mapping["codeowners_rules"][1],
            )
            self._sync_generated_documents(root, mapping)
            errors = activation.validate(root)
            self.assertTrue(any("byte-sorted tracked-root order" in error for error in errors), errors)

    def test_symlinked_activation_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            active = root / activation.ACTIVE_REL
            backup = active.with_name("CODEOWNERS.real")
            active.rename(backup)
            active.symlink_to(backup.name)
            errors = activation.validate(root)
            self.assertTrue(any("regular single-link file" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
