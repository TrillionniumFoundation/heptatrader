from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "systemd" / "hepta-broker-network-policy-v1.json"
IDENTITIES_PATH = ROOT / "systemd" / "hepta-service-identities-v1.json"
HOST_MAP_PATH = ROOT / "systemd" / "hepta-x230-paper-host-identity-map-v1.json"
SPEC = importlib.util.spec_from_file_location(
    "hepta_broker_egress_policy",
    ROOT / "scripts" / "hepta_broker_egress_policy.py",
)
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class BrokerEgressRulesetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy_raw = POLICY_PATH.read_bytes()
        cls.policy_value = json.loads(cls.policy_raw)
        cls.identities = json.loads(
            IDENTITIES_PATH.read_text(encoding="utf-8")
        )
        cls.host_map_raw = HOST_MAP_PATH.read_bytes()
        cls.host_map = json.loads(cls.host_map_raw)
        cls.validated = POLICY._validate_policy(cls.policy_value)  # noqa: SLF001

    def canonical_policy_root(
        self,
        directory: str,
        *,
        raw: bytes | None = None,
        mode: int = 0o644,
    ) -> tuple[Path, Path]:
        root = Path(directory)
        parent = root
        for part in POLICY.CANONICAL_POLICY_PARENT_PARTS:
            parent = parent / part
            parent.mkdir(exist_ok=True)
            parent.chmod(0o755)
        path = parent / POLICY.DEFAULT_POLICY.name
        path.write_bytes(self.policy_raw if raw is None else raw)
        path.chmod(mode)
        return root, path

    def read_fixture(
        self,
        root: Path,
        **overrides,
    ):
        arguments = {
            "root": root,
            "expected_directory_uid": os.geteuid(),
            "expected_directory_gid": os.getegid(),
            "expected_policy_uid": os.geteuid(),
            "expected_policy_gid": os.getegid(),
        }
        arguments.update(overrides)
        return POLICY._read_policy(  # noqa: SLF001
            POLICY.DEFAULT_POLICY,
            **arguments,
        )

    @staticmethod
    def render(
        *,
        uids: tuple[int, ...],
        deny_all: bool,
    ) -> str:
        return POLICY._ruleset(  # noqa: SLF001
            "inet",
            "hepta_broker_egress_v1",
            "output",
            (4001, 4002, 7496, 7497),
            uids,
            deny_all=deny_all,
            replace_existing=False,
        ).decode("ascii")

    def test_canonical_policy_digest_and_install_contract(self) -> None:
        self.assertEqual(
            hashlib.sha256(self.policy_raw).hexdigest(),
            POLICY.CANONICAL_POLICY_SHA256,
        )
        install = (ROOT / "cmake/HeptaInstall.cmake").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "systemd/hepta-broker-network-policy-v1.json",
            install,
        )
        package_policy = json.loads(
            (ROOT / "docs/preflight-policy-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(
            "share/heptatrader/hepta-broker-network-policy-v1.json",
            package_policy["profiles"]["ib-paper"][
                "required_package_paths"
            ],
        )

    def test_canonical_policy_reader_accepts_exact_owned_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, _ = self.canonical_policy_root(directory)
            self.assertEqual(
                POLICY._validate_policy(self.read_fixture(root)),  # noqa: SLF001
                POLICY.COMPILED_POLICY,
            )

    def test_policy_reader_rejects_noncanonical_argument(self) -> None:
        with self.assertRaisesRegex(
            POLICY.PolicyError, "path must be exactly"
        ):
            POLICY._read_policy(POLICY_PATH)  # noqa: SLF001

    def test_non_root_owned_mode_0600_policy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, _ = self.canonical_policy_root(
                directory, mode=0o600
            )
            with self.assertRaisesRegex(
                POLICY.PolicyError,
                "canonical root-owned mode-0644",
            ):
                self.read_fixture(
                    root,
                    expected_policy_uid=0,
                    expected_policy_gid=0,
                )

    def test_group_writable_policy_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, path = self.canonical_policy_root(directory)
            path.parent.chmod(0o775)
            with self.assertRaisesRegex(
                POLICY.PolicyError,
                "not group/world writable",
            ):
                self.read_fixture(root)

    def test_policy_digest_mismatch_is_rejected(self) -> None:
        changed = self.policy_raw.replace(b"2003", b"2004")
        self.assertNotEqual(changed, self.policy_raw)
        with tempfile.TemporaryDirectory() as directory:
            root, _ = self.canonical_policy_root(
                directory, raw=changed
            )
            with self.assertRaisesRegex(
                POLICY.PolicyError, "SHA-256"
            ):
                self.read_fixture(root)

    def test_policy_final_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, path = self.canonical_policy_root(directory)
            target = path.with_name("target.json")
            target.write_bytes(self.policy_raw)
            target.chmod(0o644)
            path.unlink()
            path.symlink_to(target.name)
            with self.assertRaises((OSError, POLICY.PolicyError)):
                self.read_fixture(root)

    def test_policy_in_place_mutation_during_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, path = self.canonical_policy_root(directory)
            original = POLICY._read_bounded_policy  # noqa: SLF001
            changed = False

            def mutating_read(descriptor: int) -> bytes:
                nonlocal changed
                raw = original(descriptor)
                with path.open("r+b") as stream:
                    stream.seek(0)
                    stream.write(
                        self.policy_raw.replace(b"2003", b"2004")
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                changed = True
                return raw

            with mock.patch.object(
                POLICY,
                "_read_bounded_policy",
                side_effect=mutating_read,
            ):
                with self.assertRaisesRegex(
                    POLICY.PolicyError, "changed while being read"
                ):
                    self.read_fixture(root)
            self.assertTrue(changed)

    def test_policy_final_replacement_during_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, path = self.canonical_policy_root(directory)
            original = POLICY._read_bounded_policy  # noqa: SLF001
            replaced = False

            def replacing_read(descriptor: int) -> bytes:
                nonlocal replaced
                raw = original(descriptor)
                replacement = path.with_name("replacement.json")
                replacement.write_bytes(self.policy_raw)
                replacement.chmod(0o644)
                os.replace(replacement, path)
                replaced = True
                return raw

            with mock.patch.object(
                POLICY,
                "_read_bounded_policy",
                side_effect=replacing_read,
            ):
                with self.assertRaisesRegex(
                    POLICY.PolicyError, "changed"
                ):
                    self.read_fixture(root)
            self.assertTrue(replaced)

    def test_policy_parent_substitution_during_read_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, path = self.canonical_policy_root(directory)
            original_parent = path.parent
            displaced = original_parent.with_name(
                "heptatrader-displaced"
            )
            original = POLICY._read_bounded_policy  # noqa: SLF001
            replaced = False

            def replacing_parent(descriptor: int) -> bytes:
                nonlocal replaced
                raw = original(descriptor)
                original_parent.rename(displaced)
                original_parent.mkdir()
                original_parent.chmod(0o755)
                new_policy = original_parent / POLICY.DEFAULT_POLICY.name
                new_policy.write_bytes(self.policy_raw)
                new_policy.chmod(0o644)
                replaced = True
                return raw

            with mock.patch.object(
                POLICY,
                "_read_bounded_policy",
                side_effect=replacing_parent,
            ):
                with self.assertRaisesRegex(
                    POLICY.PolicyError, "parent or final path"
                ):
                    self.read_fixture(root)
            self.assertTrue(replaced)

    def test_policy_read_failure_attempts_compiled_deny_all(self) -> None:
        nft = Path("/usr/sbin/nft")
        with (
            mock.patch.object(
                POLICY,
                "_read_policy",
                side_effect=POLICY.PolicyError("untrusted policy"),
            ),
            mock.patch.object(POLICY, "_apply") as apply,
        ):
            with self.assertRaisesRegex(
                POLICY.PolicyError, "untrusted policy"
            ):
                POLICY._execute(  # noqa: SLF001
                    nft,
                    POLICY.DEFAULT_POLICY,
                    apply_requested=True,
                )
        apply.assert_called_once_with(
            nft, POLICY.COMPILED_POLICY, deny_all=True
        )

    def test_allow_apply_failure_attempts_compiled_deny_all(
        self,
    ) -> None:
        nft = Path("/usr/sbin/nft")
        with (
            mock.patch.object(
                POLICY,
                "_read_policy",
                return_value=self.policy_value,
            ),
            mock.patch.object(
                POLICY,
                "_apply",
                side_effect=[
                    POLICY.PolicyError("allow failed"),
                    None,
                ],
            ) as apply,
        ):
            with self.assertRaisesRegex(
                POLICY.PolicyError, "allow failed"
            ):
                POLICY._execute(  # noqa: SLF001
                    nft,
                    POLICY.DEFAULT_POLICY,
                    apply_requested=True,
                )
        self.assertEqual(
            apply.call_args_list,
            [
                mock.call(
                    nft,
                    POLICY.COMPILED_POLICY,
                    deny_all=False,
                ),
                mock.call(
                    nft,
                    POLICY.COMPILED_POLICY,
                    deny_all=True,
                ),
            ],
        )

    def test_explicit_deny_all_does_not_read_policy(self) -> None:
        nft = Path("/usr/sbin/nft")
        with (
            mock.patch.object(POLICY, "_read_policy") as read,
            mock.patch.object(POLICY, "_apply") as apply,
        ):
            POLICY._execute(  # noqa: SLF001
                nft,
                POLICY.DEFAULT_POLICY,
                apply_requested=False,
            )
        read.assert_not_called()
        apply.assert_called_once_with(
            nft, POLICY.COMPILED_POLICY, deny_all=True
        )

    def test_canonical_policy_binds_the_logical_execution_identity(self) -> None:
        family, table, chain, ports, uids = self.validated
        logical = self.host_map["logical_execution_identity"]
        canonical = self.identities["identities"][logical["name"]]
        self.assertEqual(family, "inet")
        self.assertEqual(table, "hepta_broker_egress_v1")
        self.assertEqual(chain, "output")
        self.assertEqual(ports, (4001, 4002, 7496, 7497))
        self.assertEqual(uids, (2003,))
        self.assertEqual(self.validated, POLICY.COMPILED_POLICY)
        self.assertEqual(self.policy_value["authorized_uids"], [2003])
        self.assertEqual(
            logical,
            {
                "name": "hepta-ib-exec",
                "uid": canonical["uid"],
                "gid": canonical["gid"],
            },
        )
        self.assertEqual(
            canonical["role"],
            "ib-paper-execution-authority",
        )

    def test_x230_host_mapping_is_distinct_and_non_live(self) -> None:
        logical = self.host_map["logical_execution_identity"]
        execution = self.host_map["runtime_execution_identity"]
        runner = self.host_map["runtime_runner_identity"]
        self.assertEqual(
            self.host_map["scope"],
            "ib-paper-qualification-only",
        )
        self.assertIs(self.host_map["live_authorized"], False)
        self.assertEqual(execution["name"], "hepta-codex-ib")
        self.assertEqual(execution["uid"], 995)
        self.assertEqual(runner["name"], "hepta-actions-paper")
        self.assertEqual(runner["uid"], 994)
        self.assertEqual(
            len(
                {
                    logical["uid"],
                    execution["uid"],
                    runner["uid"],
                }
            ),
            3,
        )

    def test_canonical_apply_is_loopback_only_and_uid_scoped(self) -> None:
        rules = self.render(uids=(2003,), deny_all=False)
        self.assertIn(
            "ip daddr 127.0.0.0/8 tcp dport "
            "{ 4001, 4002, 7496, 7497 } "
            "meta skuid { 2003 } accept",
            rules,
        )
        self.assertIn(
            "ip6 daddr ::1 tcp dport "
            "{ 4001, 4002, 7496, 7497 } "
            "meta skuid { 2003 } accept",
            rules,
        )
        self.assertEqual(rules.count("reject with tcp reset"), 2)
        self.assertNotIn(
            "output tcp dport { 4001, 4002, 7496, 7497 }",
            rules,
        )

    def test_host_policy_renderer_allows_only_mapped_execution_uid(self) -> None:
        execution_uid = self.host_map[
            "runtime_execution_identity"
        ]["uid"]
        runner_uid = self.host_map["runtime_runner_identity"]["uid"]
        rules = self.render(
            uids=(execution_uid,), deny_all=False
        )
        self.assertIn("meta skuid { 995 } accept", rules)
        self.assertNotIn("meta skuid { 2003 } accept", rules)
        self.assertNotIn("meta skuid { 994 } accept", rules)
        self.assertNotEqual(execution_uid, runner_uid)

    def test_deny_all_retains_both_loopback_families(self) -> None:
        rules = self.render(uids=(2003,), deny_all=True)
        self.assertNotIn("meta skuid", rules)
        self.assertIn("ip daddr 127.0.0.0/8", rules)
        self.assertIn("ip6 daddr ::1", rules)
        self.assertEqual(rules.count("reject with tcp reset"), 2)


if __name__ == "__main__":
    unittest.main()
