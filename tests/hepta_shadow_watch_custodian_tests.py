#!/usr/bin/env python3

"""Rootless fault tests for the fail-closed WATCH lifecycle custodian."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]


def load_module():
    path = REPOSITORY / "scripts/hepta_shadow_watch_custodian.py"
    spec = importlib.util.spec_from_file_location(
        "hepta_shadow_watch_custodian_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CustodianFixture:
    def __init__(
            self, module, root: Path, *, session_outcomes=None,
            active_authority=True):
        self.module = module
        self.root = root
        self.runtime = root / "runtime"
        self.runtime.mkdir(mode=0o711)
        self.runtime.chmod(0o711)
        assert stat.S_IMODE(self.runtime.stat().st_mode) == 0o711
        self.state = root / "state"
        self.export_root = root / "run"
        self.export_root.mkdir(mode=0o700)
        self.watch_state_root = root / "watch-state"
        self.watch_state_root.mkdir(mode=0o700)
        self.config_path = root / "custtest.json"
        self.config_path.write_text("{}\n", encoding="ascii")
        self.config_path.chmod(0o600)
        self.token_bytes = b"custodian-fixture-token-" + b"x" * 32
        self.agent_uid = os.geteuid()
        self.agent_gid = os.getegid()
        self.owner_uid = self.agent_uid + 10_000
        # A distinct non-root owner UID may legitimately use the operator's
        # fixed reader group. Keeping the real fixture GID lets rootless tests
        # exercise the exact exported-evidence metadata contract.
        self.owner_gid = self.agent_gid
        self.owner_pid = 4242
        self.generation = 1
        self.boot_id = "11111111-2222-3333-4444-555555555555"
        self.config = {
            "domain_id": "custtest",
            "gateway_uid": self.agent_uid + 100,
            "agent_uid": self.agent_uid,
            "agent_gid": self.agent_gid,
            "execution_uid": self.agent_uid + 200,
            "token_directory": str(self.runtime),
            "supervisor_socket": str(root / "supervisor.sock"),
            "paper_authorized": False,
            "live_authorized": False,
        }
        self.config_digest = "sha256:" + "1" * 64
        self.sessionctl = root / "hepta-sessionctl"
        self.session_count = root / "session-count"
        self.session_log = root / "session-log"
        outcomes = session_outcomes or ["accepted"]
        self._write_sessionctl(outcomes)
        self.saved = {
            "ROOT_UID": module.ROOT_UID,
            "ROOT_GID": module.ROOT_GID,
            "STATE_ROOT": module.STATE_ROOT,
            "WATCH_STATE_ROOT": module.WATCH_STATE_ROOT,
            "EXPORT_RUNTIME_ROOT": module.EXPORT_RUNTIME_ROOT,
            "SESSIONCTL": module.SESSIONCTL,
            "_load_config": module._load_config,
            "_load_watch_reader_identity":
            module._load_watch_reader_identity,
            "_process_identity": module._process_identity,
            "_process_matches": module._process_matches,
            "_now_ms": module._now_ms,
            "_fault": module._fault,
            "_invoke_bootstrap": module._invoke_bootstrap,
        }
        module.ROOT_UID = os.geteuid()
        module.ROOT_GID = os.getegid()
        module.STATE_ROOT = self.state
        module.WATCH_STATE_ROOT = self.watch_state_root
        module.EXPORT_RUNTIME_ROOT = self.export_root
        module.SESSIONCTL = str(self.sessionctl)
        module._load_config = lambda _path: (
            dict(self.config), self.config_digest)
        module._load_watch_reader_identity = lambda _config: (
            self.owner_uid, self.owner_gid, "sha256:" + "2" * 64)
        module._process_identity = lambda pid: {
            "pid": pid,
            "uid": self.owner_uid if pid == self.owner_pid
            else module.ROOT_UID,
            "gid": self.owner_gid if pid == self.owner_pid
            else module.ROOT_GID,
            "start_ticks": 9001 if pid == self.owner_pid else 7001,
            "boot_id": self.boot_id,
        }
        module._process_matches = lambda *args: True
        if active_authority:
            self._write_active_authority()

    def _write_sessionctl(self, outcomes):
        source = """#!/usr/bin/env python3
import json
from pathlib import Path
import sys
count_path = Path(%r)
log_path = Path(%r)
outcomes = %r
count = int(count_path.read_text()) if count_path.exists() else 0
count_path.write_text(str(count + 1))
with log_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
outcome = outcomes[min(count, len(outcomes) - 1)]
generation = int(sys.argv[sys.argv.index("--generation") + 1])
if outcome == "accepted":
    print(json.dumps({"accepted": True, "reason_code": "OK",
                      "lease_generation": generation},
                     sort_keys=True, separators=(",", ":")))
    raise SystemExit(0)
if outcome == "not-found":
    print(json.dumps({"accepted": False,
                      "reason_code": "SESSION_LEASE_NOT_FOUND",
                      "lease_generation": generation},
                     sort_keys=True, separators=(",", ":")))
    raise SystemExit(4)
if outcome == "not-found-wrong-generation":
    print(json.dumps({"accepted": False,
                      "reason_code": "SESSION_LEASE_NOT_FOUND",
                      "lease_generation": generation + 1},
                     sort_keys=True, separators=(",", ":")))
    raise SystemExit(4)
print("transport failure", file=sys.stderr)
raise SystemExit(1)
""" % (
            str(self.session_count),
            str(self.session_log),
            list(outcomes),
        )
        self.sessionctl.write_text(source, encoding="utf-8")
        self.sessionctl.chmod(0o755)

    def _write_active_authority(self):
        token = self.runtime / self.module.TOKEN_NAME
        fence = self.runtime / self.module.FENCE_TOKEN_NAME
        token.write_bytes(self.token_bytes)
        fence.write_bytes(self.token_bytes)
        token.chmod(0o600)
        fence.chmod(0o600)
        now = int(time.time_ns() // 1_000_000)
        body = {
            "schema": "hepta.shadow-watch-lease-receipt.v1",
            "version": 1,
            "domain_id": "custtest",
            "agent_id": "custtest",
            "agent_uid": self.agent_uid,
            "boundary": "WATCH",
            "operation": "PROVISION",
            "lease_generation": self.generation,
            "previous_lease_generation": None,
            "previous_receipt_body_sha256": None,
            "accepted": True,
            "reason_code": "OK",
            "accepted_at_ms": now - 1_000,
            "ttl_seconds": 3600,
            "expires_at_ms": now - 1_000 + 3_600_000,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
        }
        receipt = self.module._body_document(body)
        receipt_path = self.runtime / self.module.LEASE_RECEIPT_NAME
        receipt_path.write_bytes(self.module._canonical(receipt))
        receipt_path.chmod(0o440)

    def publish_rotation(
        self,
        *,
        previous_generation=None,
        previous_digest=None,
        token_bytes=None,
        fence_bytes=None,
    ):
        receipt_path = self.runtime / self.module.LEASE_RECEIPT_NAME
        previous = json.loads(receipt_path.read_text(encoding="ascii"))
        new_generation = self.generation + 1
        replacement = (
            token_bytes or
            b"custodian-rotated-token-" + bytes([new_generation]) * 32)
        replacement_fence = fence_bytes or replacement
        token = self.runtime / self.module.TOKEN_NAME
        fence = self.runtime / self.module.FENCE_TOKEN_NAME
        token.chmod(0o600)
        fence.chmod(0o600)
        token.write_bytes(replacement)
        fence.write_bytes(replacement_fence)
        token.chmod(0o600)
        fence.chmod(0o600)
        now = int(time.time_ns() // 1_000_000)
        body = {
            "schema": "hepta.shadow-watch-lease-receipt.v1",
            "version": 1,
            "domain_id": "custtest",
            "agent_id": "custtest",
            "agent_uid": self.agent_uid,
            "boundary": "WATCH",
            "operation": "ROTATE",
            "lease_generation": new_generation,
            "previous_lease_generation": (
                self.generation if previous_generation is None
                else previous_generation),
            "previous_receipt_body_sha256": (
                previous["body_sha256"] if previous_digest is None
                else previous_digest),
            "accepted": True,
            "reason_code": "OK",
            "accepted_at_ms": now - 500,
            "ttl_seconds": 3600,
            "expires_at_ms": now - 500 + 3_600_000,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
        }
        receipt = self.module._body_document(body)
        receipt_path.chmod(0o600)
        receipt_path.write_bytes(self.module._canonical(receipt))
        receipt_path.chmod(0o440)
        self.generation = new_generation
        self.token_bytes = replacement
        return receipt

    def publish_rotation_crash_stage(self, stage):
        new_generation = self.generation + 1
        replacement = (
            b"custodian-residue-token-" +
            bytes([new_generation]) * 32)
        temporary_token = (
            self.runtime /
            ".session-token-rotate-7777-aaaaaaaaaaaaaaaa")
        temporary_fence = (
            self.runtime /
            ".session-fence-rotate-7777-bbbbbbbbbbbbbbbb")
        token = self.runtime / self.module.TOKEN_NAME
        fence = self.runtime / self.module.FENCE_TOKEN_NAME
        if stage in {"accepted-before-fixed", "fence-before-token"}:
            temporary_token.write_bytes(replacement)
            temporary_token.chmod(0o600)
        if stage == "accepted-before-fixed":
            temporary_fence.write_bytes(replacement)
            temporary_fence.chmod(0o600)
        elif stage == "fence-before-token":
            fence.chmod(0o600)
            fence.write_bytes(replacement)
            fence.chmod(0o600)
        elif stage in {"fixed-before-receipt", "fixed-missing-receipt"}:
            token.chmod(0o600)
            fence.chmod(0o600)
            token.write_bytes(replacement)
            fence.write_bytes(replacement)
            token.chmod(0o600)
            fence.chmod(0o600)
            if stage == "fixed-missing-receipt":
                (self.runtime /
                 self.module.LEASE_RECEIPT_NAME).unlink()
        else:
            raise AssertionError("unsupported rotation crash stage")
        self.generation = new_generation
        self.token_bytes = replacement

    def register(self):
        return self.module.register(
            self.config_path,
            "campaign-custodian-test",
            self.owner_pid,
            self.owner_uid,
            self.generation,
        )

    def publish_private_snapshot(self, generated_at_ms: int) -> Path:
        state_directory = (
            self.watch_state_root / "hepta-shadow-watch-custtest")
        private_directory = state_directory / "private"
        state_directory.mkdir(mode=0o700, exist_ok=True)
        private_directory.mkdir(mode=0o700, exist_ok=True)
        body = {
            "schema": "hepta.shadow-watch-snapshot.v2",
            "version": 2,
            "domain_id": "custtest",
            "agent_uid": self.agent_uid,
            "collection_started_at_ms": generated_at_ms,
            "collection_finished_at_ms": generated_at_ms,
            "read_finished_at_ms": {},
            "generated_at_ms": generated_at_ms,
            "instrument": "EUR.USD",
            "catalog_sha256": "sha256:" + "3" * 64,
            "descriptor_sha256": {},
            "reads": {},
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }
        snapshot = self.module._body_document(body)
        path = private_directory / "snapshot.json"
        path.write_bytes(self.module._canonical(snapshot))
        path.chmod(0o600)
        return path

    @property
    def domain_state(self):
        return self.state / "custtest"

    @property
    def closure_path(self):
        return (
            self.domain_state / self.module.CLOSURES_NAME /
            "campaign-custodian-test.json")

    def closure(self):
        return json.loads(self.closure_path.read_text(encoding="ascii"))

    def restore(self):
        for name, value in self.saved.items():
            setattr(self.module, name, value)


class HeptaShadowWatchCustodianTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.temporary = tempfile.TemporaryDirectory(
            prefix="hepta-watch-custodian-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def fixture(self, *, session_outcomes=None, active_authority=True):
        fixture = CustodianFixture(
            self.module, self.root,
            session_outcomes=session_outcomes,
            active_authority=active_authority)
        self.addCleanup(fixture.restore)
        return fixture

    def install_atomic_export(
            self,
            fixture: CustodianFixture,
            state: dict[str, object],
    ) -> tuple[Path, dict[str, object]]:
        export = (
            fixture.export_root /
            f"hepta-shadow-watch-export-{fixture.config['domain_id']}")
        generation_name = "generation-00000000000000000001-testfixture"
        generation = (
            export / self.module.EXPORT_GENERATIONS_NAME / generation_name)
        generation.mkdir(mode=0o750, parents=True)
        export.chmod(0o750)
        (export / self.module.EXPORT_GENERATIONS_NAME).chmod(0o750)
        generation.chmod(0o750)
        payloads = (b'{"snapshot":true}\n', b'{"lease":true}\n', b'{"export":true}\n')
        for name, payload in zip(
                self.module.EXPORT_FILES, payloads, strict=True):
            path = generation / name
            path.write_bytes(payload)
            path.chmod(0o440)
        now_ms = self.module._now_ms()
        body = {
            "schema": "hepta.shadow-watch-export-commit.v1",
            "version": 1,
            "authority_status": "ACTIVE",
            "authority_changed_at_ms": now_ms,
            "close_reason": None,
            "commit_sequence": 1,
            "generation": generation_name,
            "domain_id": fixture.config["domain_id"],
            "agent_uid": fixture.config["agent_uid"],
            "reader_uid": state["owner_uid"],
            "reader_gid": state["owner_gid"],
            "lease_generation": state["lease_generation"],
            "snapshot_body_sha256": self.module._digest(b"snapshot-body"),
            "snapshot_file_sha256": self.module._digest(payloads[0]),
            "lease_receipt_body_sha256":
                state["lease_receipt_body_sha256"],
            "lease_receipt_file_sha256": self.module._digest(payloads[1]),
            "export_receipt_body_sha256": self.module._digest(b"export-body"),
            "export_receipt_file_sha256": self.module._digest(payloads[2]),
            "committed_at_ms": now_ms,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }
        commit = self.module._body_document(body)
        current = export / self.module.EXPORT_COMMIT_NAME
        current.write_bytes(self.module._canonical(commit))
        current.chmod(0o440)
        return export, commit

    @staticmethod
    def process_stat(
            pid: int, *, state: str = "S", start_ticks: int = 9001,
            user_ticks: int = 0, resident_pages: int = 1) -> str:
        fields = [
            state,
            "1", "1", "1", "0", "-1", "4194304",
            "0", "0", "0", "0",
            str(user_ticks), "0", "0", "0",
            "20", "0", "1", "0", str(start_ticks),
            "0", str(resident_pages),
        ]
        return f"{pid} (p1 shadow reader) " + " ".join(fields) + "\n"

    def process_read_side_effect(
            self, before: str, after: str, *,
            missing: bool = False):
        status = (
            "Name:\tp1-shadow\n"
            "Uid:\t1000\t1000\t1000\t1000\n"
            "Gid:\t1000\t1000\t1000\t1000\n"
        )
        stat_reads = iter((before, after))
        stat_read_count = 0

        def read_text(path, *, encoding, errors):
            nonlocal stat_read_count
            del encoding, errors
            value = str(path)
            if value == "/proc/4242/stat":
                stat_read_count += 1
                if missing and stat_read_count == 2:
                    raise FileNotFoundError(value)
                return next(stat_reads)
            if value == "/proc/4242/status":
                return status
            if value == "/proc/sys/kernel/random/boot_id":
                return "11111111-2222-3333-4444-555555555555\n"
            raise AssertionError(f"unexpected path: {value}")

        return read_text

    def test_process_identity_allows_dynamic_stat_changes(self):
        before = self.process_stat(
            4242, start_ticks=9001, user_ticks=10, resident_pages=100)
        after = self.process_stat(
            4242, start_ticks=9001, user_ticks=11, resident_pages=101)
        with mock.patch.object(
                self.module.Path, "read_text", autospec=True,
                side_effect=self.process_read_side_effect(before, after)):
            self.assertTrue(self.module._process_matches(
                4242, 1000, 1000, 9001,
                "11111111-2222-3333-4444-555555555555"))

    def test_process_identity_rejects_reuse_disappearance_and_dead_state(self):
        cases = {
            "starttime-changed": (
                self.process_stat(4242, start_ticks=9001),
                self.process_stat(4242, start_ticks=9002),
                False,
            ),
            "zombie-before": (
                self.process_stat(4242, state="Z"),
                self.process_stat(4242),
                False,
            ),
            "dead-after": (
                self.process_stat(4242),
                self.process_stat(4242, state="X"),
                False,
            ),
            "missing": (
                self.process_stat(4242),
                self.process_stat(4242),
                True,
            ),
        }
        for label, (before, after, missing) in cases.items():
            with self.subTest(case=label):
                with mock.patch.object(
                        self.module.Path, "read_text", autospec=True,
                        side_effect=self.process_read_side_effect(
                            before, after, missing=missing)):
                    self.assertFalse(self.module._process_matches(
                        4242, 1000, 1000, 9001,
                        "11111111-2222-3333-4444-555555555555"))

    def test_registration_is_watch_only_and_contains_no_bearer(self):
        fixture = self.fixture()
        result = fixture.register()
        self.assertEqual(result["status"], "REGISTERED")
        watch_state = (
            fixture.watch_state_root / "hepta-shadow-watch-custtest")
        watch_private = watch_state / "private"
        for directory in (watch_state, watch_private):
            metadata = directory.lstat()
            self.assertTrue(stat.S_ISDIR(metadata.st_mode))
            self.assertEqual(
                (metadata.st_uid, metadata.st_gid),
                (fixture.agent_uid, fixture.agent_gid),
            )
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)
        self.assertEqual(list(watch_private.iterdir()), [])
        state = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.assertEqual(state["phase"], "ACTIVE")
        self.assertFalse(state["paper_authorized"])
        self.assertFalse(state["live_authorized"])
        self.assertFalse(state["mutation_authorized"])
        state_bytes = self.module._canonical(state)
        self.assertNotIn(fixture.token_bytes, state_bytes)
        durable_files = {
            path.relative_to(fixture.domain_state).as_posix()
            for path in fixture.domain_state.rglob("*") if path.is_file()
        }
        self.assertEqual(
            durable_files,
            {self.module.LOCK_NAME, self.module.TRANSACTION_NAME})
        self.assertEqual(
            (fixture.runtime / self.module.FENCE_TOKEN_NAME).read_bytes(),
            fixture.token_bytes)
        parser = self.module._parser()
        subcommands = next(
            action for action in parser._actions
            if action.__class__.__name__ == "_SubParsersAction")
        self.assertEqual(
            set(subcommands.choices),
            {
                "provision", "rotate",
                "supervise", "reconcile", "close",
            })

    def test_owner_sigkill_or_pid_reuse_closes_exact_generation(self):
        fixture = self.fixture()
        fixture.register()
        self.module._process_matches = lambda *args: False
        result = self.module.reconcile(fixture.config_path)
        self.assertEqual(result["close_reason"], "owner-dead")
        self.assertEqual(result["authoritative_revoke_outcome"], "ACCEPTED")
        self.assertTrue(result["local_authority_removed"])
        self.assertTrue(result["export_evidence_removed"])
        self.assertFalse((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).exists())
        for name in (
                self.module.TOKEN_NAME,
                self.module.FENCE_TOKEN_NAME,
                self.module.LEASE_RECEIPT_NAME):
            self.assertFalse((fixture.runtime / name).exists())
        arguments = json.loads(
            fixture.session_log.read_text(encoding="utf-8").splitlines()[0])
        self.assertIn("revoke", arguments)
        self.assertEqual(
            arguments[arguments.index("--generation") + 1],
            str(fixture.generation))
        self.assertNotIn("provision", arguments)
        self.assertNotIn("rotate", arguments)
        closure_bytes = fixture.closure_path.read_bytes()
        self.assertNotIn(fixture.token_bytes, closure_bytes)

    def test_close_removes_only_snapshot_bound_to_active_transaction(self):
        fixture = self.fixture()
        fixture.register()
        transaction = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        snapshot = fixture.publish_private_snapshot(
            int(transaction["registered_at_ms"]))
        self.module._process_matches = lambda *args: False
        closure = self.module.reconcile(fixture.config_path)
        self.assertEqual(closure["close_reason"], "owner-dead")
        self.assertFalse(snapshot.exists())

    def test_legacy_closed_snapshot_is_reconciled_without_transaction(self):
        fixture = self.fixture()
        fixture.register()
        self.module._process_matches = lambda *args: False
        closure = self.module.reconcile(fixture.config_path)
        snapshot = fixture.publish_private_snapshot(
            int(closure["close_started_at_ms"]))
        result = self.module.reconcile(fixture.config_path)
        self.assertEqual(result["status"], "NO_ACTIVE_TRANSACTION")
        self.assertFalse(snapshot.exists())

    def test_post_closure_snapshot_is_not_misattributed_to_old_campaign(self):
        fixture = self.fixture()
        fixture.register()
        self.module._process_matches = lambda *args: False
        closure = self.module.reconcile(fixture.config_path)
        snapshot = fixture.publish_private_snapshot(
            int(closure["close_started_at_ms"]) + 1)
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_PRIVATE_SNAPSHOT_UNBOUND"):
            self.module.reconcile(fixture.config_path)
        self.assertTrue(snapshot.exists())

    def test_unbound_or_unknown_private_snapshot_residue_fails_closed(self):
        fixture = self.fixture()
        snapshot = fixture.publish_private_snapshot(self.module._now_ms())
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_PRIVATE_SNAPSHOT_UNBOUND"):
            self.module.reconcile(fixture.config_path)
        self.assertTrue(snapshot.exists())

        unknown = snapshot.parent / "unknown"
        unknown.write_text("unsafe\n", encoding="ascii")
        unknown.chmod(0o600)
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_PRIVATE_INVENTORY_UNSAFE"):
            self.module.reconcile(fixture.config_path)
        self.assertTrue(snapshot.exists())
        self.assertTrue(unknown.exists())

    def test_private_snapshot_symlink_and_hardlink_are_never_removed(self):
        fixture = self.fixture()
        snapshot = fixture.publish_private_snapshot(self.module._now_ms())
        contents = snapshot.read_bytes()
        outside = fixture.root / "outside-snapshot.json"
        outside.write_bytes(contents)
        outside.chmod(0o600)
        snapshot.unlink()

        snapshot.symlink_to(outside)
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_FILE_METADATA_UNSAFE"):
            with self.module._opened_private_snapshot(
                    "custtest",
                    {(fixture.agent_uid, fixture.agent_gid)}):
                pass
        self.assertTrue(snapshot.is_symlink())
        self.assertEqual(outside.read_bytes(), contents)

        snapshot.unlink()
        os.link(outside, snapshot)
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_FILE_METADATA_UNSAFE"):
            with self.module._opened_private_snapshot(
                    "custtest",
                    {(fixture.agent_uid, fixture.agent_gid)}):
                pass
        self.assertTrue(snapshot.exists())
        self.assertEqual(outside.read_bytes(), contents)

    def test_private_directory_swap_cannot_redirect_snapshot_unlink(self):
        fixture = self.fixture()
        snapshot = fixture.publish_private_snapshot(self.module._now_ms())
        state_directory = snapshot.parent.parent
        original_private = snapshot.parent
        displaced_private = state_directory / "private-original"

        with self.module._opened_private_snapshot(
                "custtest",
                {(fixture.agent_uid, fixture.agent_gid)}) as candidate:
            self.assertIsNotNone(candidate)
            (state_fd, private_fd, metadata, contents, _document,
             agent_uid, agent_gid) = candidate
            original_private.rename(displaced_private)
            replacement_private = state_directory / "private"
            replacement_private.mkdir(mode=0o700)
            replacement = replacement_private / "snapshot.json"
            replacement.write_bytes(contents)
            replacement.chmod(0o600)
            with self.assertRaisesRegex(
                    self.module.CustodianError,
                    "CUSTODIAN_PRIVATE_SNAPSHOT_CHANGED"):
                self.module._remove_opened_private_snapshot(
                    state_fd,
                    private_fd,
                    metadata,
                    agent_uid=agent_uid,
                    agent_gid=agent_gid,
                )

        self.assertTrue((displaced_private / "snapshot.json").exists())
        self.assertTrue((state_directory / "private/snapshot.json").exists())

    def test_private_directory_swap_at_unlink_stays_on_opened_directory(self):
        fixture = self.fixture()
        snapshot = fixture.publish_private_snapshot(self.module._now_ms())
        state_directory = snapshot.parent.parent
        original_private = snapshot.parent
        displaced_private = state_directory / "private-original"
        replacement_contents = b"replacement-must-not-be-removed\n"
        original_unlink = os.unlink

        with self.module._opened_private_snapshot(
                "custtest",
                {(fixture.agent_uid, fixture.agent_gid)}) as candidate:
            self.assertIsNotNone(candidate)
            (state_fd, private_fd, metadata, _contents, _document,
             agent_uid, agent_gid) = candidate

            def swap_then_unlink(path, *, dir_fd=None):
                self.assertEqual(path, "snapshot.json")
                self.assertEqual(dir_fd, private_fd)
                original_private.rename(displaced_private)
                replacement_private = state_directory / "private"
                replacement_private.mkdir(mode=0o700)
                replacement = replacement_private / "snapshot.json"
                replacement.write_bytes(replacement_contents)
                replacement.chmod(0o600)
                return original_unlink(path, dir_fd=dir_fd)

            with mock.patch.object(
                    self.module.os, "unlink",
                    side_effect=swap_then_unlink):
                with self.assertRaisesRegex(
                        self.module.CustodianError,
                        "CUSTODIAN_PRIVATE_DIRECTORY_CHANGED"):
                    self.module._remove_opened_private_snapshot(
                        state_fd,
                        private_fd,
                        metadata,
                        agent_uid=agent_uid,
                        agent_gid=agent_gid,
                    )

        self.assertFalse((displaced_private / "snapshot.json").exists())
        self.assertEqual(
            (state_directory / "private/snapshot.json").read_bytes(),
            replacement_contents,
        )

    def test_ambiguous_closed_snapshot_binding_fails_closed(self):
        fixture = self.fixture()
        fixture.register()
        self.module._process_matches = lambda *args: False
        closure = self.module.reconcile(fixture.config_path)
        snapshot = fixture.publish_private_snapshot(
            int(closure["close_started_at_ms"]))
        duplicate_body = dict(closure)
        duplicate_body.pop("body_sha256")
        duplicate_body["campaign_id"] = "campaign-custodian-duplicate"
        duplicate = self.module._body_document(duplicate_body)
        self.module._validate_closure(duplicate)
        duplicate_path = (
            fixture.domain_state / self.module.CLOSURES_NAME /
            "campaign-custodian-duplicate.json")
        duplicate_path.write_bytes(self.module._canonical(duplicate))
        duplicate_path.chmod(0o600)

        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_PRIVATE_SNAPSHOT_UNBOUND"):
            self.module.reconcile(fixture.config_path)
        self.assertTrue(snapshot.exists())

    def test_crash_after_private_cleanup_reconciles_without_second_revoke(self):
        fixture = self.fixture(session_outcomes=["accepted"])
        fixture.register()
        transaction = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        snapshot = fixture.publish_private_snapshot(
            int(transaction["registered_at_ms"]))
        self.module._process_matches = lambda *args: False

        def crash(stage):
            if stage == "close.after_private_snapshot_cleanup":
                raise self.module.CustodianError("FAULT_AFTER_PRIVATE_CLEANUP")

        self.module._fault = crash
        with self.assertRaisesRegex(
                self.module.CustodianError, "FAULT_AFTER_PRIVATE_CLEANUP"):
            self.module.reconcile(fixture.config_path)
        self.assertFalse(snapshot.exists())
        self.assertEqual(
            json.loads((
                fixture.domain_state /
                self.module.TRANSACTION_NAME).read_text(
                    encoding="ascii"))["phase"],
            "CLEANING",
        )
        self.module._fault = lambda _stage: None
        closure = self.module.reconcile(fixture.config_path)
        self.assertEqual(closure["authoritative_revoke_outcome"], "ACCEPTED")
        self.assertEqual(
            fixture.session_count.read_text(encoding="ascii"), "1")

    def test_registration_crash_is_recovered_and_closed(self):
        fixture = self.fixture()

        def crash(stage):
            if stage == "register.after_preparing_publish":
                raise self.module.CustodianError("FAULT_INJECTED")

        self.module._fault = crash
        with self.assertRaisesRegex(
                self.module.CustodianError, "FAULT_INJECTED"):
            fixture.register()
        state = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.assertEqual(state["phase"], "PREPARING")
        self.module._fault = lambda _stage: None
        result = self.module.reconcile(fixture.config_path)
        self.assertEqual(result["close_reason"], "registration-recovery")
        self.assertTrue(result["local_authority_removed"])

    def test_production_provision_wrapper_persists_before_bootstrap(self):
        fixture = self.fixture(active_authority=False)
        observed_state = {}

        def bootstrap(_config_path, arguments):
            state_path = (
                fixture.domain_state / self.module.TRANSACTION_NAME)
            observed_state.update(json.loads(
                state_path.read_text(encoding="ascii")))
            private = (
                fixture.watch_state_root /
                "hepta-shadow-watch-custtest/private")
            self.assertTrue(private.is_dir())
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)
            self.assertEqual(list(private.iterdir()), [])
            self.assertEqual(
                arguments,
                [
                    "provision-watch", "--agent-id", "custtest",
                    "--session-id", "campaign-custodian-test",
                    "--ttl-sec", "3600",
                ])
            fixture._write_active_authority()
            return {
                "schema": "hepta.agent-session-bootstrap.v1",
                "accepted": True,
                "operation": "provision-watch",
                "trust_domain": "custtest",
                "peer_uid": fixture.agent_uid,
                "lease_generation": 1,
                "paper_authorized": False,
                "live_authorized": False,
            }

        self.module._invoke_bootstrap = bootstrap
        result = self.module.provision(
            fixture.config_path,
            "campaign-custodian-test",
            fixture.owner_pid,
            fixture.owner_uid,
            3600,
        )
        self.assertEqual(
            observed_state["phase"], "PROVISION_PREPARING")
        self.assertEqual(result["status"], "REGISTERED")
        active = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.assertEqual(active["phase"], "ACTIVE")
        self.assertIsNone(active["provision_ttl_seconds"])
        self.assertNotIn(
            fixture.token_bytes,
            (fixture.domain_state /
             self.module.TRANSACTION_NAME).read_bytes())

    def test_provision_creates_only_missing_root_owned_session_directory(self):
        fixture = self.fixture(active_authority=False)
        fixture.runtime.rmdir()
        fixture.root.chmod(0o711)

        def bootstrap(_config_path, _arguments):
            self.assertTrue(fixture.runtime.is_dir())
            metadata = fixture.runtime.stat()
            self.assertEqual(
                (metadata.st_uid, metadata.st_gid), (
                    self.module.ROOT_UID, self.module.ROOT_GID))
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o711)
            fixture._write_active_authority()
            return {
                "schema": "hepta.agent-session-bootstrap.v1",
                "accepted": True,
                "operation": "provision-watch",
                "trust_domain": "custtest",
                "peer_uid": fixture.agent_uid,
                "lease_generation": 1,
                "paper_authorized": False,
                "live_authorized": False,
            }

        self.module._invoke_bootstrap = bootstrap
        previous_umask = os.umask(0o077)
        try:
            result = self.module.provision(
                fixture.config_path,
                "campaign-custodian-test",
                fixture.owner_pid,
                fixture.owner_uid,
                3600,
            )
        finally:
            os.umask(previous_umask)
        self.assertEqual(result["status"], "REGISTERED")

    def test_safe_directory_never_repairs_preexisting_unsafe_metadata(self):
        target = self.root / "preexisting"
        target.mkdir(mode=0o700)
        target.chmod(0o700)
        with (
                mock.patch.object(
                    self.module, "ROOT_UID", os.geteuid()),
                mock.patch.object(
                    self.module, "ROOT_GID", os.getegid()),
                self.assertRaisesRegex(
                    self.module.CustodianError,
                    "CUSTODIAN_STATE_DIRECTORY_UNSAFE")):
            self.module._safe_directory(target, mode=0o711, create=True)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_safe_directory_rejects_created_name_rebind(self):
        target = self.root / "created"
        displaced = self.root / "displaced"
        real_fchmod = os.fchmod

        def rebind_after_fchmod(descriptor, mode):
            real_fchmod(descriptor, mode)
            target.rename(displaced)
            target.mkdir(mode=mode)
            target.chmod(mode)

        with (
                mock.patch.object(
                    self.module, "ROOT_UID", os.geteuid()),
                mock.patch.object(
                    self.module, "ROOT_GID", os.getegid()),
                mock.patch.object(
                    self.module.os, "fchmod",
                    side_effect=rebind_after_fchmod),
                self.assertRaisesRegex(
                    self.module.CustodianError,
                    "CUSTODIAN_STATE_DIRECTORY_CHANGED")):
            self.module._safe_directory(target, mode=0o711, create=True)
        self.assertNotEqual(target.stat().st_ino, displaced.stat().st_ino)

    def test_provision_wrapper_crashes_recover_before_and_after_bootstrap(self):
        for fault_stage in (
                "provision.before_bootstrap",
                "provision.after_bootstrap"):
            with self.subTest(stage=fault_stage):
                module = load_module()
                subroot = self.root / fault_stage.replace(".", "-")
                subroot.mkdir()
                fixture = CustodianFixture(
                    module, subroot, active_authority=False)
                self.addCleanup(fixture.restore)

                def bootstrap(_config_path, _arguments):
                    fixture._write_active_authority()
                    return {
                        "schema": "hepta.agent-session-bootstrap.v1",
                        "accepted": True,
                        "operation": "provision-watch",
                        "trust_domain": "custtest",
                        "peer_uid": fixture.agent_uid,
                        "lease_generation": 1,
                        "paper_authorized": False,
                        "live_authorized": False,
                    }

                module._invoke_bootstrap = bootstrap

                def crash(stage, expected=fault_stage):
                    if stage == expected:
                        raise module.CustodianError(
                            "FAULT_PROVISION_WRAPPER")

                module._fault = crash
                with self.assertRaisesRegex(
                        module.CustodianError,
                        "FAULT_PROVISION_WRAPPER"):
                    module.provision(
                        fixture.config_path,
                        "campaign-custodian-test",
                        fixture.owner_pid,
                        fixture.owner_uid,
                        3600,
                    )
                state_path = (
                    fixture.domain_state / module.TRANSACTION_NAME)
                preparing = json.loads(
                    state_path.read_text(encoding="ascii"))
                self.assertEqual(
                    preparing["phase"], "PROVISION_PREPARING")
                module._fault = lambda _stage: None
                module._now_ms = lambda: (
                    int(preparing["registered_at_ms"]) +
                    module.PROVISION_HANDOFF_GRACE_MS + 1)
                if fault_stage == "provision.before_bootstrap":
                    closure = module.reconcile(fixture.config_path)
                    self.assertEqual(
                        closure["close_reason"],
                        "registration-recovery")
                    self.assertFalse(state_path.exists())
                else:
                    config, digest = module._load_config(
                        fixture.config_path)
                    supervisor = {
                        "pid": 7007,
                        "uid": module.ROOT_UID,
                        "gid": module.ROOT_GID,
                        "start_ticks": 7001,
                        "boot_id": fixture.boot_id,
                    }
                    with module._locked(fixture.domain_state):
                        status, terminal = module._reconcile_locked(
                            config, digest, fixture.domain_state,
                            supervisor_identity=supervisor)
                    self.assertFalse(terminal)
                    self.assertEqual(status["status"], "MONITORING")
                    active = json.loads(
                        state_path.read_text(encoding="ascii"))
                    self.assertEqual(active["phase"], "ACTIVE")

    def test_provision_accepted_residue_is_exactly_revoked(self):
        fixture = self.fixture(
            session_outcomes=["accepted"], active_authority=False)
        config, preparing = self.module._provision_preparing_state(
            fixture.config_path,
            "campaign-custodian-test",
            fixture.owner_pid,
            fixture.owner_uid,
            3600,
        )
        self.assertEqual(config["domain_id"], "custtest")
        token = (
            fixture.runtime /
            ".session-token-provision-7777-aaaaaaaaaaaaaaaa")
        fence = (
            fixture.runtime /
            ".session-fence-provision-7777-bbbbbbbbbbbbbbbb")
        candidate = b"provision-candidate-" + b"z" * 32
        for path in (token, fence):
            path.write_bytes(candidate)
            path.chmod(0o600)
        self.module._process_matches = lambda *args: False
        closure = self.module.reconcile(fixture.config_path)
        self.assertEqual(
            closure["close_reason"], "registration-recovery")
        call = json.loads(
            fixture.session_log.read_text(encoding="utf-8"))
        self.assertEqual(
            call[call.index("--generation") + 1], "1")
        self.assertFalse(token.exists())
        self.assertFalse(fence.exists())
        self.assertNotIn(candidate, self.module._canonical(preparing))

    def test_config_deletion_uses_frozen_recovery_paths(self):
        fixture = self.fixture()
        fixture.register()
        transaction = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        snapshot = fixture.publish_private_snapshot(
            int(transaction["registered_at_ms"]))

        def unavailable(_path):
            raise OSError("configuration deleted")

        self.module._load_config = unavailable
        closure = self.module.reconcile(fixture.config_path)
        self.assertEqual(
            closure["close_reason"], "configuration-drift")
        self.assertEqual(
            closure["authoritative_revoke_outcome"], "ACCEPTED")
        self.assertFalse((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).exists())
        self.assertFalse(snapshot.exists())
        for name in (
                self.module.TOKEN_NAME,
                self.module.FENCE_TOKEN_NAME,
                self.module.LEASE_RECEIPT_NAME):
            self.assertFalse((fixture.runtime / name).exists())

    def test_config_deletion_during_rotation_revokes_new_generation(self):
        fixture = self.fixture()
        fixture.register()
        self.module.prepare_rotation(
            fixture.config_path,
            "campaign-custodian-test",
            fixture.generation,
        )
        fixture.publish_rotation()

        def unavailable(_path):
            raise OSError("configuration deleted during handoff")

        self.module._load_config = unavailable
        closure = self.module.reconcile(fixture.config_path)
        self.assertEqual(closure["close_reason"], "configuration-drift")
        self.assertEqual(closure["lease_generation"], fixture.generation)
        self.assertEqual(
            closure["authoritative_revoke_outcome"], "ACCEPTED")
        arguments = json.loads(
            fixture.session_log.read_text(encoding="utf-8"))
        self.assertEqual(
            arguments[arguments.index("--generation") + 1],
            str(fixture.generation))

    def test_rotation_prepare_commit_updates_exact_generation(self):
        fixture = self.fixture()
        fixture.register()
        old_token = fixture.token_bytes
        prepared = self.module.prepare_rotation(
            fixture.config_path,
            "campaign-custodian-test",
            fixture.generation,
        )
        self.assertEqual(prepared["status"], "ROTATION_PREPARED")
        rotated_receipt = fixture.publish_rotation()
        new_token = fixture.token_bytes
        committed = self.module.commit_rotation(
            fixture.config_path,
            "campaign-custodian-test",
            fixture.generation,
        )
        self.assertEqual(committed["status"], "ROTATED")
        self.assertEqual(
            committed["previous_authority_outcome"], "ROTATED")
        state_path = fixture.domain_state / self.module.TRANSACTION_NAME
        state = json.loads(state_path.read_text(encoding="ascii"))
        self.assertEqual(state["phase"], "ACTIVE")
        self.assertEqual(state["lease_generation"], fixture.generation)
        self.assertEqual(
            state["lease_receipt_body_sha256"],
            rotated_receipt["body_sha256"])
        self.assertIsNone(state["rotation_expected_generation"])
        self.assertIsNone(state["rotation_started_at_ms"])
        self.assertNotIn(old_token, state_path.read_bytes())
        self.assertNotIn(new_token, state_path.read_bytes())
        monitoring = self.module.reconcile(fixture.config_path)
        self.assertEqual(monitoring["status"], "MONITORING")
        self.assertEqual(
            monitoring["lease_generation"], fixture.generation)

        self.module._process_matches = lambda *args: False
        closure = self.module.reconcile(fixture.config_path)
        self.assertEqual(
            closure["lease_generation"], fixture.generation)
        arguments = json.loads(
            fixture.session_log.read_text(
                encoding="utf-8").splitlines()[0])
        self.assertEqual(
            arguments[arguments.index("--generation") + 1],
            str(fixture.generation))

    def test_production_rotate_wrapper_is_the_single_handoff(self):
        fixture = self.fixture()
        observed_phase = []

        def bootstrap(_config_path, arguments):
            state = json.loads((
                fixture.domain_state /
                self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
            observed_phase.append(state["phase"])
            self.assertEqual(
                arguments,
                [
                    "rotate", "--generation", "1",
                    "--ttl-sec", "3600",
                ])
            fixture.publish_rotation()
            return {
                "schema": "hepta.agent-session-bootstrap.v1",
                "accepted": True,
                "operation": "rotate",
                "trust_domain": "custtest",
                "peer_uid": fixture.agent_uid,
                "lease_generation": 2,
                "paper_authorized": False,
                "live_authorized": False,
            }

        self.module._invoke_bootstrap = bootstrap
        fixture.register()
        result = self.module.rotate(
            fixture.config_path,
            "campaign-custodian-test",
            1,
            3600,
        )
        self.assertEqual(observed_phase, ["ROTATION_PREPARING"])
        self.assertEqual(result["status"], "ROTATED")
        self.assertEqual(result["lease_generation"], 2)
        state = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.assertEqual(state["phase"], "ACTIVE")
        self.assertEqual(state["lease_generation"], 2)

    def test_unstarted_rotation_times_out_back_to_old_active(self):
        fixture = self.fixture()
        fixture.register()
        self.module.prepare_rotation(
            fixture.config_path,
            "campaign-custodian-test",
            fixture.generation,
        )
        state = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.module._now_ms = lambda: (
            int(state["rotation_started_at_ms"]) +
            self.module.ROTATION_HANDOFF_GRACE_MS + 1)
        config, digest = self.module._load_config(fixture.config_path)
        supervisor = {
            "pid": 7007,
            "uid": self.module.ROOT_UID,
            "gid": self.module.ROOT_GID,
            "start_ticks": 7001,
            "boot_id": fixture.boot_id,
        }
        with self.module._locked(fixture.domain_state):
            status, terminal = self.module._reconcile_locked(
                config, digest, fixture.domain_state,
                supervisor_identity=supervisor)
        self.assertFalse(terminal)
        self.assertEqual(status["status"], "MONITORING")
        active = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.assertEqual(active["phase"], "ACTIVE")
        self.assertEqual(active["lease_generation"], 1)
        self.assertFalse(fixture.session_count.exists())

    def test_rotation_preparing_crash_preserves_old_authority(self):
        fixture = self.fixture()
        fixture.register()

        def crash(stage):
            if stage == "rotation.after_preparing_publish":
                raise self.module.CustodianError(
                    "FAULT_ROTATION_PREPARING")

        self.module._fault = crash
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "FAULT_ROTATION_PREPARING"):
            self.module.prepare_rotation(
                fixture.config_path,
                "campaign-custodian-test",
                fixture.generation,
            )
        state_path = fixture.domain_state / self.module.TRANSACTION_NAME
        state = json.loads(state_path.read_text(encoding="ascii"))
        self.assertEqual(state["phase"], "ROTATION_PREPARING")
        self.assertEqual(
            state["rotation_expected_generation"], fixture.generation + 1)
        self.assertEqual(
            (fixture.runtime / self.module.TOKEN_NAME).read_bytes(),
            fixture.token_bytes)
        self.module._fault = lambda _stage: None
        status = self.module.reconcile(fixture.config_path)
        self.assertEqual(status["status"], "ROTATION_PREPARED")

    def test_rotation_commit_crashes_recover_from_receipt_chain(self):
        for fault_stage in (
                "rotation.after_candidate_validation",
                "rotation.after_active_publish"):
            with self.subTest(stage=fault_stage):
                module = load_module()
                subroot = self.root / fault_stage.replace(".", "-")
                subroot.mkdir()
                fixture = CustodianFixture(module, subroot)
                self.addCleanup(fixture.restore)
                fixture.register()
                module.prepare_rotation(
                    fixture.config_path,
                    "campaign-custodian-test",
                    fixture.generation,
                )
                fixture.publish_rotation()

                def crash(stage, expected=fault_stage):
                    if stage == expected:
                        raise module.CustodianError(
                            "FAULT_ROTATION_COMMIT")

                module._fault = crash
                with self.assertRaisesRegex(
                        module.CustodianError,
                        "FAULT_ROTATION_COMMIT"):
                    module.commit_rotation(
                        fixture.config_path,
                        "campaign-custodian-test",
                        fixture.generation,
                    )
                state_path = (
                    fixture.domain_state / module.TRANSACTION_NAME)
                state = json.loads(
                    state_path.read_text(encoding="ascii"))
                expected_phase = (
                    "ROTATION_PREPARING"
                    if fault_stage.endswith("candidate_validation")
                    else "ACTIVE")
                self.assertEqual(state["phase"], expected_phase)
                module._fault = lambda _stage: None
                if expected_phase == "ROTATION_PREPARING":
                    module._now_ms = lambda: (
                        int(state["rotation_started_at_ms"]) +
                        module.ROTATION_HANDOFF_GRACE_MS + 1)
                config, digest = module._load_config(
                    fixture.config_path)
                supervisor = {
                    "pid": 7007,
                    "uid": module.ROOT_UID,
                    "gid": module.ROOT_GID,
                    "start_ticks": 7001,
                    "boot_id": fixture.boot_id,
                }
                with module._locked(fixture.domain_state):
                    monitoring, terminal = module._reconcile_locked(
                        config, digest, fixture.domain_state,
                        supervisor_identity=supervisor)
                self.assertFalse(terminal)
                self.assertEqual(monitoring["status"], "MONITORING")
                self.assertEqual(
                    monitoring["lease_generation"], fixture.generation)

    def test_rotation_wrong_chain_and_token_tamper_quarantine(self):
        cases = ("digest", "generation", "token")
        for case in cases:
            with self.subTest(case=case):
                module = load_module()
                subroot = self.root / ("rotation-" + case)
                subroot.mkdir()
                fixture = CustodianFixture(module, subroot)
                self.addCleanup(fixture.restore)
                fixture.register()
                module.prepare_rotation(
                    fixture.config_path,
                    "campaign-custodian-test",
                    fixture.generation,
                )
                if case == "digest":
                    fixture.publish_rotation(
                        previous_digest="sha256:" + "f" * 64)
                    reason = "CUSTODIAN_ROTATION_NOT_PUBLISHED"
                elif case == "generation":
                    fixture.publish_rotation(previous_generation=0)
                    reason = "CUSTODIAN_ROTATION_NOT_PUBLISHED"
                else:
                    fixture.publish_rotation(
                        fence_bytes=b"tampered-fence-" + b"z" * 32)
                    reason = "CUSTODIAN_ROTATION_NOT_PUBLISHED"
                with self.assertRaisesRegex(
                        module.CustodianError, reason):
                    module.commit_rotation(
                        fixture.config_path,
                        "campaign-custodian-test",
                        fixture.generation,
                    )
                state = json.loads((
                    fixture.domain_state /
                    module.TRANSACTION_NAME).read_text(encoding="ascii"))
                self.assertEqual(state["phase"], "CLEANING")
                token = fixture.runtime / module.TOKEN_NAME
                self.assertEqual(
                    stat.S_IMODE(token.stat().st_mode), 0o400)
                self.assertNotIn(
                    fixture.token_bytes,
                    (fixture.domain_state /
                     module.TRANSACTION_NAME).read_bytes())

    def test_rotation_rejects_campaign_generation_jump_and_duplicate(self):
        fixture = self.fixture()
        fixture.register()
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_ROTATION_OLD_TRANSACTION_MISMATCH"):
            self.module.prepare_rotation(
                fixture.config_path, "wrong-campaign",
                fixture.generation)
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_ROTATION_OLD_TRANSACTION_MISMATCH"):
            self.module.prepare_rotation(
                fixture.config_path, "campaign-custodian-test",
                fixture.generation + 1)
        self.module.prepare_rotation(
            fixture.config_path, "campaign-custodian-test",
            fixture.generation)
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_ROTATION_PHASE_INVALID"):
            self.module.prepare_rotation(
                fixture.config_path, "campaign-custodian-test",
                fixture.generation)
        fixture.publish_rotation()
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_ROTATION_NEW_TRANSACTION_MISMATCH"):
            self.module.commit_rotation(
                fixture.config_path, "campaign-custodian-test",
                fixture.generation + 1)

    def test_owner_death_after_external_rotation_closes_new_generation(self):
        fixture = self.fixture()
        fixture.register()
        self.module.prepare_rotation(
            fixture.config_path, "campaign-custodian-test",
            fixture.generation)
        fixture.publish_rotation()
        self.module._process_matches = lambda *args: False
        closure = self.module.reconcile(fixture.config_path)
        self.assertEqual(closure["close_reason"], "owner-dead")
        self.assertEqual(
            closure["lease_generation"], fixture.generation)
        arguments = json.loads(
            fixture.session_log.read_text(
                encoding="utf-8").splitlines()[0])
        self.assertEqual(
            arguments[arguments.index("--generation") + 1],
            str(fixture.generation))

    def test_close_during_unpublished_rotation_revokes_old_generation(self):
        fixture = self.fixture()
        fixture.register()
        old_generation = fixture.generation
        self.module.prepare_rotation(
            fixture.config_path, "campaign-custodian-test",
            old_generation)
        closure = self.module.close(
            fixture.config_path, "operator-request")
        self.assertEqual(closure["lease_generation"], old_generation)
        arguments = json.loads(
            fixture.session_log.read_text(
                encoding="utf-8").splitlines()[0])
        self.assertEqual(
            arguments[arguments.index("--generation") + 1],
            str(old_generation))

    def test_rotation_bootstrap_crash_residue_never_orphans_n_plus_one(self):
        cases = {
            "accepted-before-fixed": ["accepted", "not-found"],
            "fence-before-token": ["accepted"],
            "fixed-before-receipt": ["accepted"],
            "fixed-missing-receipt": ["accepted"],
        }
        for stage, outcomes in cases.items():
            with self.subTest(stage=stage):
                module = load_module()
                subroot = self.root / stage
                subroot.mkdir()
                fixture = CustodianFixture(
                    module, subroot, session_outcomes=outcomes)
                self.addCleanup(fixture.restore)
                fixture.register()
                old_generation = fixture.generation
                module.prepare_rotation(
                    fixture.config_path,
                    "campaign-custodian-test",
                    old_generation,
                )
                fixture.publish_rotation_crash_stage(stage)
                module._process_matches = lambda *args: False
                closure = module.reconcile(fixture.config_path)
                self.assertEqual(
                    closure["schema"],
                    "hepta.shadow-watch-custodian-closure.v1")
                calls = [
                    json.loads(line)
                    for line in fixture.session_log.read_text(
                        encoding="utf-8").splitlines()
                ]
                generations = [
                    int(call[call.index("--generation") + 1])
                    for call in calls
                ]
                self.assertEqual(generations[0], old_generation + 1)
                if stage == "accepted-before-fixed":
                    self.assertEqual(
                        generations, [old_generation + 1, old_generation])
                else:
                    self.assertEqual(generations, [old_generation + 1])
                remaining = {
                    path.name for path in fixture.runtime.iterdir()
                    if (
                        path.name.startswith(".session-token-") or
                        path.name.startswith(".session-fence-") or
                        path.name in {
                            module.TOKEN_NAME,
                            module.FENCE_TOKEN_NAME,
                            module.LEASE_RECEIPT_NAME,
                        }
                    )
                }
                self.assertEqual(remaining, set())
                self.assertFalse((
                    fixture.domain_state /
                    module.TRANSACTION_NAME).exists())

    def test_rotation_unknown_or_mixed_residue_fails_closed(self):
        fixture = self.fixture()
        fixture.register()
        self.module.prepare_rotation(
            fixture.config_path,
            "campaign-custodian-test",
            fixture.generation,
        )
        residue = (
            fixture.runtime /
            ".session-fence-provision-7777-aaaaaaaaaaaaaaaa")
        residue.write_bytes(b"unknown-residue-" + b"x" * 32)
        residue.chmod(0o600)
        state = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.module._now_ms = lambda: (
            int(state["rotation_started_at_ms"]) +
            self.module.ROTATION_HANDOFF_GRACE_MS + 1)
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_ROTATION_RESIDUE_INVENTORY_INVALID"):
            self.module.reconcile(fixture.config_path)
        self.assertFalse(fixture.session_count.exists())
        self.assertTrue(residue.exists())

    def test_crash_after_revoke_converges_from_durable_closing(self):
        fixture = self.fixture(
            session_outcomes=["accepted", "not-found"])
        fixture.register()
        self.module._process_matches = lambda *args: False

        def crash(stage):
            if stage == "close.after_revoke_before_commit":
                raise self.module.CustodianError("FAULT_AFTER_REVOKE")

        self.module._fault = crash
        with self.assertRaisesRegex(
                self.module.CustodianError, "FAULT_AFTER_REVOKE"):
            self.module.reconcile(fixture.config_path)
        state = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.assertEqual(state["phase"], "CLOSING")
        token = fixture.runtime / self.module.TOKEN_NAME
        self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o400)
        self.assertEqual(token.stat().st_uid, self.module.ROOT_UID)
        self.module._fault = lambda _stage: None
        result = self.module.reconcile(fixture.config_path)
        self.assertEqual(
            result["authoritative_revoke_outcome"], "ALREADY_ABSENT")
        self.assertEqual(
            fixture.session_count.read_text(encoding="ascii"), "2")

    def test_not_found_for_another_generation_is_not_absence_proof(self):
        fixture = self.fixture(
            session_outcomes=["not-found-wrong-generation"])
        fixture.register()
        self.module._process_matches = lambda *args: False
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_REVOKE_RESULT_INVALID"):
            self.module.reconcile(fixture.config_path)
        state = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.assertEqual(state["phase"], "CLOSING")
        self.assertFalse(fixture.closure_path.exists())
        self.assertTrue((
            fixture.runtime / self.module.FENCE_TOKEN_NAME).exists())

    def test_cleaning_commit_survives_crash_without_second_revoke(self):
        fixture = self.fixture(session_outcomes=["accepted"])
        fixture.register()
        self.module._process_matches = lambda *args: False

        def crash(stage):
            if stage == "close.after_authoritative_revoke":
                raise self.module.CustodianError("FAULT_AFTER_COMMIT")

        self.module._fault = crash
        with self.assertRaisesRegex(
                self.module.CustodianError, "FAULT_AFTER_COMMIT"):
            self.module.reconcile(fixture.config_path)
        state = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.assertEqual(state["phase"], "CLEANING")
        self.assertEqual(
            state["authoritative_revoke_outcome"], "ACCEPTED")
        self.module._fault = lambda _stage: None
        result = self.module.reconcile(fixture.config_path)
        self.assertEqual(
            result["authoritative_revoke_outcome"], "ACCEPTED")
        self.assertEqual(
            fixture.session_count.read_text(encoding="ascii"), "1")

    def test_crash_after_closure_publish_is_idempotent(self):
        fixture = self.fixture(session_outcomes=["accepted"])
        fixture.register()
        self.module._process_matches = lambda *args: False

        def crash(stage):
            if stage == "close.after_closure_publish":
                raise self.module.CustodianError("FAULT_AFTER_CLOSURE")

        self.module._fault = crash
        with self.assertRaisesRegex(
                self.module.CustodianError, "FAULT_AFTER_CLOSURE"):
            self.module.reconcile(fixture.config_path)
        original = fixture.closure_path.read_bytes()
        published = json.loads(original)
        snapshot = fixture.publish_private_snapshot(
            int(published["close_started_at_ms"]))
        self.module._fault = lambda _stage: None
        result = self.module.reconcile(fixture.config_path)
        self.assertEqual(
            self.module._canonical(result), original)
        self.assertFalse(snapshot.exists())
        self.assertEqual(
            fixture.session_count.read_text(encoding="ascii"), "1")

    def test_service_restart_closes_even_while_owner_is_alive(self):
        fixture = self.fixture()
        fixture.register()
        config, digest = self.module._load_config(fixture.config_path)
        directory = fixture.domain_state
        first = {
            "pid": 6001, "uid": self.module.ROOT_UID,
            "gid": self.module.ROOT_GID, "start_ticks": 11,
            "boot_id": fixture.boot_id,
        }
        second = {
            "pid": 6002, "uid": self.module.ROOT_UID,
            "gid": self.module.ROOT_GID, "start_ticks": 12,
            "boot_id": fixture.boot_id,
        }
        with self.module._locked(directory):
            status, terminal = self.module._reconcile_locked(
                config, digest, directory, supervisor_identity=first)
        self.assertFalse(terminal)
        self.assertEqual(status["status"], "MONITORING")
        with self.module._locked(directory):
            closure, terminal = self.module._reconcile_locked(
                config, digest, directory, supervisor_identity=second)
        self.assertTrue(terminal)
        self.assertEqual(closure["close_reason"], "custodian-restart")

    def test_expiry_closes_and_exact_export_inventory_is_removed(self):
        fixture = self.fixture()
        fixture.register()
        transaction = fixture.domain_state / self.module.TRANSACTION_NAME
        state = json.loads(transaction.read_text(encoding="ascii"))
        receipt_path = (
            fixture.runtime / self.module.LEASE_RECEIPT_NAME)
        receipt = json.loads(receipt_path.read_text(encoding="ascii"))
        receipt_body = dict(receipt)
        receipt_body.pop("body_sha256")
        receipt_body["accepted_at_ms"] = self.module._now_ms() - 3_700_000
        receipt_body["expires_at_ms"] = (
            receipt_body["accepted_at_ms"] +
            receipt_body["ttl_seconds"] * 1000)
        expired_receipt = self.module._body_document(receipt_body)
        receipt_path.chmod(0o600)
        receipt_path.write_bytes(self.module._canonical(expired_receipt))
        receipt_path.chmod(0o440)
        body = dict(state)
        body.pop("body_sha256")
        body["lease_expires_at_ms"] = expired_receipt["expires_at_ms"]
        body["lease_receipt_body_sha256"] = (
            expired_receipt["body_sha256"])
        expired = self.module._body_document(body)
        self.module._atomic_write(transaction, expired, replace=True)
        export = (
            fixture.export_root /
            f"hepta-shadow-watch-export-{fixture.config['domain_id']}")
        export.mkdir(mode=0o750)
        export.chmod(0o750)
        self.assertEqual(stat.S_IMODE(export.stat().st_mode), 0o750)
        for name in (
                "snapshot.json",
                "shadow-watch-lease-receipt.json",
                "shadow-watch-export-receipt.json"):
            path = export / name
            path.write_bytes(
                b"x" * 70_000 if name == "snapshot.json" else b"{}\n")
            path.chmod(0o440)
        result = self.module.reconcile(fixture.config_path)
        self.assertEqual(result["close_reason"], "lease-expired")
        self.assertFalse(export.exists())

    def test_atomic_export_cleanup_publishes_closing_then_closed(self):
        fixture = self.fixture()
        fixture.register()
        state = json.loads((
            fixture.domain_state / self.module.TRANSACTION_NAME
        ).read_text(encoding="ascii"))
        export, active = self.install_atomic_export(fixture, state)
        cleaning = {**state, "close_reason": "service-stop"}
        observed: list[str] = []
        real_atomic = self.module._atomic_export_commit

        def record(path, document, reader_gid):
            observed.append(document["authority_status"])
            return real_atomic(path, document, reader_gid)

        with mock.patch.object(
                self.module, "_atomic_export_commit", side_effect=record):
            self.assertTrue(
                self.module._cleanup_export_evidence(
                    fixture.config, cleaning))
        self.assertEqual(observed, ["CLOSING", "CLOSED"])
        self.assertTrue(export.is_dir())
        self.assertEqual(
            os.listdir(export), [self.module.EXPORT_COMMIT_NAME])
        closed = json.loads((
            export / self.module.EXPORT_COMMIT_NAME
        ).read_text(encoding="ascii"))
        self.assertEqual(closed["authority_status"], "CLOSED")
        self.assertEqual(closed["close_reason"], "service-stop")
        self.assertEqual(closed["commit_sequence"], 3)
        self.assertIsNone(closed["generation"])
        self.assertIsNone(closed["snapshot_file_sha256"])
        self.assertEqual(
            closed["lease_generation"], state["lease_generation"])
        self.assertEqual(
            closed["lease_receipt_body_sha256"],
            state["lease_receipt_body_sha256"])
        self.assertNotEqual(closed["body_sha256"], active["body_sha256"])
        self.module._validate_export_commit(
            closed, fixture.config, cleaning)

    def test_atomic_close_tombstone_binds_latest_rotated_authority(self):
        fixture = self.fixture()
        fixture.register()
        old_state = json.loads((
            fixture.domain_state / self.module.TRANSACTION_NAME
        ).read_text(encoding="ascii"))
        export, _active = self.install_atomic_export(fixture, old_state)
        cleaning = {
            **old_state,
            "lease_generation": old_state["lease_generation"] + 1,
            "lease_receipt_body_sha256":
                self.module._digest(b"latest-rotated-lease"),
            "close_reason": "service-stop",
        }
        self.assertTrue(
            self.module._cleanup_export_evidence(fixture.config, cleaning))
        closed = json.loads((
            export / self.module.EXPORT_COMMIT_NAME
        ).read_text(encoding="ascii"))
        self.assertEqual(closed["authority_status"], "CLOSED")
        self.assertEqual(
            closed["lease_generation"], cleaning["lease_generation"])
        self.assertEqual(
            closed["lease_receipt_body_sha256"],
            cleaning["lease_receipt_body_sha256"])
        self.module._validate_export_commit(
            closed, fixture.config, cleaning)

    def test_atomic_export_cleanup_crashes_recover_at_every_stage(self):
        for index, stage in enumerate((
                "close.after_export_closing",
                "close.after_export_generation_cleanup",
                "close.after_export_closed",
        )):
            with self.subTest(stage=stage):
                subroot = self.root / f"atomic-close-{index}"
                subroot.mkdir()
                fixture = CustodianFixture(self.module, subroot)
                self.addCleanup(fixture.restore)
                fixture.register()
                state = json.loads((
                    fixture.domain_state / self.module.TRANSACTION_NAME
                ).read_text(encoding="ascii"))
                export, _active = self.install_atomic_export(fixture, state)
                cleaning = {**state, "close_reason": "service-stop"}

                def fault(candidate: str) -> None:
                    if candidate == stage:
                        raise RuntimeError("crash")

                with mock.patch.object(
                        self.module, "_fault", side_effect=fault), \
                        self.assertRaisesRegex(RuntimeError, "crash"):
                    self.module._cleanup_export_evidence(
                        fixture.config, cleaning)
                self.assertTrue(export.exists())
                self.assertTrue(
                    self.module._cleanup_export_evidence(
                        fixture.config, cleaning))
                closed = json.loads((
                    export / self.module.EXPORT_COMMIT_NAME
                ).read_text(encoding="ascii"))
                self.assertEqual(closed["authority_status"], "CLOSED")
                self.assertEqual(
                    os.listdir(export), [self.module.EXPORT_COMMIT_NAME])

    def test_second_close_removes_closed_tombstone_for_launcher_cleanliness(
            self):
        fixture = self.fixture()
        fixture.register()
        state = json.loads((
            fixture.domain_state / self.module.TRANSACTION_NAME
        ).read_text(encoding="ascii"))
        export, _active = self.install_atomic_export(fixture, state)
        first = self.module.close(
            fixture.config_path, "service-stop")
        self.assertTrue(first["export_evidence_removed"])
        self.assertTrue(export.is_dir())
        closed = json.loads((
            export / self.module.EXPORT_COMMIT_NAME
        ).read_text(encoding="ascii"))
        self.assertEqual(closed["authority_status"], "CLOSED")
        self.assertEqual(
            os.listdir(export), [self.module.EXPORT_COMMIT_NAME])

        second = self.module.close(
            fixture.config_path, "service-stop")
        self.assertEqual(second["status"], "NO_ACTIVE_TRANSACTION")
        self.assertFalse(export.exists())

    def test_close_before_first_export_and_orphan_tombstone_reconcile_clean(
            self):
        fixture = self.fixture()
        fixture.register()
        export = (
            fixture.export_root /
            f"hepta-shadow-watch-export-{fixture.config['domain_id']}")
        self.assertFalse(export.exists())
        closure = self.module.close(
            fixture.config_path, "service-stop")
        self.assertTrue(closure["export_evidence_removed"])
        self.assertFalse(export.exists())
        reconciled = self.module.reconcile(fixture.config_path)
        self.assertEqual(
            reconciled["status"], "NO_ACTIVE_TRANSACTION")
        self.assertFalse(export.exists())

    def test_next_registration_removes_bound_closed_tombstone(self):
        fixture = self.fixture()
        fixture.register()
        state = json.loads((
            fixture.domain_state / self.module.TRANSACTION_NAME
        ).read_text(encoding="ascii"))
        export, _active = self.install_atomic_export(fixture, state)
        self.module.close(fixture.config_path, "service-stop")
        self.assertTrue(export.exists())

        fixture._write_active_authority()
        next_registration = self.module.register(
            fixture.config_path,
            "campaign-custodian-next",
            fixture.owner_pid,
            fixture.owner_uid,
            fixture.generation,
        )
        self.assertEqual(next_registration["status"], "REGISTERED")
        self.assertFalse(export.exists())

    def test_transport_failure_keeps_runtime_fence_and_quarantines_agent(self):
        fixture = self.fixture(session_outcomes=["transport"])
        fixture.register()
        self.module._process_matches = lambda *args: False
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_REVOKE_RESULT_INVALID"):
            self.module.reconcile(fixture.config_path)
        state = json.loads((
            fixture.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.assertEqual(state["phase"], "CLOSING")
        self.assertEqual(
            (fixture.runtime / self.module.FENCE_TOKEN_NAME).read_bytes(),
            fixture.token_bytes)
        self.assertNotIn(
            fixture.token_bytes,
            (fixture.domain_state /
             self.module.TRANSACTION_NAME).read_bytes())
        token = fixture.runtime / self.module.TOKEN_NAME
        self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o400)
        self.assertEqual(token.stat().st_uid, self.module.ROOT_UID)
        self.assertFalse(fixture.closure_path.exists())

    def test_reboot_token_loss_waits_for_expiry_without_persisting_bearer(self):
        fixture = self.fixture()
        fixture.register()
        state_path = (
            fixture.domain_state / self.module.TRANSACTION_NAME)
        active = json.loads(state_path.read_text(encoding="ascii"))
        for name in (
                self.module.TOKEN_NAME,
                self.module.FENCE_TOKEN_NAME,
                self.module.LEASE_RECEIPT_NAME):
            (fixture.runtime / name).unlink()
        self.module._process_matches = lambda *args: False
        pending = self.module.reconcile(fixture.config_path)
        self.assertEqual(pending["status"], "PENDING_EXPIRY")
        closing = json.loads(state_path.read_text(encoding="ascii"))
        self.assertEqual(closing["phase"], "CLOSING")
        self.assertFalse(fixture.session_count.exists())
        self.assertNotIn(fixture.token_bytes, state_path.read_bytes())

        fixture.runtime.rmdir()
        self.module._now_ms = lambda: (
            int(active["lease_expires_at_ms"]) + 1)
        closure = self.module.reconcile(fixture.config_path)
        self.assertEqual(
            closure["authoritative_revoke_outcome"], "EXPIRED")
        self.assertFalse(state_path.exists())
        self.assertFalse(fixture.session_count.exists())

    def test_tampered_state_and_unknown_export_entry_fail_closed(self):
        fixture = self.fixture()
        fixture.register()
        transaction = fixture.domain_state / self.module.TRANSACTION_NAME
        state = json.loads(transaction.read_text(encoding="ascii"))
        state["unknown"] = True
        self.module._atomic_write(transaction, state, replace=True)
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_TRANSACTION_CONTRACT_INVALID"):
            self.module.reconcile(fixture.config_path)

        fixture.restore()
        second_root = self.root / "second"
        second_root.mkdir()
        second = CustodianFixture(self.module, second_root)
        self.addCleanup(second.restore)
        second.register()
        export = (
            second.export_root /
            f"hepta-shadow-watch-export-{second.config['domain_id']}")
        export.mkdir(mode=0o750)
        export.chmod(0o750)
        self.assertEqual(stat.S_IMODE(export.stat().st_mode), 0o750)
        unexpected = export / "unexpected"
        unexpected.write_text("unsafe\n", encoding="ascii")
        unexpected.chmod(0o440)
        self.module._process_matches = lambda *args: False
        with self.assertRaisesRegex(
                self.module.CustodianError,
                "CUSTODIAN_EXPORT_INVENTORY_UNSAFE"):
            self.module.reconcile(second.config_path)
        state = json.loads((
            second.domain_state /
            self.module.TRANSACTION_NAME).read_text(encoding="ascii"))
        self.assertEqual(state["phase"], "CLEANING")
        self.assertFalse(second.closure_path.exists())

    def test_units_are_root_watch_only_and_have_restart_backstop(self):
        service = (
            REPOSITORY / "systemd/hepta-shadow-watch-custodian@.service"
        ).read_text(encoding="utf-8")
        reconcile = (
            REPOSITORY /
            "systemd/hepta-shadow-watch-custodian-reconcile@.service"
        ).read_text(encoding="utf-8")
        timer = (
            REPOSITORY /
            "systemd/hepta-shadow-watch-custodian-reconcile@.timer"
        ).read_text(encoding="utf-8")
        for required in (
                "User=root", "PrivateNetwork=yes",
                "RestrictAddressFamilies=AF_UNIX",
                "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER",
                "Restart=on-failure", "ExecStop=", "ExecStopPost=",
                "StateDirectory=hepta-shadow-watch-custodian",
                "ReadWritePaths=-/run/hepta-agent-%i/sessions",
                "ReadWritePaths=/var/lib/hepta-shadow-watch-%i/private"):
            self.assertIn(required, service)
        self.assertIn(
            "ReadWritePaths=-/run/hepta-agent-%i/sessions", reconcile)
        self.assertIn(
            "ReadWritePaths=-/var/lib/hepta-shadow-watch-%i/private",
            reconcile)
        self.assertIn(" reconcile", reconcile)
        self.assertIn("Persistent=true", timer)
        self.assertIn("WantedBy=timers.target", timer)
        combined = "\n".join((service, reconcile, timer))
        for forbidden in (
                "provision-watch", " rotate", " renew",
                "risk.preview", "trade.", "PAPER", "LIVE"):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
