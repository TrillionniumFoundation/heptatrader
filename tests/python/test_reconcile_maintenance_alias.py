"""Filesystem-only alias reconciliation fixtures, no Broker or runtime effects."""
import json
from pathlib import Path
import os
import tempfile
import unittest

from reconcile_maintenance_alias import (
    reconcile, sha256, OLD_RELEASE, NEW_RELEASE, HELPER_RELEASE,
    RECEIPT, SOURCE_COMMIT,
)

WRAP = b"#!/bin/sh\necho hypothetical\n"
HELP = b"print('read only fixture')\n"


class AliasTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.home = Path(self.dir.name)
        self.old = self.home / ".local/share/heptatrader" / OLD_RELEASE
        self.new = self.home / ".local/share/heptatrader" / NEW_RELEASE
        self.helper = self.home / ".local/share/heptatrader" / HELPER_RELEASE
        self.alias = self.home / ".local/bin/heptatrader"
        self.alias.parent.mkdir(parents=True)
        self.old.parent.mkdir(parents=True)
        self.old.write_bytes(WRAP)
        self.new.parent.mkdir(parents=True)
        self.new.write_bytes(WRAP)
        self.new.chmod(0o555)
        self.helper.write_bytes(HELP)
        self.helper.chmod(0o444)

    def use(self, path):
        if self.alias.exists() or self.alias.is_symlink():
            self.alias.unlink()
        self.alias.symlink_to(path)

    def inspect(self):
        return reconcile(
            self.home, self.home, uid=os.geteuid(),
            expected_wrapper=WRAP, expected_helper=HELP
        )

    def receipt(self):
        p = self.home / RECEIPT
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({
            "source_commit": SOURCE_COMMIT,
            "new_pointer": str(self.new),
            "new_source_sha256": {
                "heptatrader-user": sha256(WRAP),
                "hepta_maintenance_latch.py": sha256(HELP)
            },
            "service_identities_unchanged": True,
            "broker_orders_requested": 0,
            "service_restarts_requested": 0,
        }))
        return p

    def test_missing_alias_denied(self):
        self.assertEqual(self.inspect()["state"], "ALIAS_MISSING_OR_INACCESSIBLE")

    def test_old_alias_not_reexecuted(self):
        self.use(self.old)
        self.assertEqual(self.inspect()["state"], "ORIGINAL_CLI_STILL_ACTIVE")

    def test_new_without_receipt_is_not_fabricated_success(self):
        self.use(self.new)
        value = self.inspect()
        self.assertEqual(value["state"], "NEW_CLI_EFFECTIVE_RECEIPT_MISSING")
        self.assertFalse(value["prior_job_result_recovered"])

    def test_receipt_agrees(self):
        self.use(self.new)
        self.receipt()
        self.assertEqual(self.inspect()["state"], "NEW_CLI_EFFECTIVE_PRIOR_RECEIPT_MATCHES")

    def test_receipt_conflict(self):
        self.use(self.new)
        p = self.receipt()
        value = json.loads(p.read_text())
        value["broker_orders_requested"] = 1
        p.write_text(json.dumps(value))
        self.assertEqual(self.inspect()["state"], "NEW_CLI_EFFECTIVE_RECEIPT_CONFLICT")

    def test_changed_candidate_denied(self):
        self.use(self.new)
        self.new.chmod(0o755)
        self.assertEqual(self.inspect()["state"], "CANDIDATE_RELEASE_UNSAFE")

    def test_altered_helper_denied(self):
        self.use(self.new)
        self.helper.chmod(0o644)
        self.assertEqual(self.inspect()["state"], "CANDIDATE_RELEASE_UNSAFE")

    def test_changed_candidate_size_fails_before_read(self):
        self.use(self.new)
        self.new.chmod(0o644)
        self.new.write_bytes(WRAP + b"unexpected-extra-content")
        self.new.chmod(0o555)
        self.assertEqual(self.inspect()["state"], "CANDIDATE_RELEASE_UNSAFE")

    def test_foreign_alias_denied(self):
        self.use(self.helper)
        self.assertEqual(self.inspect()["state"], "UNEXPECTED_CLI_TARGET")

    def test_dangling_symlink_denied(self):
        self.use(self.home / "absent")
        self.assertEqual(self.inspect()["state"], "ALIAS_DANGLING_OR_CYCLIC")

    def test_regular_alias_denied(self):
        self.alias.write_text("not a symlink")
        self.assertEqual(self.inspect()["state"], "ALIAS_NOT_OWNER_SYMLINK")

    def test_no_order_or_stop_permission_in_any_case(self):
        for target in [self.old, self.new, self.home / "absent"]:
            self.use(target)
            result = self.inspect()
            self.assertFalse(result["may_stop_exposed_driver"])
            self.assertEqual(result["broker_orders_requested"], 0)
            self.assertEqual(result["service_restarts_requested"], 0)


if __name__ == "__main__":
    unittest.main()
