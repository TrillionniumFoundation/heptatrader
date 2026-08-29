#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/hepta_p1_paper_kill_switch_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("kill_bootstrap", SOURCE)
assert SPEC is not None and SPEC.loader is not None
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BOOTSTRAP
SPEC.loader.exec_module(BOOTSTRAP)

BOOT_ID = "11111111-2222-4333-8444-555555555555"
SOURCE_SHA = "sha256:" + "a" * 64


class Crash(BaseException):
    pass


class StatProxy:
    def __init__(self, value: os.stat_result, **changes: int) -> None:
        self._value = value
        self._changes = changes

    def __getattr__(self, name: str) -> object:
        if name in self._changes:
            return self._changes[name]
        return getattr(self._value, name)


class Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.anchor = Path(self.temporary.name) / "run" / "hepta"
        self.anchor.mkdir(parents=True, mode=0o755)
        self.anchor.chmod(0o755)
        self.uid = os.geteuid()
        self.gid = os.getegid()
        if self.uid == 0:
            # The suite is also valid in a rootful test container.  PAPER
            # identities must remain non-root even there.
            self.paper_id = 2231
        else:
            self.paper_id = self.uid
        self.paths = BOOTSTRAP.BootstrapPaths(self.anchor)
        self.producer = BOOTSTRAP.ProducerEvidence(
            "/usr/libexec/hepta-p1-paper-kill-switch-bootstrap",
            hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            0o755, self.uid, self.gid)
        self.identity = BOOTSTRAP.PaperIdentity(
            BOOTSTRAP.IDENTITY, self.paper_id, self.paper_id,
            "/nonexistent", "/usr/sbin/nologin", (self.paper_id,))
        self.clock = 1000

    def close(self) -> None:
        self.temporary.cleanup()

    def now(self) -> int:
        self.clock += 1
        return self.clock

    def run(self) -> dict[str, object]:
        return BOOTSTRAP.bootstrap(
            paths=self.paths, expected_uid=self.paper_id,
            expected_gid=self.paper_id,
            source_baseline_sha256=SOURCE_SHA, producer=self.producer,
            boot_id=BOOT_ID, identity=self.identity,
            owner_uid=self.uid, owner_gid=self.gid, now_ms=self.now)

    def precreate(self) -> None:
        self.paths.control.mkdir(mode=0o750)
        os.chown(self.paths.control, self.uid, self.paper_id)
        self.paths.control.chmod(0o750)
        marker = self.paths.marker
        marker.write_bytes(b"engaged")
        os.chown(marker, self.uid, self.paper_id)
        marker.chmod(0o440)


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_fresh_bootstrap_is_exact_and_non_authorizing(self) -> None:
        receipt = self.fixture.run()
        self.assertEqual(receipt["schema"], BOOTSTRAP.SCHEMA)
        self.assertEqual(receipt["status"], "COMPLETE")
        self.assertEqual(receipt["domain"], "alpha")
        self.assertEqual(receipt["operation"],
                         "ENSURE_ENGAGED_NON_AUTHORIZING")
        self.assertEqual(self.fixture.paths.marker.read_bytes(), b"engaged")
        directory = self.fixture.paths.control.stat()
        marker = self.fixture.paths.marker.stat()
        self.assertEqual(stat.S_IMODE(directory.st_mode), 0o750)
        self.assertEqual((directory.st_uid, directory.st_gid),
                         (self.fixture.uid, self.fixture.paper_id))
        self.assertEqual(stat.S_IMODE(marker.st_mode), 0o440)
        self.assertEqual(marker.st_nlink, 1)
        for field in (
                "paper_test_admission_candidate", "paper_authorized",
                "live_authorized", "mutation_authorized",
                "direct_broker_access", "order_submission_authorized",
                "authorization_manifest_created",
                "credential_created_or_accessed",
                "unit_created_enabled_or_started",
                "connector_created_or_accessed"):
            self.assertIs(receipt[field], False)
        self.assertEqual(
            sorted(path.name for path in self.fixture.paths.control.iterdir()),
            ["kill-switch"])
        self.assertEqual(
            sorted(path.name for path in self.fixture.paths.state.iterdir()),
            ["journal.v1.json", "receipt.v1.json", "transaction.lock"])

    def test_receipt_has_exact_fields_and_valid_body_digest(self) -> None:
        receipt = self.fixture.run()
        self.assertEqual(set(receipt), {
            "schema", "version", "round", "domain", "operation", "status",
            "completed_at_ms", "boot_id", "source_baseline_sha256",
            "producer", "paper_identity", "control_directory",
            "kill_switch_marker", "transaction", "created",
            "authorization_manifest_created", "credential_created_or_accessed",
            "unit_created_enabled_or_started", "connector_created_or_accessed",
            "paper_test_admission_candidate", "paper_authorized",
            "live_authorized", "mutation_authorized", "direct_broker_access",
            "order_submission_authorized", "body_sha256",
        })
        self.assertEqual(receipt["body_sha256"],
                         BOOTSTRAP._body_digest(receipt))
        raw = (self.fixture.paths.state / BOOTSTRAP.RECEIPT_NAME).read_bytes()
        self.assertEqual(raw, BOOTSTRAP._canonical(receipt))
        self.assertEqual(stat.S_IMODE(os.stat(
            self.fixture.paths.state / BOOTSTRAP.RECEIPT_NAME).st_mode), 0o400)

    def test_existing_exact_marker_is_accepted_without_claiming_creation(self) -> None:
        self.fixture.precreate()
        receipt = self.fixture.run()
        self.assertEqual(receipt["created"], {
            "control_directory": False, "kill_switch_marker": False})

    def test_idempotent_retry_returns_same_receipt(self) -> None:
        first = self.fixture.run()
        second = self.fixture.run()
        self.assertEqual(first, second)
        self.assertEqual(self.fixture.paths.marker.stat().st_nlink, 1)

    def test_crash_after_each_durable_commit_resumes(self) -> None:
        for stage in (
                "after_journal", "after_directory", "after_marker",
                "after_receipt", "after_complete"):
            with self.subTest(stage=stage):
                fixture = Fixture()
                fired = False

                def fault(observed: str) -> None:
                    nonlocal fired
                    if observed == stage and not fired:
                        fired = True
                        raise Crash(stage)

                try:
                    with mock.patch.object(BOOTSTRAP, "_fault", fault):
                        with self.assertRaises(Crash):
                            fixture.run()
                    receipt = fixture.run()
                    self.assertEqual(receipt["status"], "COMPLETE")
                    self.assertEqual(fixture.paths.marker.read_bytes(), b"engaged")
                finally:
                    fixture.close()

    def test_control_directory_extra_entry_fails_closed(self) -> None:
        self.fixture.precreate()
        (self.fixture.paths.control / "credential").write_text("forbidden")
        with self.assertRaisesRegex(
                BOOTSTRAP.BootstrapError, "extra entries"):
            self.fixture.run()

    def test_state_directory_extra_entry_fails_closed(self) -> None:
        self.fixture.paths.state.mkdir(mode=0o700)
        (self.fixture.paths.state / "unexpected").write_text("x")
        with self.assertRaisesRegex(
                BOOTSTRAP.BootstrapError, "unexpected entries"):
            self.fixture.run()

    def test_control_symlink_fails_closed(self) -> None:
        target = self.fixture.anchor / "target"
        target.mkdir()
        self.fixture.paths.control.symlink_to(target, target_is_directory=True)
        with self.assertRaises(BOOTSTRAP.BootstrapError):
            self.fixture.run()

    def test_marker_symlink_fails_closed(self) -> None:
        self.fixture.paths.control.mkdir(mode=0o750)
        os.chown(self.fixture.paths.control, self.fixture.uid,
                 self.fixture.paper_id)
        self.fixture.paths.control.chmod(0o750)
        target = self.fixture.anchor / "engaged"
        target.write_bytes(b"engaged")
        self.fixture.paths.marker.symlink_to(target)
        with self.assertRaises(BOOTSTRAP.BootstrapError):
            self.fixture.run()

    def test_marker_hardlink_fails_closed(self) -> None:
        self.fixture.precreate()
        os.link(self.fixture.paths.marker,
                self.fixture.anchor / "kill-switch-alias")
        with self.assertRaisesRegex(
                BOOTSTRAP.BootstrapError, "metadata mismatch"):
            self.fixture.run()

    def test_marker_content_mode_and_group_drift_fail_closed(self) -> None:
        def change_contents(path: Path) -> None:
            path.chmod(0o600)
            path.write_bytes(b"release")
            path.chmod(0o440)

        mutations = [
            change_contents,
            lambda path: path.chmod(0o640),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                fixture = Fixture()
                try:
                    fixture.precreate()
                    mutation(fixture.paths.marker)
                    with self.assertRaises(BOOTSTRAP.BootstrapError):
                        fixture.run()
                finally:
                    fixture.close()

    def test_directory_mode_and_group_drift_fail_closed(self) -> None:
        self.fixture.precreate()
        self.fixture.paths.control.chmod(0o755)
        with self.assertRaises(BOOTSTRAP.BootstrapError):
            self.fixture.run()

    def test_directory_group_drift_fails_closed(self) -> None:
        self.fixture.precreate()
        real_lstat = os.lstat

        def drift(path: object, *args: object, **kwargs: object) -> object:
            value = real_lstat(path, *args, **kwargs)
            if Path(path) == self.fixture.paths.control:
                return StatProxy(value, st_gid=value.st_gid + 1)
            return value

        with mock.patch.object(BOOTSTRAP.os, "lstat", side_effect=drift):
            with self.assertRaisesRegex(
                    BOOTSTRAP.BootstrapError, "metadata mismatch"):
                self.fixture.run()

    def test_marker_group_drift_fails_closed(self) -> None:
        self.fixture.precreate()
        real_stat = os.stat

        def drift(path: object, *args: object, **kwargs: object) -> object:
            value = real_stat(path, *args, **kwargs)
            if path == "kill-switch" and kwargs.get("dir_fd") is not None:
                return StatProxy(value, st_gid=value.st_gid + 1)
            return value

        with mock.patch.object(BOOTSTRAP.os, "stat", side_effect=drift):
            with self.assertRaisesRegex(
                    BOOTSTRAP.BootstrapError, "metadata mismatch"):
                self.fixture.run()

    def test_journal_tamper_fails_closed(self) -> None:
        self.fixture.run()
        journal = self.fixture.paths.state / BOOTSTRAP.JOURNAL_NAME
        document = json.loads(journal.read_text())
        document["domain"] = "beta"
        journal.chmod(0o600)
        journal.write_bytes(BOOTSTRAP._canonical(document))
        journal.chmod(0o600)
        with self.assertRaises(BOOTSTRAP.BootstrapError):
            self.fixture.run()

    def test_receipt_tamper_fails_closed(self) -> None:
        self.fixture.run()
        receipt = self.fixture.paths.state / BOOTSTRAP.RECEIPT_NAME
        receipt.chmod(0o600)
        document = json.loads(receipt.read_text())
        document["paper_authorized"] = True
        receipt.write_bytes(BOOTSTRAP._canonical(document))
        receipt.chmod(0o400)
        with self.assertRaises(BOOTSTRAP.BootstrapError):
            self.fixture.run()

    def test_source_boot_and_identity_pins_are_mandatory(self) -> None:
        cases = (
            {"source_baseline_sha256": "sha256:bad"},
            {"boot_id": "not-a-boot"},
            {"expected_uid": self.fixture.paper_id + 1},
            {"expected_gid": self.fixture.paper_id + 1},
        )
        base = {
            "paths": self.fixture.paths,
            "expected_uid": self.fixture.paper_id,
            "expected_gid": self.fixture.paper_id,
            "source_baseline_sha256": SOURCE_SHA,
            "producer": self.fixture.producer,
            "boot_id": BOOT_ID,
            "identity": self.fixture.identity,
            "owner_uid": self.fixture.uid,
            "owner_gid": self.fixture.gid,
            "now_ms": self.fixture.now,
        }
        for changes in cases:
            with self.subTest(changes=changes):
                arguments = {**base, **changes}
                with self.assertRaises(BOOTSTRAP.BootstrapError):
                    BOOTSTRAP.bootstrap(**arguments)

    def test_domain_and_paths_are_frozen_to_alpha(self) -> None:
        changed = BOOTSTRAP.BootstrapPaths(
            self.fixture.anchor, control_name="ib-paper-control-beta")
        with self.assertRaisesRegex(
                BOOTSTRAP.BootstrapError, "override is forbidden"):
            BOOTSTRAP.bootstrap(
                paths=changed, expected_uid=self.fixture.paper_id,
                expected_gid=self.fixture.paper_id,
                source_baseline_sha256=SOURCE_SHA,
                producer=self.fixture.producer, boot_id=BOOT_ID,
                identity=self.fixture.identity, owner_uid=self.fixture.uid,
                owner_gid=self.fixture.gid, now_ms=self.fixture.now)

    def test_fixed_producer_mode_owner_and_digest_are_bound(self) -> None:
        for producer in (
                BOOTSTRAP.ProducerEvidence("/tmp/x", "x" * 64, 0o755,
                                           self.fixture.uid, self.fixture.gid),
                BOOTSTRAP.ProducerEvidence("/tmp/x", "a" * 64, 0o775,
                                           self.fixture.uid, self.fixture.gid),
                BOOTSTRAP.ProducerEvidence("/tmp/x", "a" * 64, 0o755,
                                           self.fixture.uid + 1,
                                           self.fixture.gid)):
            with self.subTest(producer=producer):
                with self.assertRaises(BOOTSTRAP.BootstrapError):
                    BOOTSTRAP.bootstrap(
                        paths=self.fixture.paths,
                        expected_uid=self.fixture.paper_id,
                        expected_gid=self.fixture.paper_id,
                        source_baseline_sha256=SOURCE_SHA,
                        producer=producer, boot_id=BOOT_ID,
                        identity=self.fixture.identity,
                        owner_uid=self.fixture.uid,
                        owner_gid=self.fixture.gid,
                        now_ms=self.fixture.now)

    def test_cli_has_no_domain_output_or_authority_option(self) -> None:
        help_text = BOOTSTRAP._parser().format_help()
        self.assertNotIn("--domain", help_text)
        self.assertNotIn("--output", help_text)
        self.assertNotIn("authorize", help_text.lower())

    def test_deployable_source_has_no_service_network_or_disarm_surface(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("AF_INET", source)
        self.assertNotIn("unlink(\"kill-switch\"", source)
        self.assertNotIn("paper_authorized\": True", source)
        self.assertNotIn("live_authorized\": True", source)


if __name__ == "__main__":
    unittest.main()
